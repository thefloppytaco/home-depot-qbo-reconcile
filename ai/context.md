# AI context: the data model and the rules

This file is written for AI models working in or with this repo. It compresses the
domain knowledge from `docs/` into one place: every file shape, every sign convention,
and every bookkeeping invariant. If you follow only one file, follow this one.
Human-oriented explanations live in `docs/`; nothing here contradicts them.

## The one-paragraph domain

A contracting business buys and returns constantly at Home Depot across many job sites
("projects"), paying with several credit cards plus fungible store-credit cards. The goal
is a ledger with **one row per transaction, assigned to the right project**, that can be
reconciled into QuickBooks Online (QBO). The hard parts: returns don't carry their own
project (it must be recovered from the original purchase), store-credit legs and some
returns/cancellations **never appear in any bank feed**, and the same transaction shows
different dates in different systems.

Two ways to work, both in this repo. **The weekly sync** (recommended; `ai/skills/hd-sync.SKILL.md`)
fetches each new transaction's item detail and printed receipt straight from Home Depot's site,
drafts itemized QuickBooks entries per job and posts them after approval, so every bank-feed line has
an entry waiting to be Matched. **The ledger-first pipeline** (older) builds a multi-year
`ledger.csv` from the order history plus Gmail receipts. The sync also uses `ledger.csv`, to find
the original sale behind each return.

## Entities

- **Transaction** — one sale, return, or cancellation. Spine source: the order-history
  API pull. A transaction has 0..n **tender legs**.
- **Tender leg** — one payment instrument's share of a transaction (card, debit, or
  store-credit card), identified by network code + last-4 + amount.
- **Receipt** — the emailed PDF/HTML for a transaction. The *detail layer*: real
  store-credit card numbers, line items, and `ORIG REC` links from returns to originals.
  Only ~2/3 of transactions have one.
- **Register locator** — `store|register|txn#|date`, printed on every register receipt.
  A return's `ORIG REC:` line contains the *original sale's* locator. Normalize by
  stripping leading zeros from register/txn before comparing (`norm_locator` in
  `src/parse_receipts.py`).
- **Project (job)** — the `PO/JOB NAME` on the receipt / `POJobName` in the API. In QBO,
  projects are modeled as **customers' projects**; booking to a project means setting the
  customer reference.
- **Receipt bundle** — one line of `hd_bundles_*.jsonl` (from `src/fetch_receipts.js`): a
  transaction's order-history row + Home Depot's structured order details + the parsed printed
  receipt (+ the Pro Desk order PDF's PO). The sync's unit of work.
- **ORIG REC block** — on a printed return receipt, the items returned from one original purchase,
  headed `* ORIG REC: <store> <reg> <txn+check digit> <mm/dd/yy> *` (or `NO RECEIPT`), with their
  own `SALES TAX` line (sometimes missing). One return can hold several blocks from several jobs.
- **Sub-row** — an order-history row that repeats part of another row's receipt: the tender-less
  rows of a batch return, and the order-return row Home Depot adds when a register return includes
  an online / Pro Desk order. A sub-row's items live inside the receipt's entry; it is never booked.
- **Line-item tracker** (`line_items.csv`) — every booked item with its QuickBooks id. How a return
  finds the job its original was *booked* to.
- **"HD Store Credit" clearing account** — a single QBO account (type **Bank /
  Cash-on-hand**, so it can be a pay-from account) that pools ALL store-credit cards.
  Individual store-credit cards are fungible and are deliberately not tracked separately.

## File shapes (data dictionary)

### `hd_orderhistory_full.json` — the spine (input)

Produced by `src/pull_orderhistory.js` (or manually per `docs/01`) and extended each week by
`src/merge_orderhistory.py`. Shape:

```json
{ "pulled": "<ISO timestamp>", "rows": [ {
    "date": "YYYY-MM-DD",
    "type": "Sale | Return | Cancel | SaleRering | ReturnRering",
    "origin": "\"#0000, City\" or \"online\"",
    "store": <int>,
    "job": "project name, may be \"\"",
    "total": <float, NEGATIVE for returns, 0 for cancels>,
    "pretax": <float>,
    "tx": <transaction id>,
    "receipt": "register locator or \"\"",
    "invoices": ["..."],
    "tenders": [ { "net": "<code>", "last4": "0000", "amt": "<string, negative = charge>" } ],
    "register": <int>, "txc": <txn with check digit>, "order": "H####-###### or \"\""   (newer pulls)
} ] }
```

Older files have `"pulled": <count>` and no `register` / `txc` / `order`; every tool accepts both.

Tender network codes (`net`): `VI` Visa, `AX` Amex, `MA` Mastercard, `DS` Discover,
`DB` Debit, `HD` Home Depot card (a credit card, NOT store credit), `ED` other, and the
store-credit group: **`GR`** gift/merchandise card, **`CS`** cash/store credit,
**`PERK_GC`** Pro Xtra perks gift card.

### `hd_bundles_<date>.jsonl` — the week's receipts (from `src/fetch_receipts.js`)

Line 1: `{"_meta": {"from", "to", "job", "details", "rows", "fetched_at"}}` (`job` non-empty = a
job-filtered pull, never merged). Then one bundle per transaction:

```
i, d (date), t (type), s (store), reg, txc, tx, po_row (online PO), tot, pre, on (order #), nick,
inv, tn: [[net, last4, amount]], row: {the order-history row, shape above},
od:  {grand, sub, tax, disc, po_online, added, related ("reg;store;date;txn" of a single-origin
      return's original, else ""), head: [store, reg, txc, date] (what the details answered for),
      tn, ct: [{code, kind: debit|credit, amt}] (charge history), big: [order #s with a PDF],
      items: [{sku, desc, pos, qty, list, net, disc, pretax, tax, dept, st, ft}]}      | od_err
rc:  {printedPO, head, balances: [{last4, amt, balance}] (store-credit CARD BALANCE lines),
      blocks: [{ref: [store, reg, txc, mm/dd/yy] | null (NO RECEIPT), tax | null,
                items: [{upc, desc, qty, unit, total}]}], hasNoReceipt}                 | rc_err
big: {po} (the Pro Desk order PDF's printed PO)                                          | {err}
err  (the whole fetch for this row failed; `row` is still present)
```

Never add the order-details field `accountIdValue` — it holds the full card number. The fetch keeps
it only when `accountIdType` is `related_transaction_key` (then it's a transaction key, as `related`).

### `hd-sync.json` — the owner's map (private; template `templates/hd-sync.example.json`)

`company`, `cutoff_date`, `accounts {cogs, tools, rental?, store_credit}`, `vendor_hd`,
`cards {last4: account id}`, `never_book [last4]`, `ask_first [last4]`, `jobs {name: customer or
project id}`, `po_aliases {NORMALIZED PO PREFIX: job}`, `garbage_pos`, `tools_pos`,
`excluded_origin_refunds` (`ask` | `untagged`). Keys starting with `_` are comments. Found via
`$HD_SYNC_CONFIG`, else the current folder or up to three parents.

### Weekly working files (`sync/<date>/` and the repo root)

- `new_rows.csv` (merge) — `n, date, type, store, receipt, tx, order, po_online, total, pretax, tenders,
  nick, invoices, subrow_of, late, bucket, entry_key, qbo_id, notes`. Every row must get a `bucket`
  (`proposed`, `not-posted`, `sub-row`, `cancel`, `held`); `gen_entries.py` fills what it can.
- `job_diffs.csv` (merge) — fields that changed on rows already stored (a changed PO on a booked row).
- `entries.py` (draft from `gen_entries.py`; you resolve `# REVIEW` lines and run it) — calls to
  `add(key, date, receipt, order, store, job, card, type, amount, items, note=, groups=)` and
  `skip(...)`. Items are `(description, qty, unit, line_total)`, positive for sales and returns alike;
  `'Sales tax'` is a line. Types: `Sale`, `Return` (card credit), `Return (SC earn)` (refund to store
  credit → journal entry). `groups` (returns): `(job, customer|None, ORIG REC label, original's QBO id,
  items, tax)` — one per original.
- `entries.json`, `payloads.json` (`{key: {tool, params}}` — exact QuickBooks API bodies, not posted),
  `PROPOSAL.md`, `line_items_new.csv` (`qbo_id` = `PENDING-<key>` until close-out).
- `line_items.csv` (root) — `date, receipt_locator, order_number, store, job, card, type, item_desc, qty,
  unit_price, line_total, return_by, qbo_id`; returns are negative.
- `booking_log_<date>.csv` (root) — `qbo_id, date, type, amount, card, job, expense_account,
  receipt_locator, notes`.
- `hd_sync_state.json` (root) — `last_sync, next_window_after, posted_ids, last_posted_entry,
  store_credit_balance, pending_rows, history`.

### `receipts.json` — the detail layer (from `src/parse_receipts.py`)

Array of receipt objects:

```
file, format ("register" | "h-order"), store, datetime ("MM/DD/YY HH:MM AM"),
project (as printed, or null), total (negative for returns), is_return (bool),
tenders: [ {last4, type, amount|null} ],        # amounts AS PRINTED on the receipt
line_items: [ {sku, description, amount|null, orig_rec|null} ],  # per-item detail;
                                                # orig_rec groups returned items by original
orig_recs: [normalized locators],               # returns: links to original sale(s)
locator (register receipts), order ("H####-######", h-order receipts),
card_balance (store-credit card balance line, if printed),
resolved_projects: [...],                       # returns: original project(s), resolved
unresolved_orig_recs: [...],                    # links whose sale isn't in the corpus
error                                           # present only if parsing failed
```

`resolved_projects` entries may carry a ` (on-receipt)` suffix when the only source was
the return's own printed job.

### `ledger.csv` — the output (from `src/build_ledger.py`)

One row per transaction. Columns, in order:

| column | meaning |
| --- | --- |
| `date` | YYYY-MM-DD (receipt/Pro-Xtra date — see date-skew rule) |
| `type` | `Sale`, `Return`, `Cancel` (or `*Rering` variants) |
| `origin` | store descriptor or `online` |
| `store` | store number |
| `project` | job the money belongs to; recovered returns are tagged `(via receipt)` or `(via order#)` |
| `total` | transaction total; **negative = return** |
| `pretax` | pre-tax amount |
| `transaction_id` | API transaction id |
| `receipt_locator` | register locator as reported |
| `invoices` | invoice numbers, `\|`-joined |
| `tenders` | `Name..last4=amt` legs, ` \| `-joined; **negative amt = card charge, positive = credit back** |
| `store_credit_amt` | absolute sum of store-credit legs, `""` if none |
| `store_credit_acct` | `HD Store Credit` when `store_credit_amt` is set |
| `needs_review` | `YES` or `""` |
| `review_reason` | `;`-joined: `return-project-unknown`, `no-project`, `cancellation` |

(The legacy `src/merge_ledger.py` emits a similar but not identical column set from the
CSV export; prefer `build_ledger.py`.)

### `tender_roster.csv` — card map (from `src/parse_receipts.py`)

`last4, detected_type, times_seen, is_store_credit, QBO_account_TO_FILL` — one row per
distinct card seen across all receipts. A human (or an agent, with the user's map in
`templates/accounts.example.yml` → their private `accounts.yml`) fills the last column
with the QBO account each card feeds into.

### `reconciliation_report.csv` — per-receipt QA (from `src/parse_receipts.py`)

`file, type, format, project, resolved_projects, total, tenders, store_credit_used,
orig_rec_links, order, needs_review, review_reason` with reasons like
`tenders(x)!=total(y)`, `multi-store-credit`, `return-no-orig-link`,
`return-orig-not-in-corpus`, `return-partially-resolved`, `no-project`, `no-tender`.

### `returns_needing_lookup.csv` — the return worklist (from `src/build_ledger.py`)

`date, total, tenders, orig_recs, transaction_id, next_step` — one row per return whose
project the **offline** ladder rungs (receipt corpus + order-history spine) could not
resolve. This is the agent's queue for the **external** rungs: search Gmail for the
original receipt, then check QBO (the original — or the return — is often already booked;
match by amount + date ±3d and inherit its project). Empty file = nothing left to chase.

## The invariants (rules you must not break)

1. **Golden rule.** One Home Depot receipt = one coherent set of QBO entries, split by
   project, with returns / store-credit-used as negative lines; the card legs must equal
   what actually hit each card.
2. **Sign conventions.** Ledger `total`: negative = return. Ledger tender legs:
   negative = charge, positive = credit. Receipt tender amounts are as printed.
3. **A return belongs to the original purchase's project.** Never to a default account,
   never guessed. Recover it with the **original-receipt lookup ladder** (below) — search
   the corpus, the order-history spine, Gmail, and QBO itself before giving up. Only when
   every rung misses (`return-project-unknown`) does a human decide.
4. **All store credit pools into one clearing account** ("HD Store Credit", type
   Bank/Cash-on-hand). Earned store credit = debit in (credit the job's COGS); spent
   store credit = credit out (debit the job's COGS). After both legs of a receipt are
   booked, the account nets to zero for that receipt.
5. **Store-credit legs, some returns, and all cancellations never hit the bank feed.**
   Booking only what the feed shows silently corrupts job costs. These legs must be
   created manually (or by an agent) from the ledger.
6. **The cancellation ghost.** A cancellation charges the card for the full order, then
   credits back the canceled item's subtotal + tax — with **no receipt** and a zeroed or
   missing order-history row. Sweep rule: *an unmatched card credit with no receipt and
   no order-history return is a cancellation until proven otherwise*; resolve the amount
   via Order Details on the Pro site.
7. **Date skew: never match on exact date.** The same transaction carries up to three
   dates (receipt/Pro-Xtra, charge, posted) differing by days. Match on
   **amount + card within ±3 days**.
8. **Dedupe by amount + date (±3 days), never by card last-4.** QBO memos don't reliably
   carry last-4 (mobile-wallet charges can show a different number). Check **every**
   COGS account, not just the main supplies one. Two similar transactions on nearby days
   can both be genuine — verify against the order before calling anything a duplicate.
9. **Match, don't Add.** With the pre-create/match model, when a bank-feed line arrives
   for an entry you already created, always **Match** — "Add" creates a duplicate.
10. **Cutoff discipline.** Never modify booked history before the user's cutoff date.
    The cutoff only moves forward. Store-credit accounting is additive: add missing
    legs, don't rewrite what the feed booked.
11. **Never post to QBO without explicit user approval** of the proposed entries, and
    keep a booking log (date, type, amount, job, QBO entry #) — it defines the start of
    the next run's window.
12. **Privacy.** Never commit or paste real receipts, exports, `ledger.csv`,
    account IDs, or `.gmail_creds`. Everything generated is git-ignored; keep it that
    way. Synthetic data only in `examples/`. Never read out, print or save a full card
    number (`accountIdValue` in Home Depot's API).
13. **Reconcile by receipt, never by row.** The bank sees tender legs; Home Depot's history
    shows rows. A batch return's tender-less rows, and an order-return row repeating part of a
    register receipt, are sub-rows — booking them double-counts. The biggest row on a receipt is
    the receipt. Every new row still gets a bucket, and the counts add up.
14. **A return row's own PO is not evidence.** Credit each ORIG REC block's items + tax to the job
    that original was **booked** to (`line_items.csv`, then booking logs, then the original's PO).
    A batch return at the counter carries one arbitrary PO for items from many jobs. Each group goes
    back to the tender it was bought with: use the one partition of groups to refund legs that adds
    up, split by share when every group is on one job, otherwise ask.
15. **Itemize at net price and book the card charge.** Items at Home Depot's net unit price × qty
    (the per-line discount sign is inconsistent); Σ net = the receipt's pretax, + tax = total. Store
    credit pays for items in receipt order and the card leg carries the tax (with no card leg, the
    store credit pays the tax too). When the card was charged a different amount than the row says,
    book the charge and find the difference (delivery, adjustment). Never plug.
16. **The printed PO wins.** Use the PO printed on the register receipt (or the Pro Desk order PDF)
    over the online PO; the owner's standing rules (`po_aliases`) apply while they hold; a blank,
    nonsense or unknown PO is a question.

## Return attribution: the original-receipt lookup ladder

A return's project lives on its **original purchase**, not on the return itself (a return
receipt's own `PO/JOB` is often blank, generic, or wrong — Home Depot captures the job at
*purchase* time). Each return carries one or more `ORIG REC:` locators; resolve every one
before flagging `return-project-unknown`. Walk the ladder in order and stop at the first hit:

1. **Receipt corpus** (`receipts.json`) — the original sale's parsed receipt, matched by
   normalized locator. (Today's `resolved_projects` / `unresolved_orig_recs` only look here.)
2. **Order-history spine** (`hd_orderhistory_full.json`) — match the `ORIG REC` locator (or its
   date + amount) to a spine row and inherit its `job`.
3. **Gmail** — search the receipt mailbox for the original sale (store + date + amount, or the
   locator / order #), fetch and parse it, then inherit its job. Originals from months back
   routinely fall **outside the current download window** — go fetch them, don't assume absent.
4. **QBO itself** — the original purchase (or the return) is often **already booked** with a
   project; find it by amount + date (±3 days) across every COGS account and inherit that
   project. Frequently the *fastest* resolution, not the last resort.

Only when all four miss does the row become `return-project-unknown` for a human. Note which
rung resolved each return in the booking log.

**The weekly sync resolves returns first from the printed receipt it fetched:** each ORIG REC block →
the original in `ledger.csv` by store + date + transaction (`txc // 10`; within ±3 days for online
orders, whose printed date can differ) or by order number → the job it was **booked** to. Only what
that misses falls to the rungs above.

*Why this matters (a real case):* a return included a faucet set whose `ORIG REC` was a sale
from eleven months earlier — long before the receipt-download window. Steps 1–2 missed it and
the whole receipt stalled in review; steps 3–4 (Gmail + the already-booked original) recovered
the job instead.

## Break receipts down to line items

Parse and keep **per-line-item detail** for every purchase and return — SKU/description,
extended amount, tax, and (on returns) the `ORIG REC` each line belongs to — alongside the
transaction row. Rationale: matching returns to originals by locator alone fails whenever the
locator is missing or the original is outside the corpus. **Line items are the durable key:** a
returned SKU traces to the exact original purchase (and its project) even when the return's
`PO/JOB` is blank, and a partially-returned original splits cleanly across jobs. This
strengthens every rung of the lookup ladder above.

## Edge cases worth knowing

- One receipt can mix a return (job A) and a purchase (job B) with the card charged the
  difference → book as one split transaction; lines sum to the card charge.
- Multiple employee cards on one credit line feed a **single** QBO account; the last-4
  distinguishes people, not accounts (`tender_roster.csv` is the map).
- The store-credit clearing account may legitimately run negative before an opening
  balance is set (credit earned before the cutoff, spent after). Opening balance =
  earned − spent through the day before the cutoff, computed from the ledger.
- `Cancel` rows in the order-history pull are zeroed — they mark *that* a cancellation
  happened, never how much; some don't appear at all (see invariant 6).
- Home Depot's CSV export **cannot** contain returns or cancellations, and it collapses
  every store-credit card into one token — that's why the API pull is the spine.
- On a receipt, a tender leg with no inline amount takes the next `USD$` line; if
  exactly one leg has no amount anywhere, back-solve it: total − sum(known legs).
- HTML receipt emails repeat the receipt block (responsive layout) — parse the first
  copy only (cut at `RETURN POLICY DEFINITIONS`) and dedupe tender lines.
- **Order-return rows.** When a register return includes items from an online / Pro Desk order,
  Home Depot lists the receipt twice: the register row (the whole receipt, its real tenders) and an
  order-return row (just the order's part, with the order number and sometimes a different tender).
  Book the register row; the order-return row is a sub-row. Its order number tells you which order
  that block's original came from. Its order details describe the original order, not the return.
- **Missing block tax.** One-block return receipts often print tax only after `SUBTOTAL`. The
  block's tax is the receipt's tax left over after the blocks that do print one — never an assumed
  rate (rates differ by state).
- **Pro Desk / online orders** have no register receipt; the printed PO is on the order PDF
  (`receipt/view/bigReceipt`), on the line **before** the "PO / Job Name" label.
- **Unpriced fee lines** (e.g. a state paint-care fee) come back with no price in the order details;
  when exactly one item is unpriced and the gap is small, it is the receipt's pretax minus the
  priced items (flagged for a check).
- **Date ranges.** Ask Home Depot's order history for its own two-year window and filter locally: a
  narrow range has come back as an empty success (`orderCount 0`), which must never be read as "no
  new transactions". `searchValue` filters by job text or order number (Home Depot's own search).
- **Charge history.** An order's `chargeTransactions` and current total show cancellations and price
  adjustments that have no order-history row; current total < amount charged = a credit is coming.
- QuickBooks' public API exposes neither the bank feed's **For Review** queue nor the
  **Match** action — no connector can do the final Match click. Agents pre-create the
  entries; a human matches. Connector capability checklist and the situation → QBO
  API entity mapping (Purchase, `Credit: true`, JournalEntry…) live in
  [`../docs/07-quickbooks-connector.md`](../docs/07-quickbooks-connector.md).
