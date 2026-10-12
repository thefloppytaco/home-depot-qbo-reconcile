# 09 · The weekly sync

Once a week, every Home Depot transaction since the last sync goes into QuickBooks, itemized and
on the right job. This page is for people. The AI's version of the same steps, with more detail
on each decision, is [`ai/skills/hd-sync.SKILL.md`](../ai/skills/hd-sync.SKILL.md). Most owners
just tell their assistant "run the Home Depot sync" and answer its questions. Read on to know what
it's doing, or to run the steps yourself.

**Before the first sync** you need `hd-sync.json` (your accounts, cards and jobs) and your order
history. Setup covers both: [`ai/skills/hd-setup.SKILL.md`](../ai/skills/hd-setup.SKILL.md).

## What happens

| Step | Who | What |
|---|---|---|
| 1. Fetch | browser script in your signed-in Home Depot tab | Every transaction since about three weeks before the last sync, with Home Depot's item detail, the printed receipt (register PO, store-credit balances, the ORIG REC lines on returns) and, for Pro Desk orders, the PO on the order PDF. Saved as `sync/<date>/hd_bundles_<date>.jsonl`. |
| 2. Merge | `merge_orderhistory.py` | Adds new rows to your history and writes `new_rows.csv`, the week's checklist. Stops if a row it already had has disappeared. |
| 3. Rebuild | `build_ledger.py` | Refreshes `ledger.csv`, which returns use to find their original purchases. |
| 4. Draft | `gen_entries.py` | Writes `entries.py`: one entry per card or store-credit payment, item by item, each on its job. Anything it can't decide is marked `# REVIEW`. |
| 5. Review | your AI, asking you | Resolves the `# REVIEW` lines: a new job, a new card, an original it can't find. Then runs `entries.py`, which checks that every number ties and writes `PROPOSAL.md`. |
| 6. Approve | **you** | Read the proposal. Nothing is posted until you say so. |
| 7. Post | your AI, through a QuickBooks connector | Creates the entries. No connector? You enter them from `PROPOSAL.md`. |
| 8. Close out | `closeout.py` | Records what was posted (`line_items.csv`, `booking_log_<date>.csv`) and where next week starts (`hd_sync_state.json`). |
| 9. Match | **you**, in QuickBooks | As card lines arrive in the bank feed, click **Match** on the entry already waiting. Never **Add**: that makes a duplicate. |

Store-credit purchases and store-credit refunds never show up in the bank feed. Their entries have
no feed line to match, and that's expected.

## Reading `PROPOSAL.md`

- A table with one line per entry: date, type, what paid (card or store credit), the QuickBooks
  account it lands in, the amount, and the job.
- **Card credit** = a refund to a card. **JE (store-credit refund)** = a refund onto a store-credit
  card, booked as a journal entry into your store-credit account. Returns show "by ORIG REC" and
  each job's share. One return can credit several jobs.
- **NOT POSTED** = a purchase on a card that isn't the company's (a client's card on your Pro
  account). It's itemized for the record but never booked.
- **Lines** lists every item, at the net price you paid.
- **Bucket arithmetic** proves every new Home Depot row is accounted for: posted, not posted, a
  sub-row inside another entry, a cancellation, or held for a question.

## Fetching without a browser tool

If your assistant can't run JavaScript in your browser, do step 1 yourself. It takes about two
minutes:

1. In Chrome, Edge or Firefox, sign in at homedepot.com and open **Purchase History**
   (`https://www.homedepot.com/myaccount/purchase-history`).
2. Open DevTools (F12, or Cmd+Opt+I on a Mac) → **Console**. If the browser warns about pasting,
   type `allow pasting` and press Enter.
3. Optional: to choose the start date, first paste this line and press Enter (your assistant will
   tell you the date). Without it, the script fetches the last 45 days.
   ```js
   window.HD_FETCH = {from: '2026-09-15'}
   ```
4. Paste the **whole** of [`src/fetch_receipts.js`](../src/fetch_receipts.js) and press Enter.
5. If the Console says **WAITING FOR IDS**, click to page 2 of your Purchase History once.
6. Wait for **done**. Your browser downloads `hd_bundles_<date>.jsonl`. Give that file to your
   assistant, or put it in `sync/<date>/` in the repo.

The script only reads your own account, talks only to homedepot.com, and never keeps your full
card number. It doesn't change anything on your account.

## Running the steps yourself

```bash
python3 src/merge_orderhistory.py --sync sync/2026-10-12            # dry run: read it
python3 src/merge_orderhistory.py --sync sync/2026-10-12 --write
python3 src/build_ledger.py
python3 src/gen_entries.py --sync sync/2026-10-12 --only-new
#   open sync/2026-10-12/entries.py, fix every "# REVIEW" line
cd sync/2026-10-12 && python3 entries.py && cd ../..
#   post the entries in PROPOSAL.md / payloads.json, note each QuickBooks id
python3 src/closeout.py --sync sync/2026-10-12 --ids P1=101,P2=102,J1=103 --write
```

Try the whole loop on fictional data first: `python3 examples/run_sync_demo.py`.

## When something looks wrong

- **"VANISHED" from the merge.** A row you had is missing from the fetch. Usually the fetch didn't
  cover the whole window: run it again from an earlier date. Otherwise Home Depot voided something.
  Look it up before going on.
- **A tie failure from `entries.py`.** An item, payment or receipt doesn't add up. The message names
  it. Fix that entry, never plug it.
- **The fetch says FAILED or you're signed out.** Sign in again in that tab and re-run.
- **The fetch returns 0 rows when you know you bought something.** Re-run it. Never treat an empty
  answer as "nothing new".
- **A job's numbers look off over time.** Run a job audit:
  [`ai/skills/hd-job-audit.SKILL.md`](../ai/skills/hd-job-audit.SKILL.md).
