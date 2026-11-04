---
name: hd-job-audit
description: "Check one job's Home Depot cost against QuickBooks month by month, including store-credit purchases, cross-job returns and cancellations."
---

# Job audit (Claude Code entry point)

Thin wrapper over the canonical skill:

1. Read `ai/context.md`, then `ai/skills/hd-job-audit.SKILL.md`, and follow it.
2. The Home Depot side comes from `src/fetch_receipts.js` with `job` set (run in the owner's signed-in
   tab) and `python3 src/audit_job.py`; the QuickBooks side from a P&L for the job, vendor Home Depot,
   by month, plus the job's store-credit journal entries.

Read-only until the owner approves fixes; prefer re-lining existing entries over recreating them.
