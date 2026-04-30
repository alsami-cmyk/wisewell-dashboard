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
 *   MAIN_SHEET_ID  — dashboard sheet     (1NjPJKswE2rX...)  ← daily summary appended here
 */

// ── Sheet IDs ──────────────────────────────────────────────────────────────────
const RAW_SHEET_ID  = "1j9lWQC9I8HdtTguzcGGX1AewE6KkdICkhbGYqwErKKU";  // raw pixel events
const MAIN_SHEET_ID = "1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4";  // main dashboard

const RAW_TAB       = "Store Events - Live";
const MARKETS       = ["UAE", "KSA", "USA"];

// Summary tab name per market in the main sheet
const SUMMARY_TABS  = {
  UAE: "Shopify Website - UAE",
  KSA: "Shopify Website - KSA",
  USA: "Shopify Website - USA",
};

const RAW_HEADERS = [
  "timestamp", "market", "source", "event_type",
  "session_id", "page_path", "product_id", "product_title",
  "value", "currency", "order_id", "checkout_id",
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
    sheet.appendRow(["date", "market", "sessions", "add_to_cart", "reached_checkout", "completed_checkout", "conversion_rate"]);
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
        ev.source        || "",        // "pixel" or "webhook"
        ev.event_type    || "",
        ev.session_id    || "",
        ev.page_path     || "",
        ev.product_id    || "",
        ev.product_title || "",
        ev.value         || "",
        ev.currency      || "",
        ev.order_id      || "",
        ev.checkout_id   || "",
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

// Allow CORS preflight from Shopify pixel sandbox
function doGet(e) {
  return ContentService
    .createTextOutput(JSON.stringify({ ok: true, service: "Wisewell Store Events" }))
    .setMimeType(ContentService.MimeType.JSON);
}

// ── Nightly aggregation ────────────────────────────────────────────────────────

/**
 * Reads yesterday's raw events, aggregates per market, and appends one row
 * per market to the corresponding "Shopify Website - {market}" tab in the
 * main dashboard sheet.
 *
 * Triggered automatically at ~1am by a time-driven trigger.
 * Can also be run manually from the Apps Script editor to backfill.
 */
function aggregateYesterday() {
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  const dateStr = Utilities.formatDate(yesterday, "Asia/Dubai", "dd/MM/yyyy");
  const datePrefix = Utilities.formatDate(yesterday, "Asia/Dubai", "yyyy-MM-dd"); // for matching

  _aggregateDate(dateStr, datePrefix);
}

/**
 * Backfill a specific date. Call from the editor with a date string.
 * Example: aggregateDate("27/04/2026")
 */
function aggregateDate(ddmmyyyy) {
  // Parse dd/MM/yyyy → yyyy-MM-dd prefix for row matching
  const parts = ddmmyyyy.split("/");
  const datePrefix = `${parts[2]}-${parts[1]}-${parts[0]}`;
  _aggregateDate(ddmmyyyy, datePrefix);
}

function _aggregateDate(dateStr, datePrefix) {
  const rawSheet = getRawSheet();
  const data = rawSheet.getDataRange().getValues();

  if (data.length < 2) {
    Logger.log("No raw events found.");
    return;
  }

  const headers = data[0];
  const tsIdx    = headers.indexOf("timestamp");
  const mktIdx   = headers.indexOf("market");
  const evtIdx   = headers.indexOf("event_type");
  const sessIdx  = headers.indexOf("session_id");

  // Aggregate per market
  const stats = {};
  MARKETS.forEach(m => {
    stats[m] = { sessions: new Set(), add_to_cart: 0, reached_checkout: 0, completed_checkout: 0 };
  });

  data.slice(1).forEach(row => {
    const ts  = String(row[tsIdx] || "");
    const mkt = String(row[mktIdx] || "").trim().toUpperCase();
    const evt = String(row[evtIdx] || "").trim();
    const sid = String(row[sessIdx] || "").trim();

    // Match rows for this date (ISO timestamp starts with yyyy-MM-dd)
    if (!ts.startsWith(datePrefix)) return;
    if (!stats[mkt]) return;

    if (evt === "page_viewed" && sid) {
      stats[mkt].sessions.add(sid);
    } else if (evt === "product_added_to_cart") {
      stats[mkt].add_to_cart++;
    } else if (evt === "checkout_started") {
      stats[mkt].reached_checkout++;
    } else if (evt === "checkout_completed") {
      stats[mkt].completed_checkout++;
    }
  });

  // Append to each market's summary tab (skip if already exists for this date)
  MARKETS.forEach(market => {
    const s       = stats[market];
    const sessions = s.sessions.size;
    const cvr     = sessions > 0 ? (s.completed_checkout / sessions) : 0;
    const cvrPct  = (cvr * 100).toFixed(2) + "%";

    const summarySheet = getSummarySheet(market);
    const existing = summarySheet.getDataRange().getValues();

    // Check if we already have a row for this date
    const alreadyExists = existing.slice(1).some(r => String(r[0]).trim() === dateStr);
    if (alreadyExists) {
      Logger.log(`${market} — ${dateStr} already aggregated, skipping.`);
      return;
    }

    summarySheet.appendRow([
      dateStr,
      market,
      sessions,
      s.add_to_cart,
      s.reached_checkout,
      s.completed_checkout,
      cvrPct,
    ]);

    Logger.log(`${market} — ${dateStr}: sessions=${sessions}, atc=${s.add_to_cart}, checkout=${s.reached_checkout}, orders=${s.completed_checkout}, cvr=${cvrPct}`);
  });
}
