# ai/ — the machine-readable half of this repo

Everything an AI model needs to work on this pipeline or operate it for a user, without
re-deriving the domain from the human docs.

| File | What it is |
| --- | --- |
| [`context.md`](context.md) | The whole domain in one file: entities, every file shape (data dictionary), sign conventions, and the 12 invariants. **Read this first.** |
| [`skills/receipt-log.SKILL.md`](skills/receipt-log.SKILL.md) | Daily scheduled task: new Gmail receipts → tracker table, tricky ones flagged. |
| [`skills/qbo-poster.SKILL.md`](skills/qbo-poster.SKILL.md) | Post a window of `ledger.csv` into QuickBooks, split by project, propose-then-post. |
| [`skills/cancellation-sweep.SKILL.md`](skills/cancellation-sweep.SKILL.md) | Hunt the "ghost" card credits (cancellations) and book them to the right job. |

The skills are **templates**: every `<...>` placeholder (base/table/field IDs, QBO account
IDs, cutoff date) must be filled from the user's private copy of
[`../templates/accounts.example.yml`](../templates/accounts.example.yml) before use. They
assume connectors (Gmail, a table store, QuickBooks) that vary by setup — adapt the tool
calls to whatever the user has connected.

## Minting a new skill from this repo

The docs encode more workflows than are templated here (weekly runbook, tender-roster
labeling, opening-balance calculation…). To turn one into a skill:

1. Read [`context.md`](context.md) and the relevant `docs/` file. The invariants are
   non-negotiable; a skill that violates one (e.g. matches on exact date, or dedupes by
   card last-4) is wrong even if it seems to work.
2. Write a `SKILL.md` with YAML frontmatter — `name` (kebab-case) and a one-sentence
   `description` stating when to trigger — then numbered, imperative steps.
3. Parameterize everything user-specific as `<PLACEHOLDER>`s listed in one block near the
   top. No real account numbers, IDs, or data in the skill file, ever.
4. Build in the safety gates: **propose before posting**, hold `needs_review` rows for a
   human, respect the cutoff, and end with a summary that calls out returns /
   store-credit / cancellations.
5. Test against `examples/` (synthetic data) before touching real books.

## Ground rules for agents (short version)

Full version in [`../AGENTS.md`](../AGENTS.md): never commit or paste real data, never
guess a project, never post to QuickBooks without explicit approval, Match — don't Add,
and the cutoff only moves forward.
