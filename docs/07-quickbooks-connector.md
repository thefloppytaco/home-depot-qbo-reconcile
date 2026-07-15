# 07 · Using a QuickBooks connector / MCP

Pass ② of the [runbook](06-runbook.md) — posting the ledger — can be done by a human in
the QBO UI, or by an AI agent driving a QuickBooks connector (MCP server). This doc covers
what that connector must actually be able to do, how each ledger situation maps to a QBO
API entity, and the one thing **no** connector can do.

## Not every QuickBooks connector qualifies

QuickBooks connectors differ wildly. Many — including some official/first-party ones — are
**sales- and reports-oriented**: invoices, estimates, payment links, P&L and aging reports,
payroll. Those have no way to create Expenses, Credit Card Credits, Journal Entries, or
Deposits, which is precisely what posting this ledger requires. They can't run the
playbook, no matter how the skill is worded.

Checklist — the connector must expose tools that can:

**Read**

- Query/search **Purchase** transactions by account and date range — the dedupe pass
- List/search **Accounts** — to find the card accounts, COGS accounts, and the
  "HD Store Credit" clearing account by ID
- Search **Customers**, including sub-customers — projects live there
- Nice to have: a General Ledger / register report, and Vendor search

**Write**

- Create a **Purchase** — including `Credit: true` (that's a Credit Card Credit) and
  expense lines that carry BOTH an account ref (COGS) and a customer/project ref
- Create a **JournalEntry** (or a **Deposit**) — for the store-credit legs
- Nice to have: create Customer (new jobs appear all the time), create Vendor, and
  Attachable (attach the receipt file to the entry)

If your connector can only read, or can't tag lines with a project, don't force it: have
the agent produce a review-ready posting list from `ledger.csv` and enter the entries in
the QBO UI yourself. That's still most of the win.

## Setting one up

Three routes, in rough order of effort:

1. **Your AI client's connector directory.** In Claude, that's Settings → Connectors (other
   MCP clients have an equivalent); add a QuickBooks connector and authorize it against
   your company file. Directory connectors vary a lot — several, including first-party
   ones, are invoices-and-reports only — so run the smoke test below **before** trusting
   one with a posting run.
2. **A self-hosted QuickBooks MCP server** that wraps the full QBO Accounting API. Several
   open-source ones exist, and the API is a plain OAuth2 REST API if you'd rather build
   your own:
   - Create a free developer account at **developer.intuit.com** → create an app → note
     its Client ID / Client Secret. Every new app comes with a **sandbox company** —
     ideal for the first test run.
   - Run the MCP server with those credentials and complete its OAuth flow against your
     company. One QBO **company file = one connection** (one "realm"): if you run several
     companies, set up one connector instance per company and name them unmistakably —
     posting to the wrong company is the expensive failure mode.
   - Register the server with your client (Claude Desktop: Settings → Connectors → add
     custom connector; Claude Code: `claude mcp add`; other clients: their MCP config).
   - **Security:** this server holds write access to your books. Use code you (or someone
     you trust) have reviewed, scope it to one company, and revoke its access from the
     Intuit developer portal when you stop using it.
3. **No qualifying connector.** Fall back to the read-only flow above: the agent prepares
   the posting list, you enter it in the UI.

## First run: smoke test, then dry run

1. **Smoke test.** Ask the agent to list its QuickBooks tools and map them against the
   checklist above. Tool **names vary between servers** (`create_purchase` vs
   `create_expense` vs `qbo_create_...`) — the capability is what matters. The two
   deal-breakers: creating a Purchase with `Credit: true` and project-tagged lines, and
   creating a JournalEntry (or Deposit). Missing either → this connector can't post.
2. **Fill your ID map** (`accounts.yml`, from
   [`../templates/accounts.example.yml`](../templates/accounts.example.yml)). Easiest way:
   have the agent look everything up through the connector — search accounts (cards, COGS,
   "HD Store Credit"), the Home Depot vendor, and each job's customer/project — and read
   the IDs back to you.
3. **Dry run.** Pick a 2–3 day window. The agent proposes entries only (the
   [qbo-poster skill](../ai/skills/qbo-poster.SKILL.md) gates on your approval), posts one
   or two, you verify them in the QBO UI, and you click **Match** when the feed lines
   arrive. If you built your own server, do this first pass against the Intuit sandbox
   company. Only then widen the window.

## How each ledger situation maps to the QBO API

The playbook's situations ([docs/04](04-quickbooks-workflow.md)) in API terms — QBO's
"Expense" and "Credit Card Credit" UI forms are both the **Purchase** entity underneath:

| Ledger situation | API entity |
| --- | --- |
| Card purchase | `Purchase` — PaymentType `CreditCard`, AccountRef = the card account; each line: COGS AccountRef + CustomerRef = the project; EntityRef = the Home Depot vendor |
| Return refunded to a card | `Purchase` with **`Credit: true`** (a Credit Card Credit), same refs, to the *original* job |
| Store credit **earned** | `JournalEntry` — debit "HD Store Credit", credit the job's COGS (CustomerRef on the COGS line) |
| Store credit **spent** | `Purchase` — PaymentType `Cash`, AccountRef = "HD Store Credit" (it's a Bank-type account, so it can pay), line to the job's COGS |
| Mixed same-receipt | One `Purchase` with positive and negative lines; the total equals the actual card charge |
| Cancellation (resolved) | `Purchase` with `Credit: true` on that card → the order's project |

Projects are **sub-customers**: a line's CustomerRef points at the project and QBO
resolves the parent. Keep your ID map filled in (`accounts.yml`, from
[`../templates/accounts.example.yml`](../templates/accounts.example.yml)) so the agent
posts by ID instead of searching names every run.

## The hard limit: no API can see "For Review"

QuickBooks' public API does not expose the bank feed's **For Review** queue or the
**Match** action. No connector or MCP can list unmatched feed lines or click Match. The
playbook's **pre-create / match model** exists precisely because of this: the agent
creates the project-tagged entries via the API; when the feed line arrives, the QBO UI
offers a 1-click **Match**, and a human clicks it (**Match, don't Add**). Consequences:

- The dedupe pass reads the card **registers** (booked transactions), not For Review.
- The [cancellation sweep](../ai/skills/cancellation-sweep.SKILL.md) can't read For
  Review either — pull candidate credits from the register/report via the connector, or
  paste your For Review list to the agent.
- Nothing in this repo assumes an agent can complete a Match. Budget one human minute
  per run for the Match clicks.

## Guardrails when an agent holds the pen

Everything in [AGENTS.md](../AGENTS.md) applies, plus two connector-specific ones:

- Many QuickBooks MCPs also expose `delete_*`/`update_*` tools. The playbook never needs
  a delete; an agent should treat them as off-limits unless the user explicitly asks.
- Test the loop on a QBO **sandbox company** or a tiny date window first: post two or
  three entries, verify them in the UI, then widen the window.
