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
    s = _apply_mkt(get_all_machine_sales(start_dt=start_ts, end_dt=end_ts))
    return int(s["qty"].sum()) if s is not None and not s.empty else 0


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
    Day-prorated marketing spend in USD for [start_ts, end_ts] inclusive.

    For PAST months: the row holds the final monthly total — divide by
    days-in-month so each day gets an equal share.

    For the CURRENT month: the row holds cumulative actuals through the
    last Meta sync (rebuilt 2x/day). It is NOT a full-month projection.
    Dividing by days-in-month would heavily under-count MTD spend.
    Instead, divide by days elapsed so far in the month — each day's
    allocated spend equals the average per-day actual.
    """
    mkt = load_marketing_spend()
    if mkt is None or mkt.empty:
        return 0.0

    col_by_market = {
        "UAE": "uae_usd", "KSA": "ksa_usd", "USA": "usa_usd",
    }
    col = col_by_market.get(mkt_filter, "total_usd")

    month_to_spend = {
        ms: float(spend) if pd.notna(spend) else 0.0
        for ms, spend in zip(mkt["month_dt"], mkt[col])
    }

    today_norm           = pd.Timestamp.today().normalize()
    current_month_start  = today_norm.to_period("M").to_timestamp()
    days_elapsed_current = today_norm.day  # 1-indexed: May 2 → 2

    total = 0.0
    days = pd.date_range(start_ts.normalize(), end_ts.normalize(), freq="D")
    for day in days:
        month_start  = day.to_period("M").to_timestamp()
        spend_for_mo = month_to_spend.get(month_start, 0.0)
        if month_start == current_month_start:
            divisor = max(days_elapsed_current, 1)
        else:
            divisor = (month_start + pd.offsets.MonthEnd(0)).day
        total += spend_for_mo / divisor
    return total


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

# Today / yesterday for the "Today's Sales" scorecard
yesterday_ts = today_ts - pd.Timedelta(days=1)

# Trailing 7-day windows (inclusive of today for the current window)
t7_start          = today_ts - pd.Timedelta(days=6)
t7_end            = today_ts
t7_prev_start     = today_ts - pd.Timedelta(days=13)
t7_prev_end       = today_ts - pd.Timedelta(days=7)

# Current-period figures
cur_user_base   = _active_users_at(today_ts)
prev_user_base  = _active_users_at(prev_end)

cur_arr   = _arr_usd_at(today_ts)
prev_arr  = _arr_usd_at(prev_end)

cur_new     = _new_sales_in(mtd_start, today_ts)
cur_churn   = _churned_in(mtd_start, today_ts)
prev_new    = _new_sales_in(prev_start, prev_end)
prev_churn  = _churned_in(prev_start, prev_end)

# Today / yesterday sales (for "Today's Sales" scorecard)
today_sales       = _new_sales_in(today_ts, today_ts)
yesterday_sales   = _new_sales_in(yesterday_ts, yesterday_ts)

# Trailing 7d figures
t7_sales           = _new_sales_in(t7_start, t7_end)
t7_prev_sales      = _new_sales_in(t7_prev_start, t7_prev_end)
t7_churn           = _churned_in(t7_start, t7_end)
t7_prev_churn      = _churned_in(t7_prev_start, t7_prev_end)
t7_net             = t7_sales - t7_churn
t7_prev_net        = t7_prev_sales - t7_prev_churn
t7_spend           = _marketing_spend_in(t7_start, t7_end)
t7_prev_spend      = _marketing_spend_in(t7_prev_start, t7_prev_end)
t7_cac             = (t7_spend / t7_sales)            if t7_sales      > 0 else 0.0
t7_prev_cac        = (t7_prev_spend / t7_prev_sales)  if t7_prev_sales > 0 else 0.0

# Monthly churn rate — denominator is ACTIVE MACHINE SUBS only,
# matching the convention on the Retention page.
#
# CURRENT month: project MTD churn to a full-month rate based on pace so far.
#   projected_full_month_churn = mtd_churn × (days_in_month / days_into_month)
#   projected_rate             = projected_full_month_churn / active_at_month_start
#
# PRIOR month: actual full-month churn rate (April 1-30 churn / April 1 base).
active_subs_mtd_start        = _active_machine_subs_at(mtd_start)
active_subs_prev_full_start  = _active_machine_subs_at(prev_full_start)

prev_full_churn = _churned_in(prev_full_start, prev_full_end)

if active_subs_mtd_start > 0 and days_into_month > 0:
    projected_full_churn = cur_churn * (days_in_cur_month / days_into_month)
    cur_churn_rate       = projected_full_churn / active_subs_mtd_start
else:
    cur_churn_rate = 0.0

prev_churn_rate = (prev_full_churn / active_subs_prev_full_start) \
                  if active_subs_prev_full_start > 0 else 0.0

# CAC MTD
cur_spend   = _marketing_spend_in(mtd_start, today_ts)
prev_spend  = _marketing_spend_in(prev_start, prev_end)
cur_cac  = (cur_spend / cur_new)   if cur_new  > 0 else 0.0
prev_cac = (prev_spend / prev_new) if prev_new > 0 else 0.0

# ── Row 1: Headline KPIs ──────────────────────────────────────────────────────
st.markdown("---")
k1, k2, k3, k4, k5 = st.columns(5)

k1.metric(
    "TODAY'S SALES",
    f"{today_sales:,}",
    delta=_fmt_delta(_delta_pct(today_sales, yesterday_sales)),
    help="New machine sales today (vs. yesterday). Today is partial — "
         "expect this to grow throughout the day.",
)
k2.metric(
    "ARR (USD)",
    fmt_usd(cur_arr),
    delta=_fmt_delta(_delta_pct(cur_arr, prev_arr)),
    help=f"Annualised run-rate from active Machine + Filter subs as of today "
         f"vs. same MTD-day in prior month ({prev_end:%d %b %Y}).",
)
k3.metric(
    "TOTAL USER BASE",
    f"{cur_user_base:,}",
    delta=_fmt_delta(_delta_pct(cur_user_base, prev_user_base)),
    help=f"Active machine subs + active ownership today vs. same MTD-day "
         f"in prior month ({prev_end:%d %b %Y}).",
)
k4.metric(
    "PROJECTED MONTHLY CHURN",
    f"{cur_churn_rate:.2%}",
    delta=_fmt_delta(_delta_pct(cur_churn_rate, prev_churn_rate)),
    delta_color="inverse",
    help=(
        f"Pro-rata projection: MTD churns ({cur_churn:,} between "
        f"{mtd_start:%d %b} and {today_ts:%d %b}) scaled to full-month pace "
        f"({days_in_cur_month}/{days_into_month}× = "
        f"~{cur_churn * days_in_cur_month / max(days_into_month,1):.0f} projected), "
        f"then divided by active machine subs at month start. "
        f"Comparison: actual full-month churn rate for "
        f"{prev_full_start:%b %Y} ({prev_full_churn:,} cancels)."
    ),
)
k5.metric(
    "CAC · MTD",
    fmt_usd(cur_cac) if cur_cac > 0 else "—",
    delta=_fmt_delta(_delta_pct(cur_cac, prev_cac)),
    delta_color="inverse",
    help=(
        f"Blended CAC = marketing spend ÷ new machine sales for "
        f"{mtd_start:%d %b}–{today_ts:%d %b}. Compared to same {days_into_month}-day "
        f"window of prior month ({prev_start:%d %b}–{prev_end:%d %b}). "
        f"Per-market spend pulled from Marketing Spend tab (UAE / KSA / USA columns)."
    ),
)

# ── MTD sales vs. projections ─────────────────────────────────────────────────
_proj = load_projections()
_proj_key = mtd_start.strftime("%Y-%m-%d")
_proj_month = _proj.get(_proj_key)

if _proj_month is None:
    st.caption(
        f"ℹ️ No projection found for **{mtd_start:%b %Y}** in the Projections tab. "
        f"Current MTD sales: **{cur_new:,}**."
    )
else:
    # Target value depends on country filter
    if country_sel == "All":
        _target       = _proj_month["global"]
        _proj_market_split = _proj_month["by_market"]   # absolute counts
    else:
        _target       = _proj_month["by_market"].get(country_sel, 0)
        _proj_market_split = {country_sel: _target}

    # Pace-aware projection: where does the current month land if the daily pace continues?
    _days_so_far    = days_into_month
    _days_in_month  = days_in_cur_month
    _projected_eom  = (cur_new / _days_so_far * _days_in_month) if _days_so_far > 0 else 0
    _pct_of_target  = (cur_new / _target * 100) if _target > 0 else 0
    _proj_pct       = (_projected_eom / _target * 100) if _target > 0 else 0

    # Linear pacing: at day N, you should be N/days_in_month of target.
    _expected_so_far = _target * (_days_so_far / _days_in_month)
    _pace_delta      = cur_new - _expected_so_far

    # On-track / behind status
    if _projected_eom >= _target * 0.98:
        _status_label = "✅ ON TRACK"
        _status_color = "#10b981"
    elif _projected_eom >= _target * 0.85:
        _status_label = "⚠️ SLIGHTLY BEHIND"
        _status_color = "#f59e0b"
    else:
        _status_label = "🔴 BEHIND TARGET"
        _status_color = "#ef4444"

    # Layout: progress bar on left (~65%), market split on right (~35%)
    pcol_left, pcol_right = st.columns([2.0, 1.0])

    with pcol_left:
        # Progress bar — capped at 100% for the fill, but actual % is shown
        _fill_pct = min(_pct_of_target, 100)
        _bar_color = "#10b981" if _pct_of_target >= 100 else (
                     "#22c55e" if _pace_delta >= 0 else
                     "#f59e0b" if _projected_eom >= _target * 0.85 else "#ef4444")

        st.markdown(
            f"""
            <div style="padding:14px 16px; background:rgba(30,41,59,0.5); border-radius:12px;
                        border:1px solid rgba(71,85,105,0.4);">
              <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:8px;">
                <div style="font-size:12px; color:#94a3b8; letter-spacing:0.05em;">
                  {country_sel.upper()} TARGET · {mtd_start.strftime('%b %Y').upper()}
                </div>
                <div style="font-size:11px; color:{_status_color}; font-weight:600;">
                  {_status_label}
                </div>
              </div>
              <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:6px;">
                <div style="font-size:24px; font-weight:600; color:#e2e8f0;">
                  {cur_new:,} <span style="font-size:14px; color:#64748b;">/ {_target:,}</span>
                </div>
                <div style="font-size:14px; color:#cbd5e1;">
                  <strong>{_pct_of_target:.0f}%</strong> of target
                </div>
              </div>
              <div style="position:relative; height:14px; background:rgba(15,23,42,0.6);
                          border-radius:7px; overflow:hidden;">
                <div style="position:absolute; left:0; top:0; height:100%;
                            width:{_fill_pct:.1f}%; background:{_bar_color};
                            border-radius:7px;"></div>
                <!-- Pace indicator: where you should be by today, linearly -->
                <div style="position:absolute; left:{(_days_so_far/_days_in_month)*100:.1f}%;
                            top:-2px; height:18px; width:2px; background:#e2e8f0;
                            opacity:0.5;" title="Linear pace marker"></div>
              </div>
              <div style="display:flex; justify-content:space-between; margin-top:8px;
                          font-size:11px; color:#94a3b8;">
                <span>Pace: <strong style="color:{'#22c55e' if _pace_delta >= 0 else '#ef4444'}">
                  {'+' if _pace_delta >= 0 else ''}{_pace_delta:.0f}
                </strong> vs linear ({_expected_so_far:,.0f} expected by today)</span>
                <span>Projected EOM: <strong style="color:#cbd5e1">{_projected_eom:,.0f}</strong>
                  ({'+' if _projected_eom >= _target else ''}{(_projected_eom - _target):,.0f} vs target)</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with pcol_right:
        # Market-split delta: actual MTD vs projected breakdown
        if country_sel == "All":
            split_rows = []
            sales_df = get_all_machine_sales(start_dt=mtd_start, end_dt=today_ts)
            for mkt in ("UAE", "KSA", "USA"):
                actual_mkt = (
                    int(sales_df[sales_df["market"] == mkt]["qty"].sum())
                    if not sales_df.empty else 0
                )
                proj_mkt = _proj_month["by_market"].get(mkt, 0)
                expected_mkt = proj_mkt * (_days_so_far / _days_in_month)
                delta_pp = (actual_mkt - expected_mkt)
                actual_pct = (actual_mkt / cur_new * 100) if cur_new > 0 else 0
                proj_pct   = _proj_month["by_market_pct"].get(mkt, 0) * 100
                split_rows.append({
                    "Market":      mkt,
                    "MTD":         f"{actual_mkt:,}",
                    "Pace Δ":      f"{'+' if delta_pp >= 0 else ''}{delta_pp:.0f}",
                    "Mix %":       f"{actual_pct:.0f}%",
                    "Proj Mix %":  f"{proj_pct:.0f}%",
                })
            _split_title = "MARKET MIX"
            split_df = pd.DataFrame(split_rows)
        else:
            # Show product split for the selected market
            prod_key = f"by_{country_sel.lower()}_product"
            proj_products = _proj_month.get(prod_key, {})
            proj_total    = sum(proj_products.values()) or 1
            sales_df = get_all_machine_sales(start_dt=mtd_start, end_dt=today_ts)
            sales_df = sales_df[sales_df["market"] == country_sel] if not sales_df.empty else sales_df
            split_rows = []
            for prod, proj_qty in proj_products.items():
                if proj_qty == 0 and (sales_df.empty or sales_df[sales_df["product"] == prod]["qty"].sum() == 0):
                    continue
                actual_qty = int(sales_df[sales_df["product"] == prod]["qty"].sum()) if not sales_df.empty else 0
                expected = proj_qty * (_days_so_far / _days_in_month)
                delta = actual_qty - expected
                actual_pct = (actual_qty / cur_new * 100) if cur_new > 0 else 0
                proj_pct   = (proj_qty / proj_total * 100)
                split_rows.append({
                    "Product":    prod,
                    "MTD":        f"{actual_qty:,}",
                    "Pace Δ":     f"{'+' if delta >= 0 else ''}{delta:.0f}",
                    "Mix %":      f"{actual_pct:.0f}%",
                    "Proj Mix %": f"{proj_pct:.0f}%",
                })
            _split_title = f"{country_sel} PRODUCT MIX"
            split_df = pd.DataFrame(split_rows)

        st.markdown(
            f"<div style='font-size:11px; color:#94a3b8; letter-spacing:0.05em; "
            f"margin:0 0 6px 4px;'>{_split_title}</div>",
            unsafe_allow_html=True,
        )
        if not split_df.empty:
            st.dataframe(split_df, hide_index=True, use_container_width=True, height=180)

# ── Trailing 7-Day Analysis ───────────────────────────────────────────────────
st.markdown("---")
st.markdown("### Trailing 7-Day Analysis")

# Daily averages — first three cards report per-day means (totals ÷ 7).
t7_sales_avg       = t7_sales       / 7
t7_prev_sales_avg  = t7_prev_sales  / 7
t7_churn_avg       = t7_churn       / 7
t7_prev_churn_avg  = t7_prev_churn  / 7
t7_net_avg         = t7_net         / 7
t7_prev_net_avg    = t7_prev_net    / 7

t1, t2, t3, t4 = st.columns(4)

t1.metric(
    "AVG DAILY SALES (T7D)",
    f"{t7_sales_avg:.1f}",
    delta=_fmt_delta(_delta_pct(t7_sales_avg, t7_prev_sales_avg)),
    help=f"Average new machine sales per day, {t7_start.strftime('%b %d')} – "
         f"{t7_end.strftime('%b %d')} (total ÷ 7) vs. previous 7-day window.",
)
t2.metric(
    "AVG DAILY CANCELLATIONS (T7D)",
    f"{t7_churn_avg:.1f}",
    delta=_fmt_delta(_delta_pct(t7_churn_avg, t7_prev_churn_avg)),
    delta_color="inverse",
    help=f"Average true machine cancels per day, {t7_start.strftime('%b %d')} – "
         f"{t7_end.strftime('%b %d')} (total ÷ 7) vs. previous 7-day window.",
)
t3.metric(
    "AVG DAILY NET USERS (T7D)",
    f"{'+' if t7_net_avg >= 0 else ''}{t7_net_avg:.1f}",
    delta=_fmt_delta(_delta_pct(t7_net_avg, t7_prev_net_avg)),
    help="Average net users added per day = (T7d sales − T7d cancellations) ÷ 7, "
         "vs. previous 7-day window.",
)
t4.metric(
    "TRAILING 7D CAC",
    fmt_usd(t7_cac) if t7_cac > 0 else "—",
    delta=_fmt_delta(_delta_pct(t7_cac, t7_prev_cac)),
    delta_color="inverse",
    help="Marketing spend (day-prorated) ÷ new machine sales over the trailing 7 days. "
         "Spend prorates each month's total by day count.",
)

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
    sales_mo  = sales_series[i]
    spend_mo  = _marketing_spend_in(ms_ts, mp_ts)
    cac_series.append(spend_mo / sales_mo if sales_mo > 0 else None)

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
