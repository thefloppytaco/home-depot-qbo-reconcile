# 02 · Gmail setup for `download_receipts.py`

`download_receipts.py` connects to Gmail over **IMAP** and downloads every Home Depot
receipt email (the PDF attachment, or the HTML body as a fallback) into `downloads/`.

> **Why IMAP instead of an API/connector?** Each receipt email is ~140 KB of HTML. Pulling
> a couple thousand of them through a chat connector is impractical and slow. IMAP runs
> locally, grabs everything in one pass, and costs nothing.

## 1. Turn on 2-Step Verification

App Passwords require it. In your Google Account: **Security → 2-Step Verification → turn
on** (if it isn't already).

## 2. Create an App Password

Google Account → **Security → 2-Step Verification → App passwords** → generate one (name it
something like `hd-receipts`). Google shows a 16-character password like
`abcd efgh ijkl mnop`. Copy it.

App Passwords are scoped and revocable — you can delete this one anytime without changing
your main password.

## 3. Create your local credentials file

Copy the template and fill in your values:

```bash
cp src/.gmail_creds.template src/.gmail_creds
```

Edit `src/.gmail_creds`:

```
EMAIL=your-receipts-inbox@gmail.com
APP_PASSWORD=abcd efgh ijkl mnop
```

Spaces in the app password are fine — the script strips them.

> **This file is git-ignored** (`.gmail_creds` and `gmail_creds.txt` are both in
> `.gitignore`). Never commit it, and never paste it into a chat. Only
> `.gmail_creds.template` (with a blank password) belongs in the repo.

The script also accepts a plain `src/gmail_creds.txt` with the same two lines if you prefer;
it reads that first and falls back to `.gmail_creds`.

## 4. Run it

Run from the repo root:

```bash
python3 src/download_receipts.py
```

It searches Gmail's **All Mail** for `from:HomeDepot@order.homedepot.com`, keeps only
messages whose subject contains "receipt" (skipping quotes and marketing), and writes each
one into `--out` (default `./downloads`) named `<date>_<order-or-tag>_<message-id>.pdf` (or
`.html`). It prints a running count and a final tally. Other flags: `--creds PATH` for a
non-default credentials file and `--query` to change the Gmail search (see
Troubleshooting) — run `python3 src/download_receipts.py --help` for the full list.

The first run backfills every Home Depot receipt in the mailbox; after that, the daily
receipt-log automation (see [05-daily-receipt-log.md](05-daily-receipt-log.md)) keeps up
with new receipts day to day. Re-running this script yourself is cheap and intended too —
it checks each message's headers against what's already in `downloads/` and skips anything
it already has, so a periodic re-run only downloads what's new. Pass `--refresh` to force a
full re-download instead.

## Troubleshooting

- **`Missing .gmail_creds`** — you didn't create the file, or it's not in `src/`.
- **Login fails** — you used your normal Google password instead of the App Password, or
  2-Step Verification isn't on.
- **0 matches** — the receipts arrive from a different sender address; check what
  Home Depot uses for your account and pass `--query` with the right search instead of
  editing the script.
