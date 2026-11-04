---
name: hd-setup
description: "Set up the Home Depot to QuickBooks sync for a business: run the demo, map QuickBooks accounts, cards and jobs into hd-sync.json, pull history, first sync."
---

# First-time setup (Claude Code entry point)

Thin wrapper over the canonical skill:

1. Read `ai/context.md`, then `ai/skills/hd-setup.SKILL.md`, and follow it one step at a time,
   confirming each step with the owner before the next.
2. QuickBooks wiring for Claude Code (Intuit's official MCP server and alternatives):
   `docs/08-claude-code.md` § *Wiring up QuickBooks*; capability check: `docs/07-quickbooks-connector.md`.
3. Optional older path — Gmail receipt backfill (`docs/02-gmail-setup.md`): have the owner create
   `src/.gmail_creds` from `src/.gmail_creds.template` themselves. Never take a password in chat.

Never commit anything setup creates; `.gitignore` already excludes it.
