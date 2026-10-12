---
name: hd-sync
description: "Weekly Home Depot to QuickBooks Online sync: fetch new receipts, draft itemized entries per job (returns and store credit included), get approval, post, close out."
---

# Home Depot → QuickBooks weekly sync

Gets every Home Depot transaction since the last sync into QuickBooks Online, booked to the right
job and itemized line by line — including the store-credit purchases, store-credit refunds and
batch returns that a bank feed never explains. The goal: every card line in the bank feed already
has an entry to **Match** (never "Add"), and every return credits the job its items came from.

Read [`../context.md`](../context.md) once (invariants 1–15). The scripts do the mechanics; your job
is the judgment calls the scripts mark `# REVIEW`, the QuickBooks duplicate check, and the owner's
approval.

## What you need

- This repo, checked out, with Python 3.10+. Run everything from the repo root.
- `hd-sync.json` at the repo root. **Missing? Stop and run setup first:** [`hd-setup.SKILL.md`](hd-setup.SKILL.md).
- A way to run JavaScript in the owner's **signed-in** homedepot.com tab: a browser tool such as
  Claude in Chrome, or the owner pastes the script into their browser's DevTools Console and hands
  you the file it saves ([`docs/09-weekly-sync.md`](../../docs/09-weekly-sync.md)).
- A QuickBooks connector that can create Purchases and Journal Entries
  ([`docs/07`](../../docs/07-quickbooks-connector.md)). Without one, the deliverable is `PROPOSAL.md`
  for the owner to enter by hand — everything else still applies.

## Files (all at the repo root, all git-ignored, all private)

| File | Role |
|---|---|
| `hd-sync.json` | The owner's map: QuickBooks account / vendor / customer ids, cards, jobs, standing PO rules |
| `hd_sync_state.json` | **Read first.** Last sync, `next_window_after`, posted ids (written by `closeout.py`) |
| `hd_orderhistory_full.json` | Every Home Depot transaction (the spine); `ledger.csv` is rebuilt from it |
| `line_items.csv` | One row per booked item with its QuickBooks id. **This is how a return finds the job its original was booked to** |
| `booking_log_<date>.csv` | What each sync posted |
| `sync/<date>/` | That sync's working files: bundles, `new_rows.csv`, `entries.py`, `PROPOSAL.md`, `payloads.json` |

## Run it fast

- **Scripts do the mechanics, you decide.** Never hand-type item lines from receipt text when the
  bundle has Home Depot's order details; never re-derive what a script already computed.
- **Keep big data out of the conversation.** The fetch runs in the page; you read only its status
  and then the file. Summarize; don't paste CSVs.
- **Batch.** Load browser tools in one search, QuickBooks tools in another; fire independent
  QuickBooks reads together.

## 1. Window

Read `hd_sync_state.json`. Fetch from a **compare zone** about three weeks before
`next_window_after` (`from = next_window_after − 21 days`): every row already stored in that zone
must come back (proof the pull is healthy), and rows that rang up late still get caught. First sync
ever: `from = cutoff_date` from `hd-sync.json`. Never create or change anything dated before
`cutoff_date`.

## 2. Fetch (in the owner's browser)

1. Open `https://www.homedepot.com/myaccount/purchase-history` in the owner's browser. **They must
   already be signed in. Never sign in for them or type their password.** A sign-in page or HTTP 401
   = ask them to sign in, then run again.
2. Run, in that tab: `window.HD_FETCH = {from: '<zone start>', output: 'page'};` followed by the
   whole of [`src/fetch_receipts.js`](../../src/fetch_receipts.js). It returns at once and works in the
   background.
3. Poll `window.__hdf.status`. `WAITING FOR IDS` → click page 2 of the Purchase History list once
   (or ask the owner to). `fetching receipts n/N` → wait. `FAILED: …` → read it and stop.
4. When it says `done: N rows … K chunk(s)`: for each `k` from 0 to K−1, call `window.__hdf.chunk(k)`
   and read the page text (or the call's return value), and append it verbatim to
   `sync/<today>/hd_bundles_<today>.jsonl`. Then check the file in Python: it must have N+1 lines
   (one `_meta` line + one per row) and every line must parse as JSON. Re-read any chunk that broke.
5. If you cannot run JavaScript in their browser, ask the owner to do steps 1–2 with
   `output: 'download'` (or no settings at all: it defaults to the last 45 days) and give you the
   `hd_bundles_<date>.jsonl` it saves.

The script never keeps the full card number Home Depot's API returns, and redacts any 13–19 digit
number as a backstop. Do not add code that reads `accountIdValue`.

## 3. Merge and rebuild

```bash
python3 src/merge_orderhistory.py --sync sync/<today>            # dry run
python3 src/merge_orderhistory.py --sync sync/<today> --write
python3 src/build_ledger.py
```

The dry run must say `vanished: 0`. A vanished row means the pull is incomplete or Home Depot voided
something: stop and look before writing. Read `sync/<today>/job_diffs.csv`: a PO that changed on a row
you already booked may mean that entry needs re-lining (ask). `new_rows.csv` is the week's checklist —
every row in it must end up with a bucket.

## 4. Draft the entries

```bash
python3 src/gen_entries.py --sync sync/<today> --only-new
```

Open `sync/<today>/entries.py`. Every judgment call is a `# REVIEW` line; resolve each one by
editing the `add()` call, turning it into `skip(...)` (itemized, not posted), or deleting it:

- **No job for a PO** → search QuickBooks customers for the street, confirm with the owner, add the job
  to `hd-sync.json` `"jobs"` (or a standing rule to `"po_aliases"`), and re-run `gen_entries.py`.
- **Unknown card** → ask the owner which QuickBooks account it feeds; add it to `"cards"` (or to
  `"never_book"` if it is not a company card, `"ask_first"` if it needs a question every time).
- **Original not found** for a returned item → the receipt email, or QuickBooks: a purchase of that
  amount within ±3 days of the original's date. Still nothing → ask. Never guess a job.
- **Several refund legs with no unique split** → each group goes back to the tender it was bought
  with; check the originals' tenders in `ledger.csv`, or ask.
- **Blank or nonsense PO** → ask.

Glance at the `# CHECK` lines (derived fee lines, originals with no booking on file, tax derived from
the receipt total) — they are decided but worth a look. Then:

```bash
cd sync/<today> && python3 entries.py
```

`finish()` writes nothing if any tie fails (quantity × unit = line, lines = entry, entries per receipt
= the receipt). It writes `PROPOSAL.md`, `payloads.json` (exact QuickBooks API bodies — nothing is
posted), `entries.json` and `line_items_new.csv`, and fills the bucket column of `new_rows.csv`.

## 5. Check QuickBooks for duplicates

The bank feed is worked concurrently, so something may already exist. Before proposing, and again
right before posting:

1. Purchases with vendor Home Depot dated from (window start − 3 days), across **all** accounts.
2. Journal entries in the same range (store-credit refunds).
3. For every proposed amount, an existing entry of that amount within ±3 days. **Dedupe by amount +
   date, never by card last-4 or exact date.**

An existing match: drop that entry from the proposal (or propose re-lining the existing one if its job
is wrong — a re-line that keeps total and date does not break a bank match).

## 6. Propose, get approval, post

Show the owner a compact table (date, card, amount, job) from `PROPOSAL.md`, plus numbered questions
for anything still open. **Post nothing until they approve.** "Wait" on one item holds all of them. If
a fix changes after approval, confirm again.

Post each approved entry from `payloads.json` (`tool` + `params` are the QuickBooks API shapes — adapt
the call to your connector's parameter format). Order: store-credit refund journal entries, then
store-credit purchases, then card purchases and credits. Record the id QuickBooks returns for each
key and confirm each total.

## 7. Close out

```bash
python3 src/closeout.py --sync sync/<today> --ids P1=<id>,P2=<id>,J1=<id>,...           # dry run
python3 src/closeout.py --sync sync/<today> --ids ... --write
```

This appends the week to `line_items.csv`, writes `booking_log_<date>.csv` and `hd_sync_state.json`.
Then tell the owner, briefly: what was posted; which card feed lines to **Match** to which entry; which
entries will never get a feed line (store-credit purchases, store-credit refund journal entries — that
is expected); what is held and why.

## Rules that do not bend

- Approval before posting. Never guess a job. Never sign in to Home Depot for anyone.
- **Every new row gets a bucket and the counts add up** (`PROPOSAL.md` shows the arithmetic).
- **Itemize every row** — sales and returns, card and store credit, and cards that are never posted
  (`skip()` keeps the items on record).
- Items at net price; net × qty summed = the receipt's pretax; + tax = the total. **Book what the card
  was charged**; never plug a difference.
- **A return credits the job its original was booked to**, found through the receipt's ORIG REC lines.
  **Never** use the return row's own PO: a batch return at the counter carries one arbitrary PO for
  items from many jobs.
- **Reconcile by receipt, not by row.** Tender-less rows of a batch return, and an order-return row
  that repeats part of a register receipt, are sub-rows: their items live inside the receipt's entry.
- The printed PO beats the online PO; standing rules (`po_aliases`) apply without asking while they hold.
- The cutoff only moves forward. Match, don't Add.

## Questions only the owner can answer

A new job, a new card, a card that is not the company's, where a refund of an excluded card's purchase
should go, an original that cannot be found, and anything that would change an entry already matched to
the bank. Ask them together, numbered, once per sync.
