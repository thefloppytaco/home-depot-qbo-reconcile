#!/usr/bin/env python3
"""Merge a fresh Home Depot pull into hd_orderhistory_full.json and list exactly what is new.

    python3 src/merge_orderhistory.py --sync sync/2026-10-12                 (dry run: prints the comparison)
    python3 src/merge_orderhistory.py --sync sync/2026-10-12 --write         (backs up, appends, writes new_rows.csv)

--pull   the fresh pull: hd_bundles_*.jsonl from src/fetch_receipts.js (default: the newest one in --sync), or a
         hd_orderhistory_full.json-shaped file from src/pull_orderhistory.js
--zone   compare-zone start (default: the pull's first date). Every stored row dated on/after it must reappear in
         the pull. If one does not, the script STOPS and writes nothing - a row Home Depot no longer returns is
         usually a mapping problem, occasionally a voided transaction, and either way a human should look.
--late   rows dated before this are flagged late=Y in new_rows.csv: they rang up before the last sync but only
         appeared now (default: hd_sync_state.json "next_window_after", if present)

Matching is a multiset on (transaction, date, total, store) with the register as the tie-break, because two
registers in one store can ring the same transaction number on the same day. Stored rows are never modified;
differences on matched rows (PO edited after the fact, tenders, receipt) go to <sync>/job_diffs.csv.

Writes in <sync>/: job_diffs.csv (always) and, on --write, new_rows.csv - the week's checklist, with blank
bucket / entry_key / qbo_id / notes columns. src/gen_entries.py fills bucket and entry_key; every row must end up
with a bucket (proposed, not-posted, sub-row, cancel, held, ...) so the week's arithmetic adds up.

The checklist is the rows that are new in this pull, PLUS:
  - rows held last week (hd_sync_state.json "held", written by closeout.py) that are still not booked;
  - on the FIRST sync (no hd_sync_state.json yet), every stored row dated on/after hd-sync.json's cutoff_date
    that line_items.csv doesn't already book - setup's full-history pull already stored them, so they would
    otherwise never count as "new". --since DATE does the same for any date.
A first run with no hd_orderhistory_full.json yet creates it from the pull. Stdlib only.
"""
import argparse, collections, csv, datetime as dt, glob, json, os, shutil, sys


def norm(s): return ' '.join(str(s or '').split()).upper()
def ttuple(tn): return sorted((str(t.get('net')), str(t.get('last4')), str(t.get('amt'))) for t in tn or [])
def reg_of(r): return str(r.get('register') or (r.get('receipt') or '')[:5]).lstrip('0')


def spine_row(r):
    """Normalize a row to the hd_orderhistory_full.json shape (+ register/txc/order when known)."""
    out = {'date': str(r.get('date', ''))[:10], 'type': r.get('type', ''), 'origin': r.get('origin', ''), 'store': int(r.get('store') or 0),
           'job': r.get('job') or '', 'total': round(float(r.get('total') or 0), 2), 'pretax': round(float(r.get('pretax') or 0), 2),
           'tx': int(r.get('tx') or 0), 'receipt': r.get('receipt') or '', 'invoices': r.get('invoices') or [],
           'tenders': [{'net': t.get('net'), 'last4': str(t.get('last4') or ''), 'amt': t.get('amt')} for t in r.get('tenders') or []]}
    for k in ('register', 'txc', 'order'):
        if r.get(k) not in (None, ''):
            out[k] = r[k]
    return out


def read_pull(path):
    """-> (meta, [(row, nick)])"""
    if path.endswith('.jsonl') or path.endswith('.txt'):
        meta, rows = {}, []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith('@@B'):
                    line = line[3:]
                b = json.loads(line)
                if '_meta' in b:
                    meta = b['_meta']
                elif b.get('row'):
                    rows.append((spine_row(b['row']), b.get('nick', '')))
        return meta, rows
    with open(path) as f:
        d = json.load(f)
    meta = {k: d.get(k) for k in ('pulled', 'start', 'end') if k in d}
    meta['from'] = d.get('start')
    return meta, [(spine_row(r), '') for r in d['rows']]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument('--sync', default=os.path.join('sync', dt.date.today().isoformat()))
    ap.add_argument('--pull')
    ap.add_argument('--spine', default='hd_orderhistory_full.json')
    ap.add_argument('--zone')
    ap.add_argument('--late')
    ap.add_argument('--since', help='also list stored rows on/after this date that line_items.csv does not book')
    ap.add_argument('--line-items', default='line_items.csv')
    ap.add_argument('--write', action='store_true')
    a = ap.parse_args(argv)
    S = a.sync.rstrip('/')
    pull = a.pull or (sorted(glob.glob(os.path.join(S, 'hd_bundles*.jsonl'))) or [None])[-1]
    if not pull or not os.path.exists(pull):
        sys.exit(f'no pull file - run src/fetch_receipts.js and save hd_bundles_<date>.jsonl into {S}/ (or pass --pull)')
    meta, inc = read_pull(pull)
    if meta.get('job'):
        sys.exit(f"{pull} is a job-filtered pull (job={meta['job']!r}) - it is not the whole history, so it is never merged. Use it with src/audit_job.py.")
    if not inc:
        sys.exit(f'{pull} has no rows')
    zone = a.zone or meta.get('from') or min(r['date'] for r, _ in inc)
    state = json.load(open('hd_sync_state.json')) if os.path.exists('hd_sync_state.json') else None
    late = a.late or (state or {}).get('next_window_after') or zone
    if state and state.get('next_window_after') and zone > state['next_window_after']:
        sys.exit(f"this pull starts {zone}, after last sync's window ended ({state['next_window_after']}) - rows in between would be "
                 f"missed. Fetch again from about three weeks before {state['next_window_after']}.")
    since = a.since
    if not since and state is None:
        try:
            since = json.load(open('hd-sync.json')).get('cutoff_date')
        except (OSError, ValueError):
            since = None
        if since:
            print(f'first sync (no hd_sync_state.json): also listing stored rows since cutoff_date {since} that are not booked yet')
    if since and zone > since:
        sys.exit(f'this pull starts {zone} but rows since {since} are still to be booked - fetch again from {since}')
    inc = [(r, n) for r, n in inc if r['date'] >= zone]
    print(f'pull: {pull} | rows on/after zone {zone}: {len(inc)}')

    if os.path.exists(a.spine):
        with open(a.spine) as f:
            store = json.load(f)
    else:
        print(f'{a.spine} does not exist yet - this run creates it from the pull')
        store = {'pulled': '', 'rows': []}
    stored = store['rows']
    key = lambda x: (int(x['tx']), x['date'], round(float(x['total']), 2), int(x['store']))
    zone_stored = [(i, s) for i, s in enumerate(stored) if s['date'] >= zone]
    print(f'stored rows on/after zone: {len(zone_stored)} | total stored: {len(stored)}')

    pool = collections.defaultdict(list)
    for i, s in zone_stored:
        pool[key(s)].append((i, s))
    matched, new, used = [], [], set()
    for row, nick in inc:
        cands = [c for c in pool.get(key(row), []) if c[0] not in used]
        if len(cands) > 1:
            cands = [c for c in cands if reg_of(c[1]) == reg_of(row)] or cands
        if cands:
            used.add(cands[0][0])
            matched.append((row, cands[0][1]))
        else:
            new.append((row, nick))
    vanished = [s for i, s in zone_stored if i not in used]
    print(f'matched: {len(matched)} | new: {len(new)} | vanished: {len(vanished)}')
    for s in vanished:
        print('  VANISHED', s['date'], s['type'], s['receipt'], s['total'], s['job'])

    os.makedirs(S, exist_ok=True)
    diffs = []
    for row, s in matched:
        f = {}
        if norm(row['job']) != norm(s['job']):
            f['job'] = (s['job'], row['job'])
        if ttuple(row['tenders']) != ttuple(s['tenders']):
            f['tenders'] = (ttuple(s['tenders']), ttuple(row['tenders']))
        if round(row['pretax'], 2) != round(float(s['pretax']), 2):
            f['pretax'] = (s['pretax'], row['pretax'])
        if s.get('receipt') and row['receipt'] != s['receipt']:
            f['receipt'] = (s['receipt'], row['receipt'])
        if row.get('order') and s.get('order') and row['order'] != s['order']:
            f['order'] = (s['order'], row['order'])
        for k, (x, y) in f.items():
            diffs.append([row['date'], row['receipt'], row['total'], k, x, y])
    with open(os.path.join(S, 'job_diffs.csv'), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['date', 'receipt', 'total', 'field', 'stored', 'incoming'])
        w.writerows(diffs)
    print(f'differences on matched rows: {len(diffs)} -> {S}/job_diffs.csv' + (' (a changed PO on a row you already booked may mean a re-line)' if diffs else ''))
    print('NEW ROWS:')
    for row, _ in sorted(new, key=lambda x: (x[0]['date'], x[0]['receipt'])):
        tn = ';'.join(f"{t['net']}..{t['last4']}={t['amt']}" for t in row['tenders'])
        print(f"   {row['date']} {row['type']:<8} {row['receipt']} {row['total']:>10} {tn} {row['job']!r} {row.get('order', '')}"
              + ('  LATE' if row['date'] < late else ''))

    if vanished:
        print('STOP: stored rows are missing from the pull. Nothing written. Check the pull covered the whole zone, '
              'then look each one up on homedepot.com before deciding.')
        return 2
    if not a.write:
        print('DRY RUN - only job_diffs.csv written. Re-run with --write to merge.')
        return 0

    if os.path.exists(a.spine):
        bk = f"{os.path.splitext(a.spine)[0]}.backup-{dt.date.today().strftime('%Y%m%d')}.json"
        for sfx in 'bcdefgh':
            if not os.path.exists(bk):
                break
            bk = f"{os.path.splitext(a.spine)[0]}.backup-{dt.date.today().strftime('%Y%m%d')}{sfx}.json"
        shutil.copy2(a.spine, bk)
        print('backup', bk)
    # rows already stored that still need booking: held last week, or (first sync / --since) never booked
    booked = set()
    if os.path.exists(a.line_items):
        with open(a.line_items, newline='') as fh:
            for r in csv.DictReader(fh):
                booked.add((r.get('date'), r.get('receipt_locator') or ('order:' + (r.get('order_number') or ''))))
    held = {(h.get('date'), h.get('receipt') or h.get('loc')) for h in (state or {}).get('held', [])}
    rk = lambda r: r.get('receipt') or ('order:' + (r.get('order') or ''))
    carry = []
    for row, s in matched:
        k = (row['date'], rk(row))
        if k in booked:
            continue
        if k in held:
            carry.append((row, 'held last sync'))
        elif since and row['date'] >= since:
            carry.append((row, f'stored, not booked since {since}'))
    for d, loc in held - {(r['date'], rk(r)) for r, _ in carry}:
        if (d, loc) not in booked and d < zone:
            print(f'  NOTE held row {d} {loc} is older than this pull ({zone}); fetch from {d} to draft it, or it stays held')
    for row, _ in new:
        stored.append(row)
    store['pulled'] = meta.get('fetched_at') or meta.get('pulled') or dt.datetime.now().isoformat(timespec='seconds')
    with open(a.spine, 'w') as fh:
        json.dump(store, fh, separators=(', ', ': '), ensure_ascii=False)
    json.load(open(a.spine))
    print(f'{a.spine}: {len(stored)} rows')

    # sub-rows: tender-less rows of a batch return (same date/store/transaction as the row with tenders), and rows that
    # repeat part of another row's receipt (an order-return row beside the register row). The biggest row is the receipt.
    agg, big = {}, {}
    for row, _ in inc:
        if row['tenders']:
            agg.setdefault((row['date'], row['store'], row['tx']), row['receipt'])
            k = (row['date'], row['receipt'])
            if row['receipt'] and (k not in big or abs(row['total']) > abs(big[k]['total'])):
                big[k] = row
    cols = 'n,date,type,store,receipt,tx,order,po_online,total,pretax,tenders,nick,invoices,subrow_of,late,bucket,entry_key,qbo_id,notes'.split(',')
    listed = [(row, nick, '') for row, nick in new] + [(row, '', why) for row, why in carry]
    with open(os.path.join(S, 'new_rows.csv'), 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for n, (row, nick, why) in enumerate(sorted(listed, key=lambda x: (x[0]['date'], x[0]['receipt'])), 1):
            sub = '' if row['tenders'] else agg.get((row['date'], row['store'], row['tx']), '')
            if row['tenders'] and big.get((row['date'], row['receipt'])) is not None and big[(row['date'], row['receipt'])] is not row:
                sub = row['receipt']
            w.writerow([n, row['date'], row['type'], row['store'], row['receipt'], row['tx'], row.get('order', ''), row['job'], row['total'],
                        row['pretax'], ';'.join(f"{t['net']}..{t['last4']}={t['amt']}" for t in row['tenders']), nick,
                        ';'.join(map(str, row['invoices'])), sub, 'Y' if row['date'] < late and not why else '', '', '', '', why])
    print(f'{S}/new_rows.csv: {len(listed)} rows ({len(new)} new' + (f', {len(carry)} stored but not booked yet' if carry else '') +
          f'). Next: python3 src/build_ledger.py, then python3 src/gen_entries.py --sync {S} --only-new')
    return 0


if __name__ == '__main__':
    sys.exit(main())
