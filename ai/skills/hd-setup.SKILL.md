---
name: hd-setup
description: "Set up the Home Depot to QuickBooks sync for a new business: prove it on sample data, map QuickBooks accounts, cards and jobs into hd-sync.json, pull history, run the first sync."
---

# Set up the Home Depot → QuickBooks sync for a business

Walk the owner through this one step at a time and confirm each step worked before the next. It
takes one sitting. Nothing here posts to QuickBooks; the first posting happens in the first sync, after
approval. Never ask for passwords or tokens in the conversation, and never commit anything the steps
create — `.gitignore` already keeps it out; leave that alone.

Read [`../context.md`](../context.md) first.

## 1. Check the basics and show what it does

- `python3 --version` (3.10 or newer). You are at the repo root.
- `python3 examples/run_sync_demo.py` — one whole week on a fictional business. Show the owner the
  `PROPOSAL.md` it prints and point out: the batch return credited to two jobs by its ORIG REC lines
  despite its PO, the split-tender sale (store credit first, card carries the tax), the purchase on a
  client's card itemized but not posted, and the online-order return found by its order number.
- Ask what they need: weekly syncing (most owners), checking a job's numbers, or both.

## 2. QuickBooks connection

The sync needs a connector that can **read** accounts, customers, vendors and purchases, and **create**
Purchases (including credits) and Journal Entries. Many QuickBooks connectors only do reports or
invoices. Run the capability check in [`docs/07-quickbooks-connector.md`](../../docs/07-quickbooks-connector.md)
and **confirm which company** you are connected to — owners often have several. No qualifying
connector: everything still works, and the owner enters the proposed entries by hand.

## 3. Build `hd-sync.json`

`cp templates/hd-sync.example.json hd-sync.json`, then fill it in with the owner. With a connector,
look things up in QuickBooks yourself and ask the owner to confirm — don't make them dig for ids.
**Without a connector**, use the exact QuickBooks *names* instead of ids (e.g. `"cogs": "Job
Materials"`): the proposal then reads in names, which is all hand entry needs.

| Key | How to fill it |
|---|---|
| `accounts.cogs` | The account Home Depot materials are expensed to (often a Cost of Goods Sold account like "Job Materials"). Ask which, if there are several. |
| `accounts.tools` | Where tool purchases go (POs like "TOOLS" or "SHOP"), no job. |
| `accounts.rental` | Tool-rental expense, if they use one. Delete the key otherwise. |
| `accounts.store_credit` | **One** account that pools every Home Depot gift card and store-credit card, e.g. "HD Store Credit". It must be type **Bank** — store-credit purchases are paid *from* it, and QuickBooks only pays from Bank or credit-card accounts. If none exists, propose creating it and wait for a yes. |
| `vendor_hd` | The vendor record for The Home Depot. Duplicates are common: ask which one they use. |
| `cards` | Each card last-4 that pays at Home Depot → the QuickBooks credit-card or bank account its charges land in. Several employee cards often share one account. After step 4, list every last-4 in `ledger.csv`'s `tenders` column since the cutoff and ask about each. Store-credit and gift cards (`Gift/Store Credit` in the ledger) are not listed: they all use `accounts.store_credit`. |
| `never_book` | Last-4s that are not company cards (a client's card saved on the Pro account, a personal card). Their purchases are itemized but never posted. |
| `ask_first` | Last-4s to ask about every time (an employee's personal card used by mistake). |
| `jobs` | Each active job → the QuickBooks customer id its lines go to. **If the job is a QuickBooks project, use the project's id.** Name each job the way it reads on a PO ("123 Example St"): matching is by street number + first street word, so "123 EXAMPLE STREET" finds it. Build the list from the POs in the history (step 4) against QuickBooks customers, and have the owner confirm the table. |
| `po_aliases` | Standing rules for PO text that should go to a job: the shop/office PO, a nickname, a stale job name the registers keep auto-filling. |
| `garbage_pos`, `tools_pos` | PO values meaning "no job given" (held for a question) and "tools" (tools account). |
| `cutoff_date` | The first date this sync is responsible for — usually the day after the books were last reconciled. Nothing earlier is ever created or changed. |
| `excluded_origin_refunds` | When a return refunds onto a company card something bought on a `never_book` card: `"ask"` (default) or `"untagged"`. |
| `billable_status` | `"Billable"` only if they bill materials back to customers from QuickBooks (cost-plus); otherwise `"NotBillable"`. |
| `labels` | Optional: each id → its QuickBooks name, so the proposal reads "Job Materials (55)". Worth filling. |

`python3 src/hdlib.py --check` must say the file looks complete (it catches leftover placeholders,
bad card numbers, aliases pointing at unknown jobs).

**Store-credit opening balance.** Store-credit cards usually hold money earned before the cutoff.
Ask the owner for each card's balance as of the cutoff (the latest printed `CARD BALANCE`, or Home
Depot's gift-card balance page) and have them record the total as the store-credit account's opening
balance on the cutoff date. Otherwise the account drifts negative as old credit is spent.

## 4. Pull the history

In the owner's signed-in Purchase History tab, run [`src/pull_orderhistory.js`](../../src/pull_orderhistory.js)
(the whole history, newest first; see [`docs/01`](../../docs/01-pull-order-history.md)). With a browser
tool: `window.HD_PULL = {output: 'page'}`, run the file, poll `window.__hd_pull.status`, then read
`window.__hd_pull.chunk(k)` for each chunk into `hd_orderhistory_full.json`. Without one, the owner
pastes it into DevTools and moves the downloaded file to the repo root. Then:

```bash
python3 src/build_ledger.py
```

Use `ledger.csv` to fill the gaps in step 3: every PO seen in the last few months (for `jobs` and
`po_aliases`) and every last-4 (for `cards`).

## 5. The first sync

Run [`hd-sync.SKILL.md`](hd-sync.SKILL.md) with `from = cutoff_date`. With no `hd_sync_state.json` yet,
the merge lists every stored row since the cutoff that isn't booked, so everything since the cutoff gets
drafted even though step 4 already stored it. If the owner already entered some of those in QuickBooks
by hand, the duplicate check (sync step 5) catches them — that check matters most on the first sync.
The first sync has more questions than later ones (new jobs, new cards, originals booked before this
system existed); later weeks are mostly confirmation. Returns of purchases booked before the first sync
are matched to their job through Home Depot's PO on the original and flagged `# CHECK` — confirm those
against QuickBooks.

## 6. Make it routine

- Weekly: "run the Home Depot sync" (`hd-sync`). If the owner's AI can schedule tasks, offer a weekly
  scheduled run that drafts the proposal and waits for approval.
- When a job's numbers look off: `hd-job-audit`.
- Optional, older paths still in the repo: the Gmail receipt pipeline and daily receipt log
  ([`docs/02`](../../docs/02-gmail-setup.md), [`docs/05`](../../docs/05-daily-receipt-log.md)) for
  receipts the order history cannot explain.
