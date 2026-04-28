"""
Cohort Analysis — subscription retention heatmap.

Rows = cohort month (signup month).
Columns = M0 … M11 (months elapsed since signup).
Cell value = % of cohort still active at the end of that month offset.

Scope: Machine subscriptions · true cancellations only (swaps excluded).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils import PRODUCT_ORDER, load_recharge_full

# ── Page header ───────────────────────────────────────────────────────────────
st.markdown("## 📈 Cohort analysis")
st.caption("How many of each month's new subscriptions are still active after N months.")

# ── Filter bar ────────────────────────────────────────────────────────────────
today_d = date.today()

# Build a month/year list spanning the past 36 months up to the current month.
# User picks from a dropdown in "MMM YYYY" format — no full date picker needed.
_today_month = pd.Timestamp(today_d).to_period("M").to_timestamp()
_months      = pd.date_range(
    _today_month - pd.DateOffset(months=35),
    _today_month,
    freq="MS",
)
_month_labels = [m.strftime("%b %Y") for m in _months]
_label_to_ts  = dict(zip(_month_labels, _months))

# Defaults: last 12 cohort months
default_end_idx   = len(_month_labels) - 1
default_start_idx = max(0, default_end_idx - 11)

c1, c2, c3, c4 = st.columns([2.2, 2.2, 1.8, 1.4])

with c1:
    start_label = st.selectbox(
        "First cohort month",
        _month_labels,
        index=default_start_idx,
        key="co_start_month",
    )
with c2:
    end_label = st.selectbox(
        "Last cohort month",
        _month_labels,
        index=default_end_idx,
        key="co_end_month",
    )
with c3:
    product_sel = st.selectbox(
        "Product", ["All"] + PRODUCT_ORDER, key="co_product",
    )
with c4:
    country_sel = st.selectbox(
        "Region", ["All", "UAE", "KSA", "USA"], key="co_country",
    )

start_month_ts = _label_to_ts[start_label]
end_month_ts   = _label_to_ts[end_label]
if start_month_ts > end_month_ts:
    st.error("First cohort month must be on or before the last cohort month.")
    st.stop()

mkt_filter  = None if country_sel == "All" else country_sel
prod_filter = None if product_sel == "All" else product_sel

# Number of "Month K" columns to show. Dynamically sized so the oldest
# cohort in the selected range gets to fill its entire timeline up to
# today. Always at least 12 columns so very-recent ranges still get a
# full first-year view.
_today_ts   = pd.Timestamp(today_d).normalize()
_months_old = (
    (_today_ts.year  - start_month_ts.year) * 12
    + (_today_ts.month - start_month_ts.month)
    + 1
)
MAX_MONTHS = max(12, int(_months_old))


# ── Build cohort matrix ───────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def _cohort_matrix(
    start_month_ts: pd.Timestamp,
    end_month_ts:   pd.Timestamp,
    mkt_filter:     str | None,
    prod_filter:    str | None,
    max_months:     int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (retention_pct_df, size_series).

    retention_pct_df: index = cohort month, columns = m0..m{max-1}, values = 0–100 or NaN
    size_series: index = cohort month, value = cohort size (subscribers)
    """
    rc = load_recharge_full()
    if rc.empty:
        return pd.DataFrame(), pd.Series(dtype=int)

    rc = rc[rc["category"] == "Machine"].copy()
    rc = rc.dropna(subset=["created_at_dt"])
    if mkt_filter:
        rc = rc[rc["market"] == mkt_filter]
    if prod_filter:
        rc = rc[rc["product"] == prod_filter]
    if rc.empty:
        return pd.DataFrame(), pd.Series(dtype=int)

    rc["cohort_month"] = rc["created_at_dt"].dt.to_period("M").dt.to_timestamp()
    rc = rc[(rc["cohort_month"] >= start_month_ts) & (rc["cohort_month"] <= end_month_ts)]
    if rc.empty:
        return pd.DataFrame(), pd.Series(dtype=int)

    today_ts = pd.Timestamp.today().normalize()

    cohort_months = pd.date_range(start_month_ts, end_month_ts, freq="MS")
    out_rows = []
    sizes    = []
    for cm in cohort_months:
        cohort = rc[rc["cohort_month"] == cm]
        size   = int(cohort["quantity"].sum())
        sizes.append((cm, size))
        if size == 0:
            out_rows.append((cm, [np.nan] * max_months))
            continue

        pct_row = []
        for k in range(max_months):
            # End of (cohort_month + k)  — last day of that offset month
            k_end = (cm + pd.DateOffset(months=k + 1)) - pd.Timedelta(days=1)
            if k_end > today_ts:
                pct_row.append(np.nan)
                continue
            # Active at k_end = true-cancel-only: cancelled_at > k_end or null
            # Swap cancels count as retained (still a customer, just on a different product).
            active_mask = (
                cohort["cancelled_at_dt"].isna()
                | (~cohort["is_true_cancel"])
                | (cohort["cancelled_at_dt"] > k_end)
            )
            active = int(cohort.loc[active_mask, "quantity"].sum())
            pct_row.append(active / size * 100 if size else np.nan)
        out_rows.append((cm, pct_row))

    df = pd.DataFrame(
        [r[1] for r in out_rows],
        index=[r[0] for r in out_rows],
        columns=[f"m{i}" for i in range(max_months)],
    )
    size_s = pd.Series({cm: sz for cm, sz in sizes})
    return df, size_s


retention_df, size_series = _cohort_matrix(
    start_month_ts, end_month_ts, mkt_filter, prod_filter, MAX_MONTHS
)

if retention_df.empty:
    st.info("No cohort data for the selected filters.")
    st.stop()

# ── Display toggle ────────────────────────────────────────────────────────────
view_col, _ = st.columns([2, 6])
with view_col:
    view = st.radio(
        "View",
        ["Retention %", "Retention count"],
        horizontal=True,
        key="co_view",
        label_visibility="collapsed",
    )

# ── Heatmap ───────────────────────────────────────────────────────────────────
cohort_labels = [cm.strftime("%b %Y") for cm in retention_df.index]
month_cols    = [f"Month {i}" for i in range(MAX_MONTHS)]

if view == "Retention count":
    # Convert % back to counts using each cohort's size
    z_vals = retention_df.values * (size_series.values[:, None] / 100)
    text_vals = np.where(
        np.isnan(retention_df.values),
        "",
        np.round(z_vals).astype("Int64").astype(str) if False else
        np.vectorize(lambda v: "" if np.isnan(v) else f"{int(round(v))}")(z_vals),
    )
    colorscale = [[0, "#1e1b4b"], [0.5, "#6366f1"], [1, "#c7d2fe"]]
else:
    z_vals = retention_df.values
    text_vals = np.vectorize(
        lambda v: "" if np.isnan(v) else f"{v:.0f}%"
    )(z_vals)
    colorscale = [
        [0.00, "#f1f5f9"],
        [0.50, "#a5b4fc"],
        [0.80, "#6366f1"],
        [1.00, "#312e81"],
    ]

fig_heat = go.Figure(
    data=go.Heatmap(
        z=z_vals,
        x=month_cols,
        y=cohort_labels,
        text=text_vals,
        texttemplate="%{text}",
        textfont=dict(color="white", size=11),
        colorscale=colorscale,
        showscale=True,
        hovertemplate="Cohort %{y}<br>%{x}<br>Value: %{text}<extra></extra>",
        xgap=2, ygap=2,
    )
)
fig_heat.update_layout(
    title=dict(
        text="<b>SUBSCRIPTION COHORT RETENTION BY MONTH</b>",
        font=dict(size=12, color="#94a3b8"), x=0.01, xanchor="left",
    ),
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#e2e8f0", size=11),
    height=max(420, 36 * len(cohort_labels) + 120),
    margin=dict(l=120, r=20, t=55, b=30),
    xaxis=dict(side="top", showgrid=False),
    yaxis=dict(showgrid=False, autorange="reversed"),
)
st.plotly_chart(fig_heat, use_container_width=True)

# ── Cohort size reference table ───────────────────────────────────────────────
with st.expander("Cohort sizes", expanded=False):
    size_df = size_series.rename("Subscribers").to_frame()
    size_df.index = [cm.strftime("%b %Y") for cm in size_df.index]
    size_df.index.name = "Cohort"
    st.dataframe(size_df, use_container_width=True)

# ── Weighted-average retention curve ──────────────────────────────────────────
weighted = []
for col in retention_df.columns:
    vals   = retention_df[col].dropna()
    weights = size_series.loc[vals.index]
    if weights.sum() > 0:
        weighted.append(float((vals * weights).sum() / weights.sum()))
    else:
        weighted.append(np.nan)

fig_avg = go.Figure()
fig_avg.add_trace(
    go.Scatter(
        x=month_cols,
        y=weighted,
        mode="lines+markers+text",
        line=dict(color="#6366f1", width=2.5),
        marker=dict(size=8, color="#6366f1", line=dict(color="#1e293b", width=1)),
        text=[f"{v:.0f}%" if v is not None and not (isinstance(v, float) and np.isnan(v)) else ""
              for v in weighted],
        textposition="top center",
        textfont=dict(color="#e2e8f0", size=11),
        cliponaxis=False,
        hovertemplate="%{x}<br>Retention: %{y:.1f}%<extra></extra>",
    )
)
fig_avg.update_layout(
    title=dict(
        text="<b>WEIGHTED AVERAGE RETENTION CURVE</b>",
        font=dict(size=12, color="#94a3b8"), x=0.01, xanchor="left",
    ),
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#e2e8f0", size=11),
    height=320,
    margin=dict(l=10, r=10, t=45, b=30),
    xaxis=dict(showgrid=False),
    yaxis=dict(
        gridcolor="rgba(148,163,184,0.15)", zeroline=False,
        ticksuffix="%", range=[0, 105],
    ),
    showlegend=False,
)
st.plotly_chart(fig_avg, use_container_width=True)

# ── Footnote ──────────────────────────────────────────────────────────────────
st.caption(
    f"Cohorts: **{start_month_ts.strftime('%b %Y')} – {end_month_ts.strftime('%b %Y')}** · "
    f"Product: **{product_sel}** · Region: **{country_sel}** · "
    "Retention excludes swaps/conversions (counted as retained)."
)
