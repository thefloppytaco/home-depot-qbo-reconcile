# AGENTS.md — orientation for AI models

This repo turns a business's Home Depot purchase/return history into a per-project
expense ledger (`ledger.csv`) and reconciles it into QuickBooks Online. You may be here
to (a) modify the code, (b) run the pipeline on a user's data, (c) post results to
QuickBooks, or (d) build a skill/automation from the docs. Start here, then read
**[`ai/context.md`](ai/context.md)** — it has the data dictionary and the 12 invariants
that all work must respect.

## Repo map

```
src/pull_orderhistory.js   Paste into DevTools on homedepot.com → downloads the order
                           history (the SPINE: includes returns + cancellations)
src/download_receipts.py   Gmail IMAP backfill of receipt emails → downloads/
src/parse_receipts.py      Receipts → receipts.json + tender_roster.csv + report
src/build_ledger.py        Spine + receipt detail → ledger.csv   (the main output)
src/merge_ledger.py        LEGACY builder from the CSV export (no returns/cancels)
docs/01..08                Human guides: pull, Gmail, data model, QBO playbook,
                           daily log, runbook, QuickBooks connectors, Claude Code
ai/                        Machine-readable context + ready-to-adapt SKILL templates
.claude/skills/            Claude Code wrappers for those skills (/hd-setup,
                           /hd-runbook, /qbo-poster, /cancellation-sweep) + CLAUDE.md
templates/                 accounts.example.yml — the user's private ID map (template)
examples/                  Synthetic data only; safe to run and test against
```

Pipeline: `pull_orderhistory.js` → `download_receipts.py` → `parse_receipts.py` →
`build_ledger.py` → post to QBO per `docs/04` + `docs/06`. All scripts are Python 3.10+
stdlib-only (plus the `pdftotext` system binary) and take `--help`.

## Rules for working IN this repo (code/docs changes)

- **Never commit data.** No real receipts, exports, ledgers, IDs, or credentials —
  not in code, docs, examples, or tests. `examples/` is synthetic only. `.gitignore`
  already excludes generated outputs; don't weaken it.
- Keep scripts stdlib-only and standalone (no new pip deps, no shared module).
- `ledger.csv` column names/order are a public contract — don't change them casually.
- The docs encode hard-won bookkeeping rules (see the invariants). Don't "simplify"
  domain logic you don't recognize; it's probably load-bearing.

## Rules for operating ON a user's books

- **Propose, then post.** Never create/modify QuickBooks entries without showing the
  user the exact list first and getting approval.
- **Verify the connector before a posting run.** Many QuickBooks connectors can't write
  expenses at all — smoke-test capabilities and confirm the target company first
  (checklist + setup guide: [`docs/07-quickbooks-connector.md`](docs/07-quickbooks-connector.md)).
- **Never guess a project.** `needs_review = YES` rows go to a human.
- **Match, don't Add** pre-created entries when feed lines arrive.
- Match/dedupe by **amount + date ±3 days**, never exact date, never card last-4.
- The **cutoff date only moves forward**; history before it is read-only.
- Store-credit legs and cancellations never appear in the bank feed — creating those
  entries is the whole point, but only per the playbook in `docs/04`.

## Building skills from this repo

Three ready templates live in [`ai/skills/`](ai/skills/) (daily receipt log, QBO
posting run, cancellation sweep). The recipe for minting new ones — frontmatter format,
placeholder conventions, required safety gates — is in [`ai/README.md`](ai/README.md).

## Verifying changes

Run the pipeline on the synthetic examples (no credentials or real data needed):

```bash
python3 src/parse_receipts.py examples -o /tmp/hd-demo
python3 src/build_ledger.py --orderhistory examples/sample_orderhistory.json \
        --receipts /tmp/hd-demo/receipts.json -o /tmp/hd-demo/ledger.csv
```

If a test suite exists under `tests/`, run `pytest` too. A change that alters
`examples/sample_ledger.csv` output needs a very good reason.
