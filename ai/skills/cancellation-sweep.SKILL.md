---
name: home-depot-cancellation-sweep
description: "Find and resolve Home Depot 'ghost' credits — cancellations that produce no receipt and no usable order-history row — and book them to the right project."
---

# Cancellation sweep (template)

Cancellations are the one Home Depot event **invisible to every automated source**: the
card is charged for the full order, then credited for the canceled item, with no receipt
and a zeroed or missing order-history row. The credit exists only in the bank feed.
This skill hunts them down. Background: § *Cancellations* in
[`../../docs/04-quickbooks-workflow.md`](../../docs/04-quickbooks-workflow.md);
data model: [`../context.md`](../context.md) (invariants 6–8).

Assumes a QuickBooks connector (read For-Review/registers) and the repo's `ledger.csv`.
Resolving amounts needs the Home Depot Pro site (a human, or a browser tool, signed in).

## Steps

1. **Collect candidates.** From QBO For Review (and recent register history) on every
   Home Depot-facing card account: every **credit** in the window.
2. **Eliminate the explained ones.** Match each credit against `ledger.csv` rows of
   type `Return` by **amount + card within ±3 days** (never exact date, never by
   last-4 alone). Also check store-credit-earned legs — those never hit the feed, so
   they can't explain a feed credit, but a same-amount coincidence can mislead;
   verify before excluding.
3. **Apply the sweep rule.** What remains — a card credit with no receipt and no
   order-history return — is a **cancellation until proven otherwise**.
4. **Resolve each one** on the Pro site: Purchase History → find the order (search
   the order # if known, else scan that card's orders around the date, mind the
   ±3-day skew) → Order Details → the canceled line shows *"Canceled [date]" Qty 0*.
   The credit equals that item's **subtotal + tax**. Confirm it matches the feed
   amount to the cent.
5. **Propose the entries:** for each resolved cancellation, a **Credit Card Credit**
   on that card → the order's project's COGS. Present the list; **post only after
   approval**. Then Match the feed line (don't Add).
6. **Report the unresolved.** Anything you couldn't tie to an order goes in a short
   list with dates, amounts, and card — a human decides. Never book a ghost credit
   to a default or guessed project.

## Guardrails

- Amounts must reconcile exactly; a near-miss means you found the wrong order line.
- Don't assume a repeated credit is a duplicate — two small returns for the same
  order on different days both happen. Check Order Details for both dates.
- Cutoff discipline: ignore credits dated before the user's cutoff.
