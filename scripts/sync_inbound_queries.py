"""
sync_inbound_queries.py
========================

Pulls daily Freshchat conversation counts and Freshdesk ticket counts that are
labeled as "Product Information" and writes them to the "Inbound Queries" tab
of the Customer Support tracking sheet.

Runs twice daily via GitHub Actions (see .github/workflows/sync-inbound-queries.yml).
Idempotent — every run rebuilds the trailing window from the API.

────────────────────────────────────────────────────────────────────────────
Required environment variables
────────────────────────────────────────────────────────────────────────────

  FRESHCHAT_API_TOKEN        Freshchat JWT bearer token (scope: reports:extract,
                             conversation:read).
  FRESHCHAT_DOMAIN           e.g. "wisewell-team-8d9549c6b6cb25916984096.freshchat.com"
  FRESHDESK_API_KEY          Freshdesk API key (basic auth, password "X")
  FRESHDESK_DOMAIN           e.g. "wisewellteam" (no .freshdesk.com suffix)
  GOOGLE_SERVICE_ACCOUNT     JSON string of a service account key with edit
                             access to the target spreadsheet.

Optional environment variables
  INBOUND_QUERIES_SHEET_ID   Override default spreadsheet ID
  INBOUND_QUERIES_TAB        Override tab name (default: "Inbound Queries")
  LOOKBACK_DAYS              Days to refresh on each run (default: 30)
  PRODUCT_INFO_LABEL         cf_type value to count (default: "Product information")
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import time
import zipfile
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ── Constants ────────────────────────────────────────────────────────────────
DEFAULT_SHEET_ID = "1zvnS62G88U17sxru4zTVrnzaORL0H4Am-T3Witxe_2M"
DEFAULT_TAB = "Inbound Queries"
DEFAULT_LOOKBACK = 15
DEFAULT_LABEL = "Product information"
DATE_COL_HEADER_HUMAN = "%-d %b, %Y"  # e.g. "1 Apr, 2026"

CONV_FETCH_CONCURRENCY = 15
REPORT_POLL_INTERVAL_S = 15
REPORT_TIMEOUT_S = 600  # 10 min

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("inbound_queries")


# ── Env helpers ──────────────────────────────────────────────────────────────
def env(name: str, required: bool = True, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        log.error("Missing required env var: %s", name)
        sys.exit(1)
    return val or ""


# ── Freshchat: report extraction ─────────────────────────────────────────────
def fc_extract_conversation_created(token: str, domain: str, start: datetime, end: datetime) -> dict:
    """POST /v2/reports/raw — kicks off async report. Returns the response JSON."""
    url = f"https://{domain}/v2/reports/raw"
    body = {
        "start": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "event": "Conversation-Created",
        "format": "CSV",
    }
    log.info("Extracting Conversation-Created report %s → %s", body["start"], body["end"])
    r = requests.post(url, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def fc_poll_report(token: str, domain: str, report_id: str) -> dict:
    """Poll until COMPLETED. Returns the final report response with download links."""
    url = f"https://{domain}/v2/reports/raw/{report_id}"
    deadline = time.time() + REPORT_TIMEOUT_S
    while time.time() < deadline:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        r.raise_for_status()
        data = r.json()
        status = data.get("status")
        if status == "COMPLETED":
            return data
        if status == "FAILED":
            raise RuntimeError(f"Report generation failed: {data}")
        log.info("  Report %s status=%s — waiting...", report_id, status)
        time.sleep(REPORT_POLL_INTERVAL_S)
    raise TimeoutError(f"Report {report_id} did not complete in {REPORT_TIMEOUT_S}s")


def fc_download_report(report_data: dict) -> list[dict]:
    """Download all sub-report zips and parse the CSVs.
    Returns list of {conversation_id, created_at}."""
    rows: list[dict] = []
    for link_obj in report_data.get("links", []):
        url = link_obj["link"]["href"]
        log.info("  Downloading sub-report %s → %s", link_obj["from"], link_obj["to"])
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            for name in zf.namelist():
                with zf.open(name) as f:
                    text = f.read().decode("utf-8")
                rows.extend(_parse_conversation_created_csv(text))
    log.info("  Parsed %d conversation rows from report", len(rows))
    return rows


def _parse_conversation_created_csv(text: str) -> list[dict]:
    """Parse the Conversation-Created CSV. Handles quoted fields with embedded commas."""
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if len(lines) < 2:
        return []
    header = _parse_csv_row(lines[0])
    try:
        id_idx = header.index("conversation_id")
        date_idx = header.index("created_at")
    except ValueError:
        log.warning("Expected columns missing in report CSV: %s", header)
        return []
    out = []
    for line in lines[1:]:
        fields = _parse_csv_row(line)
        if id_idx < len(fields) and date_idx < len(fields):
            cid = fields[id_idx]
            created = fields[date_idx]
            if cid and created:
                out.append({"id": cid, "created_at": created})
    return out


def _parse_csv_row(line: str) -> list[str]:
    """Simple CSV parser respecting double-quoted fields (no escape chars)."""
    fields: list[str] = []
    current = []
    in_quotes = False
    for ch in line:
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == "," and not in_quotes:
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields


# ── Freshchat: per-conversation lookup ───────────────────────────────────────
def fc_fetch_conversations_concurrent(token: str, domain: str, conv_ids: list[str]) -> dict[str, str | None]:
    """For each conversation_id, GET /v2/conversations/{id} and return the cf_type.
    Uses a thread pool for concurrency."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    headers = {"Authorization": f"Bearer {token}"}
    base_url = f"https://{domain}/v2/conversations/"

    def fetch_one(cid: str) -> tuple[str, str | None]:
        try:
            r = requests.get(base_url + cid, headers=headers, timeout=15)
            if r.status_code != 200:
                return cid, None
            props = (r.json() or {}).get("properties") or {}
            return cid, props.get("cf_type")
        except Exception as exc:  # noqa: BLE001
            log.debug("  fetch failed for %s: %s", cid, exc)
            return cid, None

    result: dict[str, str | None] = {}
    with ThreadPoolExecutor(max_workers=CONV_FETCH_CONCURRENCY) as pool:
        futures = [pool.submit(fetch_one, cid) for cid in conv_ids]
        done = 0
        for fut in as_completed(futures):
            cid, cf_type = fut.result()
            result[cid] = cf_type
            done += 1
            if done % 200 == 0:
                log.info("  Enriched %d / %d conversations", done, len(conv_ids))
    log.info("  Enriched %d / %d conversations (final)", len(result), len(conv_ids))
    return result


# ── Freshdesk: ticket counts by date ─────────────────────────────────────────
def fd_count_product_info_per_day(api_key: str, domain: str, start: datetime, end: datetime, label: str) -> dict[str, int]:
    """Use Freshdesk search API to count tickets per day with type=label."""
    headers = {"Authorization": "Basic " + b64encode(f"{api_key}:X".encode()).decode()}
    base_url = f"https://{domain}.freshdesk.com/api/v2/search/tickets"

    counts: dict[str, int] = {}
    cur = start.date()
    end_d = end.date()
    while cur < end_d:
        next_d = cur + timedelta(days=1)
        query = f"\"type:'{label}' AND created_at:>'{cur.isoformat()}' AND created_at:<'{next_d.isoformat()}'\""
        try:
            r = requests.get(base_url, params={"query": query}, headers=headers, timeout=20)
            if r.status_code == 200:
                counts[cur.isoformat()] = r.json().get("total", 0)
            else:
                log.warning("Freshdesk search failed for %s (status %s): %s", cur, r.status_code, r.text[:200])
                counts[cur.isoformat()] = 0
        except Exception as exc:  # noqa: BLE001
            log.warning("Freshdesk error for %s: %s", cur, exc)
            counts[cur.isoformat()] = 0
        cur = next_d
        time.sleep(0.25)  # gentle on rate limits
    return counts


# ── Aggregation ──────────────────────────────────────────────────────────────
def aggregate_chats_by_date(
    rows: list[dict],
    cf_types: dict[str, str | None],
    label: str,
) -> dict[str, int]:
    """Count conversations per UTC date where cf_type matches the label (case-insensitive)."""
    target = label.strip().lower()
    by_date: dict[str, int] = {}
    for row in rows:
        cf = (cf_types.get(row["id"]) or "").strip().lower()
        if cf != target:
            continue
        date_part = row["created_at"][:10]
        if date_part:
            by_date[date_part] = by_date.get(date_part, 0) + 1
    return by_date


# ── Google Sheets ────────────────────────────────────────────────────────────
def sheets_client():
    sa_json = env("GOOGLE_SERVICE_ACCOUNT")
    info = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def read_date_column(svc, sheet_id: str, tab: str, max_rows: int = 200) -> list[str]:
    """Read column A (dates) from row 2 down. Returns list of strings as they appear in the sheet."""
    rng = f"'{tab}'!A2:A{max_rows + 1}"
    res = svc.spreadsheets().values().get(spreadsheetId=sheet_id, range=rng).execute()
    return [row[0] if row else "" for row in res.get("values", [])]


def parse_sheet_date(s: str) -> str | None:
    """Parse '1 Apr, 2026' → '2026-04-01' (ISO). Returns None on failure."""
    s = s.strip()
    for fmt in ("%d %b, %Y", "%d %B, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def write_counts(
    svc,
    sheet_id: str,
    tab: str,
    chats_by_date: dict[str, int],
    tickets_by_date: dict[str, int],
) -> int:
    """Match each row in column A by date and update columns B (chats) and C (tickets)."""
    sheet_dates = read_date_column(svc, sheet_id, tab)
    log.info("Read %d date rows from sheet", len(sheet_dates))

    updates: list[dict[str, Any]] = []
    matched = 0
    for idx, raw in enumerate(sheet_dates):
        iso = parse_sheet_date(raw) if raw else None
        if not iso:
            continue
        chats = chats_by_date.get(iso)
        tickets = tickets_by_date.get(iso)
        # Only write rows that have at least one source value to avoid clobbering
        # historical or future blank rows.
        if chats is None and tickets is None:
            continue
        row_num = idx + 2  # offset for header
        updates.append({
            "range": f"'{tab}'!B{row_num}:C{row_num}",
            "values": [[chats if chats is not None else "", tickets if tickets is not None else ""]],
        })
        matched += 1

    if not updates:
        log.info("No rows matched the data — nothing to write.")
        return 0

    body = {"valueInputOption": "RAW", "data": updates}
    svc.spreadsheets().values().batchUpdate(spreadsheetId=sheet_id, body=body).execute()
    log.info("Wrote %d rows to '%s'", matched, tab)
    return matched


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> int:
    fc_token = env("FRESHCHAT_API_TOKEN")
    fc_domain = env("FRESHCHAT_DOMAIN")
    fd_key = env("FRESHDESK_API_KEY")
    fd_domain = env("FRESHDESK_DOMAIN")
    sheet_id = env("INBOUND_QUERIES_SHEET_ID", required=False, default=DEFAULT_SHEET_ID)
    tab = env("INBOUND_QUERIES_TAB", required=False, default=DEFAULT_TAB)
    lookback = int(env("LOOKBACK_DAYS", required=False, default=str(DEFAULT_LOOKBACK)))
    label = env("PRODUCT_INFO_LABEL", required=False, default=DEFAULT_LABEL)

    now = datetime.now(timezone.utc)
    end = now.replace(microsecond=0)
    start = (now - timedelta(days=lookback)).replace(hour=0, minute=0, second=0, microsecond=0)
    log.info("Refresh window: %s → %s (%d days)", start.isoformat(), end.isoformat(), lookback)

    # 1. Generate Freshchat report
    extract = fc_extract_conversation_created(fc_token, fc_domain, start, end)
    report_id = extract["id"]
    log.info("Report ID: %s — polling for completion...", report_id)
    report = fc_poll_report(fc_token, fc_domain, report_id)

    # 2. Download and parse
    rows = fc_download_report(report)
    if not rows:
        log.warning("No conversation rows in report. Sheet not updated.")
        return 0

    # 3. Enrich each conversation with cf_type
    conv_ids = [r["id"] for r in rows]
    cf_types = fc_fetch_conversations_concurrent(fc_token, fc_domain, conv_ids)
    chats_by_date = aggregate_chats_by_date(rows, cf_types, label)
    log.info("Chat counts (cf_type='%s') by date: %d unique dates, %d total chats",
             label, len(chats_by_date), sum(chats_by_date.values()))

    # 4. Pull ticket counts
    tickets_by_date = fd_count_product_info_per_day(fd_key, fd_domain, start, end, label)
    log.info("Ticket counts: %d unique dates, %d total tickets",
             len(tickets_by_date), sum(tickets_by_date.values()))

    # 5. Write to sheet
    svc = sheets_client()
    rows_written = write_counts(svc, sheet_id, tab, chats_by_date, tickets_by_date)
    log.info("Done — %d rows written to '%s'", rows_written, tab)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        log.exception("sync_inbound_queries.py failed")
        sys.exit(1)
