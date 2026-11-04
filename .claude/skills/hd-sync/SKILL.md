---
name: hd-sync
description: "Weekly Home Depot to QuickBooks sync: fetch new receipts, draft itemized entries per job (returns and store credit included), get approval, post, close out."
---

# Weekly sync (Claude Code entry point)

Thin wrapper over the canonical skill:

1. If `hd-sync.json` is missing at the repo root, run `/hd-setup` first.
2. Read `ai/context.md`, then `ai/skills/hd-sync.SKILL.md`, and follow it step by step.
3. The fetch runs in the owner's signed-in homedepot.com tab (Claude in Chrome, or the owner pastes
   `src/fetch_receipts.js` into DevTools and gives you the file). Never sign in for them.

Hard rules: post nothing to QuickBooks without explicit approval; never guess a job; returns credit
the job their original was booked to (ORIG REC), never the return row's PO.
