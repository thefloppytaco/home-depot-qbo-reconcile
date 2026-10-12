#!/usr/bin/env python3
"""Draft the week's QuickBooks entries from receipt bundles.

    python3 src/gen_entries.py --sync sync/2026-10-07 [--bundles FILE] [--only-new]

Reads   <sync>/hd_bundles*.jsonl (from src/fetch_receipts.js: one JSON object per Home Depot transaction, holding Home
        Depot's structured order details plus the printed receipt), hd-sync.json (your config), ledger.csv (from
        src/build_ledger.py, to find the original sale behind every return), and - when they exist - line_items.csv and
        booking_log_*.csv (to find the job each original was BOOKED to).
Writes  <sync>/entries.py (a DRAFT: one add()/skip() call per entry, every judgment call marked '# REVIEW' or '# CHECK')
        and <sync>/gen_report.txt. Resolve the REVIEW lines, then run entries.py (see src/hdlib.py).
--only-new keeps only the receipts listed in <sync>/new_rows.csv (from src/merge_orderhistory.py).

What it decides, and says so in the draft:
  - items at NET price per line from Home Depot's order details (list price and discount noted in the description);
    tax = the receipt's total minus pretax. Net x qty summed must equal the row's pretax, or the receipt is flagged.
  - the job from the PRINTED PO (the Pro Desk PDF's PO for desk orders), through your po_aliases, then by street number +
    first street word against hd-sync.json "jobs".
  - one entry per tender leg. Store credit is applied in receipt order (whole items, then one split line); the card leg
    carries all the tax. Excluded cards become skip() (itemized, not posted).
  - returns: every ORIG REC block on the printed refund receipt -> the original sale in ledger.csv (store + date + txn)
    -> the job that original was BOOKED to. NEVER the return row's own PO: batch-return rows routinely carry another
    job's PO. NO RECEIPT items -> untagged.
What it refuses to decide (REVIEW): excluded/ask-first/unknown cards, a PO matching no job or several, blank/garbage POs,
an original it cannot find, a receipt whose items don't tie, order details answering for a different transaction,
several refund legs on one return, a card charge that differs from the receipt total.
Stdlib only.
"""
import json, csv, glob, os, re, sys, argparse, collections, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hdlib  # noqa: E402

SC_NETS = {'GR', 'CS', 'PERK_GC'}
NETNAME = {'AX': 'Amex', 'VI': 'Visa', 'MA': 'MC', 'DS': 'Disc', 'DB': 'Debit', 'HD': 'HDCard', 'ED': 'Other'}
LEDGER_NET = {'amex': 'AX', 'visa': 'VI', 'mastercard': 'MA', 'mc': 'MA', 'discover': 'DS', 'debit': 'DB', 'hd card': 'HD'}


def norm(s): return re.sub(r'[^A-Z0-9]', '', str(s or '').upper())


def key2(s):
    """'123 EXAMPLE STREET' -> '123 EXAMPLE' (street number + first word): the matching key for jobs."""
    t = re.findall(r'[A-Z0-9]+', str(s or '').upper())
    return ' '.join(t[:2])


def card_label(net, last4): return f"{'SC' if net in SC_NETS else NETNAME.get(net, net)} ..{last4}"


def rowkey(b):
    """The receipt locator new_rows.csv and hdlib use for a bundle: the order-history receipt field, else register-txc,
    else 'order:<order number>' (cancel rows and some online orders carry no receipt)."""
    r = (b.get('row') or {}).get('receipt')
    if r:
        return r
    if b.get('reg') not in (None, '') and b.get('txc') not in (None, ''):
        return f"{str(b['reg']).zfill(5)}-{str(b['txc']).zfill(5)}"
    return f"order:{b.get('on') or ''}"


def rowkey_csv(r): return r.get('receipt') or f"order:{r.get('order') or ''}"


class Gen:
    def __init__(self, config, ledger_rows, line_items, logs):
        self.c = config
        self.jobs = config['jobs']
        self.alias = {norm(k): v for k, v in config.get('po_aliases', {}).items()}
        self.garbage = {norm(x) for x in config.get('garbage_pos', ['0'])}
        self.tools = {norm(x) for x in config.get('tools_pos', ['TOOLS'])}
        self.jobkey = collections.defaultdict(list)
        for j in self.jobs:
            self.jobkey[key2(j)].append(j)
        self.ledger = ledger_rows
        self.led_by = {}
        for r in ledger_rows:
            try:
                self.led_by[(str(int(r['store'])), r['date'], int(r['transaction_id']))] = r
            except (KeyError, ValueError, TypeError):
                pass
        self.li_by = collections.defaultdict(list)
        for r in line_items:
            self.li_by[(r.get('date'), r.get('receipt_locator'))].append(r)
        self.logs = logs

    # ---------- lookups ----------
    def resolve_job(self, po):
        n = norm(po)
        if not n:
            return '', 'REVIEW: blank PO - ask which job'
        if n in self.tools:
            return 'Tools', 'Tools PO -> tools account, no project'
        for ak, target in sorted(self.alias.items(), key=lambda kv: -len(kv[0])):
            if n.startswith(ak):
                return target, f'standing rule: {po!r} -> {target}'
        if n in self.garbage:
            return '', f'REVIEW: PO {po!r} names no job - ask'
        cands = self.jobkey.get(key2(po), [])
        if len(cands) == 1:
            return cands[0], ''
        if len(cands) > 1:
            return '', f'REVIEW: several jobs match {po!r}: {cands} - pick one'
        k = key2(po).split(' ')
        if len(k) == 2:
            loose = [j for j in self.jobs if key2(j).split(' ')[0] == k[0] and key2(j).split(' ')[1:2] and key2(j).split(' ')[1][:4] == k[1][:4]]
            if len(loose) == 1:
                return loose[0], f'spelling: {po!r} read as {loose[0]}'
        return '', (f'REVIEW: no job in hd-sync.json for PO {po!r} - find the customer in QuickBooks, confirm with the owner, '
                    f'add it to "jobs", re-run')

    def card_status(self, net, last4):
        if net in SC_NETS:
            return 'sc'
        if last4 in self.c.get('never_book', []):
            return 'never'
        if last4 in self.c.get('ask_first', []):
            return 'ask'
        return 'ok' if last4 in self.c.get('cards', {}) else 'unknown'

    def find_original(self, ref):
        """ORIG REC [store, reg, txn(5, with check digit), mm/dd/yy] -> (ledger row or None, locator)."""
        store, reg, txn, mdy = ref
        m, d, y = mdy.split('/')
        iso = f'20{y}-{m}-{d}'
        loc = f'{reg.zfill(5)}-{txn.zfill(5)}'
        row = self.led_by.get((str(int(store)), iso, int(txn) // 10)) or self.led_by.get((str(int(store)), iso, int(txn)))
        return row, loc

    def booked_job(self, iso, locs):
        """The job + QBO id an original was BOOKED to: line_items.csv first, then booking logs."""
        for loc in locs:
            rows = [r for r in self.li_by.get((iso, loc), []) if r.get('qbo_id') and not str(r['qbo_id']).startswith(('PENDING', 'NOT', 'HELD', 'ASK'))]
            if rows:
                jobs = {r['job'].split(' (')[0] for r in rows}
                ids = sorted({r['qbo_id'] for r in rows})
                return (jobs.pop() if len(jobs) == 1 else '|'.join(sorted(jobs))), ','.join(ids), 'line_items.csv'
        for loc in locs:
            for r in self.logs:
                if (r.get('receipt_locator') or '').startswith(loc) and r.get('date') == iso:
                    return r.get('job') or '', r.get('qbo_id') or '', 'booking log'
        return '', '', ''

    def excluded_origin(self, ledger_tenders):
        for m in re.finditer(r'([A-Za-z/ ]+?)\.\.(\d{4})=', ledger_tenders or ''):
            if m.group(2) in self.c.get('never_book', []):
                return True
        return False

    # ---------- per bundle ----------
    def items_from_od(self, b, od, flags):
        items, unpriced = [], []
        for it in od.get('items', []):
            if it.get('pretax') is None:
                unpriced.append(it)
                continue
            qty = it.get('qty') or it.get('oq') or 1
            # the per-line discount sign is inconsistent across receipts; net x qty is the reliable figure
            net_total = round(abs(it['net']) * qty, 2) if it.get('net') is not None else round(abs(it['pretax']) - abs(it.get('disc') or 0), 2)
            unit = round(net_total / qty, 2) if qty else net_total
            qty_note = ''
            if abs(unit * qty - net_total) > 0.004:
                qty_note, unit, qty = f" ({qty} @ {it.get('net', '')})", net_total, 1
            desc = it.get('desc') or it.get('pos') or f"SKU {it.get('sku')}"
            if abs(abs(it['pretax']) - net_total) > 0.004:
                desc += f" (list {abs(it['pretax']):.2f}, discount {net_total - abs(it['pretax']):.2f})"
            if it.get('sku'):
                desc += f" [sku {it['sku']}]"
            items.append((desc + qty_note, qty, abs(unit), abs(net_total)))
        gap = round(abs(b['pre']) - round(sum(x[3] for x in items), 2), 2)
        if unpriced:
            if len(unpriced) == 1 and 0 < gap < 25:
                u = unpriced[0]
                items.append((f"{u.get('desc')} (amount = receipt pretax minus priced items) [sku {u.get('sku')}]", 1, gap, gap))
                flags.append(f"CHECK: fee line {u.get('desc')!r} {gap:.2f} derived - confirm on the printed receipt")
                gap = 0.0
            else:
                flags.append(f"REVIEW: {len(unpriced)} unpriced item(s) {[u.get('desc') for u in unpriced]}, gap {gap:.2f}")
        if abs(gap) > 0.004:
            flags.append(f"REVIEW: items (net) {abs(b['pre']) - gap:.2f} != row pretax {abs(b['pre']):.2f} (gap {gap:.2f}) - does not tie")
        return items

    def return_groups(self, b, od, rc, items, tax, po, job):
        groups = []
        blocks = rc.get('blocks') or []
        if blocks:
            for blk in blocks:
                gtax = abs(blk['tax']) if blk.get('tax') is not None else round(sum(abs(i['total']) for i in blk['items']) * 0.06, 2)
                gitems = [(i['desc'], i['qty'], abs(i['unit']), abs(i['total'])) for i in blk['items']]
                for k, gi in enumerate(gitems):   # better names from order details when one item matches by amount
                    m = [x for x in od.get('items', []) if x.get('pretax') is not None and abs(abs(x['pretax']) - gi[3]) < 0.005 and (x.get('qty') or 1) == gi[1]]
                    if len(m) == 1:
                        gitems[k] = (f"{m[0]['desc']} [sku {m[0].get('sku')}]", gi[1], gi[2], gi[3])
                if blk['ref'] is None:
                    groups.append(('untagged (NO RECEIPT)', None, 'ORIG REC: NO RECEIPT', '', gitems, gtax, 'no-receipt return -> cogs, no job'))
                    continue
                orig, oloc = self.find_original(blk['ref'])
                label = f"ORIG REC {' '.join(blk['ref'])}"
                if not orig:
                    groups.append(('REVIEW', None, label, '', gitems, gtax, 'REVIEW: original not in ledger.csv (older than the pull, an unlinked card, '
                                   'or desk-order numbering) - find it (Gmail, QuickBooks by amount + date) or ask'))
                    continue
                if self.excluded_origin(orig.get('tenders')):
                    mode = self.c.get('excluded_origin_refunds', 'ask')
                    if mode == 'untagged':
                        groups.append(('untagged (excluded-card origin)', None, label, '', gitems, gtax, 'original was paid on an excluded card; refunded to you -> untagged (config)'))
                    else:
                        groups.append(('REVIEW', None, label, '', gitems, gtax, f"REVIEW: original was paid on an excluded card ({orig.get('tenders')}) but refunded to "
                                       'your card/store credit - ask the owner where it goes'))
                    continue
                ojob, oid, src = self.booked_job(orig['date'], [oloc, orig.get('receipt_locator', '')])
                if ojob:
                    cj, cf = self.resolve_job(ojob)
                    groups.append((cj or ojob, None, label, f'#{oid}' if oid and not oid.startswith('#') else oid, gitems, gtax,
                                   f"original {orig['date']} {oloc} booked to {ojob} ({src})" + (f'; {cf}' if cf else '')))
                else:
                    cj, cf = self.resolve_job(orig.get('project', ''))
                    groups.append((cj or 'REVIEW', None, label, '', gitems, gtax,
                                   f"{'CHECK' if cj else 'REVIEW'}: original {orig['date']} {oloc} has no booking on file; Home Depot's PO on it is "
                                   f"{orig.get('project')!r}{' -> ' + cj if cj else ''}. Confirm in QuickBooks (amount {abs(float(orig['total'])):.2f}, "
                                   f"date +-3 days) which job its entry is on"))
            return groups
        rel = od.get('related') or ''
        if rel:
            reg, store, iso, txn = rel.split(';')
            orig = self.led_by.get((str(int(store)), iso, int(txn)))
            if orig:
                ojob, oid, src = self.booked_job(orig['date'], [orig.get('receipt_locator', '')])
                cj, cf = self.resolve_job(ojob or orig.get('project', ''))
                groups.append((cj or 'REVIEW', None, f'related txn {rel}', f'#{oid}' if oid else '', items, tax,
                               f"single-origin return of {orig['date']} {orig.get('receipt_locator')} ({src or 'HD PO ' + repr(orig.get('project'))})"))
            else:
                groups.append(('REVIEW', None, f'related txn {rel}', '', items, tax, 'REVIEW: original not in ledger.csv'))
        elif b.get('on'):
            groups.append(('REVIEW', None, f"order {b['on']}", '', items, tax, f"REVIEW: return of order {b['on']} - find that order's sale and the job it was "
                           f"booked to; this return row's PO {po!r} is NOT evidence"))
        else:
            groups.append((job or 'REVIEW', None, 'single receipt, no ORIG REC', '', items, tax, 'single-job return booked off its own printed PO'))
        return groups

    def run(self, bundles):
        out = ['# entries.py - DRAFT written by src/gen_entries.py on ' + dt.date.today().isoformat(),
               "# Resolve every '# REVIEW' line (fix the add() call, change it to skip(), or delete it), glance at '# CHECK' lines, then run:",
               '#     python3 entries.py',
               'import os, sys',
               "sys.path[:0] = [os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'src'), " + repr(HERE) + "]",
               'from hdlib import *', '']
        rep, counters, keys = [], collections.Counter(), collections.Counter()
        self.buckets = {}   # receipt locator -> (bucket, [entry keys]) - written back into new_rows.csv by main()

        def nk(p):
            keys[p] += 1
            k = f'{p}{keys[p]}'
            cur = self.buckets.get(loc_now[0], ('', []))
            self.buckets[loc_now[0]] = (cur[0], cur[1] + [k])
            return k

        def bucket(name):
            cur = self.buckets.get(loc_now[0], ('', []))
            self.buckets[loc_now[0]] = (name, cur[1])

        loc_now = ['']

        def q(s): return repr(str(s))
        def it(d, qy, u, t): return f'({q(d)}, {qy}, {u:.2f}, {t:.2f})'

        for b in sorted(bundles, key=lambda x: (x['d'], x.get('i', 0))):
            loc = rowkey(b)
            loc_now[0] = loc
            flags = []
            tn = b.get('tn') or []
            if b.get('err'):
                flags.append(f"REVIEW: the fetch failed for this row ({b['err']}) - re-run src/fetch_receipts.js for its date")
            if str(b['t']).lower().startswith('cancel'):
                bucket('cancel')
                counters['cancel'] += 1
                rep.append(f"{b['d']} {loc} {b['tot']}: CANCEL row - the credit is booked from the order's charge history (ai/skills/cancellation-sweep)")
                out += [f"# {b['d']} {loc} {b['tot']} order {b.get('on')!r}: Cancel row (Home Depot zeroes these). The refund shows in the order's charge history;",
                        "#   book it as a card credit to the order's job per ai/skills/cancellation-sweep.SKILL.md - bucket 'cancel', no entry here", '']
                continue
            if not tn:   # new_rows.csv marks these via subrow_of; no bucket recorded here (it would overwrite the aggregate's)
                counters['sub-row'] += 1
                rep.append(f"{b['d']} {loc} {b['tot']}: sub-row (no tenders) - its items live inside the aggregate return's entry")
                out += [f"# {b['d']} {loc} {b['tot']} PO {b.get('po_row')!r}: sub-row of a batch return (no tenders) - bucket 'sub-row', no entry", '']
                continue
            od, rc, big = b.get('od') or {}, b.get('rc') or {}, b.get('big') or {}
            is_ret = str(b['t']).lower().startswith('return')
            if not od:
                flags.append(f"REVIEW: no order details ({b.get('od_err')}) - items come from the receipt text only")
            elif od.get('head') and [str(x).lstrip('0') for x in (od['head'][0], od['head'][2], str(od['head'][3])[:10])] != \
                    [str(x).lstrip('0') for x in (b['s'], b['txc'], b['d'])]:
                flags.append(f"REVIEW: order details answered for a DIFFERENT transaction {od['head']} (a related sale/return) - items below come from the printed receipt; verify")
                od = {}
            if not rc and not big:
                flags.append(f"REVIEW: no printed receipt ({b.get('rc_err')}) - today's receipts can lag hours; HOLD until it appears")
            printed = (rc.get('printedPO') or '').strip() or (big.get('po') or '').strip()
            online = (od.get('po_online') or b.get('po_row') or '').strip()
            po = printed or online
            po_src = 'printed' if printed else 'online'
            if printed and online and norm(printed) != norm(online) and key2(printed) != key2(online):
                flags.append(f"CHECK: printed PO {printed!r} vs online PO {online!r} - the printed one wins; ask if they name two real jobs")
            items = self.items_from_od(b, od, flags) if od.get('items') else [
                (i['desc'], i['qty'], abs(i['unit']), abs(i['total'])) for blk in rc.get('blocks', []) for i in blk['items']]
            tax = round(abs(b['tot']) - abs(b['pre']), 2)
            job, jflag = self.resolve_job(po)
            if jflag and not is_ret:   # a return's job comes from its ORIG REC originals, not its own PO
                flags.append(jflag + ('' if po_src == 'printed' else ' [online PO; no printed PO available]'))
            legs = [(t[0], t[1], round(float(t[2]), 2)) for t in tn if t[2] not in ('', 'None', None)]
            charge = [l for l in legs if (l[2] < 0) != is_ret]   # negative = charge on a sale; positive = refund on a return
            echo = [l for l in legs if l not in charge]
            if echo:
                flags.append(f"CHECK: echo legs ignored {echo} - a refund leg on a sale row is a later return of this order; it books from its own return row")
            if od and od.get('grand') is not None and not is_ret and abs(abs(od['grand']) - abs(b['tot'])) > 0.005:
                flags.append(f"CHECK: Home Depot's order total is now {od['grand']:.2f} vs {abs(b['tot']):.2f} charged - a later adjustment, cancellation or "
                             f"return; expect a separate card credit")
            out.append(f"# {b['d']} {b['t']} rcpt {loc}{' / ' + b['on'] if b.get('on') else ''}  total {b['tot']}  PO printed={printed!r} online={online!r}")
            shown = set()

            def emit():
                for f in flags:
                    if f not in shown:
                        out.append(f'#   {f}')
                        shown.add(f)
            emit()
            if not items:
                bucket('held')
                counters['held'] += 1
                out += ['#   REVIEW: NO ITEMS - itemize by hand from the printed receipt, or HOLD if it has not appeared yet', '']
                rep.append(f"{b['d']} {loc} {b['tot']}: HELD (no items)")
                continue

            if is_ret:
                groups = self.return_groups(b, od, rc, items, tax, po, job)
                gl = [f"    ({q(g[0])}, None, {q(g[2])}, {q(g[3])}, [{', '.join(it(*i) for i in g[4])}], {g[5]:.2f}),  # {g[6]}" for g in groups]
                if len(charge) > 1:
                    out.append(f'#   REVIEW: {len(charge)} refund legs {charge} - give each leg its own entry with the groups bought on that card '
                               '(each group goes back to the tender it was bought with)')
                for net, last4, lamt in charge:
                    st = self.card_status(net, last4)
                    lab = card_label(net, last4)
                    total_groups = round(sum(sum(i[3] for i in g[4]) + g[5] for g in groups), 2)
                    if len(charge) == 1 and abs(total_groups - abs(lamt)) > 0.004:
                        out.append(f'#   REVIEW: groups sum {total_groups:.2f} != refund {abs(lamt):.2f}')
                    if any(g[0] == 'REVIEW' for g in groups):
                        out.append("#   REVIEW: a group has no job yet - replace its 'REVIEW' with the job (or 'untagged ...')")
                    if st == 'never':
                        out.append(f'#   REVIEW: refund to excluded card {lab} - tell the owner; itemized only')
                        out.append(f"skip({q(nk('X'))}, {q(b['d'])}, {q(loc)}, {q(b.get('on', ''))}, {b['s']}, 'return by ORIG REC', {q(lab)}, 'Return', {abs(lamt):.2f}, [], "
                                   f"reason={q(lab + ' is on the never_book list')}, groups=[")
                        out += gl + ['])']
                        bucket('not-posted')
                        counters['not-posted'] += 1
                        continue
                    if st in ('ask', 'unknown'):
                        out.append(f"#   REVIEW: card {lab} is {'ask-first' if st == 'ask' else 'not in hd-sync.json cards'} - ask the owner")
                    typ = 'Return (SC earn)' if st == 'sc' else 'Return'
                    note = ''
                    if st == 'sc':
                        bal = next((x for x in rc.get('balances', []) if x['last4'] == last4), None)
                        note = f"printed CARD BALANCE after: {bal['balance']:.2f}" if bal else "no printed card balance - check the card chain"
                    out.append(f"add({q(nk('J' if st == 'sc' else 'C'))}, {q(b['d'])}, {q(loc)}, {q(b.get('on', ''))}, {b['s']}, 'return by ORIG REC', {q(lab)}, {q(typ)}, "
                               f"{abs(lamt):.2f}, [], note={q(note)}, groups=[")
                    out += gl + ['])']
                    bucket('proposed')
                    counters['proposed'] += 1
                out.append('')
                rep.append(f"{b['d']} {loc} {b['tot']}: RETURN, {len(groups)} group(s), legs {charge}")
                continue

            if not charge:
                bucket('held')
                counters['held'] += 1
                out += [f'#   REVIEW: no charge leg on this sale (tenders {legs}) - look at the receipt and decide by hand', '']
                rep.append(f"{b['d']} {loc} {b['tot']}: HELD (no charge leg)")
                continue
            sc_legs = [l for l in charge if l[0] in SC_NETS]
            card_legs = [l for l in charge if l[0] not in SC_NETS]
            if len(card_legs) > 1:
                flags.append(f'REVIEW: {len(card_legs)} card legs {card_legs} - split the items between them by the printed receipt (each leg is its own bank line)')
            remaining, sc_entries = list(items), []
            for net, last4, lamt in sc_legs:
                rem, chosen, rest = round(abs(lamt), 2), [], []
                for x in remaining:   # Home Depot applies store credit in receipt order
                    if rem <= 0.004:
                        rest.append(x)
                    elif x[3] <= rem + 0.004:
                        chosen.append(x)
                        rem = round(rem - x[3], 2)
                    else:
                        base = x[0].split(' [sku')[0]
                        chosen.append((f'{base} (store-credit share of {x[3]:.2f}; card share {x[3] - rem:.2f})', 1, rem, rem))
                        rest.append((f'{base} (card share of {x[3]:.2f}; store-credit share {rem:.2f})', 1, round(x[3] - rem, 2), round(x[3] - rem, 2)))
                        rem = 0.0
                if rem > 0.004:
                    flags.append(f'REVIEW: store-credit leg {abs(lamt):.2f} is more than the items ({rem:.2f} left) - allocate by hand')
                remaining = rest
                bal = next((x for x in rc.get('balances', []) if x['last4'] == last4), None)
                sc_entries.append((net, last4, round(abs(lamt), 2), chosen, bal))
            emit()
            jobarg = job or f'REVIEW-{po}'
            rental = re.search(r'rental|customer agreement|e-?dep', ' '.join(i[0] for i in items), re.I) and self.c['accounts'].get('rental')
            for net, last4, lamt in card_legs:
                st = self.card_status(net, last4)
                lab = card_label(net, last4)
                lines = remaining + ([('Sales tax', 1, tax, tax)] if tax else [])
                if st == 'never':
                    out.append(f'#   NOT POSTED: {lab} is on the never_book list - itemized only')
                    out.append(f"skip({q(nk('X'))}, {q(b['d'])}, {q(loc)}, {q(b.get('on', ''))}, {b['s']}, {q(job or po)}, {q(lab)}, 'Sale', {abs(lamt):.2f}, "
                               f"[{', '.join(it(*i) for i in lines)}], reason={q(lab + ' is on the never_book list')})")
                    bucket('not-posted')
                    counters['not-posted'] += 1
                    continue
                if st in ('ask', 'unknown'):
                    out.append(f"#   REVIEW: card {lab} is {'ask-first' if st == 'ask' else 'not in hd-sync.json cards'} - ask the owner; use skip(...) if it is not posted")
                chk = round(sum(i[3] for i in lines), 2)
                if abs(chk - abs(lamt)) > 0.004:
                    out.append(f'#   REVIEW: lines {chk:.2f} != card charge {abs(lamt):.2f}. Book what the card was charged: the gap is usually delivery or a '
                               'later adjustment - check the order details and add a line for it')
                note = f'{po_src} PO {po!r}'
                if sc_entries:
                    note += f"; split tender: card {abs(lamt):.2f} + store credit {', '.join(f'..{s[1]} {s[2]:.2f}' for s in sc_entries)}; card leg carries the tax"
                if od.get('disc'):
                    note += f"; Home Depot discount {od['disc']:.2f} applied per line (net booked)"
                extra = f", acct={q(self.c['accounts']['rental'])}" if rental else ''
                if rental:
                    out.append('#   CHECK: tool rental detected -> rental account')
                out.append(f"add({q(nk('P'))}, {q(b['d'])}, {q(loc)}, {q(b.get('on', ''))}, {b['s']}, {q(jobarg)}, {q(lab)}, 'Sale', {abs(lamt):.2f}, [")
                out += [f'    {it(*i)},' for i in lines]
                out.append(f'], note={q(note)}{extra})')
                bucket('proposed')
                counters['proposed'] += 1
            for net, last4, target, chosen, bal in sc_entries:
                lab = card_label(net, last4)
                note = f"store-credit spend {target:.2f}" + (f"; printed CARD BALANCE after: {bal['balance']:.2f}" if bal else '; no printed balance - check the card chain')
                out.append(f"add({q(nk('S'))}, {q(b['d'])}, {q(loc)}, {q(b.get('on', ''))}, {b['s']}, {q(jobarg)}, {q(lab)}, 'Sale', {target:.2f}, [")
                out += [f'    {it(*i)},' for i in chosen]
                out.append(f'], note={q(note)})')
                bucket('proposed')
                counters['proposed'] += 1
            out.append('')
            rep.append(f"{b['d']} {loc} {b['tot']}: SALE job={job or 'REVIEW'} legs={charge} items={len(items)}")

        out.append("finish()   # finish(partial=('<receipt>',)) only for a receipt where a leg is deliberately not posted")
        rep.append('')
        rep.append(f'bundles {len(bundles)}: ' + ', '.join(f'{k} {v}' for k, v in sorted(counters.items())))
        body = out[7:]   # skip the header, which mentions both words
        nrev = sum(1 for l in body if re.search(r'#\s+REVIEW', l))
        nchk = sum(1 for l in body if re.search(r'#\s+CHECK', l))
        rep.append(f"REVIEW lines: {nrev}   CHECK lines: {nchk}")
        return out, rep


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


def read_bundles(path):
    """hd_bundles_*.jsonl from src/fetch_receipts.js (one JSON object per line; '@@B'-prefixed lines also accepted).
    The {"_meta": ...} line is skipped. A bundle whose fetch failed still carries its order-history row; its fields
    are filled in from that row so it shows up in the draft (flagged) instead of vanishing."""
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith('@@B'):
                line = line[3:]
            b = json.loads(line)
            if '_meta' in b:
                continue
            r = b.get('row') or {}
            if r and 'd' not in b:
                b.update(d=r.get('date'), t=r.get('type'), s=r.get('store'), reg=r.get('register'), txc=r.get('txc'), tx=r.get('tx'),
                         po_row=r.get('job', ''), tot=r.get('total'), pre=r.get('pretax'), on=r.get('order', ''),
                         tn=[[t.get('net'), t.get('last4'), t.get('amt')] for t in r.get('tenders', [])])
            out.append(b)
    return out


def write_buckets(path, buckets):
    """Fill new_rows.csv's blank bucket / entry_key cells from the draft. Never overwrites a cell you already filled."""
    if not os.path.exists(path):
        return 0
    with open(path, newline='') as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return 0
    cols = list(rows[0].keys())
    n = 0
    for r in rows:
        bk = buckets.get(rowkey_csv(r))
        if r.get('subrow_of') and not r.get('bucket'):
            r['bucket'] = 'sub-row'
            n += 1
        elif bk:
            if not r.get('bucket') and bk[0]:
                r['bucket'] = bk[0]
                n += 1
            if not r.get('entry_key') and bk[1]:
                r['entry_key'] = ' '.join(bk[1])
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--sync', required=True, help='the session folder, e.g. sync/2026-10-07')
    ap.add_argument('--bundles', help='default: newest <sync>/hd_bundles*.jsonl')
    ap.add_argument('--ledger', default='ledger.csv')
    ap.add_argument('--line-items', default='line_items.csv')
    ap.add_argument('--only-new', action='store_true')
    a = ap.parse_args(argv)
    S = a.sync.rstrip('/')
    bf = a.bundles or (sorted(glob.glob(os.path.join(S, 'hd_bundles*.jsonl'))) or [None])[-1]
    if not bf or not os.path.exists(bf):
        sys.exit(f'no bundles file in {S} - run src/fetch_receipts.js and save its output there as hd_bundles_<date>.jsonl')
    bundles = read_bundles(bf)
    if a.only_new and os.path.exists(os.path.join(S, 'new_rows.csv')):
        keep = {(r['date'], rowkey_csv(r)) for r in read_csv(os.path.join(S, 'new_rows.csv'))}
        bundles = [b for b in bundles if (b['d'], rowkey(b)) in keep]
    logs = []
    for f in glob.glob('booking_log_*.csv'):
        with open(f, newline='') as fh:
            logs += list(csv.DictReader(l for l in fh if not l.startswith('#')))
    g = Gen(hdlib.cfg(), read_csv(a.ledger), read_csv(a.line_items), logs)
    out, rep = g.run(bundles)
    with open(os.path.join(S, 'entries.py'), 'w') as f:
        f.write('\n'.join(out) + '\n')
    with open(os.path.join(S, 'gen_report.txt'), 'w') as f:
        f.write('\n'.join(rep) + '\n')
    nb = write_buckets(os.path.join(S, 'new_rows.csv'), g.buckets)
    print('\n'.join(rep))
    if nb:
        print(f'filled {nb} bucket cell(s) in {S}/new_rows.csv')
    print(f'wrote {S}/entries.py - resolve the REVIEW lines, then: cd {S} && python3 entries.py')


if __name__ == '__main__':
    main()
