/**
 * Wisewell Store Events Collector
 *
 * Deploy as a Google Apps Script Web App:
 *   Extensions → Apps Script → paste this → Deploy → New deployment
 *   Type: Web app | Execute as: Me | Who has access: Anyone
 *   Copy the deployment URL → use as ENDPOINT in the pixel and webhook scripts
 *
 * Sheet tabs used:
 *   "Store Events - Live"  ← pixel + webhook events (this script writes here)
 */

const SHEET_ID  = "1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4";
const TAB_NAME  = "Store Events - Live";
const HEADERS   = [
  "timestamp", "market", "source", "event_type",
  "session_id", "page_path", "product_id", "product_title",
  "value", "currency", "order_id", "checkout_id",
];

function getOrCreateSheet() {
  const ss   = SpreadsheetApp.openById(SHEET_ID);
  let sheet  = ss.getSheetByName(TAB_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(TAB_NAME);
    sheet.appendRow(HEADERS);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    const sheet = getOrCreateSheet();

    // Support both single event and batch array
    const events = Array.isArray(body) ? body : [body];

    events.forEach(ev => {
      sheet.appendRow([
        ev.timestamp   || new Date().toISOString(),
        ev.market      || "",
        ev.source      || "",     // "pixel" or "webhook"
        ev.event_type  || "",
        ev.session_id  || "",
        ev.page_path   || "",
        ev.product_id  || "",
        ev.product_title || "",
        ev.value       || "",
        ev.currency    || "",
        ev.order_id    || "",
        ev.checkout_id || "",
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
