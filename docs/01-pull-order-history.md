# 01 · Pull your complete Home Depot order history

The order-history pull is the **gold source** for the ledger: unlike the CSV export, it
includes **returns and cancellations**, split tenders, the store, and the PO/Job on every
transaction.

## The easy way: paste the script

You don't have to write a pagination script yourself —
[`src/pull_orderhistory.js`](../src/pull_orderhistory.js) does the whole pull for you, right in
your browser:

1. Sign in at homedepot.com and open **Purchase History**
   (`https://www.homedepot.com/myaccount/purchase-history`).
2. Open DevTools (F12, or Cmd+Opt+I / Ctrl+Shift+I) and switch to the **Console** tab. Some
   browsers — Chrome included — block pasting into the Console and show a warning first; if so,
   type `allow pasting` and press Enter before continuing.
3. Paste the **entire contents** of [`src/pull_orderhistory.js`](../src/pull_orderhistory.js)
   into the Console and press Enter.
4. Follow the prompts. If you already know your IDs, fill `USER_ID` and `CUSTOMER_ACCOUNT_ID`
   into the consts at the top of the file before pasting. Otherwise leave them blank — the
   script auto-captures both from the next `orderhistory` request your browser makes, so just
   click to another page of Purchase History or change a filter.
5. When it's done, your browser downloads **`hd_orderhistory_full.json`**. Put it in the repo
   root (next to `src/`) — that's where `build_ledger.py` reads it from.

Everything below documents the endpoint the script uses — useful if you want to run or adapt
the pull yourself.

> **Why not the CSV export?** Home Depot's Purchase-History "Export" button says it up front:
> the CSV **cannot contain returns or cancellations**, and it collapses every store-credit
> card into one token. The website's Purchase History is backed by an API that returns
> *everything* — that's what we use. The "QuickBooks Export" button on that page pushes
> directly to a single clearing account with **no job split** and is buggy; don't use it.

## What you need

- A Home Depot **Pro** account, signed in on homedepot.com in a normal browser.
- Your two account identifiers (below) — you read them from your own logged-in session.

## Find your two IDs

The endpoint needs two values that are specific to your account:

- `USER_ID` — appears in the request path.
- `customerAccountId` — **required** in the request body (omit it and the API returns
  *"Invalid customer account number"*).

To get them, sign in at homedepot.com, open **Purchase History**
(`https://www.homedepot.com/myaccount/purchase-history`), open your browser's **DevTools →
Network** tab, reload the page, and look for the `orderhistory` request. Its URL contains
your `USER_ID`; its request payload contains your `customerAccountId`. Copy both.

## Endpoint

```
POST https://www.homedepot.com/oms/customer/order/v1/user/{USER_ID}/orderhistory
```

### Headers

```
Accept: application/json, text/plain, */*
Content-Type: application/json
channelId: 1
Client: ocm_pd_experience_customer-account-orders-purchases
channel: desktop
X-Client-App: PHX-Desktop
```

Do **not** replay stale `newrelic` / `traceparent` / `X-B3-TraceId` headers — replaying them
causes a 400. They aren't required.

### Body

```json
{"orderHistoryRequest":{
  "pageSize":500,"pageNumber":1,
  "startDate":"2023-01-01","endDate":"2026-06-29",
  "customerAccountId":"{CUSTOMER_ACCOUNT_ID}",
  "sortBy":"salesDate","sortOrder":"desc",
  "searchType":"ORDERS","resultsFilter":"allOrders",
  "timezone":"America/New_York","searchValue":""}}
```

- The API returns ~495 orders per page regardless of `pageSize`. **Paginate** `pageNumber`
  from 1 upward until you've collected the reported `orderCount`.
- It **must** run inside an authenticated homedepot.com session (it relies on your cookies).
  Run it from the DevTools **Console**, or drive it with a browser-automation tool. A plain
  server-side `fetch` won't carry the session and will fail.

## Response shape (per order)

```
{ salesDate,
  transactionType,          // Sale | Return | Cancel | SaleRering | ReturnRering
  orderOrigin,              // store ("#0000, City") or "online"
  storeNumber,
  POJobName,                // the project/job — this is what the ledger keys on
  totalAmount,              // negative for returns
  preTaxAmount,
  transactionId,
  receiptDetails,           // register locator
  invoiceNumbers[],
  tenders: [ { type/net, value /* last4 */, amount } ],
  receiptAddedDate, ... }
```

### Tender network codes

| Code | Meaning |
| --- | --- |
| `VI` | Visa |
| `AX` | Amex |
| `MA` | Mastercard |
| `DS` | Discover |
| `DB` | Debit |
| `HD` | Home Depot card |
| `GR` | Gift / merchandise-credit card — **this is "store credit"** |
| `CS` | Cash / store credit |
| `PERK_GC` | Pro Xtra perks gift card |
| `ED` | Other |

## Save the result

Collect every page, de-duplicate by `transactionId | date | total | store`, build compact
rows, and write them to **`hd_orderhistory_full.json`** in the repo root. `build_ledger.py`
reads that file. The expected shape is:

```json
{ "pulled": 3095, "rows": [ { "date": "...", "type": "Sale", "origin": "...", "store": 0000,
  "job": "...", "total": 0.00, "pretax": 0.00, "tx": 0, "receipt": "...",
  "invoices": ["..."], "tenders": [ { "net": "VI", "last4": "0000", "amt": "-0.00" } ] } ] }
```

See [`../examples/sample_orderhistory.json`](../examples/sample_orderhistory.json) for a
tiny synthetic example with the exact keys.

> **Note on cancellations:** even this gold source has a blind spot — a cancellation (an item
> pulled from an order before fulfillment) comes through as a **zeroed** `Cancel` row, and
> some cancellation orders don't appear at all. Those surface only as an unmatched card
> credit in your bank feed. See [04-quickbooks-workflow.md](04-quickbooks-workflow.md) §
> *Cancellations*.
