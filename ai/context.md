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
- **"HD Store Credit" clearing account** — a single QBO account (type **Bank /
  Cash-on-hand**, so it can be a pay-from account) that pools ALL store-credit cards.
  Individual store-credit cards are fungible and are deliberately not tracked separately.

## File shapes (data dictionary)

### `hd_orderhistory_full.json` — the spine (input)

Produced by `src/pull_orderhistory.js` (or manually per `docs/01`). Shape:

```json
{ "pulled": <int>, "rows": [ {
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
    "tenders": [ { "net": "<code>", "last4": "0000", "amt": "<string, negative = charge>" } ]
} ] }
```

Tender network codes (`net`): `VI` Visa, `AX` Amex, `MA` Mastercard, `DS` Discover,
`DB` Debit, `HD` Home Depot card (a credit card, NOT store credit), `ED` other, and the
store-credit group: **`GR`** gift/merchandise card, **`CS`** cash/store credit,
**`PERK_GC`** Pro Xtra perks gift card.

### `receipts.json` — the detail layer (from `src/parse_receipts.py`)

Array of receipt objects:

```
file, format ("register" | "h-order"), store, datetime ("MM/DD/YY HH:MM AM"),
project (as printed, or null), total (negative for returns), is_return (bool),
tenders: [ {last4, type, amount|null} ],        # amounts AS PRINTED on the receipt
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

## The invariants (rules you must not break)

1. **Golden rule.** One Home Depot receipt = one coherent set of QBO entries, split by
   project, with returns / store-credit-used as negative lines; the card legs must equal
   what actually hit each card.
2. **Sign conventions.** Ledger `total`: negative = return. Ledger tender legs:
   negative = charge, positive = credit. Receipt tender amounts are as printed.
3. **A return belongs to the original purchase's project.** Never to a default account,
   never guessed. If the original can't be resolved (`return-project-unknown`), a human
   decides.
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
    way. Synthetic data only in `examples/`.

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
