#!/usr/bin/env python3
"""Everything Home Depot says one job should carry, month by month - to check a project's books against.

    python3 src/audit_job.py --job "123 Example St" [--pull FILE] [--from 2026-01-01] [--to 2026-10-31]

Why this exists: a project's Home Depot cost is NOT just the purchases tagged with its PO. Purchases paid entirely
with store credit or gift cards never reach the bank feed; a batch return at the counter often carries some OTHER
job's PO while crediting this job's items (or the reverse); cancellations and post-sale adjustments only show in an
order's charge history. This script applies the same rules as the weekly sync to the whole life of one job:
  - a purchase belongs to the job named by its PRINTED PO (register receipt, or the Pro Desk order PDF), falling
    back to the online PO when no printed receipt was fetched;
  - each returned item belongs to the job of the ORIGINAL purchase its "ORIG REC" line points to - never the
    return row's own PO;
  - purchases paid with store credit are listed separately, because those are the ones bank-feed bookkeeping misses.

Inputs (all optional except --job):
  --pull      hd_bundles_*.jsonl from src/fetch_receipts.js run with job set to this job and from far back - the
              fastest way to get every row Home Depot's own search ties to the job, with receipts
  --spine     hd_orderhistory_full.json (default) - used to find the originals behind returns and the returns that
              carry another job's PO
  --bundles   glob for more receipt bundles (default: sync/**/hd_bundles*.jsonl) - more receipts = fewer unknowns
  --line-items line_items.csv (default) - when present, each row shows which QuickBooks entry it is booked to
Writes  audit_<job>.csv (one line per transaction or return group) and audit_<job>.md (monthly totals, the
        store-credit-only purchases, and every flag), in --out (default: audits/).
Compare the monthly totals to QuickBooks: Profit and Loss for this customer/project, vendor The Home Depot, by
month. A month that differs points at the entries to fix. Stdlib only.
"""
import argparse, collections, csv, glob, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hdlib  # noqa: E402
from gen_entries import Gen, SC_NETS, NETNAME, key2, norm, read_bundles, read_csv  # noqa: E402

LEDGER_NAME = dict(NETNAME, GR='Gift/Store Credit', CS='Gift/Store Credit', PERK_GC='Gift/Store Credit')


def tenders_ledger(tn):
    return ' | '.join(f"{LEDGER_NAME.get(t.get('net'), t.get('net'))}..{t.get('last4')}={t.get('amt')}" for t in tn or [])


def loc_of(b):
    return f"{str(b.get('reg')).zfill(5)}-{str(b.get('txc')).zfill(5)}"


def slug(s): return re.sub(r'[^A-Za-z0-9]+', '_', s).strip('_').lower()[:40]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument('--job', required=True)
    ap.add_argument('--pull')
    ap.add_argument('--spine', default='hd_orderhistory_full.json')
    ap.add_argument('--bundles', default=os.path.join('sync', '**', 'hd_bundles*.jsonl'))
    ap.add_argument('--line-items', default='line_items.csv')
    ap.add_argument('--from', dest='frm', default='0000')
    ap.add_argument('--to', default='9999')
    ap.add_argument('--out', default='audits')
    a = ap.parse_args(argv)

    try:
        config = dict(hdlib.cfg())
    except SystemExit:
        config = {'jobs': {}}
    config['jobs'] = dict(config.get('jobs') or {})

    # ---- rows: the spine plus the job pull, deduped
    rows, seen = [], set()

    def take(r):
        k = (int(r.get('tx') or 0), r['date'], round(float(r['total']), 2), int(r.get('store') or 0), str(r.get('register') or r.get('receipt', '')[:5]).lstrip('0'))
        if k not in seen:
            seen.add(k)
            rows.append(r)
    if os.path.exists(a.spine):
        for r in json.load(open(a.spine))['rows']:
            take(r)
    bfiles = sorted(set(glob.glob(a.bundles, recursive=True) + ([a.pull] if a.pull else [])))
    bundles, allb = {}, []
    for f in bfiles:
        for b in read_bundles(f):
            allb.append(b)
            if b.get('row'):
                take(b['row'])
            for k in {(b.get('d'), loc_of(b)), (b.get('d'), (b.get('row') or {}).get('receipt'))}:
                cur = bundles.get(k)
                # the whole receipt wins over a row that repeats part of it (or a tender-less sub-row); otherwise newer files win
                if k[1] and (cur is None or (bool(b.get('tn')), abs(float(b.get('tot') or 0))) >= (bool(cur.get('tn')), abs(float(cur.get('tot') or 0)))):
                    bundles[k] = b
    if not rows:
        sys.exit('no rows - pass --pull (a job pull from src/fetch_receipts.js) or keep hd_orderhistory_full.json at the repo root')

    ledger = [{'store': r.get('store'), 'date': r['date'], 'transaction_id': r.get('tx'), 'receipt_locator': r.get('receipt', ''),
               'project': r.get('job', ''), 'tenders': tenders_ledger(r.get('tenders')), 'total': r.get('total'), 'type': r.get('type', ''),
               'invoices': ' '.join(map(str, (r.get('invoices') or []) + ([r['order']] if r.get('order') else [])))} for r in rows]
    line_items = read_csv(a.line_items)
    logs = []
    for f in glob.glob('booking_log_*.csv'):
        with open(f, newline='') as fh:
            logs += list(csv.DictReader(l for l in fh if not l.startswith('#')))

    Gen.link_shadows(allb)
    orders = {}
    for r in rows:
        if r.get('order') and r.get('receipt') and not str(r.get('type', '')).lower().startswith(('return', 'cancel')):
            orders.setdefault(r['order'], (r['date'], r['receipt']))
    probe = Gen(config, ledger, line_items, logs, orders)
    target, _ = probe.resolve_job(a.job)
    if not target or target == 'Tools':
        target = a.job
        config['jobs'][target] = config['jobs'].get(target, '')
    g = Gen(config, ledger, line_items, logs, orders)
    is_target = lambda j: bool(j) and (j == target or key2(j) == key2(target))
    print(f'auditing job {target!r} | rows {len(rows)} | receipt bundles {len(bundles)} from {len(bfiles)} file(s)')

    out, flags = [], []
    done_returns = set()
    in_range = [r for r in rows if a.frm <= r['date'] <= a.to]
    by_txn = collections.defaultdict(list)
    for r in in_range:
        by_txn[(r['date'], int(r.get('store') or 0), int(r.get('tx') or 0))].append(r)

    def booked(r):
        hit = [x for x in line_items if x.get('date') == r['date'] and x.get('receipt_locator') == r.get('receipt')]
        return (','.join(sorted({x.get('qbo_id', '') for x in hit})), '; '.join(sorted({(x.get('job') or '').split(' (')[0] for x in hit}))) if hit else ('', '')

    def legs(r, ret):
        sc = card = 0.0
        for t in r.get('tenders') or []:
            try:
                v = float(t.get('amt'))
            except (TypeError, ValueError):
                continue
            if (v < 0) == ret:      # echo leg (a later return showing on the sale row, or the reverse)
                continue
            if t.get('net') in SC_NETS:
                sc += abs(v)
            else:
                card += abs(v)
        return round(card, 2), round(sc, 2)

    for r in sorted(in_range, key=lambda x: (x['date'], x.get('receipt', ''))):
        typ = str(r.get('type', ''))
        b = bundles.get((r['date'], r.get('receipt')))
        ret = typ.lower().startswith('return')
        if typ.lower().startswith('cancel'):
            if is_target(g.resolve_job(r.get('job', ''))[0]):
                flags.append(f"{r['date']} {r.get('receipt')} order {r.get('order', '')}: CANCEL row on this job - the credit only shows in the order's charge history "
                             '(ai/skills/cancellation-sweep.SKILL.md); make sure a card credit is booked')
            continue
        if not r.get('tenders'):
            if ret and is_target(g.resolve_job(r.get('job', ''))[0]):
                agg = [x for x in by_txn[(r['date'], int(r.get('store') or 0), int(r.get('tx') or 0))] if x.get('tenders')]
                if not agg:
                    flags.append(f"{r['date']} {r.get('receipt')}: sub-row of a batch return with this job's PO, but its aggregate row is not in the data")
            continue
        od, rc, big = (b or {}).get('od') or {}, (b or {}).get('rc') or {}, (b or {}).get('big') or {}
        printed = (rc.get('printedPO') or '').strip() or (big.get('po') or '').strip()
        online = (od.get('po_online') or r.get('job') or '').strip()
        po = printed or online
        job, _ = g.resolve_job(po)
        qid, bjob = booked(r)
        card, sc = legs(r, ret)
        base = {'date': r['date'], 'month': r['date'][:7], 'type': typ, 'receipt': r.get('receipt'), 'order': r.get('order', ''), 'row_po': r.get('job', ''),
                'printed_po': printed, 'tenders': tenders_ledger(r.get('tenders')), 'booked_qbo_ids': qid, 'booked_job': bjob}
        if not ret:
            if is_target(job):
                amt = round(card + sc, 2)
                f = []
                if not printed:
                    f.append('no printed receipt fetched - job from the online PO')
                elif online and not is_target(g.resolve_job(online)[0]):
                    f.append(f'printed PO wins over online PO {online!r}')
                if od and od.get('grand') is not None and abs(abs(od['grand']) - abs(float(r['total']))) > 0.005:
                    f.append(f"Home Depot's order total is now {od['grand']:.2f} vs {abs(float(r['total'])):.2f} charged - an adjustment/cancellation credit should exist")
                if line_items and not qid:
                    f.append('not found in line_items.csv')
                elif bjob and not is_target(g.resolve_job(bjob)[0]):
                    f.append(f'booked to {bjob!r}')
                out.append(dict(base, basis='printed PO' if printed else 'online PO', amount=amt, card=card, store_credit=sc, flags='; '.join(f)))
            elif is_target(g.resolve_job(online)[0]):
                flags.append(f"{r['date']} {r.get('receipt')} {r['total']}: online PO says this job but the printed PO {printed!r} says {job or 'another/unknown job'} - NOT counted here")
            continue

        # returns: allocate by ORIG REC, never by the row's PO
        key = (r['date'], int(r.get('store') or 0), int(r.get('tx') or 0), r.get('receipt'))
        if key in done_returns:
            continue
        done_returns.add(key)
        row_says_target = is_target(g.resolve_job(r.get('job', ''))[0]) or any(
            is_target(g.resolve_job(x.get('job', ''))[0]) for x in by_txn[key[:3]] if not x.get('tenders'))
        if b and rc.get('blocks'):
            groups = g.return_groups(b, od, rc, [], 0.0, po, job)
            for x in groups:
                gamt = round(sum(i[3] for i in x[4]) + x[5], 2)
                if is_target(x[0]):
                    f = [x[6]] if ('CHECK' in x[6] or 'REVIEW' in x[6]) else []
                    if not row_says_target:
                        f.append(f"the return row's PO is {r.get('job')!r} - credited here by ORIG REC" if r.get('job') else 'blank PO on the return - credited here by ORIG REC')
                    out.append(dict(base, basis=x[2], amount=-gamt, card='', store_credit='', flags='; '.join(f)))
                elif x[0] == 'REVIEW' and row_says_target:
                    out.append(dict(base, basis=x[2], amount=-gamt, card='', store_credit='', flags='UNRESOLVED (counted here because the row PO says this job): ' + x[6]))
                elif row_says_target:
                    flags.append(f"{r['date']} {r.get('receipt')}: return row carries this job's PO, but {x[2]} ({gamt:.2f}) is an original of {x[0]!r} - NOT this job's credit")
        elif row_says_target:
            amt = round(card + sc, 2)
            why = 'no printed receipt fetched' if not b else 'receipt has no ORIG REC lines'
            out.append(dict(base, basis='row PO (unverified)', amount=-amt, card=-card, store_credit=-sc,
                            flags=f'{why} - allocated by the row PO; batch-return POs are often another job\'s. Fetch its receipt to be sure'))

    os.makedirs(a.out, exist_ok=True)
    name = slug(target)
    cols = ['date', 'month', 'type', 'receipt', 'order', 'row_po', 'printed_po', 'basis', 'amount', 'card', 'store_credit', 'tenders', 'booked_qbo_ids', 'booked_job', 'flags']
    with open(os.path.join(a.out, f'audit_{name}.csv'), 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(out)

    months = collections.OrderedDict()
    for x in sorted(out, key=lambda x: x['date']):
        m = months.setdefault(x['month'], {'purchases': 0.0, 'returns': 0.0, 'sc_paid': 0.0, 'n': 0})
        if x['amount'] >= 0:
            m['purchases'] += x['amount']
            m['sc_paid'] += float(x['store_credit'] or 0)
        else:
            m['returns'] += x['amount']
        m['n'] += 1
    md = [f'# Home Depot audit: {target}', '', f"{len(out)} transactions / return groups"
          + (f" between {a.frm} and {a.to}" if a.frm != '0000' or a.to != '9999' else '') + '. Amounts include sales tax. '
          'Compare each month to QuickBooks: Profit and Loss for this project, vendor The Home Depot, by month.', '',
          '| Month | Purchases | of which store credit | Returns | Net | Rows |', '|---|---:|---:|---:|---:|---:|']
    tot = {'purchases': 0.0, 'returns': 0.0, 'sc_paid': 0.0, 'n': 0}
    for mth, m in months.items():
        md.append(f"| {mth} | {m['purchases']:,.2f} | {m['sc_paid']:,.2f} | {m['returns']:,.2f} | {m['purchases'] + m['returns']:,.2f} | {m['n']} |")
        for k in tot:
            tot[k] += m[k]
    md.append(f"| **Total** | **{tot['purchases']:,.2f}** | **{tot['sc_paid']:,.2f}** | **{tot['returns']:,.2f}** | **{tot['purchases'] + tot['returns']:,.2f}** | {tot['n']} |")
    sc_only = [x for x in out if x['amount'] > 0 and not x['card'] and x['store_credit']]
    md += ['', '## Paid entirely with store credit (never in the bank feed)', '']
    md += [f"- {x['date']} {x['receipt']} {x['amount']:,.2f} ({x['tenders']}){' - booked ' + x['booked_qbo_ids'] if x['booked_qbo_ids'] else ''}" for x in sc_only] or ['- none']
    row_flags = [f"- {x['date']} {x['receipt']} {x['amount']:,.2f}: {x['flags']}" for x in out if x['flags']]
    md += ['', '## Flags on counted rows', ''] + (row_flags or ['- none'])
    md += ['', '## Not counted here, but look', ''] + ([f'- {f}' for f in flags] or ['- none'])
    with open(os.path.join(a.out, f'audit_{name}.md'), 'w') as fh:
        fh.write('\n'.join(md) + '\n')
    print('\n'.join(md[:len(months) + 6]))
    print(f"wrote {a.out}/audit_{name}.csv and {a.out}/audit_{name}.md ({len(row_flags)} flagged rows, {len(flags)} other notes)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
