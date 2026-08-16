# Wisewell "User Base Data" — Agent Reading Guide

**Purpose:** everything an autonomous agent needs to read Wisewell sales, user-base and
cancellation data correctly from the *User Base Data* workbook — without re-deriving the
business rules or repeating known mistakes.

- **Workbook (the only source of truth):** `1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4`
- **Markets:** `UAE` (AED) · `KSA` (SAR) · `USA` (USD)
- **Every claim below was verified against the live workbook and `utils.py` on 2026-08-17.**
  Row counts marked *(as of 2026-08-17)* drift — re-check them, don't trust them blindly.

> **Golden rule:** this workbook is *read-only* for agents. `Recharge - *` tabs are written
> live by Zapier; the ops team owns the manual tabs. Never write to a tab you did not create.

---

## 1. How to read it

### Mode A — reuse the dashboard's data layer (STRONGLY PREFERRED)

The repo `alsami-cmyk/wisewell-dashboard` already encodes every rule in this document.
If the agent can run Python in that repo, do **not** re-implement anything:

```python
from utils import (
    load_recharge_full,        # all subscriptions, all markets, fully classified
    get_all_machine_sales,     # unified daily machine sales (subs + ownership)
    get_monthly_sales_blended, # historical + live monthly sales
    get_monthly_cancellations_blended,
    get_active_subscriptions, get_active_ownership,
)

rc = load_recharge_full()   # one row per subscription; schema below
```

`load_recharge_full()` returns these guaranteed columns (all verified present):

| column | meaning |
|---|---|
| `subscription_id` | unique id (`justlife_`-prefixed for Justlife rows) |
| `customer_email`, `status` | `ACTIVE` / `CANCELLED` (`DELETED` already removed) |
| `product_title`, `variant_title`, `sku` | raw values from source |
| `category` | `Machine` \| `Filter` \| `None` |
| `product` | canonical product (see §4) |
| `recurring_price`, `quantity`, `charge_interval_frequency`, `currency` | billing |
| `arr_local` | annualised value, **ACTIVE machine subs only**, in local currency |
| `created_at_dt`, `cancelled_at_dt` | parsed dates (USA start dates already corrected) |
| `cancellation_reason` | canonical reason (see §5) |
| `is_true_cancel` | **use this for churn**, not `cancelled_at` (see §5.2) |
| `market` | `UAE` \| `KSA` \| `USA` |

Auth is handled by `utils.get_credentials()` — service account from
`st.secrets["GOOGLE_SERVICE_ACCOUNT"]`, else local `token.json`.

### Mode B — read the Sheets API directly

Only if the agent cannot run the repo. Then it **must** implement §4 and §5 itself.

```python
svc.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=f"'{tab_name}'").execute()
```

- Read the **whole tab** (`'Tab Name'`, no A1 range) — column counts drift.
- Row 1 is the header; pad short rows (Sheets truncates trailing empties).
- Scope `https://www.googleapis.com/auth/spreadsheets.readonly` is enough.
- **Auth caveat:** the service-account identity can read *this* workbook, but **not** the
  external ops workbook `1lnsUjLYOpk2LrnCPSjQbtlctOC35t4mrWmOZnDchW5I` (403). Local
  `token.json` (Sami's own account) can read both.
- Cache ~5 min (`@st.cache_data(ttl=300)`). Do not hammer the API.

---

## 2. The historical / live boundary — the most important concept

```
LIVE_DATA_START = 2025-09-01
```

| period | authority |
|---|---|
| **before 2025-09-01** | the monthly matrix tabs (`Monthly Sales`, `Monthly Cancellations`, `Monthly User Base`) |
| **2025-09-01 onward** | live row-level tabs (`Recharge - *`, `Shopify - *`, offline tabs) |

Reason: `Recharge - *` strips `DELETED` rows, so it *undercounts* history; and ownership flow
data only starts Sep-2025. Never reconstruct pre-Sep-2025 totals from Recharge alone — the
numbers come out too low. `get_monthly_sales_blended()` stitches both eras.

---

## 3. Where each required dataset lives

### 3.1 Sales

| dataset | tab(s) | notes |
|---|---|---|
| **Historical sales (pre-Sep-2025)** | `Monthly Sales` | month-per-column matrix; **row position = (market, product, is_ownership)** via hardcoded `_HIST_SALES_ROWS`. Header = `Jan-23 … Dec-26`. |
| **Historical UAE subs w/ corrected start dates** | **`Frozen Seed (UAE)`** | subscription-level, `start_date_corrected` on **100%** of rows. See §3.4 — **currently read by NO code.** |
| **Live subscription sales — all markets** | `Recharge - UAE`, `Recharge - KSA`, `Recharge - USA` | one row per subscription; sale date = `created_at`. Zapier-fed, near-real-time. |
| **USA true start dates** | `Recharge - USA Seed` | override only — see §3.4. |
| **UAE marketplace** | `Justlife - UAE` | counted as UAE sales + churn; flagged `is_partner` (excluded from CAC). |
| **Live ownership sales** | `Shopify - UAE`, `Shopify - KSA` | unit columns per product. **`Shopify - USA` is fetched but intentionally NOT used** (all USA machine revenue flows through Recharge; using it double-counts). |
| **Offline / B2B** | `Offline - Subscriptions`, `Offline - Ownership` | flagged `is_offline` → excluded from CAC. |

> ### ⚠️ USA: ONE live source only
> **USA live data = `Recharge - USA`, plus `Recharge - USA Seed` for corrected start dates.
> Nothing else.**
> The Stripe-era subscriptions were **migrated into Recharge**, so they are *already* rows in
> `Recharge - USA` — only their `created_at` shows the migration date, which is exactly what the
> seed corrects. The old `Stripe - USA` tab has been **deleted from the workbook** and removed
> from the code. Never re-introduce it: appending it would **double-count** the very
> subscriptions the seed exists to fix.

### 3.2 Cancellations

| dataset | tab | notes |
|---|---|---|
| **Historical (pre-Sep-2025)** | `Monthly Cancellations` | month-matrix, `(market, product)` row map `_HIST_CANCEL_ROWS`. **True cancels only.** |
| **Live — all markets** | the same `Recharge - *` tabs | a cancellation is a *column on the subscription row*: `cancelled_at` + `cancellation_reason`. There is **no separate live cancellations tab.** |
| **Historical UAE detail** | `Frozen Seed (UAE)` | cancelled rows carry `cancelled_at`, `cancel_month`, `cancellation_reason`. |
| (blended) | — | `get_monthly_cancellations_blended()` stitches both eras. |

> `Daily Sales` / `Daily Cancellations` tabs exist but are **presentation matrices**
> (day-per-column) and are **not** read by the dashboard. Prefer the row-level sources.

### 3.3 User base

`Monthly User Base` is the manually maintained ground truth for **pre-Sep-2025** active counts
(`_HIST_UB_SUB_ROWS` subscriptions, `_HIST_UB_OWN_ROWS` ownership; Nano Tank absent — it
launched later). After Sep-2025, active counts are computed live from Recharge + Shopify.

### 3.4 The two seed tabs (corrected start dates)

Both exist because subscriptions were **migrated into Recharge**, so Recharge's `created_at` is
the *migration* date, not the customer's true start.

**`Frozen Seed (UAE)`** — the richest historical asset in the workbook.
*(as of 2026-08-17: 8,726 rows — 6,605 ACTIVE / 2,121 CANCELLED; `start_date_corrected`
populated on 8,726/8,726; `start_month` spans 2021-12 → 2026-06.)*

| column | use |
|---|---|
| `start_date_corrected` | **the true start date** (100% populated). Format `DD/MM/YYYY`. |
| `start_month` | pre-computed `YYYY-MM` |
| `recharge_created_at_raw` | the uncorrected Recharge value, for audit |
| `start_date_source` | `recharge_genuine` 7,230 · `shopify_recovered` 867 · `swap_recovered_genuine` 311 · `cancellation_file` 239 · `migration_stamp_unrecovered` 44 · `shopify_recovered_swap` 35 |
| `relationship_class` | `genuine_new` 7,034 · `recovered_pre_migration` 867 · `swap_continuation` 346 · `pre_migration_churn` 239 · `prior_owner_new_sub` 196 · `unrecovered_stamp` 44 |
| `record_source` | `recharge` 8,487 · `pre_migration_cancellation` 239 |
| `possible_duplicate` | `review` on 6 rows — exclude or inspect |
| `cancelled_at`, `cancel_month`, `cancellation_reason` | churn detail |

> ⚠️ **`Frozen Seed (UAE)` is in NO code path.** It is not in `RAW_TABS`; the dashboard's
> pre-Sep-2025 UAE figures come from the `Monthly *` matrices instead. Agents using the seed
> will get *different* (more granular, start-date-corrected) numbers than the dashboard —
> that is expected. Treat `relationship_class` carefully: `swap_continuation` and
> `prior_owner_new_sub` are **not** straightforward new customers.
>
> It is also **not a superset** of `Recharge - UAE`: *(as of 2026-08-17)* Recharge-UAE has
> 10,529 ids vs the seed's 8,726 — **8,486 overlap, 240 seed-only** (pre-migration
> cancellation records).

**`Recharge - USA Seed`** — `subscription_id → adjusted_created_at`. **This one IS wired in:**
`load_recharge_full()` overrides `created_at_dt` for matching USA rows.
Columns: `subscription_id`, `adjusted_created_at`, `recharge_created_snapshot`, `stripe_link`,
`in_recharge_usa`. *(as of 2026-08-17: 491 rows, 429 `Y` / 62 `N`.)*
The number actually applied each run is lower than 429 and drifts, because the live tab loses
rows as ops deletes them (`applied USA start-date seed to N subs` in the logs).
The live `Recharge - USA` tab is **never modified** — it self-corrects over time, at which point
the override becomes a harmless no-op.

### 3.5 ⚠️ Tabs the code fetches that NO LONGER EXIST

`RAW_TABS` still lists 8 tabs that are absent from the workbook *(as of 2026-08-17)*. Each
fails with 3 retries on every cold load (`All tabs: 16/24 OK`):

```
Returns · Meta Ads Daily - Claude · Meta Ads Campaign Daily - Claude
Shopify Website - UAE / KSA / USA · Sessions by Source - Daily · Top Landing Pages - Daily
```

Consequence to be aware of: **`Returns` is missing, so returned ownership machines are no
longer subtracted** — `load_offline_returns()` silently returns 0 rows. The paid-ads/website
tabs were migrated elsewhere. Loaders fail soft (empty frame), so nothing crashes — but do not
assume these datasets are available.

---

## 4. Classifications — products

### 4.1 Canonical products

```
PRODUCT_ORDER = ["Model 1", "Nano+", "Bubble", "Flat", "Nano Tank", "Sparkle"]
```

Two categories: **`Machine`** (the hardware subscription — what "sales" means) and **`Filter`**
(consumable add-on, tracked separately, excluded from machine metrics).

### 4.2 Mapping raw `product_title` → canonical product

Match on the **lowercased** title, in this order (mirrors `_classify_recharge_product`):

1. **Filter first** — title contains `filter subscription`, `care+ plan`, or `care+`
   → category `Filter`, product from the qualifier (`(model 1)` → Model 1, `nano+` → Nano+,
   `bubble` → Bubble, `flat` → Flat; bare `filter subscription` → Model 1).
2. **`ownership` in a Recharge title = data error → exclude**
   (sole exception: `Wisewell Bubble Ownership + Holiday Set`, a real promo bundle).
3. **Machine** regexes:

| canonical | matches |
|---|---|
| `Model 1` | `model\s*1.*subscription`, or exactly `wisewell model 1` (USA) |
| `Nano+` | `nano\s*\+\s*subscription` |
| `Bubble` | `bubble.*subscription`, or the holiday bundle |
| `Flat` | `wisewell\s*flat\s*subscription` (and not "filter") |
| `Sparkle` | `sparkle.*subscription` (and not "filter") — **US + UAE**, added 2026-07 |
| `Nano Tank` | title is exactly `wisewell nano subscription` **or** `wisewell nano` |

> **Trap (verified):** `Wisewell Nano Subscription` (UAE) and `Wisewell Nano` (USA) both mean
> **Nano Tank**, *not* Nano+. `Nano+` always carries an explicit `+`.

### 4.3 Pricing / ARR

- `arr_local = recurring_price × quantity × (12 / charge_interval_frequency)`, **ACTIVE machine
  subs only**. A `charge_interval_frequency` of `30` is normalised to `1` (monthly).
- Convert with `get_fx()` (live rates, 1 h cache; fallback pegs AED 3.6725, SAR 3.75).
- **Trust the sheet's own `recurring_price`** for all Recharge rows, every market — USA prices
  genuinely vary and must not be forced to a flat figure.
- The only imputed prices are `Justlife - UAE` (feed carries none):
  AED Model 1 150 · Nano+ 99 · Bubble 199 · Flat 139 · Nano Tank 99.

---

## 5. Classifications — cancellations

### 5.1 Canonical reasons (2026-07 consolidation)

Every legacy/free-text reason maps onto this set. Recharge now emits these directly; the mapping
exists so **historical** rows read consistently.

```
Water Quality · Water Capacity / Usage Preference · Product Experience & Usability
Delivery Delay · Service Failure · Machine Malfunction · Relocation
Personal / Lifestyle Change · Financial Constraint · Machine Fit "Size"
Switched to Another Provider · Delivery Unsuccessful - Unresponsive Customer
Account Abandoned · Swap · Ownership
```

Plus two labels outside that set, and one drop rule:

| label | meaning |
|---|---|
| `Payment Failure` | involuntary payment-retry auto-cancels (`Failed Payment Flow max retries`, `Failed Payments`, `Max Number of Charge attempts reached`) |
| `Other / Not Specified` | generic/blank/unknown (`Other Reason`, `Others`, blank, `no reason`) |
| **dropped entirely** | ops / non-customer records — see §5.3 |

Verified legacy → canonical mappings: `Water Taste`/`Water Content & Quality` → Water Quality ·
`Relocating Outside UAE` → Relocation · `Machine Functionality`/`Machine Issues` → Product
Experience & Usability · `Defective Machine` → Machine Malfunction · `This is too expensive` →
Financial Constraint · `Machine Size` → Machine Fit "Size" · `Moved To Other Provider` →
Switched to Another Provider · `Swapped to Nano +` → Swap · `Purchased The Machine` → Ownership ·
`Customer Unresponsive` → Delivery Unsuccessful · `Customer Defaulted` → Account Abandoned.

### 5.2 Churn: use `is_true_cancel`, never raw `cancelled_at`

Two **different** fields; conflating them is the most common error:

- **User base** uses `cancelled_at_dt` — *any* cancellation removes the user.
- **Churn** uses `is_true_cancel` — a *non-true* cancel is still deducted from the user base but
  does **not** count as churn.

`is_true_cancel = cancelled_at is set AND reason is not a non-churn bucket`:

| non-churn bucket | scope |
|---|---|
| `Swap`, `Ownership`, `Payment Failure` | **all markets** |
| `Delivery Unsuccessful - Unresponsive Customer` | **UAE + USA only** (KSA still counts it as churn) |

`Account Abandoned` and `Other / Not Specified` **do** count as churn.
All of these still count as **gross sales** on their `created_at`.

### 5.3 Rows dropped entirely

Never counted in sales, user base *or* churn:

- any row whose `status` normalises to **`DELETED`**
- ops / non-customer cancellations: `Order not proceeding (ops cleanup)`,
  `This was created by accident`, `test/invalid subscription`, `Moved to Marketing Collab`

> These were bad records that should have been deleted at source, and ops is deleting them in
> Recharge. **Expect ops spreadsheets to show higher totals than the dashboard while that
> cleanup is in progress** — the dashboard figure is the correct one. (Worked example: in
> July-2026 an ops sheet showed 120 May-2026 USA subs vs 63 on the dashboard; the entire 57-row
> gap was `Order not proceeding (ops cleanup)` records. As ops deletes them both sides converge —
> USA May-2026 read 58 units on 2026-08-17.)

### 5.4 Test / internal rows

UAE historicals contain ~39 internal-email (`@wisewell.com`) rows that are deliberately left
untouched. If an agent needs a clean customer list, filter `@wisewell.com` itself.

---

## 6. Traps that have already caused real bugs

1. **Date formats are mixed.** Recharge tabs use `DD/MM/YYYY [HH:MM]`; Shopify/ops exports use
   ISO `YYYY-MM-DD HH:MM:SS+00`; the seeds use `DD/MM/YYYY` (UAE) and `YYYY-MM-DD` (USA). Parse
   **ISO first**, then day-first — pandas with `dayfirst=True` misreads `2026-06-04` as
   *April 6*. Use `utils._parse_dates`.
2. **Times are real.** `created_at` carries a time-of-day. When bucketing by day, **normalise
   both the value and the window bounds to midnight**, or single-day queries silently return 0.
3. **USA needs the start-date override.** Without `Recharge - USA Seed`, migrated USA subs land
   on their Aug-2026 migration date instead of their true Apr–Jul start.
4. **Never append a separate Stripe source for the USA** — those subs are already in
   `Recharge - USA`. See the callout in §3.1.
5. **Don't use `Shopify - USA`** — double-counts USA revenue.
6. **CAC denominators exclude `is_offline` (B2B/direct) and `is_partner` (Justlife)** — neither is
   a paid-ads acquisition. They still count as gross sales.
7. **Historical matrices are positional.** `Monthly *` tabs are read by hardcoded **row index**,
   not by label. Inserting or reordering a row silently corrupts every historical number.

---

## 7. Quick recipes

**All machine sales (subs + ownership), daily, live era:**
```python
sales = get_all_machine_sales(start_dt=..., end_dt=...)
# → date, market, product, is_ownership, is_offline, is_partner, qty
```

**Churn in a window:**
```python
rc = load_recharge_full()
churn = rc[(rc.category == "Machine") & rc.is_true_cancel
           & rc.cancelled_at_dt.dt.normalize().between(start.normalize(), end.normalize())]
units = int(churn.quantity.sum())
```

**UAE history with corrected start dates (read the seed directly):**
```python
seed = read_tab("Frozen Seed (UAE)")
seed = seed[seed.possible_duplicate != "review"]
seed["start"] = parse_ddmmyyyy(seed.start_date_corrected)
new_subs = seed[seed.relationship_class.isin(["genuine_new", "recovered_pre_migration"])]
```

---

## 8. Related references

- `docs/data-architecture.md` — deep architecture doc. **Written 2026-05-08: predates the USA
  rework, the Sparkle launch, the cancellation-reason consolidation, both seed tabs and the
  removal of `Stripe - USA`.** Treat *this* guide as authoritative where they conflict.
- `docs/sheet-reference.md` — per-tab column reference (same staleness caveat).
- `🟢 README` tab in the workbook — tab inventory; also stale (predates the seeds, and still
  implies sources that no longer exist).
