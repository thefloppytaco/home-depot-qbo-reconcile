---
name: cancellation-sweep
description: "Find and resolve Home Depot 'ghost' card credits — cancellations that produce no receipt and no usable order-history row — and book them to the right project. Use when the user has unexplained Home Depot card credits or asks to sweep for cancellations."
---

# Cancellation sweep (Claude Code entry point)

Thin wrapper over the canonical skill:

1. Read `ai/context.md` (especially invariants 6–8: the cancellation ghost, ±3-day
   date skew, and the dedupe rules).
2. Read `ai/skills/cancellation-sweep.SKILL.md` and follow it exactly.
3. Note: no QuickBooks API exposes the bank feed's For Review queue — pull candidate
   credits from the card registers via the connector, or ask the user to paste their
   For Review list. Resolving amounts requires the Home Depot Pro site (the user, or
   a browser tool, signed in).

Hard rules: amounts must reconcile to the cent; propose entries and get explicit
approval before posting; unresolved credits go to a human list, never to a guessed
project.
