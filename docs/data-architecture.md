# Wisewell Dashboard — Data Architecture & Engineering Reference

> **Audience**: a Claude Code / Cowork agent (or any engineer) building a parallel dashboard
> against the same Google Sheet workbook. This is the ground-truth document — all business rules,
> data filters, metric formulas, and pipeline triggers are documented here verbatim from the code.
>
> **Maintainer note**: update this doc whenever a business rule changes in `utils.py`,
> any `pages/*.py`, any `scripts/sync_*.py`, or the Apps Script that handles pixel events.

---

## 0. TL;DR (read this first)

The dashboard runs in **Streamlit** and reads everything from one Google Sheet workbook
called **"Wisewell - User Base Data"** (id: `1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4`).

Six pages render off five categories of data:

1. **Subscription orders** — Recharge per market (UAE/KSA/USA), one row per subscription.
2. **One-time machine purchases** — Shopify per market (UAE/KSA), columns count units sold.
3. **Offline orders** — manual tabs (sub + ownership + returns).
4. **Marketing spend** — Meta + Google Ads, daily and monthly granularity.
5. **Funnel / web analytics** — Shopify Web Pixel events aggregated daily by market.

A **`LIVE_DATA_START` boundary at 2025-09-01** splits hardcoded historical truth (`Monthly Sales`,
`Monthly Cancellations`, `Monthly User Base` tabs — never edited by code) from computed-from-raw
live data (Sep-2025 onwards). Blended series stitch the two together transparently.

Every loader applies one or more business rules (DELETED filtering, true-cancel logic,
product classification, FX conversion, etc.). **Read § 4 before touching any loader.**

---

## 1. System Overview

```
                   ┌─────────────────────────────────────────────────────────┐
                   │  Wisewell - User Base Data  (Google Sheet workbook)     │
                   │       id: 1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4   │
                   └─────────────────────────────────────────────────────────┘
                              ▲                                ▲
                              │                                │
                ┌─────────────┴────────────┐    ┌───────────────┴─────────────┐
                │                          │    │                              │
        Pipelines (write)             Loaders (read)            Manual edits
                │                          │                              │
   ┌────────────┼─────────────┐            │                ┌─────────────┘
   │            │             │            │                │
   ▼            ▼             ▼            ▼                ▼
 Zapier      Meta API    Apps Script   Streamlit         Operator
(Recharge)  cron sync   (pixel + web   utils.py + 6      (Offline tabs,
                          pixel events) pages            Returns,
                                          │              Projections,
                                          │              Targets)
                                          ▼
                                Streamlit Cloud app
                                (password-gated, 5-min
                                  auto-refresh)
```

### Data flow at a glance

| Source                          | Fresh data lands at                                     | Cadence            | Code path                                        |
|---------------------------------|---------------------------------------------------------|--------------------|--------------------------------------------------|
| Recharge subscriptions          | `Recharge - {UAE,KSA,USA}` tabs                         | Real-time (Zapier) | External Zapier integration                      |
| Shopify orders (machines)       | `Shopify - {UAE,KSA,USA}` tabs                          | Manual export      | Operator                                         |
| Offline orders / Returns        | `Offline - Subscriptions / Ownership`, `Returns`        | Manual             | Operator                                         |
| Meta Ads spend & campaigns      | `Paid Ads Spend - {Monthly,Daily}`, `Meta Ads Daily - Claude`, `Meta Ads Campaign Daily - Claude` | Hourly cron        | `scripts/sync_marketing_spend.py`                |
| Google Ads spend                | `Google Ads - Claude` (when wired)                      | Manual / TBD       | `scripts/sync_google_ads.py`                     |
| Web pixel events (raw)          | `Store Events - Live` (separate sheet `1j9lWQC9I8...`)  | Real-time          | Shopify Web Pixel → Apps Script `doPost`         |
| Web pixel daily aggregates      | `Shopify Website - {UAE,KSA,USA}`, `Sessions by Source - Daily`, `Top Landing Pages - Daily` | Every 15 min   | Apps Script `aggregateToday()` trigger           |
| Sales targets / projections     | `Projections` tab                                       | Manual             | Operator (Sami)                                  |

---

## 2. The Spreadsheet — `Wisewell - User Base Data`

**Workbook URL**: `https://docs.google.com/spreadsheets/d/1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4/edit`

There is also a **separate raw events sheet** at `1j9lWQC9I8HdtTguzcGGX1AewE6KkdICkhbGYqwErKKU`
that holds the high-volume Shopify pixel event log (`Store Events - Live` tab) and the
historical channel-attribution exports (`Channel Hist - {UAE,KSA,USA}` tabs). It exists to keep
the main sheet from bloating with thousands of pixel events per day.

### 2.1 Tab inventory (everything the dashboard touches)

> Source of truth: `RAW_TABS` and `HIST_TABS` constants in `utils.py` (lines 53–67).
> Tabs not in this list are NOT read by the dashboard — even if they exist in the workbook.

| Tab                                  | Type        | Granularity     | Written by                                | Read by loader                                                    |
|--------------------------------------|-------------|-----------------|-------------------------------------------|-------------------------------------------------------------------|
| `Recharge - UAE`                     | Raw         | One row / sub    | Zapier (live)                             | `load_recharge_full()`                                            |
| `Recharge - KSA`                     | Raw         | One row / sub    | Zapier (live)                             | `load_recharge_full()`                                            |
| `Recharge - USA`                     | Raw         | One row / sub    | Zapier (live)                             | `load_recharge_full()`                                            |
| `Shopify - UAE`                      | Raw         | One row / order  | Manual Shopify export                     | `load_shopify_ownership()`                                        |
| `Shopify - KSA`                      | Raw         | One row / order  | Manual Shopify export                     | `load_shopify_ownership()`                                        |
| `Shopify - USA`                      | Raw         | One row / order  | Manual Shopify export                     | (intentionally unused — see § 4.4)                                 |
| `Offline - Subscriptions`            | Raw         | One row / order  | Manual                                    | `load_offline_subscriptions()`                                    |
| `Offline - Ownership`                | Raw         | One row / order  | Manual                                    | `load_offline_ownership()`                                        |
| `Returns`                            | Raw         | One row / return | Manual (CS team)                          | `load_offline_returns()`                                          |
| `Paid Ads Spend - Monthly`           | Raw         | Monthly          | `sync_marketing_spend.py` + manual Google | `load_marketing_spend()`                                          |
| `Paid Ads Spend - Daily`             | Raw         | Daily            | `sync_marketing_spend.py`                 | `load_marketing_spend_daily()`                                    |
| `Meta Ads Daily - Claude`            | Raw         | Daily, by market | `sync_marketing_spend.py`                 | `load_meta_ads_daily()`                                           |
| `Meta Ads Campaign Daily - Claude`   | Raw         | Daily, by campaign | `sync_marketing_spend.py`                 | `load_meta_ads_campaign_daily()`                                  |
| `Shopify Website - UAE`              | Aggregated  | Daily            | Apps Script `aggregateToday()` + manual seed (pre-pixel data) | `load_shopify_website_analytics()` |
| `Shopify Website - KSA`              | Aggregated  | Daily            | Apps Script + manual                      | `load_shopify_website_analytics()`                                |
| `Shopify Website - USA`              | Aggregated  | Daily            | Apps Script + manual                      | `load_shopify_website_analytics()`                                |
| `Sessions by Source - Daily`         | Aggregated  | Daily, by channel | Apps Script `aggregateToday()`            | `load_sessions_by_source()`                                       |
| `Top Landing Pages - Daily`          | Aggregated  | Daily, by URL    | Apps Script `aggregateToday()`            | `load_top_landing_pages()`                                        |
| `Projections`                        | Manual      | Monthly          | Operator (Sami)                           | `load_projections()`                                              |
| `Monthly Sales`                      | Historical  | Monthly          | **Hardcoded — never edit by code**        | `load_historical_sales()`                                         |
| `Monthly Cancellations`              | Historical  | Monthly          | **Hardcoded — never edit by code**        | `load_historical_cancellations()`                                 |
| `Monthly User Base`                  | Historical  | Monthly          | **Hardcoded — never edit by code**        | `load_historical_user_base_series()`, `load_historical_ownership_seed()` |

> **Rule**: Never read `Daily Sales`, `Daily Cancellations`, `Subscriber Base`, `Quarterly Investor Report`, `Cover | Readme`, `Wisewell Sales Targets`, `Sheet27`, `Sheet28`, `Sheet31`, `P-Table Cancellations UAE`, `Work In Progress`, `Claude - Test`, or any other tab not in the table above. Those are derived/working tabs, not the source of truth.

### 2.2 Tab schemas (the columns that matter)

#### 2.2.1 `Recharge - {UAE,KSA,USA}` — identical schema across markets

| Column                          | Type             | Notes                                                                     |
|---------------------------------|------------------|---------------------------------------------------------------------------|
| `subscription_id`               | string (numeric) | Primary key. Duplicates can exist due to Zapier retries (cleaned in code) |
| `customer_email`                | string           | Used for cohort tagging                                                   |
| `status`                        | string           | One of `ACTIVE`, `CANCELLED`, `DELETED`. **`DELETED` is filtered out.**   |
| `product_title`                 | string           | E.g. "Wisewell Model 1 Subscription" — classified into category + product |
| `recurring_price`               | numeric          | Local currency (AED for UAE, SAR for KSA, USD for USA)                    |
| `quantity`                      | int              | Almost always 1. Empty/null treated as 1.                                 |
| `charge_interval_frequency`     | int              | 1 = monthly. Value `30` is normalized to `1` in the loader.              |
| `cancelled_at`                  | string date      | `dd/mm/yyyy` or empty                                                      |
| `cancellation_reason`           | string           | Free-text + normalized via `CANCELLATION_REASON_MAP`                      |
| `created_at`                    | string date      | `dd/mm/yyyy` — the sale date                                              |

#### 2.2.2 `Shopify - {UAE,KSA}`

Standard Shopify "Orders Export" CSV columns. The dashboard only uses:

- `Created at` — date (becomes the sale date)
- The **ownership unit columns** mapped in `SHOPIFY_OWN_COLS` (utils.py line 92):

```python
SHOPIFY_OWN_COLS = [
    ("Model 1",   "Units - Model 1 (Own)"),
    ("Nano+",     "Units - Nano+ (Own)"),
    ("Bubble",    "Units - Bubble (Own)"),
    ("Flat",      "Units - Flat (Own)"),
    ("Nano Tank", "Units - Nano (Own)"),
]
```

Each is an integer count of one-time machine purchases (NOT subscriptions). All other Shopify columns are ignored.

#### 2.2.3 `Offline - Subscriptions` and `Offline - Ownership`

| Column            | Type    | Notes                                                              |
|-------------------|---------|--------------------------------------------------------------------|
| `Country`         | string  | "UAE" / "KSA" / "USA"                                              |
| `Created At`      | date    | `dd/mm/yyyy`                                                       |
| `Lineitem Name`   | string  | Free-text describing the product — classified via `_classify_offline_product()` |
| `Lineitem Quantity` | int    | Units sold                                                         |

#### 2.2.4 `Returns`

| Column    | Type    | Notes                                                     |
|-----------|---------|-----------------------------------------------------------|
| `date`    | date    | Date returned                                             |
| `market`  | string  | UAE / KSA / USA                                           |
| `product` | string  | One of the products in `PRODUCT_ORDER`                    |
| `qty`     | int     | Units returned (subtracted from active ownership)         |

#### 2.2.5 `Paid Ads Spend - Monthly`

| Column           | Notes                                                    |
|------------------|----------------------------------------------------------|
| `Month`          | First-of-month label like `Mar-26` (parsed `%b-%y`)      |
| `Total Spend`    | NOT trusted — total derived from per-market columns      |
| `UAE`/`KSA`/`USA`| Per-market USD spend                                     |
| `META`/`Google`  | Per-platform USD spend                                   |
| `UAE - META`/`KSA - META`/`USA - Facebook` | Per-market-per-platform spend  |
| `UAE - Google`/`KSA - Google`/`USA - Google` | Per-market-per-platform spend |

#### 2.2.6 `Paid Ads Spend - Daily`

Same column layout as Monthly. The `Day` column in `dd MMM, YYYY` format (e.g., `7 May, 2026`).

#### 2.2.7 `Meta Ads Daily - Claude`

Schema: `Date | Market | Spend (USD) | Clicks | Impressions | CTR (%) | CPC (USD)`. One row per (date, market). Rebuilt fully on each sync run.

#### 2.2.8 `Meta Ads Campaign Daily - Claude`

Schema: `Date | Market | Campaign ID | Campaign Name | Objective | Status | Spend (USD) | Clicks | Impressions | CTR (%) | CPC (USD) | CPM (USD)`. One row per (date, market, campaign). Last 90 days only.

#### 2.2.9 `Shopify Website - {UAE,KSA,USA}`

| Column                              | Notes                                                |
|-------------------------------------|------------------------------------------------------|
| `Day`                               | Daily date (the loader accepts `Day`, `Date`, or `date`) |
| `Sessions`                          | Total sessions                                       |
| `Sessions with cart additions`      | (also accepts alias `add_to_cart`)                   |
| `Sessions that reached checkout`    | (also accepts alias `reached_checkout`)              |
| `Sessions that completed checkout`  | (also accepts alias `completed_checkout`)            |
| `Conversion rate`                   | Float OR percentage string — both handled            |
| `New sessions` *(post-pixel)*       | First-touch sessions                                 |
| `Returning sessions` *(post-pixel)* | Repeat-touch sessions                                |

> Pre-pixel data was manually pasted from Shopify export. Post-pixel data is upserted by the Apps Script.

#### 2.2.10 `Sessions by Source - Daily`

Schema: `date | market | channel | utm_source | utm_campaign | sessions | add_to_cart | reached_checkout | completed_checkout`. Multiple rows per (date, market). `channel` is one of:
`Direct, Organic Search, Organic Social, Paid Social (Meta/TikTok/Snapchat), Paid Search (Google), Paid Other, Email, SMS, Referral` — see Apps Script `classifyChannel()` for exact rules.

#### 2.2.11 `Top Landing Pages - Daily`

Schema: `date | market | page_path | sessions | add_to_cart`. Top 10 landing pages per (date, market).

#### 2.2.12 `Projections`

Free-form layout — multiple sections. The loader (`utils.load_projections`) parses these sections by anchor labels:

- Top section: header row of months (Mar-26 → Mar-28), then rows:
  - `Total Gross Sales - USA` — monthly USA target
  - `Total Gross Sales - GCC` — monthly UAE+KSA target (currently UAE only)
  - `Total Gross Sales - Global` — sum of USA + GCC
- Per-market sections (`UAE`, `KSA`, `USA`) each with sub-rows for each product (subscription + ownership), summing to a `Total {market} Sales` row.

#### 2.2.13 Historical tabs (`Monthly Sales`, `Monthly Cancellations`, `Monthly User Base`)

These are **hardcoded matrices** with months as columns and named rows. They are the single source of truth for pre-Sep-2025 data. The dashboard NEVER computes against them dynamically; it just reads them. Row positions are encoded in `_HIST_SALES_ROWS`, `_HIST_CANCEL_ROWS`, `_HIST_UB_SUB_ROWS`, `_HIST_UB_OWN_ROWS` constants.

> **Rule**: do not mutate these tabs through any automation. They are frozen reference data.

---

## 3. Data Pipelines (how each tab gets populated)

### 3.1 Recharge subscriptions → `Recharge - {market}` tabs

- **Source**: Recharge (subscription billing platform) + Shopify checkout webhooks.
- **Mechanism**: Zapier Zaps. One Zap per market (UAE/KSA/USA). Triggered on subscription create / update.
- **Output**: New row appended to the corresponding `Recharge - {market}` tab.
- **Known issue**: occasional duplicate runs cause the same `subscription_id` to appear twice.
  Cleanup is one-off via the script in this repo's history (kept first occurrence). Long-term fix
  is to add a Zapier de-dupe step.
- **Timezone caveat**: Zapier formats the `created_at` field in the Zapier account's timezone
  (Asia/Dubai). For a USA order placed at 10pm EST, that becomes 7am Dubai the next day → date is
  written as next-day's date. **Not currently corrected.** See § 9 "Known gotchas".

### 3.2 Shopify ownership orders → `Shopify - {UAE,KSA}` tabs

- **Mechanism**: Operator manually exports from Shopify Admin and pastes columns into the sheet.
- **Cadence**: Periodic (typically weekly).
- **Why USA is excluded**: The USA Shopify store routes 100% of machine revenue through Recharge subscriptions (incl. rent-to-own). Counting `Shopify - USA` would double-count the same units. The tab exists for future use but is not read.

### 3.3 Offline & Returns → `Offline - *`, `Returns`

- **Mechanism**: Manual entry by ops/CS team.
- Columns described in § 2.2.3 / 2.2.4.

### 3.4 Meta Ads → `Paid Ads Spend - *`, `Meta Ads Daily - Claude`, `Meta Ads Campaign Daily - Claude`

- **Script**: `scripts/sync_marketing_spend.py`.
- **Trigger**: hourly cron, `0 * * * *` (configurable). Wrapped by `scripts/run_meta_sync.sh`.
- **Reads from Meta Graph API v19.0**:
  - `/{act}/insights` at `level=account` for monthly spend
  - `/{act}/insights` at `level=account` and `time_increment=1` for daily spend
  - `/{act}/insights` at `level=campaign` and `time_increment=1` for campaign-level daily (last 90 days)
- **Writes to**:
  - `Paid Ads Spend - Monthly` (legacy `Marketing Spend - Claude` writer; keep both in sync)
  - `Paid Ads Spend - Daily`
  - `Meta Ads Daily - Claude`
  - `Meta Ads Campaign Daily - Claude`
  - `Meta Ads - Claude` (per-market monthly performance — secondary)
- **Currency**: Multiplies by per-market FX rate (`USD_RATES` constant, fixed pegs:
  AED=0.27226, SAR=0.26667, USD=1.0). Write USD columns.
- **Idempotency**: Each run rebuilds the affected tabs from scratch. Safe to re-run.
- **Required env vars**:
  - `META_ACCESS_TOKEN` (system-user token, scopes: `ads_read,ads_management,business_management`)
  - `META_AD_ACCOUNT_UAE`, `META_AD_ACCOUNT_KSA`, `META_AD_ACCOUNT_USA` (the `act_xxx` IDs)
  - `GOOGLE_SERVICE_ACCOUNT` (JSON service-account key with edit access to the sheet)

### 3.5 Google Ads → `Google Ads - Claude`

- **Script**: `scripts/sync_google_ads.py`.
- **Status**: Wired but waiting on Standard Access approval for the Google Ads API (typically 2-7 business days).
- **Trigger**: same `run_meta_sync.sh` (or a separate cron once enabled).
- **Reads**: Google Ads API v19, monthly + daily insights per account.
- **Writes**: `Google Ads - Claude` tab.
- **Required**: `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_LOGIN_CUSTOMER_ID` (MCC),
  `GOOGLE_ADS_CUSTOMER_*` per market, plus a `google_ads_token.json` OAuth credential file.

### 3.6 Shopify Web Pixel → raw events sheet → daily aggregates

This is a custom-built pipeline because Shopify Plus Trial blocks ShopifyQL. It has 4 components:

#### 3.6.1 The pixel scripts (one per store)

- **Files**: `scripts/shopify_pixel_UAE.js`, `_KSA.js`, `_USA.js`.
- **Where it runs**: Pasted into Shopify Admin → Settings → Customer Events → custom pixel.
- **Subscribes to** the following Shopify Customer Events:
  - `page_viewed` — captures every page view + UTM/referrer/click-id attribution
  - `product_added_to_cart`
  - `checkout_started`
  - `checkout_completed`
- **Posts** each event as JSON to the Apps Script web-app endpoint with fields:
  `timestamp, market, source="pixel", event_type, session_id (clientId), page_path, product_id, product_title, value, currency, order_id, checkout_id, referrer, utm_source, utm_medium, utm_campaign, utm_content, fbclid, gclid`.

#### 3.6.2 The Apps Script web app (`scripts/shopify_events_appscript.js`)

- **Deployment**: Deploy as Web App (Execute as: Me, Access: Anyone). The deployment URL is
  baked into each pixel script as `ENDPOINT`. Same URL across all 3 markets.
- **`doPost(e)`**: appends one row per event to `Store Events - Live` tab in the
  **separate raw events sheet** (`1j9lWQC9I8...`).
- **`aggregateToday()`** — runs every 15 min via time-driven trigger.
  Re-aggregates today's events from the raw sheet and **upserts** (delete then re-write) the
  per-market summary rows into:
  - `Shopify Website - {UAE,KSA,USA}` (one row per market per day, schema § 2.2.9)
  - `Sessions by Source - Daily` (multiple rows per market)
  - `Top Landing Pages - Daily` (top 10 pages per market)
- **`aggregateDate("dd/MM/yyyy")`** — manual backfill from the editor for a single past date.
- **`MARKET_TZ`**: each market's day boundary is computed in its own local timezone:
  UAE = Asia/Dubai, KSA = Asia/Riyadh, USA = America/New_York. So an event at 10pm ET on May 6
  is correctly bucketed to May 6 USA (not May 7 Dubai).
- **Channel classification** (`classifyChannel()` in the Apps Script):
  - `fbclid` present → `Paid Social (Meta)`
  - `gclid` present → `Paid Search (Google)`
  - `utm_medium` ∈ {cpc, ppc, paid} → `Paid Search/Social/Other` based on `utm_source`
  - `utm_source` contains "facebook"/"instagram"/"meta" → `Paid Social (Meta)`
  - `utm_source` contains "google" + non-organic medium → `Paid Search (Google)`
  - `utm_medium=email` or source contains klaviyo/mailchimp → `Email`
  - referrer matches google.com/bing/yahoo → `Organic Search`
  - referrer matches facebook/instagram/tiktok/twitter → `Organic Social`
  - else with referrer → `Referral`
  - no referrer → `Direct`
- **Upsert delete strategy**: Uses `getDisplayValues()` (not `getValues()`) so deletion correctly
  matches both string dates and Date objects (a Sheet auto-converts pasted CSV dates to Date type;
  this caused a big duplicate-rows bug previously).

#### 3.6.3 Historical channel data — `Channel Hist - {UAE,KSA,USA}`

- Lives in the **separate raw events sheet** (`1j9lWQC9I8...`), NOT the main user base sheet.
- Source: One-time Shopify Analytics export for all-time data, manually pasted.
- Schema: `Day | Referrer source | UTM source | UTM medium | UTM campaign | Sessions | Sessions with cart additions | Sessions that reached checkout | Sessions that completed checkout | Conversion rate | Checkout conversion rate`.
- Loaded by `load_channel_history()` and unioned with live `Sessions by Source - Daily` via
  `load_channel_attribution_unified()`. Live rows take precedence on overlap days per market.

### 3.7 Sales projections → `Projections`

- **Mechanism**: Manual entry by Sami (CEO).
- **What it drives**: The "Target" section under the Executive Summary headline KPIs, including the
  full-month progress bar, projected EOM, status badge (✅/⚠️/🔴), and product-mix delta panel.

### 3.8 Misc supporting scripts

- `scripts/register_shopify_webhooks.py` — registers Shopify Admin webhooks for orders/checkouts.
  One-time setup per store; not used by the dashboard live.
- `scripts/shopify_oauth.py` — interactive OAuth flow that captures a Shopify Admin API token
  (writes to `.shopify_{market}_token.txt`). Required for `register_shopify_webhooks.py` and the
  Shopify Admin API loaders in `utils.py` (`load_shopify_store_analytics`, etc.) — these are
  fallbacks not currently used by the live pages but exist in `utils.py`.
- `scripts/sync_recharge.py` — pulls **failed-charge / dunning** state from Recharge for a separate
  Failed Payments dashboard. Not part of the main user-base pipeline.
- `scripts/sync_inbound_queries.py` — Freshchat + Freshdesk sync for an "Inbound Queries" feature
  on the Executive Summary (conversion-rate KPI from inbound product queries). Trigger: 2x/day
  via GitHub Actions.
- `scripts/sheet_refresh.py` — internal refresh helper (used by daily auto-commits in CI).

---

## 4. Cross-Cutting Business Rules

These apply across many loaders. Understanding them is non-negotiable for any agent reading
or replicating this codebase.

### 4.1 The `LIVE_DATA_START` boundary — 2025-09-01

```python
LIVE_DATA_START   = pd.Timestamp("2025-09-01")
OWNERSHIP_SEED_DT = pd.Timestamp("2025-08-01")
```

**Rule**: Any series that spans pre- and post-Sep-2025 data (sales, cancellations, user base)
is BLENDED — historical totals come from the hardcoded `Monthly Sales` / `Monthly Cancellations`
/ `Monthly User Base` tabs, and Sep-2025 onwards is computed live from raw Recharge / Shopify /
Offline data.

Functions that handle the blend:

- `get_monthly_sales_blended()` — concatenates `load_historical_sales()` + monthly aggregation of `get_all_machine_sales(start_dt=LIVE_DATA_START)`.
- `get_monthly_cancellations_blended()` — same pattern.
- `get_monthly_user_base_blended()` — historical uses `load_historical_user_base_series()`; live month-end is `(active_subs at month_end + cumulative_ownership at month_end)`. Cumulative ownership uses the **Aug-2025 month-end seed** + every Sep+ delta.

> **Rule**: Never read pre-Sep-2025 data live from Recharge — even if it's there. The Zapier feed
> may have gaps for old subs. Always blend.

### 4.2 Ownership accounting

- **Seed**: `OWNERSHIP_SEED_DT = 2025-08-01` (Aug 2025 month-end).
- **Source of seed**: `load_historical_ownership_seed()` reads the Aug-2025 column of `Monthly User Base` for the ownership rows (`_HIST_UB_OWN_ROWS`).
- **Live deltas (Sep-2025 onwards)**:
  - Add: `load_shopify_ownership()` qty per (date, market, product) — UAE + KSA only.
  - Add: `load_offline_ownership()` qty.
  - **Subtract**: `load_offline_returns()` qty.
- **Floor**: `get_active_ownership()` clips qty to ≥ 0 (never negative).

### 4.3 DELETED subscription filtering

```python
# load_recharge_full(), utils.py line 432-443
_status_norm = df["status"].fillna("").astype(str).str.strip().str.upper()
df = df[_status_norm != "DELETED"].copy()
```

- DELETED rows are excluded from EVERY metric.
- **Why**: They're orphan/test/buggy entries in Recharge.
- **Note**: Some DELETED rows have garbage `created_at` like `30/12/1899` — still excluded.

### 4.4 True cancellations vs swaps

Computed in `load_recharge_full()` (utils.py line 477–488):

```python
SWAP_REASONS = {"swapped", "purchased", "converted", "swap", "max"}
df["is_true_cancel"] = (
    df["cancelled_at_dt"].notna()
    & ~df["cancellation_reason"].str.lower().isin(SWAP_REASONS)
)
```

- A "true cancel" = customer left and isn't returning under another product.
- Swaps/conversions/upgrades aren't churn (the customer is retained on a different SKU).
- **All churn metrics use `is_true_cancel == True` only.**

### 4.5 Product classification

#### 4.5.1 Recharge → `_classify_recharge_product(title)`

Returns `(category, product)` where category ∈ `{"Machine", "Filter", None}` and product is one
of `PRODUCT_ORDER` or `None` (excluded).

Rules (in order, first match wins):

1. **Filter category** if title contains `"filter subscription"`, `"care+ plan"`, or `"care+"`.
   Parent product is inferred (Model 1 / Bubble / etc.) from the same title.
2. **Ownership exclusion**: title contains `"ownership"` → return `(None, None)` UNLESS title
   matches `"bubble ownership + holiday set"` (specific promo bundle counted as Bubble Machine).
3. **Machine matching** (case-insensitive regex on lowercased title):
   - `model\s*1.*subscription` OR exact `wisewell model 1` → `("Machine", "Model 1")`
   - `nano\s*\+\s*subscription` → `("Machine", "Nano+")`
   - `bubble.*subscription` → `("Machine", "Bubble")`
   - `wisewell\s*flat\s*subscription` (and not "filter") → `("Machine", "Flat")`
   - `wisewell nano subscription` (UAE) OR `wisewell nano` (USA exact) → `("Machine", "Nano Tank")`

> The `Wisewell Nano` USA-exact rule is critical: USA Recharge records a Nano Tank subscription
> with title `"Wisewell Nano"` (no "Subscription" suffix). UAE uses `"Wisewell Nano Subscription"`.

#### 4.5.2 Offline → `_classify_offline_product(lineitem)`

Pure regex on the line-item name:
- `\bnano\b` (NOT followed by `+` or "plus") → `Nano Tank`
- `model\s*1` OR `\bm1\b` → `Model 1`
- `nano\s*\+` → `Nano+`
- contains `bubble` AND NOT `filter` → `Bubble`
- `\bflat\b` AND NOT `filter` → `Flat`
- otherwise → `None` (skipped)

### 4.6 FX conversion

- Live rates from `https://open.er-api.com/v6/latest/USD`, cached 1h via `get_fx()`.
- Stored as **base-USD** rates, e.g., `fx["AED"] = 1 / 3.6725 ≈ 0.27226`.
- **Fallback**: If the API fails, hardcoded pegs:
  - AED: `1 / 3.6725`
  - SAR: `1 / 3.7500`
  - USD: `1.0`
- **Where applied**: `_arr_usd_at(end_ts)` and any USD-denominated revenue metric. Local price ×
  qty × (12 / freq) × FX → ARR USD.

### 4.7 Per-market timezones (Apps Script only)

Already documented in § 3.6.2. Critical because:
- Each market's "today" is computed in its own local TZ.
- Past-date rows are NEVER re-touched by `aggregateToday()` (only today's rows upsert).
- `aggregateYesterday()` was removed — past dates are fully manual / frozen by design.

### 4.8 Charge-interval normalization

- Recharge stores `charge_interval_frequency` as 1 (1 month) usually, but sometimes `30` (30 days).
- `load_recharge_full()` normalizes 30 → 1 so ARR formula `× (12 / freq)` stays correct.

### 4.9 Pro-rata projections (current month)

The dashboard projects MTD figures to a full-month rate where comparison vs prior full month
is the right framing. Specifically:

- **Churn rate** (`PROJECTED MONTHLY CHURN` KPI): `(MTD true cancels × days_in_month / days_into_month) / active_at_month_start`. Compared to **prior full month's actual churn rate**.
- **CAC / spend**: Daily actuals from `Paid Ads Spend - Daily` are summed. For days not in the daily tab, falls back to **monthly total ÷ days-in-month**, but for the current month it falls back to **monthly total ÷ days-elapsed** (because the monthly cell is cumulative-actuals, not a projection — see comment in `_marketing_spend_in`).

### 4.10 Inflated-bot-traffic awareness

You'll see two pre-pixel session spikes in `Shopify Website - UAE`:
- April 1–13, 2026: ~20k–130k sessions/day (real avg ~3k)
- May 4–5, 2026: ~10k–13k sessions/day with 0.04% Paid-Search CVR

These are bot/scraper waves. Real customer behaviour (ATC, checkout, orders) was unchanged. Going
forward the pixel pipeline filters most of this — but historical reporting should treat those days
with care.

---

## 5. Loader Reference (`utils.py`)

> All loaders are decorated with `@st.cache_data(ttl=300, show_spinner=False)` unless noted.

### 5.1 Authentication & infrastructure

| Function                       | Purpose                                                                           |
|--------------------------------|-----------------------------------------------------------------------------------|
| `get_credentials()`            | Returns Sheets API creds. Service account from `st.secrets["GOOGLE_SERVICE_ACCOUNT"]` (cloud) or local `token.json` (dev). |
| `get_fx()` (TTL 1h)            | Live USD FX rates, fallback to hardcoded pegs.                                    |
| `_fetch_single_tab(creds, tab)`| Fetch one tab with 3 retries / exponential backoff. Returns `(tab, rows, elapsed, error)`. |
| `_fetch_all_tabs()`            | Parallel fetch all `RAW_TABS + HIST_TABS`. Returns `(data_dict, errors_dict, elapsed)`. |
| `_rows_to_df(rows)`            | Pad uneven rows → DataFrame with header row as columns.                           |
| `_parse_dates(series)`         | Parse `dd/mm/yyyy` then ISO fallback. Returns naive UTC.                          |

### 5.2 Raw data loaders

#### `load_recharge_full()` — utils.py line 397

- Reads: `Recharge - {UAE,KSA,USA}`.
- Drops DELETED.
- Normalizes `charge_interval_frequency` (30→1).
- Adds: `created_at_dt`, `cancelled_at_dt` (parsed dates).
- Adds: `is_true_cancel` (see § 4.4).
- Adds: `cancellation_reason` (normalized via `CANCELLATION_REASON_MAP`).
- Adds: `market`, `currency` (AED/SAR/USD).
- Adds: `category` (Machine/Filter/None) and `product` via `_classify_recharge_product()`.
- Adds: `arr_local` for ACTIVE only (`recurring_price × quantity × 12 / charge_interval_frequency`).

#### `load_shopify_ownership()` — utils.py line 512

- Reads: `Shopify - UAE`, `Shopify - KSA` only (USA excluded — see § 4 / § 2.1).
- Iterates over `SHOPIFY_OWN_COLS` columns; one record per unit per (date, market, product).
- Returns: `[date, market, product, qty]`.

#### `load_offline_subscriptions()`, `load_offline_ownership()` — line ~597, ~603

- Wrappers over `_load_offline_generic("Offline - Subscriptions" / "Offline - Ownership")`.
- Returns: `[date, market, product, qty]`.

#### `load_offline_returns()` — line 609

- Reads: `Returns`.
- Returns: `[date, market, product, qty]`. Used to net against ownership.

#### `load_marketing_spend()` — line 653

- Reads: `Paid Ads Spend - Monthly` (with fallback to legacy `Marketing Spend` name).
- Date format: `"%b-%y"` ("Mar-26").
- Returns: `[month_dt, total_usd, uae_usd, ksa_usd, usa_usd]`.
- `total_usd` is **derived** as `uae + ksa + usa` (do not trust the Total Spend column — it can be stale).

#### `load_marketing_spend_daily()` — line 697

- Reads: `Paid Ads Spend - Daily`.
- Date format: `"%d %b, %Y"` ("7 May, 2026") with general fallback.
- Returns: `[date, total_usd, uae_usd, ksa_usd, usa_usd]`.

#### `load_meta_ads_daily()` — line 880

- Reads: `Meta Ads Daily - Claude`.
- Returns: `[date, market, spend_usd, clicks, impressions, ctr_pct, cpc_usd]`.

#### `load_meta_ads_campaign_daily()` — line 981

- Reads: `Meta Ads Campaign Daily - Claude`.
- Returns: `[date, market, campaign_id, campaign_name, objective, status, spend_usd, clicks, impressions, ctr_pct, cpc_usd, cpm_usd]`.

#### `load_shopify_website_analytics()` — line 913

- Reads: `Shopify Website - {UAE,KSA,USA}`.
- Returns: `[date, market, sessions, new_sessions, returning_sessions, add_to_cart, reached_checkout, completed_checkout, conversion_rate]`.
- Tolerates both Shopify-export header style (`Day`, `Sessions with cart additions`) and Apps Script style (`date`, `add_to_cart`).
- Tolerates conversion rate as float (0.5) or percent string ("50%" or "50.0%"), normalizes to 0–1 float.

#### `load_sessions_by_source()` — line 1018

- Reads: `Sessions by Source - Daily`.
- Returns: `[date, market, channel, utm_source, utm_campaign, sessions, add_to_cart, reached_checkout, completed_checkout]`.

#### `load_top_landing_pages()` — line 1043

- Reads: `Top Landing Pages - Daily`.
- Returns: `[date, market, page_path, sessions, add_to_cart]`.

#### `load_channel_history()` — line ~1090

- Reads `Channel Hist - {UAE,KSA,USA}` from the **separate raw events sheet** (`RAW_EVENTS_SHEET_ID`).
- Auto-detects `Day` vs `Month` granularity (USA was originally monthly; now daily after re-export).
- Returns: `[date, market, channel, referrer_source, utm_source, utm_medium, utm_campaign, sessions, add_to_cart, reached_checkout, completed_checkout]`.

#### `load_channel_attribution_unified()` — line ~1180

- Unions `load_channel_history()` (historical) with `load_sessions_by_source()` (live).
- Live takes precedence on overlap days per market.

#### `load_projections()` — line 742

- Reads: `Projections`.
- Returns: dict keyed by `"YYYY-MM-01"` strings → per-month `{global, by_market, by_market_pct, by_uae_product, by_ksa_product, by_usa_product}`.

#### `load_historical_sales()` — line 1562

- Reads: `Monthly Sales` (matrix layout, fixed row positions in `_HIST_SALES_ROWS`).
- Returns: `[month_dt, market, product, is_ownership, qty]`.
- **Only** months `< LIVE_DATA_START`.

#### `load_historical_cancellations()` — line 1580

- Reads: `Monthly Cancellations`.
- Returns: `[month_dt, market, product, qty]` (true cancels only).

#### `load_historical_ownership_seed()` — line 1597

- Reads: `Monthly User Base`.
- Returns: `[month_dt, market, product, qty]` for the **OWNERSHIP_SEED_DT month only**.

#### `load_historical_user_base_series()` — line 1944

- Reads: `Monthly User Base`.
- Returns: `[month_dt, market, product, sub_qty, own_qty]` for months `< LIVE_DATA_START`.
- Nano Tank defaults to 0 for historical months (the matrix predates Nano Tank's launch).

### 5.3 Compute helpers (blended series, point-in-time, etc.)

#### `get_all_machine_sales(start_dt=LIVE_DATA_START, end_dt=today)` — line 1643

- Sources, all filtered to date range: Recharge subs (Machine category) + Offline subs + Shopify ownership + Offline ownership.
- Returns: `[date, market, product, is_ownership, qty]`.

#### `get_monthly_sales_blended()` — line 1701

- `load_historical_sales()` + monthly aggregation of `get_all_machine_sales()`.
- Returns: `[month_dt, market, product, is_ownership, qty]` for ALL months up to current.

#### `get_active_subscriptions(as_of)` — line 1732

- Recharge ACTIVE machines + Offline subs (cumulative — no churn feed for offline).
- Returns: `[market, product, qty]`.

#### `get_active_ownership(as_of)` — line 1787

- Aug-2025 seed + Shopify ownership Sep-2025→as_of + Offline ownership Sep-2025→as_of − Returns Sep-2025→as_of.
- Floor to 0.
- Returns: `[market, product, qty]`.

#### `get_monthly_cancellations_blended()` — line 1834

- `load_historical_cancellations()` + monthly aggregation of `is_true_cancel == True` Recharge rows.
- Returns: `[month_dt, market, product, qty]`.

#### `compute_cancellation_rate(as_of, market, product)` — line 1872

- MTD extrapolated rate.
- Formula: `(MTD true cancels × days_in_month / days_elapsed) / active_machine_subs_at_prior_month_end`.
- Returns dict: `{rate, mtd_cancels, extrapolated_cancels, active_at_start, days_elapsed, days_in_month, market, product, as_of}`.

#### `get_monthly_user_base_blended()` — line 1996

- Historical: `load_historical_user_base_series()`.
- Live (Sep-2025 onwards): for each month-end, `active_machine_subs + cumulative_ownership_to_date`.
- Returns: `[month_dt, market, product, total]`.

### 5.4 Module-level constants (utils.py)

| Constant                | Value / purpose                                                                         |
|-------------------------|----------------------------------------------------------------------------------------|
| `SHEET_ID`              | `1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4` — main user base workbook                |
| `RAW_EVENTS_SHEET_ID`   | `1j9lWQC9I8HdtTguzcGGX1AewE6KkdICkhbGYqwErKKU` — Shopify raw events + Channel Hist      |
| `RAW_TABS`              | List of tab names fetched in parallel on every cache miss                              |
| `HIST_TABS`             | `["Monthly Sales", "Monthly Cancellations", "Monthly User Base"]`                       |
| `ALL_SOURCE_TABS`       | `RAW_TABS + HIST_TABS`                                                                 |
| `LIVE_DATA_START`       | `pd.Timestamp("2025-09-01")`                                                            |
| `OWNERSHIP_SEED_DT`     | `pd.Timestamp("2025-08-01")`                                                            |
| `MAX_RETRIES`           | `3`                                                                                     |
| `RETRY_BACKOFF`         | `[1, 2, 4]` seconds                                                                     |
| `PRODUCT_ORDER`         | `["Model 1", "Nano+", "Bubble", "Flat", "Nano Tank"]`                                  |
| `PRODUCT_COLOR`         | dict — used for chart consistency                                                       |
| `CATEGORY_COLOR`        | `{"Machine": "#0ea5e9", "Filter": "#10b981"}`                                          |
| `MARKET_COLOR`          | `{"UAE": "#6366f1", "KSA": "#f59e0b", "USA": "#10b981"}`                                |
| `FX_FALLBACK`           | `{"AED": 1/3.6725, "SAR": 1/3.75, "USD": 1.0}`                                          |
| `SHOPIFY_OWN_COLS`      | List of (product, ownership-column-name) tuples                                         |
| `CANCELLATION_REASON_MAP` | 15 mappings to normalize Recharge cancel reasons                                      |
| `_HIST_SALES_ROWS`      | dict (market, product, is_ownership) → row index in Monthly Sales                       |
| `_HIST_CANCEL_ROWS`     | dict (market, product) → row index in Monthly Cancellations                             |
| `_HIST_UB_SUB_ROWS`     | dict (market, product) → subscription row index in Monthly User Base                    |
| `_HIST_UB_OWN_ROWS`     | dict (market, product) → ownership row index in Monthly User Base                       |

---

## 6. Page-by-Page Metric Definitions

### 6.1 Executive Summary (`pages/executive_summary.py`)

**Sidebar route**: 🎯 Executive Summary
**Global filter**: Country selectbox (All / UAE / KSA / USA) — applied to most metrics via `_apply_mkt(df)`.

#### Row 1 — Headline KPIs (6 columns)

1. **TODAY'S SALES**
   - Value: `_new_sales_in(today_ts, today_ts)`.
   - Delta: % change vs yesterday (`_new_sales_in(yesterday_ts, yesterday_ts)`).

2. **SAME DAY · LAST WEEK ({weekday DD MMM})**
   - Value: `_new_sales_in(today_ts - 7d, today_ts - 7d)`.
   - Delta: today's sales vs that day, % change.

3. **ARR (USD)**
   - Value: `_arr_usd_at(today_ts)` = sum over active Machine + Filter subs of
     `recurring_price × quantity × (12 / charge_interval_frequency) × FX_USD`.
   - Delta: vs `_arr_usd_at(prev_mtd_end)` (same MTD-day in prior month).

4. **TOTAL USER BASE**
   - Value: `_active_users_at(today_ts)` = active machine subs + active ownership.
   - Delta: vs same MTD-day prior month.

5. **PROJECTED MONTHLY CHURN**
   - Value: `cur_churn × (days_in_month / days_into_month) / active_machine_subs_at_mtd_start`.
   - Delta (inverse): vs `prev_full_churn / active_machine_subs_at_prev_full_start`. **Compared to actual full-month rate of the prior month**, NOT same-MTD-window.
   - Visual hint: "PROJECTED" prefix to make clear it's a pace-extrapolated number.

6. **CAC · MTD**
   - Value: `_marketing_spend_in(mtd_start, today_ts) / cur_new`.
   - `_marketing_spend_in()` prefers `Paid Ads Spend - Daily` actuals; falls back to monthly proration only for days not yet in the daily tab.
   - Delta (inverse): same MTD-aligned window prior month (e.g., May 1–7 vs Apr 1–7).

#### Target Section (under headline KPIs)

- Pulls current-month projections via `load_projections()`.
- **LEFT**: Horizontal progress bar = MTD sales / monthly target.
  - Linear pace marker at `today.day / days_in_month`.
  - Status badge: ✅ ON TRACK if projected EOM ≥ 0.98 × target, ⚠️ SLIGHTLY BEHIND if 0.85–0.98×, 🔴 BEHIND if < 0.85×.
  - Pace delta text: actual MTD − linearly-expected MTD.
  - Projected EOM text: `cur_new / days_into_month × days_in_month`, with ± vs target.
- **RIGHT**: Product-mix table (always product-level, not market-level).
  - For "All" countries: sums `by_uae_product + by_ksa_product + by_usa_product` from projections.
  - For specific market: that market's product breakdown.
  - Columns: Product | MTD | Pace Δ | Mix % | Proj Mix %.

#### Row 2 — Trailing 7 Days

Cards (T7D current vs T7D previous, `_fmt_delta` % change):
- AVG DAILY SALES (T7D) — `t7_sales / 7`
- AVG DAILY CANCELLATIONS (T7D) — `t7_churn / 7`
- AVG DAILY NET GROWTH (T7D) — `t7_net / 7`
- AVG DAILY CAC (T7D, USD) — `t7_spend / t7_sales` (uses daily-tab actuals)

#### Charts

- ARR + User Base over time (monthly): `get_monthly_user_base_blended()` plus monthly ARR series computed from `_arr_usd_at(month_end_ts)` for each month.
- Sales Dashboard (left bar chart): monthly `get_all_machine_sales()` aggregated to month, with date selector.
- Sales Dashboard (right donut): same data, breakdown by product.
- Efficiency: CAC + Churn Rate (two-row subplot):
  - Top: monthly churn rate. Current incomplete month is **projected** to full-month pace via `× days_in_month / days_so_far`. Caption explains the projection.
  - Bottom: monthly CAC bars (USD).
- Inbound Queries section (if `Inbound Queries` tab present): conversion rate KPI + trend chart from inbound product queries.

### 6.2 Sales (`pages/test.py`)

**Sidebar route**: 📈 Sales

**Filters**: Date preset (MTD / 7d / YTD / Custom), comparison range (defaults to same-length prior period), granularity (Daily / Monthly / Quarterly), product filter, country filter.

**Scorecards** (current period vs comparison):
- New Sales (qty)
- User Base (EOM, count) — `_active_users_at(period_end)`
- ARR (EOM, USD) — `_arr_usd_at(period_end)`
- Net Growth (sales − true cancels)
- LTV (USD) — simplified: gross-margin proxy ÷ churn rate

**Charts**:
- Daily/weekly/monthly sales trend (line) with comparison overlay
- Product breakdown (stacked bar)

### 6.3 Retention (`pages/test2.py`)

**Sidebar route**: 🔄 Retention
**Scope**: Machine subscriptions only · `is_true_cancel == True` only.

**Filters**: Same as Sales page.

**Scorecards**:
- Churned (count) — `_churns_in(start, end)["quantity"].sum()`
- Active at Start of Period — `_active_at(period_start)`
- **Churn Rate** — labelled `PROJECTED MONTHLY CHURN` and projected to full-month pace **only when** the user has selected MTD on the current month. For all other selections (Past 7d, YTD, Custom), shows the raw rate over the chosen window.
  - When projected: comparison default switches to the prior FULL month (not "same N days prior")
- Retention Rate = 1 − churn rate

**Charts**:
- Daily churn volume trend (line, with comparison overlay)
- Per-bucket churn rate trend with **incomplete-bucket projection** (e.g., a bucket clamped at "today" gets scaled to its natural length)
- Cancellation reason breakdown (donut)

### 6.4 Cohort Analysis (`pages/cohort.py`)

**Sidebar route**: 📊 Cohort Analysis

**Filters**:
- First cohort month (oldest)
- Last cohort month (newest)
- Product (All / each)
- Region (All / each market)

**Heatmap**:
- Rows = signup cohort month (`created_at_dt.dt.to_period("M")`)
- Columns = M0 .. M_n (months elapsed since signup; n is dynamically the depth of the oldest cohort in range)
- Cell value = % of cohort still retained at that month-offset (active subs + active ownership counted as retained)
- Tooltip shows raw count and percentage
- Color: white (0%) → dark green (100%)

### 6.5 Paid Ads Analysis (`pages/paid_ads.py`)

**Sidebar route**: 📢 Paid Ads Analysis
**Data**: Meta only (`load_meta_ads_daily()`).

**Filters**: Market / granularity / date range / comparison mode.

**Scorecards**: Spend / Clicks / Impressions / CTR / CPC / CPM (all vs comparison period).

**Charts**: Spend trend (line + comparison overlay), CTR & CPM trends.

### 6.6 Paid Ads 2 (`pages/paid_ads2.py`)

**Sidebar route**: 🎯 Paid Ads 2
**Scope**: Full-funnel Meta → Shopify pixel → orders.

**Caveat banner at top of page**: "Pixel data has ~15-25% undercount vs Shopify-native — use trends, not absolutes."

**Filters**: Market, date range, comparison mode.

**Sections**:
1. North-Star Snapshot KPIs (ROAS proxy, blended CAC, Sessions, ATC, Reached Checkout, Orders, CVR).
2. Anomaly banner — auto-flagged irregularities. Includes Meta spend pacing alert (projected EOD spend vs trailing 7-day average; flags >25% under-pace after 4am Dubai).
3. Funnel waterfall: Impressions → Clicks → Sessions → ATC → Checkout → Orders.
4. Stage trend chart over time (CTR / ATC% / Checkout% / CVR).
5. Efficiency trend chart (CPM / CPC / Cost-per-ATC / Cost-per-Order).
6. Source attribution — channel mix donut, paid vs organic split.
7. Top landing pages table.
8. Meta campaign breakdown (campaign-level spend & metrics).

---

## 7. Dashboard Infrastructure

### 7.1 `dashboard.py` — entry point

- **Health-check**: `?health=1` query param → returns "OK" and stops (used by UptimeRobot keep-alive).
- **Password gate**: reads `st.secrets["DASHBOARD_PASSWORD"]`. If unset, no gate (dev mode). One-time login per session via `st.session_state["auth_ok"]`.
- **Auto-refresh**: `streamlit_autorefresh` every 5 minutes.
- **Sidebar**: includes a Claude-powered chat assistant ("WiseClaude") with a $0.50 / session budget cap.
- **Pages registered**:
  ```python
  st.navigation([
      st.Page("pages/executive_summary.py", title="Executive Summary", icon="🎯"),
      st.Page("pages/test.py",              title="Sales",             icon="📈"),
      st.Page("pages/test2.py",             title="Retention",         icon="🔄"),
      st.Page("pages/cohort.py",            title="Cohort Analysis",   icon="📊"),
      st.Page("pages/paid_ads.py",          title="Paid Ads Analysis", icon="📢"),
      st.Page("pages/paid_ads2.py",         title="Paid Ads 2",        icon="🎯"),
  ])
  ```

### 7.2 Streamlit Cloud secrets (`st.secrets`)

Required keys for production:

```toml
DASHBOARD_PASSWORD = "..."          # gates the entire app

# Service account JSON, raw (escaped newlines kept):
GOOGLE_SERVICE_ACCOUNT = '''{
  "type": "service_account",
  ...
}'''

# Shopify Admin tokens (per market) — used by paid_ads2 fallback paths
SHOPIFY_STORE_UAE = "wisewell-uae.myshopify.com"
SHOPIFY_TOKEN_UAE = "shpat_..."
SHOPIFY_STORE_KSA = "wisewellsa.myshopify.com"
SHOPIFY_TOKEN_KSA = "shpat_..."
SHOPIFY_STORE_USA = "sebastien-566.myshopify.com"
SHOPIFY_TOKEN_USA = "shpat_..."

# GA4 properties (where Google Analytics integration is wired)
GA4_PROPERTY_UAE = "..."
GA4_PROPERTY_KSA = "..."
GA4_PROPERTY_USA = "..."
```

### 7.3 Caching strategy

- All loaders use `@st.cache_data(ttl=300)` (5 min).
- `get_fx()` uses `ttl=3600` (1h).
- `_fetch_all_tabs()` parallel-fetches all RAW + HIST tabs in one cache slot.
- Apps Script aggregates run every 15 min, so dashboard cache + sheet cadence add up to a worst-case ~20 min staleness from event → dashboard.

---

## 8. File / Path Quick Reference

```
/Users/sami/Desktop/Claude Code/
├── dashboard.py               # entry point + sidebar + page router
├── utils.py                   # ALL data loaders + business rules (single big file)
├── auth.py                    # (legacy / unused?) auth helpers
├── chat_agent.py              # WiseClaude sidebar agent
├── ARCHITECTURE.md            # earlier (now-stale) version of this doc
├── pages/
│   ├── executive_summary.py
│   ├── test.py                # "Sales" page (legacy filename)
│   ├── test2.py               # "Retention" page (legacy filename)
│   ├── cohort.py
│   ├── paid_ads.py
│   └── paid_ads2.py
├── scripts/
│   ├── sync_marketing_spend.py     # Meta Ads → sheet (hourly cron)
│   ├── sync_google_ads.py          # Google Ads → sheet (pending API approval)
│   ├── sync_recharge.py            # Recharge failed-payments (separate dashboard)
│   ├── sync_inbound_queries.py     # Freshchat/Freshdesk queries (2x/day cron)
│   ├── sheet_refresh.py            # internal utility
│   ├── shopify_events_appscript.js # GAS web app (paste into script.google.com)
│   ├── shopify_pixel_UAE.js        # Shopify Customer Events pixel — UAE
│   ├── shopify_pixel_KSA.js        # Shopify Customer Events pixel — KSA
│   ├── shopify_pixel_USA.js        # Shopify Customer Events pixel — USA
│   ├── shopify_oauth.py            # one-off Shopify OAuth token capture
│   ├── register_shopify_webhooks.py# (one-off) register Shopify webhooks
│   ├── run_meta_sync.sh            # cron wrapper for sync_marketing_spend.py
│   └── run_sync.sh                 # cron wrapper for sync_recharge.py
├── docs/
│   └── data-architecture.md        # ← this document
└── .streamlit/
    └── secrets.toml                # local secrets (mirrors Streamlit Cloud secrets)
```

---

## 9. Known Gotchas & Edge Cases

### 9.1 Zapier writes Recharge dates in Asia/Dubai TZ for ALL markets

A USA order placed at 10pm EST May 6 (= 7am Dubai May 7) gets written as `created_at = "07/05/2026"`. Going forward this will be fixed by capturing full ISO timestamps; for now USA evening orders may shift one day forward in date assignments. See § 3.1.

### 9.2 Recharge `subscription_id` duplicates

Caused by Zapier retries. Cleanup is a one-off (we kept first occurrence per ID). If you re-pull old Recharge data and notice duplicates, dedupe on `subscription_id` keeping any non-DELETED row. Pure duplicates (all 15 raw cols identical) are safe to delete; CANCELLED + ACTIVE pairs need manual review (status drift).

### 9.3 USA Recharge product names differ from UAE/KSA

UAE: `"Wisewell Nano Subscription"` → Nano Tank.
USA: `"Wisewell Nano"` (no "Subscription" suffix) → Nano Tank.
The classifier handles both, but anyone querying the raw data needs to be aware.

### 9.4 USA Shopify ownership is excluded by design

USA machines are sold via Recharge (rent-to-own / monthly subs even for ownership). Counting `Shopify - USA` would double-count. Tab is preserved for future use.

### 9.5 `Marketing Spend` was renamed to `Paid Ads Spend - Monthly`

The loader checks both names. Ensure new code uses the new name. Legacy ARCHITECTURE.md still references the old name — this doc supersedes it.

### 9.6 Bot/scraper traffic spikes in `Shopify Website - UAE`

Pre-pixel data: April 1–13 2026 (avg 50k+ sessions, real ~3k) and May 4–5 2026 (10–13k) are bot waves. Real ATC/checkout/orders unchanged. Filter or annotate when reporting these dates.

### 9.7 Apps Script trigger nuances

- The 15-min trigger runs `aggregateToday()` which **only ever writes today's row** in each market's local TZ. Past dates are immutable.
- If you need to backfill a specific past date, run `aggregateDate("DD/MM/YYYY")` manually from the Apps Script editor.
- `aggregateYesterday()` was deprecated and removed (it conflicted with manually-pasted authoritative data).

### 9.8 The Apps Script writes to TWO sheets

- Raw events → `1j9lWQC9I8...` / `Store Events - Live`.
- Daily aggregates → `1NjPJKswE2rX...` / `Shopify Website - {market}` and `Sessions by Source - Daily` and `Top Landing Pages - Daily`.

### 9.9 ARR includes Machine + Filter subs but cancellation rate excludes filters

`_arr_usd_at()` includes both `category in {Machine, Filter}`. But `compute_cancellation_rate()` and the Retention page filter to `category == Machine` only. This is intentional (filter cancels are mostly autoswaps / replacements; machine churn is the real retention signal).

### 9.10 Charge interval normalization (30 → 1)

Recharge sometimes stores `charge_interval_frequency = 30` instead of `1` (30 days vs 1 month). The loader normalizes — if you query the raw sheet directly, do the same.

### 9.11 The Paid Ads 2 page uses Meta-only spend (intentionally)

Even though `Paid Ads Spend - Daily` includes Google, Paid Ads 2 reads `load_meta_ads_daily()` because its CTR/CPC/campaign-level metrics are Meta-specific. Mixing Google spend would muddle the per-platform analysis. The Executive Summary uses **blended** (Meta + Google) spend.

---

## 10. Replicating This Architecture (cheat-sheet for an agent building a parallel dashboard)

If you're building a parallel dashboard against this same workbook:

1. **Auth**: get a Google service-account JSON with read access to the workbook (and write access for sync scripts). Set scope `https://www.googleapis.com/auth/spreadsheets.readonly` for read-only consumers.

2. **Read order matters**: fetch all tabs in parallel — it's slow if serial. See `_fetch_all_tabs()` for the threadpool pattern.

3. **Always blend pre/post Sep 2025**. Don't try to compute live data for old months. The historical tabs are the truth and are NEVER modified by code.

4. **Filter DELETED first**. Every Recharge query starts with that.

5. **Use `is_true_cancel` for churn**, never raw `cancelled_at` presence.

6. **Use FX live with fallback**, don't hardcode rates anywhere except as a fallback safety net.

7. **Respect timezones in pixel data** — Apps Script does it correctly. Don't bucket by UTC if you're showing market-specific daily counts.

8. **For CAC**, prefer `Paid Ads Spend - Daily` over monthly-prorated. The daily tab is rebuilt each cron run from API actuals.

9. **For sales targets**, read `Projections` and pace-extrapolate MTD before comparing.

10. **For ownership**, always seed from Aug-2025 month-end and apply Sep-2025+ deltas (adds + offline + returns).

11. **The Apps Script is the single ingestion point** for browser-side events. If you want per-second data, you'd need to build a separate ingest layer; currently the 15-min upsert is good enough.

---

## Appendix A — Constants quick reference

```python
# utils.py — single source of truth
SHEET_ID            = "1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4"
RAW_EVENTS_SHEET_ID = "1j9lWQC9I8HdtTguzcGGX1AewE6KkdICkhbGYqwErKKU"

LIVE_DATA_START   = pd.Timestamp("2025-09-01")
OWNERSHIP_SEED_DT = pd.Timestamp("2025-08-01")

PRODUCT_ORDER = ["Model 1", "Nano+", "Bubble", "Flat", "Nano Tank"]

FX_FALLBACK = {"AED": 1/3.6725, "SAR": 1/3.7500, "USD": 1.0}

SHOPIFY_OWN_COLS = [
    ("Model 1",   "Units - Model 1 (Own)"),
    ("Nano+",     "Units - Nano+ (Own)"),
    ("Bubble",    "Units - Bubble (Own)"),
    ("Flat",      "Units - Flat (Own)"),
    ("Nano Tank", "Units - Nano (Own)"),
]
```

```javascript
// shopify_events_appscript.js
const RAW_SHEET_ID  = "1j9lWQC9I8HdtTguzcGGX1AewE6KkdICkhbGYqwErKKU";
const MAIN_SHEET_ID = "1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4";

const MARKETS = ["UAE", "KSA", "USA"];
const MARKET_TZ = {
  UAE: "Asia/Dubai",
  KSA: "Asia/Riyadh",
  USA: "America/New_York",
};
```

---

## Appendix B — Glossary

- **ARR**: Annual Recurring Revenue. `recurring_price × quantity × 12 / charge_interval_frequency` per active sub, summed and FX-converted.
- **ATC**: Add-to-cart event in the Shopify pixel.
- **CPC**: Cost per click.
- **CTR**: Click-through rate.
- **Filter sub**: A "Filter Subscription" or "Care+ Plan" — recurring filter replacement, not a machine sub. Excluded from machine-sales metrics.
- **Live era**: 2025-09-01 onwards. Computed dynamically.
- **Historical era**: Pre-2025-09-01. Hardcoded in the three `Monthly *` tabs.
- **Ownership**: One-time machine purchase (vs. subscription).
- **Pixel data**: Browser-side events captured by the Shopify Web Pixel. Has 15–25% undercount vs Shopify-native due to ad blockers + ITP.
- **True cancel**: `cancelled_at` is set AND `cancellation_reason` is not a swap/conversion. Used for ALL churn metrics.
- **Upsert**: The Apps Script's delete-then-write strategy that ensures one row per (date, market) for live aggregations.

---

*End of document. Last updated: 2026-05-07.*
