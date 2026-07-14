#!/usr/bin/env python3
"""
LEGACY / ALTERNATIVE ledger builder. Prefer build_ledger.py, which builds from the
order-history API pull and can include returns and cancellations that Home Depot's
CSV export cannot contain (see docs/03-how-it-works.md). Use this script only if you
can't run the browser-based order-history pull.

Merges the Home Depot CSV (master spine: every transaction) with parsed receipt
detail (return->original-project links, true store-credit card #s, split tenders).

Inputs:
  receipts.json                        (from parse_receipts.py; default: current
                                         directory, falling back to this script's
                                         directory)
  Purchase_History_*_(1).csv           (transaction-level, in --csv-dir)
  Purchase_History_*.csv               (line-level: per-SKU project, in --csv-dir)

Output:
  ledger.csv  — one row per CSV transaction-row, enriched with receipt detail,
                with a project assignment, store-credit handling, and review flag.
                Defaults to the current directory; override with -o/--output.

Decisions baked in (confirmed 2026-06-29):
  - CSV is the spine; receipts are the detail layer.
  - All store credit -> one shared "HD Store Credit" account.
"""
import argparse, json, csv, re, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
STORE_CREDIT_ACCT = "HD Store Credit"   # single shared clearing account

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

def load_csv(fn):
    rows = list(csv.reader(open(fn, encoding="latin-1")))
    for i, row in enumerate(rows):
        if row and row[0].strip() == "Date":
            return row, [r for r in rows[i + 1:] if len(r) >= len(row)]
    return None, []

def money(x):
    x = x.replace("$", "").replace(",", "").strip()
    try: return round(float(x), 2)
    except ValueError: return None

def recdate(r):
    dt = r.get("datetime") or ""
    m = re.match(r'(\d{2})/(\d{2})/(\d{2})', dt)
    return f"20{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else None

def parse_args():
    p = argparse.ArgumentParser(
        prog="merge_ledger.py",
        description="LEGACY/ALTERNATIVE ledger builder: merges Home Depot's Purchase-History "
                     "CSV export (the spine) with receipts.json (the detail layer). Prefer "
                     "build_ledger.py — it builds from the order-history API pull, which "
                     "(unlike this CSV export) can include returns and cancellations. Writes "
                     "ledger.csv to the current directory unless -o/--output says otherwise.",
    )
    p.add_argument("--csv-dir", metavar="DIR", default=".",
                    help="Folder holding the two Purchase_History_*.csv exports from Home "
                         "Depot's Purchase History page: one transaction-level file (name "
                         "ends \"(1).csv\") and one line-level file (default: current directory)")
    p.add_argument("--receipts", metavar="PATH",
                    help="Path to receipts.json, produced by parse_receipts.py (default: "
                         "./receipts.json, falling back to this script's directory)")
    p.add_argument("-o", "--output", metavar="PATH", default="ledger.csv",
                    help="Where to write the ledger (default: ./ledger.csv)")
    return p.parse_args()

def main():
    args = parse_args()

    receipts_path = resolve_input("receipts.json", args.receipts)
    if not os.path.exists(receipts_path):
        fail(
            f"receipts.json not found: {receipts_path}\n"
            "  Generate it first: python3 src/parse_receipts.py downloads\n"
            "  or pass --receipts PATH."
        )
    try:
        recs = json.load(open(receipts_path))
    except json.JSONDecodeError as e:
        fail(f"{receipts_path} is not valid JSON ({e})")

    if not os.path.isdir(args.csv_dir):
        fail(
            f"--csv-dir not found: {args.csv_dir}\n"
            "  This should be the folder holding the two Purchase_History CSV exports from\n"
            "  Home Depot's Purchase History page (Export button): one transaction-level file\n"
            "  (name ends \"(1).csv\") and one line-level file (per-SKU project)."
        )
    files = os.listdir(args.csv_dir)
    txn_csvs = [f for f in files if f.endswith("(1).csv")]
    line_csvs = [f for f in files if f.endswith(".csv") and "(1)" not in f]
    if not txn_csvs or not line_csvs:
        fail(
            f"Purchase_History CSV pair not found in {args.csv_dir}\n"
            "  Expected two exports from Home Depot's Purchase History page: a "
            "transaction-level CSV (filename ends \"(1).csv\") and a line-level CSV "
            "(per-SKU project, filename has no \"(1)\"). Pass --csv-dir to point elsewhere."
        )
    txn_csv, line_csv = txn_csvs[0], line_csvs[0]
    Sh, Sd = load_csv(os.path.join(args.csv_dir, txn_csv))
    S = {c: i for i, c in enumerate(Sh)}

    # Line-level CSV: Order# -> project(s). Lets returns with a blank job but an
    # Order# recover the ORIGINAL project without needing an email receipt.
    Lh, Ld = load_csv(os.path.join(args.csv_dir, line_csv))
    L = {c: i for i, c in enumerate(Lh)}
    order_to_jobs = {}
    for r in Ld:
        o, j = r[L["Order Number"]].strip(), r[L["Job Name"]].strip()
        if o and j:
            order_to_jobs.setdefault(o, set()).add(j)

    # ---- Receipt lookup indexes ----
    rec_by_order = {}
    rec_by_dt = {}      # (date, SIGNED total) -> [receipts]  (sign keeps returns/sales apart)
    for r in recs:
        if r.get("order"):
            rec_by_order.setdefault(r["order"], r)
        d, t = recdate(r), r.get("total")
        if d and t is not None:
            rec_by_dt.setdefault((d, round(t, 2)), []).append(r)

    def match_receipt(row, is_return):
        """Match the transaction's OWN receipt (for tender detail). Gated on sign so a
        return row never grabs its original-purchase receipt."""
        o = row[S["Order Number"]].strip()
        # order# match only when the receipt's return/sale nature matches the row's
        if o and o in rec_by_order and bool(rec_by_order[o].get("is_return")) == is_return:
            return rec_by_order[o], "order#"
        d = row[S["Date"]].strip()
        tot = money(row[S["Total Amount Paid"]])
        if tot is None:
            return None, ""
        cand = rec_by_dt.get((d, round(tot, 2)), [])
        if len(cand) == 1:
            return cand[0], "date+amt"
        if len(cand) > 1:
            pay = set(re.findall(r'(\d{4})', row[S["Payment"]]))
            for c in cand:
                if pay & set(t["last4"] for t in c.get("tenders", [])):
                    return c, "date+amt+card"
            return cand[0], "date+amt(ambig)"
        return None, ""

    out_rows = []
    stats = {"total": 0, "with_receipt": 0, "returns": 0,
             "return_proj_recovered": 0, "store_credit_txns": 0, "needs_review": 0}

    for row in Sd:
        stats["total"] += 1
        date = row[S["Date"]].strip()
        txid = row[S["Transaction ID"]].strip()
        order = row[S["Order Number"]].strip()
        job = row[S["Job Name"]].strip()
        total = money(row[S["Total Amount Paid"]])
        pay = row[S["Payment"]].strip()
        is_return = (total is not None and total < 0)
        if is_return:
            stats["returns"] += 1

        rec, how = match_receipt(row, is_return)
        if rec:
            stats["with_receipt"] += 1

        # --- project assignment ---
        review = []
        if is_return:
            # prefer receipt-resolved original project(s)
            resolved = rec.get("resolved_projects") if rec else None
            if resolved:
                project = " | ".join(resolved)
                stats["return_proj_recovered"] += 1
            elif order and order in order_to_jobs:
                # CSV-internal: original order's project from the line-level CSV
                project = " | ".join(sorted(order_to_jobs[order])) + " (via order#)"
                stats["return_proj_recovered"] += 1
            elif job:
                project = job + " (return, on-receipt job)"
            else:
                project = ""
                review.append("return-project-unknown")
        else:
            project = job
            if not project:
                review.append("no-project")

        # --- tenders: prefer real receipt tenders (true store-credit #s, splits) ---
        if rec and rec.get("tenders"):
            tenders = rec["tenders"]
            tender_src = "receipt"
        else:
            tenders = [{"last4": x, "type": "?", "amount": None}
                       for x in re.findall(r'(\d{4})', pay)] or \
                      ([{"last4": "STORECREDIT", "type": "STORE CREDIT", "amount": None}]
                       if re.search(r'[A-F0-9]{12,}', pay) else [])
            tender_src = "csv"

        sc_amt = round(sum(t["amount"] for t in tenders
                           if t.get("amount") and "STORE CREDIT" in t["type"].upper()), 2)
        if sc_amt:
            stats["store_credit_txns"] += 1

        tender_str = " | ".join(
            f"{t['type']}..{t['last4']}" + (f"={t['amount']}" if t.get('amount') is not None else "")
            for t in tenders)

        if review:
            stats["needs_review"] += 1

        out_rows.append({
            "date": date, "transaction_id": txid, "order_number": order,
            "type": "RETURN" if is_return else "SALE",
            "project": project,
            "pretax": money(row[S["Pre-tax Amount"]]),
            "total": total,
            "tenders": tender_str,
            "tender_source": tender_src,
            "store_credit_amt": sc_amt or "",
            "store_credit_acct": STORE_CREDIT_ACCT if sc_amt else "",
            "has_receipt": "YES" if rec else "",
            "match": how,
            "receipt_file": (rec or {}).get("file", ""),
            "needs_review": "YES" if review else "",
            "review_reason": "; ".join(review),
        })

    if not out_rows:
        fail(f"no transaction rows found in {txn_csv} — nothing to write.")

    cols = list(out_rows[0].keys())
    with open(args.output, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out_rows)

    print(f"ledger.csv written: {len(out_rows)} transaction-rows\n")
    for k, v in stats.items():
        print(f"  {k:24} {v}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        fail(f"unexpected error: {e}")
