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

const SUMMARY_TABS = {
  UAE: "Shopify Website - UAE",
  KSA: "Shopify Website - KSA",
  USA: "Shopify Website - USA",
};

const SOURCE_TAB = "Sessions by Source - Daily";   // multi-market, written to MAIN sheet
const PAGE_TAB   = "Top Landing Pages - Daily";    // multi-market, written to MAIN sheet
const LIVE_TAB   = "Shopify Website - Live Today"; // 3 rows (one per market), overwritten by aggregateLive()

const RAW_HEADERS = [
  "timestamp", "market", "source", "event_type",
  "session_id", "page_path", "product_id", "product_title",
  "value", "currency", "order_id", "checkout_id",
  "referrer", "utm_source", "utm_medium", "utm_campaign",
  "utm_content", "fbclid", "gclid",
];

const SUMMARY_HEADERS = [
  "date", "market", "sessions", "new_sessions", "returning_sessions",
  "add_to_cart", "reached_checkout", "completed_checkout", "conversion_rate",
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

function aggregateYesterday() {
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  const dateStr    = Utilities.formatDate(yesterday, "Asia/Dubai", "dd/MM/yyyy");
  const datePrefix = Utilities.formatDate(yesterday, "Asia/Dubai", "yyyy-MM-dd");
  _aggregateDate(dateStr, datePrefix);
}

/**
 * Aggregates TODAY's events so far and writes the result to the "Shopify
 * Website - Live Today" tab in the main sheet (one row per market). The
 * row is overwritten on each call so the tab always reflects "today as of now".
 *
 * Run on a 15-minute time-driven trigger.
 */
function aggregateLive() {
  const now        = new Date();
  const dateStr    = Utilities.formatDate(now, "Asia/Dubai", "dd/MM/yyyy");
  const datePrefix = Utilities.formatDate(now, "Asia/Dubai", "yyyy-MM-dd");
  const updatedAt  = Utilities.formatDate(now, "Asia/Dubai", "yyyy-MM-dd HH:mm");

  const rawSheet = getRawSheet();
  const data = rawSheet.getDataRange().getValues();
  if (data.length < 2) return;

  const headers = data[0];
  const tsI    = headers.indexOf("timestamp");
  const mktI   = headers.indexOf("market");
  const evtI   = headers.indexOf("event_type");
  const sidI   = headers.indexOf("session_id");

  // Build seen-before set per market for new vs returning
  const seenBefore = {};
  MARKETS.forEach(m => { seenBefore[m] = new Set(); });
  data.slice(1).forEach(r => {
    const ts = String(r[tsI] || "");
    if (ts.startsWith(datePrefix)) return;
    const mkt = String(r[mktI] || "").trim().toUpperCase();
    const sid = String(r[sidI] || "").trim();
    if (mkt && sid && seenBefore[mkt]) seenBefore[mkt].add(sid);
  });

  const stats = {};
  MARKETS.forEach(m => {
    stats[m] = {
      sessions: new Set(), new_sessions: new Set(), returning_sessions: new Set(),
      add_to_cart: 0, reached_checkout: 0, completed_checkout: 0,
    };
  });

  data.slice(1).forEach(r => {
    const ts = String(r[tsI] || "");
    if (!ts.startsWith(datePrefix)) return;
    const mkt = String(r[mktI] || "").trim().toUpperCase();
    if (!stats[mkt]) return;
    const evt = String(r[evtI] || "").trim();
    const sid = String(r[sidI] || "").trim();

    if (evt === "page_viewed") {
      if (sid) {
        stats[mkt].sessions.add(sid);
        if (seenBefore[mkt].has(sid)) stats[mkt].returning_sessions.add(sid);
        else                          stats[mkt].new_sessions.add(sid);
      }
    } else if (evt === "product_added_to_cart") {
      stats[mkt].add_to_cart++;
    } else if (evt === "checkout_started") {
      stats[mkt].reached_checkout++;
    } else if (evt === "checkout_completed") {
      stats[mkt].completed_checkout++;
    }
  });

  // Overwrite the live tab from scratch
  const ss = SpreadsheetApp.openById(MAIN_SHEET_ID);
  let sheet = ss.getSheetByName(LIVE_TAB);
  const liveHeaders = [
    "date", "market", "sessions", "new_sessions", "returning_sessions",
    "add_to_cart", "reached_checkout", "completed_checkout", "conversion_rate",
    "updated_at",
  ];
  if (!sheet) {
    sheet = ss.insertSheet(LIVE_TAB);
  } else {
    sheet.clear();
  }
  sheet.appendRow(liveHeaders);
  sheet.setFrozenRows(1);

  MARKETS.forEach(market => {
    const s = stats[market];
    const sessions = s.sessions.size;
    const cvr = sessions > 0 ? (s.completed_checkout / sessions) : 0;
    sheet.appendRow([
      dateStr, market, sessions, s.new_sessions.size, s.returning_sessions.size,
      s.add_to_cart, s.reached_checkout, s.completed_checkout,
      (cvr * 100).toFixed(2) + "%",
      updatedAt,
    ]);
  });

  Logger.log(`Live aggregation written for ${dateStr} at ${updatedAt}.`);
}

function aggregateDate(ddmmyyyy) {
  const parts = ddmmyyyy.split("/");
  const datePrefix = `${parts[2]}-${parts[1]}-${parts[0]}`;
  _aggregateDate(ddmmyyyy, datePrefix);
}

function _aggregateDate(dateStr, datePrefix) {
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

  // Build set of clientIds seen BEFORE today across the raw sheet (for new vs returning)
  const seenBefore = {};
  MARKETS.forEach(m => { seenBefore[m] = new Set(); });
  data.slice(1).forEach(r => {
    const ts = String(r[tsI] || "");
    if (ts.startsWith(datePrefix)) return;
    if (ts > datePrefix) return;
    const mkt = String(r[mktI] || "").trim().toUpperCase();
    const sid = String(r[sidI] || "").trim();
    if (mkt && sid && seenBefore[mkt]) seenBefore[mkt].add(sid);
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
    const ts  = String(r[tsI] || "");
    if (!ts.startsWith(datePrefix)) return;
    const mkt = String(r[mktI] || "").trim().toUpperCase();
    if (!summary[mkt]) return;
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
      // Increment ATC on the most recent page seen by this session — best effort: skip
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
    const ts  = String(r[tsI] || "");
    if (!ts.startsWith(datePrefix)) return;
    const mkt = String(r[mktI] || "").trim().toUpperCase();
    if (!sourceStats[mkt]) return;
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

  // ── WRITE: Per-market daily summary ──
  MARKETS.forEach(market => {
    const s = summary[market];
    const sessions = s.sessions.size;
    const cvr = sessions > 0 ? (s.completed_checkout / sessions) : 0;
    const cvrPct = (cvr * 100).toFixed(2) + "%";

    const sheet = getSummarySheet(market);
    const existing = sheet.getDataRange().getValues();
    if (existing.slice(1).some(r => String(r[0]).trim() === dateStr)) {
      Logger.log(`${market} summary ${dateStr} already exists, skipping.`);
      return;
    }
    sheet.appendRow([
      dateStr, market, sessions, s.new_sessions.size, s.returning_sessions.size,
      s.add_to_cart, s.reached_checkout, s.completed_checkout, cvrPct,
    ]);
  });

  // ── WRITE: Sessions by Source - Daily ──
  const sourceSheet = getMainTab(SOURCE_TAB, SOURCE_HEADERS);
  const sourceExisting = sourceSheet.getDataRange().getValues();
  const alreadyHaveSource = sourceExisting.slice(1).some(r =>
    String(r[0]).trim() === dateStr
  );
  if (!alreadyHaveSource) {
    MARKETS.forEach(market => {
      Object.values(sourceStats[market])
        .sort((a, b) => b.sessions - a.sessions)
        .forEach(row => {
          sourceSheet.appendRow([
            dateStr, market, row.channel, row.utm_source, row.utm_campaign,
            row.sessions, row.atc, row.reached, row.completed,
          ]);
        });
    });
  } else {
    Logger.log(`Source breakdown for ${dateStr} already exists, skipping.`);
  }

  // ── WRITE: Top Landing Pages - Daily (top 10 per market) ──
  const pageSheet = getMainTab(PAGE_TAB, PAGE_HEADERS);
  const pageExisting = pageSheet.getDataRange().getValues();
  const alreadyHavePages = pageExisting.slice(1).some(r =>
    String(r[0]).trim() === dateStr
  );
  if (!alreadyHavePages) {
    MARKETS.forEach(market => {
      const pages = Object.entries(pageStats[market])
        .map(([pg, s]) => ({ page: pg, sessions: s.sessions.size, atc: s.atc }))
        .sort((a, b) => b.sessions - a.sessions)
        .slice(0, 10);
      pages.forEach(p => {
        pageSheet.appendRow([dateStr, market, p.page, p.sessions, p.atc]);
      });
    });
  } else {
    Logger.log(`Page breakdown for ${dateStr} already exists, skipping.`);
  }

  Logger.log(`Aggregated ${dateStr} for ${MARKETS.length} markets.`);
}
