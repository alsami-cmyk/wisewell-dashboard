"""
Executive Summary — top-level "how is the business doing" page.

Three rows:
  1. Headline KPIs (scorecards with MoM deltas)
  2. ARR + User Base over time (monthly, with month-range selector)
  3. Sales (daily bar, left) + Sales by product (donut, right)
     — each with its own date-range selector

Global filter at top: Country.
Machine subscriptions only (same scope convention as Test / Test 2).
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from utils import (
    PRODUCT_COLOR,
    PRODUCT_ORDER,
    fmt_usd,
    get_active_ownership,
    get_active_subscriptions,
    get_all_machine_sales,
    get_fx,
    load_marketing_spend,
    load_marketing_spend_daily,
    load_projections,
    load_recharge_full,
)

st.markdown("## 🎯 Executive summary")

# ── Global country filter ─────────────────────────────────────────────────────
country_sel = st.selectbox(
    "Country",
    ["All", "UAE", "KSA", "USA"],
    index=0,
    key="xs_country",
)
mkt_filter = None if country_sel == "All" else country_sel


def _apply_mkt(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if mkt_filter and "market" in df.columns:
        return df[df["market"] == mkt_filter]
    return df


# ── Point-in-time helpers ─────────────────────────────────────────────────────
def _active_users_at(ts: pd.Timestamp) -> int:
    """Total user base = active machine subs + active ownership."""
    a_sub = _apply_mkt(get_active_subscriptions(as_of=ts))
    a_own = _apply_mkt(get_active_ownership(as_of=ts))
    return (
        int(a_sub["qty"].sum() if a_sub is not None and not a_sub.empty else 0)
        + int(a_own["qty"].sum() if a_own is not None and not a_own.empty else 0)
    )


def _active_machine_subs_at(ts: pd.Timestamp) -> int:
    """Active machine subs only — denominator for the churn-rate scorecard
    (matches the convention used on the Retention page).
    """
    a_sub = _apply_mkt(get_active_subscriptions(as_of=ts))
    return int(a_sub["qty"].sum() if a_sub is not None and not a_sub.empty else 0)


def _arr_usd_at(end_ts: pd.Timestamp) -> float:
    """ARR (USD) from active Machine + Filter subs at end_ts."""
    rc = load_recharge_full()
    if rc.empty:
        return 0.0
    rc = _apply_mkt(rc)
    if rc is None or rc.empty:
        return 0.0
    mask = (
        rc["category"].isin(["Machine", "Filter"])
        & rc["created_at_dt"].notna()
        & (rc["created_at_dt"] <= end_ts)
        & (rc["cancelled_at_dt"].isna() | (rc["cancelled_at_dt"] > end_ts))
    )
    active = rc.loc[mask]
    if active.empty:
        return 0.0
    freq      = active["charge_interval_frequency"].replace(0, 1).fillna(1)
    price     = active["recurring_price"].fillna(0)
    qty       = active["quantity"].fillna(0)
    arr_local = price * qty * (12.0 / freq)
    fx        = get_fx()
    currency  = active["currency"].fillna("USD")
    arr_usd   = arr_local * currency.map(lambda c: fx.get(c, 1.0))
    return float(arr_usd.sum())


def _new_sales_in(start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> int:
    """Gross new machine sales in [start, end] — includes offline deals."""
    s = _apply_mkt(get_all_machine_sales(start_dt=start_ts, end_dt=end_ts))
    return int(s["qty"].sum()) if s is not None and not s.empty else 0


def _paid_sales_in(start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> int:
    """
    Paid-attributable new sales in [start, end] — EXCLUDES offline.

    Used as the CAC denominator. Offline subscriptions/ownership are
    typically larger B2B deals not acquired through the paid-ads
    engine, so they would otherwise inflate the denominator and
    artificially deflate CAC. (Example: a 58-unit offline B2B deal
    in May 2026.)
    """
    s = _apply_mkt(get_all_machine_sales(start_dt=start_ts, end_dt=end_ts))
    if s is None or s.empty:
        return 0
    s = s[~s["is_offline"]]
    return int(s["qty"].sum()) if not s.empty else 0


# ── Market-aware helpers (used by the GCC vs USA landing-page KPIs) ──────────
# These IGNORE the global country dropdown — the new headline KPIs at the top
# of the page always show GCC (UAE + KSA) vs USA explicitly. The dropdown still
# affects everything from "Growth: ARR and Monthly Sales" downwards.

GCC_MARKETS = ["UAE", "KSA"]
USA_MARKETS = ["USA"]
ALL_MARKETS = ["UAE", "KSA", "USA"]


def _filter_markets(df: pd.DataFrame, markets: list[str]) -> pd.DataFrame:
    if df is None or df.empty or "market" not in df.columns:
        return df if df is not None else pd.DataFrame()
    return df[df["market"].isin(markets)]


def _new_sales_markets(start_ts: pd.Timestamp, end_ts: pd.Timestamp,
                       markets: list[str]) -> int:
    s = _filter_markets(get_all_machine_sales(start_dt=start_ts, end_dt=end_ts), markets)
    return int(s["qty"].sum()) if not s.empty else 0


def _paid_sales_markets(start_ts: pd.Timestamp, end_ts: pd.Timestamp,
                        markets: list[str]) -> int:
    s = _filter_markets(get_all_machine_sales(start_dt=start_ts, end_dt=end_ts), markets)
    if s.empty:
        return 0
    s = s[~s["is_offline"]]
    return int(s["qty"].sum()) if not s.empty else 0


def _churned_markets(start_ts: pd.Timestamp, end_ts: pd.Timestamp,
                     markets: list[str]) -> int:
    rc = load_recharge_full()
    if rc.empty:
        return 0
    rc = rc[(rc["category"] == "Machine") & rc["market"].isin(markets)]
    if rc.empty:
        return 0
    mask = (
        rc["is_true_cancel"]
        & rc["cancelled_at_dt"].notna()
        & (rc["cancelled_at_dt"] >= start_ts)
        & (rc["cancelled_at_dt"] <= end_ts)
    )
    return int(rc.loc[mask, "quantity"].sum())


def _arr_added_markets(start_ts: pd.Timestamp, end_ts: pd.Timestamp,
                       markets: list[str]) -> float:
    """
    Annualised value of NEW Machine + Filter subs created in [start, end].
    Doesn't subtract churn — pure additions.
    """
    rc = load_recharge_full()
    if rc.empty:
        return 0.0
    rc = rc[rc["market"].isin(markets)]
    if rc.empty:
        return 0.0
    mask = (
        rc["category"].isin(["Machine", "Filter"])
        & rc["created_at_dt"].notna()
        & (rc["created_at_dt"] >= start_ts)
        & (rc["created_at_dt"] <= end_ts)
    )
    new = rc.loc[mask]
    if new.empty:
        return 0.0
    freq      = new["charge_interval_frequency"].replace(0, 1).fillna(1)
    arr_local = new["recurring_price"].fillna(0) * new["quantity"].fillna(0) * (12.0 / freq)
    fx        = get_fx()
    currency  = new["currency"].fillna("USD")
    return float((arr_local * currency.map(lambda c: fx.get(c, 1.0))).sum())


def _spend_markets(start_ts: pd.Timestamp, end_ts: pd.Timestamp,
                   markets: list[str]) -> float:
    """
    Marketing spend in USD across one or more markets for [start, end].
    Mirrors _marketing_spend_in but sums multiple per-market columns instead
    of using the global country filter.
    """
    col_by_market = {"UAE": "uae_usd", "KSA": "ksa_usd", "USA": "usa_usd"}
    cols = [col_by_market[m] for m in markets if m in col_by_market]
    if not cols:
        return 0.0

    daily = load_marketing_spend_daily()
    daily_total = 0.0
    days_covered_by_daily: set = set()
    if daily is not None and not daily.empty:
        d = daily[(daily["date"] >= start_ts.normalize())
                  & (daily["date"] <= end_ts.normalize())]
        if not d.empty:
            daily_total = float(d[cols].sum().sum())
            days_covered_by_daily = {ts.normalize() for ts in d["date"]}

    mkt = load_marketing_spend()
    if mkt is None or mkt.empty:
        return daily_total

    month_to_spend = {}
    for ms, *vals in zip(mkt["month_dt"], *[mkt[c] for c in cols]):
        month_to_spend[ms] = sum(float(v) if pd.notna(v) else 0.0 for v in vals)

    today_norm           = pd.Timestamp.today().normalize()
    current_month_start  = today_norm.to_period("M").to_timestamp()
    days_elapsed_current = today_norm.day

    fallback = 0.0
    for day in pd.date_range(start_ts.normalize(), end_ts.normalize(), freq="D"):
        if day in days_covered_by_daily:
            continue
        month_start  = day.to_period("M").to_timestamp()
        spend_for_mo = month_to_spend.get(month_start, 0.0)
        divisor = (max(days_elapsed_current, 1) if month_start == current_month_start
                   else (month_start + pd.offsets.MonthEnd(0)).day)
        fallback += spend_for_mo / divisor
    return daily_total + fallback


def _active_users_at_markets(ts: pd.Timestamp, markets: list[str]) -> int:
    a_sub = _filter_markets(get_active_subscriptions(as_of=ts), markets)
    a_own = _filter_markets(get_active_ownership(as_of=ts),     markets)
    return (
        int(a_sub["qty"].sum() if not a_sub.empty else 0)
        + int(a_own["qty"].sum() if not a_own.empty else 0)
    )


def _arr_at_markets(ts: pd.Timestamp, markets: list[str]) -> float:
    rc = load_recharge_full()
    if rc.empty:
        return 0.0
    rc = rc[rc["market"].isin(markets)]
    if rc.empty:
        return 0.0
    mask = (
        rc["category"].isin(["Machine", "Filter"])
        & rc["created_at_dt"].notna()
        & (rc["created_at_dt"] <= ts)
        & (rc["cancelled_at_dt"].isna() | (rc["cancelled_at_dt"] > ts))
    )
    a = rc.loc[mask]
    if a.empty:
        return 0.0
    freq      = a["charge_interval_frequency"].replace(0, 1).fillna(1)
    arr_local = a["recurring_price"].fillna(0) * a["quantity"].fillna(0) * (12.0 / freq)
    fx        = get_fx()
    currency  = a["currency"].fillna("USD")
    return float((arr_local * currency.map(lambda c: fx.get(c, 1.0))).sum())


def _user_base_breakdown(ts: pd.Timestamp) -> pd.DataFrame:
    """Country × product matrix of total user base at `ts`."""
    sub = get_active_subscriptions(as_of=ts)
    own = get_active_ownership(as_of=ts)
    pieces = [p for p in (sub, own) if p is not None and not p.empty]
    if not pieces:
        return pd.DataFrame()
    combined = pd.concat(pieces, ignore_index=True)
    pivot = combined.groupby(["market", "product"], as_index=False)["qty"].sum()
    table = pivot.pivot(index="market", columns="product", values="qty").fillna(0).astype(int)
    # Order columns by canonical product order
    cols_in_order = [p for p in PRODUCT_ORDER if p in table.columns]
    table = table[cols_in_order]
    table.loc["Total"] = table.sum()
    table["Total"] = table.sum(axis=1)
    return table.reset_index().rename(columns={"market": "Market"})


def _arr_breakdown(ts: pd.Timestamp) -> pd.DataFrame:
    """Country × product matrix of ARR (USD) at `ts`."""
    rc = load_recharge_full()
    if rc.empty:
        return pd.DataFrame()
    mask = (
        rc["category"].isin(["Machine", "Filter"])
        & rc["created_at_dt"].notna()
        & (rc["created_at_dt"] <= ts)
        & (rc["cancelled_at_dt"].isna() | (rc["cancelled_at_dt"] > ts))
    )
    a = rc.loc[mask].copy()
    if a.empty:
        return pd.DataFrame()
    freq = a["charge_interval_frequency"].replace(0, 1).fillna(1)
    a["arr_usd"] = (a["recurring_price"].fillna(0) * a["quantity"].fillna(0) * (12.0 / freq))
    fx       = get_fx()
    a["arr_usd"] = a["arr_usd"] * a["currency"].fillna("USD").map(lambda c: fx.get(c, 1.0))
    pivot = a.groupby(["market", "product"], as_index=False)["arr_usd"].sum()
    table = pivot.pivot(index="market", columns="product", values="arr_usd").fillna(0)
    cols_in_order = [p for p in PRODUCT_ORDER if p in table.columns]
    table = table[cols_in_order]
    table.loc["Total"] = table.sum()
    table["Total"] = table.sum(axis=1)
    # Format as USD
    fmt = table.copy().reset_index().rename(columns={"market": "Market"})
    for c in fmt.columns:
        if c != "Market":
            fmt[c] = fmt[c].map(fmt_usd)
    return fmt


def _churned_in(start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> int:
    rc = load_recharge_full()
    if rc.empty:
        return 0
    rc_m = rc[rc["category"] == "Machine"].copy()
    rc_m = _apply_mkt(rc_m)
    if rc_m is None or rc_m.empty:
        return 0
    mask = (
        rc_m["is_true_cancel"]
        & rc_m["cancelled_at_dt"].notna()
        & (rc_m["cancelled_at_dt"] >= start_ts)
        & (rc_m["cancelled_at_dt"] <= end_ts)
    )
    return int(rc_m.loc[mask, "quantity"].sum())


def _marketing_spend_in(start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> float:
    """
    Marketing spend in USD for [start_ts, end_ts] inclusive.

    Strategy: prefer the 'Paid Ads Spend - Daily' tab (true daily actuals
    per market). Fall back to the monthly Marketing Spend tab proration
    only for days NOT yet present in the daily tab (e.g. very old history
    if the daily tab was started later, or future projections).
    """
    col_by_market = {"UAE": "uae_usd", "KSA": "ksa_usd", "USA": "usa_usd"}
    col = col_by_market.get(mkt_filter, "total_usd")

    daily = load_marketing_spend_daily()
    daily_total = 0.0
    days_covered_by_daily: set = set()
    if daily is not None and not daily.empty:
        d = daily[(daily["date"] >= start_ts.normalize())
                  & (daily["date"] <= end_ts.normalize())]
        if not d.empty:
            daily_total = float(d[col].sum())
            days_covered_by_daily = {ts.normalize() for ts in d["date"]}

    # Fallback: any days in [start, end] that were NOT in the daily tab
    # use monthly proration (legacy behaviour).
    mkt = load_marketing_spend()
    if mkt is None or mkt.empty:
        return daily_total

    month_to_spend = {
        ms: float(spend) if pd.notna(spend) else 0.0
        for ms, spend in zip(mkt["month_dt"], mkt[col])
    }
    today_norm           = pd.Timestamp.today().normalize()
    current_month_start  = today_norm.to_period("M").to_timestamp()
    days_elapsed_current = today_norm.day

    fallback = 0.0
    for day in pd.date_range(start_ts.normalize(), end_ts.normalize(), freq="D"):
        if day in days_covered_by_daily:
            continue
        month_start  = day.to_period("M").to_timestamp()
        spend_for_mo = month_to_spend.get(month_start, 0.0)
        if month_start == current_month_start:
            divisor = max(days_elapsed_current, 1)
        else:
            divisor = (month_start + pd.offsets.MonthEnd(0)).day
        fallback += spend_for_mo / divisor
    return daily_total + fallback


def _delta_pct(cur_val: float, prev_val: float) -> float | None:
    if prev_val == 0:
        return None if cur_val == 0 else (100.0 if cur_val > 0 else -100.0)
    return (cur_val - prev_val) / prev_val * 100


def _fmt_delta(delta: float | None) -> str:
    if delta is None:
        return "—"
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.1f}%"


# ── Headline KPI computations ─────────────────────────────────────────────────
today_d   = date.today()
today_ts  = pd.Timestamp(today_d)
mtd_start = pd.Timestamp(today_d.replace(day=1))

# Two prior-period windows, used for different metrics:
#
#   prev_mtd_*   — same N days of prior month (e.g. April 1-2 when MTD is
#                  May 1-2). Used for sales / spend / CAC where MTD-vs-MTD
#                  is apples-to-apples.
#
#   prev_full_*  — full prior month (April 1-30). Used for the projected
#                  monthly churn rate, which compares "if May continues at
#                  this pace, full-month churn rate" to "April's actual
#                  full-month churn rate".
days_into_month   = today_d.day
days_in_cur_month = (mtd_start + pd.offsets.MonthEnd(0)).day  # 31 for May

prev_mtd_start  = (mtd_start - pd.DateOffset(months=1))
prev_mtd_end    = prev_mtd_start + pd.Timedelta(days=days_into_month - 1)

prev_full_start = prev_mtd_start
prev_full_end   = mtd_start - pd.Timedelta(days=1)

# MTD-aligned aliases used for sales / spend / user-base / ARR comparisons
prev_start = prev_mtd_start
prev_end   = prev_mtd_end

# Today / yesterday / trailing-7d windows
yesterday_ts  = today_ts - pd.Timedelta(days=1)
t7_start      = today_ts - pd.Timedelta(days=6)
t7_end        = today_ts

# ── KPI Row A: User Base + ARR (with breakdown popovers) ────────────────────
st.markdown("---")
rA1, rA2 = st.columns(2)

cur_user_base  = _active_users_at_markets(today_ts, ALL_MARKETS)
prev_user_base = _active_users_at_markets(prev_end, ALL_MARKETS)
cur_arr        = _arr_at_markets(today_ts, ALL_MARKETS)
prev_arr       = _arr_at_markets(prev_end, ALL_MARKETS)

with rA1:
    metric_col, pop_col = st.columns([4, 1])
    metric_col.metric(
        "TOTAL USER BASE",
        f"{cur_user_base:,}",
        delta=_fmt_delta(_delta_pct(cur_user_base, prev_user_base)),
        help=f"Active machine subscribers + active ownership today, vs. same MTD-day "
             f"of prior month ({prev_end:%d %b %Y}). Click Breakdown for market × product split.",
    )
    with pop_col:
        st.write("")  # vertical alignment nudge
        with st.popover("Breakdown ▾", use_container_width=True):
            st.markdown("**User base · market × product**")
            ub = _user_base_breakdown(today_ts)
            if not ub.empty:
                st.dataframe(ub, hide_index=True, use_container_width=True)
            else:
                st.caption("No data.")

with rA2:
    metric_col, pop_col = st.columns([4, 1])
    metric_col.metric(
        "TOTAL ARR (USD)",
        fmt_usd(cur_arr),
        delta=_fmt_delta(_delta_pct(cur_arr, prev_arr)),
        help=f"Annualised run-rate from active Machine + Filter subs as of today "
             f"vs. same MTD-day in prior month ({prev_end:%d %b %Y}). Click Breakdown for market × product split.",
    )
    with pop_col:
        st.write("")
        with st.popover("Breakdown ▾", use_container_width=True):
            st.markdown("**ARR · market × product (USD)**")
            ab = _arr_breakdown(today_ts)
            if not ab.empty:
                st.dataframe(ab, hide_index=True, use_container_width=True)
            else:
                st.caption("No data.")

# ── KPI Rows B / C / D: Today's Sales | ARR Added Today | CAC MTD (Total / GCC / USA)
st.markdown("---")

# Sales today
sales_today_all = _new_sales_markets(today_ts, today_ts, ALL_MARKETS)
sales_today_gcc = _new_sales_markets(today_ts, today_ts, GCC_MARKETS)
sales_today_usa = _new_sales_markets(today_ts, today_ts, USA_MARKETS)
sales_yest_all  = _new_sales_markets(yesterday_ts, yesterday_ts, ALL_MARKETS)
sales_yest_gcc  = _new_sales_markets(yesterday_ts, yesterday_ts, GCC_MARKETS)
sales_yest_usa  = _new_sales_markets(yesterday_ts, yesterday_ts, USA_MARKETS)

rB1, rB2, rB3 = st.columns(3)
rB1.metric(
    "TOTAL SALES TODAY", f"{sales_today_all:,}",
    delta=_fmt_delta(_delta_pct(sales_today_all, sales_yest_all)),
    help="New machine sales today, gross of offline. Compared to yesterday.",
)
rB2.metric(
    "GCC SALES TODAY (UAE + KSA)", f"{sales_today_gcc:,}",
    delta=_fmt_delta(_delta_pct(sales_today_gcc, sales_yest_gcc)),
)
rB3.metric(
    "USA SALES TODAY", f"{sales_today_usa:,}",
    delta=_fmt_delta(_delta_pct(sales_today_usa, sales_yest_usa)),
)

# ARR added today (gross — no churn subtraction)
arr_today_all = _arr_added_markets(today_ts, today_ts, ALL_MARKETS)
arr_today_gcc = _arr_added_markets(today_ts, today_ts, GCC_MARKETS)
arr_today_usa = _arr_added_markets(today_ts, today_ts, USA_MARKETS)
arr_yest_all  = _arr_added_markets(yesterday_ts, yesterday_ts, ALL_MARKETS)
arr_yest_gcc  = _arr_added_markets(yesterday_ts, yesterday_ts, GCC_MARKETS)
arr_yest_usa  = _arr_added_markets(yesterday_ts, yesterday_ts, USA_MARKETS)

rC1, rC2, rC3 = st.columns(3)
rC1.metric(
    "TOTAL ARR ADDED TODAY", fmt_usd(arr_today_all),
    delta=_fmt_delta(_delta_pct(arr_today_all, arr_yest_all)),
    help="Annualised value of Machine + Filter subs CREATED today "
         "(recurring_price × qty × 12 ÷ charge_interval). Gross only — "
         "today's cancellations aren't subtracted.",
)
rC2.metric(
    "GCC ARR ADDED TODAY", fmt_usd(arr_today_gcc),
    delta=_fmt_delta(_delta_pct(arr_today_gcc, arr_yest_gcc)),
)
rC3.metric(
    "USA ARR ADDED TODAY", fmt_usd(arr_today_usa),
    delta=_fmt_delta(_delta_pct(arr_today_usa, arr_yest_usa)),
)

# CAC MTD — denominator excludes offline (B2B / direct) deals
spend_mtd_all  = _spend_markets(mtd_start, today_ts, ALL_MARKETS)
spend_mtd_gcc  = _spend_markets(mtd_start, today_ts, GCC_MARKETS)
spend_mtd_usa  = _spend_markets(mtd_start, today_ts, USA_MARKETS)
paid_mtd_all   = _paid_sales_markets(mtd_start, today_ts, ALL_MARKETS)
paid_mtd_gcc   = _paid_sales_markets(mtd_start, today_ts, GCC_MARKETS)
paid_mtd_usa   = _paid_sales_markets(mtd_start, today_ts, USA_MARKETS)
cac_mtd_all = (spend_mtd_all / paid_mtd_all) if paid_mtd_all > 0 else 0.0
cac_mtd_gcc = (spend_mtd_gcc / paid_mtd_gcc) if paid_mtd_gcc > 0 else 0.0
cac_mtd_usa = (spend_mtd_usa / paid_mtd_usa) if paid_mtd_usa > 0 else 0.0

spend_prev_all = _spend_markets(prev_start, prev_end, ALL_MARKETS)
spend_prev_gcc = _spend_markets(prev_start, prev_end, GCC_MARKETS)
spend_prev_usa = _spend_markets(prev_start, prev_end, USA_MARKETS)
paid_prev_all  = _paid_sales_markets(prev_start, prev_end, ALL_MARKETS)
paid_prev_gcc  = _paid_sales_markets(prev_start, prev_end, GCC_MARKETS)
paid_prev_usa  = _paid_sales_markets(prev_start, prev_end, USA_MARKETS)
cac_prev_all = (spend_prev_all / paid_prev_all) if paid_prev_all > 0 else 0.0
cac_prev_gcc = (spend_prev_gcc / paid_prev_gcc) if paid_prev_gcc > 0 else 0.0
cac_prev_usa = (spend_prev_usa / paid_prev_usa) if paid_prev_usa > 0 else 0.0

rD1, rD2, rD3 = st.columns(3)
rD1.metric(
    "TOTAL CAC · MTD", fmt_usd(cac_mtd_all) if cac_mtd_all > 0 else "—",
    delta=_fmt_delta(_delta_pct(cac_mtd_all, cac_prev_all)),
    delta_color="inverse",
    help=f"Paid-ad spend ÷ paid-attributable new sales for "
         f"{mtd_start:%d %b}–{today_ts:%d %b}. Offline (B2B / direct) deals "
         f"excluded from the denominator. Compared to same {days_into_month}-day "
         f"window of prior month.",
)
rD2.metric(
    "GCC CAC · MTD", fmt_usd(cac_mtd_gcc) if cac_mtd_gcc > 0 else "—",
    delta=_fmt_delta(_delta_pct(cac_mtd_gcc, cac_prev_gcc)),
    delta_color="inverse",
)
rD3.metric(
    "USA CAC · MTD", fmt_usd(cac_mtd_usa) if cac_mtd_usa > 0 else "—",
    delta=_fmt_delta(_delta_pct(cac_mtd_usa, cac_prev_usa)),
    delta_color="inverse",
)

# ── Row E: Last-7-day chart + KPI table (GCC × USA) ──────────────────────────
st.markdown("---")
st.markdown("### Trailing 7-Day Snapshot")

eL, eR = st.columns([2, 1])

with eL:
    days = list(pd.date_range(t7_start, t7_end, freq="D"))
    x_labels   = [d.strftime("%a %d %b") for d in days]
    gcc_series = [_new_sales_markets(d, d, GCC_MARKETS) for d in days]
    usa_series = [_new_sales_markets(d, d, USA_MARKETS) for d in days]

    fig_t7 = go.Figure()
    fig_t7.add_trace(go.Bar(
        x=x_labels, y=gcc_series, name="GCC (UAE + KSA)",
        marker_color="#6366f1", opacity=0.9,
        text=[f"{v:,}" for v in gcc_series], textposition="outside",
        textfont=dict(color="#cbd5e1", size=10), cliponaxis=False,
        hovertemplate="%{x}<br>GCC: %{y:,}<extra></extra>",
    ))
    fig_t7.add_trace(go.Bar(
        x=x_labels, y=usa_series, name="USA",
        marker_color="#10b981", opacity=0.9,
        text=[f"{v:,}" for v in usa_series], textposition="outside",
        textfont=dict(color="#cbd5e1", size=10), cliponaxis=False,
        hovertemplate="%{x}<br>USA: %{y:,}<extra></extra>",
    ))
    fig_t7.update_layout(
        barmode="group",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e2e8f0", size=11),
        height=340,
        margin=dict(l=10, r=10, t=20, b=30),
        xaxis=dict(showgrid=False),
        yaxis=dict(gridcolor="rgba(148,163,184,0.15)", zeroline=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig_t7, use_container_width=True)

with eR:
    def _t7_avg_sales(m): return _new_sales_markets(t7_start, t7_end, m) / 7
    def _t7_avg_churn(m): return _churned_markets(t7_start, t7_end, m) / 7
    def _t7_avg_net(m):   return (_new_sales_markets(t7_start, t7_end, m) - _churned_markets(t7_start, t7_end, m)) / 7
    def _t7_cac(m):
        spend = _spend_markets(t7_start, t7_end, m)
        paid  = _paid_sales_markets(t7_start, t7_end, m)
        return spend / paid if paid > 0 else 0.0

    cac_gcc = _t7_cac(GCC_MARKETS)
    cac_usa = _t7_cac(USA_MARKETS)
    kpi_rows = [
        ("Avg Daily Sales",        f"{_t7_avg_sales(GCC_MARKETS):.1f}", f"{_t7_avg_sales(USA_MARKETS):.1f}"),
        ("Avg Daily Cancellations",f"{_t7_avg_churn(GCC_MARKETS):.1f}", f"{_t7_avg_churn(USA_MARKETS):.1f}"),
        ("Avg Daily Net Users",    f"{_t7_avg_net(GCC_MARKETS):+.1f}",  f"{_t7_avg_net(USA_MARKETS):+.1f}"),
        ("Trailing 7-Day CAC",     fmt_usd(cac_gcc) if cac_gcc > 0 else "—",
                                   fmt_usd(cac_usa) if cac_usa > 0 else "—"),
    ]
    kpi_df = pd.DataFrame(kpi_rows, columns=["Metric", "GCC", "USA"])
    st.markdown(
        "<div style='font-size:11px; color:#94a3b8; letter-spacing:0.05em; "
        "margin:6px 0 6px 4px;'>LAST 7 DAYS · {start} – {end}</div>".format(
            start=t7_start.strftime("%d %b"), end=t7_end.strftime("%d %b"),
        ),
        unsafe_allow_html=True,
    )
    st.dataframe(kpi_df, hide_index=True, use_container_width=True, height=210)

# ── Growth: ARR + Monthly Sales over time ─────────────────────────────────────
st.markdown("---")
st.markdown("### Growth: ARR and Monthly Sales")

# Month-range pickers. Default start = Jan 2025, end = current month.
_today_month = pd.Timestamp(today_d).to_period("M").to_timestamp()
_min_month   = pd.Timestamp("2022-01-01")
_months      = pd.date_range(_min_month, _today_month, freq="MS")
_month_labels = [m.strftime("%b %Y") for m in _months]
_label_to_ts  = dict(zip(_month_labels, _months))

_default_start_label = "Jan 2025" if "Jan 2025" in _label_to_ts else _month_labels[0]
_default_end_label   = _month_labels[-1]

g1, g2, _sp = st.columns([1.6, 1.6, 4])
with g1:
    growth_start_label = st.selectbox(
        "Start month", _month_labels,
        index=_month_labels.index(_default_start_label),
        key="xs_growth_start",
    )
with g2:
    growth_end_label = st.selectbox(
        "End month", _month_labels,
        index=_month_labels.index(_default_end_label),
        key="xs_growth_end",
    )

growth_start_ts = _label_to_ts[growth_start_label]
growth_end_ts   = _label_to_ts[growth_end_label]
if growth_start_ts > growth_end_ts:
    st.error("Start month must be on or before end month.")
    st.stop()

# Measure at each month end
month_starts = pd.date_range(growth_start_ts, growth_end_ts, freq="MS")
# End-of-month timestamps (last day of each month), clamped to today
measure_points = []
for ms in month_starts:
    mend = (ms + pd.offsets.MonthEnd(0))
    measure_points.append(min(mend, pd.Timestamp(today_d)))

arr_series   = [_arr_usd_at(pd.Timestamp(ts)) for ts in measure_points]
x_labels     = [ts.strftime("%b %Y") for ts in measure_points]

# Monthly sales: new machine sales for each month in the range.
# The final month may be partial (clamped to today).
sales_series = []
for ms, mp in zip(month_starts, measure_points):
    sales_series.append(_new_sales_in(pd.Timestamp(ms), pd.Timestamp(mp)))


def _fmt_usd_compact(v: float) -> str:
    if v >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v/1_000:.1f}K"
    return f"${v:,.0f}"


fig_growth = go.Figure()
fig_growth.add_trace(
    go.Bar(
        x=x_labels,
        y=sales_series,
        name="Monthly sales",
        marker_color="#818cf8",
        opacity=0.75,
        yaxis="y2",
        text=[f"{v:,}" for v in sales_series],
        textposition="outside",
        textfont=dict(color="#cbd5e1", size=10),
        cliponaxis=False,
        hovertemplate="%{x}<br>Sales: %{y:,}<extra></extra>",
    )
)
fig_growth.add_trace(
    go.Scatter(
        x=x_labels,
        y=arr_series,
        name="ARR (USD)",
        mode="lines+markers+text",
        line=dict(color="#10b981", width=2.5),
        marker=dict(size=7, color="#10b981", line=dict(color="#1e293b", width=1)),
        text=[_fmt_usd_compact(v) for v in arr_series],
        textposition="top center",
        textfont=dict(color="#e2e8f0", size=11),
        cliponaxis=False,
        hovertemplate="%{x}<br>ARR: $%{y:,.0f}<extra></extra>",
    )
)
fig_growth.update_layout(
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#e2e8f0", size=11),
    height=420,
    margin=dict(l=10, r=10, t=20, b=30),
    xaxis=dict(showgrid=False),
    yaxis=dict(
        title=dict(text="ARR (USD)", font=dict(color="#10b981")),
        gridcolor="rgba(148,163,184,0.15)", zeroline=False,
        tickprefix="$", tickformat=",.0f",
    ),
    yaxis2=dict(
        title=dict(text="Monthly sales", font=dict(color="#818cf8")),
        overlaying="y", side="right",
        showgrid=False, zeroline=False,
    ),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    barmode="overlay",
)
st.plotly_chart(fig_growth, use_container_width=True)

# ── Efficiency: CAC and Churn Rate ────────────────────────────────────────────
st.markdown("### Efficiency: CAC and Churn Rate")
st.caption(
    "The current (incomplete) month's churn rate is **projected** to a full-month "
    "pace (MTD churns × days-in-month ÷ days-elapsed) so the trend stays comparable "
    "to prior full months."
)

cac_series        = []
churn_rate_series = []
today_ts_norm     = pd.Timestamp(today_d)
for i, (ms, mp) in enumerate(zip(month_starts, measure_points)):
    ms_ts = pd.Timestamp(ms)
    mp_ts = pd.Timestamp(mp)
    # CAC denominator excludes offline (B2B / direct) deals
    paid_sales_mo = _paid_sales_in(ms_ts, mp_ts)
    spend_mo      = _marketing_spend_in(ms_ts, mp_ts)
    cac_series.append(spend_mo / paid_sales_mo if paid_sales_mo > 0 else None)

    churned_mo      = _churned_in(ms_ts, mp_ts)
    active_subs_mo  = _active_machine_subs_at(ms_ts)
    if active_subs_mo > 0:
        # If this is the current (incomplete) month, project MTD pace to a
        # full-month rate so the trend line stays comparable to prior months.
        days_in_mo  = (ms_ts + pd.offsets.MonthEnd(0)).day
        days_so_far = (mp_ts - ms_ts).days + 1
        is_partial  = mp_ts < (ms_ts + pd.offsets.MonthEnd(0)) and mp_ts >= today_ts_norm
        if is_partial and days_so_far > 0:
            projected_churned = churned_mo * (days_in_mo / days_so_far)
            churn_rate_series.append(projected_churned / active_subs_mo)
        else:
            churn_rate_series.append(churned_mo / active_subs_mo)
    else:
        churn_rate_series.append(None)

churn_pct_series = [v * 100 if v is not None else None for v in churn_rate_series]

# Two-panel subplot: churn line on top, CAC bars on bottom — guaranteed no overlap
fig_eff = make_subplots(
    rows=2, cols=1,
    shared_xaxes=True,
    row_heights=[0.42, 0.58],
    vertical_spacing=0.10,
    subplot_titles=["Churn Rate (%)", "CAC (USD)"],
)

fig_eff.add_trace(
    go.Scatter(
        x=x_labels,
        y=churn_pct_series,
        name="Churn Rate (%)",
        mode="lines+markers+text",
        line=dict(color="#f87171", width=2.5),
        marker=dict(size=7, color="#f87171", line=dict(color="#1e293b", width=1)),
        text=[f"{v:.1f}%" if v is not None else "" for v in churn_pct_series],
        textposition="top center",
        textfont=dict(color="#fca5a5", size=10),
        cliponaxis=False,
        hovertemplate="%{x}<br>Churn rate: %{y:.2f}%<extra></extra>",
        connectgaps=True,
    ),
    row=1, col=1,
)
fig_eff.add_trace(
    go.Bar(
        x=x_labels,
        y=cac_series,
        name="CAC (USD)",
        marker_color="#f59e0b",
        opacity=0.8,
        text=[f"${v:,.0f}" if v is not None else "" for v in cac_series],
        textposition="outside",
        textfont=dict(color="#fde68a", size=10),
        cliponaxis=False,
        hovertemplate="%{x}<br>CAC: $%{y:,.0f}<extra></extra>",
    ),
    row=2, col=1,
)
fig_eff.update_layout(
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#e2e8f0", size=11),
    height=480,
    margin=dict(l=10, r=10, t=40, b=30),
    showlegend=False,
)
fig_eff.update_xaxes(showgrid=False)
fig_eff.update_yaxes(
    gridcolor="rgba(148,163,184,0.15)", zeroline=False,
    ticksuffix="%", tickformat=".1f", row=1, col=1,
)
fig_eff.update_yaxes(
    gridcolor="rgba(148,163,184,0.15)", zeroline=False,
    tickprefix="$", tickformat=",.0f", row=2, col=1,
)
for ann in fig_eff.layout.annotations:
    ann.font.color = "#94a3b8"
    ann.font.size  = 12
st.plotly_chart(fig_eff, use_container_width=True)


# ── Row 3: Sales (daily bars) + Sales by product (donut) ─────────────────────
st.markdown("---")
left_col, right_col = st.columns(2)

with left_col:
    st.markdown("### Sales over time")

    _default_bar_end   = today_d
    _default_bar_start = today_d - timedelta(days=6)  # last 7 days incl. today

    bar_range = st.date_input(
        "Date range",
        value=(_default_bar_start, _default_bar_end),
        key="xs_bar_range",
    )
    if isinstance(bar_range, (list, tuple)) and len(bar_range) == 2:
        bar_start, bar_end = bar_range
    else:
        bar_start, bar_end = _default_bar_start, _default_bar_end
    bar_start_ts = pd.Timestamp(bar_start)
    bar_end_ts   = pd.Timestamp(bar_end)

    bar_df = _apply_mkt(get_all_machine_sales(start_dt=bar_start_ts, end_dt=bar_end_ts))
    all_days = pd.date_range(bar_start_ts, bar_end_ts, freq="D")
    if bar_df is None or bar_df.empty:
        daily = pd.Series(0, index=all_days)
    else:
        daily = bar_df.groupby("date")["qty"].sum().reindex(all_days, fill_value=0)

    # Colour today's bar (partial day) slightly lighter
    colors = [
        "#bae6fd" if pd.Timestamp(d) == pd.Timestamp(today_d) else "#38bdf8"
        for d in daily.index
    ]

    fig_bar = go.Figure()
    fig_bar.add_trace(
        go.Bar(
            x=list(daily.index),
            y=list(daily.values),
            marker_color=colors,
            text=[str(int(v)) for v in daily.values],
            textposition="outside",
            textfont=dict(color="#e2e8f0", size=11),
            cliponaxis=False,
            hovertemplate="%{x|%b %d}<br>Sales: %{y}<extra></extra>",
        )
    )
    fig_bar.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e2e8f0", size=11),
        height=380,
        margin=dict(l=10, r=10, t=20, b=30),
        xaxis=dict(showgrid=False, tickformat="%d %b"),
        yaxis=dict(gridcolor="rgba(148,163,184,0.15)", zeroline=False),
        showlegend=False,
    )
    st.plotly_chart(fig_bar, use_container_width=True)

with right_col:
    st.markdown("### Sales by product")

    _default_donut_start = today_d.replace(day=1)
    _default_donut_end   = today_d

    donut_range = st.date_input(
        "Date range",
        value=(_default_donut_start, _default_donut_end),
        key="xs_donut_range",
    )
    if isinstance(donut_range, (list, tuple)) and len(donut_range) == 2:
        donut_start, donut_end = donut_range
    else:
        donut_start, donut_end = _default_donut_start, _default_donut_end
    donut_start_ts = pd.Timestamp(donut_start)
    donut_end_ts   = pd.Timestamp(donut_end)

    donut_df = _apply_mkt(get_all_machine_sales(start_dt=donut_start_ts, end_dt=donut_end_ts))
    if donut_df is None or donut_df.empty:
        st.info("No sales in the selected period.")
    else:
        by_prod = (
            donut_df.groupby("product")["qty"].sum().reindex(PRODUCT_ORDER, fill_value=0)
        )
        labels = by_prod.index.tolist()
        values = by_prod.values.tolist()
        colors = [PRODUCT_COLOR.get(p, "#64748b") for p in labels]

        fig_donut = go.Figure()
        fig_donut.add_trace(
            go.Pie(
                labels=labels,
                values=values,
                hole=0.55,
                marker=dict(colors=colors, line=dict(color="#0f172a", width=2)),
                textinfo="label+value",
                textfont=dict(color="#e2e8f0", size=12),
                hovertemplate="%{label}<br>Sales: %{value:,}<br>%{percent}<extra></extra>",
                sort=False,
            )
        )
        fig_donut.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#e2e8f0", size=11),
            height=380,
            margin=dict(l=10, r=10, t=20, b=10),
            showlegend=False,
        )
        st.plotly_chart(fig_donut, use_container_width=True)

# ── Footnote ──────────────────────────────────────────────────────────────────
st.caption(f"Country: **{country_sel}** · Machine-subscription scope · ARR includes Filter subs.")
