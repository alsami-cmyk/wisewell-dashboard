"""
sync_marketing_spend.py
=======================

Pulls monthly Meta Ads spend from the 3 Wisewell ad accounts (UAE, KSA, USA),
converts to USD when needed, and writes the full historical series to the
"Marketing Spend - Claude" tab in the Wisewell User Base Data spreadsheet.

Designed to be invoked by a Cowork scheduled task (or any cron). The script
is fully idempotent: every run rebuilds the entire data area of the tab from
the authoritative API source. The "Marketing Spend" tab (manual, no suffix)
is never touched.

Phase 1: Meta only. Google Ads columns are written as 0.00 placeholders
until phase 2 wires up the Google Ads API.

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
  TAB_NAME                   Override default tab name
                             (default: "Marketing Spend - Claude")

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
    DUBAI_TZ = None  # logging will fall back to local time

# ── Constants ────────────────────────────────────────────────────────────────
META_API_VERSION = "v19.0"
META_GRAPH       = f"https://graph.facebook.com/{META_API_VERSION}"

DEFAULT_SHEET_ID = "1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4"
DEFAULT_TAB_NAME = "Marketing Spend - Claude"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Pegged-currency conversion to USD. Do not fetch live rates — these are
# functionally fixed by central-bank pegs.
#   1 AED = 0.27226 USD  (peg: 3.6725 AED / USD)
#   1 SAR = 0.26667 USD  (peg: 3.7500 SAR / USD)
USD_RATES: dict[str, float] = {
    "USD": 1.0,
    "AED": 0.27226,
    "SAR": 0.26667,
}

# Sheet header — must match the existing layout exactly.
HEADER: list[str] = [
    "Month", "Total Spend",
    "UAE", "KSA", "USA",
    "META", "Google",
    "UAE - META", "KSA - META", "USA - Facebook",
    "UAE - Google", "KSA - Google", "USA - Google",
]

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sync_marketing_spend")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _normalize_account_id(raw: str) -> str:
    """Ensure 'act_' prefix on a Meta ad account ID."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not raw.startswith("act_"):
        raw = f"act_{raw}"
    return raw


def _ym_to_label(year_month: str) -> str:
    """Convert 'YYYY-MM' → 'Mmm-YY' (e.g. '2026-04' → 'Apr-26')."""
    return datetime.strptime(year_month, "%Y-%m").strftime("%b-%y")


def _fmt_money(v: float) -> str:
    """Two-decimal with thousands separator, no $ sign."""
    return f"{v:,.2f}"


def _now_dubai_str() -> str:
    if DUBAI_TZ is not None:
        return datetime.now(DUBAI_TZ).strftime("%Y-%m-%d %H:%M UAE")
    return datetime.now().strftime("%Y-%m-%d %H:%M LOCAL")


# ─────────────────────────────────────────────────────────────────────────────
# Meta Ads API
# ─────────────────────────────────────────────────────────────────────────────
def get_account_currency(account_id: str, token: str) -> str:
    """Returns the billing currency of a Meta ad account ('USD', 'AED', ...)."""
    r = requests.get(
        f"{META_GRAPH}/{account_id}",
        params={"fields": "currency", "access_token": token},
        timeout=15,
    )
    r.raise_for_status()
    return (r.json().get("currency") or "USD").upper()


def get_monthly_spend(account_id: str, token: str) -> dict[str, float]:
    """
    Returns {YYYY-MM: spend_in_account_currency}.

    Uses Meta Insights with time_increment=monthly + date_preset=maximum
    to pull every month with non-zero activity in one request (paginated).
    """
    out: dict[str, float] = {}
    url: str | None = f"{META_GRAPH}/{account_id}/insights"
    params: dict[str, Any] | None = {
        "level":           "account",
        "fields":          "spend",
        "time_increment":  "monthly",
        "date_preset":     "maximum",
        "limit":           500,
        "access_token":    token,
    }

    while url:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        for row in body.get("data", []):
            spend = float(row.get("spend", 0) or 0)
            ym    = (row.get("date_start") or "")[:7]  # YYYY-MM
            if not ym:
                continue
            out[ym] = out.get(ym, 0.0) + spend
        # Cursor pagination — Meta returns full URL with embedded params
        url = body.get("paging", {}).get("next")
        params = None

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Sheets writer
# ─────────────────────────────────────────────────────────────────────────────
def write_sheet(
    sa_json: str,
    sheet_id: str,
    tab_name: str,
    rows: list[list[str]],
) -> None:
    """
    Replace the data area of `tab_name` with HEADER + rows.
    Keeps the tab itself; never touches other tabs.
    """
    creds_info = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    svc   = build("sheets", "v4", credentials=creds, cache_discovery=False)

    range_clear = f"'{tab_name}'!A2:M"
    svc.spreadsheets().values().clear(
        spreadsheetId=sheet_id, range=range_clear,
    ).execute()

    body = {"values": [HEADER] + rows}
    svc.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{tab_name}'!A1",
        # RAW (not USER_ENTERED): Sheets must NOT parse month labels like
        # "Mar-23" as dates — it would silently coerce them to "Mar 23 of
        # current year", remapping every historical month to a 2026 date.
        # Numeric strings ("1,234.56") are read back fine by the dashboard,
        # which has its own comma-stripping numeric coercion.
        valueInputOption="RAW",
        body=body,
    ).execute()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
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

    sheet_id = os.environ.get("SHEET_ID", DEFAULT_SHEET_ID)
    tab_name = os.environ.get("TAB_NAME", DEFAULT_TAB_NAME)

    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT")
    if not sa_json:
        log.error("Missing required env var: GOOGLE_SERVICE_ACCOUNT")
        return 1

    # ── Step 1: detect each account's billing currency ──
    currencies: dict[str, str] = {}
    for market, acc in accounts.items():
        try:
            cur = get_account_currency(acc, token)
        except requests.HTTPError as e:
            log.error("Currency lookup failed for %s (%s): %s", market, acc, e)
            return 2
        if cur not in USD_RATES:
            log.error(
                "Unknown billing currency '%s' on %s (%s). "
                "Add it to USD_RATES or fix the account currency.",
                cur, market, acc,
            )
            return 2
        currencies[market] = cur
        log.info("Meta %s billing currency: %s", market, cur)

    # ── Step 2: pull monthly spend per account, convert to USD ──
    spend_by_market: dict[str, dict[str, float]] = {}
    for market, acc in accounts.items():
        try:
            local = get_monthly_spend(acc, token)
        except requests.HTTPError as e:
            log.error("Insights fetch failed for %s (%s): %s", market, acc, e)
            spend_by_market[market] = {}
            continue
        rate = USD_RATES[currencies[market]]
        spend_by_market[market] = {
            ym: round(amt * rate, 2) for ym, amt in local.items()
        }
        total_usd = sum(spend_by_market[market].values())
        log.info(
            "Meta %s: %d months · total $%s",
            market, len(local), _fmt_money(total_usd),
        )

    # ── Step 3: collect every month that appears across accounts ──
    months: set[str] = set()
    for d in spend_by_market.values():
        months.update(d.keys())
    if not months:
        log.warning("No spend data returned from any Meta account — aborting writes.")
        return 0

    # ── Step 4: build sheet rows (sorted ascending) ──
    rows: list[list[str]] = []
    for ym in sorted(months):
        meta_uae = spend_by_market["UAE"].get(ym, 0.0)
        meta_ksa = spend_by_market["KSA"].get(ym, 0.0)
        meta_usa = spend_by_market["USA"].get(ym, 0.0)
        meta_total = meta_uae + meta_ksa + meta_usa

        # Google placeholders — populated by phase 2
        google_uae = 0.0
        google_ksa = 0.0
        google_usa = 0.0
        google_total = google_uae + google_ksa + google_usa

        uae_total = meta_uae + google_uae
        ksa_total = meta_ksa + google_ksa
        usa_total = meta_usa + google_usa
        grand_total = meta_total + google_total

        rows.append([
            _ym_to_label(ym),
            _fmt_money(grand_total),
            _fmt_money(uae_total),
            _fmt_money(ksa_total),
            _fmt_money(usa_total),
            _fmt_money(meta_total),
            _fmt_money(google_total),
            _fmt_money(meta_uae),
            _fmt_money(meta_ksa),
            _fmt_money(meta_usa),
            _fmt_money(google_uae),
            _fmt_money(google_ksa),
            _fmt_money(google_usa),
        ])

    # ── Step 5: write to the sheet ──
    try:
        write_sheet(sa_json, sheet_id, tab_name, rows)
    except HttpError as e:
        log.error("Sheets API write failed: %s", e)
        return 3
    except Exception as e:
        log.error("Unexpected sheet-write failure: %s", e)
        return 3

    latest = rows[-1]
    log.info(
        "[WW Spend Sync] %s | OK | months=%d | latest=%s $%s | "
        "currencies={UAE:%s, KSA:%s, USA:%s}",
        _now_dubai_str(),
        len(rows),
        latest[0], latest[1],
        currencies["UAE"], currencies["KSA"], currencies["USA"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
