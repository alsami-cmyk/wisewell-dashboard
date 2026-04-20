"""
Retention Dashboard — cancellation rate, reason breakdown, cohort heatmap.
All live metrics (Sep-2025+) computed from raw Recharge data.
Pre-Sep-2025 cancellation history blended from hardcoded Monthly Cancellations tab.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dateutil.relativedelta import relativedelta

from utils import (
    PRODUCT_COLOR, PRODUCT_ORDER,
    compute_cancellation_rate, get_load_diagnostics,
    get_monthly_cancellations_blended, load_recharge_full,
)

# ── Sidebar filter state ───────────────────────────────────────────────────────
country_sel = st.session_state.get("s_country", "All")
product_sel = st.session_state.get("s_product", "All")

raw_range = st.session_state.get("s_daterange", (date(2023, 1, 1), date.today()))
if isinstance(raw_range, (list, tuple)) and len(raw_range) == 2:
    chart_start = pd.Timestamp(raw_range[0])
    chart_end   = pd.Timestamp(raw_range[1])
else:
    chart_start = pd.Timestamp(date(2023, 1, 1))
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

# ── Apply sidebar filters ──────────────────────────────────────────────────────
_mkt_filter = country_sel if country_sel not in ("All", "") else None
_prd_filter = product_sel if product_sel not in ("All", "") else None

rc = rc_full.copy()
if _mkt_filter:
    rc = rc[rc["market"] == _mkt_filter]
if _prd_filter:
    rc = rc[rc["product"] == _prd_filter]

rc_machine = rc[rc["category"] == "Machine"].copy()

# ── Date helpers ───────────────────────────────────────────────────────────────
today        = pd.Timestamp.today().normalize()
month_start  = today.replace(day=1)
prev_m_start = (month_start - timedelta(days=1)).replace(day=1)
days_elapsed = today.day

# ── Header ─────────────────────────────────────────────────────────────────────
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

# ── Period selector ────────────────────────────────────────────────────────────
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


# ═══════════════════════════════════════════════════════════════════════════════
# CHURN METRICS HELPER
# ═══════════════════════════════════════════════════════════════════════════════

def _churn_metrics(df_machine: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    """
    Compute cancellation rate for a period.
    Denominator = active subscribers × quantity at start of period.
    Numerator   = true cancellations × quantity within [start, end].
    Current partial month is extrapolated.
    """
    _today = pd.Timestamp.today().normalize()

    churned = int(df_machine[
        df_machine["is_true_cancel"] &
        df_machine["cancelled_at_dt"].notna() &
        (df_machine["cancelled_at_dt"] >= start) &
        (df_machine["cancelled_at_dt"] <= end)
    ]["quantity"].sum())

    has_created = df_machine[df_machine["created_at_dt"].notna()]
    active_start = int(has_created[
        (has_created["created_at_dt"] <= start) &
        (has_created["cancelled_at_dt"].isna() | (has_created["cancelled_at_dt"] > start))
    ]["quantity"].sum())

    active_end = int(has_created[
        (has_created["created_at_dt"] <= end) &
        (has_created["cancelled_at_dt"].isna() | (has_created["cancelled_at_dt"] > end))
    ]["quantity"].sum())

    # Extrapolate if period overlaps the current (incomplete) month
    cur_m_start   = _today.replace(day=1)
    days_in_month = calendar.monthrange(_today.year, _today.month)[1]
    is_partial    = (end >= cur_m_start and _today.day < days_in_month)

    if is_partial and _today.day > 0:
        extrapolated = churned * days_in_month / _today.day
        rate = extrapolated / active_start if active_start > 0 else 0.0
    else:
        extrapolated = float(churned)
        rate = churned / active_start if active_start > 0 else 0.0

    return {
        "churned":         churned,
        "extrapolated":    extrapolated,
        "active_at_start": active_start,
        "active_at_end":   active_end,
        "rate":            rate,
    }


# ── Compute main period + compare ─────────────────────────────────────────────
m_main = _churn_metrics(rc_machine, p_start, p_end)
m_comp = _churn_metrics(rc_machine, comp_start, comp_end)

delta_churned = m_main["churned"] - m_comp["churned"]
delta_rate    = m_main["rate"]    - m_comp["rate"]

# Average lifetime for true cancellations (all time, current filters)
cxl_dated = rc_machine[
    rc_machine["cancelled_at_dt"].notna() &
    rc_machine["created_at_dt"].notna() &
    rc_machine["is_true_cancel"]
]
avg_lifetime = float(
    ((cxl_dated["cancelled_at_dt"] - cxl_dated["created_at_dt"]).dt.days / 30.44).mean()
) if not cxl_dated.empty else 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# KPI ROW
# ═══════════════════════════════════════════════════════════════════════════════
k1, k2, k3, k4, k5 = st.columns(5)

k1.metric(
    "Cancellation Rate",
    f"{m_main['rate']:.2%}",
    delta=f"{delta_rate:+.2%}",
    delta_color="inverse",
    help="True cancels (× quantity) ÷ active subs at period start. Current month extrapolated.",
)
k2.metric(
    "Cancellations",
    f"{m_main['churned']:,}",
    delta=f"{delta_churned:+,} vs compare",
    delta_color="inverse",
)
k3.metric(
    "Active at Period Start",
    f"{m_main['active_at_start']:,}",
    help="Machine subscription units (quantity) active at start of the reporting period",
)
k4.metric(
    "Avg Lifetime",
    f"{avg_lifetime:.1f} mo",
    help="Mean months from subscription creation to cancellation (true cancels, all time)",
)
k5.metric(
    "Active Machine Subs",
    f"{m_main['active_at_end']:,}",
    help="Machine subscription units active at end of period",
)

st.markdown("---")

# Cancellations in the reporting window (for charts)
cxl_window = rc_machine[
    rc_machine["is_true_cancel"] &
    rc_machine["cancelled_at_dt"].notna() &
    (rc_machine["cancelled_at_dt"] >= p_start) &
    (rc_machine["cancelled_at_dt"] <= p_end)
]


# ═══════════════════════════════════════════════════════════════════════════════
# ROW 1: Cancellation Reasons stacked bar + Donut
# ═══════════════════════════════════════════════════════════════════════════════
c_reasons, c_donut = st.columns([3, 2])

with c_reasons:
    if not cxl_window.empty:
        # Weighted by quantity
        reason_prod = (
            cxl_window.groupby(["cancellation_reason", "product"])["quantity"]
            .sum().reset_index(name="count")
        )
        reason_totals = (
            reason_prod.groupby("cancellation_reason")["count"].sum()
            .sort_values(ascending=True)
        )
        sorted_reasons = reason_totals.index.tolist()

        fig_reasons = go.Figure()
        for prod in PRODUCT_ORDER:
            prod_data = reason_prod[reason_prod["product"] == prod]
            if prod_data.empty:
                continue
            prod_dict = prod_data.set_index("cancellation_reason")["count"].to_dict()
            fig_reasons.add_trace(go.Bar(
                y=sorted_reasons,
                x=[prod_dict.get(r, 0) for r in sorted_reasons],
                name=prod,
                orientation="h",
                marker_color=PRODUCT_COLOR.get(prod, "#94a3b8"),
                text=[prod_dict.get(r, 0) if prod_dict.get(r, 0) > 0 else ""
                      for r in sorted_reasons],
                textposition="inside",
                textfont=dict(size=9, color="white"),
                hovertemplate="%{y}: %{x}<extra>" + prod + "</extra>",
            ))

        fig_reasons.update_layout(
            barmode="stack",
            title=dict(text="Cancellation Reasons by Product", x=0,
                       font=dict(size=13, color="#e2e5f0")),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            height=max(340, len(sorted_reasons) * 36),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                        font=dict(size=10, color="#94a3b8")),
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
            cxl_window.groupby("cancellation_reason")["quantity"]
            .sum().reset_index(name="count")
            .sort_values("count", ascending=False)
            .query("count > 0")
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

st.markdown("---")


# ═══════════════════════════════════════════════════════════════════════════════
# ROW 2: Monthly Cancellation Trend (blended) + Lifetime Distribution
# ═══════════════════════════════════════════════════════════════════════════════
c_trend, c_life = st.columns([3, 2])

with c_trend:
    cancel_series = get_monthly_cancellations_blended()

    # Apply market / product filters
    cs = cancel_series.copy()
    if _mkt_filter:
        cs = cs[cs["market"] == _mkt_filter]
    if _prd_filter:
        cs = cs[cs["product"] == _prd_filter]

    cs = cs.groupby("month_dt", as_index=False)["qty"].sum()
    cs = cs[(cs["month_dt"] >= chart_start) & (cs["month_dt"] <= chart_end)]
    cs["label"] = cs["month_dt"].dt.strftime("%b-%y")

    if not cs.empty:
        bar_colors = [
            "#fca5a5" if (m.year == today.year and m.month == today.month) else "#ef4444"
            for m in cs["month_dt"]
        ]
        fig_trend = go.Figure(go.Bar(
            x=cs["label"], y=cs["qty"],
            marker_color=bar_colors,
            text=cs["qty"].apply(lambda v: str(v) if v > 0 else ""),
            textposition="outside", textfont=dict(size=9, color="#94a3b8"),
            hovertemplate="<b>%{x}</b><br>%{y:,} cancellations<extra></extra>",
        ))
        fig_trend.update_layout(
            title=dict(text="Monthly Cancellations (pre-Sep-25 hardcoded · Sep-25+ live)",
                       x=0, font=dict(size=13, color="#e2e5f0")),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            height=340,
            xaxis=dict(tickangle=-45, tickfont=dict(size=9, color="#94a3b8"),
                       showgrid=False, zeroline=False),
            yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.06)",
                       zeroline=False, tickfont=dict(size=9, color="#94a3b8")),
            margin=dict(t=56, b=8, l=4, r=8), bargap=0.3,
        )
        st.plotly_chart(fig_trend, use_container_width=True)
    else:
        st.info("No cancellation trend data for the selected filters.")

with c_life:
    if not cxl_dated.empty:
        life_months = (cxl_dated["cancelled_at_dt"] - cxl_dated["created_at_dt"]).dt.days / 30.44
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
            hovertemplate="%{x}: %{y}<extra></extra>",
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
        st.info("No lifetime data available.")

st.markdown("---")


# ═══════════════════════════════════════════════════════════════════════════════
# ROW 3: Cohort Retention Heatmap
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown(
    '<p style="font-size:13px; font-weight:600; color:#e2e5f0; margin-bottom:0.5rem;">'
    "Cohort Retention — Machine Subscriptions (quantity-weighted)</p>",
    unsafe_allow_html=True,
)

MAX_COHORT_MONTHS = 12

cohort_src = rc_machine[rc_machine["created_at_dt"].notna()].copy()
cohort_src["cohort"] = cohort_src["created_at_dt"].dt.to_period("M")
cohort_src = cohort_src[cohort_src["cohort"].dt.to_timestamp() >= chart_start]

cohort_labels = sorted(cohort_src["cohort"].unique())
cohort_rows: list[dict] = []
cohort_sizes: list[dict] = []

for cohort in cohort_labels:
    subs  = cohort_src[cohort_src["cohort"] == cohort]
    total = int(subs["quantity"].sum())   # quantity-weighted cohort size
    if total == 0:
        continue
    c_start      = cohort.to_timestamp()
    months_since = (today.year - c_start.year) * 12 + today.month - c_start.month
    max_m        = min(MAX_COHORT_MONTHS, months_since)

    row = {}
    for m in range(max_m + 1):
        cutoff = c_start + pd.DateOffset(months=m)
        retained = int(subs[
            subs["cancelled_at_dt"].isna() | (subs["cancelled_at_dt"] > cutoff)
        ]["quantity"].sum())
        row[f"M{m}"] = round(100 * retained / total, 1)

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
        colorscale=[[0.0, "#ef4444"], [0.5, "#f59e0b"], [1.0, "#10b981"]],
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
        yaxis=dict(tickfont=dict(size=10, color="#e2e8f0"), autorange="reversed"),
        margin=dict(t=40, b=8, l=120, r=8),
    )
    st.plotly_chart(fig_hm, use_container_width=True)

    ck1, ck2, ck3, ck4 = st.columns(4)
    def _avg_ret(col):
        if col in cohort_df.columns:
            v = cohort_df[col].dropna()
            return f"{v.mean():.0f}%" if len(v) > 0 else "—"
        return "—"
    ck1.metric("Avg M1 Retention",  _avg_ret("M1"))
    ck2.metric("Avg M3 Retention",  _avg_ret("M3"))
    ck3.metric("Avg M6 Retention",  _avg_ret("M6"))
    ck4.metric("Avg M12 Retention", _avg_ret("M12"))
else:
    st.info("Not enough cohort data in the selected date range.")


# ── Footer ──────────────────────────────────────────────────────────────────────
st.markdown("---")
with st.expander("ℹ️  Data notes", expanded=False):
    st.markdown(
        "- **Cancellation rate** = (MTD true cancels × quantity ÷ days elapsed × days in month) "
        "÷ active machine subs (× quantity) at last day of prior month.\n"
        "- **True cancellations** = `cancelled_at` is set AND reason NOT in "
        "`swapped / purchased / converted / swap / max`.\n"
        "- **Cohort retention** is quantity-weighted: a subscription of 5 machines counts as 5 units.\n"
        "- **Monthly trend** pre-Sep-2025: hardcoded spreadsheet data (final truth); "
        "Sep-2025+: live Recharge data.\n"
        "- **Compare period** = same duration immediately before the main period.\n"
    )
