# 08 · Running the whole thing in Claude Code

[Claude Code](https://code.claude.com/docs) is a natural home for this repo: the sync is local
files + Python (its native territory), the receipt fetch runs in your browser through the Claude in
Chrome extension, and posting goes through a QuickBooks MCP server. Your working files stay in the
repo folder on your machine between weeks.

## Get started

```bash
git clone https://github.com/thefloppytaco/home-depot-qbo-reconcile.git
cd home-depot-qbo-reconcile
claude
```

Then type **`/hd-setup`** and follow along. Claude Code automatically picks up:

- **`CLAUDE.md`** — loads the project context (including [`AGENTS.md`](../AGENTS.md))
  into every session.
- **`.claude/skills/`** — project skills, available as slash commands and by auto-trigger:

| Skill | What it does |
| --- | --- |
| `/hd-setup` | First-time setup for your business: demo, QuickBooks mapping into `hd-sync.json`, history pull, first sync. |
| `/hd-sync` | **The weekly sync** ([docs/09](09-weekly-sync.md)): fetch, merge, draft, approve, post, close out. |
| `/hd-job-audit` | One job's Home Depot cost against QuickBooks, month by month. |
| `/cancellation-sweep` | Card credits with no receipt (cancellations, price adjustments) → the right job. |
| `/hd-runbook`, `/qbo-poster` | The older ledger-first path ([docs/06](06-runbook.md)): Gmail receipts → `ledger.csv` → QuickBooks. |

The Claude Code skills are thin wrappers over the client-agnostic templates in
[`ai/skills/`](../ai/skills/) — one source of truth, two entry points.

Two steps happen outside the terminal. Home Depot data comes from **your** signed-in browser tab:
with the Claude in Chrome extension connected, Claude runs the fetch there itself; without it, you
paste the script into DevTools ([docs/09](09-weekly-sync.md#fetching-without-a-browser-tool)). And the
final **Match** clicks happen in the QBO UI ([docs/07](07-quickbooks-connector.md) explains why no
connector can do those).

## Wiring up QuickBooks (for posting)

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

5. In a new session, run `/hd-setup` (or `/hd-sync` if you're set up) — it smoke-tests the tools
   and confirms the target company before anything is proposed.

**Multiple companies:** one QBO company file (realm) = one OAuth connection. Run one
server instance per company and name them unmistakably (`quickbooks-acme`,
`quickbooks-acme-llc-2`) — posting into the wrong company is the expensive mistake.

**Credentials hygiene:** prefer `--scope user` (config lives in your home directory,
not the repo). If you use `--scope project`, Claude Code writes `.mcp.json` into this
repo — that file is gitignored here precisely so OAuth secrets can't end up in a
public fork. Keep it that way.

## Making it weekly

Run `/hd-sync` once a week. Most of it is the scripts. Your time goes to answering its questions
and approving the proposal. Keep **posting** interactive: the skill stops for approval by design,
and an unattended run should only get as far as `PROPOSAL.md`. The sync needs your local files and
your signed-in browser, so schedule it on your own machine (or just make it a Monday habit), not on
hosted infrastructure.

## Not a Claude Code user?

Everything here degrades gracefully: the same skills exist as client-agnostic templates
in [`ai/skills/`](../ai/skills/), the connector guidance in
[docs/07](07-quickbooks-connector.md) applies to any MCP client, and the scripts are
plain Python you can run yourself.
