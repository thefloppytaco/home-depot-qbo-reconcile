#!/usr/bin/env python3
"""
Bulk-download Home Depot receipt emails from Gmail via IMAP (one-time backfill,
safe to re-run — see Incremental below).

Why IMAP and not the Claude Gmail connector: each receipt email is ~140 KB of HTML;
pulling ~1,900 through the connector is impractical. IMAP runs locally, grabs the PDF
attachment (and HTML fallback) for every receipt, and costs nothing in tokens.

SETUP (one time):
  1. The Gmail account needs 2-Step Verification ON.
  2. Create an App Password:  Google Account -> Security -> 2-Step Verification
     -> App passwords -> generate one (name it "hd-receipts").
  3. Put credentials in a file named gmail_creds.txt (or .gmail_creds) in the
     current directory, or next to this script:
         EMAIL=your-receipts-inbox@gmail.com
         APP_PASSWORD=xxxx xxxx xxxx xxxx
     (Fill this in yourself; it is gitignored and never leaves your machine.)
  See docs/02-gmail-setup.md for the full walkthrough.

RUN:
  python3 download_receipts.py
Incremental by default: each email's header is checked first, and the full message
is only fetched when its receipt file isn't already in --out. Pass --refresh to
force a full re-download.

Output (default --out ./downloads):
  *.pdf   (receipt PDFs, named <date>_<order-or-msgid>.pdf)
  *.html  (HTML fallback when an email has no PDF attachment)
"""
import argparse, imaplib, email, os, re, sys
from email.header import decode_header

HERE = os.path.dirname(os.path.abspath(__file__))

# Gmail search by sender only (no quoted phrases -> avoids IMAP parse errors).
# Quote/marketing emails from the same sender are filtered out in the loop below.
GM_QUERY = 'from:HomeDepot@order.homedepot.com'

def resolve_input(filename, explicit=None):
    """Resolve a default input path: an explicit CLI value always wins; otherwise
    check the current directory first, then fall back to this script's directory
    (backward compatible with the old same-folder layout)."""
    if explicit:
        return explicit
    if os.path.exists(filename):
        return filename
    fallback = os.path.join(HERE, filename)
    return fallback if os.path.exists(fallback) else filename

def fail(msg):
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)

def find_creds(explicit=None):
    """Search order: an explicit --creds always wins; otherwise prefer the visible
    gmail_creds.txt over the dotfile .gmail_creds, checking the current directory
    before this script's directory for each name."""
    if explicit:
        return explicit
    for name in ("gmail_creds.txt", ".gmail_creds"):
        p = resolve_input(name)
        if os.path.exists(p):
            return p
    return "gmail_creds.txt"  # default location named in the "missing" error

def is_receipt_subject(subj):
    s = subj.lower()
    if "receipt" in s:           # "Your Electronic Receipt" / "...your Home Depot receipt for #"
        return True
    return False                 # excludes "...quote for #..." and any marketing

def load_creds(path):
    if not os.path.exists(path):
        fail(
            f"missing credentials file: {path}\n"
            "  Create it with EMAIL= and APP_PASSWORD= lines (copy src/.gmail_creds.template),\n"
            "  or point to an existing one with --creds PATH. See docs/02-gmail-setup.md."
        )
    kv = {}
    for line in open(path):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip()
    if not kv.get("EMAIL") or not kv.get("APP_PASSWORD"):
        fail(f"both EMAIL and APP_PASSWORD must be set in {path}")
    return kv["EMAIL"], kv["APP_PASSWORD"].replace(" ", "")

def safe(s):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', s)[:80]

def parse_args():
    p = argparse.ArgumentParser(
        prog="download_receipts.py",
        description="Bulk-download Home Depot receipt emails from Gmail over IMAP into "
                     "--out (default: ./downloads). Incremental by default: re-running only "
                     "downloads receipts that aren't already saved there.",
        epilog="Needs a Gmail App Password, not your normal password — see docs/02-gmail-setup.md.",
    )
    p.add_argument("--creds", metavar="PATH",
                    help="Path to your Gmail credentials file (default: look for "
                         "./gmail_creds.txt or ./.gmail_creds, then the same names next to "
                         "this script)")
    p.add_argument("--out", metavar="DIR", default="downloads",
                    help="Folder to save receipt PDFs/HTML into (default: ./downloads)")
    p.add_argument("--query", default=GM_QUERY,
                    help="Gmail search query (X-GM-RAW) matching the receipt sender "
                         f"(default: {GM_QUERY!r})")
    p.add_argument("--refresh", action="store_true",
                    help="Force re-download even when a receipt file already exists in --out")
    return p.parse_args()

def main():
    args = parse_args()
    creds_path = find_creds(args.creds)
    email_addr, app_pw = load_creds(creds_path)

    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)

    M = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        M.login(email_addr, app_pw)
    except imaplib.IMAP4.error as e:
        fail(
            "Gmail login failed. This needs a Google App Password, not your normal Google\n"
            "  password:\n"
            "    1. Turn on 2-Step Verification on this Gmail account.\n"
            "    2. Google Account -> Security -> 2-Step Verification -> App passwords ->\n"
            "       generate one.\n"
            "    3. Put EMAIL= and APP_PASSWORD= (the app password) in your creds file.\n"
            f"  See docs/02-gmail-setup.md for the full walkthrough. (IMAP said: {e})"
        )
    M.select('"[Gmail]/All Mail"', readonly=True)

    typ, data = M.search(None, 'X-GM-RAW', f'"{args.query}"')
    if typ != "OK":
        fail(f"IMAP search failed: {typ}")
    ids = data[0].split()
    print(f"Matched {len(ids)} candidate emails. Checking against {out_dir} ...")

    n_pdf = n_html = n_skip = 0
    for i, num in enumerate(ids, 1):
        typ, hdr_data = M.fetch(num, "(BODY.PEEK[HEADER])")
        if typ != "OK":
            print(f"  [{i}] header fetch failed"); continue
        msg = email.message_from_bytes(hdr_data[0][1])

        # date for filename
        date = msg.get("Date", "")
        dm = re.search(r'(\d{1,2})\s+(\w{3})\s+(\d{4})', date)
        datestr = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1).zfill(2)}" if dm else "nodate"
        subj = "".join(p.decode(enc or "utf-8", "replace") if isinstance(p, bytes) else p
                       for p, enc in decode_header(msg.get("Subject", "")))
        if not is_receipt_subject(subj):
            continue
        order = re.search(r'H\d{4}-\d{6}', subj)
        tag = order.group(0) if order else "reg"
        # Always append the unique Message-Id local part so nothing overwrites.
        msgid_local = (msg.get("Message-Id", "") or str(i)).strip("<>").split("@")[0]
        base = f"{datestr}_{tag}_{safe(msgid_local)}"

        pdf_path = os.path.join(out_dir, base + ".pdf")
        html_path = os.path.join(out_dir, base + ".html")
        if not args.refresh and (os.path.exists(pdf_path) or os.path.exists(html_path)):
            n_skip += 1
            if i % 100 == 0:
                print(f"  ...{i}/{len(ids)}")
            continue

        typ, msg_data = M.fetch(num, "(RFC822)")
        if typ != "OK":
            print(f"  [{i}] fetch failed"); continue
        full_msg = email.message_from_bytes(msg_data[0][1])

        got_pdf = False
        html_part = None
        for part in full_msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            fn = part.get_filename() or ""
            if ctype == "application/pdf" or fn.lower().endswith(".pdf"):
                payload = part.get_payload(decode=True)
                if payload:
                    with open(pdf_path, "wb") as f:
                        f.write(payload)
                    got_pdf = True; n_pdf += 1
            elif ctype == "text/html" and "attachment" not in disp:
                html_part = part
        if not got_pdf and html_part is not None:
            payload = html_part.get_payload(decode=True)
            if payload:
                with open(html_path, "wb") as f:
                    f.write(payload)
                n_html += 1
        if i % 100 == 0:
            print(f"  ...{i}/{len(ids)}")

    M.logout()
    print(f"\nDone. {n_pdf} PDFs + {n_html} HTML receipts downloaded, "
          f"{n_skip} skipped (already in {out_dir})")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        fail(f"unexpected error: {e}")
