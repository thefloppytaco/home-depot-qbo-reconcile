#!/usr/bin/env python3
"""
Home Depot receipt parser — receipts are the source of truth.
Handles two receipt formats:
  1) eReceipt / register receipts (in-store sales & returns)
  2) Customer Receipt / Special Services Invoice (H-order desk orders)

Extracts per receipt: type (SALE/RETURN), store, datetime, project (PO/Job),
totals, tenders [(last4, type, amount)], line_items [(sku, description, amount,
orig_rec)], and for returns the ORIG REC links. Line items are grouped by the ORIG REC
they belong to, so a returned item traces back to its original purchase even when the
return's own PO/JOB is blank.

Run:  python3 parse_receipts.py [folder]
The folder defaults to ./downloads, falling back to this script's directory for
backward compatibility, if omitted. Outputs (receipts.json, tender_roster.csv,
reconciliation_report.csv) are written to --outdir, which defaults to the current
directory.
"""
import argparse, sys, os, re, json, glob, subprocess, shutil
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))

TENDER_TYPES = ["STORE CREDIT", "AMEX", "AMERICAN EXPRESS", "VISA", "MASTERCARD",
                "DISCOVER", "DEBIT", "GIFT CARD", "GIFT", "CASH", "CHECK"]

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

def text_of(path):
    if path.lower().endswith((".html", ".htm", ".txt")):
        return html_to_text(open(path, encoding="utf-8", errors="replace").read())
    # pdftotext -layout keeps tender amounts on the same line as the PAN
    return subprocess.check_output(["pdftotext", "-layout", path, "-"],
                                   stderr=subprocess.DEVNULL).decode("utf-8", "replace")

def html_to_text(html):
    """Strip an HD receipt HTML email body down to receipt text (same layout as the PDF)."""
    html = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.S | re.I)
    text = re.sub(r'<[^>]+>', '\n', html)
    text = (text.replace('&nbsp;', ' ').replace('&amp;', '&')
                .replace('&lt;', '<').replace('&gt;', '>')
                .replace('&quot;', '"').replace('&#36;', '$'))
    lines = [re.sub(r'[ \t]+', ' ', l).strip() for l in text.splitlines()]
    return "\n".join(l for l in lines if l)

def norm_locator(store, reg, txn, date):
    """Normalize a register locator so a sale header and a return's ORIG REC match.
    Strips leading zeros on register/txn (header uses '00019', ORIG REC uses '090')."""
    return f"{store}|{reg.lstrip('0') or '0'}|{txn.lstrip('0') or '0'}|{date}"

def money(s):
    s = s.replace(",", "").replace("$", "").strip()
    try:
        return float(s)
    except ValueError:
        return None

# --- Line-item extraction ----------------------------------------------------
# Durable per-item detail (SKU, description, amount) is what lets a returned item
# be traced back to the exact original purchase — and its project — even when the
# return's own PO/JOB is blank. See ai/context.md "Break receipts down to line items".
# amount may be jammed against the description on wide items ("...wi-99.99"), so the
# separator before it is optional.
_ITEM_SKU_RE = re.compile(r'^\s*(\d{3,4}-\d{3}-\d{3})\s+(.+?)\s*(-?\$?[\d,]+\.\d{2})\s*$')
_ITEM_RETURNED_RE = re.compile(r'^\s*RETURNED:\s*(.+?)\s*$', re.I)
_ITEM_TRAIL_RE = re.compile(r'^\s*(.+?)\s+\$(-?[\d,]+\.\d{2})\s*$')
_ITEM_QTY_RE = re.compile(r'\s+\d+\s*@\s*[\d.]+\s*$')
_ITEM_SKIP = ("SUBTOTAL", "SALES TAX", "TOTAL", "ORIG REC", "PO/JOB", "THANK YOU",
              "REFUND", "RETURN POLICY", "CARD BALANCE", "BALANCE", "AUTH",
              "INVOICE", "USD$", "PRO XTRA", "CASHIER", "MANAGER", "THE HOME DEPOT",
              "HOW DOERS", "CUSTOMER", "RETURN DECLINED", "RETURN DENIED",
              "TRANSACTION ID", "CHANGE DUE", "ITEMS SOLD")

def parse_line_items(txt):
    """Extract per-line-item detail from a register receipt, grouped by the ORIG REC
    the item belongs to (None for a straight sale). Returns a list of
    {sku, description, amount, orig_rec}. Amount is None when the receipt doesn't
    print a per-item figure (e.g. some return formats)."""
    items = []
    cur_orig = None
    body = re.split(r'RETURN POLICY', txt, maxsplit=1)[0]
    for raw in body.splitlines():
        line = raw.rstrip()
        u = line.strip().upper()
        if not u:
            continue
        om = re.search(r'ORIG REC:\s*(\d{4})\s+(\d+)\s+(\d+)\s+(\d{2}/\d{2}/\d{2})', line)
        if om:
            cur_orig = norm_locator(*om.groups())
            continue
        if any(k in u for k in _ITEM_SKIP) or re.match(r'^\s*X{4,}\d{4}', line):
            continue
        m = _ITEM_SKU_RE.match(line)
        if m:
            items.append({"sku": m.group(1), "description": re.sub(r'\s+', ' ', m.group(2)).strip(),
                          "amount": money(m.group(3)), "orig_rec": cur_orig})
            continue
        m = _ITEM_RETURNED_RE.match(line)
        if m:
            items.append({"sku": None, "description": re.sub(r'\s+', ' ', m.group(1)).strip(),
                          "amount": None, "orig_rec": cur_orig})
            continue
        m = _ITEM_TRAIL_RE.match(line)
        if m:
            desc = _ITEM_QTY_RE.sub('', m.group(1)).strip()
            if len(desc) >= 3:
                items.append({"sku": None, "description": re.sub(r'\s+', ' ', desc).strip(),
                              "amount": money(m.group(2)), "orig_rec": cur_orig})
    return items

def parse_register_receipt(txt, fn):
    """eReceipt format."""
    rec = {"file": fn, "format": "register", "tenders": [], "orig_recs": [],
           "project": None, "total": None, "is_return": False,
           "store": None, "datetime": None, "card_balance": None}

    m = re.search(r'\b(\d{4})\s+(\d{5})\s+(\d{4,6})\s+(\d{2}/\d{2}/\d{2})\s+(\d{1,2}:\d{2}\s*[AP]M)', txt)
    if m:
        rec["store"] = m.group(1)
        rec["datetime"] = f"{m.group(4)} {m.group(5)}"
        # This receipt's own locator (store, register, txn#, date) — normalized
        rec["locator"] = norm_locator(m.group(1), m.group(2), m.group(3), m.group(4))

    for om in re.finditer(r'ORIG REC:\s*(\d{4})\s+(\d+)\s+(\d+)\s+(\d{2}/\d{2}/\d{2})', txt):
        rec["orig_recs"].append(norm_locator(*om.groups()))

    pj = re.search(r'PO/JOB NAME:\s*(.+)', txt)
    if pj:
        # project sometimes wraps to next line (e.g. "6116 GREENSPRIN\nG")
        val = pj.group(1).strip()
        rec["project"] = re.sub(r'\s+', ' ', val)

    tm = re.search(r'\bTOTAL\s+(-?\$[\d,]+\.\d{2})', txt)
    if tm:
        rec["total"] = money(tm.group(1))

    # Precise return marker (avoids false positives from return-policy boilerplate)
    rec["is_return"] = ("REFUND-CUSTOMER COPY" in txt.upper()) or \
                       (rec["total"] is not None and rec["total"] < 0)

    cb = re.search(r'CARD BALANCE\s+([\d,]+\.\d{2})', txt)
    if cb:
        rec["card_balance"] = money(cb.group(1))

    # Tender lines, scanned line-by-line so we catch BOTH styles on one receipt:
    #   inline amount:   XXXXXXXX6841 STORE CREDIT  0.94
    #   amount on next line (cards): XXXXXXXX0641 VISA \n  USD$ 58.26
    TYPE = r'(STORE CREDIT|AMERICAN EXPRESS|AMEX|VISA|MASTERCARD|DISCOVER|DEBIT|GIFT(?: CARD)?)'
    lines = txt.splitlines()
    for i, line in enumerate(lines):
        tm = re.search(r'X{4,}(\d{4})\s+' + TYPE + r'(?:\s+(-?[\d,]+\.\d{2}))?', line)
        if not tm:
            continue
        last4, ttype, amt = tm.group(1), tm.group(2).strip(), tm.group(3)
        if amt is None:  # look ahead a few lines for the USD$ amount
            for j in range(i + 1, min(i + 4, len(lines))):
                um = re.search(r'USD\$\s*(-?[\d,]+\.\d{2})', lines[j])
                if um:
                    amt = um.group(1); break
        rec["tenders"].append({"last4": last4, "type": ttype,
                               "amount": money(amt) if amt is not None else None})
    # HTML email bodies repeat the receipt block (responsive layouts) -> dedupe
    rec["tenders"] = _dedupe(rec["tenders"], lambda t: (t["last4"], t["type"], t["amount"]))
    rec["orig_recs"] = list(dict.fromkeys(rec["orig_recs"]))
    rec["line_items"] = parse_line_items(txt)
    return rec

def _dedupe(items, keyfn):
    seen, out = set(), []
    for it in items:
        k = keyfn(it)
        if k not in seen:
            seen.add(k); out.append(it)
    return out

def parse_customer_receipt(txt, fn):
    """Customer Receipt / Special Services Invoice (H-order)."""
    rec = {"file": fn, "format": "h-order", "tenders": [], "orig_recs": [],
           "project": None, "total": None, "is_return": False,
           "order": None, "datetime": None, "line_items": []}
    om = re.search(r'H\d{4}-\d{6}', txt)
    if om:
        rec["order"] = om.group(0)
    pj = re.search(r'(?:PO\s*/\s*Job Name|Job Description)\s*[:#]?\s*(.+)', txt)
    if pj:
        rec["project"] = re.sub(r'\s+', ' ', pj.group(1).strip())
    tm = re.search(r'(?:Order Total|TOTAL)\s*\$?\s*([\d,]+\.\d{2})', txt)
    if tm:
        rec["total"] = money(tm.group(1))
    # Payment Method block: "American Express 1000 Charged $264.89"
    for pm in re.finditer(r'(American Express|Visa|Mastercard|MasterCard|Discover|Store Credit)\s+(\d{4})\s+Charged\s+\$?([\d,]+\.\d{2})', txt):
        rec["tenders"].append({"last4": pm.group(2), "type": pm.group(1).upper(),
                               "amount": money(pm.group(3))})
    return rec

def classify_and_parse(path):
    txt = text_of(path)
    fn = os.path.basename(path)
    if "Customer Receipt" in txt or "SPECIAL SERVICES" in txt or "Payment Method" in txt:
        return parse_customer_receipt(txt, fn)
    return parse_register_receipt(txt, fn)

def norm_type(t):
    t = t.upper()
    if "STORE CREDIT" in t: return "STORE CREDIT"
    if "AMEX" in t or "AMERICAN EXPRESS" in t: return "AMEX"
    if "VISA" in t: return "VISA"
    if "MASTERCARD" in t: return "MASTERCARD"
    if "DISCOVER" in t: return "DISCOVER"
    if "DEBIT" in t: return "DEBIT"
    if "GIFT" in t: return "GIFT CARD"
    return t

def parse_args():
    p = argparse.ArgumentParser(
        prog="parse_receipts.py",
        description="Parse Home Depot receipt PDFs/HTML emails into receipts.json, "
                     "tender_roster.csv, and reconciliation_report.csv. Links each return "
                     "back to its original purchase's project via the ORIG REC locator. "
                     "Outputs are written to --outdir (default: the current directory).",
    )
    p.add_argument("folder", nargs="?", default=None,
                    help="Folder of receipt PDF/HTML/txt files (default: ./downloads, "
                         "falling back to this script's directory)")
    p.add_argument("-o", "--outdir", metavar="DIR", default=".",
                    help="Where to write receipts.json, tender_roster.csv, and "
                         "reconciliation_report.csv (default: current directory)")
    return p.parse_args()

def main():
    args = parse_args()
    folder = args.folder if args.folder is not None else resolve_input("downloads")

    if not os.path.isdir(folder):
        fail(
            f"receipts folder not found: {folder}\n"
            "  Download your receipt emails first: python3 src/download_receipts.py\n"
            "  (see docs/02-gmail-setup.md) — or pass the folder as an argument."
        )

    pdfs = [p for ext in ("*.pdf", "*.html", "*.htm", "*.txt")
            for p in sorted(glob.glob(os.path.join(folder, ext)))
            if "Purchase_History" not in os.path.basename(p)]

    if not pdfs:
        fail(
            f"no receipt files (*.pdf, *.html, *.htm, *.txt) found in: {folder}\n"
            "  Download your receipt emails first: python3 src/download_receipts.py\n"
            "  (see docs/02-gmail-setup.md)."
        )

    if any(p.lower().endswith(".pdf") for p in pdfs) and shutil.which("pdftotext") is None:
        fail(
            "pdftotext not found on PATH (needed to read PDF receipts). Install Poppler:\n"
            "    macOS:         brew install poppler\n"
            "    Debian/Ubuntu: sudo apt-get install poppler-utils\n"
            "    Windows:       install Poppler and add its bin/ to PATH (or use WSL)"
        )

    os.makedirs(args.outdir, exist_ok=True)

    recs = []
    for p in pdfs:
        try:
            recs.append(classify_and_parse(p))
        except Exception as e:
            recs.append({"file": os.path.basename(p), "error": str(e)})

    # --- Return -> original-project resolver -------------------------------
    # Index every SALE register receipt by its locator -> project.
    sale_index = {}
    for r in recs:
        if not r.get("is_return") and r.get("locator") and r.get("project"):
            sale_index[r["locator"]] = r["project"]
    # Also index H-order sales by order number -> project (for H-order returns).
    order_index = {}
    for r in recs:
        if not r.get("is_return") and r.get("order") and r.get("project"):
            order_index[r["order"]] = r["project"]

    for r in recs:
        if not r.get("is_return"):
            continue
        resolved, unresolved = {}, []
        for loc in r.get("orig_recs", []):
            proj = sale_index.get(loc)
            if proj:
                resolved[loc] = proj
            else:
                unresolved.append(loc)
        # H-order return: resolve via the original order number too.
        if r.get("order") and r["order"] in order_index:
            resolved[r["order"]] = order_index[r["order"]]
        r["resolved_projects"] = sorted(set(resolved.values()))
        r["unresolved_orig_recs"] = unresolved
        # If the return itself already carries a project, keep it as a hint.
        if not r["resolved_projects"] and r.get("project"):
            r["resolved_projects"] = [r["project"] + " (on-receipt)"]

    with open(os.path.join(args.outdir, "receipts.json"), "w") as f:
        json.dump(recs, f, indent=2)

    # Tender roster: distinct (last4, normalized type)
    roster = defaultdict(lambda: {"count": 0, "raw_types": set(), "sample_amt": None})
    for r in recs:
        for t in r.get("tenders", []):
            key = (t["last4"], norm_type(t["type"]))
            roster[key]["count"] += 1
            roster[key]["raw_types"].add(t["type"])
            if roster[key]["sample_amt"] is None:
                roster[key]["sample_amt"] = t["amount"]

    roster_path = os.path.join(args.outdir, "tender_roster.csv")
    with open(roster_path, "w") as f:
        f.write("last4,detected_type,times_seen,is_store_credit,QBO_account_TO_FILL\n")
        for (last4, ntype), v in sorted(roster.items(), key=lambda kv: -kv[1]["count"]):
            sc = "YES" if ntype == "STORE CREDIT" else ""
            f.write(f"{last4},{ntype},{v['count']},{sc},\n")

    # Reconciliation report: one row per receipt, with a review flag
    def review_reason(r):
        reasons = []
        tend = [t for t in r.get("tenders", []) if t.get("amount") is not None]
        if r.get("total") is not None and tend:
            s = round(sum(t["amount"] for t in tend), 2)
            if abs(s - r["total"]) > 0.02:
                reasons.append(f"tenders({s})!=total({r['total']})")
        if sum(1 for t in r.get("tenders", []) if norm_type(t["type"]) == "STORE CREDIT") > 1:
            reasons.append("multi-store-credit")
        if r.get("is_return"):
            if not r.get("orig_recs") and not r.get("order"):
                reasons.append("return-no-orig-link")
            elif r.get("unresolved_orig_recs") and not r.get("resolved_projects"):
                reasons.append("return-orig-not-in-corpus")
            elif r.get("unresolved_orig_recs"):
                reasons.append("return-partially-resolved")
        if not r.get("project"):
            reasons.append("no-project")
        if not r.get("tenders") and r.get("format") != "h-order":
            reasons.append("no-tender")
        return "; ".join(reasons)

    report_path = os.path.join(args.outdir, "reconciliation_report.csv")
    with open(report_path, "w") as f:
        f.write("file,type,format,project,resolved_projects,total,tenders,store_credit_used,"
                "orig_rec_links,order,needs_review,review_reason\n")
        for r in recs:
            if "error" in r:
                f.write(f"{r['file']},ERROR,,,,,,,,YES,{r['error']}\n"); continue
            tend = "|".join(f"{norm_type(t['type'])}..{t['last4']}={t['amount']}"
                            for t in r.get("tenders", []))
            sc = any(norm_type(t["type"]) == "STORE CREDIT" for t in r.get("tenders", []))
            rr = review_reason(r)
            f.write(",".join([
                f"\"{r['file']}\"",
                "RETURN" if r.get("is_return") else "SALE",
                r.get("format", ""),
                f"\"{r.get('project') or ''}\"",
                f"\"{'|'.join(r.get('resolved_projects', []))}\"",
                str(r.get("total") if r.get("total") is not None else ""),
                f"\"{tend}\"",
                "YES" if sc else "",
                f"\"{'|'.join(r.get('orig_recs', []))}\"",
                r.get("order") or "",
                "YES" if rr else "",
                f"\"{rr}\"",
            ]) + "\n")

    # Console summary
    print(f"Parsed {len(recs)} receipts -> reconciliation_report.csv\n")
    print(f"{'FILE':28} {'TYPE':7} {'PROJECT':24} {'TOTAL':>10}  TENDERS / ORIG-RECS")
    for r in recs:
        if "error" in r:
            print(f"{r['file']:28} ERROR {r['error']}"); continue
        typ = "RETURN" if r.get("is_return") else "SALE"
        tend = ", ".join(f"{t['type']}..{t['last4']}={t['amount']}" for t in r["tenders"]) or "-"
        proj = (r.get("project") or "-")[:24]
        tot = r.get("total")
        extra = f"  ORIG:{len(r['orig_recs'])}" if r.get("orig_recs") else ""
        print(f"{r['file'][:28]:28} {typ:7} {proj:24} {str(tot):>10}  {tend}{extra}")

    print(f"\n--- TENDER ROSTER (fill in QBO account for each) -> {roster_path} ---")
    for (last4, ntype), v in sorted(roster.items(), key=lambda kv: -kv[1]["count"]):
        sc = "  <-- STORE CREDIT" if ntype == "STORE CREDIT" else ""
        print(f"  ..{last4}  {ntype:14} seen {v['count']}x{sc}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        fail(f"unexpected error: {e}")
