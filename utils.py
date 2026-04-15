"""
Shared constants, helpers, and data loaders.

Architecture: every metric is computed directly from the six live source tabs —
  Recharge-UAE, Recharge-KSA, Recharge-USA
  Shopify-UAE,  Shopify-KSA,  Shopify-USA
plus the Marketing Spend tab for CAC.
No calculated / pre-aggregated tabs are read.

Performance: all 7 tabs are fetched in parallel on first load (~2-4s instead of
~12-20s sequential). Automatic retry on transient Google API errors.
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import streamlit as st
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials

# ── Logging ───────────────────────────────────────────────────────────────────
logger = logging.getLogger("wisewell")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# ── Sheet identity ─────────────────────────────────────────────────────────────
SHEET_ID = "1NjPJKswE2rXFnXsCah5Kv4tiSEi88jlGLnZwfHsp5o4"
SCOPES   = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

ALL_SOURCE_TABS = [
    "Recharge - UAE", "Recharge - KSA", "Recharge - USA",
    "Shopify - UAE",  "Shopify - KSA",  "Shopify - USA",
    "Marketing Spend",
]

MAX_RETRIES   = 3
RETRY_BACKOFF = [1, 2, 4]   # seconds between retries

# ── Product catalogue ──────────────────────────────────────────────────────────
PRODUCT_MAP: dict[str, tuple[str, str]] = {
    "Wisewell Model 1 Subscription":              ("Machine", "Model 1"),
    "Wisewell Model 1 Subscription (with AMC)":   ("Machine", "Model 1"),
    "Wisewell Nano + Subscription":               ("Machine", "Nano+"),
    "Wisewell Nano + Subscription (with AMC)":    ("Machine", "Nano+"),
    "Wisewell Bubble Subscription":               ("Machine", "Bubble"),
    "Wisewell Bubble Subscription + Holiday Set": ("Machine", "Bubble"),
    "Wisewell Bubble Ownership + Holiday Set":    ("Machine", "Bubble"),
    "Wisewell Flat Subscription":                 ("Machine", "Flat"),
    "Wisewell Nano Subscription":                 ("Machine", "Nano Tank"),
    "Filter Subscription":                        ("Filter",  "Model 1"),
    "Filter Subscription (Model 1)":              ("Filter",  "Model 1"),
    "Filter Subscription (Nano+)":                ("Filter",  "Nano+"),
    "Wisewell Bubble Care+ Plan":                 ("Filter",  "Bubble"),
}

PRODUCT_ORDER = ["Model 1", "Nano+", "Bubble", "Flat", "Nano Tank"]

PRODUCT_COLOR: dict[str, str] = {
    "Model 1":   "#8b5cf6",
    "Nano+":     "#0ea5e9",
    "Bubble":    "#f43f5e",
    "Flat":      "#10b981",
    "Nano Tank": "#f59e0b",
    "Filter":    "#94a3b8",
    "Others":    "#94a3b8",
}

CATEGORY_COLOR = {"Machine": "#0ea5e9", "Filter": "#10b981"}
MARKET_COLOR   = {"UAE": "#6366f1",     "KSA": "#f59e0b", "USA": "#10b981"}
FX_FALLBACK    = {"AED": 1 / 3.6725,   "SAR": 1 / 3.75,  "USD": 1.0}

# Cancellation reason normalisation (raw Recharge values → display labels)
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

# Shopify unit column mappings: (product_name, ownership_col, subscription_col)
SHOPIFY_UNIT_COLS = [
    ("Model 1",   "Units - Model 1 (Own)", "Units - Model 1 (Sub)"),
    ("Nano+",     "Units - Nano+ (Own)",   "Units - Nano+ (Sub)"),
    ("Bubble",    "Units - Bubble (Own)",  "Units - Bubble (Sub)"),
    ("Flat",      "Units - Flat (Own)",    "Units - Flat (Sub)"),
    ("Nano Tank", "Units - Nano (Own)",    "Units - Nano (Sub)"),
]

# ── Shared CSS ────────────────────────────────────────────────────────────────
SHARED_CSS = """
<style>
/* KPI metric cards */
div[data-testid="metric-container"] {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 1rem 1.25rem;
}
[data-testid="stMetricValue"] {
    font-size: 1.55rem;
    font-weight: 700;
}
[data-testid="stMetricLabel"] {
    font-size: 0.78rem;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: .05em;
}
/* Dark sidebar */
section[data-testid="stSidebar"] > div:first-child {
    background-color: #0f172a !important;
}
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] small,
section[data-testid="stSidebar"] .stMarkdown {
    color: #e2e8f0 !important;
}
section[data-testid="stSidebar"] hr {
    border-color: #1e3a5f !important;
}
/* Hide Streamlit chrome */
#MainMenu, footer { visibility: hidden; }
</style>
"""

# ── Credential helpers ────────────────────────────────────────────────────────

def get_credentials():
    """
    Service account (Streamlit Cloud) or OAuth token.json (local dev).
    """
    try:
        info = json.loads(st.secrets["GOOGLE_SERVICE_ACCOUNT"])
        return service_account.Credentials.from_service_account_info(
            info, scopes=SCOPES
        )
    except (KeyError, FileNotFoundError):
        token_path = os.path.join(os.path.dirname(__file__), "token.json")
        return Credentials.from_authorized_user_file(token_path, SCOPES)


@st.cache_data(ttl=3600, show_spinner=False)
def get_fx() -> dict:
    """Live USD conversion rates (1-hour cache, fixed-peg fallback)."""
    try:
        r     = requests.get("https://open.er-api.com/v6/latest/USD", timeout=5)
        rates = r.json().get("rates", {})
        return {
            "AED": 1 / rates["AED"],
            "SAR": 1 / rates["SAR"],
            "USD": 1.0,
            "source": "live",
        }
    except Exception:
        return {**FX_FALLBACK, "source": "fallback (fixed peg)"}


def fmt_usd(v: float) -> str:
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:,.0f}"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_dates(series: pd.Series) -> pd.Series:
    """
    Try DD/MM/YYYY first (Recharge export format), then ISO 8601 fallback.
    Returns a datetime Series (UTC-naive).
    """
    s      = series.astype(str).str.strip()
    result = pd.to_datetime(s, format="%d/%m/%Y", errors="coerce")
    mask   = result.isna() & s.ne("")
    if mask.any():
        result[mask] = pd.to_datetime(s[mask], errors="coerce")
    return result


def _shopify_product_from_name(name: str) -> tuple[str | None, str]:
    """Infer (category, product) from a Shopify Lineitem name string."""
    n = name.lower()
    if "filter subscription (nano+)" in n:    return "Filter",  "Nano+"
    if "filter subscription (model 1)" in n:  return "Filter",  "Model 1"
    if "bubble care+" in n or "care+ plan" in n: return "Filter", "Bubble"
    if "filter subscription" in n:            return "Filter",  "Model 1"
    if "model 1" in n:                        return "Machine", "Model 1"
    if "nano +" in n or "nano+" in n:         return "Machine", "Nano+"
    if "bubble" in n:                         return "Machine", "Bubble"
    if "flat" in n:                           return "Machine", "Flat"
    if "nano" in n:                           return "Machine", "Nano Tank"
    return None, "Unknown"


def _rows_to_df(rows: list[list[str]]) -> pd.DataFrame:
    """Pad and convert raw Sheets rows to a DataFrame."""
    if len(rows) < 2:
        return pd.DataFrame()
    max_cols = max(len(r) for r in rows)
    padded   = [r + [""] * (max_cols - len(r)) for r in rows]
    return pd.DataFrame(padded[1:], columns=padded[0])


# ── Parallel tab fetcher ──────────────────────────────────────────────────────

def _fetch_single_tab(
    creds, tab_name: str
) -> tuple[str, list[list[str]], float, str | None]:
    """
    Fetch one tab with retry.  Returns (tab_name, rows, elapsed_sec, error_msg).
    Each thread builds its own Sheets service for thread-safety.
    """
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
            logger.info(
                "Fetched '%s': %d rows in %.2fs", tab_name, len(rows), elapsed
            )
            return tab_name, rows, elapsed, None
        except Exception as exc:
            wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            logger.warning(
                "Retry %d/%d for '%s': %s — waiting %ds",
                attempt + 1, MAX_RETRIES, tab_name, exc, wait,
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait)

    elapsed = time.perf_counter() - t0
    error   = f"Failed after {MAX_RETRIES} retries"
    logger.error("FAILED '%s' — %s (%.2fs)", tab_name, error, elapsed)
    return tab_name, [], elapsed, error


@st.cache_data(ttl=300, show_spinner="Syncing with Google Sheets…")
def _fetch_all_tabs() -> tuple[dict[str, list[list[str]]], dict[str, str], float]:
    """
    Fetch all 7 source tabs in parallel.
    Returns (data_dict, errors_dict, total_seconds).
    """
    creds   = get_credentials()
    t_start = time.perf_counter()

    results: dict[str, list[list[str]]] = {}
    errors:  dict[str, str]             = {}

    with ThreadPoolExecutor(max_workers=7) as pool:
        futures = {
            pool.submit(_fetch_single_tab, creds, tab): tab
            for tab in ALL_SOURCE_TABS
        }
        for future in as_completed(futures):
            tab_name, rows, _elapsed, err = future.result()
            results[tab_name] = rows
            if err:
                errors[tab_name] = err

    total = time.perf_counter() - t_start
    ok    = len(ALL_SOURCE_TABS) - len(errors)
    logger.info(
        "All tabs done: %d/%d OK in %.2fs (parallel)",
        ok, len(ALL_SOURCE_TABS), total,
    )
    return results, errors, total


# ── Live source-tab loaders ───────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def load_recharge_full() -> pd.DataFrame:
    """
    ALL Recharge rows (every status) from UAE + KSA + USA.

    Guaranteed columns:
      subscription_id, status, product_title,
      recurring_price, quantity, charge_interval_frequency,
      created_at_dt (datetime), cancelled_at_dt (datetime),
      is_true_cancel (bool), cancellation_reason (str),
      market, currency, category, product, arr_local (float)

    arr_local is non-zero only for ACTIVE rows.
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

    # ── Numeric cols ──────────────────────────────────────────────────────────
    for col, default in [
        ("recurring_price",           0.0),
        ("quantity",                  1.0),
        ("charge_interval_frequency", 1.0),
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(default)
        else:
            df[col] = default

    # 30-day billing cycle → treat as monthly
    df["charge_interval_frequency"] = df["charge_interval_frequency"].apply(
        lambda x: 1.0 if x == 30 else x
    )

    # ── Category / product ────────────────────────────────────────────────────
    df["category"] = df["product_title"].map(
        lambda t: PRODUCT_MAP.get(str(t), (None, None))[0]
    )
    df["product"] = df["product_title"].map(
        lambda t: PRODUCT_MAP.get(str(t), (None, None))[1]
    )

    # ── ARR (ACTIVE only) ─────────────────────────────────────────────────────
    df["arr_local"] = df.apply(
        lambda r: (
            r["recurring_price"]
            * r["quantity"]
            * (12.0 / r["charge_interval_frequency"])
        )
        if r.get("status") == "ACTIVE"
        else 0.0,
        axis=1,
    )

    # ── Date columns ─────────────────────────────────────────────────────────
    ca_col = next(
        (c for c in df.columns if c.strip().lower() == "created_at"), None
    )
    df["created_at_dt"] = (
        _parse_dates(df[ca_col]) if ca_col else pd.NaT
    )

    can_col = next(
        (c for c in df.columns if c.strip().lower() == "cancelled_at"), None
    )
    df["cancelled_at_dt"] = (
        _parse_dates(df[can_col]) if can_col else pd.NaT
    )

    # ── True-cancel flag ──────────────────────────────────────────────────────
    tc_col = next(
        (c for c in df.columns
         if "true" in c.lower() and "cancel" in c.lower()),
        None,
    )
    df["is_true_cancel"] = (
        df[tc_col].apply(lambda x: str(x).strip() == "1") if tc_col else False
    )

    # ── Cancellation reason ──────────────────────────────────────────────
    reason_col = next(
        (c for c in df.columns
         if "cancellation" in c.lower() and "reason" in c.lower()),
        None,
    )
    if reason_col:
        raw = df[reason_col].astype(str).str.strip()
        df["cancellation_reason"] = (
            raw.str.lower()
            .map(CANCELLATION_REASON_MAP)
            .fillna(raw.where(raw.ne("") & raw.ne("nan"), "Not Specified"))
        )
    else:
        df["cancellation_reason"] = "Not Specified"

    keep = [
        "subscription_id", "status", "product_title",
        "recurring_price", "quantity", "charge_interval_frequency",
        "created_at_dt", "cancelled_at_dt", "is_true_cancel",
        "cancellation_reason",
        "market", "currency", "category", "product", "arr_local",
    ]
    return df[[c for c in keep if c in df.columns]]


@st.cache_data(ttl=300, show_spinner=False)
def load_shopify_all() -> pd.DataFrame:
    """
    All Shopify orders from UAE + KSA + USA.

    Columns: country, date, product, category, qty, is_ownership
      is_ownership = True when the order came from an ownership (Units-Own) column.
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    frames = []

    for tab_name, country in [
        ("Shopify - UAE", "UAE"),
        ("Shopify - KSA", "KSA"),
        ("Shopify - USA", "USA"),
    ]:
        rows = raw_data.get(tab_name, [])
        if len(rows) < 2:
            continue
        headers = [h.strip() for h in rows[0]]
        if headers and headers[0] in ("", " "):
            headers[0] = "Order ID"
        n      = len(headers)
        padded = [r[:n] + [""] * max(0, n - len(r)) for r in rows[1:]]
        df     = pd.DataFrame(padded, columns=headers)

        df["date"] = pd.to_datetime(
            df.get("Created at", ""), errors="coerce"
        ).dt.normalize()

        records: list[tuple] = []
        for _, row in df.iterrows():
            product, category, qty, is_own = "Unknown", None, 0, False

            # Primary: named unit columns (machine products)
            for prod, own_col, sub_col in SHOPIFY_UNIT_COLS:
                try:    own_v = int(str(row.get(own_col, 0) or 0))
                except: own_v = 0
                try:    sub_v = int(str(row.get(sub_col, 0) or 0))
                except: sub_v = 0
                if own_v > 0 or sub_v > 0:
                    product  = prod
                    category = "Machine"
                    qty      = max(own_v, sub_v)
                    is_own   = own_v > 0
                    break

            # Fallback: parse Lineitem name (catches filter subs etc.)
            if product == "Unknown":
                lineitem = str(row.get("Lineitem name", ""))
                category, product = _shopify_product_from_name(lineitem)
                if product != "Unknown":
                    try:    qty = max(1, int(str(row.get("Lineitem quantity", 1) or 1)))
                    except: qty = 1
                    is_own = False

            records.append((product, category, qty, is_own))

        if not records:
            continue
        prods, cats, qtys, owns = zip(*records)
        df["product"]      = prods
        df["category"]     = cats
        df["qty"]          = qtys
        df["is_ownership"] = owns
        df["country"]      = country
        df = df[df["product"] != "Unknown"].copy()
        frames.append(df[["country", "date", "product", "category", "qty", "is_ownership"]])

    if not frames:
        return pd.DataFrame(
            columns=["country", "date", "product", "category", "qty", "is_ownership"]
        )
    return pd.concat(frames, ignore_index=True)


@st.cache_data(ttl=300, show_spinner=False)
def load_marketing_spend() -> pd.DataFrame:
    """
    Marketing Spend tab → monthly spend by country (USD).

    Columns: month_dt, total_usd, uae_usd, ksa_usd
    Values assumed to be USD (Meta/Google ad platforms billed in USD).
    """
    raw_data, _errors, _elapsed = _fetch_all_tabs()
    rows = raw_data.get("Marketing Spend", [])
    df   = _rows_to_df(rows)

    empty = pd.DataFrame(columns=["month_dt", "total_usd", "uae_usd", "ksa_usd"])
    if df.empty:
        return empty

    # Month column (first column, e.g. "Jan-25")
    month_col    = df.columns[0]
    df["month_dt"] = pd.to_datetime(
        df[month_col].astype(str).str.strip(), format="%b-%y", errors="coerce"
    )
    # Fallback: apostrophe separator ("Jan'25")
    mask = df["month_dt"].isna() & df[month_col].astype(str).str.strip().ne("")
    if mask.any():
        df.loc[mask, "month_dt"] = pd.to_datetime(
            df.loc[mask, month_col].astype(str).str.replace("'", "-"),
            format="%b-%y",
            errors="coerce",
        )
    df = df[df["month_dt"].notna()].copy()
    if df.empty:
        return empty

    def _spend(label: str) -> pd.Series:
        col = next(
            (c for c in df.columns if c.strip().lower() == label.lower()), None
        )
        if col is None:
            return pd.Series([0.0] * len(df), index=df.index)
        return pd.to_numeric(
            df[col].astype(str).str.replace(r"[$,\s]", "", regex=True),
            errors="coerce",
        ).fillna(0.0)

    df["total_usd"] = _spend("Total Spend")
    df["uae_usd"]   = _spend("UAE")
    df["ksa_usd"]   = _spend("KSA")

    return (
        df[["month_dt", "total_usd", "uae_usd", "ksa_usd"]]
        .sort_values("month_dt")
        .reset_index(drop=True)
    )


def get_load_diagnostics() -> tuple[dict[str, str], float]:
    """
    Returns (errors_dict, total_fetch_seconds) from the last _fetch_all_tabs() call.
    Call AFTER the loaders have run (they trigger _fetch_all_tabs if cache is cold).
    """
    _data, errors, elapsed = _fetch_all_tabs()
    return errors, elapsed
