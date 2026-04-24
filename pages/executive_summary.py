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
    a_sub = _apply_mkt(get_active_subscriptions(as_of=ts))
    a_own = _apply_mkt(get_active_ownership(as_of=ts))
    return (
        int(a_sub["qty"].sum() if a_sub is not None and not a_sub.empty else 0)
        + int(a_own["qty"].sum() if a_own is not None and not a_own.empty else 0)
    )


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
    """Total marketing spend in USD for the period (Country-aware)."""
    mkt = load_marketing_spend()
    if mkt is None or mkt.empty:
        return 0.0
    in_window = mkt[(mkt["month_dt"] >= start_ts.to_period("M").to_timestamp())
                    & (mkt["month_dt"] <= end_ts.to_period("M").to_timestamp())]
    if in_window.empty:
        return 0.0
    if mkt_filter == "UAE":
        return float(in_window["uae_usd"].fillna(0).sum())
    if mkt_filter == "KSA":
        return float(in_window["ksa_usd"].fillna(0).sum())
    if mkt_filter == "USA":
        # No USA column in the spreadsheet today.
        return 0.0
    return float(in_window["total_usd"].fillna(0).sum())


def _delta_pct(cur_val: float, prev_val: float) -> float | None:
    if prev_val == 0:
        return None if cur_val == 0 else (100.0 if cur_val > 0 else -100.0)
    return (cur_val - prev_val) / prev_val * 100


def _fmt_delta(delta: float | None) -> str:
    if delta is None:
        return "—"
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.1f}%"


# ── Row 1: Headline KPIs ──────────────────────────────────────────────────────
today_d   = date.today()
today_ts  = pd.Timestamp(today_d)
mtd_start = pd.Timestamp(today_d.replace(day=1))
# Previous full month for deltas
prev_end   = mtd_start - pd.Timedelta(days=1)
prev_start = prev_end.to_period("M").to_timestamp()

# Current-period figures
cur_user_base   = _active_users_at(today_ts)
prev_user_base  = _active_users_at(prev_end)

cur_arr   = _arr_usd_at(today_ts)
prev_arr  = _arr_usd_at(prev_end)

cur_new     = _new_sales_in(mtd_start, today_ts)
cur_churn   = _churned_in(mtd_start, today_ts)
cur_net     = cur_new - cur_churn
prev_new    = _new_sales_in(prev_start, prev_end)
prev_churn  = _churned_in(prev_start, prev_end)
prev_net    = prev_new - prev_churn

active_at_mtd_start   = _active_users_at(mtd_start)
active_at_prev_start  = _active_users_at(prev_start)
cur_churn_rate   = (cur_churn / active_at_mtd_start)  if active_at_mtd_start  > 0 else 0.0
prev_churn_rate  = (prev_churn / active_at_prev_start) if active_at_prev_start > 0 else 0.0

# CAC = marketing spend / new customers (machine sales)  — over the period
cur_spend   = _marketing_spend_in(mtd_start, today_ts)
prev_spend  = _marketing_spend_in(prev_start, prev_end)
cur_cac  = (cur_spend / cur_new)   if cur_new  > 0 else 0.0
prev_cac = (prev_spend / prev_new) if prev_new > 0 else 0.0

st.markdown("---")
k1, k2, k3, k4, k5 = st.columns(5)

k1.metric(
    "ARR (USD)",
    fmt_usd(cur_arr),
    delta=_fmt_delta(_delta_pct(cur_arr, prev_arr)),
    help="Annualised run-rate from active Machine + Filter subs today vs. prior month end.",
)
k2.metric(
    "TOTAL USER BASE",
    f"{cur_user_base:,}",
    delta=_fmt_delta(_delta_pct(cur_user_base, prev_user_base)),
    help="Active machine subs + active ownership, as of today vs. prior month end.",
)
k3.metric(
    "NET NEW CUSTOMERS · MTD",
    f"{'+' if cur_net >= 0 else ''}{cur_net:,}",
    delta=_fmt_delta(_delta_pct(cur_net, prev_net)),
    help="New machine sales MTD minus true cancellations MTD.",
)
k4.metric(
    "MONTHLY CHURN RATE",
    f"{cur_churn_rate:.2%}",
    delta=_fmt_delta(_delta_pct(cur_churn_rate, prev_churn_rate)),
    delta_color="inverse",
    help="True cancels MTD ÷ active subs at start of month.",
)
k5.metric(
    "CAC · MTD",
    fmt_usd(cur_cac) if cur_cac > 0 else "—",
    delta=_fmt_delta(_delta_pct(cur_cac, prev_cac)),
    delta_color="inverse",
    help=(
        "Blended CAC = marketing spend ÷ new machine sales over the month. "
        "USA marketing spend not yet tracked — CAC shown only when a market "
        "with spend data is selected or for UAE+KSA in 'All'."
    ),
)

# ── Row 2: ARR + User Base over time (monthly) ────────────────────────────────
st.markdown("---")
st.markdown("### Growth: ARR and User Base")

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

arr_series  = [_arr_usd_at(pd.Timestamp(ts))     for ts in measure_points]
base_series = [_active_users_at(pd.Timestamp(ts)) for ts in measure_points]
x_labels    = [ts.strftime("%b %Y") for ts in measure_points]


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
        y=base_series,
        name="User base",
        marker_color="#818cf8",
        opacity=0.55,
        yaxis="y2",
        text=[f"{v:,}" for v in base_series],
        textposition="outside",
        textfont=dict(color="#cbd5e1", size=10),
        cliponaxis=False,
        hovertemplate="%{x}<br>Users: %{y:,}<extra></extra>",
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
        title=dict(text="User base", font=dict(color="#818cf8")),
        overlaying="y", side="right",
        showgrid=False, zeroline=False,
    ),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    barmode="overlay",
)
st.plotly_chart(fig_growth, use_container_width=True)


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
