# CLAUDE.md

This repo turns a business's Home Depot purchase/return history into a per-project
expense ledger (`ledger.csv`) and reconciles it into QuickBooks Online. The full
orientation — repo map, data shapes, and the rules that must not be broken — is in:

@AGENTS.md

## Commands

- Demo on synthetic data (no credentials needed): `python3 examples/run_demo.py`
- Tests: `pytest -q`
- Pipeline, run from the repo root: `python3 src/download_receipts.py` →
  `python3 src/parse_receipts.py downloads` → `python3 src/build_ledger.py`
- Order history: the user pastes `src/pull_orderhistory.js` into their browser's
  DevTools Console (see `docs/01-pull-order-history.md`) — this cannot run headlessly.

## Skills shipped in this repo

- `/hd-setup` — guided first-time setup (prerequisites, demo, Gmail creds, order pull,
  optional QuickBooks MCP).
- `/hd-runbook` — the recurring update: rebuild the ledger, summarize, hand off posting.
- `/qbo-poster` and `/cancellation-sweep` — QuickBooks posting workflows. Both require a
  QuickBooks MCP server that can WRITE (see `docs/07-quickbooks-connector.md`) and both
  gate on explicit user approval before posting anything.

Deep domain context for any bookkeeping logic: `ai/context.md`. Never commit generated
data or credentials — `.gitignore` is load-bearing here.
