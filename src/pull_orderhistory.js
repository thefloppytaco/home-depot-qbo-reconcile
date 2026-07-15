/* =================================================================================================
 * pull_orderhistory.js
 * =================================================================================================
 *
 * WHAT THIS DOES
 * -------------------------------------------------------------------------------------------------
 * Pulls your COMPLETE Home Depot order history — sales, returns, cancellations, and online
 * orders, with split tenders, store, and PO/Job on every row — by paging through Home Depot's
 * own order-history API, and downloads it as hd_orderhistory_full.json in the exact shape
 * build_ledger.py expects. See docs/01-pull-order-history.md for the full write-up of the
 * endpoint this script drives. This file is meant to be read, not just run — it doubles as
 * documentation.
 *
 * HOW TO USE IT
 * -------------------------------------------------------------------------------------------------
 *   1. Sign in at homedepot.com and open Purchase History:
 *        https://www.homedepot.com/myaccount/purchase-history
 *   2. Open DevTools (F12, or Cmd+Opt+I / Ctrl+Shift+I) and switch to the Console tab. Some
 *      browsers (Chrome included) block pasting into the Console and show a warning first —
 *      if so, type "allow pasting" and press Enter before continuing.
 *   3. Paste this ENTIRE file into the Console and press Enter.
 *   4. Follow whatever it prints:
 *        - If USER_ID / CUSTOMER_ACCOUNT_ID are already filled in below, it starts pulling
 *          immediately.
 *        - If not, it quietly watches for you to trigger an orderhistory request — click to
 *          another page of Purchase History, or change a filter/date range — and captures the
 *          IDs from that request automatically, then starts.
 *   5. When it's done, your browser downloads hd_orderhistory_full.json. Move it into the repo
 *      root (next to src/) — that's where build_ledger.py looks for it.
 *
 * PRIVACY
 * -------------------------------------------------------------------------------------------------
 * This script runs entirely inside your own logged-in browser tab. It loads no external
 * library and phones home to nowhere: the only network requests it makes are the same
 * orderhistory POST requests homedepot.com's own Purchase History page already makes, sent
 * with YOUR browser's existing homedepot.com session cookies. Nothing is sent to any server
 * other than homedepot.com. The output file is written to your Downloads folder via a plain
 * in-browser Blob download — it never leaves your machine unless you move it yourself.
 *
 * Everything below is wrapped in one function so you can safely paste this file into the
 * Console more than once in the same session (e.g. after tweaking a config value below)
 * without hitting "Identifier has already been declared" errors.
 * =================================================================================================
 */
(async function pullOrderHistory() {
  "use strict";

  // ===================================================================================================
  // CONFIG — fill these in yourself, or leave them blank and let AUTO-CAPTURE find them for you.
  // See docs/01-pull-order-history.md, "Find your two IDs", for where to read them by hand.
  //
  //   (a) MANUAL:       paste your own USER_ID / CUSTOMER_ACCOUNT_ID into the two consts below.
  //   (b) AUTO-CAPTURE:  leave either one blank and this script will watch your browser's own
  //                      requests for the next orderhistory call and pull the IDs out of it.
  // ===================================================================================================
  const USER_ID = "";              // from the request path: /user/{USER_ID}/orderhistory
  const CUSTOMER_ACCOUNT_ID = "";  // from the request body: "customerAccountId"
  const START_DATE = "2018-01-01"; // YYYY-MM-DD — pull everything from here forward
  const END_DATE = "";             // YYYY-MM-DD — empty means "today"
  const PAGE_DELAY_MS = 400;       // pause between page requests (also used as the retry backoff)

  // Internal constants — no need to edit these.
  const LOG_PREFIX = "[pull_orderhistory]";
  const ENDPOINT_ROOT = "https://www.homedepot.com/oms/customer/order/v1/user";
  const OUTPUT_FILENAME = "hd_orderhistory_full.json";
  const SAFETY_MAX_PAGES = 500; // hard stop so a mis-parsed total can never loop forever
  const MAX_WINDOW_MONTHS = 24; // live API 400s on a single request spanning more than ~25 months
  const B2B_DATE_RANGE_ERROR_CODE = "B2B_ORDER_HISTORY_ERR_8013"; // API's code for "range too wide"

  function logInfo(...args) { console.log(LOG_PREFIX, ...args); }
  function logWarn(...args) { console.warn(LOG_PREFIX, ...args); }
  function logError(...args) { console.error(LOG_PREFIX, ...args); }
  function logBanner(text, color) {
    console.log(`%c${LOG_PREFIX} ${text}`, `color:${color};font-weight:bold;font-size:13px`);
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function todayISO() {
    const d = new Date();
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return `${yyyy}-${mm}-${dd}`;
  }

  // -------------------------------------------------------------------------------------------
  // Date windowing — as of mid-2026 the live API 400s (error code B2B_ORDER_HISTORY_ERR_8013,
  // "Start date and end date...") on any single request whose startDate..endDate range is too
  // wide; ranges of ~24-25 months succeed (the site's own UI itself uses "Last 25 Months"). So
  // instead of one request spanning the whole START_DATE..END_DATE range, we split it into a
  // series of <=24-month windows and pull each one separately, paginating within each window as
  // before. A page-1 400 on an individual window is also used as a signal that the window
  // predates the account's available order history (see runPull() and STEP 2 below). See also
  // docs/01-pull-order-history.md.
  // -------------------------------------------------------------------------------------------

  function addMonthsISO(dateStr, months) {
    const [y, m, d] = dateStr.split("-").map(Number);
    const date = new Date(Date.UTC(y, m - 1, d));
    date.setUTCMonth(date.getUTCMonth() + months);
    return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-` +
      `${String(date.getUTCDate()).padStart(2, "0")}`;
  }

  function addDaysISO(dateStr, days) {
    const [y, m, d] = dateStr.split("-").map(Number);
    const date = new Date(Date.UTC(y, m - 1, d));
    date.setUTCDate(date.getUTCDate() + days);
    return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-` +
      `${String(date.getUTCDate()).padStart(2, "0")}`;
  }

  /** Splits [startDate, endDate] (inclusive, "YYYY-MM-DD") into contiguous, non-overlapping
   *  windows of at most maxMonths months each, oldest window first. The live API rejects any
   *  single request spanning more than ~25 months, so every window generated here must fit
   *  under that ceiling. */
  function generateDateWindows(startDate, endDate, maxMonths) {
    const windows = [];
    let winStart = startDate;
    while (winStart <= endDate) {
      const uncappedEnd = addDaysISO(addMonthsISO(winStart, maxMonths), -1);
      const winEnd = uncappedEnd > endDate ? endDate : uncappedEnd;
      windows.push({ startDate: winStart, endDate: winEnd });
      winStart = addDaysISO(winEnd, 1);
    }
    return windows;
  }

  // Sanity check — this only works from a homedepot.com tab (it relies on your session
  // cookies for that origin). Warn, but don't hard-stop, in case of an unusual subdomain.
  if (typeof location !== "undefined" && !/(^|\.)homedepot\.com$/i.test(location.hostname || "")) {
    logWarn(
      "this tab doesn't look like homedepot.com. This script only works when run from a " +
      "homedepot.com tab (e.g. your Purchase History page) — it relies on your session cookies."
    );
  }

  // =================================================================================================
  // STEP 1 — get USER_ID + CUSTOMER_ACCOUNT_ID, either from CONFIG above or by auto-capture.
  // =================================================================================================

  function looksLikeOrderHistoryUrl(url) {
    return typeof url === "string" && /\/orderhistory(\?|$)/i.test(url);
  }

  function extractUserIdFromUrl(url) {
    if (typeof url !== "string") return null;
    const m = url.match(/\/user\/([^/]+)\/orderhistory/i);
    return m ? decodeURIComponent(m[1]) : null;
  }

  function extractCustomerAccountId(bodyText) {
    if (typeof bodyText !== "string" || !bodyText) return null;
    try {
      const body = JSON.parse(bodyText);
      return (body && body.orderHistoryRequest && body.orderHistoryRequest.customerAccountId) ??
        (body && body.customerAccountId) ?? null;
    } catch (e) {
      return null;
    }
  }

  /** Normalizes a Headers instance, plain object, or array-of-pairs into a plain
   *  { name: value } object. Returns null if nothing usable was found. */
  function headersToObject(h) {
    if (!h) return null;
    try {
      if (typeof Headers !== "undefined" && h instanceof Headers) {
        const obj = {};
        for (const [k, v] of h.entries()) obj[k] = v;
        return Object.keys(obj).length ? obj : null;
      }
      if (Array.isArray(h)) {
        const obj = {};
        for (const [k, v] of h) obj[k] = v;
        return Object.keys(obj).length ? obj : null;
      }
      if (typeof h === "object") {
        return Object.keys(h).length ? { ...h } : null;
      }
    } catch (e) {
      /* ignore — fall through to null */
    }
    return null;
  }

  /** Watches window.fetch and XMLHttpRequest for a request whose URL matches /orderhistory
   *  and pulls USER_ID out of the URL, customerAccountId out of the JSON body, and the full
   *  set of request headers the live page sent (including things like TMXProfileID and
   *  freshly-generated tracing headers that aren't in our hard-coded fallback set — see
   *  buildHeaders() below). Resolves once it has both IDs, having restored the original
   *  fetch/XHR/setRequestHeader first. */
  function autoCaptureIds(alreadyHave) {
    return new Promise((resolve) => {
      const originalFetch = window.fetch;
      const originalOpen = XMLHttpRequest.prototype.open;
      const originalSend = XMLHttpRequest.prototype.send;
      const originalSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
      const captured = {
        userId: alreadyHave.userId || null,
        customerAccountId: alreadyHave.customerAccountId || null,
        headers: alreadyHave.headers || null,
      };
      let done = false;
      let reminder = null;

      function tryResolve() {
        if (done || !captured.userId || !captured.customerAccountId) return;
        done = true;
        window.fetch = originalFetch;
        XMLHttpRequest.prototype.open = originalOpen;
        XMLHttpRequest.prototype.send = originalSend;
        XMLHttpRequest.prototype.setRequestHeader = originalSetRequestHeader;
        if (reminder) clearInterval(reminder);
        logBanner("IDs captured — restoring normal fetch/XHR and starting the pull.", "#0a7d1e");
        resolve(captured);
      }

      function observe(url, bodyText, headers) {
        if (!looksLikeOrderHistoryUrl(url)) return;
        const uid = extractUserIdFromUrl(url);
        if (uid) captured.userId = uid;
        const cid = extractCustomerAccountId(bodyText);
        if (cid) captured.customerAccountId = cid;
        const h = headersToObject(headers);
        if (h) captured.headers = h;
        tryResolve();
      }

      window.fetch = function (input, init) {
        try {
          const url = typeof input === "string" ? input : input && input.url;
          if (looksLikeOrderHistoryUrl(url)) {
            const headers = (init && init.headers) || (input && input.headers) || null;
            if (init && typeof init.body === "string") {
              observe(url, init.body, headers);
            } else if (input && typeof input.clone === "function") {
              // Body may live on a Request object instead of `init` — read a clone so we
              // don't consume the body the page itself is about to send.
              input.clone().text()
                .then((text) => observe(url, text, headers))
                .catch(() => observe(url, null, headers));
            } else {
              observe(url, null, headers);
            }
          }
        } catch (e) {
          /* never let capture logic break the page */
        }
        return originalFetch.apply(this, arguments);
      };

      XMLHttpRequest.prototype.open = function (method, url, ...rest) {
        this.__pull_orderhistory_url = url;
        this.__pull_orderhistory_headers = {};
        return originalOpen.call(this, method, url, ...rest);
      };
      XMLHttpRequest.prototype.setRequestHeader = function (name, value) {
        try {
          if (!this.__pull_orderhistory_headers) this.__pull_orderhistory_headers = {};
          this.__pull_orderhistory_headers[name] = value;
        } catch (e) {
          /* never let capture logic break the page */
        }
        return originalSetRequestHeader.apply(this, arguments);
      };
      XMLHttpRequest.prototype.send = function (body) {
        try {
          const url = this.__pull_orderhistory_url;
          if (looksLikeOrderHistoryUrl(url)) {
            observe(url, typeof body === "string" ? body : null, this.__pull_orderhistory_headers);
          }
        } catch (e) {
          /* never let capture logic break the page */
        }
        return originalSend.call(this, body);
      };

      logBanner(
        "IDs not filled in — click to another page of your Purchase History (or change a " +
        "filter) so the site makes an orderhistory request; I'll capture the IDs and start " +
        "automatically.",
        "#c1170a"
      );
      reminder = setInterval(() => {
        logInfo("still waiting for an orderhistory request to capture IDs from…");
      }, 15000);
    });
  }

  // =================================================================================================
  // STEP 2 — the pull itself.
  // =================================================================================================

  /** Builds the header set for a page request. If AUTO-CAPTURE grabbed the full header set
   *  from a live orderhistory request earlier in this page load (see autoCaptureIds() above),
   *  that captured set — including things like TMXProfileID and freshly-generated tracing
   *  headers — is reused as-is; live replay of that fresh, complete set has worked fine. In
   *  manual-ID mode (USER_ID/CUSTOMER_ACCOUNT_ID filled in by hand, so nothing was captured)
   *  we fall back to this hard-coded base set instead. */
  function buildHeaders(capturedHeaders) {
    if (capturedHeaders && Object.keys(capturedHeaders).length > 0) {
      return { ...capturedHeaders };
    }
    return {
      "Accept": "application/json, text/plain, */*",
      "Content-Type": "application/json",
      "channelId": "1",
      "Client": "ocm_pd_experience_customer-account-orders-purchases",
      "channel": "desktop",
      "X-Client-App": "PHX-Desktop",
      // Fallback set only (manual-ID mode, nothing was auto-captured). Deliberately NOT
      // sending newrelic / traceparent / X-B3-* / TMXProfileID headers here — replaying STALE
      // tracing headers copied by hand from an old captured request causes the API to 400.
      // A FRESH captured set (see above) is fine and is preferred whenever it's available;
      // this fallback is what we send when there's nothing to capture from. See
      // docs/01-pull-order-history.md.
    };
  }

  function buildBody(pageNumber, startDate, endDate, customerAccountId) {
    return JSON.stringify({
      orderHistoryRequest: {
        pageSize: 500,
        pageNumber,
        startDate,
        endDate,
        customerAccountId,
        sortBy: "salesDate",
        sortOrder: "desc",
        searchType: "ORDERS",
        resultsFilter: "allOrders",
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        searchValue: "",
      },
    });
  }

  /** POSTs one page. Retries once on a network-level failure (e.g. a dropped connection);
   *  does NOT retry on HTTP error responses — those are reported immediately with a clear,
   *  specific message instead. */
  async function fetchPage(userId, customerAccountId, pageNumber, startDate, endDate, capturedHeaders) {
    const url = `${ENDPOINT_ROOT}/${encodeURIComponent(userId)}/orderhistory`;
    const doFetch = () =>
      fetch(url, {
        method: "POST",
        headers: buildHeaders(capturedHeaders),
        credentials: "include",
        body: buildBody(pageNumber, startDate, endDate, customerAccountId),
      });

    let res;
    try {
      res = await doFetch();
    } catch (networkErr) {
      logWarn(`page ${pageNumber}: network error (${networkErr && networkErr.message}) — retrying once…`);
      await sleep(PAGE_DELAY_MS);
      try {
        res = await doFetch();
      } catch (networkErr2) {
        throw new Error(
          `page ${pageNumber} failed after one retry — network error: ${networkErr2 && networkErr2.message}`
        );
      }
    }

    if (res.status === 401 || res.status === 403) {
      throw new Error(
        `page ${pageNumber} got HTTP ${res.status} — you must be signed in on homedepot.com in ` +
        `this tab. Sign in, open Purchase History, and re-run the script.`
      );
    }

    const text = await res.text();

    if (text.includes("Invalid customer account number")) {
      throw new Error(
        `page ${pageNumber}: the API says "Invalid customer account number" — ` +
        `CUSTOMER_ACCOUNT_ID is wrong. Clear both ID consts and let AUTO-CAPTURE find it, or ` +
        `re-check the value in DevTools → Network (see docs/01-pull-order-history.md).`
      );
    }

    if (!res.ok) {
      // Attach the HTTP status so callers (see runPull's window loop) can tell a page-1 400 —
      // possibly B2B_ORDER_HISTORY_ERR_8013, meaning this date window is too wide or predates
      // the account's available history — apart from a genuine mid-window failure.
      const err = new Error(`page ${pageNumber} got HTTP ${res.status} ${res.statusText}: ${text.slice(0, 300)}`);
      err.httpStatus = res.status;
      throw err;
    }

    try {
      return JSON.parse(text);
    } catch (e) {
      throw new Error(`page ${pageNumber}: response wasn't valid JSON: ${text.slice(0, 300)}`);
    }
  }

  /** Reads the reported total order count defensively — tries a few known key names at the
   *  top level, then one level deep. Logs the response's keys if nothing is found, so a
   *  renamed field is easy to diagnose instead of failing silently. */
  function findReportedTotal(json) {
    const keys = ["orderCount", "totalOrders", "totalRecords"];
    for (const k of keys) {
      if (typeof json?.[k] === "number") return json[k];
    }
    for (const outerKey of Object.keys(json || {})) {
      const val = json[outerKey];
      if (val && typeof val === "object" && !Array.isArray(val)) {
        for (const k of keys) {
          if (typeof val[k] === "number") return val[k];
        }
      }
    }
    logWarn("couldn't find a total-orders count in the response. Top-level keys:", Object.keys(json || {}));
    return null;
  }

  /** Finds the array of raw order objects in a response defensively — tries the known keys,
   *  then falls back to the first array-of-objects value found at the top level or one level
   *  deep. Returns { orders, path }; `path` is only used for the progress log. */
  function locateOrdersArray(json) {
    if (Array.isArray(json?.orders)) return { orders: json.orders, path: "orders" };
    if (Array.isArray(json?.orderHistory)) return { orders: json.orderHistory, path: "orderHistory" };
    if (Array.isArray(json?.data?.orders)) return { orders: json.data.orders, path: "data.orders" };

    const isArrayOfObjects = (v) => Array.isArray(v) && (v.length === 0 || typeof v[0] === "object");

    for (const key of Object.keys(json || {})) {
      if (isArrayOfObjects(json[key])) return { orders: json[key], path: key };
    }
    for (const key of Object.keys(json || {})) {
      const val = json[key];
      if (val && typeof val === "object" && !Array.isArray(val)) {
        for (const key2 of Object.keys(val)) {
          if (isArrayOfObjects(val[key2])) return { orders: val[key2], path: `${key}.${key2}` };
        }
      }
    }
    return { orders: [], path: null };
  }

  // =================================================================================================
  // STEP 3 — map each raw order into the compact row shape build_ledger.py reads, then dedupe.
  // Keys must match examples/sample_orderhistory.json exactly: date, type, origin, store, job,
  // total, pretax, tx, receipt, invoices, tenders[{net, last4, amt}].
  // =================================================================================================

  function normalizeDate(raw) {
    if (!raw) return "";
    const s = String(raw);
    const iso = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (iso) return `${iso[1]}-${iso[2]}-${iso[3]}`;
    const us = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})/); // MM/DD/YYYY, just in case
    if (us) return `${us[3]}-${us[1].padStart(2, "0")}-${us[2].padStart(2, "0")}`;
    const d = new Date(s);
    if (!isNaN(d.getTime())) {
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    }
    return s; // unrecognized format — pass it through rather than silently lose it
  }

  function mapOrder(o) {
    return {
      date: normalizeDate(o.salesDate),
      type: o.transactionType,
      origin: o.orderOrigin,
      store: o.storeNumber,
      job: o.POJobName ?? "",
      total: o.totalAmount,
      pretax: o.preTaxAmount,
      tx: o.transactionId,
      receipt: o.receiptDetails ?? "",
      invoices: o.invoiceNumbers ?? [],
      tenders: (o.tenders ?? []).map((t) => ({
        net: t.net ?? t.type ?? "?",
        last4: t.value ?? t.last4 ?? "",
        amt: t.amount,
      })),
    };
  }

  function dedupeRows(rows) {
    const seen = new Set();
    const out = [];
    for (const r of rows) {
      const key = `${r.tx}|${r.date}|${r.total}|${r.store}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(r);
    }
    return out;
  }

  // =================================================================================================
  // STEP 4 — save the result: download hd_orderhistory_full.json, and stash a copy on window.
  // =================================================================================================

  function downloadJSON(obj, filename) {
    const blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  // =================================================================================================
  // Orchestration.
  // =================================================================================================

  /** Pulls every page of a single date window. Returns { orders, skipped, reason }.
   *  A page-1 HTTP 400 is treated as "this window predates the account's available history" —
   *  it's reported back as skipped rather than thrown, so the caller can move on to the next
   *  window. A 400 on any later page is still a real error and propagates normally. */
  async function fetchWindow(userId, customerAccountId, win, capturedHeaders) {
    let reportedTotal = null;
    let pageNumber = 1;
    const orders = [];

    while (pageNumber <= SAFETY_MAX_PAGES) {
      logInfo(`window ${win.startDate} .. ${win.endDate}: fetching page ${pageNumber}…`);

      let json;
      try {
        json = await fetchPage(userId, customerAccountId, pageNumber, win.startDate, win.endDate, capturedHeaders);
      } catch (err) {
        if (pageNumber === 1 && err && err.httpStatus === 400) {
          const reason = `HTTP 400 on page 1 (likely ${B2B_DATE_RANGE_ERROR_CODE} — "Start date and ` +
            `end date...") — this window likely predates the account's available order history`;
          logWarn(`window ${win.startDate} .. ${win.endDate}: ${reason}. Skipping and continuing.`);
          return { orders, pagesFetched: 0, skipped: true, reason };
        }
        throw err; // a 400 on a later page, or any other error, is a real failure — don't swallow it
      }

      if (reportedTotal === null) {
        reportedTotal = findReportedTotal(json);
        if (reportedTotal !== null) {
          logInfo(`window ${win.startDate} .. ${win.endDate}: API reports ${reportedTotal} total orders.`);
        }
      }

      const { orders: pageOrders, path } = locateOrdersArray(json);
      logInfo(
        `window ${win.startDate} .. ${win.endDate}: page ${pageNumber}: ${pageOrders.length} orders ` +
        `(found at "${path ?? "?"}"). ${orders.length + pageOrders.length} collected in this window so far.`
      );

      if (pageOrders.length === 0) {
        logInfo(`window ${win.startDate} .. ${win.endDate}: page ${pageNumber} returned 0 orders — window done.`);
        return { orders, pagesFetched: pageNumber, skipped: false };
      }
      orders.push(...pageOrders);

      if (reportedTotal !== null && orders.length >= reportedTotal) {
        logInfo(
          `window ${win.startDate} .. ${win.endDate}: collected ${orders.length} >= reported total ` +
          `${reportedTotal} — window done.`
        );
        return { orders, pagesFetched: pageNumber, skipped: false };
      }

      pageNumber += 1;
      await sleep(PAGE_DELAY_MS);
    }

    logWarn(
      `window ${win.startDate} .. ${win.endDate}: hit the safety cap of ${SAFETY_MAX_PAGES} pages — ` +
      `stopping this window anyway. If you really have more than ~${SAFETY_MAX_PAGES * 495} orders in ` +
      `one window, raise SAFETY_MAX_PAGES near the top of this file.`
    );
    return { orders, pagesFetched: SAFETY_MAX_PAGES, skipped: false };
  }

  async function runPull(userId, customerAccountId, capturedHeaders) {
    const startDate = START_DATE;
    const endDate = END_DATE || todayISO();
    // Newest-first: if a run is interrupted, the most useful (most recent) data is already
    // downloaded. Windows older than the account's history simply get skipped as we go.
    const windows = generateDateWindows(startDate, endDate, MAX_WINDOW_MONTHS).reverse();

    logInfo(`starting pull — USER_ID=${userId}  CUSTOMER_ACCOUNT_ID=${customerAccountId}`);
    logInfo(
      `overall date range ${startDate} .. ${endDate}, split into ${windows.length} window(s) of up ` +
      `to ${MAX_WINDOW_MONTHS} months each (the live API 400s on wider single-request ranges).`
    );

    const rawOrders = [];
    const skippedWindows = [];
    let totalPagesFetched = 0;

    for (const win of windows) {
      logInfo(`window ${win.startDate} .. ${win.endDate}: starting…`);
      const result = await fetchWindow(userId, customerAccountId, win, capturedHeaders);
      totalPagesFetched += result.pagesFetched;

      if (result.skipped) {
        skippedWindows.push({ startDate: win.startDate, endDate: win.endDate, reason: result.reason });
      } else {
        rawOrders.push(...result.orders);
        logInfo(`window ${win.startDate} .. ${win.endDate}: done — orderCount=${result.orders.length}.`);
      }

      await sleep(PAGE_DELAY_MS);
    }

    const rows = dedupeRows(rawOrders.map(mapOrder));
    const dupes = rawOrders.length - rows.length;

    const byType = {};
    for (const r of rows) byType[r.type] = (byType[r.type] || 0) + 1;

    logBanner("done.", "#0a7d1e");
    console.table([
      { metric: "windows fetched", value: windows.length },
      { metric: "windows skipped (predate available history)", value: skippedWindows.length },
      { metric: "pages fetched", value: totalPagesFetched },
      { metric: "raw orders pulled", value: rawOrders.length },
      { metric: "duplicates removed", value: dupes },
      { metric: "unique rows", value: rows.length },
      ...Object.keys(byType).sort().map((type) => ({ metric: `type: ${type}`, value: byType[type] })),
      ...skippedWindows.map((w) => ({
        metric: `skipped window: ${w.startDate} .. ${w.endDate}`,
        value: w.reason,
      })),
    ]);

    const result = { pulled: rows.length, rows };
    window.__hd_orderhistory = result;
    downloadJSON(result, OUTPUT_FILENAME);

    logInfo(`downloaded ${OUTPUT_FILENAME} — move it into the repo root, next to src/.`);
    logInfo(
      `if your browser blocked the download, run this instead: ` +
      `copy(JSON.stringify(window.__hd_orderhistory)) — then paste the clipboard into a new ` +
      `file named ${OUTPUT_FILENAME}.`
    );

    return result;
  }

  try {
    let userId = USER_ID;
    let customerAccountId = CUSTOMER_ACCOUNT_ID;
    let capturedHeaders = null;
    if (!userId || !customerAccountId) {
      const captured = await autoCaptureIds({ userId, customerAccountId });
      userId = captured.userId;
      customerAccountId = captured.customerAccountId;
      capturedHeaders = captured.headers || null;
    }
    await runPull(userId, customerAccountId, capturedHeaders);
  } catch (err) {
    logError("pull failed — no file was downloaded.");
    logError((err && err.message) || err);
  }
})();
