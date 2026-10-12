# 10 · What Home Depot's site gives you

Everything the sync knows comes from five calls the Purchase History page makes for itself.
They only work inside a signed-in homedepot.com tab (they rely on its cookies). The browser
scripts run there for that reason: [`src/fetch_receipts.js`](../src/fetch_receipts.js) (weekly, or
one job) and [`src/pull_orderhistory.js`](../src/pull_orderhistory.js) (whole history). This page
records what each call returns and the quirks that cost real time to learn. Observed on live
accounts through October 2026; Home Depot can change any of it.

All calls are `POST https://www.homedepot.com/oms/customer/order/v1/user/{USER_ID}/…` with the
headers in [01-pull-order-history.md](01-pull-order-history.md#headers). Bodies carry your
`customerAccountId`. The scripts capture both IDs from the page's own traffic: run the script,
then click to page 2 of Purchase History once.

## 1. `orderhistory` — the list

One row per transaction: date, type (`Sale`, `Return`, `Cancel`…), store, register, transaction
number (with and without check digit), `POJobName` (the online PO), totals, tenders (network code +
last-4 + amount), invoice and order numbers. Full field list and paging: [01](01-pull-order-history.md).

- Ask for a ~24-month window and filter locally. Narrower ranges have come back as an **empty
  success** (`orderCount: 0`), and so has `pageSize: 500` at times (retry at 60).
- `searchValue` filters by job text (substring, any spelling) or by order number.
- A **batch return** appears as one row with tenders plus tender-less sub-rows. A register return
  that includes an **online / Pro Desk order** appears twice with tenders: the whole receipt, and an
  order-return row repeating the order's part. Book the receipt once.
- `Cancel` rows are zeroed: they say *that* something was canceled, not how much.

## 2. `orderdetails` — the items

Body: `{"orderDetailsRequest": {"searchType": "instore", "salesDate", "storeNumber", "registerNumber",
"transactionId", "orderNumber", "customerAccountId", "groupAndSortBy": {"groupBy": "fulfillmentType",
"filterResponse": true}, "requestOriginatedFrom": "orderDetails", "timezone"}}`. It works for ordinary
register receipts, not just online orders.

- Each line: SKU, catalog and register descriptions, quantity, `originalUnitPrice` (list),
  `netUnitPrice` (what you paid), `pretaxTotal` (list × qty), `totalDiscountAmount`, per-line
  `taxAmount`, department, status.
- **Use net unit price × quantity.** The per-line discount's sign isn't consistent, and the order
  subtotal is sometimes list, sometimes net. Σ net × qty = the row's pretax is the check.
- Order totals (`grandTotalAmount`, `subTotalAmount`, `taxTotalAmount`), the online PO, tenders, and
  `chargeTransactions` (below).
- On a single-origin return, `accountIdType: "related_transaction_key"` and `accountIdValue` =
  `register;store;date;transaction` of the original.
- **Otherwise `accountIdValue` is your full card number.** Never print or save it. The script keeps
  the field only in the related-transaction case.
- Sometimes the answer describes a **different transaction** than you asked for: the original order
  of an order return, or a later return of a sale. Compare the returned store / transaction / date
  with the row (the scripts save them as `od.head`). On a mismatch, use the printed receipt.
- Fee lines (e.g. a state paint-care fee) can come back with no price.

## 3. `receipt/view` — the printed receipt

Body: `{"customerAccountId", "encryptedReceiptRequest"}` (the key comes with the order-history row or
the order details). It returns the receipt as HTML. Its text has things no other call has:

- `PO/JOB NAME:` — the PO typed at the register. It wins over the online PO.
- `XXXX1234 STORE CREDIT -50.00` / `CARD BALANCE 12.10` — each store-credit card used, and its
  balance after.
- On returns, `* ORIG REC: 0000 050 31311 09/18/26 *` blocks (store, register, transaction with check
  digit, date of the original), each followed by its items and usually its own `SALES TAX` line.
  `ORIG REC: NO RECEIPT` = no original. One-block receipts often print tax only after `SUBTOTAL`.
  The block's tax is then whatever receipt tax is left.
- Receipts for today's transactions can take hours to appear.

## 4. `receipt/view/bigReceipt` — the Pro Desk order PDF

Body: `{"customerAccountId", "encryptedBigReceiptRequest"}`, where the key comes from the order
details' `encryptedBigReceiptRequests[orderNumber]`. It returns a PDF. Desk and online orders have no
register receipt, so this is where their printed PO lives: in the PDF's text it's on the line
**before** the "PO / Job Name" label. The line after is the fulfillment type, e.g. "Carryout".

## 5. Charge history (`chargeTransactions` in the order details)

The card charges and credits Home Depot actually ran for an order. It's the only place price
adjustments and cancellations show up: when the order's current total is below what the card was
charged, a credit is coming (or came). `gen_entries.py` flags it, and
[`cancellation-sweep`](../ai/skills/cancellation-sweep.SKILL.md) uses it. The bank posts on the
charge date, which can be days after the order date.

## Return originals: how the sync finds them

ORIG REC → the original in `ledger.csv` by store + date + transaction (the printed number has a
check digit: `31311 // 10 = 3131`). Online orders can print a date a day or two off the history's
date, so the match allows ±3 days. If that misses, the original is looked up by order number (an
order-return row's order number tells the whole receipt which order a block came from). Then the
job is the one that original was **booked** to: `line_items.csv` first, then the booking logs,
then the original's PO.
