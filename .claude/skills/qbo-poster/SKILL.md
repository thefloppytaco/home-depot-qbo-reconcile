---
name: qbo-poster
description: "Post a window of ledger.csv rows into QuickBooks Online, split by project, including the store-credit and return legs the bank feed never shows. Proposes first, posts only after explicit approval. Use when the user wants to post, book, or enter Home Depot transactions into QuickBooks."
---

# QBO posting run (Claude Code entry point)

This is a thin wrapper — the canonical, client-agnostic skill lives in the repo:

1. Read `ai/context.md` first. The 12 invariants there are non-negotiable.
2. Read `ai/skills/qbo-poster.SKILL.md` and follow it exactly, including the
   **Before the first run** section: smoke-test the connected QuickBooks tools by
   capability, **confirm which company you're posting into**, and keep the first
   window tiny.
3. Requirements: a QuickBooks MCP server that can WRITE (create Purchase with
   `Credit: true`, JournalEntry/Deposit) — setup: `docs/08-claude-code.md` and
   `docs/07-quickbooks-connector.md`. If the connector can't write, downgrade to
   producing the review-ready posting list.
4. If `accounts.yml` doesn't exist, offer to create it from
   `templates/accounts.example.yml` and fill the IDs via connector lookups with the
   user before posting anything.

Hard rules: propose the full entry list and get explicit approval before creating
anything; never guess a project; never touch history before the cutoff; the user
clicks Match in the QBO UI (no API can).
