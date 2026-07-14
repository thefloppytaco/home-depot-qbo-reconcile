# Examples (synthetic)

Fictional data — safe to commit. They show the shapes the tools read and write, and the
whole pipeline is runnable end to end against them.

## Run the demo

```
python3 examples/run_demo.py
```

Parses the two sample receipts below, builds the ledger from `sample_orderhistory.json`
enriched with those receipts, and prints the result as a table. No credentials, no real
data, and no `pdftotext`/Poppler install needed — the sample receipts are HTML.

## Files

- **`sample_orderhistory.json`** — the shape of `hd_orderhistory_full.json` (see
  [docs/01](../docs/01-pull-order-history.md)). Four rows: two sales, one split-tender
  return with a **blank job**, one zeroed cancellation.
- **`sample_receipt_sale.html`** — a synthetic eReceipt email for the *original* purchase
  behind that return (register `00052`, transaction `71234`, `06/20/26`,
  `PO/JOB NAME: 456 SAMPLE AVE`).
- **`sample_receipt_return.html`** — the synthetic return of that same purchase; its
  `ORIG REC:` line links back to the sale above. It deliberately carries **no**
  `PO/JOB NAME` — the project is recovered purely from that link, which is the point of
  this example.
- **`sample_ledger.csv`** — the exact output of `python3 examples/run_demo.py` (i.e.
  `parse_receipts.py` over this folder, then `build_ledger.py` against
  `sample_orderhistory.json` enriched with those receipts). This is the flagship feature
  in action: the 2026-06-28 Return row arrives from order history with a **blank job**,
  but resolves to project `456 SAMPLE AVE (via receipt)` with `needs_review` empty,
  because `parse_receipts.py` linked the return back to its original purchase via the
  `ORIG REC` locator. Run `build_ledger.py` *without* `--receipts` and that same row
  falls back to a blank project and `needs_review = return-project-unknown` instead — try
  it:

  ```
  python3 src/parse_receipts.py examples -o /tmp/demo
  python3 src/build_ledger.py --orderhistory examples/sample_orderhistory.json \
          -o /tmp/demo/ledger.csv   # no --receipts this time
  ```

  The 2026-06-27 Cancel row stays flagged `needs_review = YES` / `cancellation`
  either way — cancellations always need a human, by design.
- **`sample_tender_roster.csv`** — the shape of `tender_roster.csv`. Fill the
  `QBO_account_TO_FILL` column with the account each card's last-4 maps to.
