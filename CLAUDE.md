# CLAUDE.md

This repo keeps a contractor's Home Depot spending booked to the right job in QuickBooks Online:
a weekly fetch of every purchase and return, itemized entries drafted by script, posted after the
owner approves. The full orientation — what to do for each request, the repo map, and the rules
that must not be broken — is in:

@AGENTS.md

## Skills shipped in this repo

- `/hd-setup` — first-time setup for a business: demo, QuickBooks mapping into `hd-sync.json`,
  history pull, first sync.
- `/hd-sync` — the weekly sync: fetch, merge, draft, approve, post, close out.
- `/hd-job-audit` — one job's Home Depot cost against QuickBooks, month by month.
- `/cancellation-sweep` — card credits with no receipt (cancellations, price adjustments).
- `/hd-runbook`, `/qbo-poster` — the older ledger-first path (Gmail receipts → `ledger.csv`).

Canonical versions live in `ai/skills/`; the `.claude/skills/` wrappers just point there.

## Commands

- Demos on synthetic data (no credentials): `python3 examples/run_sync_demo.py`, `python3 examples/run_demo.py`
- Tests: `pytest -q`; browser scripts: `node --check src/*.js`
- Weekly sync, from the repo root: `src/fetch_receipts.js` runs in the owner's signed-in homedepot.com
  tab → `python3 src/merge_orderhistory.py --sync sync/<date> --write` → `python3 src/build_ledger.py`
  → `python3 src/gen_entries.py --sync sync/<date> --only-new` → resolve `# REVIEW` lines →
  `cd sync/<date> && python3 entries.py` → approval → post → `python3 src/closeout.py --sync sync/<date> --ids ...`

Bookkeeping context: `ai/context.md`. Never commit generated data or credentials — `.gitignore`
is load-bearing here.
