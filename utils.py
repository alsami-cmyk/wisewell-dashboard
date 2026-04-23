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
    "Marketing Spend",
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
    Parse date strings supporting both d/m/yyyy (Recharge) and yyyy-mm-dd (ISO).
    Returns UTC-naive datetime series.
    """
    s      = series.astype(str).str.strip()
    result = pd.to_datetime(s, format="%d/%m/%Y", errors="coerce")
    mask   = result.isna() & s.ne("") & s.ne("nan")
    if mask.any():
        result[mask] = pd.to_datetime(s[mask], errors="coerce")
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
    if re.search(r"model\s*1.*subscription", tl):
        return "Machine", "Model 1"
    if re.search(r"nano\s*\+\s*subscription", tl):
        return "Machine", "Nano+"
    if re.search(r"bubble.*subscription", tl) or "bubble ownership + holiday" in tl:
        return "Machine", "Bubble"
    if re.search(r"wisewell\s*flat\s*subscription", tl) and "filter" not in tl:
        return "Machine", "Flat"
    if tl == "wisewell nano subscription":
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

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)

    # Drop DELETED subscriptions
    if "status" in df.columns:
        df = df[df["status"].str.upper() != "DELETED"].copy()

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
    #   cancelled_at is set  AND  reason NOT in swap/convert group
    has_cancelled = df["cancelled_at_dt"].notna()
    reason_col = next(
        (c for c in df.columns if "cancellation" in c.lower() and "reason" in c.lower()
         and "comment" not in c.lower()), None
    )
    raw_reason = df[reason_col].astype(str).str.strip() if reason_col else pd.Series("", index=df.index)
    is_swap = raw_reason.str.lower().str.contains(
        r"swapped|purchased|converted|swap|max", regex=True, na=False
    )
    df["is_true_cancel"] = has_cancelled & ~is_swap

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
        "product_title", "recurring_price", "quantity",
        "charge_interval_frequency", "created_at_dt", "cancelled_at_dt",
        "is_true_cancel", "cancellation_reason",
        "market", "currency", "category", "product", "arr_local",
    ]
    return df[[c for c in keep if c in df.columns]].copy()


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

    for tab_name, market in [
        ("Shopify - UAE", "UAE"),
        ("Shopify - KSA", "KSA"),
        ("Shopify - USA", "USA"),
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
    Marketing Spend tab → monthly spend in USD.
    Returns: month_dt, total_usd, uae_usd, ksa_usd
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    rows = raw_data.get("Marketing Spend", [])
    df   = _rows_to_df(rows)
    empty = pd.DataFrame(columns=["month_dt", "total_usd", "uae_usd", "ksa_usd"])
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

    df["total_usd"] = _spend("Total Spend")
    df["uae_usd"]   = _spend("UAE")
    df["ksa_usd"]   = _spend("KSA")

    return df[["month_dt", "total_usd", "uae_usd", "ksa_usd"]].sort_values("month_dt").reset_index(drop=True)


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


@st.cache_data(ttl=3600, show_spinner=False)
def load_shopify_store_analytics() -> pd.DataFrame:
    """
    Online store performance metrics (MTD) from all configured Shopify stores.

    Always available (all plans, requires read_orders scope):
        orders (int)          — Shopify orders placed MTD
        revenue_local (float) — gross revenue in local currency
        aov_local (float)     — average order value in local currency

    Requires Shopify Advanced / Plus + read_analytics scope (ShopifyQL):
        sessions (int)          — store sessions MTD
        conversion_rate (float) — sessions → orders rate

    Returns columns:
        market, orders, revenue_local, aov_local, sessions, conversion_rate,
        configured, missing_scopes (list[str]), sessions_unavailable_reason (str)
    """
    creds = _shopify_creds()
    if not creds:
        return pd.DataFrame(columns=[
            "market", "orders", "revenue_local", "aov_local",
            "sessions", "conversion_rate", "configured",
            "missing_scopes", "sessions_unavailable_reason",
        ])

    today       = pd.Timestamp.today()
    m_start     = today.replace(day=1)
    m_start_str = m_start.strftime("%Y-%m-%dT00:00:00")
    today_str   = today.strftime("%Y-%m-%dT23:59:59")
    since_ql    = m_start.strftime("%Y-%m-%d")
    until_ql    = today.strftime("%Y-%m-%d")

    records = []
    for market, store, token in creds:
        rec: dict = {
            "market": market, "orders": 0, "revenue_local": 0.0, "aov_local": 0.0,
            "sessions": None, "conversion_rate": None, "configured": True,
            "missing_scopes": [], "sessions_unavailable_reason": "",
        }

        # ── Check granted scopes first ────────────────────────────────────────
        try:
            scope_resp   = _shopify_rest(store, token, "access_scopes.json")
            granted      = {s.get("handle", "") for s in scope_resp.get("access_scopes", [])}
            missing      = []
            if "read_orders" not in granted:
                missing.append("read_orders")
            if "read_analytics" not in granted:
                missing.append("read_analytics")
            rec["missing_scopes"] = missing
            if missing:
                logger.warning(
                    "Shopify %s token missing scopes: %s. "
                    "Re-install the custom app after adding these scopes.",
                    market, missing,
                )
        except Exception as scope_err:
            logger.info("Could not fetch scopes for %s: %s", market, scope_err)

        try:
            # ── Orders count MTD ──────────────────────────────────────────────
            cnt = _shopify_rest(store, token, "orders/count.json", {
                "status": "any",
                "created_at_min": m_start_str,
                "created_at_max": today_str,
            })
            # Shopify returns {"errors":...} (HTTP 200) when scope is missing
            if "errors" in cnt:
                logger.warning("Shopify orders/count for %s returned errors: %s", market, cnt["errors"])
            else:
                rec["orders"] = int(cnt.get("count", 0))

            # ── Revenue + AOV (first 250 orders; enough for monthly view) ─────
            ords = _shopify_rest(store, token, "orders.json", {
                "status": "any",
                "created_at_min": m_start_str,
                "created_at_max": today_str,
                "fields": "id,total_price",
                "limit": 250,
            })
            order_list       = ords.get("orders", [])
            total_rev        = sum(float(o.get("total_price", 0)) for o in order_list)
            rec["revenue_local"] = total_rev
            rec["aov_local"]     = total_rev / len(order_list) if order_list else 0.0

            # ── Sessions + conversion via ShopifyQL (Advanced / Plus only) ────
            try:
                ql_query = (
                    "{ analyticsReport(query: "
                    '"SHOW sessions, conversion_rate FROM sessions '
                    f"SINCE {since_ql} UNTIL {until_ql}"
                    '") { result { headers rowData } } }'
                )
                gql    = _shopify_graphql(store, token, ql_query)
                # Surface any GraphQL errors
                gql_errors = gql.get("errors") or []
                if gql_errors:
                    reason = "; ".join(
                        e.get("message", str(e)) for e in gql_errors
                    )
                    rec["sessions_unavailable_reason"] = reason
                    logger.info("ShopifyQL errors for %s: %s", market, reason)
                else:
                    result = (
                        gql.get("data", {})
                        .get("analyticsReport", {})
                        .get("result", {})
                    )
                    headers  = result.get("headers", [])
                    row_data = result.get("rowData", [])
                    if headers and row_data:
                        h   = {v.lower(): i for i, v in enumerate(headers)}
                        row = row_data[0]
                        if "sessions" in h:
                            rec["sessions"] = int(float(row[h["sessions"]]))
                        if "conversion_rate" in h:
                            cr = float(row[h["conversion_rate"]])
                            # Shopify returns e.g. 3.2 (meaning 3.2%) — normalise to 0–1
                            rec["conversion_rate"] = cr / 100 if cr > 1 else cr
                    else:
                        rec["sessions_unavailable_reason"] = "No data returned by ShopifyQL"
            except Exception as gql_err:
                reason = str(gql_err)
                rec["sessions_unavailable_reason"] = reason
                logger.info(
                    "ShopifyQL sessions not available for %s (%s) — order data only.",
                    market, reason,
                )

        except Exception as exc:
            logger.warning("Shopify REST failed for %s: %s", market, exc)
            rec["configured"] = False

        records.append(rec)

    return pd.DataFrame(records)


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

    Returns: date, market, product, is_ownership (bool), qty (int)

    Sources:
      Subscriptions: Recharge created_at_dt × quantity (Machine category)
                   + Offline - Subscriptions
      Ownership:    Shopify ownership unit columns × literal value
                   + Offline - Ownership
    """
    records = []
    _sd = start_dt or LIVE_DATA_START
    _ed = end_dt or pd.Timestamp.today().normalize()

    # ── Recharge subscriptions ────────────────────────────────────────────────
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
            False,
            int(row["quantity"]),
        ))

    # ── Offline subscriptions ─────────────────────────────────────────────────
    off_sub = load_offline_subscriptions()
    for _, row in off_sub[(off_sub["date"] >= _sd) & (off_sub["date"] <= _ed)].iterrows():
        records.append((row["date"], row["market"], row["product"], False, int(row["qty"])))

    # ── Shopify ownership ─────────────────────────────────────────────────────
    shop_own = load_shopify_ownership()
    for _, row in shop_own[(shop_own["date"] >= _sd) & (shop_own["date"] <= _ed)].iterrows():
        records.append((row["date"], row["market"], row["product"], True, int(row["qty"])))

    # ── Offline ownership ─────────────────────────────────────────────────────
    off_own = load_offline_ownership()
    for _, row in off_own[(off_own["date"] >= _sd) & (off_own["date"] <= _ed)].iterrows():
        records.append((row["date"], row["market"], row["product"], True, int(row["qty"])))

    if not records:
        return pd.DataFrame(columns=["date", "market", "product", "is_ownership", "qty"])
    return pd.DataFrame(records, columns=["date", "market", "product", "is_ownership", "qty"])


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


@st.cache_data(ttl=300, show_spinner=False)
def get_active_subscriptions(
    as_of: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """
    Active machine subscribers at a given point in time.
    Computable for any date since Recharge holds full history.

    Returns: market, product, qty (int)
    One row per (market, product). Use .sum() for totals.
    """
    as_of = (as_of or pd.Timestamp.today()).normalize()
    rc    = load_recharge_full()

    mask = (
        (rc["category"] == "Machine") &
        (rc["created_at_dt"].notna()) &
        (rc["created_at_dt"] <= as_of) &
        (rc["cancelled_at_dt"].isna() | (rc["cancelled_at_dt"] > as_of))
    )
    grouped = (
        rc[mask]
        .groupby(["market", "product"], as_index=False)["quantity"]
        .sum()
        .rename(columns={"quantity": "qty"})
    )
    return grouped


@st.cache_data(ttl=300, show_spinner=False)
def get_active_ownership(
    as_of: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """
    Active ownership users at a given point in time.

    Formula: seed (Aug-2025 from Monthly User Base)
           + Shopify ownership sales (Sep-2025 → as_of)
           + Offline ownership (Sep-2025 → as_of)
           − Returns (Sep-2025 → as_of)

    Returns: market, product, qty (int)  (qty can be 0 but not negative)
    """
    as_of = (as_of or pd.Timestamp.today()).normalize()

    # Seed
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
