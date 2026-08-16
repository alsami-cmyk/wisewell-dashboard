# Wisewell "User Base Data" — Agent Reading Guide

**Purpose:** everything an autonomous agent needs to read Wisewell sales, user-base and
cancellation data correctly from the *User Base Data* workbook — without re-deriving the
business rules or repeating known mistakes.

- **Workbook (the only source of truth):** `1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4`
- **Markets:** `UAE` (AED) · `KSA` (SAR) · `USA` (USD)
- **Last verified:** 2026-08-14

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

rc = load_recharge_full()   # one row per subscription; see schema in §4
```

`load_recharge_full()` returns a DataFrame with these guaranteed columns:

| column | meaning |
|---|---|
| `subscription_id` | unique id (prefixed `stripe_` / `justlife_` for those sources) |
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

- Read the **whole tab** (`'Tab Name'` with no A1 range) — column counts drift.
- Row 1 is the header; pad short rows (Sheets truncates trailing empties).
- Scope `https://www.googleapis.com/auth/spreadsheets.readonly` is enough.
- **Auth caveat:** the service-account identity can read *this* workbook, but **not** the
  external ops workbook `1lnsUjLYOpk2LrnCPSjQbtlctOC35t4mrWmOZnDchW5I` (403). Local
  `token.json` (Sami's own account) can read both.
- Cache for ~5 min; the dashboard uses `@st.cache_data(ttl=300)`. Do not hammer the API.

---

## 2. The historical / live boundary — the single most important concept

```
LIVE_DATA_START = 2025-09-01
```

| period | authority |
|---|---|
| **before 2025-09-01** | the hardcoded monthly matrix tabs (`Monthly Sales`, `Monthly Cancellations`, `Monthly User Base`) |
| **2025-09-01 onward** | live row-level tabs (`Recharge - *`, `Shopify - *`, offline tabs) |

Reason: `Recharge - *` strips `DELETED` rows, so it *undercounts* history; and ownership
flow data only starts Sep-2025. Never reconstruct pre-Sep-2025 totals from Recharge alone —
you will get numbers that are too low. `get_monthly_sales_blended()` stitches both eras.

---

## 3. Where each required dataset lives

### 3.1 Sales

| dataset | tab(s) | notes |
|---|---|---|
| **Historical sales (pre-Sep-2025)** | `Monthly Sales` | month-per-column matrix; **row position = (market, product, is_ownership)** via hardcoded maps `_HIST_SALES_ROWS`. Header row = `Jan-23 … Dec-26`. |
| **Historical UAE subscriptions w/ corrected start dates** | **`Frozen Seed (UAE)`** | 8,726 rows, subscription-level, `start_date_corrected` on **100%** of rows, spanning 2021-12 → 2026-06. See §3.4 — **currently NOT read by the dashboard.** |
| **Live subscription sales** | `Recharge - UAE`, `Recharge - KSA`, `Recharge - USA` | one row per subscription; sale date = `created_at`. Zapier-fed, near-real-time. |
| **Live USA Stripe-era backfill** | `Stripe - USA` | flat order export, no price/no churn cols; price imputed (§4.3). |
| **UAE marketplace** | `Justlife - UAE` | counted as UAE sales+churn; `is_partner` (excluded from CAC). |
| **Live ownership sales** | `Shopify - UAE`, `Shopify - KSA` | unit columns per product. **`Shopify - USA` is intentionally unused** (USA revenue flows through Recharge; reading it double-counts). |
| **Offline / B2B** | `Offline - Subscriptions`, `Offline - Ownership` | `is_offline` → excluded from CAC. |
| **Returns** | `Returns` | returned ownership machines, subtracted from active. |

### 3.2 Cancellations

| dataset | tab | notes |
|---|---|---|
| **Historical (pre-Sep-2025)** | `Monthly Cancellations` | month-matrix, `(market, product)` row map `_HIST_CANCEL_ROWS`. **True cancels only.** |
| **Live** | the same `Recharge - *` tabs | a cancellation is a *column on the subscription row*: `cancelled_at` + `cancellation_reason`. There is **no separate live cancellations tab**. |
| **Historical UAE detail** | `Frozen Seed (UAE)` | 2,121 cancelled rows with `cancelled_at`, `cancel_month`, `cancellation_reason`. |
| (blended) | — | `get_monthly_cancellations_blended()` stitches both eras. |

> `Daily Sales` / `Daily Cancellations` tabs exist but are **presentation matrices** (day-per-column,
> starting 1-Sep) — not read by the dashboard. Prefer row-level sources.

### 3.3 User base

`Monthly User Base` is the manually maintained ground truth for **pre-Sep-2025** active counts
(`_HIST_UB_SUB_ROWS` for subscriptions, `_HIST_UB_OWN_ROWS` for ownership; Nano Tank absent —
it launched later). After Sep-2025, active counts are computed live from Recharge + Shopify.

### 3.4 The two seed tabs (corrected start dates)

Both exist because subscriptions were **migrated into Recharge**, so Recharge's `created_at`
is the *migration* date, not the customer's true start.

**`Frozen Seed (UAE)`** — 8,726 rows, the richest historical asset in the workbook.

| column | use |
|---|---|
| `start_date_corrected` | **the true start date** (100% populated). Format `DD/MM/YYYY`. |
| `start_month` | pre-computed `YYYY-MM` |
| `recharge_created_at_raw` | the uncorrected Recharge value, for audit |
| `start_date_source` | provenance: `recharge_genuine` (7,230) · `shopify_recovered` (867) · `swap_recovered_genuine` (311) · `cancellation_file` (239) · `migration_stamp_unrecovered` (44) · `shopify_recovered_swap` (35) |
| `relationship_class` | `genuine_new` (7,034) · `recovered_pre_migration` (867) · `swap_continuation` (346) · `pre_migration_churn` (239) · `prior_owner_new_sub` (196) · `unrecovered_stamp` (44) |
| `record_source` | `recharge` (8,487) · `pre_migration_cancellation` (239) |
| `possible_duplicate` | `review` on 6 rows — exclude or inspect |
| `cancelled_at`, `cancel_month`, `cancellation_reason` | churn detail (2,121 cancelled) |

> ⚠️ **Important for agents:** `Frozen Seed (UAE)` is **not** in the dashboard's `RAW_TABS` and is
> read by **no** code today. The dashboard's pre-Sep-2025 UAE figures come from the
> `Monthly *` matrices instead. For subscription-level UAE history with true start dates,
> the seed is the better source — but expect it to disagree with the dashboard, and treat
> `relationship_class` carefully: `swap_continuation` and `prior_owner_new_sub` are *not*
> straightforward new customers.
>
> Coverage: seed 8,726 ids vs `Recharge - UAE` 10,529 ids — **8,486 overlap, 240 seed-only**
> (the pre-migration cancellation records). The seed is **not** a superset of Recharge-UAE.

**`Recharge - USA Seed`** — 491 rows, `subscription_id → adjusted_created_at`.
This one **is** wired in: `load_recharge_full()` overrides `created_at_dt` for matching USA rows.
Columns: `subscription_id`, `adjusted_created_at`, `recharge_created_snapshot`, `stripe_link`,
`in_recharge_usa` (`Y` for 429, `N` for 62 that no longer exist in Recharge).
The live `Recharge - USA` tab is never modified — it self-corrects over time, at which point the
override becomes a harmless no-op.

---

## 4. Classifications — products

### 4.1 Canonical products

```
PRODUCT_ORDER = ["Model 1", "Nano+", "Bubble", "Flat", "Nano Tank", "Sparkle"]
```

Two categories: **`Machine`** (the hardware subscription — what "sales" means) and
**`Filter`** (consumable add-on, tracked separately, excluded from machine metrics).

### 4.2 Mapping raw `product_title` → canonical product

Match on the **lowercased** title, in this order (mirrors `_classify_recharge_product`):

1. **Filter first** — title contains `filter subscription`, `care+ plan`, or `care+`
   → category `Filter`, product taken from the qualifier
   (`(model 1)` → Model 1, `nano+` → Nano+, `bubble` → Bubble, `flat` → Flat;
   bare `filter subscription` → Model 1).
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

> **Trap:** `Wisewell Nano Subscription` (UAE) and `Wisewell Nano` (USA) both mean **Nano Tank**,
> *not* Nano+. `Nano+` always carries an explicit `+`.

### 4.3 Pricing / ARR

- `arr_local = recurring_price × quantity × (12 / charge_interval_frequency)`, **ACTIVE machine subs only**.
  A `charge_interval_frequency` of `30` is normalised to `1` (monthly).
- Convert with `get_fx()` (live rates, 1 h cache; fallback pegs AED 3.6725, SAR 3.75).
- **Trust the sheet's own `recurring_price`** for all Recharge rows (USA prices genuinely vary).
- Imputed only where the feed carries no price:
  - `Stripe - USA` → Model 1 **$69.99**, Nano **$39.99** (`US_STRIPE_PRICE`)
  - `Justlife - UAE` → AED Model 1 150, Nano+ 99, Bubble 199, Flat 139, Nano Tank 99

---

## 5. Classifications — cancellations

### 5.1 Canonical reasons (2026-07 consolidation)

Every legacy/free-text reason maps onto this set. Recharge now emits these directly; the
mapping exists so **historical** rows read consistently.

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

Legacy → canonical highlights (normalise: lowercase, trim, collapse whitespace, unify curly
quotes/dashes): `Water Taste`/`Water Content & Quality` → Water Quality · `Relocating Outside UAE`
→ Relocation · `Machine Functionality`/`Machine Issues` → Product Experience & Usability ·
`Machine Fault`/`Defective Machine`/`Application Issue` → Machine Malfunction ·
`Financial`/`This is too expensive` → Financial Constraint · `Machine Size`/`Moving house/office…`
→ Machine Fit "Size" · `Moved To Other Provider`/`Switched to Competitor` → Switched to Another
Provider · `Swapped to Nano +`/`Product Swap` → Swap · `Purchased The Machine`/`Converted to
Ownership` → Ownership · `Customer Unresponsive` → Delivery Unsuccessful · `Customer Defaulted`
→ Account Abandoned.

### 5.2 Churn: use `is_true_cancel`, never raw `cancelled_at`

Two **different** fields, and conflating them is the most common error:

- **User base** uses `cancelled_at_dt` — *any* cancellation removes the user.
- **Churn** uses `is_true_cancel` — a *non-true* cancel is still deducted from the user base
  but does **not** count as churn.

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

> These were bad records that should have been deleted at source. Ops is deleting them in
> Recharge. **Expect seed/ops spreadsheets to disagree with the dashboard because of this** —
> e.g. May-2026 USA reads 63 on the dashboard vs 120 in the ops sheet; the 57-row gap is
> exactly these `Order not proceeding (ops cleanup)` records. The dashboard figure is correct.

### 5.4 Test / internal rows

`Stripe - USA` drops every row whose `customer_email` ends in `@wisewell.com`.
UAE historicals contain ~39 internal-email rows that are deliberately left untouched.

---

## 6. Traps that have already caused real bugs

1. **Date formats are mixed.** Recharge tabs use `DD/MM/YYYY [HH:MM]`; Stripe/Shopify exports use
   ISO `YYYY-MM-DD HH:MM:SS+00`; the seeds use both. Parse **ISO first**, then day-first —
   `dateutil`/pandas with `dayfirst=True` misreads `2026-06-04` as 4 June→*April 6*. Use
   `utils._parse_dates`.
2. **Times are real.** `created_at` carries a time-of-day. When bucketing by day, **normalise
   both the value and the window bounds to midnight**, or single-day queries silently return 0.
3. **USA needs the start-date override.** Without `Recharge - USA Seed`, ~429 USA subs land on
   their Aug-2026 migration date instead of their true Apr–Jul start.
4. **USA is deduped.** A customer appearing in both `Stripe - USA` and `Recharge - USA` is
   counted once — **live Recharge wins**, the Stripe row is dropped (32 emails / 34 rows).
5. **Don't read `Shopify - USA`** — double-counts USA revenue.
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
  rework, the Sparkle launch, the cancellation-reason consolidation and both seed tabs.**
  Treat this guide as authoritative where they conflict.
- `docs/sheet-reference.md` — per-tab column reference (same staleness caveat).
- `🟢 README` tab in the workbook — tab inventory (also predates the seeds / Stripe-USA / Justlife).
