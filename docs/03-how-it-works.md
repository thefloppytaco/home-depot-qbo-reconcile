# 03 · How it works (the data model)

This explains what the scripts actually do under the hood, so you can trust the ledger and
adapt it.

## The two receipt formats

`parse_receipts.py` handles both kinds of Home Depot receipt:

1. **eReceipt / register receipt** — in-store sales and returns. Fully itemized in the HTML
   email body (and in the register PDF). Has a header line like:

   ```
   2584  00016  12498  06/15/26  01:11 PM
   store  reg    txn    date      time
   ```

2. **Customer Receipt / Special Services Invoice** — ProDesk / desk orders (order numbers
   look like `H2584-526432`). Itemization lives in the **PDF attachment**; the email body is
   just a wrapper. A "Payment Method" block lists tenders as `Amex 1000 Charged $264.89`.

The parser sniffs the text (`Customer Receipt` / `SPECIAL SERVICES` / `Payment Method`) and
routes to the right extractor. PDFs are read with `pdftotext -layout` (Poppler), which keeps
each tender amount on the same line as its card number.

## What gets extracted per receipt

`type` (SALE/RETURN), `store`, `datetime`, `project` (from `PO/JOB NAME:`), `total`,
`tenders` as `(last4, type, amount)`, and — for returns — the `ORIG REC` links back to the
original purchase. Output lands in `receipts.json`.

## Register locators & linking returns to their original project

This is the clever part. A **return doesn't carry its own job** — the cost belongs to
whatever project the *original purchase* was booked to. Each register sale has a locator:

```
store | register | txn# | date
```

and a return prints an `ORIG REC:` line pointing at the original sale's locator. The parser
**normalizes** both (stripping leading zeros, since the header uses `00016` while the
`ORIG REC` uses `090`) so they match, then builds an index of every sale's
`locator → project`. For each return it looks up the `ORIG REC` links and recovers the
original project. H-order returns are resolved the same way via the order number.

If a return can't be linked (its original sale isn't in your corpus), it's flagged for
review rather than guessed.

## Split tenders and the store-credit blob problem

One receipt can be paid with several cards **plus** one or more store-credit (merchandise)
cards. The parser reads every tender line in two styles on the same receipt:

```
XXXXXXXX6841 STORE CREDIT   0.94       (amount inline)
XXXXXXXX0641 VISA                       (amount on the next USD$ line)
  USD$ 58.26
```

Crucially, the receipts expose the **real, distinct store-credit card numbers**, where the
CSV/API collapse them into one token. That matters because store credit is fungible across
cards but must still be accounted for.

## Store-credit routing

All store-credit / merchandise-credit tenders route to a **single shared clearing account**
named **"HD Store Credit"** in the ledger. Rationale: the physical store-credit cards are
fungible (a return can be split across four of them, then spent from a fifth), so tracking
them individually adds noise without value. One pooled account that mirrors the cards' total
balance is enough. How to actually book that in QuickBooks is in
[04-quickbooks-workflow.md](04-quickbooks-workflow.md).

## The tender sign convention

In `ledger.csv`, the `tenders` field reads `Type..last4=amount`, where:

- **negative = a card charge** (a purchase), and
- **positive = a credit** (a return).

Keep this straight when you post to QuickBooks.

## Review flags

Rows are flagged `needs_review = YES` when a human should look:

- a return whose original project couldn't be resolved,
- tenders that don't sum to the total,
- more than one store-credit card on a receipt,
- a blank PO/Job,
- a cancellation.

In practice this is a small minority of rows; the rest flow straight through.

## Two builders, one output

- **`build_ledger.py`** (recommended) builds from `hd_orderhistory_full.json` (the gold
  source with returns + cancellations) and uses the parsed receipts only to recover the
  project on returns that came through with a blank job.
- **`merge_ledger.py`** (legacy) builds the same `ledger.csv` from Home Depot's CSV export
  instead. Use it only if you can't run the browser pull — you lose return/cancellation
  detail.

Both emit the same columns, described in the main [README](../README.md#output-ledgercsv).
