---
name: home-depot-qbo-poster
description: "Post a window of ledger.csv rows into QuickBooks Online, split by project, including the store-credit and return legs the bank feed never shows. Propose first, post only after approval."
---

# QBO posting run (template)

Turn rows of `ledger.csv` into correctly-booked QuickBooks Online entries. This is
"Pass ②" of the [runbook](../../docs/06-runbook.md) as an agent skill. It assumes a
QuickBooks connector (or a human driving QBO) and read access to the repo's outputs.
Read [`../context.md`](../context.md) first — invariants 1–12 all apply here.

This is a genericized template: fill every `<...>` from your private `accounts.yml`
(copied from [`../../templates/accounts.example.yml`](../../templates/accounts.example.yml)).

## Inputs

- `ledger.csv` (+ `receipts.json` for detail, `tender_roster.csv` for the card map)
- Your account map: store-credit clearing account `<ID>`, COGS accounts `<ID>`s,
  vendor `<ID>`, card accounts `<ID>`s, job → customer/project `<ID>`s
- **Cutoff date** `<YYYY-MM-DD>` and the **booking log** from the last run

## Steps

1. **Set the window.** Rows after the last booking-log entry, on/after the cutoff.
   Never touch anything booked before the cutoff.
2. **Dedupe against QBO** before creating anything: pull the relevant card registers
   and every COGS account for the window ± 3 days. Skip legs already booked or
   feed-matched. Dedupe by **amount + date (±3 days)** — **never by card last-4**.
   A repeated amount on nearby days can be two genuine returns; verify against the
   order before excluding.
3. **Book each row by situation** (rules table in
   [`../../docs/04-quickbooks-workflow.md`](../../docs/04-quickbooks-workflow.md)):
   - Card purchase → pre-create a project-tagged **Expense** on that card account →
     the job's COGS. The feed line will offer a 1-click **Match**.
   - Return refunded to a card → **Credit Card Credit** → the original job's COGS
     (the ledger's `project` column already resolved it; `(via receipt)` /
     `(via order#)` tags tell you how).
   - Store credit **earned** (return to a store-credit card) → JE/Deposit **into**
     the "HD Store Credit" account, crediting the job's COGS.
   - Store credit **spent** → **Expense from** "HD Store Credit" → the job's COGS.
   - Mixed receipt (return job A + buy job B) → one split transaction; lines sum to
     the actual card charge.
   - `Cancel` rows and mystery feed credits → hand to the cancellation-sweep skill.
4. **Hold the flagged rows.** Anything with `needs_review = YES` goes to a human list
   with the `review_reason`; never guess a project for `return-project-unknown` rows.
5. **Propose, then post.** Present the full list of entries you intend to create
   (date, type, amount, project, account). **Post only after explicit approval.**
6. **Close the loop.** Append each posted entry to the booking log (date, type,
   amount, job, QBO entry #), flip matching daily-log rows to "Logged", and remind
   the user: when the feed lines arrive, **Match — don't Add**.

## Guardrails

- No posting without approval (step 5) — this skill never auto-posts.
- The cutoff only moves forward; history before it is read-only.
- Card legs must equal what actually hit each card (golden rule).
- If the account map is missing an ID (new job, new card in `tender_roster.csv`),
  stop and ask rather than defaulting.
