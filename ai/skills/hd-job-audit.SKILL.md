---
name: hd-job-audit
description: "Check one job's Home Depot cost end to end: everything Home Depot says the job should carry, month by month, against QuickBooks - including store-credit buys and cross-job returns."
---

# Audit one job's Home Depot cost

Use when the owner asks "are the Home Depot numbers right for <job>?", or hands you a list of
purchases for a job to check. A job's real Home Depot cost is not just the receipts with its PO:

- purchases paid entirely with **store credit or gift cards** never reach the bank feed;
- a **batch return** at the counter often carries some other job's PO while crediting this job's items
  (and the reverse) — only the receipt's ORIG REC lines say whose items they were;
- **cancellations and price adjustments** show only in an order's charge history.

Read [`../context.md`](../context.md) first. Same tools and privacy rules as
[`hd-sync.SKILL.md`](hd-sync.SKILL.md); this skill only reads QuickBooks until the owner approves fixes.

## 1. Everything Home Depot has for the job

In the owner's signed-in Purchase History tab, run [`src/fetch_receipts.js`](../../src/fetch_receipts.js)
with Home Depot's own job search:

```js
window.HD_FETCH = {from: '<a date before the job started>', job: '<street number and name>', output: 'page'}
```

`job` is a case-insensitive substring match on Home Depot's side, so `'123 Example'` finds every
spelling the registers used. Save the chunks as `sync/audit-<job>/hd_bundles_<date>_job.jsonl` (step 2
of the sync skill explains reading chunks). A job-filtered file is never merged into the history.

**Returns that carry another job's PO** are not in that search. `audit_job.py` finds them in the
weekly bundles already under `sync/` and in `hd_orderhistory_full.json`. For full coverage over the
job's period, also fetch every transaction for those dates (no `job`), save it beside the job file,
and pass both.

## 2. Build the Home Depot side

```bash
python3 src/audit_job.py --job "<job as in hd-sync.json>" --pull sync/audit-<job>/hd_bundles_<date>_job.jsonl
```

It reads every `hd_bundles*.jsonl` under `sync/` by default, so a whole-period fetch saved as
`sync/audit-<job>/hd_bundles_<date>_period.jsonl` is picked up without extra flags. It writes
`audits/audit_<job>.md` (monthly purchases / store-credit share / returns / net, the store-credit-only
purchases, and flags) and `audits/audit_<job>.csv` (one line per transaction or return group, with the
QuickBooks id it is booked to when `line_items.csv` knows it).

## 3. The QuickBooks side, by month

Profit and Loss for that customer/project, vendor The Home Depot, columns by month. Vendor-filtered
reports usually leave out journal entries, so add the job's store-credit refund journal entries (lines
whose customer is this job). Many connectors take customer + vendor + "summarize by month" on the P&L
report; otherwise search purchases by customer and date.

## 4. Compare and explain

Months that tie need nothing. For a month that differs, line up the audit CSV rows for that month with
the QuickBooks entries and name each difference: a purchase not booked, booked to another job, booked
at the row total instead of the card charge, a return credited by its row PO instead of its ORIG REC,
a store-credit purchase or refund that was never created, a cancellation never credited.

Lists the owner hands over usually double-count returns (the aggregate row plus its sub-rows plus a
journal entry), use row totals instead of card charges, leave out adjustments and cancellations, and
date items by Home Depot's last-activity date. Reconcile to the cent before concluding anything.

## 5. Propose fixes

Propose, don't post. Prefer **re-lining** an existing entry (same total and date, new job on the lines)
over deleting and recreating — re-lines keep bank-feed matches intact. Missing entries follow the sync
skill's rules. Post only what the owner approves, then record it (`closeout.py` for new entries; a note
in the booking log for re-lines).

Save the write-up as `audits/<job>-<date>.md`: the monthly table, each difference with its receipt and
QuickBooks id, and what was changed.
