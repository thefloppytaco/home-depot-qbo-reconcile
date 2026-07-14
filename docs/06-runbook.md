# 06 · Recurring update runbook

Keep QuickBooks current in two passes. Run it weekly, or whenever the QBO "For Review" list
starts piling up with Home Depot lines.

- **Pass ① — rebuild the ledger** (this repo's scripts).
- **Pass ② — post to QuickBooks** (a human, or an AI agent with your QBO connected, following
  [04-quickbooks-workflow.md](04-quickbooks-workflow.md)). The agent-ready version of this
  pass is [ai/skills/qbo-poster.SKILL.md](../ai/skills/qbo-poster.SKILL.md) (plus
  [ai/skills/cancellation-sweep.SKILL.md](../ai/skills/cancellation-sweep.SKILL.md) for the
  ghost-credit sweep). Check your connector can actually write expenses first:
  [07-quickbooks-connector.md](07-quickbooks-connector.md).

## One-time setup

1. Have the repo folder somewhere your tools can read it.
2. Put your Gmail App Password in `src/.gmail_creds` (see
   [02-gmail-setup.md](02-gmail-setup.md)).
3. If you use the daily receipt log, leave its host running so the morning task fires
   (see [05-daily-receipt-log.md](05-daily-receipt-log.md)).
4. Fill in `templates/accounts.example.yml` with your account/project/card IDs and keep it
   private.

## Pass ① — rebuild the ledger

```bash
# 1. Re-pull order history (the only source with returns AND cancellations): paste
#    src/pull_orderhistory.js into the DevTools Console on homedepot.com Purchase
#    History (method: docs/01-pull-order-history.md) and save
#    hd_orderhistory_full.json to the repo root

# 2. Fetch any new receipt emails (incremental — safe to re-run, skips what you have)
python3 src/download_receipts.py

# 3. Parse receipts
python3 src/parse_receipts.py downloads

# 4. Rebuild the ledger
python3 src/build_ledger.py
```

Run from the repo root — outputs land there by default, and every script takes `--help`
for custom paths.

**Produces:** updated `ledger.csv` (the spine), `tender_roster.csv` (card → account map),
`receipts.json` (detail). Note the newest date and the counts by type (Sale / Return /
Cancel) and how many rows have a blank PO/Job — that's your handoff into Pass ②.

## Pass ② — post to QuickBooks

Working from the latest `ledger.csv` (+ `tender_roster.csv`, `receipts.json`) and the rules
in [04-quickbooks-workflow.md](04-quickbooks-workflow.md):

1. **Set the window.** Only transactions after the last thing you posted, and on/after your
   cutoff date. Never touch anything before the cutoff.
2. **Dedupe against QBO.** Pull the relevant card registers and skip any leg already booked
   or feed-matched. Dedupe by **amount + date (±3 days)**, across **every** COGS account —
   never by card last-4.
3. **Book to the correct project** (projects = customers; look up new ones):
   - Card purchases → pre-create as a project-tagged Expense on the card account → COGS; it
     appears as a **Match** in For Review (don't "Add").
   - Returns / cancellations not in the feed → Credit Card Credit to the order's project. A
     mystery credit with no receipt → treat as a cancellation, resolve via the Pro site.
   - Store-credit legs → JE into / Expense from the "HD Store Credit" account.
   - Tools-COGS purchases and $0 rows → handle per your own COA / skip.
4. **Review before posting.** Propose the entries, approve, then post. Update your booking
   log and flip the matching daily-log rows to "Logged."
5. **Flag blank PO/Job rows** for cleanup and remember to **Match (not Add)** the created
   credits when their feed lines show up.

## Guardrails

- Run ① then ② in that order; ② waits for your approval before posting.
- The cutoff only moves forward.
- **Match, don't Add** — pre-created entries are meant to be matched to their feed lines.
- Keep a booking log (date, type, amount, job, QBO entry #) — it's the source of truth for
  "what's the last thing posted," which sets the next window.
