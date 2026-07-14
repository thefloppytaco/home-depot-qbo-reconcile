# 04 · The QuickBooks bookkeeping playbook

This is the "why it's hard, and how to do it right" guide. The scripts produce a clean
`ledger.csv`; this doc is how you get those transactions into QuickBooks Online (QBO)
**booked to the correct job, including returns and store credit.**

Everything here is generic. Fill in your own account, project, and card IDs in
[`../templates/accounts.example.yml`](../templates/accounts.example.yml) and keep that file
private (it's git-ignored).

---

## The core problem

A renovation/flip business buys at Home Depot constantly, across many job sites, on multiple
cards and store-credit cards, with frequent returns. Home Depot's tender model does **not**
map cleanly onto the QBO bank feed:

- A **card** tender leg hits the card/bank feed → shows up in QBO **For Review** as **one
  transaction per card**. A split-tender receipt therefore fragments into several feed items.
- **Store-credit legs, some returns, and all cancellations never appear in any feed.** If you
  only categorize what the feed shows you, the affected jobs are silently wrong.

**Golden rule:** *One Home Depot receipt = one set of QBO entries, split by job, with
returns / store-credit-used as negative lines. The card legs equal what actually hit the
card.*

---

## How to book each situation

| Situation | How to book it |
| --- | --- |
| **Card purchase** | Categorize the feed item → the job's **COGS** (e.g. a "Supplies & materials" or "Tools" COGS account), tagged to the **job/project**. |
| **Return refunded to a card** | **Credit Card Credit** → the job's COGS. |
| **Cancellation** (item pulled before fulfillment) | **Credit Card Credit** → the order's job's COGS. See *Cancellations* below. |
| **Store credit earned** (a return loaded onto a store-credit card) | **Journal Entry / Deposit** into your **"HD Store Credit"** clearing account (debit), crediting the job's COGS. |
| **Store credit spent** (store-credit tender on a purchase) | **Expense from "HD Store Credit"** → the job's COGS. |
| **Mixed same-receipt** (return job A + buy job B, pay the difference) | One **split** transaction: positive line(s) to job B, negative line(s) to job A; the total equals the card charge. |

### The store-credit clearing account

Create one account (type **Bank / Cash-on-hand**, so QBO lets you "pay from" it on Expense
and Deposit forms — an *Other Current Asset* can't be a pay-from account) named e.g.
**"HD Store Credit."** Route **all** store-credit / merchandise-card tenders through it.
Money earned as store credit is a **debit** in; money spent is a **credit** out. When you've
booked both legs of a receipt, the account nets to zero for that receipt and both jobs carry
the right cost. Its running balance should mirror the physical cards' total balance once you
set an opening balance (see *Opening balance* below).

---

## Projects = customers

In QBO, jobs are modeled as **projects**, and a project belongs to a **customer**. Every
Home Depot line carries the job (`PO/JOB NAME`). When you book, set the customer reference to
the project's customer and QBO resolves the project. Keep your own job → customer/project ID
map in `templates/accounts.example.yml`; look up new jobs as they appear.

---

## Employee cards collapse into one account

If you issue multiple employee cards on the same credit line (e.g. several Amex cards under
one account, or several Visa sub-cards under one parent), QBO usually feeds them into **one**
account. You tell them apart by the **last-4 in the feed descriptor**, not by the account.
Your `tender_roster.csv` (produced by `parse_receipts.py`) is where you map each last-4 to
the QBO account it belongs to — fill in the `QBO_account_TO_FILL` column.

---

## Cancellations — the hardest gap

A **cancellation** is an item pulled from an order *before* fulfillment. The card is charged
for the whole order, then **credited** for the removed item. It is invisible to every
automated source:

- **No receipt** is ever produced for the canceled item.
- In the order-history pull, `Cancel` rows are **zeroed** — they flag *that* it happened,
  never the amount or card. Some cancellation orders don't appear at all.
- So the credit shows up **only on the bank feed** — a "ghost."

**Sweep rule:** an unmatched card **credit** with no receipt and no order-history return is a
**cancellation until proven otherwise.**

**Resolve it:** on the Home Depot Pro site → **Purchase History** → find the order (search
the order # or scan that card around the date — mind the date skew below) → **Order
Details** → the canceled item is marked *"Canceled [date]" Qty 0*. The credit equals its
**subtotal + tax**. Book a **Credit Card Credit** on that card → the order's project.

*Worked example (fictional):* an order for $1,094 charges the card in full; a $116.28 item
(10 × $10.97 + $6.58 tax) is later canceled, so the card is credited $116.28. Nothing but the
bank feed ever shows it. You book a $116.28 Credit Card Credit to that order's job.

---

## Date skew — never match on exact date

A single transaction can carry up to **three** different dates: the **receipt / Pro-Xtra**
date, the **bank charge** date, and the **posted** date — differing by days (e.g. Pro-Xtra
6/21, charges 6/22, posts 6/24). When matching a For-Review line to a Home Depot record,
**match on amount + card within a ±3-day window — never on exact date.** Exact-date matching
manufactures phantom discrepancies.

---

## Dedupe by amount + date, NOT by card last-4

When you sweep for "what's missing," most plain card purchases are **already booked via the
feed**; the real gaps are the store-credit legs and un-booked returns. To find the gaps
safely:

- **Dedupe against the full card register by amount + date (±3 days).**
- **Do not** dedupe by card last-4 — QBO memos don't reliably carry it (a mobile-wallet
  charge can show a different last-4 than the ledger), so a last-4 dedupe will *miss*
  already-booked entries and create duplicates.
- **Check every COGS account**, not just your main supplies account — some purchases post to
  a Tools COGS account instead.
- Genuine duplicates do exist (two separate small returns for the same order on different
  days). Don't assume a repeat is a dup — check the dates against the order.

---

## Pre-create / match model (recommended)

Rather than waiting for feed lines and clicking "Add," you can **pre-create** each expected
transaction as a project-tagged Expense / Credit Card Credit on the right account. When the
bank-feed line arrives in For Review, QBO offers it as a **1-click "Match."**

> **Match, don't Add.** For pre-created entries, always **Match** the feed line — clicking
> "Add" instead creates a duplicate.

---

## Opening balance for the store-credit account

If you start booking mid-history (a cutoff date), store credit earned *before* your cutoff
will be spent *after* it, so the clearing account can legitimately run negative until you set
an **opening balance** = (store credit earned − spent) through the day before your cutoff.
Compute it from the ledger. Until then, a negative balance is expected, not an error.

---

## A note on cutoffs and history

Pick a **cutoff date** and never modify booked history before it. Store-credit accounting is
additive — you add the missing legs, you don't rewrite what the feed already booked. Move the
cutoff forward as you go; the last thing you posted defines where the next run starts. The
[runbook](06-runbook.md) turns this into a repeatable weekly loop.
