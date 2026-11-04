# AGENTS.md — start here if you are an AI

This repo lets you keep a contractor's Home Depot spending booked correctly in QuickBooks Online.
Each week you fetch every Home Depot purchase and return, draft itemized QuickBooks entries on the
right job (returns, store credit and split payments included), get the owner's approval, and post.

You are probably here for one of these. Find yours and follow that skill step by step:

| The person says… | Do this |
|---|---|
| "Set this up for my business" (and there is no `hd-sync.json` yet) | [`ai/skills/hd-setup.SKILL.md`](ai/skills/hd-setup.SKILL.md) |
| "Run the Home Depot sync" / "get this week into QuickBooks" | [`ai/skills/hd-sync.SKILL.md`](ai/skills/hd-sync.SKILL.md) |
| "Are the Home Depot numbers right for <job>?" | [`ai/skills/hd-job-audit.SKILL.md`](ai/skills/hd-job-audit.SKILL.md) |
| "Where did this Home Depot credit come from?" | [`ai/skills/cancellation-sweep.SKILL.md`](ai/skills/cancellation-sweep.SKILL.md) |
| Change the code or docs | **Rules for working in this repo**, below |

Read [`ai/context.md`](ai/context.md) before any bookkeeping: it has the data shapes and the rules
that must not be broken. Claude Code users get the skills as `/hd-setup`, `/hd-sync`, `/hd-job-audit`
(wrappers in `.claude/skills/`).

## Getting the repo

- **You have a shell with network access** (Claude, Claude Code, Codex…): `git clone
  https://github.com/thefloppytaco/home-depot-qbo-reconcile.git` and work from the repo root.
- **You can't clone** (e.g. ChatGPT): ask the owner to download the ZIP (GitHub → Code → Download ZIP)
  and upload it, then unzip it in your sandbox.
- Check: `python3 --version` (3.10+) and `python3 examples/run_sync_demo.py` should finish without
  errors. Show the owner its proposal. It's the fastest way to explain what you're about to do
  for them.

## What you need from the owner's setup, and the fallback for each

| Capability | Used for | If you don't have it |
|---|---|---|
| Run Python on files | every script | required |
| Run JavaScript in the owner's **signed-in** homedepot.com tab (a browser tool such as Claude in Chrome) | fetching receipts (weekly) and the history pull (setup) | the owner pastes the script (`src/fetch_receipts.js` weekly, `src/pull_orderhistory.js` once at setup) into their browser's DevTools Console on the Purchase History page and gives you the file it saves ([docs/09](docs/09-weekly-sync.md#fetching-without-a-browser-tool)) |
| A QuickBooks connector that can **create** Purchases and Journal Entries | lookups, the duplicate check, posting | the owner answers the lookups, checks for duplicates, and types the entries from `PROPOSAL.md`'s entry-by-entry section; close out with `closeout.py --manual` ([hd-sync, "No QuickBooks connector"](ai/skills/hd-sync.SKILL.md#no-quickbooks-connector)) |
| A folder that persists between sessions | next week's sync | see below |

Never sign in to Home Depot or QuickBooks for the owner, and never ask for a password in the
conversation. A sign-in page means: ask them to sign in, then retry.

## Where the working files live

The sync keeps state between weeks in the repo folder. All of it is git-ignored and private:

| File | Why it must survive |
|---|---|
| `hd-sync.json` | the owner's accounts, cards, jobs and standing rules |
| `hd_orderhistory_full.json` | every transaction so far; each fetch is merged into it |
| `line_items.csv` | every booked item with its QuickBooks id — how a return finds its original's job |
| `hd_sync_state.json` | where next week's window starts, receipts on hold, open cancellations |
| `booking_log_*.csv` | what each sync posted |
| `sync/<date>/` | that week's draft and proposal — needed until `closeout.py` has run, and its bundles help later job audits |

If your workspace is temporary (a cloud sandbox, a chat), put the repo in a folder on the owner's
computer if you can reach one. If you can't, end every session by giving the owner those files and
folders as one zip, and ask for it back (with the repo) at the start of the next one. `ledger.csv`
can always be rebuilt with `python3 src/build_ledger.py`.

## Repo map

```
src/fetch_receipts.js        browser: transactions + Home Depot item detail + printed receipt
                             (+ Pro Desk PDF PO) for a date range or one job -> hd_bundles_*.jsonl
src/pull_orderhistory.js     browser: the whole order history -> hd_orderhistory_full.json (setup)
src/merge_orderhistory.py    add a fetch to the history; new_rows.csv = the week's checklist
src/build_ledger.py          history -> ledger.csv (one row per transaction; finds originals)
src/gen_entries.py           bundles -> entries.py draft, every judgment call marked # REVIEW
src/hdlib.py                 what entries.py runs on: tie checks, QuickBooks payloads, PROPOSAL.md
src/closeout.py              after posting: line_items.csv, booking log, hd_sync_state.json
src/audit_job.py             one job's Home Depot cost by month, for comparing with QuickBooks
src/download_receipts.py, parse_receipts.py, merge_ledger.py
                             the older ledger-first pipeline (Gmail receipts); not needed weekly
ai/context.md                data shapes + the rules           ai/skills/   the skills
docs/                        human guides (09 = weekly sync, 10 = Home Depot's data, 04 = bookkeeping)
templates/hd-sync.example.json   the config template          examples/    synthetic data + demos
```

## Rules for operating on someone's books

- **Propose, then post.** Show the exact entries, wait for an explicit yes. "Wait" on one holds all.
  If a fix changes after approval, ask again.
- **Check the connector and the company first.** Many QuickBooks connectors can't create expenses,
  and owners often have several companies ([docs/07](docs/07-quickbooks-connector.md)).
- **Never guess a job.** A PO that matches no job, a new card, an original you can't find: ask.
- **A return credits the job its original purchase was booked to**, found through the receipt's ORIG
  REC lines. Never use the return row's own PO.
- **Reconcile by receipt, not by row**, and **book what the card was charged.** Never plug a
  difference.
- **Match, don't Add.** Your entries exist so the owner can Match bank-feed lines to them. Check for
  existing entries by amount and date ±3 days, never by card last-4 or exact date.
- **The cutoff date only moves forward.** Nothing before it is created or changed.
- **Card numbers.** Home Depot's API returns the full card number in `accountIdValue`. Never read it
  out, print it, or save it. The scripts drop it.

## Rules for working in this repo (code and docs)

- **Never commit data.** No real receipts, exports, ledgers, ids, card digits, job addresses or
  names. Not in code, docs, examples, tests or commit messages. `examples/` is synthetic.
  `.gitignore` is load-bearing; don't weaken it.
- Scripts stay **Python 3.10+, standard library only**. `src/hdlib.py` is the one shared module
  (used by `gen_entries.py`, `closeout.py`, `audit_job.py` and each week's `entries.py`). Every
  script takes `--help`.
- `ledger.csv`'s columns, `hd-sync.json`'s keys and the bundle fields are contracts. Change them only
  with the tests, the template and `ai/context.md` in the same commit.
- The bookkeeping rules came from real mistakes. Don't "simplify" logic you don't recognize;
  `ai/context.md` probably explains it.
- Before committing: `pytest -q`, `python3 examples/run_sync_demo.py`, `python3 examples/run_demo.py`,
  and `node --check src/*.js` if you touched the browser scripts. A change to the demo's proposal
  needs a reason.
