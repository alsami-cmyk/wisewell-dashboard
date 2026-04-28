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

    The Marketing Spend tab is monthly-grained, so to get an accurate
    figure for sub-month windows (MTD, trailing 7 days, etc.) we
    prorate each month's spend by the number of days from the window
    that fall inside it.
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

    total = 0.0
    days = pd.date_range(start_ts.normalize(), end_ts.normalize(), freq="D")
    for day in days:
        month_start    = day.to_period("M").to_timestamp()
        days_in_month  = (month_start + pd.offsets.MonthEnd(0)).day
        spend_for_mo   = month_to_spend.get(month_start, 0.0)
        total         += spend_for_mo / days_in_month
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
# Previous full month for deltas
prev_end   = mtd_start - pd.Timedelta(days=1)
prev_start = prev_end.to_period("M").to_timestamp()

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
# matching the convention on the Retention page (was previously
# subs + ownership, which inflated the denominator and showed a
# lower rate than the Retention page).
active_subs_mtd_start   = _active_machine_subs_at(mtd_start)
active_subs_prev_start  = _active_machine_subs_at(prev_start)
cur_churn_rate   = (cur_churn / active_subs_mtd_start)  if active_subs_mtd_start  > 0 else 0.0
prev_churn_rate  = (prev_churn / active_subs_prev_start) if active_subs_prev_start > 0 else 0.0

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
    help="Annualised run-rate from active Machine + Filter subs today vs. prior month end.",
)
k3.metric(
    "TOTAL USER BASE",
    f"{cur_user_base:,}",
    delta=_fmt_delta(_delta_pct(cur_user_base, prev_user_base)),
    help="Active machine subs + active ownership, as of today vs. prior month end.",
)
k4.metric(
    "MONTHLY CHURN RATE",
    f"{cur_churn_rate:.2%}",
    delta=_fmt_delta(_delta_pct(cur_churn_rate, prev_churn_rate)),
    delta_color="inverse",
    help="True machine cancels MTD ÷ active machine subs at start of month. "
         "(Matches the Retention page denominator — machine subs only.)",
)
k5.metric(
    "CAC · MTD",
    fmt_usd(cur_cac) if cur_cac > 0 else "—",
    delta=_fmt_delta(_delta_pct(cur_cac, prev_cac)),
    delta_color="inverse",
    help=(
        "Blended CAC = marketing spend ÷ new machine sales over the month. "
        "Per-market spend pulled from Marketing Spend tab (UAE / KSA / USA columns)."
    ),
)

# ── MTD sales vs. target meter ────────────────────────────────────────────────
_TARGETS = {"UAE": 780, "USA": 50, "KSA": 0, "All": 780 + 50}  # KSA target TBC
_target = _TARGETS.get(country_sel, 0)

if _target > 0:
    _pct = cur_new / _target * 100 if _target else 0
    _bar_color = "#10b981" if _pct >= 100 else "#818cf8"
    fig_meter = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=cur_new,
            number=dict(
                suffix=f" / {_target:,}",
                font=dict(size=26, color="#e2e8f0"),
            ),
            title=dict(
                text=(
                    f"<b>MTD SALES VS. TARGET · {country_sel.upper()}</b>"
                    f"<br><span style='font-size:12px;color:#94a3b8'>"
                    f"{_pct:.0f}% of {_target:,} target</span>"
                ),
                font=dict(size=13, color="#cbd5e1"),
            ),
            gauge=dict(
                axis=dict(
                    range=[0, max(_target * 1.1, cur_new * 1.05)],
                    tickcolor="#475569",
                    tickfont=dict(color="#94a3b8", size=10),
                ),
                bar=dict(color=_bar_color, thickness=0.7),
                bgcolor="rgba(30,41,59,0.6)",
                borderwidth=0,
                steps=[
                    dict(range=[0, _target * 0.5], color="rgba(239,68,68,0.20)"),
                    dict(range=[_target * 0.5, _target * 0.8], color="rgba(245,158,11,0.20)"),
                    dict(range=[_target * 0.8, _target], color="rgba(16,185,129,0.20)"),
                ],
                threshold=dict(
                    line=dict(color="#e2e8f0", width=3),
                    thickness=0.85,
                    value=_target,
                ),
            ),
        )
    )
    fig_meter.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        height=230,
        margin=dict(l=30, r=30, t=70, b=10),
    )
    st.plotly_chart(fig_meter, use_container_width=True)
else:
    st.caption(
        f"ℹ️ No MTD sales target set for **{country_sel}**. "
        f"Current MTD sales: **{cur_new:,}**."
    )

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

cac_series        = []
churn_rate_series = []
for i, (ms, mp) in enumerate(zip(month_starts, measure_points)):
    ms_ts = pd.Timestamp(ms)
    mp_ts = pd.Timestamp(mp)
    sales_mo  = sales_series[i]
    spend_mo  = _marketing_spend_in(ms_ts, mp_ts)
    cac_series.append(spend_mo / sales_mo if sales_mo > 0 else None)

    churned_mo      = _churned_in(ms_ts, mp_ts)
    active_subs_mo  = _active_machine_subs_at(ms_ts)
    churn_rate_series.append(
        churned_mo / active_subs_mo if active_subs_mo > 0 else None
    )

churn_pct_series = [v * 100 if v is not None else None for v in churn_rate_series]

_cac_max   = max((v for v in cac_series       if v is not None), default=1)
_churn_max = max((v for v in churn_pct_series  if v is not None), default=1)

fig_eff = go.Figure()
fig_eff.add_trace(
    go.Bar(
        x=x_labels,
        y=cac_series,
        name="CAC (USD)",
        marker_color="#f59e0b",
        opacity=0.75,
        text=[f"${v:,.0f}" if v is not None else "" for v in cac_series],
        textposition="outside",
        textfont=dict(color="#fde68a", size=10),
        cliponaxis=False,
        hovertemplate="%{x}<br>CAC: $%{y:,.0f}<extra></extra>",
    )
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
        yaxis="y2",
        hovertemplate="%{x}<br>Churn rate: %{y:.2f}%<extra></extra>",
        connectgaps=True,
    )
)
fig_eff.update_layout(
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#e2e8f0", size=11),
    height=420,
    margin=dict(l=10, r=10, t=30, b=30),
    xaxis=dict(showgrid=False),
    # y1 range stretched to 2.5× max so bars sit in the bottom ~40%
    yaxis=dict(
        title=dict(text="CAC (USD)", font=dict(color="#f59e0b")),
        gridcolor="rgba(148,163,184,0.15)", zeroline=False,
        tickprefix="$", tickformat=",.0f",
        range=[0, _cac_max * 2.5],
    ),
    # y2 range normal (1.3×) so churn line floats in the upper portion
    yaxis2=dict(
        title=dict(text="Churn Rate (%)", font=dict(color="#f87171")),
        overlaying="y", side="right",
        showgrid=False, zeroline=False,
        ticksuffix="%", tickformat=".1f",
        range=[0, _churn_max * 1.3],
    ),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    barmode="overlay",
)
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
