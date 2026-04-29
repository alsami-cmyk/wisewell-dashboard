# Cowork Scheduled Agent — Meta Ads Sync

Runs twice daily to refresh Meta Ads data in the Wisewell User Base Data sheet.

---

## Schedule

Cron: `0 2,13 * * *`  
Timezone: `Asia/Dubai`  
(02:00 AM and 01:00 PM UAE time)

---

## Required Cowork secrets

| Secret name | What it is |
|---|---|
| `META_ACCESS_TOKEN` | Long-lived System User token (ads_read, ads_management, business_management) |
| `META_AD_ACCOUNT_UAE` | `act_3734539713438684` |
| `META_AD_ACCOUNT_KSA` | `act_283714881224687` |
| `META_AD_ACCOUNT_USA` | `act_944214021797739` |
| `GOOGLE_SERVICE_ACCOUNT` | Full service-account JSON as a single string (copy from Streamlit Cloud secrets) |

---

## Agent prompt (paste into Cowork)

```
ROLE
====
You are an automated data-sync agent. You do NOT interact with humans.
Your only job: run sync_marketing_spend.py to refresh two tabs in the
Wisewell User Base Data Google Sheet:
  - "Marketing Spend - Claude"  (spend aggregation, feeds CAC)
  - "Meta Ads - Claude"         (full performance: spend, clicks, impressions, CTR, CPC)

NEVER touch the "Marketing Spend" tab — that is a manual reference tab.

EXECUTION STEPS
===============
1. Confirm these environment variables are populated from Cowork secrets.
   Abort and log clearly if any required one is missing:
     META_ACCESS_TOKEN          (required)
     META_AD_ACCOUNT_UAE        (required)
     META_AD_ACCOUNT_KSA        (required)
     META_AD_ACCOUNT_USA        (required)
     GOOGLE_SERVICE_ACCOUNT     (required)

2. Install dependencies if needed:
     pip install -q requests google-api-python-client google-auth

3. Download the latest version of the script from GitHub:
     curl -fsSL "https://raw.githubusercontent.com/alsami-cmyk/wisewell-dashboard/main/scripts/sync_marketing_spend.py" -o sync_marketing_spend.py

4. Run the script:
     python sync_marketing_spend.py

   The script will:
     - Detect each Meta ad account's billing currency
     - Pull full monthly history: spend, clicks, impressions, CTR, CPC
     - Convert non-USD values to USD (AED peg 3.6725, SAR peg 3.7500)
     - Write "Marketing Spend - Claude" (spend totals by market)
     - Write "Meta Ads - Claude" (one row per month per market, all metrics)

5. Capture stdout and stderr.

6. Report summary to run log:
   - Success (exit 0): repeat the "[WW Meta Sync] ..." line from stdout.
   - Failure (non-zero): log last 20 lines of stderr.

ERROR RECOVERY
==============
- On transient network error, retry once after 60 seconds.
- Do NOT modify the script.
- Do NOT write to any tab other than "Marketing Spend - Claude" and "Meta Ads - Claude".
```

---

## What the dashboard reads

| Tab | Read by | Notes |
|---|---|---|
| `Marketing Spend - Claude` | `utils.py → load_marketing_spend()` | Feeds CAC on exec summary |
| `Meta Ads - Claude` | Future `pages/paid_ads.py` | Spend + CPC + CTR + impressions + clicks |

## Setup checklist

| Step | Done? |
|---|---|
| Cowork secrets populated for all 5 vars | ☐ |
| Scheduled task created with above prompt | ☐ |
| Manual trigger run — check log for `[WW Meta Sync] ... OK` | ☐ |
| Verify "Meta Ads - Claude" tab appears in sheet with data | ☐ |
