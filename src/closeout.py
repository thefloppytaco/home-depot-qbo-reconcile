#!/usr/bin/env python3
"""After posting: stamp the real QuickBooks ids into the week's line items, append them to line_items.csv, write the
booking log, and save hd_sync_state.json for next week.

    python3 src/closeout.py --sync sync/2026-10-12 --ids P1=11681,P2=11682,J1=11678            (dry run)
    python3 src/closeout.py --sync sync/2026-10-12 --ids P1=11681,P2=11682,J1=11678 --write

Run from the repo root (where line_items.csv and hd_sync_state.json live). --ids maps every POSTED entry key from
entries.py to the id QuickBooks returned. Entries itemized with skip() need no id - their rows keep their reason.
Entered by hand, with no ids to hand? Use --manual instead of --ids: each entry is recorded as manual-<date>-<key>.
Receipts parked with hold() are written to hd_sync_state.json "held" so next week's merge lists them again, and
Cancel rows go to "open_cancellations" until their credit is booked (cancellation-sweep) - delete them from the
state file once resolved.
--write backs up line_items.csv (never overwriting an earlier backup), appends the week's rows, writes
booking_log_<date>.csv (never overwriting an earlier log for the same day), and writes hd_sync_state.json.
--sc-balance  the store-credit account's QuickBooks balance after posting (optional; recorded in the state file so
              next week can check the card chain)
--pending     free text: rows that rang up after the pull, to pick up next week
Stdlib only.
"""
import argparse, csv, datetime as dt, io, json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument('--sync', required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--ids', help='KEY=qbo_id,KEY=qbo_id,...')
    g.add_argument('--manual', action='store_true', help='entries were typed in by hand: record them as manual-<date>-<key>')
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--date', default=dt.date.today().isoformat())
    ap.add_argument('--sc-balance')
    ap.add_argument('--pending', default='')
    ap.add_argument('--line-items', default='line_items.csv')
    a = ap.parse_args(argv)
    S = a.sync.rstrip('/')
    E = json.load(open(os.path.join(S, 'entries.json')))
    posted = [e for e in E if e.get('book', True)]
    if a.manual:
        ids = {e['key']: f"manual-{a.date}-{e['key']}" for e in posted}
    else:
        ids = dict(x.strip().split('=', 1) for x in a.ids.split(',') if x.strip())
        dup = sorted({v for v in ids.values() if list(ids.values()).count(v) > 1})
        if dup:
            sys.exit(f'the same id was given for two entries: {dup} - every entry needs its own')
    missing = [e['key'] for e in posted if e['key'] not in ids]
    if missing:
        sys.exit(f'no QuickBooks id given for posted entries: {missing}')
    extra = [k for k in ids if k not in {e['key'] for e in posted}]
    if extra:
        sys.exit(f'ids given for keys that are not posted entries: {extra}')

    with open(os.path.join(S, 'line_items_new.csv'), newline='') as fh:
        rows = list(csv.reader(fh))
    hdr, body = rows[0], rows[1:]
    qi, ti = hdr.index('qbo_id'), hdr.index('line_total')
    for r in body:
        if r[qi].startswith('PENDING-'):
            r[qi] = ids[r[qi][8:]]
    assert not [r for r in body if r[qi].startswith('PENDING-')]

    by = {}
    for r in body:
        by[r[qi]] = round(by.get(r[qi], 0) + float(r[ti]), 2)
    bad = 0
    for e in posted:
        exp = -e['amount'] if e['type'].lower().startswith('return') else e['amount']
        got = by.get(ids[e['key']])
        ok = got is not None and abs(got - exp) < 0.005
        bad += not ok
        gtxt = '-' if got is None else f'{got:,.2f}'
        print(f"  {e['key']:4} #{ids[e['key']]:>8} {e['amount']:>10,.2f}  item rows sum {gtxt}" + ('' if ok else '   <-- CHECK'))
    for e in E:
        if not e.get('book', True):
            print(f"  {e['key']:4} {'not posted':>9} {e['amount']:>10,.2f}  {e.get('reason', '')}")
    if bad:
        sys.exit('item rows do not tie to their entries - fix before writing')

    LI = a.line_items
    raw = open(LI, 'rb').read() if os.path.exists(LI) else b''
    nl = '\r\n' if b'\r\n' in raw else '\n'
    log = f'booking_log_{a.date}.csv'
    for sfx in 'bcdefgh':
        if not os.path.exists(log):
            break
        log = f'booking_log_{a.date}{sfx}.csv'
    nlines = raw.count(b'\n')
    print(f'{LI}: {nlines} lines -> +{len(body)}; booking log -> {log}')
    if not a.write:
        print('DRY RUN - nothing written. Re-run with --write.')
        return 0

    if raw:
        bk = f"{os.path.splitext(LI)[0]}.backup-{a.date.replace('-', '')}.csv"
        if not os.path.exists(bk):
            shutil.copy2(LI, bk)
            print('backup', bk)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator=nl)
    if not raw:
        w.writerow(hdr)
    w.writerows(body)
    with open(LI, 'ab') as fh:
        if raw and not raw.endswith(b'\n'):
            fh.write(nl.encode())
        fh.write(buf.getvalue().encode())
    with open(os.path.join(S, 'line_items_new.csv'), 'w', newline='') as fh:
        csv.writer(fh).writerows([hdr] + body)

    try:
        import hdlib
        acc = hdlib.cfg()['accounts']
    except SystemExit:
        acc = {}
    with open(log, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow('qbo_id,date,type,amount,card,job,expense_account,receipt_locator,notes'.split(','))
        for e in E:
            job = '; '.join(f"{g[0]} {sum(i[3] for i in g[4]) + g[5]:.2f}" for g in e['groups']) if e['groups'] else (e['job'] or 'untagged')
            acct = e.get('acct') or (acc.get('tools', 'tools') if (e['job'] or '').lower().startswith('tools') else acc.get('cogs', 'cogs'))
            qid = ids.get(e['key']) or e.get('reason') or 'NOT POSTED'
            w.writerow([qid, e['date'], e['type'], f"{e['amount']:.2f}", e['card'], job, acct,
                        e['loc'] + (f" / {e['order']}" if e['order'] else ''), e['note']])
    state = {'last_sync': a.date, 'posted_ids': ids, 'last_posted_entry': max((int(v) for v in ids.values() if str(v).isdigit()), default=None),
             'next_window_after': max(e['date'] for e in E) if E else None, 'store_credit_balance': a.sc_balance, 'pending_rows': a.pending,
             'note': 'written by src/closeout.py - read this first next week'}
    prev = json.load(open('hd_sync_state.json')) if os.path.exists('hd_sync_state.json') else {}
    state['history'] = (prev.get('history') or [])[-25:] + [{k: state[k] for k in ('last_sync', 'next_window_after', 'last_posted_entry')}]
    hp = os.path.join(S, 'held.json')
    state['held'] = [dict(date=h['date'], receipt=h['loc'], order=h.get('order', ''), reason=h['reason'], since=a.date)
                     for h in (json.load(open(hp)) if os.path.exists(hp) else [])]
    cancels = list(prev.get('open_cancellations') or [])
    nr = os.path.join(S, 'new_rows.csv')
    if os.path.exists(nr):
        with open(nr, newline='') as fh:
            for r in csv.DictReader(fh):
                if r.get('bucket') == 'cancel' and not any(c.get('order') == r.get('order') and c.get('date') == r.get('date') for c in cancels):
                    cancels.append(dict(date=r.get('date'), order=r.get('order', ''), receipt=r.get('receipt', '')))
    state['open_cancellations'] = cancels
    with open('hd_sync_state.json', 'w') as fh:
        json.dump(state, fh, indent=1)
    print(f"written: {LI}, {log}, hd_sync_state.json (next window after {state['next_window_after']}; "
          f"{len(state['held'])} held, {len(cancels)} open cancellation(s))")
    return 0


if __name__ == '__main__':
    sys.exit(main())
