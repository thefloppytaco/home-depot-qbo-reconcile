---
name: hd-setup
description: "First-time setup of the Home Depot → QuickBooks pipeline: check prerequisites, run the demo, set up Gmail credentials, pull order history, build the first ledger, and optionally wire a QuickBooks MCP. Use when the user wants to set up, install, configure, or get started with this repo."
---

# First-time setup (guided)

Walk the user through setup one step at a time. Confirm each step worked before moving
on. Never ask the user to paste passwords or tokens into chat, and never commit
generated data — `.gitignore` already excludes it; leave that alone.

## Steps

1. **Prerequisites.** Run `python3 --version` (need 3.10+) and `pdftotext -v`. If
   `pdftotext` is missing, give the install line for their OS (macOS:
   `brew install poppler`; Debian/Ubuntu: `sudo apt-get install poppler-utils`;
   Windows: Poppler on PATH or WSL). Confirm the current directory is the repo root.

2. **Prove the pipeline works** before touching real data: run
   `python3 examples/run_demo.py` and walk the user through the output — the return
   resolved to its original project via receipt, store credit routed to the shared
   "HD Store Credit" account, the cancellation flagged for review.

3. **Gmail credentials.** Run `cp src/.gmail_creds.template src/.gmail_creds`, then ask
   the user to edit that file themselves with their Gmail address and an App Password
   (walk them through creating one per `docs/02-gmail-setup.md`; requires 2-Step
   Verification). Do NOT have them paste the password into the conversation. When they
   confirm, run `python3 src/download_receipts.py` (it's incremental; safe to re-run).

4. **Order history (the one manual step).** You cannot do this for them: the user signs
   in at homedepot.com → Purchase History, opens the DevTools Console, and pastes the
   entire contents of `src/pull_orderhistory.js` (full walkthrough:
   `docs/01-pull-order-history.md`). The browser downloads
   `hd_orderhistory_full.json`; have them move it to the repo root, then verify it
   exists and report `pulled` count and date range.

5. **Build the first ledger.** Run `python3 src/parse_receipts.py downloads`, then
   `python3 src/build_ledger.py`. Summarize: transaction count, counts by type, how many
   returns recovered a project, and the `needs_review` rows with reasons.

6. **Card map.** Open `tender_roster.csv` with the user and start
   `accounts.yml` from `templates/accounts.example.yml`. If a QuickBooks MCP is
   connected, offer to look up the account/customer/vendor IDs for them.

7. **Optional — QuickBooks MCP for posting.** If they want agent-assisted posting
   (Pass ②), point them to `docs/08-claude-code.md` § *Wiring up QuickBooks* — the
   recommended route is Intuit's official open-source MCP server — and
   `docs/07-quickbooks-connector.md` for the capability smoke test and first dry run.

8. **Wrap up.** Suggest running `/hd-runbook` weekly (or whenever For Review piles up),
   and note the daily receipt-log automation option (`docs/05-daily-receipt-log.md`)
   for clients with Gmail + database connectors.
