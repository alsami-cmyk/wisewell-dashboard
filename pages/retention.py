"""
Retention Dashboard — cancellation analysis, reason breakdown, cohort retention.
All data from Recharge source tabs (no Shopify needed).

Cancellation rate uses the Recharge definition:
  churned subscribers ÷ average daily active subscribers over the period.
"""

import calendar
from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
from dateutil.relativedelta import relativedelta

from utils import (
    PRODUCT_COLOR, PRODUCT_ORDER,
    load_recharge_full, get_fx, get_load_diagnostics,
)

# ── Read sidebar filter state ─────────────────────────────────────────────────
country_sel = st.session_state.get("s_country", "All")
product_sel = st.session_state.get("s_product", "All")

# Sidebar date range for charts (monthly trend, cohort heatmap)
raw_range = st.session_state.get("s_daterange", (date(2025, 1, 1), date.today()))
if isinstance(raw_range, (list, tuple)) and len(raw_range) == 2:
    chart_start = pd.Timestamp(raw_range[0])
    chart_end   = pd.Timestamp(raw_range[1])
else:
    chart_start = pd.Timestamp(date(2025, 1, 1))
    chart_end   = pd.Timestamp(date.today())

# ── Load data ─────────────────────────────────────────────────────────────────
try:
    rc_full = load_recharge_full()
    errors, fetch_time = get_load_diagnostics()
    if errors:
        for tab, msg in errors.items():
            if "Recharge" in tab:
                st.warning(f"⚠️  Could not load **{tab}**: {msg}")
except Exception as exc:
    st.error(f"**Data load failed.** Try refreshing.\n\n`{exc}`")
    st.stop()

if rc_full.empty:
    st.warning("No Recharge data available.")
    st.stop()

# ── Apply country + product filters ──────────────────────────────────────────
rc = rc_full.copy()
if country_sel != "All":
    rc = rc[rc["market"] == country_sel]
if product_sel != "All":
    rc = rc[rc["product"] == product_sel]

rc_machine = rc[rc["category"] == "Machine"].copy()

# ── Date helpers ──────────────────────────────────────────────────────────────
today        = pd.Timestamp.today().normalize()
month_start  = today.replace(day=1)
prev_m_start = (month_start - timedelta(days=1)).replace(day=1)
days_elapsed = today.day

# ── Header ────────────────────────────────────────────────────────────────────
parts    = [p for p in [
    country_sel if country_sel != "All" else "",
    product_sel if product_sel != "All" else "",
] if p]
subtitle = " · ".join(parts) if parts else "Global · All Products"

col_hdr, col_meta = st.columns([3, 1])
with col_hdr:
    st.title("🔄 Retention Dashboard")
with col_meta:
    st.markdown("<br>", unsafe_allow_html=True)
    st.caption(f"**{subtitle}**  ·  {today.strftime('%d %b %Y')}")

# ── Period selector (retention-specific, defaults MTD) ────────────────────────
st.markdown(
    '<p class="section-label" style="margin-top:0.5rem;">Reporting period</p>',
    unsafe_allow_html=True,
)
pc1, pc2, pc3 = st.columns([2, 2, 3])
with pc1:
    period_range = st.date_input(
        "Period",
        value=(month_start.date(), today.date()),
        min_value=date(2022, 1, 1),
        max_value=today.date(),
        key="ret_period",
        label_visibility="collapsed",
    )
if isinstance(period_range, (list, tuple)) and len(period_range) == 2:
    p_start = pd.Timestamp(period_range[0])
    p_end   = pd.Timestamp(period_range[1])
else:
    p_start = month_start
    p_end   = today

# Auto-compute compare period (same length, immediately prior)
period_days = (p_end - p_start).days + 1
comp_end    = p_start - timedelta(days=1)
comp_start  = comp_end - timedelta(days=period_days - 1)

with pc2:
    st.markdown(
        f'<div style="padding:0.45rem 0; font-size:0.82rem; color:#94a3b8;">'
        f'Compare to: <b>{comp_start.strftime("%-d %b %Y")}</b> – '
        f'<b>{comp_end.strftime("%-d %b %Y")}</b></div>',
        unsafe_allow_html=True,
    )
with pc3:
    st.markdown(
        f'<div style="padding:0.45rem 0; font-size:0.82rem; color:#94a3b8;">'
        f'{period_days} days</div>',
        unsafe_allow_html=True,
    )

st.markdown("---")

# ══════════════════════════════════════════════════════════════════════════════
# Helper: compute churn rate for a date range using the Recharge definition
#   churn rate = churned_in_period / avg_daily_active_subs
#
# A subscription is "active on day D" if:
#   created_at_dt <= D  AND  (cancelled_at_dt is NaT  OR  cancelled_at_dt > D)
# ══════════════════════════════════════════════════════════════════════════════

def _compute_churn_metrics(
    df_machine: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp
) -> dict:
    """
    Returns dict with: churned, avg_daily_active, churn_rate, active_at_end.
    Uses the Recharge definition: churned / avg daily active subscribers.
    """
    # Churned = true cancellations within [start, end]
    churned = df_machine[
        df_machine["is_true_cancel"]
        & df_machine["cancelled_at_dt"].notna()
        & (df_machine["cancelled_at_dt"] >= start)
        & (df_machine["cancelled_at_dt"] <= end)
    ].shape[0]

    # Average daily active subscribers over the period
    has_created = df_machine[df_machine["created_at_dt"].notna()]
    day_range   = pd.date_range(start, end, freq="D")
    daily_counts = []

    for d in day_range:
        active_on_d = has_created[
            (has_created["created_at_dt"] <= d)
            & (
                has_created["cancelled_at_dt"].isna()
                | (has_created["cancelled_at_dt"] > d)
            )
        ]["subscription_id"].nunique()
        daily_counts.append(active_on_d)

    avg_daily = sum(daily_counts) / len(daily_counts) if daily_counts else 0
    active_end = daily_counts[-1] if daily_counts else 0
    rate = churned / avg_daily if avg_daily > 0 else 0.0

    return {
        "churned":          churned,
        "avg_daily_active": round(avg_daily, 1),
        "churn_rate":       rate,
        "active_at_end":    active_end,
    }


# ── Compute metrics for main period and compare period ────────────────────────
m_main = _compute_churn_metrics(rc_machine, p_start, p_end)
m_comp = _compute_churn_metrics(rc_machine, comp_start, comp_end)

# Deltas
delta_churned = m_main["churned"] - m_comp["churned"]
delta_rate    = m_main["churn_rate"] - m_comp["churn_rate"]

# Average lifetime (all time, for cancelled machine subs in current filters)
cancelled_with_dates = rc_machine[
    rc_machine["cancelled_at_dt"].notna()
    & rc_machine["created_at_dt"].notna()
    & rc_machine["is_true_cancel"]
]
if not cancelled_with_dates.empty:
    lifetimes    = (cancelled_with_dates["cancelled_at_dt"]
                    - cancelled_with_dates["created_at_dt"]).dt.days / 30.44
    avg_lifetime = float(lifetimes.mean())
else:
    avg_lifetime = 0.0

# ── KPI row ───────────────────────────────────────────────────────────────────
k1, k2, k3, k4, k5 = st.columns(5)

k1.metric(
    "Churn Rate",
    f"{m_main['churn_rate']:.2%}",
    delta=f"{delta_rate:+.2%}",
    delta_color="inverse",
    help="Churned ÷ avg daily active subscribers (Recharge definition)",
)
k2.metric(
    "Cancellations",
    f"{m_main['churned']:,}",
    delta=f"{delta_churned:+,} vs compare",
    delta_color="inverse",
)
k3.metric(
    "Avg Daily Active",
    f"{m_main['avg_daily_active']:,.0f}",
    help="Average daily active machine subscribers across the period",
)
k4.metric(
    "Avg Lifetime",
    f"{avg_lifetime:.1f} mo",
    help="Mean months between subscription creation and cancellation",
)
k5.metric(
    "Active Machine Subs",
    f"{m_main['active_at_end']:,}",
    help="Machine subscriptions active at end of period",
)

st.markdown("---")

# ── Cancellations in the reporting period (for charts below) ──────────────────
cxl_window = rc_machine[
    rc_machine["is_true_cancel"]
    & rc_machine["cancelled_at_dt"].notna()
    & (rc_machine["cancelled_at_dt"] >= p_start)
    & (rc_machine["cancelled_at_dt"] <= p_end)
]

# ── Row 1: Cancellation Reasons stacked bar + Donut ──────────────────────────
c_reasons, c_donut = st.columns([3, 2])

with c_reasons:
    if not cxl_window.empty:
        reason_prod = (
            cxl_window.groupby(["cancellation_reason", "product"])
            .size().reset_index(name="count")
        )
        reason_totals = (
            reason_prod.groupby("cancellation_reason")["count"].sum()
            .sort_values(ascending=True)
        )
        sorted_reasons = reason_totals.index.tolist()

        fig_reasons = go.Figure()
        for product in PRODUCT_ORDER:
            prod_data = reason_prod[reason_prod["product"] == product]
            if prod_data.empty:
                continue
            prod_dict = prod_data.set_index("cancellation_reason")["count"].to_dict()
            fig_reasons.add_trace(go.Bar(
                y=sorted_reasons,
                x=[prod_dict.get(r, 0) for r in sorted_reasons],
                name=product,
                orientation="h",
                marker_color=PRODUCT_COLOR.get(product, "#94a3b8"),
                text=[prod_dict.get(r, 0) if prod_dict.get(r, 0) > 0 else ""
                      for r in sorted_reasons],
                textposition="inside",
                textfont=dict(size=9, color="white"),
                hovertemplate="%{y}: %{x}<extra>" + product + "</extra>",
            ))

        fig_reasons.update_layout(
            barmode="stack",
            title=dict(text="Cancellation Reasons by Product", x=0,
                       font=dict(size=13, color="#e2e5f0")),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            height=max(340, len(sorted_reasons) * 36),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                font=dict(size=10, color="#94a3b8"),
            ),
            xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.06)",
                       zeroline=False, tickfont=dict(size=9, color="#94a3b8")),
            yaxis=dict(tickfont=dict(size=10, color="#e2e8f0"), showgrid=False),
            margin=dict(t=56, b=8, l=160, r=8), bargap=0.2,
        )
        st.plotly_chart(fig_reasons, use_container_width=True)
    else:
        st.info("No cancellations in the selected period.")

with c_donut:
    if not cxl_window.empty:
        by_reason = (
            cxl_window.groupby("cancellation_reason").size()
            .reset_index(name="count").sort_values("count", ascending=False)
        )
        n = len(by_reason)
        reason_colors = (
            px.colors.qualitative.Set2[:n] if n <= 8
            else px.colors.qualitative.Alphabet[:n]
        )
        fig_donut = px.pie(
            by_reason, values="count", names="cancellation_reason",
            hole=0.58, title="Reason Split",
            color_discrete_sequence=reason_colors,
        )
        fig_donut.update_traces(
            texttemplate="<b>%{label}</b><br>%{value}",
            textposition="outside",
            hovertemplate="%{label}: %{value} (%{percent:.1%})<extra></extra>",
            textfont_size=10,
        )
        fig_donut.update_layout(
            height=max(340, len(by_reason) * 36),
            showlegend=False, paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(t=44, b=8, l=8, r=8),
            title=dict(x=0, font=dict(size=13, color="#e2e5f0")),
        )
        st.plotly_chart(fig_donut, use_container_width=True)
    else:
        st.empty()

st.markdown("---")

# ── Row 2: Monthly Cancellation Trend + Lifetime Distribution ────────────────
c_trend, c_life = st.columns([3, 2])

with c_trend:
    trend_src = rc_machine[
        rc_machine["is_true_cancel"] & rc_machine["cancelled_at_dt"].notna()
    ].copy()

    if not trend_src.empty:
        trend_src["mp"] = trend_src["cancelled_at_dt"].dt.to_period("M")
        monthly_cxl = trend_src.groupby("mp").size().reset_index(name="cancellations")
        monthly_cxl["label"]    = monthly_cxl["mp"].dt.strftime("%b-%y")
        monthly_cxl["month_dt"] = monthly_cxl["mp"].dt.to_timestamp()

        mask = (monthly_cxl["month_dt"] >= chart_start) & (monthly_cxl["month_dt"] <= chart_end)
        monthly_cxl = monthly_cxl[mask].sort_values("month_dt")

        bar_colors = [
            "#fca5a5" if (m.year == today.year and m.month == today.month) else "#ef4444"
            for m in monthly_cxl["month_dt"]
        ]

        fig_trend = go.Figure(go.Bar(
            x=monthly_cxl["label"], y=monthly_cxl["cancellations"],
            marker_color=bar_colors,
            text=monthly_cxl["cancellations"].apply(lambda v: f"{v}" if v > 0 else ""),
            textposition="outside", textfont=dict(size=9, color="#94a3b8"),
            hovertemplate="<b>%{x}</b><br>%{y} cancellations<extra></extra>",
        ))
        fig_trend.update_layout(
            title=dict(text="Monthly Cancellations", x=0,
                       font=dict(size=13, color="#e2e5f0")),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            height=340,
            xaxis=dict(tickangle=-45, tickfont=dict(size=9, color="#94a3b8"),
                       showgrid=False, zeroline=False),
            yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.06)",
                       zeroline=False, tickfont=dict(size=9, color="#94a3b8")),
            margin=dict(t=44, b=8, l=4, r=8), bargap=0.3,
        )
        st.plotly_chart(fig_trend, use_container_width=True)
    else:
        st.info("No cancellation trend data available.")

with c_life:
    if not cancelled_with_dates.empty:
        life_months = (
            (cancelled_with_dates["cancelled_at_dt"]
             - cancelled_with_dates["created_at_dt"]).dt.days / 30.44
        )
        buckets = pd.cut(
            life_months,
            bins=[0, 1, 3, 6, 12, float("inf")],
            labels=["< 1 mo", "1–3 mo", "3–6 mo", "6–12 mo", "12+ mo"],
            right=True,
        )
        bucket_counts = buckets.value_counts().reindex(
            ["< 1 mo", "1–3 mo", "3–6 mo", "6–12 mo", "12+ mo"], fill_value=0
        )
        total_cxl = bucket_counts.sum()

        fig_life = go.Figure(go.Bar(
            x=bucket_counts.index.tolist(), y=bucket_counts.values.tolist(),
            marker_color=["#ef4444", "#f97316", "#f59e0b", "#84cc16", "#10b981"],
            text=[
                f"{v}<br><span style='font-size:9px'>{v/total_cxl:.0%}</span>"
                if total_cxl > 0 else "" for v in bucket_counts.values
            ],
            textposition="outside", textfont=dict(size=10, color="#94a3b8"),
            hovertemplate="%{x}: %{y} cancellations<extra></extra>",
        ))
        fig_life.update_layout(
            title=dict(text="Lifetime at Cancellation", x=0,
                       font=dict(size=13, color="#e2e5f0")),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            height=340,
            xaxis=dict(tickfont=dict(size=9, color="#94a3b8"),
                       showgrid=False, zeroline=False),
            yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.06)",
                       zeroline=False, tickfont=dict(size=9, color="#94a3b8")),
            margin=dict(t=44, b=8, l=4, r=8), bargap=0.25,
        )
        st.plotly_chart(fig_life, use_container_width=True)
    else:
        st.info("No lifetime data — need both created_at and cancelled_at.")

st.markdown("---")

# ── Row 3: Cohort Retention Heatmap (full width) ─────────────────────────────
st.markdown(
    '<p style="font-size:13px; font-weight:600; color:#e2e5f0; margin-bottom:0.5rem;">'
    "Cohort Retention — Machine Subscriptions</p>",
    unsafe_allow_html=True,
)

MAX_COHORT_MONTHS = 12

cohort_src = rc_machine[rc_machine["created_at_dt"].notna()].copy()
cohort_src["cohort"] = cohort_src["created_at_dt"].dt.to_period("M")

# Filter cohorts to those starting within the sidebar date range
cohort_src = cohort_src[
    cohort_src["cohort"].dt.to_timestamp() >= chart_start
]

cohort_labels = sorted(cohort_src["cohort"].unique())
cohort_rows   = []
cohort_sizes  = []

for cohort in cohort_labels:
    subs   = cohort_src[cohort_src["cohort"] == cohort]
    total  = len(subs)
    if total == 0:
        continue
    c_start      = cohort.to_timestamp()
    months_since = (today.year - c_start.year) * 12 + today.month - c_start.month
    max_m        = min(MAX_COHORT_MONTHS, months_since)

    row = {}
    for m in range(max_m + 1):
        cutoff = c_start + pd.DateOffset(months=m)
        still_active = subs[
            subs["cancelled_at_dt"].isna() | (subs["cancelled_at_dt"] > cutoff)
        ].shape[0]
        row[f"M{m}"] = round(100 * still_active / total, 1)

    cohort_rows.append(row)
    cohort_sizes.append({"cohort": cohort.strftime("%b-%y"), "size": total})

if cohort_rows:
    cohort_df  = pd.DataFrame(cohort_rows)
    size_df    = pd.DataFrame(cohort_sizes)
    month_cols = sorted(
        [c for c in cohort_df.columns if c.startswith("M")],
        key=lambda c: int(c[1:]),
    )
    cohort_df = cohort_df[month_cols]
    z_values  = cohort_df.values
    y_labels  = [f"{r['cohort']} ({r['size']})" for _, r in size_df.iterrows()]

    fig_hm = go.Figure(go.Heatmap(
        z=z_values, x=month_cols, y=y_labels,
        colorscale=[
            [0.0, "#ef4444"], [0.5, "#f59e0b"], [1.0, "#10b981"],
        ],
        zmin=0, zmax=100,
        text=np.where(
            np.isnan(z_values.astype(float)), "",
            z_values.astype(str) + "%",
        ),
        texttemplate="%{text}", textfont=dict(size=9),
        hovertemplate="Cohort: %{y}<br>Month: %{x}<br>Retention: %{z:.1f}%<extra></extra>",
        colorbar=dict(
            title="% Retained", ticksuffix="%", len=0.6, thickness=12,
            tickfont=dict(color="#94a3b8"), titlefont=dict(color="#94a3b8"),
        ),
    ))
    fig_hm.update_layout(
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        height=max(400, len(y_labels) * 30),
        xaxis=dict(title="Months since signup", side="top",
                   tickfont=dict(size=10, color="#94a3b8"),
                   titlefont=dict(size=10, color="#94a3b8")),
        yaxis=dict(tickfont=dict(size=10, color="#e2e8f0"),
                   autorange="reversed"),
        margin=dict(t=40, b=8, l=120, r=8),
    )
    st.plotly_chart(fig_hm, use_container_width=True)

    def _avg_ret(col):
        if col in cohort_df.columns:
            v = cohort_df[col].dropna()
            return f"{v.mean():.0f}%" if len(v) > 0 else "—"
        return "—"

    ck1, ck2, ck3, ck4 = st.columns(4)
    ck1.metric("Avg M1 Retention",  _avg_ret("M1"))
    ck2.metric("Avg M3 Retention",  _avg_ret("M3"))
    ck3.metric("Avg M6 Retention",  _avg_ret("M6"))
    ck4.metric("Avg M12 Retention", _avg_ret("M12"))
else:
    st.info("Not enough cohort data in the selected date range.")

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("---")
notes = [
    "**Churn rate** = churned subscribers ÷ average daily active machine "
    "subscribers over the reporting period (Recharge definition).",
    "**Compare to** period is the same number of days immediately before "
    "the main period.",
    "**Avg lifetime** = mean months between subscription creation and "
    "cancellation (true cancellations only, all time).",
    "**Cohort retention** tracks machine subscriptions grouped by signup "
    "month. A subscription is 'retained' at month N if it had not been "
    "cancelled by that point.",
    "**Reasons** are from the Recharge cancellation_reason field. "
    "'Not Specified' means the field was blank in source data.",
]
with st.expander("ℹ️  Data notes", expanded=False):
    for n in notes:
        st.markdown(f"- {n}")
