# Home Depot → QuickBooks, booked to the right job

[![CI](https://github.com/thefloppytaco/home-depot-qbo-reconcile/actions/workflows/ci.yml/badge.svg)](https://github.com/thefloppytaco/home-depot-qbo-reconcile/actions/workflows/ci.yml)

For contractors who buy at Home Depot on a Pro account and keep their books in QuickBooks Online.
Once a week, an AI assistant uses this repo to pull every Home Depot purchase and return. It drafts
the QuickBooks entries line by line on the right job, and posts them once you approve. When a card
charge shows up in your bank feed, the entry is already there to **Match**.

It handles the cases that make Home Depot bookkeeping miserable:

- **Batch returns.** One return at the counter often holds items from several jobs, under one PO
  that's right for none of them. Each item's credit goes to the job its original purchase was
  booked to, using the "ORIG REC" lines on the receipt.
- **Store credit and gift cards.** Purchases paid with them, and refunds onto them, never appear
  in a bank feed. They get entries anyway, through one store-credit account.
- **Split payments.** Store credit pays for items in receipt order and the card carries the tax,
  so every entry equals what that card was actually charged.
- **Pro Desk and online orders, cancellations, price adjustments, client cards saved on your account,
  mistyped POs.** Each is handled by a rule or turned into a question for you. It never guesses
  a job.

> This repo is code and instructions only. It holds no receipts, account numbers or financial
> data. Your data stays in your own working folder and is never committed.

---

## Point your AI at it

Copy one of these into your AI assistant.

**Claude** (desktop or web app; ideally with the Claude in Chrome extension and a QuickBooks
connector):

```text
Set up the Home Depot → QuickBooks sync for my business using
https://github.com/thefloppytaco/home-depot-qbo-reconcile — read its AGENTS.md first.
```

Every week after that: **"Run the Home Depot sync."** To check a job: **"Check the Home Depot
numbers for 123 Main St."**

**Claude Code** (in a terminal):

```bash
git clone https://github.com/thefloppytaco/home-depot-qbo-reconcile.git
cd home-depot-qbo-reconcile && claude
# then type: /hd-setup      (weekly: /hd-sync   one job: /hd-job-audit)
```

**ChatGPT, or any assistant that can read a link and run Python:**

```text
Read https://github.com/thefloppytaco/home-depot-qbo-reconcile/blob/main/AGENTS.md and help me set
up the Home Depot → QuickBooks sync for my business. I'll upload the repo as a ZIP and any files you
ask for.
```

(Download the ZIP from the green **Code** button on GitHub.)

### What your assistant needs, and what happens without it

| If your AI can… | It will… | Without it… |
|---|---|---|
| **Run Python on files** | run the scripts that do the arithmetic and draft the entries | required |
| **Run JavaScript in your signed-in homedepot.com tab** (e.g. Claude in Chrome) | fetch the week's receipts itself | you paste one script into your browser's Console and upload the file it saves ([docs/09](docs/09-weekly-sync.md#fetching-without-a-browser-tool)) |
| **Create expenses in QuickBooks** (a connector with write access; [docs/07](docs/07-quickbooks-connector.md)) | post the entries after you approve | you get every entry, line by line, ready to type in |
| **Keep files between sessions** (a folder on your computer) | pick up where last week left off | save the working files at the end of each sync and hand them back next time ([AGENTS.md](AGENTS.md#where-the-working-files-live)) |

It never signs in to Home Depot for you, and it never posts to QuickBooks without your
approval.

---

## See it work in 30 seconds

```bash
python3 examples/run_sync_demo.py
```

This runs a whole week for a fictional contractor: merge, draft, the proposal you'd approve,
close-out, and a one-job audit. You need Python 3.10+ and nothing else: no accounts, no network, no
installs. What to look for:

- a batch return credited to two jobs by its ORIG REC lines, even though its PO names only one;
- a split-tender sale: store credit pays first, the card carries the tax;
- a purchase on a client's card that is itemized but not posted;
- a register return that included an online order (Home Depot lists it twice; it's booked once).

---

## How a week runs

```
  your signed-in Home Depot tab                       your repo folder (private, git-ignored)
  ─────────────────────────────                       ─────────────────────────────────────────
  src/fetch_receipts.js  ──►  hd_bundles_<date>.jsonl ──►  merge_orderhistory.py  (what's new?)
    every transaction since last week, with               build_ledger.py        (find originals)
    Home Depot's item detail, the printed                  gen_entries.py         (draft entries.py)
    receipt (PO, ORIG REC lines, store-credit                 └─ AI resolves the few "# REVIEW" lines
    balances) and the Pro Desk order PDF's PO              entries.py  ──►  PROPOSAL.md + payloads.json
                                                                              │
                                              you approve ◄───────────────────┘
                                                   │
                         AI posts to QuickBooks ◄──┘ ──► closeout.py (line_items.csv, booking log, state)
                         you click Match on each feed line
```

The full playbook for the AI is [ai/skills/hd-sync.SKILL.md](ai/skills/hd-sync.SKILL.md); the human
walkthrough is [docs/09-weekly-sync.md](docs/09-weekly-sync.md).

**Setting up** takes one sitting ([ai/skills/hd-setup.SKILL.md](ai/skills/hd-setup.SKILL.md)). The AI
maps your QuickBooks accounts, cards and jobs into a private `hd-sync.json`
([template](templates/hd-sync.example.json)), pulls your order history, and runs the first sync.

**Checking a job** ([ai/skills/hd-job-audit.SKILL.md](ai/skills/hd-job-audit.SKILL.md)) builds
everything Home Depot says one job should carry, month by month. That includes store-credit buys
and returns that went through under another job's PO. The AI compares it with QuickBooks and
names each difference.

---

## What's in the repo

| Path | What it is |
|---|---|
| [`AGENTS.md`](AGENTS.md) | **Start here if you are an AI.** Orientation, repo map, rules. |
| [`ai/skills/`](ai/skills/) | Step-by-step skills: `hd-setup`, `hd-sync`, `hd-job-audit`, plus older ledger-first ones. Claude Code loads them from [`.claude/skills/`](.claude/skills/). |
| [`ai/context.md`](ai/context.md) | The data model and the bookkeeping rules that must not be broken. |
| `src/fetch_receipts.js` | Browser script: transactions + item detail + printed receipts for a date range or one job. |
| `src/pull_orderhistory.js` | Browser script: your whole order history (first-time setup). |
| `src/merge_orderhistory.py` | Adds a fresh fetch to your history; lists exactly what's new; stops if anything stored disappeared. |
| `src/build_ledger.py` | History → `ledger.csv`, one row per transaction (what returns use to find their originals). |
| `src/gen_entries.py`, `src/hdlib.py` | Draft the week's entries; tie every number; write the QuickBooks payloads and the proposal. |
| `src/closeout.py` | After posting: record ids, append `line_items.csv`, write the booking log and next week's state. |
| `src/audit_job.py` | One job's Home Depot cost by month. |
| [`docs/`](docs/) | Human guides, below. |
| [`examples/`](examples/) | Synthetic data and the two demos. |

| Guide | Covers |
|---|---|
| [docs/09-weekly-sync.md](docs/09-weekly-sync.md) | The weekly sync, step by step, including running the browser script yourself. |
| [docs/10-home-depot-data.md](docs/10-home-depot-data.md) | What Home Depot's site gives you: order history, order details, printed receipts, Pro Desk PDFs, charge history. Plus the quirks. |
| [docs/04-quickbooks-workflow.md](docs/04-quickbooks-workflow.md) | The bookkeeping rules: each situation and how it's booked, the store-credit account, cancellations, date skew. |
| [docs/07-quickbooks-connector.md](docs/07-quickbooks-connector.md) | What a QuickBooks connector must support, how to check it, the API shapes. |
| [docs/08-claude-code.md](docs/08-claude-code.md) | Claude Code specifics, including Intuit's official QuickBooks MCP server. |
| [docs/01-pull-order-history.md](docs/01-pull-order-history.md) | The order-history API and the full-history pull. |
| [docs/02](docs/02-gmail-setup.md), [03](docs/03-how-it-works.md), [05](docs/05-daily-receipt-log.md), [06](docs/06-runbook.md) | The older ledger-first pipeline: Gmail receipts, receipt parsing, a daily receipt log, the ledger runbook. |

### The older ledger-first pipeline

Before the weekly sync existed, this repo built a multi-year `ledger.csv` from the order history
plus receipt emails pulled from Gmail. It's still here and still tested (`python3
examples/run_demo.py`). It's useful for backfilling history and for reporting:
`download_receipts.py` → `parse_receipts.py` → `build_ledger.py`, with the `hd-runbook`,
`qbo-poster` and `cancellation-sweep` skills. The weekly sync doesn't need Gmail at all.

---

## Privacy and safety

- **Nothing private is committed.** `.gitignore` excludes `hd-sync.json`, the order history,
  `ledger.csv`, `line_items.csv`, booking logs, `sync/`, `audits/` and the receipt downloads. Before
  pushing a fork, run `git status` and add files by name.
- **Card numbers.** Home Depot's order-details API returns your full card number in one field. The
  fetch script never keeps that field and redacts any 13–19 digit number before saving.
- **Your Home Depot session stays yours.** The browser scripts run in your own signed-in tab and talk
  only to homedepot.com. Your account IDs are captured in the tab, not stored in the repo.
- **Approval before posting**, every time. A "wait" on one entry holds all of them.

## For developers

`pytest -q` runs the tests. CI runs them on Python 3.10, runs both demos, and syntax-checks the
browser scripts. The scripts use only the Python standard library; the receipt parser also needs
`pdftotext` (Poppler) for PDF receipts. Synthetic data only, everywhere. Rules for changing code
are in [AGENTS.md](AGENTS.md).

## License

MIT — see [LICENSE](LICENSE).

Not affiliated with, endorsed by, or supported by The Home Depot or Intuit. "Home Depot" and
"QuickBooks" are trademarks of their respective owners. The browser scripts read your own account's
data through your own signed-in session; use them in line with Home Depot's terms.
