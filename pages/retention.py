"""
Retention Dashboard — cancellation analysis, reason breakdown, cohort retention.
All data from Recharge source tabs (no Shopify needed).
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
    fx      = get_fx()
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

# ── Apply filters ─────────────────────────────────────────────────────────────
rc = rc_full.copy()
if country_sel != "All":
    rc = rc[rc["market"] == country_sel]
if product_sel != "All":
    rc = rc[rc["product"] == product_sel]

# Focus on machine subscriptions for retention metrics
rc_machine = rc[rc["category"] == "Machine"].copy()

# ── Date helpers ──────────────────────────────────────────────────────────────
today        = pd.Timestamp.today().normalize()
month_start  = today.replace(day=1)
prev_m_end   = month_start - timedelta(days=1)
prev_m_start = prev_m_end.replace(day=1)
days_elapsed = today.day

# ── KPIs ──────────────────────────────────────────────────────────────────────

# True cancellations within the date-range window (for the chart/reason view)
cxl_window = rc_machine[
    rc_machine["is_true_cancel"]
    & rc_machine["cancelled_at_dt"].notna()
    & (rc_machine["cancelled_at_dt"] >= chart_start)
    & (rc_machine["cancelled_at_dt"] <= chart_end)
]

# MTD true cancellations
cxl_mtd = rc_machine[
    rc_machine["is_true_cancel"]
    & rc_machine["cancelled_at_dt"].notna()
    & (rc_machine["cancelled_at_dt"] >= month_start)
    & (rc_machine["cancelled_at_dt"] <= today)
]
total_cxl_mtd = len(cxl_mtd)

# Active machine subs (denominator for rate)
active_machine = rc_machine[rc_machine["status"] == "ACTIVE"]["subscription_id"].nunique()
cancel_rate    = total_cxl_mtd / active_machine if active_machine > 0 else 0.0

# MoM cancellation comparison
mom_cutoff   = prev_m_start + timedelta(days=days_elapsed - 1)
cxl_prev_mtd = rc_machine[
    rc_machine["is_true_cancel"]
    & rc_machine["cancelled_at_dt"].notna()
    & (rc_machine["cancelled_at_dt"] >= prev_m_start)
    & (rc_machine["cancelled_at_dt"] <= mom_cutoff)
]
total_cxl_prev = len(cxl_prev_mtd)
mom_delta      = total_cxl_mtd - total_cxl_prev

# Average lifetime (months) for cancelled machine subs
cancelled_with_dates = rc_machine[
    rc_machine["cancelled_at_dt"].notna()
    & rc_machine["created_at_dt"].notna()
    & rc_machine["is_true_cancel"]
]
if not cancelled_with_dates.empty:
    lifetimes = (
        cancelled_with_dates["cancelled_at_dt"] - cancelled_with_dates["created_at_dt"]
    ).dt.days / 30.44
    avg_lifetime = float(lifetimes.mean())
else:
    avg_lifetime = 0.0

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

st.markdown("---")

# ── KPI row ───────────────────────────────────────────────────────────────────
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("MTD Cancel Rate",   f"{cancel_rate:.2%}")
k2.metric("Total Cancellations MTD", f"{total_cxl_mtd:,}",
          delta=f"{mom_delta:+,} vs prev", delta_color="inverse")
k3.metric("Prev Month (same period)", f"{total_cxl_prev:,}")
k4.metric("Avg Lifetime",      f"{avg_lifetime:.1f} mo",
          help="Average months between subscription creation and cancellation")
k5.metric("Active Machine Subs", f"{active_machine:,}")

st.markdown("---")

# ── Row 1: Cancellation Reasons stacked bar + Donut ──────────────────────────
c_reasons, c_donut = st.columns([3, 2])

with c_reasons:
    if not cxl_window.empty:
        # Aggregate by reason × product
        reason_prod = (
            cxl_window.groupby(["cancellation_reason", "product"])
            .size()
            .reset_index(name="count")
        )
        # Sort reasons by total count descending
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
                y=[r for r in sorted_reasons],
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
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            height=max(340, len(sorted_reasons) * 36),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                font=dict(size=10, color="#94a3b8"),
            ),
            xaxis=dict(
                showgrid=True, gridcolor="rgba(255,255,255,0.06)",
                zeroline=False, tickfont=dict(size=9, color="#94a3b8"),
            ),
            yaxis=dict(
                tickfont=dict(size=10, color="#e2e8f0"),
                showgrid=False,
            ),
            margin=dict(t=56, b=8, l=160, r=8),
            bargap=0.2,
        )
        st.plotly_chart(fig_reasons, use_container_width=True)
    else:
        st.info("No cancellations in the selected date range.")

with c_donut:
    if not cxl_window.empty:
        by_reason = (
            cxl_window.groupby("cancellation_reason").size()
            .reset_index(name="count")
            .sort_values("count", ascending=False)
        )
        # Color scale — use a sequential palette for reasons
        n = len(by_reason)
        reason_colors = px.colors.qualitative.Set2[:n] if n <= 8 else px.colors.qualitative.Alphabet[:n]

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
            height=max(340, len(sorted_reasons) * 36) if not cxl_window.empty else 340,
            showlegend=False,
            paper_bgcolor="rgba(0,0,0,0)",
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
    trend_data = rc_machine[
        rc_machine["is_true_cancel"]
        & rc_machine["cancelled_at_dt"].notna()
    ].copy()

    if not trend_data.empty:
        trend_data["mp"] = trend_data["cancelled_at_dt"].dt.to_period("M")
        monthly_cxl = (
            trend_data.groupby("mp").size()
            .reset_index(name="cancellations")
        )
        monthly_cxl["label"]    = monthly_cxl["mp"].dt.strftime("%b-%y")
        monthly_cxl["month_dt"] = monthly_cxl["mp"].dt.to_timestamp()

        # Filter to sidebar date range
        mask = (
            (monthly_cxl["month_dt"] >= chart_start)
            & (monthly_cxl["month_dt"] <= chart_end)
        )
        monthly_cxl = monthly_cxl[mask].sort_values("month_dt")

        bar_colors = [
            "#fca5a5" if (m.year == today.year and m.month == today.month) else "#ef4444"
            for m in monthly_cxl["month_dt"]
        ]

        fig_trend = go.Figure(go.Bar(
            x=monthly_cxl["label"],
            y=monthly_cxl["cancellations"],
            marker_color=bar_colors,
            text=monthly_cxl["cancellations"].apply(
                lambda v: f"{v}" if v > 0 else ""
            ),
            textposition="outside",
            textfont=dict(size=9, color="#94a3b8"),
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

        # Bucket into ranges
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
            x=bucket_counts.index.tolist(),
            y=bucket_counts.values.tolist(),
            marker_color=["#ef4444", "#f97316", "#f59e0b", "#84cc16", "#10b981"],
            text=[
                f"{v}<br><span style='font-size:9px'>{v/total_cxl:.0%}</span>"
                if total_cxl > 0 else ""
                for v in bucket_counts.values
            ],
            textposition="outside",
            textfont=dict(size=10, color="#94a3b8"),
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

# Build cohort table: rows = signup month, columns = M0..M12
MAX_COHORT_MONTHS = 12

cohort_src = rc_machine[rc_machine["created_at_dt"].notna()].copy()
cohort_src["cohort"] = cohort_src["created_at_dt"].dt.to_period("M")

# Filter cohorts within date range
cohort_src = cohort_src[
    (cohort_src["created_at_dt"] >= chart_start)
    | (cohort_src["cohort"].dt.to_timestamp() >= chart_start)
]

cohort_labels   = sorted(cohort_src["cohort"].unique())
cohort_rows     = []
cohort_sizes    = []

for cohort in cohort_labels:
    cohort_subs  = cohort_src[cohort_src["cohort"] == cohort]
    total        = len(cohort_subs)
    if total == 0:
        continue
    cohort_start = cohort.to_timestamp()
    months_since = (today.year - cohort_start.year) * 12 + today.month - cohort_start.month
    max_m        = min(MAX_COHORT_MONTHS, months_since)

    row = {}
    for m in range(max_m + 1):
        cutoff = cohort_start + pd.DateOffset(months=m)
        still_active = cohort_subs[
            cohort_subs["cancelled_at_dt"].isna()
            | (cohort_subs["cancelled_at_dt"] > cutoff)
        ].shape[0]
        row[f"M{m}"] = round(100 * still_active / total, 1)

    cohort_rows.append(row)
    cohort_sizes.append({"cohort": cohort.strftime("%b-%y"), "size": total})

if cohort_rows:
    cohort_df    = pd.DataFrame(cohort_rows)
    size_df      = pd.DataFrame(cohort_sizes)
    month_cols   = [c for c in cohort_df.columns if c.startswith("M")]
    # Sort month columns numerically
    month_cols   = sorted(month_cols, key=lambda c: int(c[1:]))
    cohort_df    = cohort_df[month_cols]

    z_values     = cohort_df.values
    y_labels     = [f"{r['cohort']} ({r['size']})" for _, r in size_df.iterrows()]

    fig_hm = go.Figure(go.Heatmap(
        z=z_values,
        x=month_cols,
        y=y_labels,
        colorscale=[
            [0.0,  "#ef4444"],   # red — low retention
            [0.5,  "#f59e0b"],   # amber — mid
            [1.0,  "#10b981"],   # green — high retention
        ],
        zmin=0,
        zmax=100,
        text=np.where(np.isnan(z_values.astype(float)), "", z_values.astype(str) + "%"),
        texttemplate="%{text}",
        textfont=dict(size=9),
        hovertemplate=(
            "Cohort: %{y}<br>Month: %{x}<br>"
            "Retention: %{z:.1f}%<extra></extra>"
        ),
        colorbar=dict(
            title="% Retained",
            ticksuffix="%",
            len=0.6,
            thickness=12,
            tickfont=dict(color="#94a3b8"),
            titlefont=dict(color="#94a3b8"),
        ),
    ))
    fig_hm.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        height=max(400, len(y_labels) * 30),
        xaxis=dict(
            title="Months since signup", side="top",
            tickfont=dict(size=10, color="#94a3b8"),
            titlefont=dict(size=10, color="#94a3b8"),
        ),
        yaxis=dict(
            tickfont=dict(size=10, color="#e2e8f0"),
            autorange="reversed",
        ),
        margin=dict(t=40, b=8, l=120, r=8),
    )
    st.plotly_chart(fig_hm, use_container_width=True)

    # Cohort summary KPIs
    def _avg_retention_at(month_col):
        if month_col in cohort_df.columns:
            vals = cohort_df[month_col].dropna()
            return f"{vals.mean():.0f}%" if len(vals) > 0 else "—"
        return "—"

    ck1, ck2, ck3, ck4 = st.columns(4)
    ck1.metric("Avg M1 Retention",  _avg_retention_at("M1"))
    ck2.metric("Avg M3 Retention",  _avg_retention_at("M3"))
    ck3.metric("Avg M6 Retention",  _avg_retention_at("M6"))
    ck4.metric("Avg M12 Retention", _avg_retention_at("M12"))

else:
    st.info("Not enough cohort data in the selected date range.")

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("---")
notes = [
    "**Cancellation rate** = MTD true machine cancellations ÷ active machine subscriptions.",
    "**Avg lifetime** = mean months between subscription creation and cancellation "
    "(only includes subscriptions with both dates).",
    "**Cohort retention** tracks machine subscriptions grouped by signup month. "
    "A subscription is 'retained' at month N if it had not yet been cancelled by that point.",
    "**Reasons** are extracted from the Recharge cancellation_reason field. "
    "'Not Specified' means the field was blank in the source data.",
]
with st.expander("ℹ️  Data notes", expanded=False):
    for n in notes:
        st.markdown(f"- {n}")
