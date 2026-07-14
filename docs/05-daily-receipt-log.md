# 05 · The daily receipt log (optional automation)

The batch pipeline (scripts → `ledger.csv`) is the backbone. This optional piece keeps a
**live, running log** of receipts so nothing slips between batch runs and the tricky ones get
flagged the morning they arrive.

Each morning a scheduled job:

1. Searches Gmail for the last few days of new Home Depot receipts.
2. De-duplicates against what's already logged (by Gmail message ID).
3. Parses each new receipt.
4. Appends a row to an **Airtable** table.
5. **Flags** any receipt that needs manual QuickBooks handling — a **return**, a
   **store-credit** tender, or **multiple cards** — because those legs don't all come through
   the bank feed.

You don't have to use Airtable — any database or even a spreadsheet works. Airtable is just
convenient because it has a simple API and a nice grid UI. What matters is the **schema** and
the **flags**.

## Suggested table schema

Create a base and a "Receipts" table with these fields (names are yours to choose):

| Field | Type | Purpose |
| --- | --- | --- |
| Receipt | Single line text | Human label, e.g. `06/15/26 01:11 PM · Sale · $113.26 · <Job>` |
| Txn Date | Date | Transaction date (YYYY-MM-DD) |
| Time | Single line text | Time from the receipt header |
| Type | Single select | Sale / Return / Mixed |
| Project | Single line text | The PO/Job |
| Store | Single line text | Store number |
| Subtotal / Tax / Total | Currency | Amounts (Total is negative for returns) |
| Tenders | Long text | `Type …last4 amount \| …` summary |
| Store Credit | Long text | Only the store-credit legs |
| Has Return | Checkbox | Total < 0, or the body has an `ORIG REC` |
| Has Store Credit | Checkbox | Any tender is store credit |
| Needs SC Leg | Checkbox | Store-credit leg still to book in QBO |
| QBO Status | Single select | To log / Logged / Partial / Excluded-dup |
| Gmail Msg ID | Single line text | **Dedupe key** — never add the same message twice |
| Notes | Long text | Anything a human should know |

## Parsing rules (in-store "Your Electronic Receipt" HTML)

The daily job parses the same way `parse_receipts.py` does — key points:

- Strip HTML tags; take the **first copy only** (Home Depot's responsive emails repeat the
  receipt block — cut everything at `RETURN POLICY DEFINITIONS`).
- Header line `STORE REG TXN MM/DD/YY HH:MM AM/PM` → Store = first 4 digits, plus date/time.
- The summary **SUBTOTAL / SALES TAX / TOTAL** sit just before the single `TOTAL` line.
  Anchor `TOTAL` at the **start of the line** so it doesn't match `SUBTOTAL`. Total is
  negative if the `TOTAL` line shows a minus sign.
- `Project` = text after `PO/JOB NAME:`.
- Tenders = each line matching `XXXX####` then a network name then an optional amount. If
  exactly one tender has no amount, **back-solve** it = Total − sum(known tender amounts).
- `Type` = Return if Total < 0; Mixed if the body has `ORIG REC` **and** item lines;
  else Sale.

Order receipts ("your Home Depot receipt for # H…") itemize only in the **PDF attachment**,
which a Gmail connector can't read inline. Log a stub row (date + "to log", itemization in
PDF) and don't fail the run over it.

## The cancellation caveat, again

Cancellations (§ *Cancellations* in [04-quickbooks-workflow.md](04-quickbooks-workflow.md))
**cannot** be caught here — they produce no receipt. The daily summary should remind you that
any unmatched Home Depot **card credit** in QBO For Review that ties to no receipt/return
should be treated as a possible cancellation and resolved on the Pro site.

## Setting up the schedule

A genericized scheduled-task definition is in
[`../ai/skills/receipt-log.SKILL.md`](../ai/skills/receipt-log.SKILL.md). Fill in your own
Airtable base/table/field IDs and your receipt sender address, then register it to run daily
(e.g. 7:00 AM). Note that a desktop-hosted scheduled task only runs while the host app is
open; run it once manually first to pre-approve the Gmail + Airtable connections.
