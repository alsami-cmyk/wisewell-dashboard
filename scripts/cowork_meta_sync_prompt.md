# Cowork Scheduled Agent — Meta Ads Spend Sync

This is the prompt to paste into Claude Cowork as the instructions for a
recurring scheduled task. The task runs the embedded Python script
`sync_marketing_spend.py` twice daily.

---

## Schedule

Cron: `0 2,13 * * *`
Timezone: `Asia/Dubai`
(Equivalent: every day at 02:00 AM and 01:00 PM UAE time.)

---

## Required Cowork secrets

Add these as named secrets in Cowork before scheduling the task. The agent
reads them as environment variables when executing the script.

| Secret name | What it is | Where to get it |
|---|---|---|
| `META_ACCESS_TOKEN` | Long-lived System User token | Meta Business Settings → Users → System Users → Dashboard → Generate new token (Never expires) |
| `META_AD_ACCOUNT_UAE` | `act_3734539713438684` | Meta Business Settings → Ad Accounts |
| `META_AD_ACCOUNT_KSA` | `act_283714881224687` | same |
| `META_AD_ACCOUNT_USA` | `act_944214021797739` | same |
| `GOOGLE_SERVICE_ACCOUNT` | Full service-account JSON, as a single string | Streamlit Cloud Secrets → copy the value of `GOOGLE_SERVICE_ACCOUNT` |

---

## Agent prompt (paste this into Cowork)

```
ROLE
====
You are an automated data-sync agent. You do NOT interact with humans. You
have a single job: run the Python script below to refresh the
"Marketing Spend - Claude" tab in the Wisewell User Base Data Google
Sheet with current Meta Ads spend.

⚠️ Only ever write to the tab named "Marketing Spend - Claude".
   Never touch the "Marketing Spend" tab — that is a manual reference.

EXECUTION STEPS
===============
1. Confirm the following environment variables are populated from Cowork
   secrets. If any required one is missing, abort and log clearly:
     META_ACCESS_TOKEN          (required)
     META_AD_ACCOUNT_UAE        (required)
     META_AD_ACCOUNT_KSA        (required)
     META_AD_ACCOUNT_USA        (required)
     GOOGLE_SERVICE_ACCOUNT     (required)

2. Ensure the Python sandbox has these packages installed:
     requests
     google-api-python-client
     google-auth
   If any are missing, install them with:
     pip install -q requests google-api-python-client google-auth

3. Save the Python script (provided verbatim below) to a file named
   sync_marketing_spend.py in the working directory.

4. Execute the script:
     python sync_marketing_spend.py
   The script will:
     - Detect each ad account's billing currency
     - Pull monthly spend (entire history) for each account
     - Convert non-USD spend to USD using fixed peg rates
       (AED → 0.27226, SAR → 0.26667)
     - Rebuild the data area of the "Marketing Spend - Claude" tab
       with one row per month, ordered ascending

5. Capture the script's stdout and stderr.

6. Report a one-line summary to the run log:
   - On success (exit 0): repeat the "[WW Spend Sync] ..." line printed
     by the script.
   - On failure (any non-zero exit): log the last 20 lines of stderr.

ERROR RECOVERY
==============
- If the script fails on a transient network error, retry once after 60s.
- Do NOT modify the script's behavior.
- Do NOT write to any other Google Sheet or tab.
- Do NOT interpret the data — your only job is to run the script.

PYTHON SCRIPT
=============
Save the following content verbatim as sync_marketing_spend.py and run it.

────────────────── BEGIN sync_marketing_spend.py ──────────────────
"""
sync_marketing_spend.py — Meta Ads spend → Google Sheet.
See https://github.com/alsami-cmyk/wisewell-dashboard/blob/main/scripts/sync_marketing_spend.py
for the source of truth.
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

META_API_VERSION = "v19.0"
META_GRAPH       = f"https://graph.facebook.com/{META_API_VERSION}"
DEFAULT_SHEET_ID = "1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4"
DEFAULT_TAB_NAME = "Marketing Spend - Claude"
SCOPES           = ["https://www.googleapis.com/auth/spreadsheets"]

USD_RATES: dict[str, float] = {"USD": 1.0, "AED": 0.27226, "SAR": 0.26667}

HEADER: list[str] = [
    "Month", "Total Spend",
    "UAE", "KSA", "USA",
    "META", "Google",
    "UAE - META", "KSA - META", "USA - Facebook",
    "UAE - Google", "KSA - Google", "USA - Google",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sync_marketing_spend")


def _normalize_account_id(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw: return ""
    if not raw.startswith("act_"): raw = f"act_{raw}"
    return raw


def _ym_to_label(year_month: str) -> str:
    return datetime.strptime(year_month, "%Y-%m").strftime("%b-%y")


def _fmt_money(v: float) -> str:
    return f"{v:,.2f}"


def _now_dubai_str() -> str:
    if DUBAI_TZ is not None:
        return datetime.now(DUBAI_TZ).strftime("%Y-%m-%d %H:%M UAE")
    return datetime.now().strftime("%Y-%m-%d %H:%M LOCAL")


def get_account_currency(account_id: str, token: str) -> str:
    r = requests.get(
        f"{META_GRAPH}/{account_id}",
        params={"fields": "currency", "access_token": token},
        timeout=15,
    )
    r.raise_for_status()
    return (r.json().get("currency") or "USD").upper()


def get_monthly_spend(account_id: str, token: str) -> dict[str, float]:
    out: dict[str, float] = {}
    url: str | None = f"{META_GRAPH}/{account_id}/insights"
    params: dict[str, Any] | None = {
        "level":          "account",
        "fields":         "spend",
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
            spend = float(row.get("spend", 0) or 0)
            ym    = (row.get("date_start") or "")[:7]
            if not ym: continue
            out[ym] = out.get(ym, 0.0) + spend
        url = body.get("paging", {}).get("next")
        params = None
    return out


def write_sheet(sa_json: str, sheet_id: str, tab_name: str, rows: list[list[str]]) -> None:
    creds_info = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    svc   = build("sheets", "v4", credentials=creds, cache_discovery=False)

    svc.spreadsheets().values().clear(
        spreadsheetId=sheet_id, range=f"'{tab_name}'!A2:M",
    ).execute()

    body = {"values": [HEADER] + rows}
    svc.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"'{tab_name}'!A1",
        valueInputOption="USER_ENTERED",
        body=body,
    ).execute()


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

    currencies: dict[str, str] = {}
    for market, acc in accounts.items():
        try:
            cur = get_account_currency(acc, token)
        except requests.HTTPError as e:
            log.error("Currency lookup failed for %s (%s): %s", market, acc, e)
            return 2
        if cur not in USD_RATES:
            log.error("Unknown billing currency '%s' on %s (%s)", cur, market, acc)
            return 2
        currencies[market] = cur
        log.info("Meta %s billing currency: %s", market, cur)

    spend_by_market: dict[str, dict[str, float]] = {}
    for market, acc in accounts.items():
        try:
            local = get_monthly_spend(acc, token)
        except requests.HTTPError as e:
            log.error("Insights fetch failed for %s (%s): %s", market, acc, e)
            spend_by_market[market] = {}
            continue
        rate = USD_RATES[currencies[market]]
        spend_by_market[market] = {ym: round(amt * rate, 2) for ym, amt in local.items()}
        total_usd = sum(spend_by_market[market].values())
        log.info("Meta %s: %d months · total $%s", market, len(local), _fmt_money(total_usd))

    months: set[str] = set()
    for d in spend_by_market.values():
        months.update(d.keys())
    if not months:
        log.warning("No spend data returned from any Meta account — aborting writes.")
        return 0

    rows: list[list[str]] = []
    for ym in sorted(months):
        meta_uae = spend_by_market["UAE"].get(ym, 0.0)
        meta_ksa = spend_by_market["KSA"].get(ym, 0.0)
        meta_usa = spend_by_market["USA"].get(ym, 0.0)
        meta_total = meta_uae + meta_ksa + meta_usa

        google_uae = google_ksa = google_usa = 0.0
        google_total = 0.0

        uae_total = meta_uae + google_uae
        ksa_total = meta_ksa + google_ksa
        usa_total = meta_usa + google_usa
        grand_total = meta_total + google_total

        rows.append([
            _ym_to_label(ym),
            _fmt_money(grand_total),
            _fmt_money(uae_total), _fmt_money(ksa_total), _fmt_money(usa_total),
            _fmt_money(meta_total), _fmt_money(google_total),
            _fmt_money(meta_uae), _fmt_money(meta_ksa), _fmt_money(meta_usa),
            _fmt_money(google_uae), _fmt_money(google_ksa), _fmt_money(google_usa),
        ])

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
        _now_dubai_str(), len(rows), latest[0], latest[1],
        currencies["UAE"], currencies["KSA"], currencies["USA"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
────────────────── END sync_marketing_spend.py ──────────────────
```

---

## Setup checklist

| Step | Done? |
|---|---|
| 1. In Cowork, create a "Secrets" entry for each env var listed above | ☐ |
| 2. Verify Cowork has Google Sheets connector connected (it's used by the service account, but Cowork still needs network access) | ☐ |
| 3. Create a new Scheduled Task in Cowork. Paste the agent prompt above as its instructions. | ☐ |
| 4. Set the schedule to `Asia/Dubai`, `0 2,13 * * *` | ☐ |
| 5. Trigger one **manual** run (don't wait for the cron) and check the run log | ☐ |
| 6. Open the sheet → verify the "Marketing Spend - Claude" tab populated with monthly rows | ☐ |
| 7. (Optional) Compare to the original "Marketing Spend" tab to spot discrepancies | ☐ |

## What the dashboard reads

For now, the Streamlit dashboard still reads from the original
**"Marketing Spend"** tab. After you've verified the sync agent is writing
correctly to **"Marketing Spend - Claude"** for a few days, switch the
dashboard loader over by changing one line in `utils.py`:

```python
# utils.py, around line ~650
rows = raw_data.get("Marketing Spend", [])
# ↓ change to:
rows = raw_data.get("Marketing Spend - Claude", [])
```

## Updating the script

The source of truth lives in
`wisewell-dashboard/scripts/sync_marketing_spend.py`. When that file
changes, regenerate the embedded copy in this prompt and update the
Cowork task.
