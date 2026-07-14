---
name: home-depot-receipt-log
description: "Daily: pull new Home Depot receipts from Gmail into a receipt-log table; flag returns, store-credit, and multi-card receipts."
---

# Daily Home Depot receipt-log task (template)

Append NEW Home Depot receipts to your receipt-log table. Each run is fresh. This is a
genericized template — replace every `<...>` placeholder with your own values before use.
It assumes a Gmail connector, a table/database connector (e.g. Airtable), and a shell for
parsing. See [`../../docs/05-daily-receipt-log.md`](../../docs/05-daily-receipt-log.md)
for the schema and rationale, and [`../context.md`](../context.md) for the data model.

## Target (fill in your own)

- Base / database id: `<BASE_ID>`
- Table id: `<TABLE_ID>`  (a "Receipts" table)
- Field ids: Receipt=`<...>`, Txn Date=`<...>`, Time=`<...>`, Type=`<...>`, Project=`<...>`,
  Store=`<...>`, Subtotal=`<...>`, Tax=`<...>`, Total=`<...>`, Tenders=`<...>`,
  Store Credit=`<...>`, Has Return=`<...>`, Has Store Credit=`<...>`, Needs SC Leg=`<...>`,
  QBO Status=`<...>`, Gmail Msg ID=`<...>`, Notes=`<...>`.

## Steps

1. **Gmail search** `from:HomeDepot@order.homedepot.com newer_than:3d` (page size ~40). Keep
   only receipts: subject "Your Electronic Receipt" (in-store, itemized in the HTML body) and
   "your Home Depot receipt for # H…" (order receipts). Ignore marketing, shipping/delivery,
   quote, and verification-code emails.
2. **Dedupe:** list existing rows, collect their `Gmail Msg ID` values, and skip any message
   already present. Only add receipts not already logged.
3. For each NEW "Your Electronic Receipt": fetch the full body and parse it (see the parsing
   rules in `docs/05-daily-receipt-log.md`):
   - Strip HTML; take the FIRST copy only (cut at `RETURN POLICY DEFINITIONS`).
   - Header `STORE REG TXN MM/DD/YY HH:MM AM/PM` → Store = first 4 digits, Txn Date, Time.
   - SUBTOTAL / SALES TAX / TOTAL just before the single `TOTAL` line (anchor TOTAL at line
     start so it ≠ SUBTOTAL). Total negative if the TOTAL line shows a minus sign.
   - Project = text after `PO/JOB NAME:`.
   - Tenders = each `XXXX####` + network + optional amount line; back-solve a single blank
     tender = Total − sum(known legs).
   - Type = Return if Total < 0; Mixed if body has `ORIG REC` AND item lines; else Sale.
     Has Return = (Total < 0 or body has ORIG REC). Has Store Credit = any tender is store
     credit.
4. **Insert** each new receipt. Receipt label = "MM/DD/YY HH:MM AM/PM · Type · $Total ·
   Project". Tenders field = "Type …last4 amount | …"; Store Credit field = only the
   store-credit legs. Set QBO Status = "To log"; Needs SC Leg = Has Store Credit; Notes = "".
5. For order receipts ("…# H…") with no itemized totals (PDF-only), add a stub row (Txn Date
   = email date, Project/Total blank, QBO Status = "To log", Notes = "Order receipt —
   itemization in PDF; verify on Pro site"). Don't fail the run over these.
6. **Never modify or delete existing rows.** Only add new ones.
7. Finish with a short summary: how many added, and explicitly call out any new **Return**,
   **store-credit** (Needs SC Leg), or **multi-card** receipt — those need manual QuickBooks
   handling because those legs don't all come through the bank feed.
8. **Cancellations:** an item pulled from an order before fulfillment produces a card CREDIT
   with NO receipt, so it won't appear here (and is zeroed/missing in the order-history data).
   Note in the summary that any unmatched Home Depot card *credit* in QBO For Review tied to
   no receipt/return should be treated as a possible cancellation and resolved via Order
   Details on the Pro site. Full playbook: `docs/04-quickbooks-workflow.md`.
