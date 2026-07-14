#!/usr/bin/env python3
"""
Build the project-expense ledger from the AUTHORITATIVE order-history API pull
(hd_orderhistory_full.json) — the gold source that includes returns, cancellations,
online orders, and split tenders. Supersedes the old CSV-based merge.

Enrichment: for the handful of returns with a blank project, fall back to the email
receipt resolver (receipts.json) matched by date + amount + card last-4. Optionally,
--csv-dir adds a second fallback resolver using the two legacy Purchase_History CSVs.

Run:  python3 build_ledger.py                (uses defaults; see --help for flags)
Inputs (hd_orderhistory_full.json, receipts.json) default to the current directory,
falling back to this script's directory for backward compatibility. Output defaults
to the current directory too.

Output: ledger.csv  (one row per HD transaction)
Decisions: store credit (gift/merch cards) -> one shared "HD Store Credit" account.
"""
import argparse, json, csv, os, sys, re
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
STORE_CREDIT_ACCT = "HD Store Credit"

# Tender network codes -> (readable, is_store_credit)
NET = {
    "VI": ("Visa", False), "AX": ("Amex", False), "MA": ("Mastercard", False),
    "DS": ("Discover", False), "DB": ("Debit", False), "HD": ("HD Card", False),
    "GR": ("Gift/Store Credit", True), "CS": ("Cash/Store Credit", True),
    "PERK_GC": ("Pro Xtra Perks GC", True), "ED": ("EBT/Other", False),
}

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

def money(v):
    try: return round(float(v), 2)
    except (TypeError, ValueError): return None

def load_receipt_resolver(path):
    """date|amount|last4 -> resolved original project(s), from email receipts."""
    if not path or not os.path.exists(path):
        return {}
    recs = json.load(open(path))
    idx = {}
    for r in recs:
        if not r.get("is_return"):
            continue
        proj = r.get("resolved_projects")
        if not proj:
            continue
        dt = r.get("datetime") or ""
        m = re.match(r'(\d{2})/(\d{2})/(\d{2})', dt)
        date = f"20{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else None
        for t in r.get("tenders", []):
            if t.get("amount") is None:
                continue
            key = (date, abs(round(t["amount"], 2)), t["last4"])
            idx[key] = " | ".join(proj)
    return idx

def load_csv_return_resolver(csv_dir):
    """(date, abs(total), last4) -> original project, from the two purchase-history CSVs.
    The transaction-level CSV's returns carry the ORIGINAL order#; the line-level CSV maps
    order# -> Job Name. This recovers projects for order-history returns that have a blank job.
    Only runs when --csv-dir is given; skipped entirely when it's omitted."""
    if not csv_dir:
        return {}
    if not os.path.isdir(csv_dir):
        print(f"Note: --csv-dir {csv_dir} not found; skipping the CSV return resolver.",
              file=sys.stderr)
        return {}
    files = os.listdir(csv_dir)
    txn = next((f for f in files if f.endswith("(1).csv")), None)
    line = next((f for f in files if f.endswith(".csv") and "(1)" not in f), None)
    if not (txn and line):
        print(f"Note: no Purchase_History CSV pair found in --csv-dir {csv_dir}; "
              "skipping the CSV return resolver.", file=sys.stderr)
        return {}

    def load(fn):
        rws = list(csv.reader(open(os.path.join(csv_dir, fn), encoding="latin-1")))
        for i, r in enumerate(rws):
            if r and r[0].strip() == "Date":
                return r, [x for x in rws[i + 1:] if len(x) >= len(r)]
        return None, []

    Lh, Ld = load(line); L = {c: i for i, c in enumerate(Lh)}
    order2job = {}
    for r in Ld:
        o, j = r[L["Order Number"]].strip(), r[L["Job Name"]].strip()
        if o and j:
            order2job.setdefault(o, j)

    Sh, Sd = load(txn); S = {c: i for i, c in enumerate(Sh)}
    idx = {}
    for r in Sd:
        tot = r[S["Total Amount Paid"]].replace("$", "").replace(",", "")
        try:
            t = float(tot)
        except ValueError:
            continue
        if t >= 0:
            continue
        date = r[S["Date"]].strip()
        job = r[S["Job Name"]].strip()
        order = r[S["Order Number"]].strip()
        proj = job or order2job.get(order, "")
        if not proj:
            continue
        for last4 in re.findall(r'(\d{4})', r[S["Payment"]]):
            idx.setdefault((date, abs(round(t, 2)), last4), proj)
    return idx

def parse_args():
    p = argparse.ArgumentParser(
        prog="build_ledger.py",
        description="Build ledger.csv from the Home Depot order-history API pull "
                     "(hd_orderhistory_full.json) — the authoritative source that includes "
                     "returns, cancellations, and split tenders. Optionally recovers the "
                     "project on blank-job returns from receipts.json and/or a legacy "
                     "Purchase_History CSV pair. Writes ledger.csv to the current directory "
                     "unless -o/--output says otherwise.",
    )
    p.add_argument("--orderhistory", metavar="PATH",
                    help="Path to hd_orderhistory_full.json (default: ./hd_orderhistory_full.json, "
                         "falling back to this script's directory)")
    p.add_argument("--receipts", metavar="PATH",
                    help="Path to receipts.json for return-project enrichment; optional and "
                         "skipped if not found (default: ./receipts.json, falling back to "
                         "this script's directory)")
    p.add_argument("--csv-dir", metavar="DIR",
                    help="Folder with the two legacy Purchase_History_*.csv exports, used only "
                         "as a second fallback to recover a blank-job return's original project. "
                         "Optional; the CSV resolver is skipped entirely when this is omitted")
    p.add_argument("-o", "--output", metavar="PATH", default="ledger.csv",
                    help="Where to write the ledger (default: ./ledger.csv)")
    return p.parse_args()

def main():
    args = parse_args()

    orderhistory_path = resolve_input("hd_orderhistory_full.json", args.orderhistory)
    if not os.path.exists(orderhistory_path):
        fail(
            f"order-history file not found: {orderhistory_path}\n"
            "  Pull your Home Depot order history first — see docs/01-pull-order-history.md\n"
            "  (src/pull_orderhistory.js can help automate the browser-console pull) — then\n"
            "  save the result as hd_orderhistory_full.json in this folder, or pass\n"
            "  --orderhistory PATH."
        )
    try:
        data = json.load(open(orderhistory_path))
    except json.JSONDecodeError as e:
        fail(f"{orderhistory_path} is not valid JSON ({e})")

    try:
        rows = data["rows"]
    except (KeyError, TypeError):
        fail(f"{orderhistory_path} is missing the expected \"rows\" list — "
             "is this a real order-history export? See docs/01-pull-order-history.md.")

    receipts_path = resolve_input("receipts.json", args.receipts)
    resolver = load_receipt_resolver(receipts_path)
    csv_resolver = load_csv_return_resolver(args.csv_dir)

    out = []
    stats = defaultdict(int)
    for o in rows:
        typ = o["type"]
        total = money(o["total"])
        is_return = ("Return" in typ) or (total is not None and total < 0)
        is_cancel = (typ == "Cancel")
        stats["total"] += 1
        stats[f"type:{typ}"] += 1

        # tenders
        tenders = []
        sc_amt = 0.0
        for t in o.get("tenders", []):
            net = t.get("net") or t.get("type") or "?"
            name, is_sc = NET.get(net, (net or "?", False))
            amt = money(t.get("amt"))
            tenders.append(f"{name}..{t.get('last4','')}={amt}")
            if is_sc and amt:
                sc_amt += abs(amt)
        sc_amt = round(sc_amt, 2)
        if sc_amt:
            stats["store_credit_txns"] += 1

        # project
        review = []
        project = o.get("job") or ""
        if is_return and not project:
            # fall back to email-receipt resolver by date+amount+last4
            hit = None; src = ""
            for t in o.get("tenders", []):
                key = (o["date"], abs(money(t.get("amt")) or 0), t.get("last4"))
                if key in resolver:
                    hit, src = resolver[key], "via receipt"; break
                if key in csv_resolver:
                    hit, src = csv_resolver[key], "via order#"; break
            if hit:
                project = f"{hit} ({src})"
                stats[f"return_recovered_{src.replace(' ','_').replace('#','')}"] += 1
            else:
                review.append("return-project-unknown")
                stats["return_project_unknown"] += 1
        elif not project and not is_cancel:
            review.append("no-project")

        if is_cancel:
            review.append("cancellation")

        out.append({
            "date": o["date"],
            "type": typ,
            "origin": o.get("origin", ""),
            "store": o.get("store", ""),
            "project": project,
            "total": total,
            "pretax": money(o.get("pretax")),
            "transaction_id": o.get("tx", ""),
            "receipt_locator": o.get("receipt", ""),
            "invoices": "|".join(o.get("invoices") or []),
            "tenders": " | ".join(tenders),
            "store_credit_amt": sc_amt or "",
            "store_credit_acct": STORE_CREDIT_ACCT if sc_amt else "",
            "needs_review": "YES" if review else "",
            "review_reason": "; ".join(review),
        })

    if not out:
        fail(f"{orderhistory_path} has no rows — nothing to write.")

    cols = list(out[0].keys())
    with open(args.output, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader(); w.writerows(out)

    print(f"ledger.csv rebuilt from order-history API: {len(out)} transactions\n")
    for k in sorted(stats):
        print(f"  {k:32} {stats[k]}")
    nr = sum(1 for r in out if r["needs_review"])
    print(f"\n  needs_review                     {nr}  ({len(out)-nr} clean)")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        fail(f"unexpected error: {e}")
