# 08 · Running the whole thing in Claude Code

[Claude Code](https://code.claude.com/docs) is arguably the best home for this pipeline:
everything in Pass ① is local files + Python + git (its native territory), and Pass ②
works through a QuickBooks MCP server. This repo ships the wiring so setup is three
commands.

## Get started

```bash
git clone https://github.com/thefloppytaco/home-depot-qbo-reconcile.git
cd home-depot-qbo-reconcile
claude
```

Then type **`/hd-setup`** and follow along. Claude Code automatically picks up:

- **`CLAUDE.md`** — loads the project context (including [`AGENTS.md`](../AGENTS.md))
  into every session.
- **`.claude/skills/`** — four project skills, available as slash commands and by
  auto-trigger:

| Skill | What it does |
| --- | --- |
| `/hd-setup` | Guided first-time setup: prerequisites, demo run, Gmail creds, order pull, first ledger. |
| `/hd-runbook` | The recurring update ([docs/06](06-runbook.md) as a skill): refresh → rebuild → summarize → posting handoff. |
| `/qbo-poster` | Post a ledger window to QuickBooks — propose-then-post, with the connector smoke test built in. |
| `/cancellation-sweep` | Hunt the "ghost" card credits and book them to the right project. |

The Claude Code skills are thin wrappers over the client-agnostic templates in
[`ai/skills/`](../ai/skills/) — one source of truth, two entry points.

The one step Claude Code can't do for you: the order-history pull runs in **your**
logged-in browser (paste [`src/pull_orderhistory.js`](../src/pull_orderhistory.js) per
[docs/01](01-pull-order-history.md)), and the final **Match** clicks happen in the QBO
UI ([docs/07](07-quickbooks-connector.md) explains why no connector can do those).

## Wiring up QuickBooks (for `/qbo-poster`)

The recommended route is **Intuit's official open-source MCP server** —
[github.com/intuit/quickbooks-online-mcp-server](https://github.com/intuit/quickbooks-online-mcp-server).
It runs locally (stdio), and it passes this repo's
[capability checklist](07-quickbooks-connector.md): full CRUD on Purchase, JournalEntry,
and Deposit, plus Account/Customer/Vendor search, Attachable, and a General Ledger
report. Follow its README; in short:

1. Clone it somewhere outside this repo; `npm install && npm run build`.
2. Create a free app at [developer.intuit.com](https://developer.intuit.com) → add
   redirect URI `http://localhost:8000/callback` → copy the Client ID/Secret into the
   server's own `.env` (that file stays in *its* folder, gitignored there).
3. Run its OAuth flow (`npm run auth`) — **start against the app's sandbox company**,
   then repeat for production when ready (production rejects localhost redirect URIs;
   their README documents the ngrok workaround).
4. Register it with Claude Code, **user scope** so nothing lands in this repo:

   ```bash
   claude mcp add quickbooks --scope user -- node /path/to/quickbooks-online-mcp-server/dist/index.js
   ```

5. In a new session, run `/qbo-poster` — its first-run gate smoke-tests the tools and
   confirms the target company before anything is proposed.

**Multiple companies:** one QBO company file (realm) = one OAuth connection. Run one
server instance per company and name them unmistakably (`quickbooks-acme`,
`quickbooks-acme-llc-2`) — posting into the wrong company is the expensive mistake.

**Credentials hygiene:** prefer `--scope user` (config lives in your home directory,
not the repo). If you use `--scope project`, Claude Code writes `.mcp.json` into this
repo — that file is gitignored here precisely so OAuth secrets can't end up in a
public fork. Keep it that way.

## Automating the runbook

Run `/hd-runbook` weekly — it takes a couple of minutes. If you want Pass ① fully
unattended, cron (or launchd) + headless mode works:

```bash
cd /path/to/home-depot-qbo-reconcile && claude -p "/hd-runbook — Pass ① only, then print the summary" --permission-mode acceptEdits
```

Keep **posting** interactive: `/qbo-poster` is designed to stop and ask for approval,
which defeats (and should defeat) unattended runs. Claude Code's hosted scheduled
routines run on managed infrastructure, not your machine — this pipeline needs your
local files and Gmail creds, so local scheduling is the better fit.

## Not a Claude Code user?

Everything here degrades gracefully: the same skills exist as client-agnostic templates
in [`ai/skills/`](../ai/skills/), the connector guidance in
[docs/07](07-quickbooks-connector.md) applies to any MCP client, and the scripts are
plain Python you can run yourself.
