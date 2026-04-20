# Wisewell Dashboard — Data Architecture

> **Purpose:** Single source of truth for how the Streamlit dashboard reads, combines, and
> computes every metric. Read this before touching `utils.py`, `pages/sales.py`, or
> `pages/retention.py`. Update this file whenever a business rule changes.

---

## 1. Google Sheet

**Workbook:** `Wisewell - User Base Data`
**Sheet ID:** `1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4`

### Tabs the dashboard reads

| Tab | Type | Used for |
|-----|------|----------|
| `Recharge - UAE` | **Raw** | Subscription sales, cancellations, active users — UAE |
| `Recharge - KSA` | **Raw** | Subscription sales, cancellations, active users — KSA |
| `Recharge - USA` | **Raw** | Subscription sales, cancellations, active users — USA |
| `Shopify - UAE` | **Raw** | Ownership sales — UAE |
| `Shopify - KSA` | **Raw** | Ownership sales — KSA |
| `Shopify - USA` | **Raw** | Ownership sales — USA (currently no ownership; wired for future) |
| `Offline - Subscriptions` | **Raw** | Bank-transfer subscription orders (UAE only today) |
| `Offline - Ownership` | **Raw** | Bank-transfer ownership purchases (UAE + KSA) |
| `Returns` | **Raw** | Manually maintained by CS team — returned ownership machines |
| `Marketing Spend` | **Raw** | Monthly ad spend by country |
| `Monthly Sales` | **Historical** | Pre-Sep-2025 sales (hardcoded, treat as final truth) |
| `Monthly Cancellations` | **Historical** | Pre-Sep-2025 cancellations (hardcoded, treat as final truth) |
| `Monthly User Base` | **Historical** | Pre-Sep-2025 user base seed values (hardcoded) |

> **Rule:** Never read `Daily Sales`, `Daily Cancellations`, or any other calculated tab.
> All live metrics (Sep-2025 onwards) are computed from the Raw tabs above.

---

## 2. Raw Tab Column Maps

### Recharge — UAE / KSA / USA (identical schema)

| Col | Header | Notes |
|-----|--------|-------|
| A | `subscription_id` | Unique subscription identifier |
| B | `customer_id` | |
| C | `customer_email` | |
| D | `status` | `ACTIVE`, `CANCELLED`, `DELETED` |
| E | `product_title` | Used for product classification |
| F | `variant_title` | |
| G | `recurring_price` | Local currency |
| H | `quantity` | **Always use this — a quantity of 5 = 5 machines = 5 users/sales** |
| I | `sku` | |
| J | `order_interval_frequency` | |
| K | `charge_interval_frequency` | Billing interval in months (30 days → 1 month) |
| L | `cancelled_at` | Date of cancellation (blank if still active). Format: `d/m/yyyy` |
| M | `cancellation_reason` | Raw reason string |
| N | `cancellation_reason_comments` | |
| O | `created_at` | Subscription start date. Format: `d/m/yyyy` OR `yyyy-mm-dd` |
| Q+ | Calculated fields | `Cancelled User`, `Cancelled LTV`, `Cancellation Month`, `Model`, `True Cancellation`, `Week Ending` — these are **computed in the sheet** and can be read as convenience columns but the dashboard **re-derives** `is_true_cancel` from raw columns (see §4) |

#### DELETED rows
Rows where `status = DELETED` are **dropped immediately** on load. They never existed for dashboard purposes — no sales, no users, no cancellations.

### Shopify — UAE / KSA / USA (identical schema)

| Col | Header | Used for |
|-----|--------|---------|
| A | Order ID | Deduplication if needed |
| J | `Created at` | Ownership sale date |
| S | `Units - Model 1 (Own)` | Model 1 ownership qty |
| T | `Units - Model 1 (Sub)` | **IGNORED** — subscriptions come from Recharge |
| U | `Units - Nano+ (Own)` | Nano+ ownership qty |
| V | `Units - Nano+ (Sub)` | **IGNORED** |
| W | `Units - Bubble (Own)` | Bubble ownership qty |
| X | `Units - Bubble (Sub)` | **IGNORED** |
| Y | `Units - Flat (Own)` | Flat ownership qty |
| Z | `Units - Flat (Sub)` | **IGNORED** |
| AA | `Units - Nano (Own)` | Nano Tank ownership qty |
| AB | `Units - Nano (Sub)` | **IGNORED** |

> **Rule:** Use the **literal numeric value** (not boolean) in each ownership column.
> A row with `Units - Model 1 (Own) = 3` means 3 machines sold.

### Offline — Subscriptions

| Col | Header | Notes |
|-----|--------|-------|
| A | `Order ID` | |
| B | `Source` | e.g. Bank Transfer |
| C | `Email` | |
| D | `Customer Name` | |
| E | `Country` | `UAE`, `KSA`, `USA` |
| F | `Created at` | Format: `yyyy-mm-dd` or `d/m/yyyy` |
| G | `Lineitem Quantity` | Number of machines |
| H | `Lineitem name` | Product name — same naming as Recharge |

### Offline — Ownership

Same schema as Offline — Subscriptions.

### Returns (CS-maintained)

| Col | Header | Notes |
|-----|--------|-------|
| A | `Return Date` | Date machine was returned — `yyyy-mm-dd` |
| B | `Country` | `UAE`, `KSA`, `USA` |
| C | `Product` | Exact match to product names below |
| D | `Quantity` | Number of machines returned |
| E | `Customer Email` | |
| F | `Order ID` | Original Shopify / Offline order reference |
| G | `Notes` | Reason for return, any context |

---

## 3. Product Classification

### Machine products (the ones that matter for all metrics)

| Product | Recharge regex on `product_title` (lower-cased) | Shopify Own col |
|---------|------------------------------------------------|-----------------|
| Model 1 | `model\s*1.*subscription` | S |
| Nano+ | `nano\s*\+\s*subscription` | U |
| Bubble | `bubble.*subscription` | W |
| Flat | `wisewell\s*flat\s*subscription` (NOT "ownership", NOT "filter") | Y |
| Nano Tank | exact: `lower(title) == "wisewell nano subscription"` | AA |

> **Flat note:** The Google Sheet formula historically matched `"flat (subscription|ownership)"`.
> This was incorrect — sales must never appear as ownership in Recharge. Only match
> `wisewell\s*flat\s*subscription`.

### Excluded from machine metrics

- Any title containing `filter` (filter replacement subscriptions)
- Any title containing `care+` or `care+ plan` (filter plans)
- Any title containing `ownership` in Recharge (data quality edge-case — not a valid sales record)

### Offline product matching (Lineitem name, lower-cased)

| Product | Regex |
|---------|-------|
| Model 1 | `model\s*1\|\\bm1\\b` |
| Nano+ | `nano\s*\+` |
| Bubble | `bubble` (NOT filter) |
| Flat | `\\bflat\\b` (NOT filter) |
| Nano Tank | `\\bnano\\b` NOT `(\+\|plus)` |

---

## 4. Metric Definitions

### Sale
One machine unit dispatched to a customer, on its subscription start date (from Recharge `created_at`) or purchase date (from Shopify / Offline `Created at`). If `quantity = 5`, that is **5 sales**, not 1.

- **Subscription sale:** `Recharge created_at` × `quantity` — filtered to Machine products above.
- **Ownership sale:** `Shopify Units-(Product)(Own)` column value, date from `Created at`.
- **Offline sub sale:** `Offline - Subscriptions` date × qty × product regex.
- **Offline own sale:** `Offline - Ownership` date × qty × product regex.
- Shopify subscription unit columns (T, V, X, Z, AB) are **never used**.

### True Cancellation
A Recharge row is a true cancellation when **both** conditions hold:
1. `cancelled_at` is not blank
2. `cancellation_reason` (lower-cased) does **not** match `swapped|purchased|converted|swap|max`

Rows excluded from #2 are product-swap / upgrade events — the customer didn't churn,
they changed machine. They should appear in churn analysis with `is_true_cancel = False`.

```python
has_cancelled_at = df['cancelled_at_dt'].notna()
is_swap = df['cancellation_reason'].str.lower().str.contains(
    r'swapped|purchased|converted|swap|max', regex=True, na=False
)
df['is_true_cancel'] = has_cancelled_at & ~is_swap
```

### Active Subscribers (point-in-time, as of date T)
From Recharge: rows where `status == 'ACTIVE'`, summed by `quantity`. Or, for historical
lookback: rows where `created_at_dt <= T` AND (`cancelled_at_dt` is null OR `cancelled_at_dt > T`),
summed by `quantity`. Supports breakdown by `market` and `product`.

### Active Ownership Users (point-in-time, as of date T)
```
active_owners(T) =
    [Aug-2025 ending owners from Monthly User Base hardcoded tab]
  + [Shopify ownership sales where date >= 2025-09-01 and date <= T]
  + [Offline ownership where date >= 2025-09-01 and date <= T]
  - [Returns where return_date >= 2025-09-01 and return_date <= T]
```
Broken down by market and product.

### Active Users (Total, for KPI display)
`active_subscribers + active_owners` — by market and product.

### Cancellation Rate (MTD, extrapolated)
```
rate = (mtd_true_cancels / days_elapsed * days_in_month) / active_subs_at_prior_month_end
```
- **Numerator:** `is_true_cancel = True`, `cancelled_at_dt` in current month, filtered by market/product.
- **Denominator:** Active subscribers as of **last day of the prior month** (Recharge point-in-time).
- **No returns in either numerator or denominator.**
- Supports breakdown by market (UAE/KSA/USA/Global) and product.

### Global
`Global = UAE + KSA + USA` for all metrics.

---

## 5. Historical Data Boundary

| Period | Sales source | Cancellations source | User base source |
|--------|-------------|---------------------|-----------------|
| Before Sep-2025 | `Monthly Sales` tab (hardcoded, final truth) | `Monthly Cancellations` tab (hardcoded) | `Monthly User Base` tab (hardcoded) |
| Sep-2025 onwards | Recharge + Shopify + Offline (raw, live) | Recharge `cancelled_at` + `is_true_cancel` | Computed (see §4) |

When blending in time-series charts, stitch the two series at the Sep-2025 boundary.
Pre-Sep-2025 values are never recomputed from raw data.

---

## 6. Historical Tab Row Layouts (for reading pre-Sep-2025 data)

### Monthly Sales (all rows 0-indexed in Python list, i.e. Sheets row = Python idx + 1)

| Python idx | Sheets row | Label |
|-----------|-----------|-------|
| 3 | 4 | Total Gross Sales (UAE + KSA) |
| 6 | 7 | UAE Model 1 Subscription |
| 7 | 8 | UAE Nano+ Subscription |
| 8 | 9 | UAE Bubble Subscription |
| 9 | 10 | UAE Flat Subscription |
| 10 | 11 | UAE Nano Tank Subscription |
| 11 | 12 | UAE Total Subscription Sales |
| 13 | 14 | UAE Model 1 Ownership |
| 14 | 15 | UAE Nano+ Ownership |
| 15 | 16 | UAE Bubble Ownership |
| 16 | 17 | UAE Flat Ownership |
| 17 | 18 | UAE Nano Tank Ownership |
| 18 | 19 | UAE Total Ownership Sales |
| 20 | 21 | UAE Total Sales |
| 24 | 25 | KSA Model 1 Subscription |
| 25 | 26 | KSA Nano+ Subscription |
| 26 | 27 | KSA Bubble Subscription |
| 27 | 28 | KSA Flat Subscription |
| 28 | 29 | KSA Nano Tank Subscription |
| 29 | 30 | KSA Total Subscription Sales |
| 31 | 32 | KSA Model 1 Ownership |
| 32 | 33 | KSA Nano+ Ownership |
| 33 | 34 | KSA Bubble Ownership |
| 34 | 35 | KSA Flat Ownership |
| 35 | 36 | KSA Nano Tank Ownership |
| 36 | 37 | KSA Total Ownership Sales |
| 38 | 39 | KSA Total Sales |

Header row (idx 0): months as `Jan-23`, `Feb-23`, … `Aug-25`
First data column: index 1 (column B in Sheets)

### Monthly Cancellations

| Python idx | Sheets row | Label |
|-----------|-----------|-------|
| 3 | 4 | Total Cancellations (UAE + KSA) |
| 7 | 8 | UAE Model 1 |
| 8 | 9 | UAE Nano+ |
| 9 | 10 | UAE Bubble |
| 10 | 11 | UAE Flat |
| 11 | 12 | UAE Nano Tank |
| 12 | 13 | UAE Total Cancellations |
| 14 | 15 | UAE Model 1 Returns |
| 15 | 16 | UAE Nano+ Returns |
| 16 | 17 | UAE Bubble Returns |
| 17 | 18 | UAE Flat Returns |
| 18 | 19 | UAE Nano Tank Returns |
| 19 | 20 | UAE Total Returns |
| 21 | 22 | UAE Total Cancellations & Returns |
| 25 | 26 | KSA Model 1 |
| 29 | 30 | KSA Total Cancellations |
| 37 | 38 | KSA Total Returns |
| 39 | 40 | KSA Total Cancellations & Returns |

### Monthly User Base

| Python idx | Sheets row | Label |
|-----------|-----------|-------|
| 3 | 4 | Ending User Base (Global) |
| 7 | 8 | UAE Model 1 Subscribers |
| 8 | 9 | UAE Nano+ Subscribers |
| 9 | 10 | UAE Bubble Subscribers |
| 10 | 11 | UAE Flat Subscribers |
| 11 | 12 | UAE Total Subscribers |
| 13 | 14 | UAE Model 1 Owners |
| 14 | 15 | UAE Nano+ Owners |
| 15 | 16 | UAE Bubble Owners |
| 16 | 17 | UAE Flat Owners |
| 17 | 18 | UAE Total Owners |
| 19 | 20 | UAE Total User Base |
| 23 | 24 | KSA Model 1 Subscribers |
| 27 | 28 | KSA Total Subscribers |
| 29 | 30 | KSA Model 1 Owners |
| 33 | 34 | KSA Total Owners |
| 35 | 36 | KSA Total User Base |

---

## 7. Marketing Spend Tab

Columns: month label (`Jan-25` format), `Total Spend`, `UAE`, `KSA`.
Values are in USD. Parsed with `pd.to_datetime(label, format="%b-%y")`.

---

## 8. Cancellation Reason Normalisation

| Raw value (lower) | Display label |
|---|---|
| relocation | Relocation |
| water quality | Water Quality |
| personal/lifestyle, personal / lifestyle | Personal / Lifestyle |
| delivery delay | Delivery Delay |
| machine issues | Machine Issues |
| water capacity | Water Capacity |
| operational/error, operational / error | Operational / Error |
| switched to competitor | Switched to Competitor |
| financial | Financial |
| installation issues | Installation Issues |
| machine fit | Machine Fit |

---

## 9. Code Module Map

| File | Responsibility |
|------|----------------|
| `utils.py` | Credentials, raw tab fetching, all loader + compute functions |
| `dashboard.py` | Streamlit page config, sidebar filters, `st.navigation()` router |
| `pages/sales.py` | Sales KPIs, monthly bar chart, product donut, marketing spend |
| `pages/retention.py` | Cancellation KPIs, reason charts, cohort heatmap, user base trend |
| `ARCHITECTURE.md` | **This file** — single source of truth for business logic |

---

## 10. Key Decisions Log

| Date | Decision | Reason |
|------|----------|--------|
| 2026-04-20 | USA = subscriptions-only; no ownership for now | Not yet launched; may add offline ownership later |
| 2026-04-20 | Ignore Shopify subscription unit columns | Recharge is source of truth for all subscription data |
| 2026-04-20 | Flat regex excludes "ownership" in Recharge | Ownership in Recharge product title = data error; never valid |
| 2026-04-20 | Always use `quantity` field for all counts | Bulk orders (e.g. 5 machines) must count as 5 units |
| 2026-04-20 | Shopify ownership: use literal value (not 0/1 boolean) | Fixes under-counting for multi-unit orders |
| 2026-04-20 | Pre-Sep-2025 data = hardcoded tabs, final truth | Data before automated tracking was manually curated |
| 2026-04-20 | Global = UAE + KSA + USA | USA now included in all global metrics |
| 2026-04-20 | Cancellation rate denominator = subs only | Returns/owners excluded; rate measures subscription health |
| 2026-04-20 | Active user seed (ownership) = Aug-2025 Monthly User Base | Recharge has full sub history; only ownership needs a seed |
