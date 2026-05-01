/**
 * Wisewell Store Events Collector + Nightly Aggregator
 *
 * SETUP:
 * 1. Paste into Google Apps Script (Extensions → Apps Script)
 * 2. Deploy as Web App:
 *      Type: Web app | Execute as: Me | Who has access: Anyone
 *      Copy the deployment URL → paste as ENDPOINT in the pixel scripts
 * 3. Set up nightly aggregation trigger:
 *      Triggers → + Add Trigger → aggregateYesterday
 *      Event source: Time-driven | Type: Day timer | Time: 1am–2am
 *
 * Sheet layout:
 *   RAW_SHEET_ID   — dedicated events sheet (1j9lWQC9I8...)  ← pixel events go here
 *   MAIN_SHEET_ID  — dashboard sheet      (1NjPJKswE2rX...)  ← daily summaries appended here
 */

// ── Sheet IDs ──────────────────────────────────────────────────────────────────
const RAW_SHEET_ID  = "1j9lWQC9I8HdtTguzcGGX1AewE6KkdICkhbGYqwErKKU";
const MAIN_SHEET_ID = "1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4";

const RAW_TAB    = "Store Events - Live";
const MARKETS    = ["UAE", "KSA", "USA"];

// Each market's calendar boundary uses its own local timezone — so "today"
// for USA rolls over at midnight Eastern, not midnight Dubai.
const MARKET_TZ = {
  UAE: "Asia/Dubai",
  KSA: "Asia/Riyadh",        // same offset as Dubai but semantically correct
  USA: "America/New_York",   // ET (handles DST automatically)
};

const SUMMARY_TABS = {
  UAE: "Shopify Website - UAE",
  KSA: "Shopify Website - KSA",
  USA: "Shopify Website - USA",
};

const SOURCE_TAB = "Sessions by Source - Daily";   // multi-market, written to MAIN sheet
const PAGE_TAB   = "Top Landing Pages - Daily";    // multi-market, written to MAIN sheet

const RAW_HEADERS = [
  "timestamp", "market", "source", "event_type",
  "session_id", "page_path", "product_id", "product_title",
  "value", "currency", "order_id", "checkout_id",
  "referrer", "utm_source", "utm_medium", "utm_campaign",
  "utm_content", "fbclid", "gclid",
];

// Per-market tab schema — matches the Shopify-exported historical schema.
// Tab name implies the market, so no "market" column.
const SUMMARY_HEADERS = [
  "Day", "Sessions", "Sessions with cart additions",
  "Sessions that reached checkout", "Sessions that completed checkout",
  "Conversion rate", "New sessions", "Returning sessions",
];

const SOURCE_HEADERS = [
  "date", "market", "channel", "utm_source", "utm_campaign",
  "sessions", "add_to_cart", "reached_checkout", "completed_checkout",
];

const PAGE_HEADERS = [
  "date", "market", "page_path", "sessions", "add_to_cart",
];

// ── Helpers ────────────────────────────────────────────────────────────────────

function getRawSheet() {
  const ss = SpreadsheetApp.openById(RAW_SHEET_ID);
  let sheet = ss.getSheetByName(RAW_TAB);
  if (!sheet) {
    sheet = ss.insertSheet(RAW_TAB);
    sheet.appendRow(RAW_HEADERS);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function getSummarySheet(market) {
  const ss = SpreadsheetApp.openById(MAIN_SHEET_ID);
  const tabName = SUMMARY_TABS[market];
  let sheet = ss.getSheetByName(tabName);
  if (!sheet) {
    sheet = ss.insertSheet(tabName);
    sheet.appendRow(SUMMARY_HEADERS);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function getMainTab(tabName, headers) {
  const ss = SpreadsheetApp.openById(MAIN_SHEET_ID);
  let sheet = ss.getSheetByName(tabName);
  if (!sheet) {
    sheet = ss.insertSheet(tabName);
    sheet.appendRow(headers);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

// ── Web App entry points ───────────────────────────────────────────────────────

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    const sheet = getRawSheet();
    const events = Array.isArray(body) ? body : [body];

    events.forEach(ev => {
      sheet.appendRow([
        ev.timestamp     || new Date().toISOString(),
        ev.market        || "",
        ev.source        || "",
        ev.event_type    || "",
        ev.session_id    || "",
        ev.page_path     || "",
        ev.product_id    || "",
        ev.product_title || "",
        ev.value         || "",
        ev.currency      || "",
        ev.order_id      || "",
        ev.checkout_id   || "",
        ev.referrer      || "",
        ev.utm_source    || "",
        ev.utm_medium    || "",
        ev.utm_campaign  || "",
        ev.utm_content   || "",
        ev.fbclid        || "",
        ev.gclid         || "",
      ]);
    });

    return ContentService
      .createTextOutput(JSON.stringify({ ok: true, rows: events.length }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: err.message }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

function doGet(e) {
  return ContentService
    .createTextOutput(JSON.stringify({ ok: true, service: "Wisewell Store Events" }))
    .setMimeType(ContentService.MimeType.JSON);
}

// ── Channel classification ─────────────────────────────────────────────────────

function classifyChannel(utm_source, utm_medium, referrer, fbclid, gclid) {
  const us = (utm_source || "").toLowerCase();
  const um = (utm_medium || "").toLowerCase();
  const ref = (referrer || "").toLowerCase();

  // Paid (high confidence — explicit click IDs win)
  if (fbclid)  return "Paid Social (Meta)";
  if (gclid)   return "Paid Search (Google)";
  if (um === "cpc" || um === "ppc" || um === "paid")  {
    if (us.includes("google"))   return "Paid Search (Google)";
    if (us.includes("facebook") || us.includes("instagram") || us.includes("meta"))
      return "Paid Social (Meta)";
    if (us.includes("tiktok"))   return "Paid Social (TikTok)";
    return "Paid Other";
  }
  if (us.includes("facebook") || us.includes("instagram") || us.includes("meta"))
    return "Paid Social (Meta)";
  if (us.includes("google") && um !== "organic") return "Paid Search (Google)";
  if (us.includes("tiktok"))   return "Paid Social (TikTok)";
  if (us.includes("snapchat")) return "Paid Social (Snapchat)";

  // Email / SMS
  if (um === "email" || us.includes("klaviyo") || us.includes("mailchimp")) return "Email";
  if (um === "sms")   return "SMS";

  // Organic / referral
  if (ref) {
    if (/google\./.test(ref))    return "Organic Search";
    if (/bing\.|yahoo\./.test(ref)) return "Organic Search";
    if (/facebook\.|instagram\.|m\.facebook|fb\./.test(ref)) return "Organic Social";
    if (/tiktok\./.test(ref))    return "Organic Social";
    if (/twitter\.|x\.com/.test(ref)) return "Organic Social";
    return "Referral";
  }

  return "Direct";
}

// ── Nightly aggregation ────────────────────────────────────────────────────────

/**
 * Run every 15 min. Aggregates each market's "today" in its OWN local
 * timezone (UAE/KSA in Asia/Dubai, USA in America/New_York). The day
 * boundary therefore follows local-midnight per market.
 */
function aggregateToday() {
  const now = new Date();
  const targetByMarket = {};
  MARKETS.forEach(m => {
    targetByMarket[m] = {
      isoDate: Utilities.formatDate(now, MARKET_TZ[m], "yyyy-MM-dd"),
      dateStr: Utilities.formatDate(now, MARKET_TZ[m], "dd/MM/yyyy"),
    };
  });
  _aggregatePerMarket(targetByMarket);
}

/**
 * Run at 1am Dubai. Catches any late events for each market's local
 * yesterday. Note: when this runs at 1am Dubai, USA's "yesterday" is
 * the day that just ended at midnight ET ~9 hours earlier — that's
 * the intended behaviour.
 */
function aggregateYesterday() {
  const yest = new Date();
  yest.setDate(yest.getDate() - 1);
  const targetByMarket = {};
  MARKETS.forEach(m => {
    targetByMarket[m] = {
      isoDate: Utilities.formatDate(yest, MARKET_TZ[m], "yyyy-MM-dd"),
      dateStr: Utilities.formatDate(yest, MARKET_TZ[m], "dd/MM/yyyy"),
    };
  });
  _aggregatePerMarket(targetByMarket);
}

/**
 * Manual backfill: aggregateDate("27/04/2026")
 * Aggregates that calendar date for each market in its own local timezone.
 */
function aggregateDate(ddmmyyyy) {
  const parts = ddmmyyyy.split("/");
  const isoDate = `${parts[2]}-${parts[1]}-${parts[0]}`;
  const targetByMarket = {};
  MARKETS.forEach(m => {
    targetByMarket[m] = { isoDate: isoDate, dateStr: ddmmyyyy };
  });
  _aggregatePerMarket(targetByMarket);
}

/** Delete all existing rows in `sheet` whose column-A *displayed* value matches
 *  `dateStr`. Using getDisplayValues() so comparison works whether the cell
 *  is a literal string "30/04/2026" or a Date object that displays as "30/04/2026"
 *  (which happens when users paste from Shopify CSV exports). */
function _deleteRowsForDate(sheet, dateStr) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return;
  const colA = sheet.getRange(2, 1, lastRow - 1, 1).getDisplayValues();
  for (let i = colA.length - 1; i >= 0; i--) {
    if (String(colA[i][0]).trim() === dateStr) {
      sheet.deleteRow(i + 2);  // +2 because we started at sheet row 2 (skipping header)
    }
  }
}

/**
 * Aggregate per-market events using each market's own local-timezone date.
 * targetByMarket: { UAE: {isoDate, dateStr}, KSA: {...}, USA: {...} }
 *   isoDate — yyyy-MM-dd in market's local TZ (used to bucket events)
 *   dateStr — dd/MM/yyyy in market's local TZ (used for the output row's date column)
 */
function _aggregatePerMarket(targetByMarket) {
  const rawSheet = getRawSheet();
  const data = rawSheet.getDataRange().getValues();
  if (data.length < 2) { Logger.log("No raw events."); return; }

  const headers = data[0];
  const idx = (k) => headers.indexOf(k);
  const tsI    = idx("timestamp");
  const mktI   = idx("market");
  const evtI   = idx("event_type");
  const sidI   = idx("session_id");
  const pageI  = idx("page_path");
  const refI   = idx("referrer");
  const usI    = idx("utm_source");
  const umI    = idx("utm_medium");
  const ucI    = idx("utm_campaign");
  const fbI    = idx("fbclid");
  const gcI    = idx("gclid");

  // Helper — convert UTC ISO timestamp to its local-date in the market's TZ
  function eventLocalIso(ts, mkt) {
    if (!ts) return "";
    try {
      return Utilities.formatDate(new Date(ts), MARKET_TZ[mkt], "yyyy-MM-dd");
    } catch (e) {
      return "";
    }
  }

  // Build set of clientIds seen BEFORE the target day in each market's local TZ
  const seenBefore = {};
  MARKETS.forEach(m => { seenBefore[m] = new Set(); });
  data.slice(1).forEach(r => {
    const mkt = String(r[mktI] || "").trim().toUpperCase();
    if (!seenBefore[mkt]) return;
    const ts = String(r[tsI] || "");
    const eventIso = eventLocalIso(ts, mkt);
    if (!eventIso) return;
    if (eventIso >= targetByMarket[mkt].isoDate) return;  // same day or future — skip for "before"
    const sid = String(r[sidI] || "").trim();
    if (sid) seenBefore[mkt].add(sid);
  });

  // Per-market summary stats
  const summary = {};
  MARKETS.forEach(m => {
    summary[m] = {
      sessions: new Set(), new_sessions: new Set(), returning_sessions: new Set(),
      add_to_cart: 0, reached_checkout: 0, completed_checkout: 0,
    };
  });

  // Per-market source-attribution stats. Key: "channel||utm_source||utm_campaign"
  const sourceStats = {};
  MARKETS.forEach(m => { sourceStats[m] = {}; });

  // Per-market session→source map (first source per session today)
  const sessionSource = {};
  MARKETS.forEach(m => { sessionSource[m] = {}; });

  // Per-market page → set of sessions
  const pageStats = {};
  MARKETS.forEach(m => { pageStats[m] = {}; });

  data.slice(1).forEach(r => {
    const mkt = String(r[mktI] || "").trim().toUpperCase();
    if (!summary[mkt]) return;
    const ts  = String(r[tsI] || "");
    const eventIso = eventLocalIso(ts, mkt);
    if (eventIso !== targetByMarket[mkt].isoDate) return;
    const evt = String(r[evtI] || "").trim();
    const sid = String(r[sidI] || "").trim();

    if (evt === "page_viewed") {
      if (sid) {
        summary[mkt].sessions.add(sid);
        if (seenBefore[mkt].has(sid)) summary[mkt].returning_sessions.add(sid);
        else                          summary[mkt].new_sessions.add(sid);
      }

      // Source attribution — store first source seen today per session
      if (sid && !sessionSource[mkt][sid]) {
        const channel = classifyChannel(r[usI], r[umI], r[refI], r[fbI], r[gcI]);
        const us = String(r[usI] || "").trim().toLowerCase() || "(none)";
        const uc = String(r[ucI] || "").trim().toLowerCase() || "(none)";
        sessionSource[mkt][sid] = { channel, utm_source: us, utm_campaign: uc };
      }

      // Top pages
      const pg = String(r[pageI] || "").trim() || "/";
      if (!pageStats[mkt][pg]) pageStats[mkt][pg] = { sessions: new Set(), atc: 0 };
      if (sid) pageStats[mkt][pg].sessions.add(sid);

    } else if (evt === "product_added_to_cart") {
      summary[mkt].add_to_cart++;
    } else if (evt === "checkout_started") {
      summary[mkt].reached_checkout++;
    } else if (evt === "checkout_completed") {
      summary[mkt].completed_checkout++;
    }
  });

  // Build sourceStats counts from session→source map
  MARKETS.forEach(market => {
    const sessMap = sessionSource[market];
    Object.keys(sessMap).forEach(sid => {
      const s = sessMap[sid];
      const key = `${s.channel}||${s.utm_source}||${s.utm_campaign}`;
      if (!sourceStats[market][key]) {
        sourceStats[market][key] = {
          channel: s.channel, utm_source: s.utm_source, utm_campaign: s.utm_campaign,
          sessions: 0, atc: 0, reached: 0, completed: 0,
        };
      }
      sourceStats[market][key].sessions++;
    });
  });

  // Approximate ATC/reached/completed by source — attribute to session's source
  data.slice(1).forEach(r => {
    const mkt = String(r[mktI] || "").trim().toUpperCase();
    if (!sourceStats[mkt]) return;
    const ts = String(r[tsI] || "");
    const eventIso = eventLocalIso(ts, mkt);
    if (eventIso !== targetByMarket[mkt].isoDate) return;
    const evt = String(r[evtI] || "").trim();
    const sid = String(r[sidI] || "").trim();
    if (!sid) return;
    const src = sessionSource[mkt][sid];
    if (!src) return;
    const key = `${src.channel}||${src.utm_source}||${src.utm_campaign}`;
    const bucket = sourceStats[mkt][key];
    if (!bucket) return;
    if (evt === "product_added_to_cart") bucket.atc++;
    else if (evt === "checkout_started") bucket.reached++;
    else if (evt === "checkout_completed") bucket.completed++;
  });

  // ── UPSERT: Per-market daily summary ──
  // Schema matches the existing Shopify-exported tab (no market column,
  // since the tab name implies it):
  // Day | Sessions | Sessions with cart additions | Sessions that reached checkout
  //     | Sessions that completed checkout | Conversion rate | New sessions | Returning sessions
  MARKETS.forEach(market => {
    const s = summary[market];
    const sessions = s.sessions.size;
    const cvr = sessions > 0 ? (s.completed_checkout / sessions) : 0;
    const cvrPct = (cvr * 100).toFixed(2) + "%";
    const dateStr = targetByMarket[market].dateStr;

    const sheet = getSummarySheet(market);
    _deleteRowsForDate(sheet, dateStr);
    sheet.appendRow([
      dateStr,
      sessions,
      s.add_to_cart,
      s.reached_checkout,
      s.completed_checkout,
      cvrPct,
      s.new_sessions.size,
      s.returning_sessions.size,
    ]);
  });

  // ── UPSERT: Sessions by Source - Daily — delete each market's date rows ──
  const sourceSheet = getMainTab(SOURCE_TAB, SOURCE_HEADERS);
  // Delete rows where (date, market) match any of the targets
  _deleteRowsForDateMarketPairs(sourceSheet,
    MARKETS.map(m => ({ date: targetByMarket[m].dateStr, market: m })));
  MARKETS.forEach(market => {
    Object.values(sourceStats[market])
      .sort((a, b) => b.sessions - a.sessions)
      .forEach(row => {
        sourceSheet.appendRow([
          targetByMarket[market].dateStr, market, row.channel,
          row.utm_source, row.utm_campaign,
          row.sessions, row.atc, row.reached, row.completed,
        ]);
      });
  });

  // ── UPSERT: Top Landing Pages - Daily ──
  const pageSheet = getMainTab(PAGE_TAB, PAGE_HEADERS);
  _deleteRowsForDateMarketPairs(pageSheet,
    MARKETS.map(m => ({ date: targetByMarket[m].dateStr, market: m })));
  MARKETS.forEach(market => {
    const pages = Object.entries(pageStats[market])
      .map(([pg, s]) => ({ page: pg, sessions: s.sessions.size, atc: s.atc }))
      .sort((a, b) => b.sessions - a.sessions)
      .slice(0, 10);
    pages.forEach(p => {
      pageSheet.appendRow([targetByMarket[market].dateStr, market, p.page, p.sessions, p.atc]);
    });
  });

  Logger.log("Upserted per-market dates: " +
             MARKETS.map(m => `${m}=${targetByMarket[m].dateStr}`).join(", "));
}

/** Delete rows where column A == date AND column B == market for any pair in `pairs`.
 *  Uses getDisplayValues() to handle Date-typed cells (e.g. from manual pastes). */
function _deleteRowsForDateMarketPairs(sheet, pairs) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return;
  const colsAB = sheet.getRange(2, 1, lastRow - 1, 2).getDisplayValues();
  const set = new Set(pairs.map(p => `${p.date}||${p.market}`));
  for (let i = colsAB.length - 1; i >= 0; i--) {
    const key = `${String(colsAB[i][0]).trim()}||${String(colsAB[i][1]).trim().toUpperCase()}`;
    if (set.has(key)) {
      sheet.deleteRow(i + 2);
    }
  }
}
