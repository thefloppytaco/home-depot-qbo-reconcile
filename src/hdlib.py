"""hdlib.py - the entry engine for the weekly sync. A week's entries file is just data:

    import os, sys; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'src'))
    from hdlib import *
    add('P1', '2026-10-05', '00090-50006', 'H0000-000001', 1234, '123 Example St', 'MC ..1111', 'Sale', 549.94, [
        ('32 in. steel prehung door [sku 826322]', 1, 388.00, 388.00), ..., (T, 1, 31.13, 31.13)], note='printed PO 123 Example St')
    add('J1', ..., 'SC ..2222', 'Return (SC earn)', 73.06, [], groups=[
        (job, cust_or_None, 'ORIG REC 1234 050 46743 09/18/26', '#11538', [(desc, qty, unit, total)], tax), ...])
    finish()

`src/gen_entries.py` writes that file for you from the receipt bundles; you review it and run it.
Run it from the sync folder (sync/YYYY-MM-DD/). finish() refuses to write anything if a single tie fails, then writes
entries.json, line_items_new.csv (qbo_id PENDING-<key>), payloads.json (exact QuickBooks API bodies, NOT posted)
and PROPOSAL.md (what to show the owner for approval).

Item tuples: (description, qty, unit, line_total). Amounts are POSITIVE for sales and returns alike; the type decides the sign.
Use T ('Sales tax') for tax. A negative line is fine for a discount.

type   'Sale'              card purchase                     -> QBO Purchase
       'Return'            refund to a card                  -> QBO Purchase with Credit: true
       'Return (SC earn)'  refund to store credit            -> QBO JournalEntry: Dr store_credit / Cr cogs per group
card   'Amex ..1234', 'Visa ..1234', 'MC ..1234' (last-4 looked up in hd-sync.json "cards"),
       'SC ..1234' = a store-credit/gift card (always the one pooled store_credit account; spends post with PaymentType Cash)
job    canonical job name from hd-sync.json "jobs" (prefix match); 'Tools' -> accounts.tools, no customer;
       '' or 'untagged ...' -> cogs with no customer
groups for returns allocated by ORIG REC: one group per original receipt -> one credit line per group, each on the job
       that original purchase was booked to. Works for card credits ('Return') and store-credit refunds alike.
Overrides on add(): cust='<customer id>', acct='<account id>', pn='<full private note>'.
skip(...) itemizes a transaction you will NOT post (excluded card, declined personal card): it lands in
line_items_new.csv with the reason as its qbo_id, and still counts toward the receipt tie.

Config: hd-sync.json, found via $HD_SYNC_CONFIG, else the current folder or up to 3 parents (copy
templates/hd-sync.example.json). Stdlib only.
"""
import json, csv, os, re, datetime as dt

T = 'Sales tax'
E = []
SYNC = dt.date.today().isoformat()
_CFG = None


def _find_config():
    env = os.environ.get('HD_SYNC_CONFIG')
    if env:
        return env
    d = os.getcwd()
    for _ in range(4):
        p = os.path.join(d, 'hd-sync.json')
        if os.path.exists(p):
            return p
        d = os.path.dirname(d)
    raise SystemExit('hd-sync.json not found. Copy templates/hd-sync.example.json to hd-sync.json at the repo root and fill it in '
                     '(AGENTS.md, "Setting up for a new business"), or set HD_SYNC_CONFIG.')


def cfg():
    global _CFG
    if _CFG is None:
        with open(_find_config()) as f:
            raw = json.load(f)
        _CFG = {k: v for k, v in raw.items() if not k.startswith('_')}
    return _CFG


def reset(config=None):
    """Clear entries (and optionally inject a config dict) - for tests and for running several weeks in one process."""
    global _CFG
    E.clear()
    _CFG = config


def add(key, date, loc, order, store, job, card, typ, amount, items, note='', ret='', groups=None, cust=None, acct=None, pn=None,
        book=True, reason=''):
    if any(e['key'] == key for e in E):
        raise SystemExit(f'duplicate key {key}')
    E.append(dict(key=key, date=date, loc=loc, order=order, store=store, job=job, card=card, type=typ, amount=round(amount, 2),
                  items=items or [], note=note, ret=ret, groups=groups, cust=cust, acct=acct, pn=pn, book=book, reason=reason))


def skip(key, date, loc, order, store, job, card, typ, amount, items, reason, note='', groups=None):
    """Itemize a transaction that is NOT posted. Rows land in line_items_new.csv with qbo_id = reason; no payload."""
    add(key, date, loc, order, store, job, card, typ, amount, items, note=note or reason, book=False, reason=reason, groups=groups)


def is_return(e): return e['type'].lower().startswith('return')
def is_sc(e): return e['card'].upper().startswith('SC')
def is_je(e): return bool(e['groups']) and is_sc(e) and is_return(e)


def _untagged(job): return not job or job.lower().startswith('untagged')
def _is_tools(job): return (job or '').strip().upper() in [t.upper() for t in cfg().get('tools_pos', ['TOOLS'])] or (job or '').lower().startswith('tools')


def cust_of(job, override=None):
    if override:
        return override
    j = (job or '').strip()
    if _untagged(j) or _is_tools(j):
        return None
    best = None
    for name, cid in cfg()['jobs'].items():
        if j.lower().startswith(name.lower()) and (best is None or len(name) > len(best[0])):
            best = (name, cid)
    if not best:
        raise SystemExit(f'No QuickBooks customer for job {job!r}. Look it up in QuickBooks, confirm it with the owner, and add it to '
                         f'hd-sync.json "jobs" (or pass cust=).')
    return best[1]


def last4_of(card):
    m = re.search(r'(\d{4})\s*$', card or '')
    return m.group(1) if m else ''


def acct_of(card):
    if card.upper().startswith('SC'):
        return cfg()['accounts']['store_credit']
    l4 = last4_of(card)
    if l4 in cfg().get('never_book', []):
        raise SystemExit(f'{card} is on the never_book list - itemize it with skip(), do not post it')
    c = cfg()['cards'].get(l4)
    if isinstance(c, dict):
        c = c.get('account')
    if not c:
        raise SystemExit(f'No QuickBooks account for card {card!r}. Ask the owner which account it feeds and add it to hd-sync.json "cards".')
    return c


def line_acct(e, job=None):
    if e['acct']:
        return e['acct']
    return cfg()['accounts']['tools'] if _is_tools(job if job is not None else e['job']) else cfg()['accounts']['cogs']


def _lines(e):
    if e['groups']:
        return [i for g in e['groups'] for i in g[4]]
    return e['items']


def _group_total(g): return round(sum(i[3] for i in g[4]) + g[5], 2)


def ties(new_rows='new_rows.csv', partial=()):
    """Hard checks: qty x unit = line; lines (+ group tax) = entry amount; entries per receipt = receipt total in new_rows.csv."""
    bad = []
    for e in E:
        for d, q, u, t in _lines(e):
            if d != T and abs(round(q * u, 2) - t) > 0.004:
                bad.append((e['key'], 'qty x unit != line', d, q, u, t))
        s = round(sum(_group_total(g) for g in e['groups']), 2) if e['groups'] else round(sum(i[3] for i in e['items']), 2)
        if abs(s - e['amount']) > 0.004:
            bad.append((e['key'], 'lines != amount', s, e['amount']))
        if is_sc(e) and is_return(e) and not e['groups']:
            bad.append((e['key'], 'a store-credit refund needs groups=[...] (one per ORIG REC block) - it posts as a journal entry'))
        if not e.get('book', True):
            continue
        acct_of(e['card'])
        if e['groups']:
            for g in e['groups']:
                if g[0] == 'REVIEW':
                    bad.append((e['key'], 'unresolved group - set its job', g[2]))
                elif not g[1]:
                    cust_of(g[0])
        else:
            cust_of(e['job'], e['cust'])
    if os.path.exists(new_rows):
        tot = {}
        for r in csv.DictReader(open(new_rows)):
            if r.get('tenders') and not r.get('subrow_of'):
                tot[r['receipt']] = abs(float(r['total']))
        by = {}
        for e in E:
            by[e['loc']] = round(by.get(e['loc'], 0) + e['amount'], 2)
        for loc, s in by.items():
            if loc in partial:
                continue
            if loc in tot and abs(tot[loc] - s) > 0.004:
                bad.append((loc, 'entries != receipt total', s, tot[loc], 'pass partial=(loc,) only if a leg is legitimately not booked'))
    return bad


def _desc(d, q, u):
    return d + (f' ({q} @ {u:.2f})' if q != 1 and d != T and f'{q} @' not in d else '')


def _pline(desc, amt, cust, acct):
    det = {'AccountRef': {'value': acct}, 'BillableStatus': 'NotBillable', 'TaxCodeRef': {'value': 'NON'}}
    if cust:
        det['CustomerRef'] = {'value': cust}
    return {'Amount': round(amt, 2), 'DetailType': 'AccountBasedExpenseLineDetail', 'Description': desc[:4000], 'AccountBasedExpenseLineDetail': det}


def _group_cust(g):
    job, cust = g[0], g[1]
    if cust:
        return cust
    if _untagged(job) or job == 'REVIEW':
        return None
    return cust_of(job)


def _items_text(items):
    return ' + '.join(f"{d.split(' (')[0]} {q}@{u:.2f} {t:.2f}" if q != 1 else f"{d.split(' (')[0]} {t:.2f}" for d, q, u, t in items)


def _note(e):
    if e['pn']:
        return e['pn']
    ref = f"rcpt {e['loc']}" + (f" / order {e['order']}" if e['order'] else '')
    jobtxt = (e['job'] or 'untagged').split(' (')[0]
    if is_je(e):
        head = f"HD store-credit refund: return {ref} ({e['date']}), {e['amount']:.2f} to {e['card']}; {len(e['groups'])} group(s) allocated by ORIG REC"
    elif e['groups']:
        head = f"HD return, {e['card']}, {ref}; {len(e['groups'])} group(s) allocated by ORIG REC"
    elif is_sc(e):
        head = f"HD store-credit spend, {e['card']}, {jobtxt} ({ref}) - itemized"
    else:
        head = f"{'HD return' if is_return(e) else 'HD purchase'}, {e['card']}, {jobtxt} ({ref}) - itemized"
    return (head + (f". {e['note']}" if e['note'] else '') + f". Sync {SYNC}")[:4000]


def payloads():
    P = {}
    c = cfg()
    hd, sc, cogs = c['vendor_hd'], c['accounts']['store_credit'], c['accounts']['cogs']
    for e in E:
        if not e.get('book', True):
            continue
        if is_je(e):
            lines = [{'Description': f"Store credit earned on {e['card']}, HD rcpt {e['loc']}", 'Amount': e['amount'], 'DetailType': 'JournalEntryLineDetail',
                      'JournalEntryLineDetail': {'PostingType': 'Debit', 'AccountRef': {'value': sc}}}]
            for g in e['groups']:
                job, cust, label, orig, items, tax = g
                jl = {'PostingType': 'Credit', 'AccountRef': {'value': cogs}}
                gc = _group_cust(g)
                if gc:
                    jl['Entity'] = {'Type': 'Customer', 'EntityRef': {'value': gc}}
                lines.append({'Description': f"{label}{' (' + orig + ')' if orig else ''}: {_items_text(items)} + tax {tax:.2f}"[:4000],
                              'Amount': _group_total(g), 'DetailType': 'JournalEntryLineDetail', 'JournalEntryLineDetail': jl})
            assert abs(sum(l['Amount'] for l in lines[1:]) - e['amount']) < 0.004, e['key']
            P[e['key']] = {'tool': 'create_journal_entry', 'params': {'journalEntry': {'TxnDate': e['date'], 'PrivateNote': _note(e), 'Line': lines}}}
            continue
        if e['groups']:
            plines = []
            for g in e['groups']:
                job, cust, label, orig, items, tax = g
                gc, ga = _group_cust(g), line_acct(e, job)
                tag = f" ({label}{' -> ' + orig if orig else ''})"
                plines += [_pline(_desc(d, q, u) + tag, t, gc, ga) for d, q, u, t in items]
                if tax:
                    plines.append(_pline(f'{T}{tag}', tax, gc, ga))
        else:
            cust, acct = cust_of(e['job'], e['cust']), line_acct(e)
            plines = [_pline(_desc(d, q, u), t, cust, acct) for d, q, u, t in e['items']]
        pu = {'AccountRef': {'value': acct_of(e['card'])}, 'PaymentType': 'Cash' if is_sc(e) else 'CreditCard',
              'EntityRef': {'value': hd, 'type': 'Vendor'}, 'TxnDate': e['date'], 'PrivateNote': _note(e), 'Line': plines}
        if is_return(e):
            pu['Credit'] = True   # line amounts stay positive; Credit makes it a refund in QBO
        assert abs(sum(l['Amount'] for l in pu['Line']) - e['amount']) < 0.004, e['key']
        P[e['key']] = {'tool': 'create_purchase', 'params': {'purchase': pu}}
    return P


def write_items(path='line_items_new.csv'):
    rows = []
    for e in E:
        ret = '' if is_return(e) else (e['ret'] or (dt.date.fromisoformat(e['date']) + dt.timedelta(days=90)).isoformat())
        sign = -1 if is_return(e) else 1
        qid = ('PENDING-' + e['key']) if e.get('book', True) else (e.get('reason') or 'NOT BOOKED')
        if e['groups']:
            for job, cust, label, orig, items, tax in e['groups']:
                for k, (d, q, u, t) in enumerate(items):
                    desc = f"{d} ({label}{' -> ' + orig if orig else ''})" if k == 0 else d
                    rows.append([e['date'], e['loc'], e['order'], e['store'], job, e['card'], 'Return', desc, q, round(-u, 2), round(-t, 2), '', qid])
                rows.append([e['date'], e['loc'], e['order'], e['store'], job, e['card'], 'Return', T, 1, round(-tax, 2), round(-tax, 2), '', qid])
        else:
            for d, q, u, t in e['items']:
                rows.append([e['date'], e['loc'], e['order'], e['store'], e['job'], e['card'], 'Return' if is_return(e) else 'Sale', d, q,
                             round(sign * u, 2), round(sign * t, 2), ret, qid])
    f2 = lambda v: f'{v:.2f}' if isinstance(v, float) else v
    with open(path, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow('date,receipt_locator,order_number,store,job,card,type,item_desc,qty,unit_price,line_total,return_by,qbo_id'.split(','))
        for r in sorted(rows, key=lambda x: (x[0], x[1])):
            w.writerow([f2(v) for v in r])
    return len(rows)


def proposal(P, path='PROPOSAL.md', extra=''):
    c = cfg()
    out = [f'# Home Depot -> QuickBooks proposal, sync {SYNC}', '', 'Nothing below has been posted. Approve, change, or hold each entry.', '',
           '| Key | Date | Type | Paid with -> account | Amount | Job -> customer | Expense acct | Receipt |', '|---|---|---|---|---|---|---|---|']
    for e in E:
        if not e.get('book', True):
            out.append(f"| {e['key']} | {e['date']} | NOT POSTED ({e.get('reason', '')}) | {e['card']} | {e['amount']:,.2f} | {e['job']} | - | {e['loc']} |")
            continue
        if e['groups']:
            kind = 'JE (store-credit refund)' if is_je(e) else 'Card credit'
            dest = c['accounts']['store_credit'] if is_je(e) else acct_of(e['card'])
            jobs = '; '.join(f"{g[0]} ({_group_cust(g) or 'untagged'}) {_group_total(g):.2f}" for g in e['groups'])
            out.append(f"| {e['key']} | {e['date']} | {kind} | {e['card']} -> {dest} | {e['amount']:,.2f} | by ORIG REC: {jobs} | {c['accounts']['cogs']} | {e['loc']} |")
        else:
            out.append(f"| {e['key']} | {e['date']} | {e['type']} | {e['card']} -> {acct_of(e['card'])} | {e['amount']:,.2f} | "
                       f"{(e['job'] or 'untagged').split(' (')[0]} -> {cust_of(e['job'], e['cust']) or '-'} | {line_acct(e)} | "
                       f"{e['loc']}{' / ' + e['order'] if e['order'] else ''} |")
    out += ['', '## Lines', '']
    for e in E:
        out.append(f"**{e['key']}** {e['note'] or ('allocated by ORIG REC' if e['groups'] else '')}")
        for d, q, u, t in _lines(e):
            out.append(f"- {_desc(d, q, u)}: {t:,.2f}")
        if e['groups']:
            out += [f"- tax, {g[2]}: {g[5]:.2f}" for g in e['groups']]
        out.append('')
    if os.path.exists('new_rows.csv'):
        rows = list(csv.DictReader(open('new_rows.csv')))
        n = {}
        for r in rows:
            n[r.get('bucket') or 'BLANK'] = n.get(r.get('bucket') or 'BLANK', 0) + 1
        out += ['## Bucket arithmetic', '', f"{len(rows)} new rows = " + ' + '.join(f'{v} {k}' for k, v in sorted(n.items())) +
                (' -- BLANK BUCKETS: give every row a bucket before presenting' if 'BLANK' in n else ''), '']
    out.append(extra)
    with open(path, 'w') as f:
        f.write('\n'.join(out))


def finish(partial=(), extra=''):
    bad = ties(partial=partial)
    if bad:
        for b in bad:
            print('TIE FAILURE', b)
        raise SystemExit('fix the ties above - nothing written')
    with open('entries.json', 'w') as f:
        json.dump(E, f, indent=1)
    n = write_items()
    P = payloads()
    with open('payloads.json', 'w') as f:
        json.dump(P, f, indent=1)
    proposal(P, extra=extra)
    print(f'OK: {len(E)} entries, {n} item rows -> entries.json, line_items_new.csv, payloads.json, PROPOSAL.md')
    for e in E:
        print(f"  {e['key']:4} {e['date']} {e['type'][:16]:16} {e['card']:12} {e['amount']:>10,.2f}  {(e['job'] or 'untagged')[:30]}"
              + ('' if e.get('book', True) else f"  [NOT POSTED: {e.get('reason')}]"))
    return P
