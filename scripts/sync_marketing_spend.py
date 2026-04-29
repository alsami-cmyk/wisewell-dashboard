"""
sync_marketing_spend.py
=======================

Pulls monthly Meta Ads metrics from the 3 Wisewell ad accounts (UAE, KSA, USA),
converts to USD when needed, and writes two tabs to the Wisewell User Base Data
spreadsheet:

  "Marketing Spend - Claude"  — existing spend-aggregation tab (unchanged schema,
                                feeds the dashboard's CAC calculation)
  "Meta Ads - Claude"         — new per-market performance tab with spend, clicks,
                                impressions, CTR, and CPC (mirrors the Google Ads
                                tab schema so both channels can be combined easily)

Fully idempotent — every run rebuilds both tabs from the API.

────────────────────────────────────────────────────────────────────────────
Required environment variables
────────────────────────────────────────────────────────────────────────────

  META_ACCESS_TOKEN          System-User token, must have:
                             ads_read, ads_management, business_management
  META_AD_ACCOUNT_UAE        e.g. "act_109540525272698" or "109540525272698"
  META_AD_ACCOUNT_KSA
  META_AD_ACCOUNT_USA
  GOOGLE_SERVICE_ACCOUNT     JSON string of a service account key with edit
                             access to the target spreadsheet

Optional environment variables
  SHEET_ID                   Override default spreadsheet ID
  TAB_NAME                   Override spend tab name
                             (default: "Marketing Spend - Claude")
  PERF_TAB_NAME              Override performance tab name
                             (default: "Meta Ads - Claude")

────────────────────────────────────────────────────────────────────────────
Local invocation (for testing)
────────────────────────────────────────────────────────────────────────────

  export META_ACCESS_TOKEN="EAAB..."
  export META_AD_ACCOUNT_UAE="act_3734539713438684"
  export META_AD_ACCOUNT_KSA="act_283714881224687"
  export META_AD_ACCOUNT_USA="act_944214021797739"
  export GOOGLE_SERVICE_ACCOUNT="$(cat ~/.config/wisewell-sa.json)"
  python scripts/sync_marketing_spend.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from typing import Any

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

try:
    from zoneinfo import ZoneInfo
    DUBAI_TZ = ZoneInfo("Asia/Dubai")
except Exception:
    DUBAI_TZ = None

# ── Constants ────────────────────────────────────────────────────────────────
META_API_VERSION = "v19.0"
META_GRAPH       = f"https://graph.facebook.com/{META_API_VERSION}"

DEFAULT_SHEET_ID    = "1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4"
DEFAULT_SPEND_TAB   = "Marketing Spend - Claude"
DEFAULT_PERF_TAB    = "Meta Ads - Claude"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

USD_RATES: dict[str, float] = {
    "USD": 1.0,
    "AED": 0.27226,   # peg: 3.6725 AED/USD
    "SAR": 0.26667,   # peg: 3.7500 SAR/USD
}

# Spend tab header — unchanged, matches existing dashboard reader
SPEND_HEADER: list[str] = [
    "Month", "Total Spend",
    "UAE", "KSA", "USA",
    "META", "Google",
    "UAE - META", "KSA - META", "USA - Facebook",
    "UAE - Google", "KSA - Google", "USA - Google",
]

# Performance tab header — mirrors Google Ads - Claude schema
PERF_HEADER: list[str] = [
    "Month", "Market",
    "Spend (USD)", "Clicks", "Impressions", "CTR (%)", "CPC (USD)",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sync_marketing_spend")


# ── Helpers ──────────────────────────────────────────────────────────────────
def _normalize_account_id(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not raw.startswith("act_"):
        raw = f"act_{raw}"
    return raw


def _ym_to_label(year_month: str) -> str:
    return datetime.strptime(year_month, "%Y-%m").strftime("%b-%y")


def _fmt_money(v: float) -> str:
    return f"{v:,.2f}"


def _now_dubai_str() -> str:
    if DUBAI_TZ is not None:
        return datetime.now(DUBAI_TZ).strftime("%Y-%m-%d %H:%M UAE")
    return datetime.now().strftime("%Y-%m-%d %H:%M LOCAL")


# ── Meta Ads API ─────────────────────────────────────────────────────────────
def get_account_currency(account_id: str, token: str) -> str:
    r = requests.get(
        f"{META_GRAPH}/{account_id}",
        params={"fields": "currency", "access_token": token},
        timeout=15,
    )
    r.raise_for_status()
    return (r.json().get("currency") or "USD").upper()


def get_monthly_insights(account_id: str, token: str) -> dict[str, dict]:
    """
    Returns {YYYY-MM: {spend, clicks, impressions, ctr_pct, cpc}} in account
    currency (caller converts spend and cpc to USD).

    Meta returns:
      spend       — string, e.g. "1234.56" (account currency)
      clicks      — string integer
      impressions — string integer
      ctr         — string percentage, e.g. "2.5432" means 2.5432%
      cpc         — string, e.g. "0.87" (account currency)
    """
    out: dict[str, dict] = {}
    url: str | None = f"{META_GRAPH}/{account_id}/insights"
    params: dict[str, Any] | None = {
        "level":          "account",
        "fields":         "spend,clicks,impressions,ctr,cpc",
        "time_increment": "monthly",
        "date_preset":    "maximum",
        "limit":          500,
        "access_token":   token,
    }

    while url:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        for row in body.get("data", []):
            ym = (row.get("date_start") or "")[:7]
            if not ym:
                continue
            if ym not in out:
                out[ym] = {
                    "spend": 0.0, "clicks": 0, "impressions": 0,
                    "ctr_pct": 0.0, "ctr_count": 0, "cpc": 0.0, "cpc_count": 0,
                }
            m = out[ym]
            m["spend"]       += float(row.get("spend", 0) or 0)
            m["clicks"]      += int(row.get("clicks", 0) or 0)
            m["impressions"] += int(row.get("impressions", 0) or 0)
            ctr = float(row.get("ctr", 0) or 0)
            if ctr > 0:
                m["ctr_pct"]   += ctr
                m["ctr_count"] += 1
            cpc = float(row.get("cpc", 0) or 0)
            if cpc > 0:
                m["cpc"]       += cpc
                m["cpc_count"] += 1

        url    = body.get("paging", {}).get("next")
        params = None

    # Average the CTR and CPC across the aggregated rows
    for ym, m in out.items():
        m["ctr_pct"] = round(m["ctr_pct"] / max(m["ctr_count"], 1), 4)
        m["cpc"]     = round(m["cpc"]     / max(m["cpc_count"],  1), 6)

    return out


# ── Sheets helpers ────────────────────────────────────────────────────────────
def _build_sheets_svc(sa_json: str):
    info  = json.loads(sa_json) if isinstance(sa_json, str) else sa_json
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _get_sheet_id_by_title(svc, spreadsheet_id: str, title: str) -> int:
    meta = svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for s in meta.get("sheets", []):
        props = s.get("properties", {})
        if props.get("title") == title:
            return int(props["sheetId"])
    raise RuntimeError(f"Tab not found: {title!r}")


def _ensure_tab(svc, spreadsheet_id: str, title: str) -> int:
    """Return sheet ID, creating the tab if it doesn't exist."""
    meta = svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for s in meta.get("sheets", []):
        if s["properties"]["title"] == title:
            return int(s["properties"]["sheetId"])
    resp = svc.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
    ).execute()
    return int(resp["replies"][0]["addSheet"]["properties"]["sheetId"])


def write_spend_tab(
    svc,
    sheet_id: str,
    tab_name: str,
    rows: list[list[str]],
) -> None:
    """Write the spend-aggregation tab (existing schema, unchanged)."""
    inner_id = _get_sheet_id_by_title(svc, sheet_id, tab_name)

    svc.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": [{
            "updateCells": {
                "range": {
                    "sheetId": inner_id,
                    "startRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 13,
                },
                "fields": "userEnteredFormat",
            }
        }]},
    ).execute()

    svc.spreadsheets().values().clear(
        spreadsheetId=sheet_id, range=f"'{tab_name}'!A2:M",
    ).execute()

    svc.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{tab_name}'!A1",
        valueInputOption="RAW",
        body={"values": [SPEND_HEADER] + rows},
    ).execute()
    log.info("Spend tab '%s': wrote %d rows.", tab_name, len(rows))


def write_perf_tab(
    svc,
    sheet_id: str,
    tab_name: str,
    rows: list[list],
) -> None:
    """Write the performance tab (new, per-market schema)."""
    sheet_inner_id = _ensure_tab(svc, sheet_id, tab_name)

    svc.spreadsheets().values().clear(
        spreadsheetId=sheet_id, range=f"'{tab_name}'"
    ).execute()

    svc.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{tab_name}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": [PERF_HEADER] + rows},
    ).execute()

    # Bold header
    svc.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": [{
            "repeatCell": {
                "range": {
                    "sheetId": sheet_inner_id,
                    "startRowIndex": 0, "endRowIndex": 1,
                },
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }
        }]},
    ).execute()
    log.info("Performance tab '%s': wrote %d rows.", tab_name, len(rows))


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> int:
    token = os.environ.get("META_ACCESS_TOKEN")
    if not token:
        log.error("Missing required env var: META_ACCESS_TOKEN")
        return 1

    accounts: dict[str, str] = {
        "UAE": _normalize_account_id(os.environ.get("META_AD_ACCOUNT_UAE", "")),
        "KSA": _normalize_account_id(os.environ.get("META_AD_ACCOUNT_KSA", "")),
        "USA": _normalize_account_id(os.environ.get("META_AD_ACCOUNT_USA", "")),
    }
    for market, acc in accounts.items():
        if acc in ("", "act_"):
            log.error("Missing required env var: META_AD_ACCOUNT_%s", market)
            return 1

    sheet_id     = os.environ.get("SHEET_ID",       DEFAULT_SHEET_ID)
    spend_tab    = os.environ.get("TAB_NAME",        DEFAULT_SPEND_TAB)
    perf_tab     = os.environ.get("PERF_TAB_NAME",   DEFAULT_PERF_TAB)
    sa_json      = os.environ.get("GOOGLE_SERVICE_ACCOUNT")
    if not sa_json:
        log.error("Missing required env var: GOOGLE_SERVICE_ACCOUNT")
        return 1

    # ── Step 1: billing currencies ──
    currencies: dict[str, str] = {}
    for market, acc in accounts.items():
        try:
            cur = get_account_currency(acc, token)
        except requests.HTTPError as e:
            log.error("Currency lookup failed for %s (%s): %s", market, acc, e)
            return 2
        if cur not in USD_RATES:
            log.error("Unknown billing currency '%s' for %s — add to USD_RATES.", cur, market)
            return 2
        currencies[market] = cur
        log.info("Meta %s billing currency: %s", market, cur)

    # ── Step 2: pull full insights per account ──
    insights_by_market: dict[str, dict[str, dict]] = {}
    for market, acc in accounts.items():
        try:
            raw = get_monthly_insights(acc, token)
        except requests.HTTPError as e:
            log.error("Insights fetch failed for %s (%s): %s", market, acc, e)
            insights_by_market[market] = {}
            continue
        rate = USD_RATES[currencies[market]]
        # Convert spend and cpc to USD in-place
        for m in raw.values():
            m["spend_usd"] = round(m["spend"] * rate, 2)
            m["cpc_usd"]   = round(m["cpc"]   * rate, 4)
        insights_by_market[market] = raw
        total_usd = sum(v["spend_usd"] for v in raw.values())
        log.info("Meta %s: %d months · total spend $%s", market, len(raw), _fmt_money(total_usd))

    # ── Step 3: union of all months ──
    months: set[str] = set()
    for d in insights_by_market.values():
        months.update(d.keys())
    if not months:
        log.warning("No data returned from any Meta account — aborting.")
        return 0

    # ── Step 4: build spend tab rows (existing schema) ──
    spend_rows: list[list[str]] = []
    for ym in sorted(months):
        meta_uae = insights_by_market["UAE"].get(ym, {}).get("spend_usd", 0.0)
        meta_ksa = insights_by_market["KSA"].get(ym, {}).get("spend_usd", 0.0)
        meta_usa = insights_by_market["USA"].get(ym, {}).get("spend_usd", 0.0)
        meta_total   = meta_uae + meta_ksa + meta_usa
        google_total = 0.0  # phase 2
        grand_total  = meta_total + google_total

        spend_rows.append([
            _ym_to_label(ym),
            _fmt_money(grand_total),
            _fmt_money(meta_uae),   # UAE total = META only for now
            _fmt_money(meta_ksa),
            _fmt_money(meta_usa),
            _fmt_money(meta_total),
            _fmt_money(google_total),
            _fmt_money(meta_uae),
            _fmt_money(meta_ksa),
            _fmt_money(meta_usa),
            "0.00", "0.00", "0.00",  # Google placeholders
        ])

    # ── Step 5: build performance tab rows (new schema, one row per market) ──
    perf_rows: list[list] = []
    for ym in sorted(months):
        for market in ("UAE", "KSA", "USA"):
            m = insights_by_market[market].get(ym)
            if not m:
                continue
            perf_rows.append([
                ym,
                market,
                m["spend_usd"],
                m["clicks"],
                m["impressions"],
                m["ctr_pct"],
                m["cpc_usd"],
            ])

    # ── Step 6: write both tabs ──
    try:
        svc = _build_sheets_svc(sa_json)
        write_spend_tab(svc, sheet_id, spend_tab, spend_rows)
        write_perf_tab(svc, sheet_id, perf_tab,   perf_rows)
    except HttpError as e:
        log.error("Sheets API write failed: %s", e)
        return 3
    except Exception as e:
        log.error("Unexpected sheet-write failure: %s", e)
        return 3

    log.info(
        "[WW Meta Sync] %s | OK | months=%d | currencies={UAE:%s, KSA:%s, USA:%s}",
        _now_dubai_str(), len(months),
        currencies["UAE"], currencies["KSA"], currencies["USA"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
