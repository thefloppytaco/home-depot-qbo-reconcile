#!/usr/bin/env python3
"""
Run the full Home Depot -> QuickBooks pipeline end to end on the synthetic
examples in this folder, in one command:

    python3 examples/run_demo.py

Steps: parse_receipts.py over examples/ (the two synthetic receipts) ->
build_ledger.py against sample_orderhistory.json + those receipts -> print the
resulting ledger.csv as a table. Output lands in examples/demo-output/
(git-ignored; regenerated fresh on every run). Stdlib only.
"""
import csv, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))   # .../examples
REPO = os.path.dirname(HERE)                         # repo root
OUTDIR = os.path.join(HERE, "demo-output")


def run(cmd):
    print(f"$ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, cwd=REPO)
    if result.returncode != 0:
        sys.exit(result.returncode)


def main():
    os.makedirs(OUTDIR, exist_ok=True)

    run([sys.executable, "src/parse_receipts.py", "examples",
         "-o", "examples/demo-output"])

    run([sys.executable, "src/build_ledger.py",
         "--orderhistory", "examples/sample_orderhistory.json",
         "--receipts", "examples/demo-output/receipts.json",
         "-o", "examples/demo-output/ledger.csv"])

    print("\n--- examples/demo-output/ledger.csv ---\n")
    with open(os.path.join(OUTDIR, "ledger.csv"), newline="") as f:
        rows = list(csv.reader(f))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    for row in rows:
        print("  ".join(cell.ljust(w) for cell, w in zip(row, widths)))

    print(
        "\nWhat to notice:\n"
        "  - 2026-06-28 Return: resolved to project '456 SAMPLE AVE (via receipt)' --\n"
        "    the order history row had a BLANK job; the receipt's ORIG REC link back\n"
        "    to the original sale recovered it. needs_review is empty.\n"
        "  - Store-credit tenders (card ..2222) route to the shared 'HD Store Credit'\n"
        "    account instead of being tracked per physical card.\n"
        "  - 2026-06-27 Cancel: stays needs_review=YES (review_reason=cancellation) --\n"
        "    cancellations never resolve automatically; a human confirms them.\n"
    )


if __name__ == "__main__":
    main()
