/*
 * fetch_receipts.js - the weekly fetch: every Home Depot transaction in a date range, with item detail and the
 * printed receipt. Run it on a signed-in homedepot.com tab (your Purchase History page), either by pasting it into
 * the DevTools Console yourself or by letting an AI browser tool (e.g. Claude in Chrome) run it for you.
 *
 * For each transaction it collects one "bundle":
 *   row  the order-history row (date, type, store, register, transaction, PO/job, totals, tenders) - the same shape
 *        src/pull_orderhistory.js writes, so src/merge_orderhistory.py can add new rows to hd_orderhistory_full.json
 *   od   Home Depot's structured order details: every line with SKU, description, qty, list and net unit price,
 *        per-line tax, the online PO, and on single-origin returns the original transaction
 *   rc   the printed register receipt, parsed: the PO typed at the register, store-credit CARD BALANCE lines, and the
 *        "ORIG REC" blocks that say which original purchase each returned item came from
 *   big  for Pro Desk / online orders: the PO printed on the order PDF
 * Output: hd_bundles_<to-date>.jsonl, one JSON object per line (the first line is {"_meta": ...}).
 * Next: python3 src/merge_orderhistory.py, then python3 src/gen_entries.py (docs/09-weekly-sync.md).
 *
 * SETTINGS - set window.HD_FETCH before running (an AI agent does this), or edit DEFAULTS below:
 *   window.HD_FETCH = {from: '2026-09-01', to: '', job: '', details: true, output: 'download'}
 *     from     first sales date, YYYY-MM-DD (default: 45 days ago)
 *     to       last sales date (default: today)
 *     job      only rows Home Depot's own search matches - job/PO text or an order number. For a job audit
 *              (src/audit_job.py), with from far back, e.g. '2018-01-01'. A job-filtered file is NOT merged.
 *     details  false = rows only, much faster (no items, no printed receipts)
 *     output   'download' saves the file. 'page' keeps it in the tab for an AI to read: poll window.__hdf.status
 *              until it says done, then read window.__hdf.chunk(0), chunk(1), ... (each chunk is also put on the
 *              page as plain text, for tools that read page text) and save them, in order, as one file.
 *
 * IDS: the script needs your Home Depot user id and customer account id. It reuses them if pull_orderhistory.js or
 * an earlier run already found them in this tab; otherwise it says "WAITING FOR IDS" - click to page 2 of your
 * Purchase History (or change its date filter) once, and it captures them from the page's own request.
 *
 * PRIVACY: nothing leaves your browser until you save the file. Home Depot's order details include your FULL card
 * number in one field; this script never keeps that field, and as a backstop redacts any 13-19 digit number before
 * output. It only reads - it never changes anything on your account.
 */
(() => {
  const DEFAULTS = {from: '', to: '', job: '', details: true, output: 'download', userId: '', customerAccountId: ''};
  const SET = Object.assign({}, DEFAULTS, window.HD_FETCH || {});
  const TAG = '[fetch_receipts]';
  const log = (...a) => console.log(TAG, ...a);
  const ROOT = '/oms/customer/order/v1/user';
  const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const S = x => String(x ?? '').replace(/[?&=]/g, ' ');   // AI browser tools drop text that looks like URL query data
  const pad5 = x => String(x ?? '').padStart(5, '0');
  const isoOf = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const shift = (s, months, days) => {
    const [y, m, d] = s.split('-').map(Number), t = new Date(Date.UTC(y, m - 1, d));
    t.setUTCMonth(t.getUTCMonth() + months); t.setUTCDate(t.getUTCDate() + days);
    return t.toISOString().slice(0, 10);
  };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const TO = SET.to || isoOf(new Date());
  const FROM = SET.from || shift(isoOf(new Date()), 0, -45);
  const CHUNK = 25000;

  const st = window.__hdf = {
    status: 'starting', settings: {from: FROM, to: TO, job: SET.job || '', details: !!SET.details, output: SET.output},
    rows: 0, bundles: 0, errors: [], text: '', chunks: [],
    chunk(k) {
      const c = this.chunks[k] ?? '';
      let pre = document.getElementById('hd-dump');
      if (!pre) { document.body.innerHTML = '<article><pre id="hd-dump"></pre></article>'; pre = document.getElementById('hd-dump'); }
      pre.textContent = c || '(no such chunk)';
      return c;
    },
  };

  // ---------------------------------------------------------------- ids + headers
  const BASE_HEADERS = {
    'Accept': 'application/json, text/plain, */*', 'Content-Type': 'application/json', 'channelId': '1',
    'Client': 'ocm_pd_experience_customer-account-orders-purchases', 'channel': 'desktop', 'X-Client-App': 'PHX-Desktop',
  };
  const remember = ids => {
    window.__hd_ids = {userId: ids.userId, customerAccountId: ids.customerAccountId};
    try { sessionStorage.setItem('hdqbo_ids', JSON.stringify(window.__hd_ids)); } catch (e) { /* private mode */ }
  };
  function getIds() {
    if (SET.userId && SET.customerAccountId) return Promise.resolve({userId: SET.userId, customerAccountId: SET.customerAccountId});
    const w = window.__hd_ids;
    if (w && w.userId && w.customerAccountId) return Promise.resolve(w);
    try {
      const s = JSON.parse(sessionStorage.getItem('hdqbo_ids') || 'null');
      if (s && s.userId && s.customerAccountId) return Promise.resolve(s);
    } catch (e) { /* ignore */ }
    return new Promise(resolve => {
      const of = window.fetch, oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send, oh = XMLHttpRequest.prototype.setRequestHeader;
      const got = {userId: null, customerAccountId: null, headers: null};
      const isOH = u => typeof u === 'string' && /\/orderhistory(\?|$)/i.test(u);
      const seen = (url, body, headers) => {
        if (!isOH(url)) return;
        const m = url.match(/\/user\/([^/]+)\/orderhistory/i); if (m) got.userId = decodeURIComponent(m[1]);
        try { const b = JSON.parse(body || 'null'); const c = b && ((b.orderHistoryRequest || {}).customerAccountId || b.customerAccountId); if (c) got.customerAccountId = c; } catch (e) { /* ignore */ }
        if (headers && typeof headers === 'object') {
          const h = {}; try { if (headers instanceof Headers) headers.forEach((v, k) => { h[k] = v; }); else Object.assign(h, headers); } catch (e) { /* ignore */ }
          if (Object.keys(h).length) got.headers = h;
        }
        if (got.userId && got.customerAccountId) {
          window.fetch = of; XMLHttpRequest.prototype.open = oo; XMLHttpRequest.prototype.send = os; XMLHttpRequest.prototype.setRequestHeader = oh;
          log('ids captured'); resolve(got);
        }
      };
      window.fetch = function (input, init) {
        try {
          const url = typeof input === 'string' ? input : input && input.url;
          if (isOH(url)) {
            const h = (init && init.headers) || (input && input.headers) || null;
            if (init && typeof init.body === 'string') seen(url, init.body, h);
            else if (input && input.clone) input.clone().text().then(t => seen(url, t, h)).catch(() => {});
          }
        } catch (e) { /* never break the page */ }
        return of.apply(this, arguments);
      };
      XMLHttpRequest.prototype.open = function (m, url, ...r) { this.__hdu = url; this.__hdh = {}; return oo.call(this, m, url, ...r); };
      XMLHttpRequest.prototype.setRequestHeader = function (k, v) { try { (this.__hdh = this.__hdh || {})[k] = v; } catch (e) { /* ignore */ } return oh.apply(this, arguments); };
      XMLHttpRequest.prototype.send = function (body) { try { seen(this.__hdu, typeof body === 'string' ? body : null, this.__hdh); } catch (e) { /* ignore */ } return os.call(this, body); };
      st.status = 'WAITING FOR IDS - click to page 2 of your Purchase History (or change its date filter) once';
      log(st.status);
    });
  }

  // ---------------------------------------------------------------- order history
  const dateOf = raw => {
    const s = String(raw || ''), a = s.match(/^(\d{4})-(\d{2})-(\d{2})/), b = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})/);
    return a ? `${a[1]}-${a[2]}-${a[3]}` : b ? `${b[3]}-${b[1].padStart(2, '0')}-${b[2].padStart(2, '0')}` : s.slice(0, 10);
  };
  const ordersIn = j => j.orders || j.orderHistory || (j.data && j.data.orders) ||
    Object.values(j || {}).find(v => Array.isArray(v) && (!v.length || typeof v[0] === 'object')) || [];
  // Always ask for Home Depot's own two-year window and stop paging once rows predate FROM: a narrow date range has
  // come back as an "empty success" (orderCount 0) at times, which must never be read as "no new transactions".
  function windows(from, to) {
    const out = []; let end = to;
    while (end >= from) { const start = shift(end, -24, 1); out.push([start, end]); end = shift(start, 0, -1); }
    return out;
  }
  async function pullWindow(ids, H, start, end) {
    let size = 500, page = 1, total = null; const got = [];
    while (page <= 200) {
      const body = {orderHistoryRequest: {pageSize: size, pageNumber: page, startDate: start, endDate: end, customerAccountId: ids.customerAccountId,
        sortBy: 'salesDate', sortOrder: 'desc', searchType: 'ORDERS', resultsFilter: 'allOrders', timezone: TZ, searchValue: SET.job || ''}};
      const r = await fetch(`${ROOT}/${encodeURIComponent(ids.userId)}/orderhistory`, {method: 'POST', headers: H, credentials: 'include', body: JSON.stringify(body)});
      if (r.status === 401 || r.status === 403) throw new Error('not signed in - sign in on homedepot.com in this tab, open Purchase History, run again');
      const txt = await r.text();
      if (!r.ok) {
        if (page === 1 && r.status === 400) { log(`window ${start}..${end}: HTTP 400 - older than your available history; skipped`); return got; }
        throw new Error(`orderhistory HTTP ${r.status}: ${S(txt.slice(0, 200))}`);
      }
      const j = JSON.parse(txt), orders = ordersIn(j);
      if (total === null && typeof j.orderCount === 'number') total = j.orderCount;
      if (!orders.length) {
        if (page === 1 && size === 500 && total !== 0) { size = 60; log('page size 500 came back empty - retrying at 60'); continue; }
        break;
      }
      got.push(...orders);
      if (total !== null && got.length >= total) break;
      if (dateOf(orders[orders.length - 1].salesDate) < FROM) break;   // sorted newest first: the rest are older than FROM
      page += 1; await sleep(300);
    }
    return got;
  }
  const rowOf = o => ({
    date: dateOf(o.salesDate), type: o.transactionType, origin: S(o.orderOrigin || ''), store: o.storeNumber, job: S(o.POJobName || ''),
    total: o.totalAmount, pretax: o.preTaxAmount, tx: o.transactionId,
    receipt: S(o.receiptDetails || `${pad5(o.registerNumber)}-${pad5(o.transactionIdWithCheckDigit)}`),
    invoices: (o.invoiceNumbers || []).map(S),
    tenders: (o.tenders || []).map(t => ({net: t.net ?? t.type ?? '?', last4: String(t.value ?? t.last4 ?? ''), amt: t.amount})),
    register: o.registerNumber, txc: o.transactionIdWithCheckDigit, order: S(o.orderNumber || ''),
  });

  // ---------------------------------------------------------------- printed receipt
  const num = s => +String(s).replace(/,/g, '');
  function parseReceipt(txt) {
    const re = /\*\s*ORIG REC:?\s*(?:(\d{4})\s+(\d{3})\s+(\d{5})\s+(\d\d\/\d\d\/\d\d)|NO RECEIPT)[^*\n]*\*/g, at = []; let m;
    while ((m = re.exec(txt))) at.push({ref: m[1] ? [m[1], m[2], m[3], m[4]] : null, s: m.index, e: re.lastIndex});
    const sub = txt.search(/\bSUBTOTAL\b/);
    const blocks = at.map((b, k) => {
      const stop = k + 1 < at.length ? at[k + 1].s : (sub > b.e ? sub : txt.length), body = txt.slice(b.e, stop);
      const tax = (body.match(/SALES TAX\s+(-?[\d,]+\.\d\d)/) || [])[1];
      const items = [...body.matchAll(/(\d{4}-\d{3}-\d{3})\s+([^\n]*?)\s+(?:(\d+)@(-?[\d,]+\.\d\d)(?:\/\d+)?\s+)?(-?[\d,]+\.\d\d)\s*$/gm)]
        .map(x => ({upc: x[1], desc: S(x[2]).trim(), qty: x[3] ? +x[3] : 1, unit: x[4] ? num(x[4]) : num(x[5]), total: num(x[5])}));
      return {ref: b.ref, tax: tax ? num(tax) : null, items};
    });
    const po = ((txt.match(/PO\/JOB NAME:\s*([^\n]*)/) || [])[1] || '').trim();
    const balances = [...txt.matchAll(/X+(\d{4})\s+STORE CREDIT\s+(-?[\d,]+\.\d\d)\s*\n\s*CARD BALANCE\s+([\d,]+\.\d\d)/g)]
      .map(x => ({last4: x[1], amt: num(x[2]), balance: num(x[3])}));
    const head = (txt.match(/\b(\d{4})\s+(\d{5})\s+(\d{5})\s+(\d\d\/\d\d\/\d\d)\s+(\d\d:\d\d [AP]M)/) || []).slice(1, 6);
    return {printedPO: S(po), head, balances, blocks, hasNoReceipt: /ORIG REC:?\s*NO RECEIPT/.test(txt)};
  }
  async function pdfText(buf) {   // text runs out of the Pro Desk order PDF (deflate streams)
    const u8 = new Uint8Array(buf), s = new TextDecoder('latin1').decode(u8), out = []; let p = 0;
    for (;;) {
      const m = s.indexOf('stream', p); if (m < 0) break;
      if (s.slice(m - 3, m) === 'end') { p = m + 6; continue; }
      let a = m + 6; if (s[a] === '\r') a++; if (s[a] === '\n') a++;
      const e = s.indexOf('endstream', a); if (e < 0) break;
      let b = e; while (b > a && (s[b - 1] === '\n' || s[b - 1] === '\r')) b--; p = e + 9;
      try {
        const ds = new DecompressionStream('deflate'), w = ds.writable.getWriter();
        w.write(u8.slice(a, b)).catch(() => {}); w.close().catch(() => {});
        const rd = ds.readable.getReader(), parts = []; let n = 0;
        for (;;) { const {value, done} = await rd.read(); if (done) break; parts.push(value); n += value.length; }
        const all = new Uint8Array(n); let o = 0; for (const x of parts) { all.set(x, o); o += x.length; }
        const t = new TextDecoder('latin1').decode(all); let mm;
        const r1 = /\(((?:\\.|[^\\)])*)\)\s*Tj/g; while ((mm = r1.exec(t))) out.push(mm[1].replace(/\\([()\\])/g, '$1'));
        const r2 = /\[((?:[^\]])*)\]\s*TJ/g; while ((mm = r2.exec(t))) out.push((mm[1].match(/\(((?:\\.|[^\\)])*)\)/g) || []).map(x => x.slice(1, -1)).join(''));
      } catch (err) { /* not a deflate stream */ }
    }
    return out.join('\n');
  }

  // ---------------------------------------------------------------- one bundle
  async function bundle(ids, H, o, i, rcCache) {
    const r = rowOf(o);
    const B = {i, d: r.date, t: r.type, s: r.store, reg: r.register, txc: r.txc, tx: r.tx, po_row: r.job, tot: r.total, pre: r.pretax,
      on: r.order, ons: (o.orderNumbers || []).map(S), nick: S(o.nickName || ''), inv: r.invoices,
      tn: (o.tenders || []).map(t => [t.type ?? t.net, String(t.value ?? t.last4 ?? ''), String(t.amount)]), row: r};
    if (!SET.details) return B;
    let od = null;
    try {
      const body = {orderDetailsRequest: {searchType: 'instore', salesDate: o.salesDate, storeNumber: o.storeNumber, registerNumber: o.registerNumber,
        transactionId: o.transactionId, orderNumber: o.orderNumber, customerAccountId: ids.customerAccountId,
        groupAndSortBy: {groupBy: 'fulfillmentType', filterResponse: true}, requestOriginatedFrom: 'orderDetails', timezone: TZ}};
      const res = await fetch(`${ROOT}/${encodeURIComponent(ids.userId)}/orderdetails`, {method: 'POST', headers: H, credentials: 'include', body: JSON.stringify(body)});
      if (res.status !== 200) B.od_err = 'HTTP ' + res.status;
      else {
        od = JSON.parse(await res.text());
        B.od = {
          grand: +od.grandTotalAmount, sub: +od.subTotalAmount, tax: +od.taxTotalAmount, disc: +(od.otherDiscountAmount || 0), rew: +(od.rewardsDiscountAmount || 0),
          po_online: S(od.POJobName || ''), added: S(od.recptAddedDate || ''),
          // accountIdValue holds the FULL CARD NUMBER unless its type is related_transaction_key - only that case is kept
          related: od.accountIdType === 'related_transaction_key' ? S(od.accountIdValue) : '',
          head: [od.storeNumber, od.registerNumber, od.transactionIdWithCheckDigit, dateOf(od.salesDate)],
          tn: (od.tenders || []).map(t => [t.type, String(t.value), String(t.amount)]),
          retTot: od.returnTotal ?? null, deliv: od.deliveryCharge ?? null,
          ct: (od.chargeTransactions || []).map(c => ({code: S(c.paymentMethodCode || ''), kind: c.creditAmount ? 'credit' : 'debit',
            amt: +((c.creditAmount || c.debitAmount || {}).amount || 0)})),
          big: Object.keys(od.encryptedBigReceiptRequests || {}).map(S),
          items: (od.fulfillmentGroups || []).flatMap(g => (g.lineItems || []).map(l => ({
            sku: S(l.skuNumber || ''), desc: S(l.itemDescFromCatalog || l.itemDescFromPOS || l.description || ''), pos: S(l.itemDescFromPOS || ''),
            qty: l.quantity, oq: l.orginalOrderedQuantity, rq: l.returnedQuantity, list: l.originalUnitPrice, net: l.netUnitPrice,
            disc: l.totalDiscountAmount || 0, pretax: l.pretaxTotal, tax: l.taxAmount, dept: S(l.departmentName || ''),
            st: S(l.statusDescription || ''), ft: S(g.fulfillmentType || '')}))),
        };
      }
    } catch (e) { B.od_err = S(e.message); }
    const enc = o.encryptedReceiptRequest || (od && od.encryptedReceiptRequest);
    if (enc) {
      const rb = JSON.stringify({customerAccountId: ids.customerAccountId, encryptedReceiptRequest: enc});
      try {
        let txt = rcCache[rb];
        if (!txt) {
          const res = await fetch(`${ROOT}/${encodeURIComponent(ids.userId)}/receipt/view`, {method: 'POST', headers: H, credentials: 'include', body: rb});
          if (res.status === 200) {
            const j = await res.json(), d = document.createElement('div');
            d.innerHTML = (j.receipt || {}).receiptImage || ''; d.querySelectorAll('img,script').forEach(x => x.remove());
            d.style.cssText = 'position:absolute;left:-99999px;white-space:pre'; document.body.appendChild(d); txt = d.innerText; d.remove();
            rcCache[rb] = txt;
          } else B.rc_err = 'HTTP ' + res.status;
        }
        if (txt) B.rc = parseReceipt(txt);
      } catch (e) { B.rc_err = S(e.message); }
    } else B.rc_err = 'no receipt link yet';
    const keys = (od && od.encryptedBigReceiptRequests) || {};
    if (Object.keys(keys).length && !(B.rc && B.rc.printedPO)) {
      try {
        const k = keys[o.orderNumber] || Object.values(keys)[0];
        const res = await fetch(`${ROOT}/${encodeURIComponent(ids.userId)}/receipt/view/bigReceipt`, {method: 'POST', headers: H, credentials: 'include',
          body: JSON.stringify({customerAccountId: ids.customerAccountId, encryptedBigReceiptRequest: k})});
        if (res.status !== 200) B.big = {err: 'HTTP ' + res.status};
        else {
          const lines = (await pdfText(await res.arrayBuffer())).split('\n').map(x => x.trim());
          // the PDF puts the job name on the line BEFORE the "PO / Job Name" label (the line after it is the fulfillment type)
          const at = lines.findIndex(l => /^PO \/ Job Name$/i.test(l)); let po = '';
          for (let j = at - 1; at > 0 && j >= 0 && j > at - 4; j--) {
            if (lines[j] && !/^(Receipt #|TBD after|Order #|H\d{4}-\d+)/i.test(lines[j])) { po = lines[j]; break; }
          }
          B.big = {po: S(po)};
        }
      } catch (e) { B.big = {err: S(e.message)}; }
    }
    return B;
  }

  // ---------------------------------------------------------------- run
  (async () => {
    try {
      const ids = await getIds();
      remember(ids);
      const H = ids.headers && Object.keys(ids.headers).length ? ids.headers : BASE_HEADERS;
      st.status = 'pulling order history';
      let raw = [];
      for (const [a, b] of windows(FROM, TO)) { log(`orderhistory ${a}..${b}${SET.job ? ' search ' + JSON.stringify(SET.job) : ''}`); raw.push(...await pullWindow(ids, H, a, b)); }
      const seen = new Set();
      raw = raw.filter(o => {
        const k = [o.transactionId, dateOf(o.salesDate), o.totalAmount, o.storeNumber, o.registerNumber, o.orderNumber].join('|');
        if (seen.has(k)) return false; seen.add(k); return true;
      }).filter(o => { const d = dateOf(o.salesDate); return d >= FROM && d <= TO; });
      st.rows = raw.length;
      log(`${raw.length} rows ${FROM}..${TO}`);
      const out = new Array(raw.length), rcCache = {}; let p = 0, done = 0;
      const worker = async () => {
        while (p < raw.length) {
          const i = p++;
          try { out[i] = await bundle(ids, H, raw[i], i, rcCache); } catch (e) { st.errors.push(S(e.message)); out[i] = {i, err: S(e.message), row: rowOf(raw[i])}; }
          done += 1; st.bundles = done; st.status = `${SET.details ? 'fetching receipts' : 'mapping rows'} ${done}/${raw.length}`;
          if (SET.details) await sleep(150);
        }
      };
      await Promise.all([worker(), worker(), worker()]);
      const meta = {_meta: {tool: 'fetch_receipts.js', fetched_at: new Date().toISOString(), from: FROM, to: TO, job: SET.job || '', details: !!SET.details, rows: raw.length}};
      const lines = [meta].concat(out.sort((a, b) => String(a.d || a.row.date).localeCompare(String(b.d || b.row.date)) || a.i - b.i)).map(x => JSON.stringify(x));
      st.text = lines.join('\n').replace(/\b\d{13,19}\b/g, '[redacted]');
      st.chunks = []; for (let k = 0; k < st.text.length; k += CHUNK) st.chunks.push(st.text.slice(k, k + CHUNK));
      const fname = `hd_bundles_${TO}${SET.job ? '_job' : ''}.jsonl`;
      if (SET.output === 'page') {
        st.status = `done: ${raw.length} rows, ${st.text.length} chars in ${st.chunks.length} chunk(s) - read window.__hdf.chunk(0..${st.chunks.length - 1}) and save them, joined, as ${fname}`;
        st.chunk(0);
      } else {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(new Blob([st.text + '\n'], {type: 'application/x-ndjson'})); a.download = fname;
        document.body.appendChild(a); a.click(); a.remove();
        st.status = `done: ${raw.length} rows - downloaded ${fname}. Give it to your AI assistant (or put it in sync/${TO}/ in the repo).`;
      }
      if (st.errors.length) st.status += ` (${st.errors.length} error(s): window.__hdf.errors)`;
      log(st.status);
    } catch (e) {
      st.status = 'FAILED: ' + S(e.message);
      console.error(TAG, e);
    }
  })();
  return 'fetch_receipts started - poll window.__hdf.status';
})();
