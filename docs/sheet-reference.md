# Wisewell — Data Sheet Reference

> **What this is**: a complete description of the **"Wisewell - User Base Data"** Google Sheet
> workbook and the business rules you must apply when reading it. This is your single
> source of truth for any analytics, dashboard, or report built off this data.
>
> **Audience**: an engineer / agent building a dashboard, analytical tool, or data product
> against this workbook. Read all of § 4 (Business Rules) before writing a single query —
> the data is much more nuanced than the column names suggest.

---

## 0. TL;DR

Wisewell sells water-filtration machines (subscription + ownership) in 3 markets:
**UAE, KSA, USA**. The workbook contains every order, cancellation, ad-spend record,
and web-funnel event the company has — in granular form, mostly raw, with a small set
of frozen historical snapshots for pre-Sep-2025 data.

The five categories of data are:

1. **Subscription orders** — Recharge per market, one row per subscription (fed by Zapier in real-time).
2. **One-time machine purchases** — Shopify per market, columns count units sold.
3. **Offline orders** — bank-transfer / B2B sales (manual entry).
4. **Marketing spend** — Meta + Google Ads, daily and monthly granularity (synced hourly via APIs).
5. **Funnel / web analytics** — Shopify Web Pixel events aggregated to daily summaries.

A **`LIVE_DATA_START` boundary at 2025-09-01** splits frozen historical truth (three `Monthly *`
tabs — never edited) from live data computed from raw feeds (Sep-2025 onwards). Any time-series
spanning the boundary must blend the two.

Every consumer of this data must apply business rules around `DELETED` filtering,
true-cancellation logic, product classification, FX conversion, per-market timezones,
and ownership accounting. Read § 4.

---

## 1. The Workbook

| Item | Value |
|---|---|
| **Workbook name** | `Wisewell - User Base Data` |
| **Workbook ID** | `1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4` |
| **URL** | `https://docs.google.com/spreadsheets/d/1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4/edit` |

There is also a **separate raw events sheet** at:

| Item | Value |
|---|---|
| **Workbook ID** | `1j9lWQC9I8HdtTguzcGGX1AewE6KkdICkhbGYqwErKKU` |
| **Tabs** | `Store Events - Live` (raw pixel event log), `Channel Hist - {UAE,KSA,USA}` (historical channel-attribution exports) |

This second workbook exists to keep the main sheet from bloating with thousands of
pixel events per day. It's read separately when you need granular event data; for
day-level rollups, just read the main workbook.

### 1.1 Read access

- Authenticate with a **Google service account** that has been granted reader access
  to the workbook(s).
- API: Google Sheets API v4. Recommended scope: `https://www.googleapis.com/auth/spreadsheets.readonly`.
- The data refreshes continuously (some tabs by webhook, some by hourly cron). Cache
  on your end with a short TTL (5 min is a good default).

---

## 2. Tab Inventory

> **Critical**: the tabs listed below are the **only** authoritative tabs.
> The workbook contains many other tabs (`Daily Sales`, `Subscriber Base`, `Sheet27`,
> `Quarterly Investor Report`, `Cover | Readme`, `Wisewell Sales Targets`,
> `P-Table Cancellations UAE`, `Work In Progress`, `Claude - Test`, `Dashboard Summary`,
> etc.) that are derived/working/scratch tabs — **don't read them**, they are not
> sources of truth.

### 2.1 Main workbook tabs

| Tab | Type | Granularity | Populated by |
|---|---|---|---|
| `Recharge - UAE` | Raw — subscriptions | One row / sub | Zapier (real-time) |
| `Recharge - KSA` | Raw — subscriptions | One row / sub | Zapier (real-time) |
| `Recharge - USA` | Raw — subscriptions | One row / sub | Zapier (real-time) |
| `Shopify - UAE` | Raw — ownership | One row / order | Manual export from Shopify Admin |
| `Shopify - KSA` | Raw — ownership | One row / order | Manual export from Shopify Admin |
| `Shopify - USA` | Raw — ownership | One row / order | **Intentionally unused** — see § 4.4 |
| `Offline - Subscriptions` | Raw — manual | One row / order | Manual entry by ops/CS team |
| `Offline - Ownership` | Raw — manual | One row / order | Manual entry by ops/CS team |
| `Returns` | Raw — manual | One row / return | Manual entry by CS team |
| `Paid Ads Spend - Monthly` | Raw — synced | Monthly | Hourly cron from Meta + Google Ads APIs |
| `Paid Ads Spend - Daily` | Raw — synced | Daily | Hourly cron from Meta + Google Ads APIs |
| `Meta Ads Daily - Claude` | Raw — synced | Daily, by market | Hourly cron from Meta API |
| `Meta Ads Campaign Daily - Claude` | Raw — synced | Daily, by campaign | Hourly cron from Meta API (last 90 days) |
| `Shopify Website - UAE` | Aggregated daily | Daily | Apps Script `aggregateToday()` (every 15 min) + manual seed (pre-pixel data) |
| `Shopify Website - KSA` | Aggregated daily | Daily | Same |
| `Shopify Website - USA` | Aggregated daily | Daily | Same |
| `Sessions by Source - Daily` | Aggregated daily | Daily, by channel | Apps Script (every 15 min) |
| `Top Landing Pages - Daily` | Aggregated daily | Daily, by URL | Apps Script (every 15 min) |
| `Projections` | Manual | Monthly | Manually maintained sales targets |
| `Monthly Sales` | **Historical, frozen** | Monthly | **Hardcoded — do not modify** |
| `Monthly Cancellations` | **Historical, frozen** | Monthly | **Hardcoded — do not modify** |
| `Monthly User Base` | **Historical, frozen** | Monthly | **Hardcoded — do not modify** |

### 2.2 Raw events workbook tabs

| Tab | Type | Granularity |
|---|---|---|
| `Store Events - Live` | Raw event log | One row per pixel event (page_viewed, ATC, checkout_started, checkout_completed) |
| `Channel Hist - UAE` | Historical attribution | Daily — pre-pixel Shopify Analytics export |
| `Channel Hist - KSA` | Historical attribution | Daily |
| `Channel Hist - USA` | Historical attribution | Daily |

---

## 3. Tab Schemas

### 3.1 `Recharge - {UAE,KSA,USA}` — identical schema across markets

| Column | Type | Notes |
|---|---|---|
| `subscription_id` | string (numeric) | Primary key. Zapier retries occasionally produce duplicates — dedupe on `subscription_id` |
| `customer_email` | string | Used to tag cohorts |
| `status` | string | `ACTIVE`, `CANCELLED`, or `DELETED`. **`DELETED` rows must be filtered out before any calculation** (see § 4.3) |
| `product_title` | string | E.g. `"Wisewell Model 1 Subscription"`. Classified into category + product via the rules in § 4.5 |
| `recurring_price` | numeric | Local currency: AED for UAE, SAR for KSA, USD for USA |
| `quantity` | int | Almost always 1. Empty/null treated as 1 |
| `charge_interval_frequency` | int | 1 = monthly. **Value `30` must be normalized to `1`** (some Recharge records store the interval in days; 30 days = 1 month for ARR math) |
| `cancelled_at` | string date | `dd/mm/yyyy` or empty |
| `cancellation_reason` | string | Free-text. Normalize before grouping (see § 4.4 for the swap-vs-cancel rule) |
| `created_at` | string date | `dd/mm/yyyy` — the sale date (see § 6.1 for the timezone caveat) |

### 3.2 `Shopify - {UAE,KSA}`

Standard Shopify "Orders Export" CSV columns. Only these fields matter for analytics:

- `Created at` — date (the sale date)
- The **ownership unit columns**, named `Units - {Product} (Own)` for each product. Each is an integer count of one-time machine purchases (NOT subscriptions). The product-to-column mapping:

```
Model 1   → "Units - Model 1 (Own)"
Nano+     → "Units - Nano+ (Own)"
Bubble    → "Units - Bubble (Own)"
Flat      → "Units - Flat (Own)"
Nano Tank → "Units - Nano (Own)"     ← note: column header says "Nano", refers to Nano Tank
```

All other Shopify export columns are ignored.

### 3.3 `Offline - Subscriptions` and `Offline - Ownership`

| Column | Type | Notes |
|---|---|---|
| `Country` | string | `"UAE"` / `"KSA"` / `"USA"` |
| `Created At` | date | `dd/mm/yyyy` |
| `Lineitem Name` | string | Free-text product name. Classify via the offline rules in § 4.5.2 |
| `Lineitem Quantity` | int | Units sold |

### 3.4 `Returns`

| Column | Type | Notes |
|---|---|---|
| `date` | date | Date returned |
| `market` | string | `UAE` / `KSA` / `USA` |
| `product` | string | One of: `Model 1`, `Nano+`, `Bubble`, `Flat`, `Nano Tank` |
| `qty` | int | Units returned. **Subtracted from active ownership** (see § 4.2) |

### 3.5 `Paid Ads Spend - Monthly`

| Column | Notes |
|---|---|
| `Month` | First-of-month label like `Mar-26` (parsed `%b-%y`) |
| `Total Spend` | **Not trusted** — derive total from per-market columns (the Total formula can be stale) |
| `UAE` / `KSA` / `USA` | Per-market USD spend (blended Meta + Google) |
| `META` / `Google` | Per-platform USD spend |
| `UAE - META` / `KSA - META` / `USA - Facebook` | Per-market-per-platform spend |
| `UAE - Google` / `KSA - Google` / `USA - Google` | Per-market-per-platform spend |

### 3.6 `Paid Ads Spend - Daily`

Identical column layout to Monthly. The `Day` column is in `dd MMM, YYYY` format (e.g., `7 May, 2026`). For sub-month windows (MTD, trailing 7d), prefer this tab over the monthly proration.

### 3.7 `Meta Ads Daily - Claude`

| Column | Notes |
|---|---|
| `Date` | ISO date |
| `Market` | `UAE` / `KSA` / `USA` |
| `Spend (USD)` | Account-level Meta spend that day |
| `Clicks` | int |
| `Impressions` | int |
| `CTR (%)` | Click-through rate as percent |
| `CPC (USD)` | Cost per click |

One row per (date, market). Tab is fully rebuilt on every cron run (idempotent).

### 3.8 `Meta Ads Campaign Daily - Claude`

| Column | Notes |
|---|---|
| `Date` | ISO date |
| `Market` | `UAE` / `KSA` / `USA` |
| `Campaign ID` | Meta campaign ID |
| `Campaign Name` | Human-readable name |
| `Objective` | Meta objective (CONVERSIONS, TRAFFIC, etc.) |
| `Status` | `ACTIVE` / `PAUSED` / etc. |
| `Spend (USD)` | int |
| `Clicks` | int |
| `Impressions` | int |
| `CTR (%)` | percent |
| `CPC (USD)` | float |
| `CPM (USD)` | float |

One row per (date, market, campaign). Last 90 days of history; older rows are dropped on refresh.

### 3.9 `Shopify Website - {UAE,KSA,USA}`

| Column | Notes |
|---|---|
| `Day` | Daily date |
| `Sessions` | Total sessions |
| `Sessions with cart additions` | (also accepts the alias `add_to_cart`) |
| `Sessions that reached checkout` | (also accepts `reached_checkout`) |
| `Sessions that completed checkout` | (also accepts `completed_checkout`) |
| `Conversion rate` | Either a float (0.5) or a percent string (`"50%"` / `"50.0%"`) — handle both |
| `New sessions` *(post-pixel only)* | First-touch sessions |
| `Returning sessions` *(post-pixel only)* | Repeat-touch sessions |

> Pre-pixel data (before the Apps Script went live) was manually pasted from Shopify export.
> Post-pixel data is upserted by the Apps Script every 15 minutes. Both formats coexist; the
> column-alias tolerance handles the difference.

### 3.10 `Sessions by Source - Daily`

| Column | Notes |
|---|---|
| `date`, `market`, `channel` | `channel` is one of: `Direct`, `Organic Search`, `Organic Social`, `Paid Social (Meta/TikTok/Snapchat)`, `Paid Search (Google)`, `Paid Other`, `Email`, `SMS`, `Referral` |
| `utm_source`, `utm_campaign` | UTM parameters captured from URL |
| `sessions`, `add_to_cart`, `reached_checkout`, `completed_checkout` | int counts |

Multiple rows per (date, market) — one per channel/utm combination.

### 3.11 `Top Landing Pages - Daily`

| Column | Notes |
|---|---|
| `date`, `market` | |
| `page_path` | URL path |
| `sessions`, `add_to_cart` | int counts |

Top 10 landing pages per (date, market).

### 3.12 `Projections`

Free-form layout with multiple sections:

- **Top section**: header row of months (`Mar-26 → Mar-28`), then totals rows:
  - `Total Gross Sales - USA` — monthly USA target
  - `Total Gross Sales - GCC` — monthly UAE+KSA target (currently UAE only)
  - `Total Gross Sales - Global` — sum
- **Per-market sections** (`UAE`, `KSA`, `USA`) each with sub-rows for each product (subscription + ownership), summing to a `Total {market} Sales` row.

Parse by anchor labels (the section headers like `"UAE"`, `"KSA"`, `"USA"`) since the row positions can shift.

### 3.13 Historical tabs (`Monthly Sales`, `Monthly Cancellations`, `Monthly User Base`)

These are **frozen matrices** with months as columns and named rows. They are the single source of truth for **pre-Sep-2025** data. Row positions are fixed.

For each tab:
- Row 1 = month labels (`Jan-23, Feb-23, ..., Aug-25, ...`)
- Subsequent rows are named (e.g., `"Total Subscription Sales - UAE - Model 1"`) with monthly counts as cells.

> **Rule**: do not mutate these tabs through any automation. They're frozen reference data.

### 3.14 `Store Events - Live` (raw events workbook)

The complete pixel event log. Schema:

| Column | Notes |
|---|---|
| `timestamp` | ISO datetime when the event fired |
| `market` | `UAE` / `KSA` / `USA` |
| `source` | `"pixel"` |
| `event_type` | `page_viewed` / `product_added_to_cart` / `checkout_started` / `checkout_completed` |
| `session_id` | Shopify clientId |
| `page_path` | URL path |
| `product_id` / `product_title` / `value` / `currency` | Cart/checkout context |
| `order_id` / `checkout_id` | For checkout events |
| `referrer`, `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, `fbclid`, `gclid` | Attribution |

Raw stream — typically you'd aggregate to daily before reporting.

### 3.15 `Channel Hist - {UAE,KSA,USA}` (raw events workbook)

| Column | Notes |
|---|---|
| `Day` (or `Month` for older USA) | Daily date |
| `Referrer source` | Shopify-bucketed source category |
| `UTM source`, `UTM medium`, `UTM campaign` | UTM params |
| `Sessions`, `Sessions with cart additions`, `Sessions that reached checkout`, `Sessions that completed checkout` | int |
| `Conversion rate`, `Checkout conversion rate` | percent |

Pre-pixel historical attribution data. One-time export from Shopify Analytics.

---

## 4. Business Rules (READ BEFORE WRITING ANY QUERY)

These rules apply across many tabs and are non-negotiable. Skipping any of them produces
silently wrong numbers.

### 4.1 The `LIVE_DATA_START` boundary — 2025-09-01

```
LIVE_DATA_START   = 2025-09-01    # start of live era
OWNERSHIP_SEED_DT = 2025-08-01    # Aug-2025 month-end ownership baseline
```

**Rule**: any series spanning pre- and post-Sep-2025 data (sales, cancellations, user base)
must be **blended**:
- **Pre-Sep-2025 data** → read from the three `Monthly *` tabs (frozen historical truth).
- **Sep-2025 onwards** → compute live from raw Recharge / Shopify / Offline feeds.

**Never** read pre-Sep-2025 data from the live Recharge feed — early data has gaps and
inconsistencies. Always blend.

### 4.2 Ownership accounting (Sep-2025 onwards)

```
ownership_at(t)  =  AUG_2025_SEED
                 +  Σ Shopify ownership UAE/KSA from 2025-09-01 to t
                 +  Σ Offline ownership from 2025-09-01 to t
                 −  Σ Returns from 2025-09-01 to t
```

- **Seed**: read the Aug-2025 column from `Monthly User Base` for ownership rows.
- **Floor**: clip to ≥ 0 (never negative).
- **USA is excluded from Shopify ownership** (see § 4.4).

### 4.3 DELETED Recharge subscriptions

```
df = df[df["status"].fillna("").str.strip().str.upper() != "DELETED"]
```

- DELETED rows are excluded from **every** metric.
- They're orphan/test/buggy entries (some have garbage `created_at` like `30/12/1899`).
- This must be the very first filter applied to any Recharge query.

### 4.4 USA Shopify is excluded by design

The USA Shopify store routes 100% of machine revenue through Recharge (rent-to-own,
monthly subscriptions even for "ownership" semantically). Counting `Shopify - USA` would
double-count the same units. The tab is preserved in the workbook for future use but
must not be read.

UAE and KSA Shopify ownership tabs are read normally.

### 4.5 True cancellations vs swaps

```
SWAP_REASONS = {"swapped", "purchased", "converted", "swap", "max"}
is_true_cancel = (cancelled_at is not null)
                 AND (cancellation_reason.lower() not in SWAP_REASONS)
```

- A **true cancel** = customer left and isn't returning under another product.
- Swaps / conversions / upgrades are NOT churn (the customer was retained on a different SKU).
- **All churn metrics use `is_true_cancel == True` only**.

### 4.6 Product classification

#### 4.6.1 Recharge — classify the `product_title`

Returns `(category, product)` where category is one of `Machine`, `Filter`, or `None` (excluded), and product is one of `Model 1`, `Nano+`, `Bubble`, `Flat`, `Nano Tank` (or `None`).

Rules in order, first match wins:

1. **Filter category** — if title contains `"filter subscription"`, `"care+ plan"`, or `"care+"` → `("Filter", parent_product)`. Parent product is inferred from the title.
2. **Ownership exclusion** — title contains `"ownership"` → return `(None, None)`. EXCEPTION: `"bubble ownership + holiday set"` → `("Machine", "Bubble")` (specific promo bundle).
3. **Machine matching** (case-insensitive regex on lowercased title):
   - `model\s*1.*subscription` OR exact `wisewell model 1` → `("Machine", "Model 1")`
   - `nano\s*\+\s*subscription` → `("Machine", "Nano+")`
   - `bubble.*subscription` → `("Machine", "Bubble")`
   - `wisewell\s*flat\s*subscription` (and not "filter") → `("Machine", "Flat")`
   - `wisewell nano subscription` (UAE/KSA) OR `wisewell nano` exact (USA) → `("Machine", "Nano Tank")`

> **Critical USA-specific rule**: USA Recharge records a Nano Tank subscription with the
> title `"Wisewell Nano"` (no "Subscription" suffix). UAE/KSA use `"Wisewell Nano Subscription"`.
> Your classifier must handle both.

#### 4.6.2 Offline — classify the `Lineitem Name`

Pure regex on the line-item name:
- `\bnano\b` (NOT followed by `+` or `plus`) → `Nano Tank`
- `model\s*1` OR `\bm1\b` → `Model 1`
- `nano\s*\+` → `Nano+`
- contains `bubble` AND NOT `filter` → `Bubble`
- `\bflat\b` AND NOT `filter` → `Flat`
- otherwise → `None` (skipped)

### 4.7 FX conversion to USD

Recharge subscriptions in UAE and KSA are billed in local currency (AED, SAR). To produce
USD figures (ARR, revenue):

- Use **live FX rates** from a reliable source (e.g., `https://open.er-api.com/v6/latest/USD`),
  cached for ~1 hour.
- **Fallback to fixed pegs** if the API fails (AED and SAR are USD-pegged anyway):

```
AED → USD:  1 / 3.6725  ≈  0.27226
SAR → USD:  1 / 3.7500  ≈  0.26667
USD → USD:  1.0
```

ARR per active subscription = `recurring_price × quantity × (12 / charge_interval_frequency) × FX_to_USD`

### 4.8 Charge-interval normalization

Recharge stores `charge_interval_frequency` as an int. Most subs are `1` (1 month).
Some legacy records store `30` (30 days). Treat them equivalently: **normalize 30 → 1**
before applying the ARR formula `× (12 / freq)`.

### 4.9 Per-market timezones (for daily bucketing)

Each market's "daily" boundary is in its own local timezone, NOT UTC and NOT the
operator's timezone:

```
UAE → Asia/Dubai
KSA → Asia/Riyadh
USA → America/New_York
```

This matters for any time-series at daily granularity. A USA event at 10pm ET on May 6
should be counted as May 6 (USA), not May 7 in any other timezone.

For the pixel pipeline, this is already correctly handled: the Apps Script that aggregates
events buckets each event by its market's local TZ. But if you query the raw event log
yourself, you must apply the same conversion.

### 4.10 ARR includes Filter subs; churn excludes them

When computing **ARR (USD)**, sum across active subs where `category` is in `{Machine, Filter}` —
Filter subscriptions are recurring revenue too.

When computing **churn rate**, filter to `category == Machine` only. Filter cancels are
mostly autoswaps / replacements; they're noise. Machine churn is the real retention signal.

### 4.11 Pro-rata projections (current month)

When showing month-to-date numbers vs full-month comparisons, project to a full-month rate:

- **Churn rate**: `(MTD true cancels × days_in_month / days_into_month) / active_at_month_start`. Compare to prior **full** month's actual rate.
- **CAC / spend**: prefer daily actuals from `Paid Ads Spend - Daily`. For days the daily tab
  doesn't cover, the monthly tab is **cumulative actuals to-date** (NOT a projection), so
  for the current month divide by `days_elapsed`, not `days_in_month`.

### 4.12 Bot/scraper traffic awareness

`Shopify Website - UAE` has two known bot-traffic spikes pre-pixel:

- **April 1–13, 2026**: ~20k–130k sessions/day (real avg ~3k)
- **May 4–5, 2026**: ~10k–13k sessions/day with 0.04% Paid-Search CVR

Real customer behavior (ATC, checkout, orders) was unchanged on those days. The pixel
pipeline filters most of this going forward, but historical reports should annotate or
exclude these spikes.

---

## 5. Data Pipelines (how each tab gets fresh data)

You don't need to build these pipelines yourself — they're already running. This section
explains the freshness, cadence, and known caveats so you can reason about staleness.

### 5.1 Recharge → `Recharge - {market}`

- **Source**: Recharge subscription billing platform.
- **Mechanism**: Zapier Zaps (one per market). Triggered on subscription create / update.
- **Cadence**: Real-time (typically <1 min after the event).
- **Caveat — duplicates**: occasional Zapier retries produce the same `subscription_id` twice.
  Always dedupe on `subscription_id` (keep first occurrence).
- **Caveat — timezone**: Zapier formats `created_at` in the Zapier account's timezone (Asia/Dubai)
  for ALL markets. A USA order placed at 10pm EST May 6 (= 7am Dubai May 7) gets written as
  `created_at = "07/05/2026"`. Currently uncorrected — be aware when bucketing USA daily numbers.

### 5.2 Shopify ownership → `Shopify - {UAE,KSA}`

- **Source**: Shopify Admin "Orders Export".
- **Mechanism**: Manual paste by operator.
- **Cadence**: Periodic (typically weekly).

### 5.3 Offline & Returns → `Offline - *`, `Returns`

- **Source**: ops/CS team (manual).
- **Cadence**: As-needed.

### 5.4 Meta Ads → `Paid Ads Spend - *`, `Meta Ads Daily - Claude`, `Meta Ads Campaign Daily - Claude`

- **Source**: Meta Graph API v19.0 (insights endpoints).
- **Mechanism**: Hourly cron job.
- **Cadence**: Updates every hour (latest hour is typically lagged by ~10 min on Meta's side).
- **Idempotency**: Each run rebuilds the affected tabs from scratch.

### 5.5 Google Ads → `Google Ads - Claude`

- **Source**: Google Ads API v19.
- **Status**: Wired but not yet live (waiting on API approval).

### 5.6 Shopify Web Pixel → raw events → daily aggregates

This pipeline has 4 components:

1. **Pixel scripts** (one per Shopify store) installed under Settings → Customer Events.
   They subscribe to `page_viewed`, `product_added_to_cart`, `checkout_started`,
   `checkout_completed` and POST to a Google Apps Script web app endpoint with full
   attribution context (UTM, referrer, click-IDs).
2. **Apps Script `doPost(e)`** appends each event as one row to `Store Events - Live`
   in the raw events workbook.
3. **Apps Script `aggregateToday()`** runs every 15 minutes via time-driven trigger.
   It re-aggregates today's events (using each market's local TZ) and **upserts** the
   daily summary rows into:
   - `Shopify Website - {UAE,KSA,USA}`
   - `Sessions by Source - Daily`
   - `Top Landing Pages - Daily`
4. **Past dates are immutable**. `aggregateToday()` only writes today's row.
   For backfill, an `aggregateDate("DD/MM/YYYY")` function exists for one-off use.

### 5.7 Sales projections → `Projections`

- **Source**: manually maintained.
- **What it represents**: monthly sales targets per market and per product.

---

## 6. Known Gotchas

### 6.1 Zapier writes Recharge dates in Asia/Dubai TZ for all markets

Already covered in § 5.1. Be aware when reporting USA daily numbers near the EST/Dubai
date boundary.

### 6.2 Recharge `subscription_id` duplicates

Zapier retries. Dedupe on `subscription_id`. Pure duplicates (all columns identical) are
safe to drop. CANCELLED + ACTIVE pairs need manual review (status drift).

### 6.3 USA Recharge product titles differ from UAE/KSA

UAE/KSA: `"Wisewell Nano Subscription"` → Nano Tank.
USA: `"Wisewell Nano"` (no `"Subscription"` suffix) → Nano Tank.
Your classifier must handle both.

### 6.4 USA Shopify ownership is excluded by design

USA machine "ownership" goes through Recharge (rent-to-own / monthly subs). Counting
`Shopify - USA` would double-count.

### 6.5 The `Marketing Spend` tab was renamed

Now called `Paid Ads Spend - Monthly`. Old references in documentation may still mention
the old name — assume they mean the new one.

### 6.6 Bot/scraper session spikes in `Shopify Website - UAE`

Already noted in § 4.12. Annotate or exclude April 1–13 and May 4–5, 2026.

### 6.7 ARR scope vs. Churn scope

ARR includes Machine + Filter subs. Churn metrics include only Machine subs. Don't
accidentally compute "churn rate against ARR-active subs" — those denominators differ.

### 6.8 The pixel pipeline writes to TWO workbooks

Raw events → `1j9lWQC9I8...`. Daily aggregates → main workbook `1NjPJKswE2rX...`.
If you only read the main workbook you get the rollups; for granular event analysis
read the raw events workbook too.

---

## 7. Building on This Data — Cheat-Sheet

If you're building a dashboard, analytical tool, or report against this workbook:

1. **Auth**: get a Google service-account JSON with read access to both workbooks.
   Scope `https://www.googleapis.com/auth/spreadsheets.readonly` is sufficient.
2. **Read order matters**: fetch all tabs in parallel — sequential reads are slow.
3. **Always blend pre/post Sep 2025**. Don't try to compute live data for old months.
4. **Filter `DELETED` first**. Every Recharge query starts with this.
5. **Use `is_true_cancel` for churn**, never raw `cancelled_at` presence.
6. **Use FX live with a hardcoded peg fallback**, don't hardcode rates.
7. **Respect timezones in pixel data** — bucket by market local TZ.
8. **For CAC**, prefer `Paid Ads Spend - Daily` actuals over monthly proration.
9. **For sales targets**, read `Projections` and pace-extrapolate MTD.
10. **For ownership**, always seed from Aug-2025 month-end and apply Sep-2025+ deltas (adds + offline + returns).
11. **Cache reads** for ~5 minutes. The data is updated continuously but doesn't need
    to be queried per-request.

---

## Appendix A — Constants quick reference

```
SHEET_ID            = "1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4"   # main user base
RAW_EVENTS_SHEET_ID = "1j9lWQC9I8HdtTguzcGGX1AewE6KkdICkhbGYqwErKKU"   # raw pixel events

LIVE_DATA_START   = 2025-09-01
OWNERSHIP_SEED_DT = 2025-08-01

PRODUCTS = ["Model 1", "Nano+", "Bubble", "Flat", "Nano Tank"]
MARKETS  = ["UAE", "KSA", "USA"]

FX_FALLBACK = {
    "AED": 1 / 3.6725,
    "SAR": 1 / 3.7500,
    "USD": 1.0,
}

MARKET_TZ = {
    "UAE": "Asia/Dubai",
    "KSA": "Asia/Riyadh",
    "USA": "America/New_York",
}

SHOPIFY_OWNERSHIP_COLUMNS = {
    "Model 1":   "Units - Model 1 (Own)",
    "Nano+":     "Units - Nano+ (Own)",
    "Bubble":    "Units - Bubble (Own)",
    "Flat":      "Units - Flat (Own)",
    "Nano Tank": "Units - Nano (Own)",
}
```

---

## Appendix B — Glossary

- **ARR**: Annual Recurring Revenue. `recurring_price × quantity × 12 / charge_interval_frequency` per active sub, summed and FX-converted.
- **ATC**: Add-to-cart event in the Shopify pixel.
- **CAC**: Customer Acquisition Cost. Marketing spend ÷ new sales.
- **CPC**: Cost per click.
- **CTR**: Click-through rate.
- **Filter sub**: A "Filter Subscription" or "Care+ Plan" — recurring filter replacement, not a machine sub. Excluded from machine-sales metrics.
- **Live era**: 2025-09-01 onwards. Computed dynamically from raw feeds.
- **Historical era**: Pre-2025-09-01. Hardcoded in the three `Monthly *` tabs.
- **Machine sub**: A recurring subscription for one of the 5 products (Model 1, Nano+, Bubble, Flat, Nano Tank). Distinguished from Filter subs.
- **Ownership**: One-time machine purchase (vs. subscription). Tracked via Shopify ownership unit columns + Offline ownership tab.
- **Pixel data**: Browser-side events captured by the Shopify Web Pixel. Has 15–25% undercount vs Shopify-native due to ad blockers + Safari ITP.
- **True cancel**: `cancelled_at` is set AND `cancellation_reason` is not a swap/conversion. Used for ALL churn metrics.
- **Upsert**: The Apps Script's delete-then-write strategy that ensures one row per (date, market) for live aggregations.

---

*Last updated: 2026-05-07.*
