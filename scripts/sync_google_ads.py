"""
sync_google_ads.py
==================

Pulls monthly Google Ads performance metrics (spend, clicks, impressions,
CTR, CPC) from the three Wisewell ad accounts (UAE, KSA, USA) and writes
the full historical series to a "Google Ads - Claude" tab in the Wisewell
User Base Data spreadsheet.

Fully idempotent — every run rebuilds the entire data area from the API.

────────────────────────────────────────────────────────────────────────────
Credentials
────────────────────────────────────────────────────────────────────────────
Reads from google_ads_token.json in the project root (gitignored).
Requires GOOGLE_SERVICE_ACCOUNT env var (same service account used by the
dashboard) for writing to the sheet.

Local invocation:
  cd "/Users/sami/Desktop/Claude Code"
  export GOOGLE_SERVICE_ACCOUNT="$(cat ~/.config/wisewell-sa.json)"
  python3 scripts/sync_google_ads.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

GADS_API_VERSION  = "v19"
GADS_BASE         = f"https://googleads.googleapis.com/{GADS_API_VERSION}"
TOKEN_URL         = "https://oauth2.googleapis.com/token"

PROJECT_ROOT      = Path(__file__).resolve().parent.parent
TOKEN_FILE        = PROJECT_ROOT / "google_ads_token.json"

DEFAULT_SHEET_ID  = "1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4"
DEFAULT_TAB_NAME  = "Google Ads - Claude"

SHEET_SCOPES      = ["https://www.googleapis.com/auth/spreadsheets"]

# Pegged FX to USD (same as rest of dashboard)
USD_RATES: dict[str, float] = {
    "USD": 1.0,
    "AED": 1 / 3.6725,
    "SAR": 1 / 3.7500,
}

# Pull full history from Jan 2023
HISTORY_START = "2023-01-01"

HEADERS = [
    "Month", "Market",
    "Spend (USD)", "Clicks", "Impressions", "CTR (%)", "CPC (USD)",
]


# ── Auth helpers ─────────────────────────────────────────────────────────────

def _load_token_file() -> dict:
    if not TOKEN_FILE.exists():
        log.error("Token file not found: %s", TOKEN_FILE)
        sys.exit(1)
    with open(TOKEN_FILE) as f:
        return json.load(f)


def _get_access_token(creds: dict) -> str:
    """Exchange refresh token for a fresh access token."""
    r = requests.post(TOKEN_URL, data={
        "grant_type":    "refresh_token",
        "refresh_token": creds["refresh_token"],
        "client_id":     creds["client_id"],
        "client_secret": creds["client_secret"],
    }, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def _sheets_service(sa_json: str):
    info  = json.loads(sa_json) if isinstance(sa_json, str) else sa_json
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=SHEET_SCOPES
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


# ── Google Ads API ───────────────────────────────────────────────────────────

GAQL = """
SELECT
  segments.month,
  metrics.cost_micros,
  metrics.clicks,
  metrics.impressions,
  metrics.ctr,
  metrics.average_cpc
FROM campaign
WHERE
  segments.date >= '{start}'
  AND segments.date <= '{end}'
  AND campaign.status != 'REMOVED'
"""


def _fetch_market(
    customer_id: str,
    manager_id: str,
    developer_token: str,
    access_token: str,
    market: str,
) -> list[dict]:
    """
    Query one ad account for monthly metrics.
    Returns list of dicts keyed by HEADERS (minus Market column).
    """
    today      = datetime.utcnow().strftime("%Y-%m-%d")
    query      = GAQL.format(start=HISTORY_START, end=today).strip()
    url        = f"{GADS_BASE}/customers/{customer_id}/googleAds:search"
    headers    = {
        "Authorization":    f"Bearer {access_token}",
        "developer-token":  developer_token,
        "login-customer-id": manager_id,
        "Content-Type":     "application/json",
    }

    monthly: dict[str, dict] = {}
    page_token: str | None = None

    while True:
        body: dict = {"query": query, "pageSize": 10000}
        if page_token:
            body["pageToken"] = page_token

        resp = requests.post(url, headers=headers, json=body, timeout=60)
        if not resp.ok:
            log.error("Google Ads API error (%s) for %s: %s",
                      resp.status_code, market, resp.text[:500])
            return []

        data      = resp.json()
        results   = data.get("results", [])
        page_token = data.get("nextPageToken")

        for row in results:
            month       = row["segments"]["month"]           # "YYYY-MM-01"
            cost_micros = int(row["metrics"].get("costMicros", 0))
            clicks      = int(row["metrics"].get("clicks", 0))
            impressions = int(row["metrics"].get("impressions", 0))
            ctr_raw     = float(row["metrics"].get("ctr", 0))       # 0–1
            cpc_micros  = int(row["metrics"].get("averageCpc", 0))

            if month not in monthly:
                monthly[month] = {
                    "cost_micros": 0, "clicks": 0,
                    "impressions": 0, "ctr_sum": 0.0,
                    "ctr_count": 0, "cpc_micros_sum": 0, "cpc_count": 0,
                }
            m = monthly[month]
            m["cost_micros"]    += cost_micros
            m["clicks"]         += clicks
            m["impressions"]    += impressions
            if ctr_raw > 0:
                m["ctr_sum"]    += ctr_raw
                m["ctr_count"]  += 1
            if cpc_micros > 0:
                m["cpc_micros_sum"] += cpc_micros
                m["cpc_count"]      += 1

        if not page_token:
            break

    # Determine account currency via a lightweight API call
    cur_url  = f"{GADS_BASE}/customers/{customer_id}"
    cur_resp = requests.get(
        cur_url,
        headers=headers,
        params={"fields": "currencyCode"},
        timeout=30,
    )
    currency = "USD"
    if cur_resp.ok:
        currency = cur_resp.json().get("currencyCode", "USD")
    fx = USD_RATES.get(currency, 1.0)
    log.info("  %s: currency=%s  fx=%.5f  months=%d", market, currency, fx, len(monthly))

    rows = []
    for month in sorted(monthly):
        m     = monthly[month]
        spend = (m["cost_micros"] / 1_000_000) * fx
        cpc   = ((m["cpc_micros_sum"] / max(m["cpc_count"], 1)) / 1_000_000) * fx
        ctr   = (m["ctr_sum"] / max(m["ctr_count"], 1)) * 100  # as percentage
        rows.append({
            "month":       month[:7],  # "YYYY-MM"
            "market":      market,
            "spend_usd":   round(spend, 2),
            "clicks":      m["clicks"],
            "impressions": m["impressions"],
            "ctr_pct":     round(ctr, 4),
            "cpc_usd":     round(cpc, 4),
        })
    return rows


# ── Sheet writer ─────────────────────────────────────────────────────────────

def _ensure_tab(svc, spreadsheet_id: str, tab_name: str) -> int:
    meta = svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for s in meta["sheets"]:
        if s["properties"]["title"] == tab_name:
            return s["properties"]["sheetId"]
    body = {"requests": [{"addSheet": {"properties": {"title": tab_name}}}]}
    resp = svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body=body
    ).execute()
    return resp["replies"][0]["addSheet"]["properties"]["sheetId"]


def _write_sheet(svc, spreadsheet_id: str, tab_name: str, rows: list[list]) -> None:
    sheet_id = _ensure_tab(svc, spreadsheet_id, tab_name)
    full_range = f"'{tab_name}'!A1"

    # Clear existing data
    svc.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id, range=f"'{tab_name}'"
    ).execute()

    # Write header + data
    all_rows = [HEADERS] + rows
    svc.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=full_range,
        valueInputOption="USER_ENTERED",
        body={"values": all_rows},
    ).execute()

    # Bold header row
    svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0, "endRowIndex": 1,
                },
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }
        }]},
    ).execute()

    log.info("Wrote %d data rows to '%s'", len(rows), tab_name)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    creds  = _load_token_file()
    sa_env = os.environ.get("GOOGLE_SERVICE_ACCOUNT")
    if not sa_env:
        log.error("Missing env var: GOOGLE_SERVICE_ACCOUNT")
        return 1

    sheet_id = os.environ.get("SHEET_ID", DEFAULT_SHEET_ID)
    tab_name = os.environ.get("TAB_NAME", DEFAULT_TAB_NAME)

    log.info("Fetching fresh access token...")
    access_token = _get_access_token(creds)

    manager_id      = creds["manager_id"]
    developer_token = creds["developer_token"]
    customer_ids    = creds["customer_ids"]

    all_rows: list[list] = []
    for market, customer_id in customer_ids.items():
        log.info("Querying Google Ads — %s (customer %s)...", market, customer_id)
        rows = _fetch_market(
            customer_id=customer_id,
            manager_id=manager_id,
            developer_token=developer_token,
            access_token=access_token,
            market=market,
        )
        for r in rows:
            all_rows.append([
                r["month"], r["market"],
                r["spend_usd"], r["clicks"], r["impressions"],
                r["ctr_pct"], r["cpc_usd"],
            ])

    # Sort by month then market
    all_rows.sort(key=lambda r: (r[0], r[1]))

    log.info("Writing %d rows to sheet...", len(all_rows))
    svc = _sheets_service(sa_env)
    _write_sheet(svc, sheet_id, tab_name, all_rows)

    log.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
