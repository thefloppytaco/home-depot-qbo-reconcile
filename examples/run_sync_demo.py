#!/usr/bin/env python3
"""
Run one whole weekly sync on synthetic data, start to finish, in one command:

    python3 examples/run_sync_demo.py

It builds a throwaway workspace in examples/demo-output/weekly-sync/ that looks like a real business's repo after a
few weeks of use (hd-sync.json, hd_orderhistory_full.json, line_items.csv, hd_sync_state.json), drops in this week's
fetch (examples/weekly-sync/sample_bundles.jsonl - the shape src/fetch_receipts.js writes), and runs:

    merge_orderhistory.py -> build_ledger.py -> gen_entries.py -> entries.py -> closeout.py -> audit_job.py

No credentials, no network, no QuickBooks. The "posting" step is simulated with made-up ids - in real use that is
where your AI shows you PROPOSAL.md, waits for your approval, and posts payloads.json through a QuickBooks connector.
Stdlib only.
"""
import os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = os.path.join(REPO, 'src')
SAMPLES = os.path.join(HERE, 'weekly-sync')
WS = os.path.join(HERE, 'demo-output', 'weekly-sync')
SYNC = os.path.join('sync', '2026-10-12')


def run(args, cwd=WS, show=True):
    print(f"\n$ {' '.join(args)}" + (f'    (in {os.path.relpath(cwd, REPO)})' if cwd != WS else ''), flush=True)
    r = subprocess.run([sys.executable] + args, cwd=cwd, capture_output=True, text=True)
    if show:
        print(r.stdout.rstrip())
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        sys.exit(f'step failed: {args[0]}')
    return r.stdout


def main():
    shutil.rmtree(WS, ignore_errors=True)
    os.makedirs(os.path.join(WS, SYNC))
    for src, dst in [('sample_hd-sync.json', 'hd-sync.json'), ('sample_history.json', 'hd_orderhistory_full.json'),
                     ('sample_line_items.csv', 'line_items.csv'), ('sample_hd_sync_state.json', 'hd_sync_state.json'),
                     ('sample_bundles.jsonl', os.path.join(SYNC, 'hd_bundles_2026-10-12.jsonl'))]:
        shutil.copy(os.path.join(SAMPLES, src), os.path.join(WS, dst))
    print(f'workspace: {os.path.relpath(WS, REPO)}/  (a fictional business, synthetic receipts)')

    print('\n=== 1. merge this week\'s fetch into the order history (dry run first, then --write) ===')
    run([os.path.join(SRC, 'merge_orderhistory.py'), '--sync', SYNC], show=False)
    run([os.path.join(SRC, 'merge_orderhistory.py'), '--sync', SYNC, '--write'])

    print('\n=== 2. rebuild the ledger (so every return can find its original sale) ===')
    run([os.path.join(SRC, 'build_ledger.py')])

    print('\n=== 3. draft the entries from the receipt bundles ===')
    run([os.path.join(SRC, 'gen_entries.py'), '--sync', SYNC, '--only-new'])
    with open(os.path.join(WS, SYNC, 'entries.py')) as f:
        draft = f.read()
    print(f'\n--- {SYNC}/entries.py (the draft your AI reviews; REVIEW lines must be resolved, CHECK lines glanced at) ---\n')
    print(draft)

    print('=== 4. run the draft: hard tie checks, then PROPOSAL.md + payloads.json (nothing is posted) ===')
    run(['entries.py'], cwd=os.path.join(WS, SYNC))
    with open(os.path.join(WS, SYNC, 'PROPOSAL.md')) as f:
        print('\n--- PROPOSAL.md (what you approve) ---\n')
        print(f.read())

    print('=== 5. (simulated) you approve, your AI posts each payload and records the ids QuickBooks returns ===')
    ids = 'P1=9101,P2=9102,P3=9103,S1=9104,J1=9105,C1=9106,C2=9107,C3=9108'
    run([os.path.join(SRC, 'closeout.py'), '--sync', SYNC, '--ids', ids, '--write'])

    print('\n=== 6. audit one job end to end ===')
    run([os.path.join(SRC, 'audit_job.py'), '--job', '123 Example St'])

    print('\nWhat to notice:\n'
          '  - The 10/08 batch return carries the PO "456 SAMPLE AVE", but its first ORIG REC block points at a 9/18\n'
          '    purchase booked to 123 Example St - so 37.10 of the store-credit refund credits 123 Example St (J1).\n'
          '  - The 10/06 split-tender sale: store credit pays for items in receipt order (S1, one split line), the Amex\n'
          '    leg carries all the tax (P2). PO "OFFICE" maps to 456 Sample Ave through a standing rule.\n'
          '  - The 10/07 purchase on the client\'s card (..9999) is itemized but NOT posted (X1).\n'
          '  - The 10/10 Pro Desk order has no register receipt; its PO comes from the order PDF, and its delivery fee is\n'
          '    derived from the receipt pretax (flagged CHECK).\n'
          '  - The 10/11 register return covered an in-store purchase AND an online order. Home Depot lists it twice: the\n'
          '    whole receipt (84.76) and an order-return row repeating the online part (31.76). Only the whole receipt is\n'
          '    booked (C2); the repeat is a sub-row, and its order number is how the online item finds its job.\n'
          '  - The 10/12 online-order return printed no tax line for its block; the tax is derived from the receipt total.\n'
          '  - The 10/02 row was already booked last week, so it is matched, not new. The Cancel row gets bucket "cancel".\n')


if __name__ == '__main__':
    main()
