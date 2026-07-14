"""
Wisewell Dashboard — Data Layer
================================
All metric computation happens here.  Pages are pure presentation.

Architecture overview (see ARCHITECTURE.md for full spec):
  Raw tabs  →  raw loaders   →  compute helpers  →  pages
  Historical tabs (pre-Sep-2025) are blended in compute helpers only.

Data boundary
  LIVE_DATA_START = 2025-09-01
  Everything before that comes from hardcoded Monthly Sales / Monthly Cancellations /
  Monthly User Base tabs (treated as final truth).
  Everything on or after that date is computed from raw Recharge / Shopify / Offline data.
"""

from __future__ import annotations

import calendar
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

import pandas as pd
import requests
import streamlit as st
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ── Logging ───────────────────────────────────────────────────────────────────
logger = logging.getLogger("wisewell")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)

# ── Sheet identity ─────────────────────────────────────────────────────────────
SHEET_ID = "1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4"
SCOPES   = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# ── Data boundary ──────────────────────────────────────────────────────────────
LIVE_DATA_START    = pd.Timestamp("2025-09-01")   # Sep-2025 = start of live era
OWNERSHIP_SEED_DT  = pd.Timestamp("2025-08-01")   # Aug-2025 ending = seed month

# ── Source tabs ────────────────────────────────────────────────────────────────
# Raw (live) tabs
RAW_TABS = [
    "Recharge - UAE", "Recharge - KSA", "Recharge - USA",
    "Shopify - UAE",  "Shopify - KSA",  "Shopify - USA",
    "Offline - Subscriptions", "Offline - Ownership", "Returns",
    "Paid Ads Spend - Monthly",
    "Meta Ads Daily - Claude",
    "Meta Ads Campaign Daily - Claude",
    "Paid Ads Spend - Daily",
    "Shopify Website - UAE", "Shopify Website - KSA", "Shopify Website - USA",
    "Sessions by Source - Daily",
    "Top Landing Pages - Daily",
    "Projections",
    # USA replacement sources (see "USA sales: multi-source override" below)
    "US Verified - May 2026",
    "Stripe - USA",
    # Justlife marketplace subscriptions — counted as UAE (see load_recharge_full)
    "Justlife - UAE",
]
# Historical (hardcoded) tabs — pre-Sep-2025 final truth
HIST_TABS = ["Monthly Sales", "Monthly Cancellations", "Monthly User Base"]

ALL_SOURCE_TABS = RAW_TABS + HIST_TABS

MAX_RETRIES   = 3
RETRY_BACKOFF = [1, 2, 4]

# ── Product catalogue ──────────────────────────────────────────────────────────
PRODUCT_ORDER = ["Model 1", "Nano+", "Bubble", "Flat", "Nano Tank"]

PRODUCT_COLOR: dict[str, str] = {
    "Model 1":   "#8b5cf6",
    "Nano+":     "#0ea5e9",
    "Bubble":    "#f43f5e",
    "Flat":      "#10b981",
    "Nano Tank": "#f59e0b",
    "Filter":    "#94a3b8",
    "Unknown":   "#cbd5e1",
}

CATEGORY_COLOR = {"Machine": "#0ea5e9", "Filter": "#10b981"}
MARKET_COLOR   = {"UAE": "#6366f1", "KSA": "#f59e0b", "USA": "#10b981"}
FX_FALLBACK    = {"AED": 1 / 3.6725, "SAR": 1 / 3.75, "USD": 1.0}

# ── USA sales: multi-source override ──────────────────────────────────────────
# As of 2026-05-08 we determined that ~94% of raw USA Recharge orders are
# fraudulent (scammers exploiting the first-month-free promotion). The raw
# Recharge - USA tab is discarded on load and replaced with three vetted
# sources, stitched together by date:
#
#   • April 2026 (Apr 23–30):  external CLEAN LIST sheet (manual review)
#   • May 2026:                'US Verified - May 2026' tab (1:1 with Shopify
#                              orders cross-referenced for product/SKU)
#   • June 2+ 2026:            'Stripe - USA' tab (new Stripe-billed subs;
#                              Recharge-style schema, auto-synced)
#
# All three sources are normalised into a Recharge-shaped DataFrame, then
# concatenated. Each loader is independently resilient — a single source
# failing doesn't take down the dashboard.
US_VERIFIED_APR_SHEET_ID = "17UMgAdech2G0ff2Lzu8-xi3s1EjEkXrtxMjh4rLgARU"
US_VERIFIED_APR_TAB      = "CLEAN LIST"      # external sheet (Apr only)
US_VERIFIED_MAY_TAB      = "US Verified - May 2026"  # in main sheet
US_STRIPE_TAB            = "Stripe - USA"            # in main sheet

# USA subscription pricing (USD/month) — used by the April CLEAN-LIST loader
# which doesn't store prices. May + Jun onwards carry recurring_price in
# their respective sheets, so this map only matters for April.
US_PRICE_MAP = {
    "Wisewell Model 1": 69.0,
    "Wisewell Nano":    49.0,
}

# Justlife (UAE marketplace) feed carries no price. Fill from the standard
# UAE monthly list price per product (AED), derived from Recharge - UAE
# medians. Keyed by the canonical product name from _classify_recharge_product.
JUSTLIFE_UAE_PRICE = {
    "Model 1":   150.0,
    "Nano+":      99.0,
    "Bubble":    199.0,
    "Flat":      139.0,
    "Nano Tank":  99.0,
}

# Shopify ownership unit columns: (product_name, column_header)
SHOPIFY_OWN_COLS = [
    ("Model 1",   "Units - Model 1 (Own)"),
    ("Nano+",     "Units - Nano+ (Own)"),
    ("Bubble",    "Units - Bubble (Own)"),
    ("Flat",      "Units - Flat (Own)"),
    ("Nano Tank", "Units - Nano (Own)"),
]

# Cancellation reason normalisation
CANCELLATION_REASON_MAP: dict[str, str] = {
    "relocation":             "Relocation",
    "water quality":          "Water Quality",
    "personal/lifestyle":     "Personal / Lifestyle",
    "personal / lifestyle":   "Personal / Lifestyle",
    "delivery delay":         "Delivery Delay",
    "machine issues":         "Machine Issues",
    "water capacity":         "Water Capacity",
    "operational/error":      "Operational / Error",
    "operational / error":    "Operational / Error",
    "switched to competitor": "Switched to Competitor",
    "financial":              "Financial",
    "installation issues":    "Installation Issues",
    "machine fit":            "Machine Fit",
    # Non-true cancellations (excluded from churn — see is_true_cancel
    # logic in load_recharge_full):
    "customer defaulted":     "Customer Defaulted",
    "customer unreachable":   "Customer Unreachable",
}

# Historical row maps (0-indexed Python list positions in the raw values list)
# Monthly Sales tab — (market, product, is_ownership) → row index
_HIST_SALES_ROWS: dict[tuple, int] = {
    ("UAE", "Model 1",   False): 6,
    ("UAE", "Nano+",     False): 7,
    ("UAE", "Bubble",    False): 8,
    ("UAE", "Flat",      False): 9,
    ("UAE", "Nano Tank", False): 10,
    ("UAE", "Model 1",   True):  13,
    ("UAE", "Nano+",     True):  14,
    ("UAE", "Bubble",    True):  15,
    ("UAE", "Flat",      True):  16,
    ("UAE", "Nano Tank", True):  17,
    ("KSA", "Model 1",   False): 24,
    ("KSA", "Nano+",     False): 25,
    ("KSA", "Bubble",    False): 26,
    ("KSA", "Flat",      False): 27,
    ("KSA", "Nano Tank", False): 28,
    ("KSA", "Model 1",   True):  31,
    ("KSA", "Nano+",     True):  32,
    ("KSA", "Bubble",    True):  33,
    ("KSA", "Flat",      True):  34,
    ("KSA", "Nano Tank", True):  35,
}

# Monthly Cancellations tab — (market, product) → row index (true cancels only)
_HIST_CANCEL_ROWS: dict[tuple, int] = {
    ("UAE", "Model 1"):   7,
    ("UAE", "Nano+"):     8,
    ("UAE", "Bubble"):    9,
    ("UAE", "Flat"):      10,
    ("UAE", "Nano Tank"): 11,
    ("KSA", "Model 1"):   25,
    ("KSA", "Nano+"):     26,
    ("KSA", "Bubble"):    27,
    ("KSA", "Flat"):      28,
    ("KSA", "Nano Tank"): 29,
}

# Monthly User Base tab — (market, product) subscriber rows → row index
_HIST_UB_SUB_ROWS: dict[tuple, int] = {
    ("UAE", "Model 1"): 7,
    ("UAE", "Nano+"):   8,
    ("UAE", "Bubble"):  9,
    ("UAE", "Flat"):    10,
    ("KSA", "Model 1"): 23,
    ("KSA", "Nano+"):   24,
    ("KSA", "Bubble"):  25,
    ("KSA", "Flat"):    26,
}

# Monthly User Base tab — (market, product) ownership rows → row index
# (Nano Tank absent from this tab — was introduced after it was built)
_HIST_UB_OWN_ROWS: dict[tuple, int] = {
    ("UAE", "Model 1"): 13,
    ("UAE", "Nano+"):   14,
    ("UAE", "Bubble"):  15,
    ("UAE", "Flat"):    16,
    ("KSA", "Model 1"): 29,
    ("KSA", "Nano+"):   30,
    ("KSA", "Bubble"):  31,
    ("KSA", "Flat"):    32,
}

# ── Shared CSS ────────────────────────────────────────────────────────────────
SHARED_CSS = """
<style>
div[data-testid="metric-container"] {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 1rem 1.25rem;
}
[data-testid="stMetricValue"]  { font-size: 1.55rem; font-weight: 700; }
[data-testid="stMetricLabel"]  { font-size: 0.78rem; color: #64748b;
                                  text-transform: uppercase; letter-spacing: .05em; }
section[data-testid="stSidebar"] > div:first-child { background-color: #0f172a !important; }
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] small,
section[data-testid="stSidebar"] .stMarkdown { color: #e2e8f0 !important; }
section[data-testid="stSidebar"] hr { border-color: #1e3a5f !important; }
#MainMenu, footer { visibility: hidden; }
</style>
"""

# ── Credential helpers ────────────────────────────────────────────────────────
def get_credentials():
    """Service account (Streamlit Cloud) or OAuth token.json (local dev)."""
    try:
        info = json.loads(st.secrets["GOOGLE_SERVICE_ACCOUNT"])
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    except (KeyError, FileNotFoundError):
        token_path = os.path.join(os.path.dirname(__file__), "token.json")
        # token.json may have broader scopes; pass them through
        try:
            import json as _j
            with open(token_path) as f:
                tok = _j.load(f)
            scopes = tok.get("scopes", SCOPES)
        except Exception:
            scopes = SCOPES
        return Credentials.from_authorized_user_file(token_path, scopes)


@st.cache_data(ttl=3600, show_spinner=False)
def get_fx() -> dict:
    """Live USD conversion rates (1-hour cache)."""
    try:
        r     = requests.get("https://open.er-api.com/v6/latest/USD", timeout=5)
        rates = r.json().get("rates", {})
        return {"AED": 1 / rates["AED"], "SAR": 1 / rates["SAR"], "USD": 1.0, "source": "live"}
    except Exception:
        return {**FX_FALLBACK, "source": "fallback (fixed peg)"}


def fmt_usd(v: float) -> str:
    if v >= 1_000_000: return f"${v/1_000_000:.2f}M"
    if v >= 1_000:     return f"${v/1_000:.1f}K"
    return f"${v:,.0f}"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_dates(series: pd.Series) -> pd.Series:
    """
    Parse date strings supporting:
      • d/m/yyyy            (legacy Recharge tabs)
      • d/m/yyyy H:M[:S]    (May verified tab, older Stripe rows)
      • yyyy-mm-dd[T| ]H:M[:S]  (new Stripe rows, Shopify GraphQL)
      • yyyy-mm-dd
    Returns UTC-naive datetime series.

    Order matters: try ISO formats FIRST when the string starts with a
    4-digit year, then DD/MM formats. Falling back to pandas auto-detect
    with dayfirst=True is brittle for ISO strings — `2026-06-04` gets
    interpreted as Apr 6 because dayfirst flips the 2nd and 3rd fields
    even when the 1st is unambiguously the year.
    """
    s      = series.astype(str).str.strip()
    result = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")

    # ── ISO-style (year-first) ─────────────────────────────────────────
    iso_mask = s.str.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}")  # starts with YYYY-M-D
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d"):
        mask = result.isna() & iso_mask & s.ne("") & s.ne("nan")
        if not mask.any():
            break
        result[mask] = pd.to_datetime(s[mask], format=fmt, errors="coerce")

    # ── DD/MM-style ───────────────────────────────────────────────────
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        mask = result.isna() & s.ne("") & s.ne("nan")
        if not mask.any():
            break
        result[mask] = pd.to_datetime(s[mask], format=fmt, errors="coerce")

    # ── Anything left → auto-detect with the right dayfirst by shape ──
    mask = result.isna() & s.ne("") & s.ne("nan")
    if mask.any():
        # Year-first strings get dayfirst=False; everything else dayfirst=True.
        iso_left = mask & iso_mask
        non_iso  = mask & ~iso_mask
        if iso_left.any():
            result[iso_left] = pd.to_datetime(s[iso_left], errors="coerce")
        if non_iso.any():
            result[non_iso]  = pd.to_datetime(s[non_iso], dayfirst=True, errors="coerce")
    return result


def _rows_to_df(rows: list[list[str]]) -> pd.DataFrame:
    """Pad raw Sheets rows list → DataFrame."""
    if len(rows) < 2:
        return pd.DataFrame()
    max_cols = max(len(r) for r in rows)
    padded   = [r + [""] * (max_cols - len(r)) for r in rows]
    return pd.DataFrame(padded[1:], columns=padded[0])


def _classify_recharge_product(title: str) -> tuple[str | None, str | None]:
    """
    Returns (category, product) for a Recharge product_title.
    Returns (None, None) for anything that is not a tracked machine subscription.

    Rules (from ARCHITECTURE.md §3):
    - DELETED rows are already stripped before this is called.
    - Titles containing 'filter' or 'care+' → Filter category (not tracked in machine metrics).
    - Titles containing 'ownership' in Recharge → data error, exclude.
      Exception: 'Wisewell Bubble Ownership + Holiday Set' (a promotional bundle).
    - Machine products identified by regex on lowercased title.
    - Nano Tank: exact match only.
    """
    if not title or not str(title).strip():
        return None, None
    t = str(title).strip()
    tl = t.lower()

    # Filter products — tracked separately from Machine. Product associates
    # each filter sub with its parent machine so product filtering works
    # correctly in ARR and related metrics.
    if "filter subscription" in tl or "care+ plan" in tl or "care+" in tl:
        if "(model 1)" in tl or "model 1" in tl:
            return "Filter", "Model 1"
        if "(nano+)" in tl or "nano+" in tl or "nano +" in tl:
            return "Filter", "Nano+"
        if "bubble" in tl:
            return "Filter", "Bubble"
        if "(flat)" in tl or re.search(r"\bflat\b", tl):
            return "Filter", "Flat"
        # Plain "Filter Subscription" with no qualifier → Model 1
        if tl == "filter subscription":
            return "Filter", "Model 1"
        return "Filter", None

    # Ownership-labeled entries in Recharge are data errors — exclude
    # except the known promotional Bubble bundle
    if "ownership" in tl and "bubble ownership + holiday" not in tl:
        return None, None

    # Machine subscriptions — regex matching
    # USA Recharge uses "Wisewell Model 1" (no "Subscription" suffix), same as
    # UAE/KSA use "Wisewell Nano Subscription" vs USA's "Wisewell Nano" for Nano Tank.
    if re.search(r"model\s*1.*subscription", tl) or tl == "wisewell model 1":
        return "Machine", "Model 1"
    if re.search(r"nano\s*\+\s*subscription", tl):
        return "Machine", "Nano+"
    if re.search(r"bubble.*subscription", tl) or "bubble ownership + holiday" in tl:
        return "Machine", "Bubble"
    if re.search(r"wisewell\s*flat\s*subscription", tl) and "filter" not in tl:
        return "Machine", "Flat"
    # Nano Tank:
    #   UAE uses "Wisewell Nano Subscription", USA uses "Wisewell Nano" (no suffix).
    #   Both are subscription products and map to Nano Tank.
    if tl in ("wisewell nano subscription", "wisewell nano"):
        return "Machine", "Nano Tank"

    return None, None


def _classify_offline_product(lineitem: str) -> str | None:
    """
    Returns product name for an Offline (Subscriptions or Ownership) Lineitem name.
    Matches the same naming conventions as Recharge / Shopify product titles.
    Returns None for unrecognised items.
    """
    if not lineitem:
        return None
    tl = str(lineitem).strip().lower()

    # Nano Tank must come before plain 'nano' checks
    if re.search(r"\bnano\b", tl) and not re.search(r"\+|plus", tl):
        return "Nano Tank"
    if re.search(r"model\s*1|\bm1\b", tl):
        return "Model 1"
    if re.search(r"nano\s*\+", tl):
        return "Nano+"
    if "bubble" in tl and "filter" not in tl:
        return "Bubble"
    if re.search(r"\bflat\b", tl) and "filter" not in tl:
        return "Flat"
    return None


# ── Parallel tab fetcher ──────────────────────────────────────────────────────

def _fetch_single_tab(creds, tab_name: str) -> tuple[str, list, float, str | None]:
    t0 = time.perf_counter()
    for attempt in range(MAX_RETRIES):
        try:
            svc  = build("sheets", "v4", credentials=creds, cache_discovery=False)
            rows = (
                svc.spreadsheets().values()
                .get(spreadsheetId=SHEET_ID, range=f"'{tab_name}'")
                .execute()
                .get("values", [])
            )
            elapsed = time.perf_counter() - t0
            logger.info("Fetched '%s': %d rows in %.2fs", tab_name, len(rows), elapsed)
            return tab_name, rows, elapsed, None
        except Exception as exc:
            wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            logger.warning("Retry %d/%d for '%s': %s", attempt + 1, MAX_RETRIES, tab_name, exc)
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait)
    elapsed = time.perf_counter() - t0
    return tab_name, [], elapsed, f"Failed after {MAX_RETRIES} retries"


@st.cache_data(ttl=300, show_spinner="Syncing with Google Sheets…")
def _fetch_all_tabs() -> tuple[dict[str, list], dict[str, str], float]:
    """Fetch all source tabs in parallel. Returns (data, errors, elapsed)."""
    creds  = get_credentials()
    t0     = time.perf_counter()
    data:  dict[str, list] = {}
    errors: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=min(len(ALL_SOURCE_TABS), 12)) as pool:
        futures = {pool.submit(_fetch_single_tab, creds, tab): tab for tab in ALL_SOURCE_TABS}
        for future in as_completed(futures):
            tab, rows, _e, err = future.result()
            data[tab] = rows
            if err:
                errors[tab] = err

    total = time.perf_counter() - t0
    logger.info("All tabs: %d/%d OK in %.2fs", len(ALL_SOURCE_TABS) - len(errors), len(ALL_SOURCE_TABS), total)
    return data, errors, total


# ── Raw data loaders ──────────────────────────────────────────────────────────

_US_VERIFIED_EMPTY_SCHEMA = [
    "subscription_id", "customer_email", "status", "product_title",
    "variant_title", "sku",
    "recurring_price", "quantity", "charge_interval_frequency",
    "created_at_dt", "cancelled_at_dt", "is_true_cancel",
    "cancellation_reason", "market", "currency", "category",
    "product", "arr_local",
]


def _empty_us_verified() -> pd.DataFrame:
    """Empty frame in the load_recharge_full schema (for safe concat fallback)."""
    return pd.DataFrame(columns=_US_VERIFIED_EMPTY_SCHEMA)


def _classify_and_arr(out: pd.DataFrame) -> pd.DataFrame:
    """Add category/product/arr_local columns to a USA-source DataFrame."""
    classified = out["product_title"].map(_classify_recharge_product)
    out["category"] = classified.map(lambda x: x[0] if x else None)
    out["product"]  = classified.map(lambda x: x[1] if x else None)
    out["arr_local"] = out.apply(
        lambda r: (
            r["recurring_price"] * r["quantity"] * (12.0 / r["charge_interval_frequency"])
        ) if r["category"] == "Machine" else 0.0,
        axis=1,
    )
    return out


def _load_apr_clean_list() -> pd.DataFrame:
    """
    April 2026 verified USA orders — external CLEAN LIST sheet.

    Uses a different schema than Recharge (Subscription ID, Customer email,
    Product Model, Order Date, Status='Validated'/'Vaildated'), so it needs
    custom parsing. Filtered to April rows only since May+ now come from
    other sources.

    MUST NOT raise — returns empty frame on any failure.
    """
    rows: list = []
    try:
        creds = get_credentials()
    except Exception as exc:
        logger.warning("_load_apr_clean_list: get_credentials failed: %s", exc)
        return _empty_us_verified()

    for attempt in range(MAX_RETRIES):
        try:
            svc  = build("sheets", "v4", credentials=creds, cache_discovery=False)
            rows = (
                svc.spreadsheets().values()
                .get(spreadsheetId=US_VERIFIED_APR_SHEET_ID,
                     range=f"'{US_VERIFIED_APR_TAB}'")
                .execute()
                .get("values", [])
            )
            break
        except Exception as exc:
            wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            logger.warning("_load_apr_clean_list: retry %d/%d (%s)",
                           attempt + 1, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait)
            else:
                logger.error("_load_apr_clean_list: gave up after %d retries",
                             MAX_RETRIES)
                return _empty_us_verified()

    try:
        df = _rows_to_df(rows)
        if df.empty:
            return _empty_us_verified()

        df.columns = [c.strip() for c in df.columns]
        cmap = {c.lower(): c for c in df.columns}

        def _col(*c):
            for k in c:
                if k.lower() in cmap: return cmap[k.lower()]
            return None

        status_col = _col("Status")
        prod_col   = _col("Product Model", "Product", "Product Title")
        date_col   = _col("Order Date", "subscription_activation_date",
                          "Created At", "Activation Date")
        sub_col    = _col("Subscription ID", "subscription_id")
        email_col  = _col("Customer email", "Email", "customer_email")

        if status_col is None or prod_col is None or date_col is None:
            logger.warning("_load_apr_clean_list: missing required column")
            return _empty_us_verified()

        status_norm = df[status_col].fillna("").astype(str).str.strip().str.upper()
        valid_mask  = status_norm.isin({"VALIDATED", "VAILDATED"})
        parsed_date = _parse_dates(df[date_col])
        valid_mask  = valid_mask & parsed_date.notna()

        sub_series = df[sub_col].fillna("").astype(str).str.strip() if sub_col \
                     else pd.Series([""] * len(df), index=df.index)

        df = df[valid_mask].copy()
        if df.empty:
            return _empty_us_verified()
        parsed_date = parsed_date[valid_mask]
        sub_series  = sub_series[valid_mask]

        # Filter to APRIL ONLY — May and later come from other sources
        apr_only = (parsed_date >= pd.Timestamp("2026-04-01")) & \
                   (parsed_date <  pd.Timestamp("2026-05-01"))
        df          = df[apr_only].copy()
        parsed_date = parsed_date[apr_only]
        sub_series  = sub_series[apr_only]
        if df.empty:
            logger.info("_load_apr_clean_list: 0 April rows after filter")
            return _empty_us_verified()

        out = pd.DataFrame(index=df.index)
        out["subscription_id"]            = sub_series
        out["customer_email"]             = df[email_col].astype(str).str.strip() if email_col else ""
        out["status"]                     = "ACTIVE"
        out["product_title"]              = df[prod_col].astype(str).str.strip()
        out["variant_title"]              = ""  # CLEAN LIST doesn't track variant
        out["sku"]                        = ""
        out["recurring_price"]            = out["product_title"].map(US_PRICE_MAP).fillna(0.0)
        out["quantity"]                   = 1
        out["charge_interval_frequency"]  = 1.0
        out["created_at_dt"]              = pd.to_datetime(parsed_date.values).normalize()
        out["cancelled_at_dt"]            = pd.NaT
        out["is_true_cancel"]             = False
        out["cancellation_reason"]        = "Not Specified"
        out["market"]                     = "USA"
        out["currency"]                   = "USD"
        out = _classify_and_arr(out).reset_index(drop=True)

        logger.info("_load_apr_clean_list: %d Apr rows loaded", len(out))
        return out
    except Exception as exc:
        logger.exception("_load_apr_clean_list: parse failed: %s", exc)
        return _empty_us_verified()


def _load_recharge_schema_usa_tab(tab_name: str,
                                   date_min: pd.Timestamp | None = None,
                                   date_max: pd.Timestamp | None = None,
                                   label: str = "") -> pd.DataFrame:
    """
    Parser for a USA tab that already uses the Recharge schema
    (subscription_id, customer_email, status, product_title, recurring_price,
    quantity, charge_interval_frequency, created_at, cancelled_at, ...).

    Used for both 'US Verified - May 2026' and 'Stripe - USA' since they
    share the schema. Optional [date_min, date_max) window so the
    Stripe - USA tab can be clipped to Jun 2+ only.

    MUST NOT raise.
    """
    try:
        raw_data, _errors, _elapsed = _fetch_all_tabs()
        rows = raw_data.get(tab_name, [])
        df = _rows_to_df(rows)
        if df.empty:
            return _empty_us_verified()

        df.columns = [c.strip() for c in df.columns]
        cmap = {c.lower(): c for c in df.columns}

        def _col(*c):
            for k in c:
                if k.lower() in cmap: return cmap[k.lower()]
            return None

        sub_col      = _col("subscription_id")
        email_col    = _col("customer_email", "Customer email", "Email")
        status_col   = _col("status")
        prod_col     = _col("product_title", "Product Title")
        variant_col  = _col("variant_title", "Variant Title")
        sku_col      = _col("sku", "SKU")
        price_col    = _col("recurring_price")
        qty_col      = _col("quantity")
        freq_col     = _col("charge_interval_frequency")
        created_col  = _col("created_at", "Created At")
        cancelled_col = _col("cancelled_at", "Cancelled At")

        if not (created_col and prod_col):
            logger.warning("_load_recharge_schema_usa_tab[%s]: missing required cols", tab_name)
            return _empty_us_verified()

        parsed_created = _parse_dates(df[created_col])
        parsed_cancel  = _parse_dates(df[cancelled_col]) if cancelled_col else pd.Series(pd.NaT, index=df.index)

        # Diagnostic: warn loudly if any non-empty created_at strings fail to
        # parse (silent drops have bitten us in the past — e.g. when Stripe
        # switched from DD/MM/YYYY to ISO timestamps in Jun 2026).
        raw_nonempty = df[created_col].astype(str).str.strip().replace({"nan": ""}) != ""
        unparseable = raw_nonempty & parsed_created.isna()
        if unparseable.any():
            samples = df.loc[unparseable, created_col].astype(str).head(3).tolist()
            logger.warning(
                "[%s] %d rows have a created_at value that _parse_dates "
                "couldn't read — these will be DROPPED. Samples: %s. "
                "Add the format to _parse_dates if it's a new convention.",
                tab_name, int(unparseable.sum()), samples,
            )

        valid_mask = parsed_created.notna()

        # Optional date window
        if date_min is not None:
            valid_mask = valid_mask & (parsed_created >= date_min)
        if date_max is not None:
            valid_mask = valid_mask & (parsed_created < date_max)

        # Diagnostic: warn if the window filter discards parseable rows
        # (helps surface "Stripe rows being parsed as April" bugs early).
        window_dropped = parsed_created.notna() & ~valid_mask
        if window_dropped.any():
            wd_samples = df.loc[window_dropped].head(3).apply(
                lambda r: f"{r.get(created_col, '')!r}→{parsed_created.loc[r.name]:%Y-%m-%d}",
                axis=1,
            ).tolist()
            logger.info(
                "[%s] %d rows parsed OK but fell outside the window "
                "[%s, %s). Samples: %s",
                tab_name, int(window_dropped.sum()),
                date_min.strftime("%Y-%m-%d") if date_min is not None else "−∞",
                date_max.strftime("%Y-%m-%d") if date_max is not None else "+∞",
                wd_samples,
            )

        df = df[valid_mask].copy()
        if df.empty:
            return _empty_us_verified()
        parsed_created = parsed_created[valid_mask]
        parsed_cancel  = parsed_cancel[valid_mask]

        # Status normalisation — Stripe uses 'trialing'/'active'/'canceled'
        raw_status = df[status_col].astype(str).str.strip().str.upper() if status_col else pd.Series("ACTIVE", index=df.index)
        # Anything not CANCELLED/CANCELED/DELETED is treated as ACTIVE for dashboard
        is_cancelled = raw_status.isin({"CANCELLED", "CANCELED", "DELETED"})

        out = pd.DataFrame(index=df.index)
        out["subscription_id"] = df[sub_col].astype(str).str.strip() if sub_col else ""
        out["customer_email"]  = df[email_col].astype(str).str.strip() if email_col else ""
        out["status"]          = raw_status.where(~is_cancelled, "CANCELLED").where(is_cancelled, "ACTIVE")
        out["product_title"]   = df[prod_col].astype(str).str.strip()
        out["variant_title"]   = df[variant_col].astype(str).str.strip() if variant_col else ""
        out["sku"]             = df[sku_col].astype(str).str.strip() if sku_col else ""
        out["recurring_price"] = pd.to_numeric(df[price_col], errors="coerce").fillna(0.0) if price_col else 0.0
        out["quantity"]        = pd.to_numeric(df[qty_col], errors="coerce").fillna(1).astype(int) if qty_col else 1
        out["quantity"]        = out["quantity"].clip(lower=1)
        out["charge_interval_frequency"] = pd.to_numeric(df[freq_col], errors="coerce").fillna(1.0).apply(
            lambda x: 1.0 if x == 30 else float(x or 1.0)
        ) if freq_col else 1.0
        # Normalise to midnight to match Recharge UAE/KSA tabs — same-day
        # filters (e.g. "Today's Sales") rely on midnight semantics.
        out["created_at_dt"]   = pd.to_datetime(parsed_created.values).normalize()
        out["cancelled_at_dt"] = pd.to_datetime(parsed_cancel.values).normalize() if parsed_cancel.notna().any() else parsed_cancel.values
        out["is_true_cancel"]  = is_cancelled.values
        out["cancellation_reason"] = "Not Specified"
        out["market"]          = "USA"
        out["currency"]        = "USD"

        # Map product titles: Stripe uses 'wisewell-nano' (lowercase, hyphenated);
        # May tab uses 'Wisewell Nano' / 'Wisewell Model 1'. Normalise so the
        # existing _classify_recharge_product regex catches both.
        norm = out["product_title"].str.lower().str.replace("-", " ", regex=False).str.strip()
        out["product_title"] = norm.map({
            "wisewell nano":    "Wisewell Nano",
            "wisewell model 1": "Wisewell Model 1",
            "filter subscription": "Filter Subscription",
        }).fillna(out["product_title"])

        out = _classify_and_arr(out).reset_index(drop=True)
        logger.info("_load_recharge_schema_usa_tab[%s]: %d rows loaded (%s)",
                    tab_name, len(out), label or "no window")
        return out
    except Exception as exc:
        logger.exception("_load_recharge_schema_usa_tab[%s]: parse failed: %s", tab_name, exc)
        return _empty_us_verified()


@st.cache_data(ttl=300, show_spinner=False)
def load_us_verified_subscriptions() -> pd.DataFrame:
    """
    Stitched USA subscription dataset from three vetted sources:

      • April 2026 (Apr 23–30):  CLEAN LIST external sheet (manual review)
      • May 2026:                'US Verified - May 2026' tab (Shopify-
                                 verified, one row per order line item)
      • Jun 2+ 2026:             'Stripe - USA' tab (live Stripe billing)

    Each source loader is independently resilient: if one fails the others
    still populate. Returns a DataFrame in load_recharge_full's schema so it
    can be concatenated directly inside the USA override.

    Columns: subscription_id, customer_email, status, product_title,
    recurring_price, quantity, charge_interval_frequency, created_at_dt,
    cancelled_at_dt, is_true_cancel, cancellation_reason, market, currency,
    category, product, arr_local
    """
    apr = _load_apr_clean_list()
    may = _load_recharge_schema_usa_tab(
        US_VERIFIED_MAY_TAB,
        date_min=pd.Timestamp("2026-05-01"),
        date_max=pd.Timestamp("2026-06-01"),
        label="May",
    )
    jun_onwards = _load_recharge_schema_usa_tab(
        US_STRIPE_TAB,
        date_min=pd.Timestamp("2026-06-02"),
        date_max=None,
        label="Jun 2+",
    )

    pieces = [p for p in (apr, may, jun_onwards) if not p.empty]
    if not pieces:
        return _empty_us_verified()
    combined = pd.concat(pieces, ignore_index=True)

    # ── Drop internal / test subscriptions entirely ───────────────────────
    # Test subs (e.g. sheraz.temp@wisewell.com trial runs, jose@wisewell.com
    # checkout tests) must not appear anywhere: not in gross sales, not in
    # the user base, not in churn. Real customers who cancelled are NOT
    # touched by this filter — they still count as a gross sale on their
    # created_at and as a churn event on their cancelled_at.
    email_lc = combined["customer_email"].fillna("").astype(str).str.strip().str.lower()
    is_test = email_lc.str.endswith("@wisewell.com")
    if is_test.any():
        logger.info(
            "load_us_verified_subscriptions: dropped %d internal/test rows (%s)",
            int(is_test.sum()),
            ", ".join(sorted(email_lc[is_test].unique())[:5]),
        )
        combined = combined[~is_test].reset_index(drop=True)

    logger.info(
        "load_us_verified_subscriptions: total %d rows  (Apr=%d, May=%d, Jun+=%d)",
        len(combined), len(apr), len(may), len(jun_onwards),
    )
    return combined


@st.cache_data(ttl=300, show_spinner=False)
def load_recharge_full() -> pd.DataFrame:
    """
    All Recharge rows from UAE + KSA + USA.

    DELETED rows are dropped on load (they never existed for dashboard purposes).

    Guaranteed columns:
      subscription_id, customer_email, status, product_title,
      recurring_price, quantity (int), charge_interval_frequency,
      created_at_dt, cancelled_at_dt,
      is_true_cancel (bool), cancellation_reason (normalised str),
      market, currency, category (Machine/Filter/None), product (str/None),
      arr_local (float, ACTIVE only)
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    frames = []

    for tab, currency, market in [
        ("Recharge - UAE", "AED", "UAE"),
        ("Recharge - KSA", "SAR", "KSA"),
        ("Recharge - USA", "USD", "USA"),
    ]:
        rows = raw_data.get(tab, [])
        df   = _rows_to_df(rows)
        if df.empty:
            continue
        df["market"]   = market
        df["currency"] = currency
        frames.append(df)

    # ── Justlife - UAE ────────────────────────────────────────────────────
    # Marketplace subscriptions sold through Justlife; counted as UAE sales
    # and churn. Feed has its own compact schema (ref_ID, status,
    # product_title, variant_title, created_at, cancelled_at,
    # cancellation_reason) and no price — we map it into the Recharge raw
    # schema so it flows through the same classification / date / churn /
    # ARR pipeline below. Price filled from JUSTLIFE_UAE_PRICE.
    jl = _rows_to_df(raw_data.get("Justlife - UAE", []))
    if not jl.empty:
        jl.columns = [c.strip() for c in jl.columns]
        _jcmap = {c.lower(): c for c in jl.columns}

        def _jl_col(name: str) -> pd.Series:
            col = _jcmap.get(name.lower())
            if col:
                return jl[col].astype(str).str.strip()
            return pd.Series([""] * len(jl), index=jl.index)

        j = pd.DataFrame(index=jl.index)
        j["subscription_id"]    = "justlife_" + _jl_col("ref_ID")
        j["customer_email"]     = ""  # feed carries customer_name only, no email
        j["status"]             = _jl_col("status").str.upper()
        j["product_title"]      = _jl_col("product_title")
        j["variant_title"]      = _jl_col("variant_title")
        j["sku"]                = ""
        _pc = j["product_title"].map(_classify_recharge_product)
        j["recurring_price"]    = _pc.map(
            lambda x: JUSTLIFE_UAE_PRICE.get(x[1], 0.0) if x else 0.0
        )
        j["quantity"]                   = 1
        j["charge_interval_frequency"]  = 1
        j["created_at"]         = _jl_col("created_at")
        j["cancelled_at"]       = _jl_col("cancelled_at")
        j["cancellation_reason"] = _jl_col("cancellation_reason")
        j["market"]             = "UAE"
        j["currency"]           = "AED"
        frames.append(j)
        logger.info("load_recharge_full: +%d Justlife-UAE rows", len(j))

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)

    # Drop DELETED subscriptions — robust to case / whitespace.
    # Any status normalising to "DELETED" (e.g. "deleted", "DELETED ",
    # " Deleted") is excluded. NaN statuses are kept (not DELETED).
    if "status" in df.columns:
        _status_norm = (
            df["status"].fillna("").astype(str).str.strip().str.upper()
        )
        _before = len(df)
        df = df[_status_norm != "DELETED"].copy()
        _dropped = _before - len(df)
        if _dropped:
            logger.info("load_recharge_full: dropped %d DELETED subscription rows", _dropped)

    # Numeric fields
    for col, default in [
        ("recurring_price",           0.0),
        ("quantity",                  1.0),
        ("charge_interval_frequency", 1.0),
    ]:
        df[col] = pd.to_numeric(df.get(col, 0), errors="coerce").fillna(default)

    df["quantity"] = df["quantity"].astype(int).clip(lower=1)
    df["charge_interval_frequency"] = df["charge_interval_frequency"].apply(
        lambda x: 1.0 if x == 30 else x
    )

    # Product classification
    classified = df["product_title"].map(_classify_recharge_product)
    df["category"] = classified.map(lambda x: x[0] if x else None)
    df["product"]  = classified.map(lambda x: x[1] if x else None)

    # ARR (ACTIVE machine subs only)
    df["arr_local"] = df.apply(
        lambda r: (
            r["recurring_price"] * r["quantity"] * (12.0 / r["charge_interval_frequency"])
        ) if r.get("status") == "ACTIVE" and r.get("category") == "Machine" else 0.0,
        axis=1,
    )

    # Date columns — find by fuzzy name match
    ca_col  = next((c for c in df.columns if c.strip().lower() == "created_at"),  None)
    can_col = next((c for c in df.columns if c.strip().lower() == "cancelled_at"), None)
    df["created_at_dt"]   = _parse_dates(df[ca_col])  if ca_col  else pd.NaT
    df["cancelled_at_dt"] = _parse_dates(df[can_col]) if can_col else pd.NaT

    # True cancellation:
    #   cancelled_at is set  AND  reason NOT in the excluded group.
    # Excluded reasons (is_true_cancel = False — never counted as churn):
    #   • ALL markets — swaps / conversions / upgrades ("swapped",
    #     "purchased", "converted", "swap", "max"): the customer stayed,
    #     just on a different product or plan.
    #   • UAE ONLY (per CS process, 2026-07) —
    #     "Customer Unreachable": ordered but never responded to delivery
    #     coordination. The subscription never really began.
    #     ("Customer Defaulted" was briefly excluded too, but as of 2026-07
    #     it counts as a TRUE cancellation — a customer who stops paying is
    #     real churn.)
    # All excluded reasons still count as gross sales on their created_at.
    has_cancelled = df["cancelled_at_dt"].notna()
    reason_col = next(
        (c for c in df.columns if "cancellation" in c.lower() and "reason" in c.lower()
         and "comment" not in c.lower()), None
    )
    raw_reason = df[reason_col].astype(str).str.strip() if reason_col else pd.Series("", index=df.index)
    reason_lc  = raw_reason.str.lower()
    is_swap = reason_lc.str.contains(
        r"swapped|purchased|converted|swap|max", regex=True, na=False
    )
    is_uae_writeoff = (df["market"] == "UAE") & reason_lc.str.contains(
        r"unreachable", regex=True, na=False
    )
    df["is_true_cancel"] = has_cancelled & ~(is_swap | is_uae_writeoff)

    # Normalise cancellation reason for display
    if reason_col:
        normalised = (
            raw_reason.str.lower()
            .map(CANCELLATION_REASON_MAP)
            .fillna(raw_reason.where(raw_reason.ne("") & raw_reason.ne("nan"), "Not Specified"))
        )
        df["cancellation_reason"] = normalised
    else:
        df["cancellation_reason"] = "Not Specified"

    keep = [
        "subscription_id", "customer_email", "status",
        "product_title", "variant_title", "sku",
        "recurring_price", "quantity",
        "charge_interval_frequency", "created_at_dt", "cancelled_at_dt",
        "is_true_cancel", "cancellation_reason",
        "market", "currency", "category", "product", "arr_local",
    ]
    # Ensure variant_title / sku exist even if a market's tab lacks them
    for c in ("variant_title", "sku"):
        if c not in df.columns:
            df[c] = ""
    df = df[[c for c in keep if c in df.columns]].copy()

    # ── USA verified-only override ────────────────────────────────────────
    # Strip raw USA Recharge rows (assumed fraudulent until verified) and
    # replace with the manually-verified CLEAN LIST. See header comment near
    # US_VERIFIED_SHEET_ID for context. Temporary measure as of 2026-05-08.
    #
    # WRAPPED in try/except so any failure here (sheet unavailable, parsing
    # error, etc.) NEVER takes down the dashboard. Worst case: USA falls
    # back to the raw Recharge data (less ideal, but page renders).
    try:
        _usa_raw_count = int((df["market"] == "USA").sum()) if "market" in df.columns else 0
        us_verified = load_us_verified_subscriptions()
        if us_verified is None:
            us_verified = _empty_us_verified()
        # Only strip USA rows if we actually got verified data — otherwise
        # keep raw USA so the dashboard isn't blank.
        if not us_verified.empty:
            df = df[df["market"] != "USA"].copy()
            us_verified = us_verified[[c for c in keep if c in us_verified.columns]].copy()
            df = pd.concat([df, us_verified], ignore_index=True)
            logger.info(
                "load_recharge_full: USA override — dropped %d raw rows, "
                "added %d verified rows", _usa_raw_count, len(us_verified),
            )
        else:
            logger.warning(
                "load_recharge_full: USA override SKIPPED — verified list empty "
                "or unavailable. Falling back to %d raw USA rows.", _usa_raw_count,
            )
    except Exception as exc:
        logger.exception(
            "load_recharge_full: USA override crashed (%s) — falling back to raw USA",
            exc,
        )

    return df


@st.cache_data(ttl=300, show_spinner=False)
def load_shopify_ownership() -> pd.DataFrame:
    """
    Ownership sales from Shopify UAE + KSA + USA.

    One record per machine unit sold (quantity = literal value in ownership col).
    Shopify subscription unit columns are ignored entirely.

    Returns: date (Timestamp), market, product, qty (int)
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    records = []

    # USA intentionally excluded: all USA machine revenue (traditional subs,
    # rent-to-own, downpayment + subscription) is processed via Recharge and
    # already counted as subscriptions. Shopify - USA would double-count it.
    for tab_name, market in [
        ("Shopify - UAE", "UAE"),
        ("Shopify - KSA", "KSA"),
    ]:
        rows = raw_data.get(tab_name, [])
        if len(rows) < 2:
            continue
        headers = [h.strip() for h in rows[0]]
        n       = len(headers)
        padded  = [r[:n] + [""] * max(0, n - len(r)) for r in rows[1:]]
        df      = pd.DataFrame(padded, columns=headers)

        # Parse date
        date_col = next((c for c in df.columns if c.strip().lower() == "created at"), None)
        if not date_col:
            continue
        df["_date"] = pd.to_datetime(df[date_col], errors="coerce").dt.normalize()

        # Iterate over ownership unit columns
        for product, own_col in SHOPIFY_OWN_COLS:
            if own_col not in df.columns:
                continue
            qty_series = pd.to_numeric(df[own_col], errors="coerce").fillna(0).astype(int)
            mask = qty_series > 0
            for date_val, qty in zip(df.loc[mask, "_date"], qty_series[mask]):
                if pd.notna(date_val):
                    records.append((date_val, market, product, int(qty)))

    if not records:
        return pd.DataFrame(columns=["date", "market", "product", "qty"])
    return pd.DataFrame(records, columns=["date", "market", "product", "qty"])


# SKU for the Handhal six-pack water bottle line (UAE). Lineitem sku
# cells in Shopify - UAE may pipe-delimit multiple SKUs from the same
# order (e.g. "six-uae-aluminum-bottles-with-caps | WISEWELL_NANO-Sub")
# with corresponding pipe-delimited Lineitem quantity values.
HANDHAL_SKU = "six-uae-aluminum-bottles-with-caps"


# Map raw variant_title / SKU-derived hints to a normalised colour.
# Products with no real colour split (Bubble, Flat) collapse to "Single".
def _normalise_colour(product: str | None, variant: str, sku: str) -> str:
    """
    Normalise variant info into one of: 'Black', 'White', 'Single', 'Unspecified'.

    - Bubble + Flat use single-colour conventions → 'Single' regardless of
      what the source says (some Recharge rows still tag them 'Black').
    - For Model 1 / Nano+ / Nano Tank, parse the variant string AND the SKU
      suffix because not every row sets both.
    """
    if product in ("Bubble", "Flat"):
        return "Single"
    v = (variant or "").strip().lower()
    s = (sku or "").strip().upper()
    if "white" in v or "_WHITE" in s or s.endswith("-002") or s.endswith("WHITE"):
        return "White"
    if "black" in v or "_BLACK" in s or s.endswith("-001") or s.endswith("BLACK"):
        return "Black"
    return "Unspecified"


@st.cache_data(ttl=300, show_spinner=False)
def get_sku_sales(start_dt: pd.Timestamp | None = None) -> pd.DataFrame:
    """
    Unified SKU-level sales for the inventory / supply-chain team.

    Combines:
      • Recharge subscriptions (UAE + KSA + USA verified + Stripe-USA),
        Machine category only
      • Shopify - UAE / KSA ownership rows (parsed from Lineitem sku +
        Lineitem name for variant info)

    Returns: date (Timestamp, midnight), market, product, variant,
             colour (normalised), sku, channel ('Subscription'/'Ownership'),
             qty (int)

    `start_dt` defaults to 2026-01-01 (inventory tracking horizon).
    """
    sd = (start_dt or pd.Timestamp("2026-01-01")).normalize()
    records: list[tuple] = []

    # ── 1. Recharge subscriptions ─────────────────────────────────────────────
    rc = load_recharge_full()
    if not rc.empty:
        m = rc[
            (rc["category"] == "Machine")
            & rc["created_at_dt"].notna()
            & (rc["created_at_dt"] >= sd)
        ]
        for _, r in m.iterrows():
            colour = _normalise_colour(r["product"], r.get("variant_title", ""), r.get("sku", ""))
            records.append((
                r["created_at_dt"].normalize(),
                r["market"], r["product"], r.get("variant_title", ""), colour,
                r.get("sku", ""), "Subscription", int(r["quantity"]),
            ))

    # ── 2. Shopify UAE/KSA ownership (parse line items for variant) ──────────
    raw, _e, _t = _fetch_all_tabs()
    own_col_map = dict(SHOPIFY_OWN_COLS)  # product → own column
    for tab, market in [("Shopify - UAE", "UAE"), ("Shopify - KSA", "KSA")]:
        rows = raw.get(tab, [])
        if len(rows) < 2:
            continue
        header = [h.strip() for h in rows[0]]
        n = len(header)
        padded = [r[:n] + [""] * max(0, n - len(r)) for r in rows[1:]]
        df = pd.DataFrame(padded, columns=header)

        date_col = next((c for c in df.columns if c.strip().lower() == "created at"), None)
        if not date_col:
            continue
        df["_d"] = _parse_dates(df[date_col]).dt.normalize()
        df = df[df["_d"] >= sd]
        if df.empty:
            continue

        for _, row in df.iterrows():
            for product, own_col in SHOPIFY_OWN_COLS:
                try:
                    qty = int(float(row.get(own_col, "") or 0))
                except (ValueError, TypeError):
                    qty = 0
                if qty <= 0:
                    continue
                # Variant heuristic: scan ANY pipe-delimited sku / name
                # cell for matching product + colour keywords.
                sku_blob = str(row.get("Lineitem sku", ""))
                name_blob = str(row.get("Lineitem name", ""))
                colour = _normalise_colour(product, name_blob, sku_blob)
                # Try to pick out the matching pipe segment for a cleaner SKU
                sku_for_row = ""
                for piece in sku_blob.split("|"):
                    p = piece.strip()
                    if product.replace(" ", "").upper().replace("+", "PLUS") in p.upper().replace("+", "PLUS"):
                        sku_for_row = p
                        break
                records.append((
                    row["_d"], market, product, "", colour,
                    sku_for_row, "Ownership", qty,
                ))

    if not records:
        return pd.DataFrame(columns=[
            "date", "market", "product", "variant", "colour",
            "sku", "channel", "qty",
        ])
    return pd.DataFrame(records, columns=[
        "date", "market", "product", "variant", "colour",
        "sku", "channel", "qty",
    ])


@st.cache_data(ttl=300, show_spinner=False)
def load_handhal_six_pack() -> pd.DataFrame:
    """
    Handhal Six-Pack (aluminium bottle) sales from Shopify - UAE.

    Matches the SKU `six-uae-aluminum-bottles-with-caps` inside the
    pipe-delimited `Lineitem sku` cells. For each matching position the
    corresponding `Lineitem quantity` slot gives the units. Unit revenue
    is also pipe-delimited inside `Subtotal`; we use it when present and
    skip silently otherwise (revenue is informational, units are the
    primary KPI).

    Returns: date (Timestamp, midnight), qty (int), revenue_aed (float)
    One row per order line that contains the SKU.
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    rows = raw_data.get("Shopify - UAE", [])
    if len(rows) < 2:
        return pd.DataFrame(columns=["date", "qty", "revenue_aed"])

    headers = [h.strip() for h in rows[0]]
    n       = len(headers)
    padded  = [r[:n] + [""] * max(0, n - len(r)) for r in rows[1:]]
    df      = pd.DataFrame(padded, columns=headers)

    col_map = {c.lower(): c for c in df.columns}
    date_col = col_map.get("created at")
    sku_col  = col_map.get("lineitem sku")
    qty_col  = col_map.get("lineitem quantity")
    subt_col = col_map.get("subtotal")
    if not all([date_col, sku_col, qty_col]):
        logger.warning(
            "load_handhal_six_pack: Shopify - UAE missing required col "
            "(date=%s, sku=%s, qty=%s)", date_col, sku_col, qty_col,
        )
        return pd.DataFrame(columns=["date", "qty", "revenue_aed"])

    parsed_date = _parse_dates(df[date_col]).dt.normalize()

    records = []
    for idx, row in df.iterrows():
        sku_raw = str(row.get(sku_col, "")).strip()
        if HANDHAL_SKU not in sku_raw.lower():
            continue
        sku_list = [s.strip().lower() for s in sku_raw.split("|")]
        qty_list = [s.strip() for s in str(row.get(qty_col, "")).split("|")]
        sub_list = [s.strip() for s in str(row.get(subt_col, "")).split(",")] if subt_col else []

        # Walk paired positions to extract this SKU's units & subtotal
        for pos, sku in enumerate(sku_list):
            if sku != HANDHAL_SKU:
                continue
            try:
                qty = int(float(qty_list[pos])) if pos < len(qty_list) else 0
            except (ValueError, IndexError):
                qty = 0
            try:
                rev = float(sub_list[pos]) if pos < len(sub_list) else 0.0
            except (ValueError, IndexError):
                rev = 0.0
            d = parsed_date.iloc[idx]
            if qty > 0 and pd.notna(d):
                records.append((d, qty, rev))

    if not records:
        return pd.DataFrame(columns=["date", "qty", "revenue_aed"])
    return pd.DataFrame(records, columns=["date", "qty", "revenue_aed"])


def _load_offline_generic(tab_name: str) -> pd.DataFrame:
    """
    Shared loader for Offline - Subscriptions and Offline - Ownership.
    Returns: date (Timestamp), market, product, qty (int)
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    rows = raw_data.get(tab_name, [])
    df   = _rows_to_df(rows)
    if df.empty:
        return pd.DataFrame(columns=["date", "market", "product", "qty"])

    # Normalise column names (lowercase strip)
    df.columns = [c.strip() for c in df.columns]
    col_map = {c.lower(): c for c in df.columns}

    country_col  = col_map.get("country")
    date_col     = col_map.get("created at")
    lineitem_col = col_map.get("lineitem name")
    qty_col      = col_map.get("lineitem quantity")

    if not all([country_col, date_col, lineitem_col, qty_col]):
        logger.warning("'%s' missing expected columns: %s", tab_name, list(df.columns))
        return pd.DataFrame(columns=["date", "market", "product", "qty"])

    df["_date"]    = _parse_dates(df[date_col]).dt.normalize()
    df["_market"]  = df[country_col].astype(str).str.strip().str.upper()
    df["_product"] = df[lineitem_col].map(_classify_offline_product)
    df["_qty"]     = pd.to_numeric(df[qty_col], errors="coerce").fillna(1).astype(int).clip(lower=1)

    out = df[df["_product"].notna() & df["_date"].notna()][
        ["_date", "_market", "_product", "_qty"]
    ].copy()
    out.columns = ["date", "market", "product", "qty"]
    return out.reset_index(drop=True)


@st.cache_data(ttl=300, show_spinner=False)
def load_offline_subscriptions() -> pd.DataFrame:
    """Bank-transfer subscription orders. Returns: date, market, product, qty."""
    return _load_offline_generic("Offline - Subscriptions")


@st.cache_data(ttl=300, show_spinner=False)
def load_offline_ownership() -> pd.DataFrame:
    """Bank-transfer ownership purchases. Returns: date, market, product, qty."""
    return _load_offline_generic("Offline - Ownership")


@st.cache_data(ttl=300, show_spinner=False)
def load_offline_returns() -> pd.DataFrame:
    """
    CS-maintained Returns tab.
    Returns: date (Timestamp), market, product, qty (int)
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    rows = raw_data.get("Returns", [])
    empty = pd.DataFrame(columns=["date", "market", "product", "qty"])
    if len(rows) < 2:
        return empty

    df = _rows_to_df(rows)
    if df.empty:
        return empty

    # Expected columns: Return Date, Country, Product, Quantity, ...
    df.columns = [c.strip() for c in df.columns]
    col_map    = {c.lower(): c for c in df.columns}

    date_col    = col_map.get("return date")
    country_col = col_map.get("country")
    product_col = col_map.get("product")
    qty_col     = col_map.get("quantity")

    if not all([date_col, country_col, product_col, qty_col]):
        logger.warning("Returns tab missing expected columns: %s", list(df.columns))
        return empty

    df["_date"]    = _parse_dates(df[date_col]).dt.normalize()
    df["_market"]  = df[country_col].astype(str).str.strip().str.upper()
    df["_product"] = df[product_col].astype(str).str.strip()
    df["_qty"]     = pd.to_numeric(df[qty_col], errors="coerce").fillna(0).astype(int)

    valid_products = set(PRODUCT_ORDER)
    out = df[
        df["_product"].isin(valid_products) &
        df["_date"].notna() &
        (df["_qty"] > 0)
    ][["_date", "_market", "_product", "_qty"]].copy()
    out.columns = ["date", "market", "product", "qty"]
    return out.reset_index(drop=True)


@st.cache_data(ttl=300, show_spinner=False)
def load_marketing_spend() -> pd.DataFrame:
    """
    'Paid Ads Spend - Monthly' tab (formerly 'Marketing Spend') → monthly spend in USD.
    Returns: month_dt, total_usd, uae_usd, ksa_usd, usa_usd
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    # Tolerate the legacy "Marketing Spend" name in case the rename gets reversed
    rows = raw_data.get("Paid Ads Spend - Monthly") or raw_data.get("Marketing Spend") or []
    df   = _rows_to_df(rows)
    empty = pd.DataFrame(columns=["month_dt", "total_usd", "uae_usd", "ksa_usd", "usa_usd"])
    if df.empty:
        return empty

    month_col      = df.columns[0]
    df["month_dt"] = pd.to_datetime(
        df[month_col].astype(str).str.strip().str.replace("'", "-"),
        format="%b-%y", errors="coerce"
    )
    df = df[df["month_dt"].notna()].copy()
    if df.empty:
        return empty

    def _spend(label: str) -> pd.Series:
        col = next((c for c in df.columns if c.strip().lower() == label.lower()), None)
        if not col:
            return pd.Series(0.0, index=df.index)
        return pd.to_numeric(
            df[col].astype(str).str.replace(r"[$,\s]", "", regex=True), errors="coerce"
        ).fillna(0.0)

    df["uae_usd"]   = _spend("UAE")
    df["ksa_usd"]   = _spend("KSA")
    df["usa_usd"]   = _spend("USA")

    # Derive total from per-market columns to be robust against a stale or
    # under-summed "Total Spend" column (e.g. when USA spend is added but the
    # Total formula isn't updated). The per-market columns are the source of
    # truth.
    df["total_usd"] = df["uae_usd"] + df["ksa_usd"] + df["usa_usd"]

    return df[["month_dt", "total_usd", "uae_usd", "ksa_usd", "usa_usd"]].sort_values("month_dt").reset_index(drop=True)


@st.cache_data(ttl=300, show_spinner=False)
def load_marketing_spend_daily() -> pd.DataFrame:
    """
    'Paid Ads Spend - Daily' tab → daily blended ad spend in USD per market.
    Same column schema as Marketing Spend but one row per day.

    Returns: date (Timestamp), total_usd, uae_usd, ksa_usd, usa_usd

    Use this in preference to the monthly-prorated load_marketing_spend()
    for sub-month windows (MTD, trailing 7d, day-by-day charts).
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    rows = raw_data.get("Paid Ads Spend - Daily", [])
    df   = _rows_to_df(rows)
    empty = pd.DataFrame(columns=["date", "total_usd", "uae_usd", "ksa_usd", "usa_usd"])
    if df.empty:
        return empty

    date_col = df.columns[0]
    df["date"] = pd.to_datetime(df[date_col].astype(str).str.strip(),
                                format="%d %b, %Y", errors="coerce")
    # Fallback parser for any rows that don't match "1 Mar, 2023" format
    bad = df["date"].isna()
    if bad.any():
        df.loc[bad, "date"] = pd.to_datetime(df.loc[bad, date_col], errors="coerce")
    df = df[df["date"].notna()].copy()
    if df.empty:
        return empty

    def _spend(label: str) -> pd.Series:
        col = next((c for c in df.columns if c.strip().lower() == label.lower()), None)
        if not col:
            return pd.Series(0.0, index=df.index)
        return pd.to_numeric(
            df[col].astype(str).str.replace(r"[$,\s]", "", regex=True), errors="coerce"
        ).fillna(0.0)

    df["uae_usd"] = _spend("UAE")
    df["ksa_usd"] = _spend("KSA")
    df["usa_usd"] = _spend("USA")
    df["total_usd"] = df["uae_usd"] + df["ksa_usd"] + df["usa_usd"]

    return df[["date", "total_usd", "uae_usd", "ksa_usd", "usa_usd"]].sort_values("date").reset_index(drop=True)


@st.cache_data(ttl=300, show_spinner=False)
def load_projections() -> dict:
    """
    Load the 'Projections' tab and return a structured dict of per-month
    projections used by the executive-summary target meter.

    Schema returned:
      {
        "2026-05-01": {
            "global":  1015,
            "by_market":   {"UAE": 915, "KSA": 0,   "USA": 100},
            "by_market_pct": {"UAE": 0.90, "KSA": 0.0, "USA": 0.10},
            "by_uae_product":  {"Model 1": 143, "Nano+": 287, ...},
            "by_ksa_product":  {...},
            "by_usa_product":  {"Model 1": 49, "Nano+": 49, ...},
        },
        ...
      }

    Returns empty dict if the tab is missing or malformed.
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    rows = raw_data.get("Projections", [])
    if len(rows) < 4:
        return {}

    # Row 0 = month labels in column B onwards (e.g. "Mar-26")
    month_labels = rows[0][1:]
    months = []
    for lbl in month_labels:
        try:
            m = pd.to_datetime(str(lbl).strip(), format="%b-%y").normalize()
            months.append(m)
        except Exception:
            months.append(None)

    def _row_by_label(label: str, after_idx: int = 0) -> list[str] | None:
        """Find a row whose first cell matches `label` (case-insensitive),
        searching from `after_idx` onwards."""
        target = label.strip().lower()
        for i in range(after_idx, len(rows)):
            r = rows[i]
            if r and str(r[0]).strip().lower() == target:
                return r
        return None

    def _to_int(s) -> int:
        try:
            return int(float(str(s).replace(",", "").replace("%", "").strip() or 0))
        except (ValueError, TypeError):
            return 0

    def _to_pct(s) -> float:
        s = str(s).replace("%", "").strip()
        try:
            v = float(s)
            return v / 100 if v > 1 else v
        except (ValueError, TypeError):
            return 0.0

    # Total rows
    row_uae   = _row_by_label("Total Gross Sales - GCC")
    row_usa   = _row_by_label("Total Gross Sales - USA")
    row_glob  = _row_by_label("Total Gross Sales - Global")
    if not row_glob:
        return {}

    # Find per-market product detail rows by anchor labels
    # (Total Subscription Sales / Total Ownership Sales appear multiple times,
    # one per market — anchor on the section header that precedes them.)
    def _find_section(name: str) -> int:
        n = name.strip().lower()
        for i, r in enumerate(rows):
            if r and len(r) >= 1 and str(r[0]).strip().lower() == n and (len(r) == 1 or not str(r[1]).strip()):
                return i
        return -1

    uae_idx = _find_section("UAE")
    ksa_idx = _find_section("KSA")
    usa_idx = _find_section("USA")

    # Per-market product totals = sum of subscription + ownership rows for each product
    PRODUCTS = ["Model 1", "Nano+", "Bubble", "Flat", "Nano Tank"]

    def _product_totals(section_start: int) -> dict[str, list[int]]:
        """Walk forward from section header; sum subscription + ownership product
        rows until we hit 'Total <market> Sales'."""
        out = {p: [0] * len(months) for p in PRODUCTS}
        if section_start < 0:
            return out
        i = section_start + 1
        while i < len(rows):
            r = rows[i]
            label = str(r[0]).strip() if r else ""
            if label.startswith("Total ") and label.endswith(" Sales") and "Total Subscription" not in label and "Total Ownership" not in label:
                break
            if label in PRODUCTS:
                for j, val in enumerate(r[1:], start=0):
                    if j < len(months) and months[j] is not None:
                        out[label][j] += _to_int(val)
            i += 1
        return out

    uae_prod = _product_totals(uae_idx)
    ksa_prod = _product_totals(ksa_idx)
    usa_prod = _product_totals(usa_idx)

    # Build per-month dict
    out: dict = {}
    for j, m in enumerate(months):
        if m is None:
            continue
        glob = _to_int(row_glob[j + 1])     if j + 1 < len(row_glob)   else 0
        uae  = _to_int(row_uae[j + 1])      if row_uae  and j + 1 < len(row_uae)  else 0
        usa  = _to_int(row_usa[j + 1])      if row_usa  and j + 1 < len(row_usa)  else 0
        ksa  = max(0, uae - sum(uae_prod[p][j] for p in PRODUCTS))  # GCC = UAE row but if KSA breakouts exist
        # Use direct KSA totals if any non-zero
        ksa_direct = sum(ksa_prod[p][j] for p in PRODUCTS)
        if ksa_direct > 0:
            ksa = ksa_direct
            uae = max(0, uae - ksa_direct) if (uae - ksa_direct) > 0 else 0
        denom = glob if glob > 0 else max(uae + ksa + usa, 1)
        out[m.strftime("%Y-%m-%d")] = {
            "month_dt": m,
            "global":   glob,
            "by_market": {"UAE": uae, "KSA": ksa, "USA": usa},
            "by_market_pct": {
                "UAE": uae / denom,
                "KSA": ksa / denom,
                "USA": usa / denom,
            },
            "by_uae_product": {p: uae_prod[p][j] for p in PRODUCTS},
            "by_ksa_product": {p: ksa_prod[p][j] for p in PRODUCTS},
            "by_usa_product": {p: usa_prod[p][j] for p in PRODUCTS},
        }
    return out


@st.cache_data(ttl=300, show_spinner=False)
def load_meta_ads_daily() -> pd.DataFrame:
    """
    Meta Ads Daily - Claude tab → daily performance per market.
    Returns: date (Timestamp), market, spend_usd, clicks, impressions, ctr_pct, cpc_usd
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    rows = raw_data.get("Meta Ads Daily - Claude", [])
    df   = _rows_to_df(rows)
    empty = pd.DataFrame(columns=["date", "market", "spend_usd", "clicks", "impressions", "ctr_pct", "cpc_usd"])
    if df.empty:
        return empty

    df.columns = ["date", "market", "spend_usd", "clicks", "impressions", "ctr_pct", "cpc_usd"]
    df["date"]        = pd.to_datetime(df["date"], errors="coerce")
    df["spend_usd"]   = pd.to_numeric(df["spend_usd"],   errors="coerce").fillna(0.0)
    df["clicks"]      = pd.to_numeric(df["clicks"],      errors="coerce").fillna(0).astype(int)
    df["impressions"] = pd.to_numeric(df["impressions"], errors="coerce").fillna(0).astype(int)
    df["ctr_pct"]     = pd.to_numeric(df["ctr_pct"],     errors="coerce").fillna(0.0)
    df["cpc_usd"]     = pd.to_numeric(df["cpc_usd"],     errors="coerce").fillna(0.0)
    df = df[df["date"].notna()].copy()
    return df.sort_values("date").reset_index(drop=True)


# ── Shopify website analytics (exported from Shopify Analytics) ───────────────

_SHOPIFY_WEBSITE_TABS = [
    ("Shopify Website - UAE", "UAE"),
    ("Shopify Website - KSA", "KSA"),
    ("Shopify Website - USA", "USA"),
]


@st.cache_data(ttl=300, show_spinner=False)
def load_shopify_website_analytics() -> pd.DataFrame:
    """
    Daily funnel data exported from Shopify Analytics into
    'Shopify Website - {market}' tabs.

    Columns: date, market, sessions, add_to_cart, reached_checkout,
             completed_checkout, conversion_rate (float 0-1)
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    records = []

    for tab_name, market in _SHOPIFY_WEBSITE_TABS:
        rows = raw_data.get(tab_name, [])
        if len(rows) < 2:
            continue
        headers = [h.strip().lower() for h in rows[0]]
        for row in rows[1:]:
            if not row or not row[0].strip():
                continue
            padded = row + [""] * max(0, len(headers) - len(row))
            d = dict(zip(headers, padded))

            # Accept both Shopify export ("day") and Apps Script ("date") header names
            date_raw = d.get("date", d.get("day", ""))
            date_val = pd.to_datetime(date_raw, dayfirst=True, errors="coerce")
            if pd.isna(date_val):
                continue

            def _int(key, alt=None):
                val = d.get(key) or (d.get(alt) if alt else None) or 0
                try:
                    return int(float(str(val).replace(",", "").strip() or 0))
                except (ValueError, TypeError):
                    return 0

            # Accept both Apps Script compact names and Shopify verbose export names
            cr_raw = str(
                d.get("conversion_rate", d.get("conversion rate", "0"))
            ).replace("%", "").strip()
            try:
                cr = float(cr_raw) / 100 if float(cr_raw) > 1 else float(cr_raw)
            except ValueError:
                cr = 0.0

            records.append({
                "date":                date_val,
                "market":              market,
                "sessions":            _int("sessions"),
                "new_sessions":        _int("new_sessions"),
                "returning_sessions":  _int("returning_sessions"),
                "add_to_cart":         _int("add_to_cart", "sessions with cart additions"),
                "reached_checkout":    _int("reached_checkout", "sessions that reached checkout"),
                "completed_checkout":  _int("completed_checkout", "sessions that completed checkout"),
                "conversion_rate":     cr,
            })

    cols = ["date", "market", "sessions", "new_sessions", "returning_sessions",
            "add_to_cart", "reached_checkout", "completed_checkout", "conversion_rate"]
    if not records:
        return pd.DataFrame(columns=cols)
    return (pd.DataFrame(records)
            .sort_values("date")
            .reset_index(drop=True))


# ── Meta Ads campaign-level daily ─────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def load_meta_ads_campaign_daily() -> pd.DataFrame:
    """
    Campaign-level Meta Ads data from 'Meta Ads Campaign Daily - Claude'.
    Returns: date, market, campaign_id, campaign_name, objective, status,
             spend_usd, clicks, impressions, ctr_pct, cpc_usd, cpm_usd
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    rows = raw_data.get("Meta Ads Campaign Daily - Claude", [])
    cols = ["date", "market", "campaign_id", "campaign_name", "objective", "status",
            "spend_usd", "clicks", "impressions", "ctr_pct", "cpc_usd", "cpm_usd"]
    if len(rows) < 2:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(rows[1:], columns=[h.strip() for h in rows[0]])
    rename = {
        "Date": "date", "Market": "market",
        "Campaign ID": "campaign_id", "Campaign Name": "campaign_name",
        "Objective": "objective", "Status": "status",
        "Spend (USD)": "spend_usd", "Clicks": "clicks",
        "Impressions": "impressions", "CTR (%)": "ctr_pct",
        "CPC (USD)": "cpc_usd", "CPM (USD)": "cpm_usd",
    }
    df = df.rename(columns=rename)
    df["date"]        = pd.to_datetime(df["date"], errors="coerce")
    df["spend_usd"]   = pd.to_numeric(df["spend_usd"],   errors="coerce").fillna(0.0)
    df["clicks"]      = pd.to_numeric(df["clicks"],      errors="coerce").fillna(0).astype(int)
    df["impressions"] = pd.to_numeric(df["impressions"], errors="coerce").fillna(0).astype(int)
    df["ctr_pct"]     = pd.to_numeric(df["ctr_pct"],     errors="coerce").fillna(0.0)
    df["cpc_usd"]     = pd.to_numeric(df["cpc_usd"],     errors="coerce").fillna(0.0)
    df["cpm_usd"]     = pd.to_numeric(df["cpm_usd"],     errors="coerce").fillna(0.0)
    df = df[df["date"].notna()].copy()
    return df.sort_values(["date", "market", "campaign_name"]).reset_index(drop=True)


# ── Sessions attributed by traffic source ─────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def load_sessions_by_source() -> pd.DataFrame:
    """
    Daily sessions broken down by channel / utm_source / utm_campaign.
    Source: 'Sessions by Source - Daily' tab written by the Apps Script.
    Returns: date, market, channel, utm_source, utm_campaign,
             sessions, add_to_cart, reached_checkout, completed_checkout
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    rows = raw_data.get("Sessions by Source - Daily", [])
    cols = ["date", "market", "channel", "utm_source", "utm_campaign",
            "sessions", "add_to_cart", "reached_checkout", "completed_checkout"]
    if len(rows) < 2:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(rows[1:], columns=[h.strip().lower() for h in rows[0]])
    df["date"] = pd.to_datetime(df.get("date", ""), dayfirst=True, errors="coerce")
    for c in ("sessions", "add_to_cart", "reached_checkout", "completed_checkout"):
        df[c] = pd.to_numeric(df.get(c, 0), errors="coerce").fillna(0).astype(int)
    df = df[df["date"].notna()].copy()
    return df.sort_values(["date", "market", "sessions"], ascending=[True, True, False]).reset_index(drop=True)


# ── Top landing pages ─────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def load_top_landing_pages() -> pd.DataFrame:
    """
    Top 10 landing pages per market per day.
    Returns: date, market, page_path, sessions, add_to_cart
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    rows = raw_data.get("Top Landing Pages - Daily", [])
    cols = ["date", "market", "page_path", "sessions", "add_to_cart"]
    if len(rows) < 2:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(rows[1:], columns=[h.strip().lower() for h in rows[0]])
    df["date"] = pd.to_datetime(df.get("date", ""), dayfirst=True, errors="coerce")
    for c in ("sessions", "add_to_cart"):
        df[c] = pd.to_numeric(df.get(c, 0), errors="coerce").fillna(0).astype(int)
    df = df[df["date"].notna()].copy()
    return df.sort_values(["date", "market", "sessions"], ascending=[True, True, False]).reset_index(drop=True)


# ── Historical channel attribution (Channel Hist - {market} tabs) ─────────────

RAW_EVENTS_SHEET_ID = "1j9lWQC9I8HdtTguzcGGX1AewE6KkdICkhbGYqwErKKU"

_CHANNEL_HIST_TABS = [
    ("Channel Hist - UAE", "UAE"),
    ("Channel Hist - KSA", "KSA"),
    ("Channel Hist - USA", "USA"),
]


def _classify_channel_py(referrer_source: str, utm_source: str, utm_medium: str) -> str:
    """Mirror of the Apps Script classifyChannel() — keep them in sync."""
    rs = (referrer_source or "").strip().lower()
    us = (utm_source      or "").strip().lower()
    um = (utm_medium      or "").strip().lower()

    # UTM-driven paid first (highest confidence)
    if um in ("cpc", "ppc", "paid"):
        if "google" in us:    return "Paid Search (Google)"
        if any(s in us for s in ("facebook", "instagram", "meta")):
            return "Paid Social (Meta)"
        if "tiktok" in us:    return "Paid Social (TikTok)"
        return "Paid Other"
    if any(s in us for s in ("facebook", "instagram", "meta")):
        return "Paid Social (Meta)"
    if "google" in us and um != "organic":
        return "Paid Search (Google)"
    if "tiktok" in us:    return "Paid Social (TikTok)"
    if "snapchat" in us:  return "Paid Social (Snapchat)"

    # Email / SMS
    if um == "email" or any(s in us for s in ("klaviyo", "mailchimp")):
        return "Email"
    if um == "sms":
        return "SMS"

    # Fall back on Shopify's referrer_source bucket
    if rs == "search":   return "Organic Search"
    if rs == "social":   return "Organic Social"
    if rs == "email":    return "Email"
    if rs == "referral": return "Referral"
    if rs == "direct":   return "Direct"
    if rs == "unknown":  return "Direct"
    return "Direct"


@st.cache_data(ttl=600, show_spinner="Loading channel history…")
def load_channel_history() -> pd.DataFrame:
    """
    Reads historical Shopify channel-attribution exports from the raw events
    spreadsheet (separate from the main dashboard sheet).

    Tabs: Channel Hist - UAE/KSA/USA
    Columns expected: Day, Referrer source, UTM source, UTM medium, UTM campaign,
                      Sessions, Sessions with cart additions, Sessions reached
                      checkout, Sessions completed checkout, Conversion rate

    Returns: date, market, channel, referrer_source, utm_source, utm_medium,
             utm_campaign, sessions, add_to_cart, reached_checkout,
             completed_checkout
    """
    cols = ["date", "market", "channel", "referrer_source", "utm_source",
            "utm_medium", "utm_campaign", "sessions", "add_to_cart",
            "reached_checkout", "completed_checkout"]

    creds = get_credentials()
    svc   = build("sheets", "v4", credentials=creds, cache_discovery=False)
    frames = []

    for tab, market in _CHANNEL_HIST_TABS:
        try:
            rows = (svc.spreadsheets().values()
                       .get(spreadsheetId=RAW_EVENTS_SHEET_ID, range=f"'{tab}'")
                       .execute().get("values", []))
        except Exception as e:
            logger.warning("Channel hist fetch failed for %s: %s", tab, e)
            continue

        if len(rows) < 2:
            continue

        headers = [h.strip().lower() for h in rows[0]]
        df_rows = []
        for r in rows[1:]:
            if not r or not r[0].strip():
                continue
            padded = r + [""] * max(0, len(headers) - len(r))
            d = dict(zip(headers, padded))
            df_rows.append(d)

        if not df_rows:
            continue

        df = pd.DataFrame(df_rows)
        # First column may be "day" or "month" depending on export granularity
        date_col = next((c for c in ("day", "month", "date") if c in df.columns), None)
        if not date_col:
            continue

        def _int(s):
            return int(float(str(s).replace(",", "").strip() or 0)) if str(s).strip() else 0

        out = pd.DataFrame({
            "date":             pd.to_datetime(df[date_col], dayfirst=True, errors="coerce"),
            "market":           market,
            "referrer_source":  df.get("referrer source", "").astype(str).str.strip().str.lower(),
            "utm_source":       df.get("utm source",      "").astype(str).str.strip().str.lower(),
            "utm_medium":       df.get("utm medium",      "").astype(str).str.strip().str.lower(),
            "utm_campaign":     df.get("utm campaign",    "").astype(str).str.strip(),
            "sessions":         df.get("sessions", 0).apply(_int),
            "add_to_cart":      df.get("sessions with cart additions", 0).apply(_int),
            "reached_checkout": df.get("sessions that reached checkout", 0).apply(_int),
            "completed_checkout": df.get("sessions that completed checkout", 0).apply(_int),
        })
        out = out[out["date"].notna()].copy()
        # Replace empty utm tokens with "(none)" for grouping consistency
        for c in ("utm_source", "utm_campaign"):
            out[c] = out[c].replace("", "(none)")
        out["channel"] = out.apply(
            lambda r: _classify_channel_py(r["referrer_source"], r["utm_source"], r["utm_medium"]),
            axis=1,
        )
        frames.append(out[cols])

    if not frames:
        return pd.DataFrame(columns=cols)
    return (pd.concat(frames, ignore_index=True)
              .sort_values(["date", "market", "sessions"], ascending=[True, True, False])
              .reset_index(drop=True))


@st.cache_data(ttl=300, show_spinner=False)
def load_channel_attribution_unified() -> pd.DataFrame:
    """
    Single source of truth for channel attribution: historical Shopify exports
    UNIONED with live pixel-derived 'Sessions by Source - Daily' data.

    Returns same schema as load_channel_history(). Live rows take precedence
    when there's an overlap on (date, market, channel, utm_source, utm_campaign).
    """
    hist = load_channel_history()
    live = load_sessions_by_source()

    cols = ["date", "market", "channel", "referrer_source", "utm_source",
            "utm_medium", "utm_campaign", "sessions", "add_to_cart",
            "reached_checkout", "completed_checkout"]

    if live.empty:
        return hist if not hist.empty else pd.DataFrame(columns=cols)

    # Normalise live to match historical schema
    live = live.copy()
    live["referrer_source"] = ""
    live["utm_medium"]      = ""
    live = live.rename(columns={
        # already has: date, market, channel, utm_source, utm_campaign,
        # sessions, add_to_cart, reached_checkout, completed_checkout
    })
    live = live[cols]

    if hist.empty:
        return live.sort_values(["date", "market", "sessions"], ascending=[True, True, False]).reset_index(drop=True)

    # Cut historical at the first day where live data exists per market
    live_first_by_market = (
        live.groupby("market", as_index=False)["date"].min()
            .rename(columns={"date": "live_first"})
    )
    hist_filtered = hist.merge(live_first_by_market, on="market", how="left")
    hist_filtered = hist_filtered[
        hist_filtered["live_first"].isna() | (hist_filtered["date"] < hist_filtered["live_first"])
    ].drop(columns=["live_first"])

    return (pd.concat([hist_filtered, live], ignore_index=True)
              .sort_values(["date", "market", "sessions"], ascending=[True, True, False])
              .reset_index(drop=True))


# ── Shopify store analytics ────────────────────────────────────────────────────

_SHOPIFY_MARKETS = [
    ("UAE", "SHOPIFY_STORE_UAE", "SHOPIFY_TOKEN_UAE"),
    ("KSA", "SHOPIFY_STORE_KSA", "SHOPIFY_TOKEN_KSA"),
    ("USA", "SHOPIFY_STORE_USA", "SHOPIFY_TOKEN_USA"),
]


def _shopify_creds() -> list[tuple[str, str, str]]:
    """Returns [(market, store_domain, token)] for markets that have secrets configured."""
    out = []
    for market, store_key, token_key in _SHOPIFY_MARKETS:
        try:
            store = str(st.secrets[store_key]).strip().rstrip("/")
            token = str(st.secrets[token_key]).strip()
            if store and token:
                out.append((market, store, token))
        except (KeyError, FileNotFoundError):
            pass
    return out


def _shopify_rest(store: str, token: str, path: str, params: dict | None = None) -> dict:
    """Shopify Admin REST API GET with retry."""
    url = f"https://{store}/admin/api/2024-10/{path}"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, headers=headers, params=params or {}, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(RETRY_BACKOFF[attempt])
    return {}


def _shopify_graphql(store: str, token: str, query: str) -> dict:
    """Shopify Admin GraphQL API (used for ShopifyQL analytics)."""
    url = f"https://{store}/admin/api/2024-10/graphql.json"
    r = requests.post(
        url,
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        json={"query": query},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _shopify_analyticsql(store: str, token: str, ql: str) -> dict:
    """
    Run a ShopifyQL analyticsReport query and return {header: value} for the
    first (summary) row.  Returns {} on any error without raising.
    """
    gql_query = '{ analyticsReport(query: "' + ql.replace('"', '\\"') + '") { result { headers rowData } } }'
    try:
        gql = _shopify_graphql(store, token, gql_query)
        errors = gql.get("errors") or []
        if errors:
            logger.info("ShopifyQL error for %s: %s", store, errors)
            return {}
        result   = gql.get("data", {}).get("analyticsReport", {}).get("result", {})
        headers  = result.get("headers", [])
        row_data = result.get("rowData", [])
        if not headers or not row_data:
            return {}
        h = {v.lower(): i for i, v in enumerate(headers)}
        return {k: row_data[0][i] for k, i in h.items()}
    except Exception as exc:
        logger.info("ShopifyQL failed for %s: %s", store, exc)
        return {}


@st.cache_data(ttl=3600, show_spinner=False)
def load_shopify_store_analytics() -> pd.DataFrame:
    """
    Online store performance metrics (MTD) from all configured Shopify stores.

    Always available (read_orders scope):
        orders, revenue_local, aov_local, abandoned_checkouts

    Requires read_analytics scope (ShopifyQL — Advanced/Plus plans):
        sessions, added_to_cart, reached_checkout, completed_checkout,
        conversion_rate, bounce_rate

    Returns one row per market with all columns; None = data unavailable.
    """
    creds = _shopify_creds()
    empty_cols = [
        "market", "configured", "missing_scopes",
        "orders", "revenue_local", "aov_local", "abandoned_checkouts",
        "sessions", "added_to_cart", "reached_checkout", "completed_checkout",
        "conversion_rate", "bounce_rate", "analytics_note",
    ]
    if not creds:
        return pd.DataFrame(columns=empty_cols)

    today       = pd.Timestamp.today()
    m_start     = today.replace(day=1)
    m_start_str = m_start.strftime("%Y-%m-%dT00:00:00")
    today_str   = today.strftime("%Y-%m-%dT23:59:59")
    since_ql    = m_start.strftime("%Y-%m-%d")
    until_ql    = today.strftime("%Y-%m-%d")

    records = []
    for market, store, token in creds:
        rec: dict = {k: None for k in empty_cols}
        rec.update({"market": market, "configured": True, "missing_scopes": [],
                    "orders": 0, "revenue_local": 0.0, "aov_local": 0.0,
                    "abandoned_checkouts": 0, "analytics_note": ""})

        # ── Scope check ───────────────────────────────────────────────────────
        try:
            scope_resp = _shopify_rest(store, token, "access_scopes.json")
            granted    = {s.get("handle", "") for s in scope_resp.get("access_scopes", [])}
            missing    = [s for s in ("read_orders", "read_analytics", "read_checkouts")
                          if s not in granted]
            rec["missing_scopes"] = missing
            if missing:
                logger.warning("Shopify %s missing scopes: %s", market, missing)
        except Exception as e:
            logger.info("Scope check failed for %s: %s", market, e)

        try:
            # ── Orders (count + revenue) ──────────────────────────────────────
            cnt = _shopify_rest(store, token, "orders/count.json",
                                {"status": "any", "created_at_min": m_start_str,
                                 "created_at_max": today_str})
            if "errors" not in cnt:
                rec["orders"] = int(cnt.get("count", 0))

            ords = _shopify_rest(store, token, "orders.json",
                                 {"status": "any", "created_at_min": m_start_str,
                                  "created_at_max": today_str,
                                  "fields": "id,total_price", "limit": 250})
            order_list = ords.get("orders", [])
            total_rev  = sum(float(o.get("total_price", 0)) for o in order_list)
            rec["revenue_local"] = total_rev
            rec["aov_local"]     = total_rev / len(order_list) if order_list else 0.0

            # ── Abandoned checkouts ───────────────────────────────────────────
            try:
                ab = _shopify_rest(store, token, "checkouts/count.json",
                                   {"created_at_min": m_start_str,
                                    "created_at_max": today_str})
                if "errors" not in ab:
                    rec["abandoned_checkouts"] = int(ab.get("count", 0))
            except Exception:
                pass

            # ── Full funnel via ShopifyQL ─────────────────────────────────────
            # Primary attempt: session funnel (Advanced / Plus)
            funnel_row = _shopify_analyticsql(
                store, token,
                f"SHOW sessions, added_to_cart_sessions, reached_checkout_sessions, "
                f"sessions_converted, conversion_rate, bounce_rate FROM sessions "
                f"SINCE {since_ql} UNTIL {until_ql}",
            )
            if funnel_row:
                def _int(k):
                    v = funnel_row.get(k)
                    return int(float(v)) if v not in (None, "") else None

                def _pct(k):
                    v = funnel_row.get(k)
                    if v in (None, ""):
                        return None
                    f = float(v)
                    return f / 100 if f > 1 else f

                rec["sessions"]           = _int("sessions")
                rec["added_to_cart"]      = _int("added_to_cart_sessions")
                rec["reached_checkout"]   = _int("reached_checkout_sessions")
                rec["completed_checkout"] = _int("sessions_converted")
                rec["conversion_rate"]    = _pct("conversion_rate")
                rec["bounce_rate"]        = _pct("bounce_rate")
            else:
                # Fallback: basic sessions + conversion only
                basic = _shopify_analyticsql(
                    store, token,
                    f"SHOW sessions, conversion_rate FROM sessions "
                    f"SINCE {since_ql} UNTIL {until_ql}",
                )
                if basic:
                    v = basic.get("sessions")
                    rec["sessions"] = int(float(v)) if v not in (None, "") else None
                    cr = basic.get("conversion_rate")
                    if cr not in (None, ""):
                        f = float(cr)
                        rec["conversion_rate"] = f / 100 if f > 1 else f
                    rec["analytics_note"] = "Funnel detail unavailable — plan may not support ShopifyQL funnel metrics"
                else:
                    rec["analytics_note"] = "Analytics unavailable — requires read_analytics scope + Advanced/Plus plan"

        except Exception as exc:
            logger.warning("Shopify REST failed for %s: %s", market, exc)
            rec["configured"] = False

        records.append(rec)

    return pd.DataFrame(records)


@st.cache_data(ttl=3600, show_spinner=False)
def load_shopify_funnel_daily(days: int = 30) -> pd.DataFrame:
    """
    Daily funnel breakdown for the last `days` days across all configured stores.

    Returns: date (Timestamp), market, sessions, added_to_cart,
             reached_checkout, completed_checkout, conversion_rate
    Rows only present where ShopifyQL returns data.
    """
    creds = _shopify_creds()
    empty = pd.DataFrame(columns=[
        "date", "market", "sessions", "added_to_cart",
        "reached_checkout", "completed_checkout", "conversion_rate",
    ])
    if not creds:
        return empty

    until_dt  = pd.Timestamp.today().normalize()
    since_dt  = until_dt - pd.Timedelta(days=days - 1)
    since_ql  = since_dt.strftime("%Y-%m-%d")
    until_ql  = until_dt.strftime("%Y-%m-%d")

    all_rows = []
    for market, store, token in creds:
        gql_query = (
            f"SHOW day, sessions, added_to_cart_sessions, "
            f"reached_checkout_sessions, sessions_converted, conversion_rate "
            f"FROM sessions SINCE {since_ql} UNTIL {until_ql}"
        )
        gql_raw = '{ analyticsReport(query: "' + gql_query.replace('"', '\\"') + '") { result { headers rowData } } }'
        try:
            gql    = _shopify_graphql(store, token, gql_raw)
            result = gql.get("data", {}).get("analyticsReport", {}).get("result", {})
            headers  = result.get("headers", [])
            row_data = result.get("rowData", [])
            if not headers or not row_data:
                continue
            h = {v.lower(): i for i, v in enumerate(headers)}
            for row in row_data:
                def _g(k, cast=float):
                    v = row[h[k]] if k in h else None
                    try:
                        return cast(v) if v not in (None, "") else None
                    except (ValueError, TypeError):
                        return None

                dt = _g("day", str)
                if not dt:
                    continue
                cr_raw = _g("conversion_rate")
                cr = (cr_raw / 100 if cr_raw and cr_raw > 1 else cr_raw)
                all_rows.append({
                    "date":               pd.to_datetime(dt, errors="coerce"),
                    "market":             market,
                    "sessions":           _g("sessions", int) if "sessions" in h else None,
                    "added_to_cart":      _g("added_to_cart_sessions", int) if "added_to_cart_sessions" in h else None,
                    "reached_checkout":   _g("reached_checkout_sessions", int) if "reached_checkout_sessions" in h else None,
                    "completed_checkout": _g("sessions_converted", int) if "sessions_converted" in h else None,
                    "conversion_rate":    cr,
                })
        except Exception as exc:
            logger.info("Funnel daily for %s failed: %s", market, exc)

    if not all_rows:
        return empty
    return pd.DataFrame(all_rows).dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


# ── Historical loaders (pre-Sep-2025) ─────────────────────────────────────────

def _parse_hist_matrix(
    vals: list[list],
    row_map: dict[tuple, int],
) -> pd.DataFrame:
    """
    Parse a historical matrix tab (months as columns, named rows).

    row_map: {key_tuple: python_row_index}
    Returns long-format DataFrame: month_dt + all key columns as rows,
    only months < LIVE_DATA_START.
    """
    if not vals or len(vals) < 2:
        return pd.DataFrame()

    header = vals[0]
    month_cols: list[tuple[int, pd.Timestamp]] = []
    for i, h in enumerate(header[1:], start=1):
        h_str = str(h).strip()
        if not h_str:
            continue
        try:
            dt = pd.to_datetime(h_str.replace("'", "-"), format="%b-%y")
            if dt < LIVE_DATA_START:
                month_cols.append((i, dt))
        except Exception:
            continue

    if not month_cols:
        return pd.DataFrame()

    records = []
    for key, row_idx in row_map.items():
        row = vals[row_idx] if row_idx < len(vals) else []
        for col_i, month_dt in month_cols:
            try:
                raw = str(row[col_i]).replace(",", "").strip() if col_i < len(row) else ""
                qty = int(float(raw)) if raw else 0
            except (ValueError, IndexError):
                qty = 0
            records.append((*key, month_dt, qty))

    return pd.DataFrame(records)


@st.cache_data(ttl=3600, show_spinner=False)
def load_historical_sales() -> pd.DataFrame:
    """
    Pre-Sep-2025 monthly sales from Monthly Sales tab (hardcoded, final truth).
    Returns: month_dt, market, product, is_ownership (bool), qty (int)
    Only months before LIVE_DATA_START.
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    vals = raw_data.get("Monthly Sales", [])
    df   = _parse_hist_matrix(vals, _HIST_SALES_ROWS)
    if df.empty:
        return pd.DataFrame(columns=["month_dt", "market", "product", "is_ownership", "qty"])

    df.columns = ["market", "product", "is_ownership", "month_dt", "qty"]
    df["is_ownership"] = df["is_ownership"].astype(bool)
    return df[df["qty"] > 0].reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def load_historical_cancellations() -> pd.DataFrame:
    """
    Pre-Sep-2025 monthly true cancellations from Monthly Cancellations tab.
    Returns: month_dt, market, product, qty (int)
    Only months before LIVE_DATA_START.
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    vals = raw_data.get("Monthly Cancellations", [])
    df   = _parse_hist_matrix(vals, _HIST_CANCEL_ROWS)
    if df.empty:
        return pd.DataFrame(columns=["month_dt", "market", "product", "qty"])

    df.columns = ["market", "product", "month_dt", "qty"]
    return df[df["qty"] > 0].reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def load_historical_ownership_seed() -> pd.DataFrame:
    """
    Aug-2025 ending ownership counts from Monthly User Base tab.
    Used as the starting seed for computing live-era ownership active users.

    Returns: market, product, qty (int)
    One row per (market, product) combination that has a non-zero count.
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    vals = raw_data.get("Monthly User Base", [])
    if not vals:
        return pd.DataFrame(columns=["market", "product", "qty"])

    # Find the Aug-25 column
    header    = vals[0] if vals else []
    aug25_col = None
    for i, h in enumerate(header[1:], start=1):
        try:
            dt = pd.to_datetime(str(h).strip().replace("'", "-"), format="%b-%y")
            if dt == OWNERSHIP_SEED_DT:
                aug25_col = i
                break
        except Exception:
            continue

    if aug25_col is None:
        logger.warning("Aug-25 column not found in Monthly User Base — ownership seed = 0")
        return pd.DataFrame(columns=["market", "product", "qty"])

    records = []
    for (market, product), row_idx in _HIST_UB_OWN_ROWS.items():
        row = vals[row_idx] if row_idx < len(vals) else []
        try:
            raw = str(row[aug25_col]).replace(",", "").strip() if aug25_col < len(row) else ""
            qty = int(float(raw)) if raw else 0
        except (ValueError, IndexError):
            qty = 0
        records.append((market, product, qty))

    df = pd.DataFrame(records, columns=["market", "product", "qty"])
    return df


# ── Compute functions ──────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def get_all_machine_sales(
    start_dt: pd.Timestamp | None = None,
    end_dt: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """
    All machine sales from LIVE_DATA_START onwards (subscription + ownership).
    Optionally filtered by [start_dt, end_dt] (inclusive).

    Returns: date, market, product, is_ownership (bool), is_offline (bool),
             qty (int)

    `is_offline` distinguishes online (Recharge / Shopify) from offline
    (Offline - Subscriptions / Offline - Ownership) sources. Useful for
    excluding B2B / direct offline deals from CAC denominators since
    those aren't acquired through paid ads.

    Sources:
      Subscriptions: Recharge created_at_dt × quantity (Machine category)  → online
                   + Offline - Subscriptions                                → offline
      Ownership:    Shopify ownership unit columns × literal value         → online
                   + Offline - Ownership                                    → offline
    """
    records = []
    _sd = start_dt or LIVE_DATA_START
    _ed = end_dt or pd.Timestamp.today().normalize()

    # ── Recharge subscriptions (online) ──────────────────────────────────────
    rc = load_recharge_full()
    rc_machine = rc[
        (rc["category"] == "Machine") &
        rc["created_at_dt"].notna() &
        (rc["created_at_dt"] >= _sd) &
        (rc["created_at_dt"] <= _ed)
    ]
    for _, row in rc_machine.iterrows():
        records.append((
            row["created_at_dt"].normalize(),
            row["market"],
            row["product"],
            False,  # is_ownership
            False,  # is_offline
            int(row["quantity"]),
        ))

    # ── Offline subscriptions ────────────────────────────────────────────────
    off_sub = load_offline_subscriptions()
    for _, row in off_sub[(off_sub["date"] >= _sd) & (off_sub["date"] <= _ed)].iterrows():
        records.append((row["date"], row["market"], row["product"], False, True, int(row["qty"])))

    # ── Shopify ownership (online) ───────────────────────────────────────────
    shop_own = load_shopify_ownership()
    for _, row in shop_own[(shop_own["date"] >= _sd) & (shop_own["date"] <= _ed)].iterrows():
        records.append((row["date"], row["market"], row["product"], True, False, int(row["qty"])))

    # ── Offline ownership ────────────────────────────────────────────────────
    off_own = load_offline_ownership()
    for _, row in off_own[(off_own["date"] >= _sd) & (off_own["date"] <= _ed)].iterrows():
        records.append((row["date"], row["market"], row["product"], True, True, int(row["qty"])))

    cols = ["date", "market", "product", "is_ownership", "is_offline", "qty"]
    if not records:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(records, columns=cols)


@st.cache_data(ttl=300, show_spinner=False)
def get_monthly_sales_blended() -> pd.DataFrame:
    """
    Full monthly sales series blending historical (pre-Sep-2025) + live data.

    Returns: month_dt, market, product, is_ownership (bool), qty (int)
    Sorted by month_dt ascending.
    """
    frames = []

    # ── Historical (pre-Sep-2025) ─────────────────────────────────────────────
    hist = load_historical_sales()
    if not hist.empty:
        frames.append(hist[["month_dt", "market", "product", "is_ownership", "qty"]])

    # ── Live (Sep-2025 onwards) ───────────────────────────────────────────────
    live = get_all_machine_sales(start_dt=LIVE_DATA_START)
    if not live.empty:
        live["month_dt"] = live["date"].dt.to_period("M").dt.to_timestamp()
        live_monthly = (
            live.groupby(["month_dt", "market", "product", "is_ownership"], as_index=False)["qty"].sum()
        )
        frames.append(live_monthly[["month_dt", "market", "product", "is_ownership", "qty"]])

    if not frames:
        return pd.DataFrame(columns=["month_dt", "market", "product", "is_ownership", "qty"])

    df = pd.concat(frames, ignore_index=True)
    return df.sort_values("month_dt").reset_index(drop=True)


def _load_historical_user_base(
    as_of: pd.Timestamp,
    kind: str,
) -> pd.DataFrame:
    """
    Read per-(market, product) active counts directly from the Monthly User
    Base sheet, for the calendar month containing `as_of`.

    Used as the authoritative source for any date BEFORE LIVE_DATA_START
    (Sep-2025). Recharge alone undercounts historical actives because
    DELETED rows are stripped on load; and the ownership flow data only
    starts at Sep-2025, so neither can reconstruct pre-Sep-2025 truth
    on their own. The Monthly User Base sheet is the manually-maintained
    ground truth for that period.

    Parameters
    ----------
    as_of : pd.Timestamp
        Any date in the desired month. Snapped to that month's column.
    kind : str
        "sub" → uses _HIST_UB_SUB_ROWS (active subscribers).
        "own" → uses _HIST_UB_OWN_ROWS (active owners).

    Returns
    -------
    DataFrame[market, product, qty] — one row per (market, product) with
    qty > 0. Empty if the column isn't found (e.g. as_of pre-dates the
    sheet's history).

    Sheet only covers UAE + KSA. USA didn't have ownership/subscriptions
    pre-2026, so absence of USA rows here is correct.
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    vals = raw_data.get("Monthly User Base", [])
    if not vals:
        return pd.DataFrame(columns=["market", "product", "qty"])

    target = as_of.to_period("M").to_timestamp()
    header = vals[0] if vals else []
    target_col = None
    for i, h in enumerate(header[1:], start=1):
        try:
            dt = pd.to_datetime(str(h).strip().replace("'", "-"), format="%b-%y")
            if dt == target:
                target_col = i
                break
        except Exception:
            continue

    if target_col is None:
        logger.warning(
            "Monthly User Base: no column for %s (kind=%s); returning empty",
            target.strftime("%b-%y"), kind,
        )
        return pd.DataFrame(columns=["market", "product", "qty"])

    row_map = _HIST_UB_SUB_ROWS if kind == "sub" else _HIST_UB_OWN_ROWS
    records = []
    for (market, product), row_idx in row_map.items():
        row = vals[row_idx] if row_idx < len(vals) else []
        try:
            raw = str(row[target_col]).replace(",", "").strip() if target_col < len(row) else ""
            qty = int(float(raw)) if raw else 0
        except (ValueError, IndexError):
            qty = 0
        if qty > 0:
            records.append((market, product, qty))

    return pd.DataFrame(records, columns=["market", "product", "qty"])


@st.cache_data(ttl=300, show_spinner=False)
def get_active_subscriptions(
    as_of: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """
    Active machine subscribers at a given point in time.

    Behaviour depends on `as_of`:

      • as_of < LIVE_DATA_START (Sep-2025): read directly from the
        Monthly User Base sheet for that month. This is the manually-
        maintained historical truth — Recharge alone undercounts because
        DELETED rows get stripped on load.

      • as_of >= LIVE_DATA_START: compute live from raw sources:
          - Recharge: created_at <= as_of AND (cancelled_at is null OR > as_of)
          - Offline - Subscriptions: all rows with date <= as_of stay
            active (no cancellation feed exists for offline subs).

    Returns: market, product, qty (int)  — one row per (market, product).
    """
    as_of = (as_of or pd.Timestamp.today()).normalize()

    # ── Pre-Sep-2025: authoritative historical sheet ─────────────────────────
    if as_of < LIVE_DATA_START:
        return _load_historical_user_base(as_of, kind="sub")

    # ── Sep-2025 onwards: live reconstruction ────────────────────────────────
    # Recharge: explicit lifecycle
    rc = load_recharge_full()
    rc_grouped = pd.DataFrame(columns=["market", "product", "qty"])
    if not rc.empty:
        mask = (
            (rc["category"] == "Machine") &
            (rc["created_at_dt"].notna()) &
            (rc["created_at_dt"] <= as_of) &
            (rc["cancelled_at_dt"].isna() | (rc["cancelled_at_dt"] > as_of))
        )
        rc_grouped = (
            rc[mask]
            .groupby(["market", "product"], as_index=False)["quantity"]
            .sum()
            .rename(columns={"quantity": "qty"})
        )

    # ── Offline subscriptions: cumulative count up to as_of ──────────────────
    off_sub = load_offline_subscriptions()
    off_grouped = pd.DataFrame(columns=["market", "product", "qty"])
    if not off_sub.empty:
        in_window = off_sub[off_sub["date"] <= as_of]
        if not in_window.empty:
            off_grouped = in_window.groupby(
                ["market", "product"], as_index=False
            )["qty"].sum()

    # ── Combine and return one row per (market, product) ─────────────────────
    combined = pd.concat([rc_grouped, off_grouped], ignore_index=True)
    if combined.empty:
        return pd.DataFrame(columns=["market", "product", "qty"])
    return (
        combined.groupby(["market", "product"], as_index=False)["qty"].sum()
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_active_ownership(
    as_of: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """
    Active ownership users at a given point in time.

    Behaviour depends on `as_of`:

      • as_of < LIVE_DATA_START (Sep-2025): read directly from the
        Monthly User Base sheet for that month. Manually-maintained
        historical truth — the previous "seed + zero deltas" approach
        returned the Aug-2025 seed unconditionally for every past
        month, which is wrong for any date older than Aug-2025.

      • as_of >= LIVE_DATA_START: compute additively:
          seed (Aug-2025 from Monthly User Base)
          + Shopify ownership sales (Sep-2025 → as_of)
          + Offline ownership          (Sep-2025 → as_of)
          − Returns                    (Sep-2025 → as_of)

    Returns: market, product, qty (int) — qty can be 0 but not negative.
    """
    as_of = (as_of or pd.Timestamp.today()).normalize()

    # ── Pre-Sep-2025: authoritative historical sheet ─────────────────────────
    if as_of < LIVE_DATA_START:
        return _load_historical_user_base(as_of, kind="own")

    # ── Sep-2025 onwards: seed + flows ───────────────────────────────────────
    seed = load_historical_ownership_seed()
    base = seed.copy() if not seed.empty else pd.DataFrame(columns=["market", "product", "qty"])

    def _agg(df: pd.DataFrame, sign: int) -> pd.DataFrame:
        """Filter to [LIVE_DATA_START, as_of] and sum qty by market/product."""
        if df.empty:
            return pd.DataFrame(columns=["market", "product", "qty"])
        filtered = df[(df["date"] >= LIVE_DATA_START) & (df["date"] <= as_of)]
        if filtered.empty:
            return pd.DataFrame(columns=["market", "product", "qty"])
        agg = filtered.groupby(["market", "product"], as_index=False)["qty"].sum()
        agg["qty"] = agg["qty"] * sign
        return agg

    pieces = [base, _agg(load_shopify_ownership(), +1),
                     _agg(load_offline_ownership(), +1),
                     _agg(load_offline_returns(),   -1)]
    combined = pd.concat([p for p in pieces if not p.empty], ignore_index=True)

    if combined.empty:
        return pd.DataFrame(columns=["market", "product", "qty"])

    result = (
        combined.groupby(["market", "product"], as_index=False)["qty"]
        .sum()
    )
    result["qty"] = result["qty"].clip(lower=0)
    return result[result["qty"] > 0].reset_index(drop=True)


@st.cache_data(ttl=300, show_spinner=False)
def get_monthly_cancellations_blended() -> pd.DataFrame:
    """
    Full monthly true-cancellation series (blended historical + live).

    Returns: month_dt, market, product, qty (int)
    Sorted by month_dt ascending.
    """
    frames = []

    # ── Historical (pre-Sep-2025) ─────────────────────────────────────────────
    hist = load_historical_cancellations()
    if not hist.empty:
        frames.append(hist[["month_dt", "market", "product", "qty"]])

    # ── Live (Sep-2025 onwards) from Recharge ─────────────────────────────────
    rc = load_recharge_full()
    rc_live = rc[
        (rc["category"] == "Machine") &
        rc["is_true_cancel"] &
        rc["cancelled_at_dt"].notna() &
        (rc["cancelled_at_dt"] >= LIVE_DATA_START)
    ].copy()
    if not rc_live.empty:
        rc_live["month_dt"] = rc_live["cancelled_at_dt"].dt.to_period("M").dt.to_timestamp()
        live_monthly = (
            rc_live.groupby(["month_dt", "market", "product"], as_index=False)["quantity"]
            .sum()
            .rename(columns={"quantity": "qty"})
        )
        frames.append(live_monthly[["month_dt", "market", "product", "qty"]])

    if not frames:
        return pd.DataFrame(columns=["month_dt", "market", "product", "qty"])

    df = pd.concat(frames, ignore_index=True)
    return df.sort_values("month_dt").reset_index(drop=True)


def compute_cancellation_rate(
    as_of: pd.Timestamp | None = None,
    market: str | None = None,
    product: str | None = None,
) -> dict:
    """
    Compute MTD extrapolated cancellation rate.

    Formula:
        rate = (mtd_cancels / days_elapsed * days_in_month) / active_at_prior_month_end

    Parameters:
        as_of  : reference date (defaults to today)
        market : 'UAE' | 'KSA' | 'USA' | None (all markets)
        product: 'Model 1' | 'Nano+' | ... | None (all products)

    Returns dict with keys:
        rate, mtd_cancels (int), extrapolated_cancels (float),
        active_at_start (int), days_elapsed (int), days_in_month (int),
        market, product, as_of
    """
    today       = (as_of or pd.Timestamp.today()).normalize()
    month_start = today.replace(day=1)
    prior_end   = month_start - timedelta(days=1)
    days_elapsed  = today.day
    days_in_month = calendar.monthrange(today.year, today.month)[1]

    rc = load_recharge_full()

    def _apply_filters(df: pd.DataFrame) -> pd.DataFrame:
        df = df[df["category"] == "Machine"]
        if market and market != "Global":
            df = df[df["market"] == market]
        if product:
            df = df[df["product"] == product]
        return df

    # MTD true cancellations (sum quantity)
    rc_machine = _apply_filters(rc)
    mtd_mask = (
        rc_machine["is_true_cancel"] &
        rc_machine["cancelled_at_dt"].notna() &
        (rc_machine["cancelled_at_dt"] >= month_start) &
        (rc_machine["cancelled_at_dt"] <= today)
    )
    mtd_cancels = int(rc_machine.loc[mtd_mask, "quantity"].sum())

    # Active subscribers at prior month end (denominator)
    active_mask = (
        rc_machine["created_at_dt"].notna() &
        (rc_machine["created_at_dt"] <= prior_end) &
        (rc_machine["cancelled_at_dt"].isna() | (rc_machine["cancelled_at_dt"] > prior_end))
    )
    active_at_start = int(rc_machine.loc[active_mask, "quantity"].sum())

    extrapolated = (mtd_cancels / days_elapsed * days_in_month) if days_elapsed > 0 else 0.0
    rate = extrapolated / active_at_start if active_at_start > 0 else 0.0

    return {
        "rate":                rate,
        "mtd_cancels":         mtd_cancels,
        "extrapolated_cancels": extrapolated,
        "active_at_start":     active_at_start,
        "days_elapsed":        days_elapsed,
        "days_in_month":       days_in_month,
        "market":              market or "Global",
        "product":             product or "All",
        "as_of":               today,
    }


@st.cache_data(ttl=3600, show_spinner=False)
def load_historical_user_base_series() -> pd.DataFrame:
    """
    Pre-Sep-2025 monthly user base (subs + owners) from Monthly User Base tab.
    Returns: month_dt, market, product, sub_qty (int), own_qty (int)
    Only months before LIVE_DATA_START. Nano Tank defaults to 0 (absent from tab).
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    vals = raw_data.get("Monthly User Base", [])

    all_rows = {**_HIST_UB_SUB_ROWS, **{k: v for k, v in _HIST_UB_OWN_ROWS.items()}}

    if not vals or len(vals) < 2:
        return pd.DataFrame(columns=["month_dt", "market", "product", "sub_qty", "own_qty"])

    header = vals[0]
    month_cols: list[tuple[int, pd.Timestamp]] = []
    for i, h in enumerate(header[1:], start=1):
        try:
            dt = pd.to_datetime(str(h).strip().replace("'", "-"), format="%b-%y")
            if dt < LIVE_DATA_START:
                month_cols.append((i, dt))
        except Exception:
            continue

    if not month_cols:
        return pd.DataFrame(columns=["month_dt", "market", "product", "sub_qty", "own_qty"])

    def _get_val(row_idx: int, col_i: int) -> int:
        row = vals[row_idx] if row_idx < len(vals) else []
        try:
            raw = str(row[col_i]).replace(",", "").strip() if col_i < len(row) else ""
            return int(float(raw)) if raw else 0
        except (ValueError, IndexError):
            return 0

    records = []
    for (market, product), _row in _HIST_UB_SUB_ROWS.items():
        for col_i, month_dt in month_cols:
            sub_v = _get_val(_HIST_UB_SUB_ROWS[(market, product)], col_i)
            own_v = _get_val(_HIST_UB_OWN_ROWS.get((market, product), -1), col_i) if (market, product) in _HIST_UB_OWN_ROWS else 0
            records.append((month_dt, market, product, sub_v, own_v))

    # Add Nano Tank with zeros (not in hardcoded tab)
    for market in ("UAE", "KSA"):
        for col_i, month_dt in month_cols:
            records.append((month_dt, market, "Nano Tank", 0, 0))

    df = pd.DataFrame(records, columns=["month_dt", "market", "product", "sub_qty", "own_qty"])
    return df.sort_values("month_dt").reset_index(drop=True)


@st.cache_data(ttl=300, show_spinner=False)
def get_monthly_user_base_blended() -> pd.DataFrame:
    """
    Full monthly user base series (blended historical + live), by market × product.

    Pre-Sep-2025: hardcoded Monthly User Base tab values.
    Sep-2025+:    computed from Recharge (subs) + ownership seed/additions/returns.

    Returns: month_dt, market, product, total (int)
    """
    frames = []

    # ── Historical ────────────────────────────────────────────────────────────
    hist = load_historical_user_base_series()
    if not hist.empty:
        hist_total = hist.copy()
        hist_total["total"] = hist_total["sub_qty"] + hist_total["own_qty"]
        frames.append(hist_total[["month_dt", "market", "product", "total"]])

    # ── Live (Sep-2025 → today, one row per month-end) ───────────────────────
    today = pd.Timestamp.today().normalize()
    months_live: list[pd.Timestamp] = []
    m = LIVE_DATA_START
    while m <= today:
        months_live.append(m)
        m = (m + pd.DateOffset(months=1)).replace(day=1)

    if months_live:
        # Build ownership series (cumulative from seed)
        seed = load_historical_ownership_seed()
        shop_own = load_shopify_ownership()
        off_own  = load_offline_ownership()
        ret      = load_offline_returns()

        # Precompute monthly ownership additions/subtractions per (market, product)
        def _monthly_agg(df: pd.DataFrame, sign: int) -> dict[tuple, dict[pd.Timestamp, int]]:
            """Returns {(market, product): {month_start: qty}} for records in df."""
            if df.empty:
                return {}
            d = df.copy()
            d["month_dt"] = d["date"].dt.to_period("M").dt.to_timestamp()
            d = d[(d["date"] >= LIVE_DATA_START) & (d["date"] <= today)]
            out: dict[tuple, dict] = {}
            for (mkt, prod, mo), grp in d.groupby(["market", "product", "month_dt"]):
                out.setdefault((mkt, prod), {})[mo] = int(grp["qty"].sum()) * sign
            return out

        own_adds = _monthly_agg(shop_own, +1)
        own_adds2 = _monthly_agg(off_own, +1)
        ret_subs  = _monthly_agg(ret, -1)

        # Merge all ownership deltas
        all_own_deltas: dict[tuple, dict[pd.Timestamp, int]] = {}
        for d in [own_adds, own_adds2, ret_subs]:
            for (mkt, prod), monthly in d.items():
                t = all_own_deltas.setdefault((mkt, prod), {})
                for mo, v in monthly.items():
                    t[mo] = t.get(mo, 0) + v

        # Running ownership totals
        seed_map: dict[tuple, int] = {}
        if not seed.empty:
            for _, row in seed.iterrows():
                seed_map[(row["market"], row["product"])] = int(row["qty"])

        all_keys = set(seed_map.keys()) | set(all_own_deltas.keys())
        all_keys.update({("UAE", p) for p in PRODUCT_ORDER})
        all_keys.update({("KSA", p) for p in PRODUCT_ORDER})

        own_running: dict[tuple, int] = {k: seed_map.get(k, 0) for k in all_keys}
        own_by_month: dict[tuple, dict[pd.Timestamp, int]] = {}

        for mo in months_live:
            for key in all_keys:
                delta = all_own_deltas.get(key, {}).get(mo, 0)
                own_running[key] = max(0, own_running.get(key, 0) + delta)
                own_by_month.setdefault(key, {})[mo] = own_running[key]

        # Subscription active count at each month end
        rc = load_recharge_full()
        rc_machine = rc[rc["category"] == "Machine"].copy()

        for mo in months_live:
            mo_end = (mo + pd.DateOffset(months=1) - pd.DateOffset(days=1)).normalize()
            mo_end = min(mo_end, today)

            active_mask = (
                rc_machine["created_at_dt"].notna() &
                (rc_machine["created_at_dt"] <= mo_end) &
                (rc_machine["cancelled_at_dt"].isna() | (rc_machine["cancelled_at_dt"] > mo_end))
            )
            sub_agg = (
                rc_machine[active_mask]
                .groupby(["market", "product"])["quantity"]
                .sum()
            )
            # Combine subs + ownership
            for key in all_keys:
                mkt, prod = key
                sub_v = int(sub_agg.get((mkt, prod), 0))
                own_v = own_by_month.get(key, {}).get(mo, 0)
                frames.append(pd.DataFrame([{
                    "month_dt": mo,
                    "market":   mkt,
                    "product":  prod,
                    "total":    sub_v + own_v,
                }]))

    if not frames:
        return pd.DataFrame(columns=["month_dt", "market", "product", "total"])

    df = pd.concat(frames, ignore_index=True)
    return df.sort_values("month_dt").reset_index(drop=True)


def get_load_diagnostics() -> tuple[dict[str, str], float]:
    """Returns (errors_dict, total_fetch_seconds) from the last fetch."""
    _data, errors, elapsed = _fetch_all_tabs()
    return errors, elapsed
