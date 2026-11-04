---
name: hd-runbook
description: "Rebuild ledger.csv from order history and Gmail receipts and summarize what changed. For the weekly QuickBooks sync, use hd-sync instead."
---

# Recurring runbook (Pass ① + handoff to Pass ②)

> **Most owners want `/hd-sync`** — the weekly flow that fetches receipts straight from Home Depot,
> drafts itemized entries and posts them after approval. This runbook is the older ledger-first path
> (order history + Gmail receipts → `ledger.csv`), still useful for history and reporting.

Follow `docs/06-runbook.md`. Run from the repo root. Summarize as you go; don't dump
raw CSV contents into the conversation.

## Pass ① — rebuild the ledger

1. **Order-history freshness.** Check `hd_orderhistory_full.json`'s newest `date`. If
   it's older than the window being updated, ask the user to re-pull (paste
   `src/pull_orderhistory.js` in their browser per `docs/01-pull-order-history.md`)
   — or confirm they're fine building from the existing pull.
2. Run `python3 src/download_receipts.py` (incremental — only new emails download).
3. Run `python3 src/parse_receipts.py downloads`.
4. Run `python3 src/build_ledger.py`.
5. **Report:** newest transaction date; counts by type (Sale/Return/Cancel); how many
   returns resolved `(via receipt)`/`(via order#)`; store-credit transactions; any NEW
   last-4s in `tender_roster.csv` not yet in `accounts.yml`; and every `needs_review`
   row with its reason.

## Pass ② — post to QuickBooks (handoff)

- If a QuickBooks MCP with write capability is connected, offer to run `/qbo-poster`
  (it smoke-tests the connector, confirms the target company, proposes entries, and
  posts only after explicit approval). Mystery card credits go to
  `/cancellation-sweep`.
- If no qualifying connector, produce the review-ready posting list instead: one line
  per entry to create (date, QBO form, account, amount, project, memo) per the rules
  in `docs/04-quickbooks-workflow.md`, so the user can enter them in the QBO UI.
- Either way, remind the user: **Match, don't Add** when feed lines arrive, and move
  the booking log / cutoff forward once posted.
