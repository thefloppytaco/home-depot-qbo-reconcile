# ai/ — the machine-readable half of this repo

Everything an AI model needs to work on this pipeline or operate it for a user, without
re-deriving the domain from the human docs.

| File | What it is |
| --- | --- |
| [`context.md`](context.md) | The whole domain in one file: entities, every file shape (data dictionary), sign conventions, and the 16 invariants. **Read this first.** |
| [`skills/hd-setup.SKILL.md`](skills/hd-setup.SKILL.md) | Set the sync up for a new business: demo, QuickBooks mapping into `hd-sync.json`, history pull, first sync. |
| [`skills/hd-sync.SKILL.md`](skills/hd-sync.SKILL.md) | **The weekly sync**: fetch receipts, merge, draft itemized entries, resolve the `# REVIEW` lines, approval, post, close out. |
| [`skills/hd-job-audit.SKILL.md`](skills/hd-job-audit.SKILL.md) | One job's Home Depot cost by month against QuickBooks, including store-credit buys and cross-job returns. |
| [`skills/cancellation-sweep.SKILL.md`](skills/cancellation-sweep.SKILL.md) | Card credits with no receipt (cancellations, price adjustments) → the right job. |
| [`skills/qbo-poster.SKILL.md`](skills/qbo-poster.SKILL.md) | Older ledger-first path: post a window of `ledger.csv` to QuickBooks. |
| [`skills/receipt-log.SKILL.md`](skills/receipt-log.SKILL.md) | Older optional automation: daily Gmail receipts → a tracker table. |

The three `hd-*` skills are ready to run: the owner's specifics live in `hd-sync.json` (from
[`../templates/hd-sync.example.json`](../templates/hd-sync.example.json)), not in the skill. The older
skills are **templates** whose `<...>` placeholders come from the owner's private copy of
[`../templates/accounts.example.yml`](../templates/accounts.example.yml). For QuickBooks connectors,
[`../docs/07-quickbooks-connector.md`](../docs/07-quickbooks-connector.md) covers set-up, the
capability check and the API entity mapping. In Claude Code these skills are auto-discovered through
thin wrappers in `.claude/skills/` (see [`../docs/08-claude-code.md`](../docs/08-claude-code.md)) — edit
the canonical versions here, not the wrappers. Skill descriptions stay under 200 characters so a
skill folder can also be uploaded to the Claude apps.

## Minting a new skill from this repo

The docs encode more workflows than are written up here (tender-roster labeling,
opening-balance calculation…). To turn one into a skill:

1. Read [`context.md`](context.md) and the relevant `docs/` file. The invariants are
   non-negotiable; a skill that violates one (e.g. matches on exact date, or dedupes by
   card last-4) is wrong even if it seems to work.
2. Write a `SKILL.md` with YAML frontmatter — `name` (kebab-case) and a one-sentence
   `description` stating when to trigger — then numbered, imperative steps.
3. Parameterize everything user-specific as `<PLACEHOLDER>`s listed in one block near the
   top. No real account numbers, IDs, or data in the skill file, ever.
4. Build in the safety gates: **propose before posting**, never guess a job, respect the
   cutoff, and end with a summary that calls out returns / store credit / cancellations.
5. Test against `examples/` (synthetic data) before touching real books —
   `examples/run_sync_demo.py` builds a throwaway workspace you can reuse.

## Ground rules for agents (short version)

Full version in [`../AGENTS.md`](../AGENTS.md): never commit or paste real data, never
guess a project, never post to QuickBooks without explicit approval, Match — don't Add,
and the cutoff only moves forward.
