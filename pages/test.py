"""
Test page — Recharge-style Subscriptions Overview layout.

Independent filters (not tied to the global sidebar):
  • Date range
  • Comparison date range (defaults to prior equal-length window)
  • Display granularity (Daily / Monthly / Quarterly)
  • Product
  • Country
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils import (
    PRODUCT_ORDER,
    fmt_usd,
    get_active_ownership,
    get_active_subscriptions,
    get_all_machine_sales,
    get_fx,
    load_recharge_full,
)

# ── Page header ───────────────────────────────────────────────────────────────
st.markdown("## 📊 Subscriptions overview")
st.caption("Recharge-style layout · independent filters (not linked to sidebar).")

# ── Inline filter bar ─────────────────────────────────────────────────────────
today_d       = date.today()
default_start = today_d.replace(day=1)

c1, c2, c3, c4, c5 = st.columns([2.4, 2.4, 1.4, 1.8, 1.4])

with c1:
    date_range = st.date_input(
        "Date",
        value=(default_start, today_d),
        key="t_daterange",
    )

# Resolve the selected window (Streamlit returns a tuple mid-edit that can be length 1)
if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    p_start, p_end = date_range
else:
    p_start, p_end = default_start, today_d

period_days       = max(1, (p_end - p_start).days)
cmp_end_default   = p_start - timedelta(days=1)
cmp_start_default = cmp_end_default - timedelta(days=period_days)

with c2:
    cmp_range = st.date_input(
        "Compare to",
        value=(cmp_start_default, cmp_end_default),
        key="t_cmp_range",
    )

with c3:
    granularity = st.selectbox(
        "Display", ["Daily", "Monthly", "Quarterly"], index=0, key="t_gran",
    )

with c4:
    product_sel = st.selectbox(
        "Product", ["All"] + PRODUCT_ORDER, key="t_product",
    )

with c5:
    country_sel = st.selectbox(
        "Country", ["All", "UAE", "KSA", "USA"], key="t_country",
    )

if isinstance(cmp_range, (list, tuple)) and len(cmp_range) == 2:
    cmp_start, cmp_end = cmp_range
else:
    cmp_start, cmp_end = cmp_start_default, cmp_end_default

p_start_ts   = pd.Timestamp(p_start)
p_end_ts     = pd.Timestamp(p_end)
cmp_start_ts = pd.Timestamp(cmp_start)
cmp_end_ts   = pd.Timestamp(cmp_end)

mkt_filter  = None if country_sel == "All" else country_sel
prod_filter = None if product_sel == "All" else product_sel


# ── Helpers ───────────────────────────────────────────────────────────────────
def _apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    """Apply market / product filters to any df that has those columns."""
    if df is None or df.empty:
        return df
    out = df
    if mkt_filter and "market" in out.columns:
        out = out[out["market"] == mkt_filter]
    if prod_filter and "product" in out.columns:
        out = out[out["product"] == prod_filter]
    return out


def _point_in_time_arr_usd(end_ts: pd.Timestamp) -> float:
    """ARR (USD) from ACTIVE machine + filter subscribers at end_ts.

    Re-computes ARR from recurring_price × quantity × (12 / freq_months) so
    the answer is correct for any historical as-of date (the cached arr_local
    column reflects *current* status only and is Machine-only).

    Includes both Machine and Filter category subscriptions — ownership
    purchases are excluded (one-time revenue, not recurring).
    """
    rc = load_recharge_full()
    if rc.empty:
        return 0.0
    rc_f = _apply_filters(rc)
    if rc_f.empty:
        return 0.0
    mask = (
        rc_f["category"].isin(["Machine", "Filter"])
        & rc_f["created_at_dt"].notna()
        & (rc_f["created_at_dt"] <= end_ts)
        & (rc_f["cancelled_at_dt"].isna() | (rc_f["cancelled_at_dt"] > end_ts))
    )
    active = rc_f.loc[mask]
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


def _period_metrics(start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> dict:
    """Compute scorecard metrics for the given window."""
    # Total user base at end_ts  (active subs + active ownership)
    a_sub = _apply_filters(get_active_subscriptions(as_of=end_ts))
    a_own = _apply_filters(get_active_ownership(as_of=end_ts))
    user_base = int(a_sub["qty"].sum() if not a_sub.empty else 0) \
              + int(a_own["qty"].sum() if not a_own.empty else 0)

    # ARR (USD) at end_ts — active machine subs only
    arr_usd = _point_in_time_arr_usd(end_ts)

    # New gross sales within [start, end]
    sales = _apply_filters(get_all_machine_sales(start_dt=start_ts, end_dt=end_ts))
    new_sales = int(sales["qty"].sum()) if not sales.empty else 0

    # Churned customers (true cancels) within [start, end]
    rc = load_recharge_full()
    rc_m = rc[rc["category"] == "Machine"].copy() if not rc.empty else rc
    rc_m = _apply_filters(rc_m)
    if rc_m.empty:
        churned = 0
    else:
        mask = (
            rc_m["is_true_cancel"]
            & rc_m["cancelled_at_dt"].notna()
            & (rc_m["cancelled_at_dt"] >= start_ts)
            & (rc_m["cancelled_at_dt"] <= end_ts)
        )
        churned = int(rc_m.loc[mask, "quantity"].sum())

    return {
        "user_base": user_base,
        "arr_usd":   arr_usd,
        "new_sales": new_sales,
        "churned":   churned,
        "net":       new_sales - churned,
    }


def _delta_pct(cur_val: float, prev_val: float) -> float | None:
    if prev_val == 0:
        return None if cur_val == 0 else (100.0 if cur_val > 0 else -100.0)
    return (cur_val - prev_val) / prev_val * 100


def _fmt_delta(delta: float | None) -> str:
    if delta is None:
        return "—"
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.1f}%"


# ── Compute current + comparison periods ──────────────────────────────────────
cur_m  = _period_metrics(p_start_ts,   p_end_ts)
prev_m = _period_metrics(cmp_start_ts, cmp_end_ts)

# ── Scorecards ────────────────────────────────────────────────────────────────
st.markdown("---")
k1, k2, k3, k4, k5 = st.columns(5)

k1.metric(
    "TOTAL USER BASE",
    f"{cur_m['user_base']:,}",
    delta=_fmt_delta(_delta_pct(cur_m["user_base"], prev_m["user_base"])),
)
k2.metric(
    "ARR (USD)",
    fmt_usd(cur_m["arr_usd"]),
    delta=_fmt_delta(_delta_pct(cur_m["arr_usd"], prev_m["arr_usd"])),
    help="Annualised run-rate from ACTIVE machine + filter subs at end of "
         "window (recurring_price × quantity × 12 / billing-interval months). "
         "Converted to USD at current FX. Ownership purchases excluded.",
)
k3.metric(
    "NEW GROSS SALES",
    f"{cur_m['new_sales']:,}",
    delta=_fmt_delta(_delta_pct(cur_m["new_sales"], prev_m["new_sales"])),
)
k4.metric(
    "CHURNED CUSTOMERS",
    f"{cur_m['churned']:,}",
    delta=_fmt_delta(_delta_pct(cur_m["churned"], prev_m["churned"])),
    delta_color="inverse",  # red = more churn is bad, green = less churn is good
)
net_prefix = "+" if cur_m["net"] >= 0 else ""
k5.metric(
    "NET GAIN/LOSS",
    f"{net_prefix}{cur_m['net']:,}",
    delta=_fmt_delta(_delta_pct(cur_m["net"], prev_m["net"])),
)

# ── Build period buckets for charts ───────────────────────────────────────────
#   Daily     → each calendar day
#   Monthly   → each month-end inside the window
#   Quarterly → each quarter-end inside the window
if granularity == "Daily":
    bucket_dates = pd.date_range(p_start_ts, p_end_ts, freq="D")
    sales_period_freq = "D"
elif granularity == "Monthly":
    bucket_dates = pd.date_range(p_start_ts, p_end_ts, freq="ME")
    if len(bucket_dates) == 0 or bucket_dates[-1] < p_end_ts:
        bucket_dates = pd.DatetimeIndex(list(bucket_dates) + [p_end_ts])
    sales_period_freq = "M"
else:  # Quarterly
    bucket_dates = pd.date_range(p_start_ts, p_end_ts, freq="QE")
    if len(bucket_dates) == 0 or bucket_dates[-1] < p_end_ts:
        bucket_dates = pd.DatetimeIndex(list(bucket_dates) + [p_end_ts])
    sales_period_freq = "Q"

# ── Chart 1: Gross sales over time (bar) ──────────────────────────────────────
st.markdown("---")

sales_df = _apply_filters(get_all_machine_sales(start_dt=p_start_ts, end_dt=p_end_ts))
if sales_df is None or sales_df.empty:
    sales_series = pd.Series(dtype=int)
else:
    sales_df = sales_df.copy()
    sales_df["bucket"] = (
        sales_df["date"].dt.to_period(sales_period_freq).dt.to_timestamp()
    )
    sales_series = sales_df.groupby("bucket")["qty"].sum().sort_index()

fig_sales = go.Figure()
fig_sales.add_trace(
    go.Bar(
        x=list(sales_series.index),
        y=list(sales_series.values),
        marker_color="#818cf8",
        text=[f"{int(v):,}" for v in sales_series.values],
        textposition="outside",
        textfont=dict(color="#e2e8f0", size=11),
        cliponaxis=False,
        hovertemplate="%{x|%b %d, %Y}<br>Sales: %{y:,}<extra></extra>",
    )
)
fig_sales.update_layout(
    title=dict(
        text=f"<b>GROSS SALES OVER TIME · {granularity.upper()}</b>",
        font=dict(size=12, color="#94a3b8"),
        x=0.01, xanchor="left",
    ),
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#e2e8f0", size=11),
    height=320,
    margin=dict(l=10, r=10, t=45, b=30),
    xaxis=dict(showgrid=False),
    yaxis=dict(gridcolor="rgba(148,163,184,0.15)", zeroline=False),
    showlegend=False,
)
st.plotly_chart(fig_sales, use_container_width=True)

# ── Chart 2: User base over time (line) ───────────────────────────────────────
base_values = []
for ts in bucket_dates:
    a_sub = _apply_filters(get_active_subscriptions(as_of=ts))
    a_own = _apply_filters(get_active_ownership(as_of=ts))
    total = int(a_sub["qty"].sum() if a_sub is not None and not a_sub.empty else 0) \
          + int(a_own["qty"].sum() if a_own is not None and not a_own.empty else 0)
    base_values.append(total)

fig_base = go.Figure()
fig_base.add_trace(
    go.Scatter(
        x=list(bucket_dates),
        y=base_values,
        mode="lines+markers+text",
        line=dict(color="#818cf8", width=2.5),
        marker=dict(color="#818cf8", size=6,
                    line=dict(color="#1e293b", width=1)),
        text=[f"{int(v):,}" for v in base_values],
        textposition="top center",
        textfont=dict(color="#e2e8f0", size=11),
        cliponaxis=False,
        hovertemplate="%{x|%b %d, %Y}<br>Users: %{y:,}<extra></extra>",
    )
)
fig_base.update_layout(
    title=dict(
        text=f"<b>USER BASE OVER TIME · {granularity.upper()}</b>",
        font=dict(size=12, color="#94a3b8"),
        x=0.01, xanchor="left",
    ),
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#e2e8f0", size=11),
    height=320,
    margin=dict(l=10, r=10, t=45, b=30),
    xaxis=dict(showgrid=False),
    yaxis=dict(gridcolor="rgba(148,163,184,0.15)", zeroline=False),
    showlegend=False,
)
st.plotly_chart(fig_base, use_container_width=True)

# ── Footnote ──────────────────────────────────────────────────────────────────
st.caption(
    f"Window: **{p_start.strftime('%b %d, %Y')} – {p_end.strftime('%b %d, %Y')}** "
    f"vs **{cmp_start.strftime('%b %d, %Y')} – {cmp_end.strftime('%b %d, %Y')}**  ·  "
    f"Display: **{granularity}**  ·  "
    f"Product: **{product_sel}**  ·  Country: **{country_sel}**"
)
