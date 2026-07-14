# Home Depot → QuickBooks project-expense reconciliation

[![CI](https://github.com/thefloppytaco/home-depot-qbo-reconcile/actions/workflows/ci.yml/badge.svg)](https://github.com/thefloppytaco/home-depot-qbo-reconcile/actions/workflows/ci.yml)

Turn a business's Home Depot purchase and return history into a clean, per-project
expense **ledger** you can match against inside QuickBooks Online (QBO).

If you run a contracting / renovation business and buy from Home Depot on a Pro
account, this pipeline answers the questions a raw bank feed can't:

- **Which job does each purchase belong to?** (from the PO/Job name on the receipt)
- **When something is returned, which job gets the credit?** (returns are linked back
  to the *original* purchase, so the money lands on the right project)
- **What actually paid for it?** (real split tenders and the *specific* store-credit
  card used — not a single collapsed "store credit" blob)

The result is one row per transaction with a project, the tender breakdown, store-credit
routing, and a review flag for the handful of rows a human should eyeball.

The system has **two halves** that work together:

- **Batch engine (this repo's scripts)** — builds the full multi-year `ledger.csv` you post
  to / match against in QuickBooks. Run it on a cadence (e.g. weekly).
- **Daily receipt log (optional automation)** — a small scheduled job that each morning
  pulls the day's new Home Depot receipts into an **Airtable** tracker and flags the ones
  that need manual QuickBooks handling (returns, store credit, multi-card). See
  **[docs/05-daily-receipt-log.md](docs/05-daily-receipt-log.md)**.

The genuinely hard bookkeeping — store-credit legs and cancellations that never hit the
bank feed — is explained, with the rules to handle it, in
**[docs/04-quickbooks-workflow.md](docs/04-quickbooks-workflow.md)**.

> **Heads up:** this repo ships **code and documentation only**. It contains **no
> receipts, no account numbers, and no financial data**. Everything you generate (your
> downloaded receipts, your order-history JSON, your ledger) stays on your machine and is
> git-ignored. See [Privacy & safety](#privacy--safety).

---

## Why two data sources

Neither Home Depot data source is complete on its own, so the pipeline can use both:

| Source | What it's good for | What it's missing |
| --- | --- | --- |
| **Order-history API pull** (`hd_orderhistory_full.json`) | The **gold source**. Every transaction — sales, **returns**, cancellations, online orders — with split tenders, project, and store. | Nothing major; this is the spine. |
| **Email receipts** (PDF/HTML from Gmail) | The **detail layer**: return → original-purchase links, the *real* store-credit card numbers, line items. | Only ~2/3 of transactions email a receipt. |

The newest builder (`build_ledger.py`) treats the **order-history pull as the spine** and
uses the **email receipts only to recover the original project for returns** that come
through with a blank job. An older `merge_ledger.py` builds the same ledger from the CSV
export instead of the API pull — kept for reference (see [Two builders](#two-builders)).

---

## The pipeline at a glance

```
                                  ┌─────────────────────────────┐
  Home Depot website (logged in)  │  docs/01-pull-order-history  │
        └──────────────┬─────────►│  paste JS in DevTools /      │
                       │          │  Claude browser tool         │
                       ▼          └─────────────┬───────────────┘
             hd_orderhistory_full.json          │
                       │                         │
  Gmail (HD receipts)  │                         ▼
        │              │                 ┌───────────────┐
        ▼              │                 │ build_ledger  │──►  ledger.csv
  download_receipts ───┤                 │    .py        │     (one row / txn,
        │              │                 └───────┬───────┘      project + tenders +
        ▼              │                         │              store-credit routing
   downloads/*.pdf ────┴──►  parse_receipts ─────┘              + review flag)
                              .py                                      │
                                │                                      ▼
                    receipts.json + tender_roster.csv        Match in QuickBooks
                    + reconciliation_report.csv              (docs/04-quickbooks-workflow)
```

1. **Pull order history** → `hd_orderhistory_full.json` — the authoritative list of every
   transaction. See **[docs/01-pull-order-history.md](docs/01-pull-order-history.md)**.
2. **Download receipt emails** → `download_receipts.py` grabs every HD receipt from Gmail
   over IMAP into `downloads/`. See **[docs/02-gmail-setup.md](docs/02-gmail-setup.md)**.
3. **Parse receipts** → `parse_receipts.py downloads/` reads every PDF/HTML receipt and
   writes `receipts.json`, `reconciliation_report.csv`, and `tender_roster.csv`. It also
   resolves each return's `ORIG REC` links back to the original purchase's project.
4. **Build the ledger** → `build_ledger.py` joins the spine + receipt detail into
   **`ledger.csv`**.
5. **Match in QuickBooks** → label each card/account in `tender_roster.csv`, then reconcile
   `ledger.csv` against QBO. See **[docs/04-quickbooks-workflow.md](docs/04-quickbooks-workflow.md)**.

### The guides

| Guide | What it covers |
| --- | --- |
| [docs/01-pull-order-history.md](docs/01-pull-order-history.md) | Pulling your authoritative order history (incl. returns/cancellations) from the Home Depot site API. |
| [docs/02-gmail-setup.md](docs/02-gmail-setup.md) | Creating the Gmail App Password so `download_receipts.py` can fetch your receipt emails. |
| [docs/03-how-it-works.md](docs/03-how-it-works.md) | The data model: receipt formats, register locators, how returns are linked to their original project, store-credit routing. |
| [docs/04-quickbooks-workflow.md](docs/04-quickbooks-workflow.md) | The bookkeeping playbook: how to book each situation in QBO, the store-credit clearing account, the cancellation "ghost" gap, and the date-skew dedupe rule. |
| [docs/05-daily-receipt-log.md](docs/05-daily-receipt-log.md) | The optional daily automation into Airtable + a genericized scheduled-task template. |
| [docs/06-runbook.md](docs/06-runbook.md) | A copy-paste recurring runbook to keep QuickBooks current. |
| [docs/07-quickbooks-connector.md](docs/07-quickbooks-connector.md) | Posting via a QuickBooks connector/MCP: what it must support, the ledger → QBO API entity mapping, and the "For Review" limitation no connector escapes. |
| [templates/](templates/) | The `accounts.example.yml` config template for your own account/project/card IDs. |
| [ai/](ai/) | The machine-readable layer for AI agents — data dictionary, invariants, and ready-to-adapt skill templates. See [For AI agents](#for-ai-agents) below. |

---

## Requirements

- **Python 3.10+** (standard library only — no `pip install` needed for the scripts).
- **`pdftotext`** on your PATH, used by `parse_receipts.py` to read receipt PDFs.
  It ships with **Poppler**:
  - macOS: `brew install poppler`
  - Debian/Ubuntu: `sudo apt-get install poppler-utils`
  - Windows: install Poppler and add its `bin/` to PATH (or run under WSL).
- A **Gmail account** that receives your Home Depot receipts, with 2-Step Verification on
  so you can create an App Password (for step 2 only).
- A **Home Depot Pro** online account (for step 1).

Developers/CI: `pip install pytest` to run the test suite (`pytest -q`); runtime needs no
pip packages.

Verify your setup:

```bash
python3 --version      # 3.10 or newer
pdftotext -v           # prints a Poppler version
```

---

## Try it in 30 seconds (no data needed)

```bash
python3 examples/run_demo.py
```

Runs the whole pipeline against synthetic sample data and prints the resulting ledger —
no credentials, no real data, no `pdftotext` install needed. It's the fastest way to see
the flagship behaviors: a return resolved back to its original project via the receipt's
`ORIG REC` link, store-credit tenders routed to the shared clearing account, and a
cancellation flagged for review.

---

## Quick start

```bash
# 0. clone the repo and enter it
git clone https://github.com/thefloppytaco/home-depot-qbo-reconcile.git
cd home-depot-qbo-reconcile

# 1. pull your full order history: paste src/pull_orderhistory.js into the DevTools
#    Console on homedepot.com Purchase History (see docs/01), then save the file it
#    downloads — hd_orderhistory_full.json — into the repo root

# 2. download your receipt emails (see docs/02 for the App Password)
cp src/.gmail_creds.template src/.gmail_creds   # then edit in your email + app password
python3 src/download_receipts.py                #  -> downloads/*.pdf|*.html

# 3. parse the receipts
python3 src/parse_receipts.py downloads         #  -> receipts.json, tender_roster.csv, reconciliation_report.csv

# 4. build the ledger from the order-history pull (+ receipt detail for returns)
python3 src/build_ledger.py                     #  -> ledger.csv

# 5. open ledger.csv, label tender_roster.csv, and reconcile in QBO (docs/04)
```

Everything above runs from the repo root by default — inputs and outputs land next to
`src/`, not inside it. Every script also takes `--help` if you want custom paths.

---

## What each script does

| Script | Input | Output | Notes |
| --- | --- | --- | --- |
| `pull_orderhistory.js` | Your logged-in homedepot.com browser session | `hd_orderhistory_full.json` | Paste into the DevTools Console (see [docs/01](docs/01-pull-order-history.md)). Auto-captures your account IDs, paginates the order-history API, dedupes, and downloads the file. |
| `download_receipts.py` | Gmail (IMAP) | `downloads/*.pdf`, `*.html` | **Incremental** — checks headers and skips receipts already saved in `downloads/`, so it's safe (and intended) to re-run periodically; pass `--refresh` to force a full re-download. Uses IMAP (not an API connector) because pulling ~2,000 × 140 KB emails that way is impractical. |
| `parse_receipts.py` | `downloads/` | `receipts.json`, `tender_roster.csv`, `reconciliation_report.csv` | Handles both eReceipt/register receipts and H-order desk invoices. Extracts tenders, totals, project, and links returns to their original purchase. |
| `build_ledger.py` | `hd_orderhistory_full.json` (+ `receipts.json`, optionally the legacy CSV pair via `--csv-dir`) | `ledger.csv` | **Recommended.** Builds from the authoritative API pull; uses receipts (and optionally the CSVs) to recover the project on blank-job returns. |
| `merge_ledger.py` | Purchase-history CSVs (+ `receipts.json`) | `ledger.csv` | **Legacy/alternative.** Builds from Home Depot's CSV export instead of the API pull. |

### Two builders

Home Depot's CSV export **cannot include returns or cancellations** (Home Depot says so in
the export dialog). That's why `build_ledger.py` prefers the order-history API pull, which
*does* include them. Use `merge_ledger.py` only if you can't run the browser pull and are
willing to lose return detail. Both write the same `ledger.csv` shape.

---

## Output: `ledger.csv`

One row per transaction. Columns (from `build_ledger.py`):

`date, type, origin, store, project, total, pretax, transaction_id, receipt_locator,
invoices, tenders, store_credit_amt, store_credit_acct, needs_review, review_reason`

- `type` — `Sale`, `Return`, or `Cancel`.
- `project` — the job the money belongs to. For returns with no job of their own, this is
  recovered from the original purchase and tagged `(via receipt)` or `(via order#)`.
- `tenders` — human-readable split tenders, e.g. `Visa..0067=-130.97 | Gift/Store Credit..8841=-12.10`.
- `store_credit_amt` / `store_credit_acct` — total routed to the shared **"HD Store Credit"**
  clearing account (see [docs/03-how-it-works.md](docs/03-how-it-works.md)).
- `needs_review` / `review_reason` — a human should look at these (e.g. a return whose
  original project couldn't be found, or a cancellation).

See `examples/` for tiny **synthetic** samples of the order-history JSON and the resulting
ledger.

---

## Privacy & safety

This repo is built to be shared **without leaking your business's data**:

- **No data ships in the repo.** `.gitignore` excludes `downloads/`, `receipts.json`,
  `hd_orderhistory_full.json`, `ledger.csv`, `*_report.csv`, `tender_roster.csv`, and any
  `Purchase_History_*` export.
- **No secrets in code.** Your Gmail App Password lives only in `src/.gmail_creds`
  (git-ignored); commit only `src/.gmail_creds.template`.
- **Your Home Depot account IDs** (`USER_ID`, `customerAccountId`) are **not** in this repo —
  the order-history guide shows you how to read your own from a logged-in session.
- **Before you push:** run `git status` and confirm no receipt, CSV, JSON export, or creds
  file is staged. When in doubt, `git add` files explicitly rather than `git add .`.

If you fork or share your own copy, keep it this way — never commit a real
`hd_orderhistory_full.json`, `ledger.csv`, or `.gmail_creds`.

---

## For AI agents

This repo ships a machine-readable layer alongside the human docs: **[AGENTS.md](AGENTS.md)**
(start here — repo map and rules), **[ai/context.md](ai/context.md)** (the data dictionary
and 12 domain invariants), **[ai/skills/](ai/skills/)** (three ready-to-adapt `SKILL.md`
templates), and **[llms.txt](llms.txt)**. An agent with this context can run the pipeline,
post to QuickBooks under the guardrails in
[docs/04-quickbooks-workflow.md](docs/04-quickbooks-workflow.md), or mint new skills for
other workflows per [ai/README.md](ai/README.md).

Posting requires a QuickBooks connector that can actually **write** expenses — many
(including some first-party ones) only do invoices and reports.
[docs/07-quickbooks-connector.md](docs/07-quickbooks-connector.md) has the capability
checklist, the QBO API entity mapping, and the one thing no connector can do (the bank
feed's "For Review" / Match step — that click stays human).

---

## Publishing this to GitHub

From the repo folder:

```bash
git init
git add .
git status                       # <-- confirm NO data/creds files are listed
git commit -m "Home Depot → QBO reconciliation pipeline"

# create an empty repo on github.com first (no README/license), then:
git remote add origin https://github.com/<you>/home-depot-qbo-reconcile.git
git branch -M main
git push -u origin main
```

`.gitignore` already keeps your data out of the commit, but always eyeball `git status`
before the first push.

---

## License

MIT — see [LICENSE](LICENSE).

This project is not affiliated with, endorsed by, or supported by The Home Depot or Intuit.
"Home Depot" and "QuickBooks" are trademarks of their respective owners. The order-history
method uses your own logged-in session to read your own account's data; use it in
accordance with Home Depot's terms.
