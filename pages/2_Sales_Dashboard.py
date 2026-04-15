"""
Wisewell Sales Dashboard — page 3
Replicates the Looker Sales Dashboard with country + product filters.

Data sources:
  - Monthly bar chart   → Monthly Sales tab (full Jan-23 history, validated)
  - Daily chart / KPIs  → Shopify tabs (live, Sep-25 onward)
  - Active users / ARR  → Recharge tabs (live)
  - Cancellation rate   → Dashboard Summary (global) / Recharge (filtered)
"""

import calendar
from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from utils import (
    PRODUCT_ORDER, PRODUCT_COLOR, SHARED_CSS,
    apply_fx, fmt_usd, get_fx, load_raw_data,
    load_all_shopify, load_recharge_cancellations,
    load_monthly_sales_raw, monthly_sales_series, load_dashboard_kpis,
)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Wisewell Sales",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)
st_autorefresh(interval=5 * 60 * 1000, key="sales_refresh")
st.markdown(SHARED_CSS, unsafe_allow_html=True)

# ── Sidebar filters ───────────────────────────────────────────────────────────
with st.sidebar:
    st.image(
        "https://cdn.prod.website-files.com/63e6996dee5c3840d6a4b55a/"
        "63e699c0a3ce5e6d68c11b15_Wisewell%20Logo%20White.svg",
        width=160,
    )
    st.markdown("---")
    st.header("Filters")
    country_sel = st.radio("Country", ["All", "UAE", "KSA"], index=0, key="s_country")
    product_sel = st.radio("Product", ["All"] + PRODUCT_ORDER, index=0, key="s_product")
    st.markdown("---")
    if st.button("↻ Force refresh", use_container_width=True, key="s_btn"):
        st.cache_data.clear()
        st.rerun()
    st.caption("Auto-refreshes every 5 min")

# ── Load data ─────────────────────────────────────────────────────────────────
shopify_df   = load_all_shopify()
recharge_df  = apply_fx(load_raw_data(), get_fx())
cancel_df    = load_recharge_cancellations()
monthly_vals = load_monthly_sales_raw()
kpis         = load_dashboard_kpis()

# ── Apply filters ─────────────────────────────────────────────────────────────
sh = shopify_df.copy()
rc = recharge_df.copy()
cc = cancel_df.copy()

if country_sel != "All":
    sh = sh[sh["country"] == country_sel]
    rc = rc[rc["market"]  == country_sel]
    cc = cc[cc["market"]  == country_sel]

if product_sel != "All":
    sh = sh[sh["product"] == product_sel]
    rc = rc[rc["product"] == product_sel]
    cc = cc[cc["product"] == product_sel]

# ── Date helpers ──────────────────────────────────────────────────────────────
today          = pd.Timestamp.today().normalize()
month_start    = today.replace(day=1)
prev_m_end     = month_start - timedelta(days=1)
prev_m_start   = prev_m_end.replace(day=1)
days_elapsed   = today.day
days_in_month  = calendar.monthrange(today.year, today.month)[1]
days_remaining = max(1, days_in_month - days_elapsed + 1)

# ── KPI computations ──────────────────────────────────────────────────────────

# Sales (from Shopify — live, Sep-25 onward)
today_sales  = int(sh[sh["date"] == today]["qty"].sum())
mtd_sales    = int(sh[sh["date"] >= month_start]["qty"].sum())
mom_cutoff   = prev_m_start + timedelta(days=days_elapsed - 1)
mom_sales    = int(sh[(sh["date"] >= prev_m_start) & (sh["date"] <= mom_cutoff)]["qty"].sum())
daily_avg    = mtd_sales / days_elapsed if days_elapsed > 0 else 0

try:
    monthly_target = int(str(kpis.get("monthly_target", "0")).replace(",", ""))
except ValueError:
    monthly_target = 0
avg_needed = max(0.0, (monthly_target - mtd_sales) / days_remaining)

# Active users + ARR (from Recharge)
total_users = rc["subscription_id"].nunique()
total_arr   = rc["arr_usd"].sum()

# Cancellation rate
# Global: use pre-computed Dashboard Summary value (matches Looker exactly)
# Filtered: compute from Recharge cancellations / active machine subs
if country_sel == "All" and product_sel == "All":
    raw_rate = kpis.get("cancellation_rate", "0%").replace("%", "")
    try:    cancel_rate_str = f"{float(raw_rate):.2f}%"
    except: cancel_rate_str = kpis.get("cancellation_rate", "N/A")
else:
    cc_machine = cc[cc["category"] == "Machine"]
    mtd_cancels = int(cc_machine[
        cc_machine["is_true_cancel"] &
        (cc_machine["cancelled_at_dt"] >= month_start) &
        (cc_machine["cancelled_at_dt"] <= today)
    ].shape[0])
    active_machine = rc[rc["category"] == "Machine"]["subscription_id"].nunique()
    rate = mtd_cancels / active_machine if active_machine > 0 else 0
    cancel_rate_str = f"{rate:.2%}"

# CAC: from Dashboard Summary (global); note when filtered
cac_raw = kpis.get("cac", "N/A")
cac_display = cac_raw if country_sel == "All" else f"{cac_raw} (Global)"

# ── Header ────────────────────────────────────────────────────────────────────
parts = []
if country_sel != "All": parts.append(country_sel)
if product_sel != "All": parts.append(product_sel)
subtitle = "  ·  ".join(parts) if parts else "Global · All Products"

st.title("📈 Wisewell Sales Dashboard")
st.caption(
    f"**{subtitle}**  ·  Last synced: {today.strftime('%d %b %Y · %H:%M')}"
)
st.markdown("---")

# ── KPI row ───────────────────────────────────────────────────────────────────
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Today's Sales",      f"{today_sales:,}")
k2.metric("Total Active Users", f"{total_users:,}")
k3.metric("ARR",                fmt_usd(total_arr))
k4.metric("Cancellation Rate",  cancel_rate_str)
k5.metric("CAC",                cac_display)
k6.metric("Monthly Target",     f"{monthly_target:,}")
st.markdown("---")

# ── Row 1: Monthly bar chart  +  secondary KPI grid ──────────────────────────
c_chart, c_kpis = st.columns([3, 2])

with c_chart:
    # Monthly Sales tab for full Jan-23 history (pre-validated numbers)
    months_raw, sales_vals = monthly_sales_series(monthly_vals, country_sel, product_sel)

    # Parse, filter future/empty months
    chart_months, chart_vals = [], []
    for m_label, v in zip(months_raw, sales_vals):
        try:
            m_date = pd.to_datetime(m_label, format="%b-%y")
        except Exception:
            continue
        if m_date > today:
            continue
        chart_months.append(m_label)
        chart_vals.append(v)

    # Highlight current (partial) month
    bar_colors = []
    for ml in chart_months:
        try:
            m = pd.to_datetime(ml, format="%b-%y")
            bar_colors.append(
                "#38bdf8" if (m.year == today.year and m.month == today.month)
                else "#0ea5e9"
            )
        except Exception:
            bar_colors.append("#0ea5e9")

    fig_monthly = go.Figure(go.Bar(
        x=chart_months,
        y=chart_vals,
        marker_color=bar_colors,
        text=[str(v) if v > 0 else "" for v in chart_vals],
        textposition="outside",
        textfont=dict(size=10),
        hovertemplate="%{x}: <b>%{y:,}</b><extra></extra>",
    ))
    fig_monthly.update_layout(
        title=dict(text="Monthly Sales", font=dict(size=14)),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        height=360,
        xaxis=dict(tickangle=-45, tickfont=dict(size=9), showgrid=False, zeroline=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(128,128,128,0.15)", zeroline=False),
        margin=dict(t=40, b=10, l=0, r=10),
        bargap=0.25,
    )
    # KSA data gap notice
    if country_sel == "KSA":
        st.caption("⚠️  Monthly Sales tab KSA figures show 0 from Jan-26 — "
                   "formula in the sheet may need updating for this market.")
    st.plotly_chart(fig_monthly, use_container_width=True)

with c_kpis:
    st.markdown("<br>", unsafe_allow_html=True)
    r1a, r1b = st.columns(2)
    r1a.metric("MTD Sales", f"{mtd_sales:,}")
    r1b.metric("MoM Sales", f"{mom_sales:,}",
               delta=f"{mtd_sales - mom_sales:+,} vs same period last month")
    st.markdown("<br><br>", unsafe_allow_html=True)
    r2a, r2b = st.columns(2)
    r2a.metric("Daily Average",  f"{daily_avg:.1f}")
    r2b.metric("Avg. Needed",    f"{avg_needed:.1f}",
               help=f"Sales/day needed to reach {monthly_target:,} monthly target")

# ── Row 2: Daily last-30-days  +  MTD split donut ────────────────────────────
c_daily, c_donut = st.columns([3, 2])

with c_daily:
    thirty_ago = today - timedelta(days=29)
    daily_agg  = (
        sh[sh["date"] >= thirty_ago]
        .groupby("date")["qty"].sum()
        .reset_index()
        .rename(columns={"qty": "sales"})
    )
    # Fill missing dates so every day in the window appears
    all_dates  = pd.date_range(thirty_ago, today, freq="D").normalize()
    daily_full = (
        pd.DataFrame({"date": all_dates})
        .merge(daily_agg, on="date", how="left")
        .fillna({"sales": 0})
    )
    daily_full["sales"] = daily_full["sales"].astype(int)
    daily_full["label"] = daily_full["date"].dt.strftime("%-d-%b")

    fig_daily = go.Figure(go.Bar(
        x=daily_full["label"],
        y=daily_full["sales"],
        marker_color="#6366f1",
        text=daily_full["sales"].apply(lambda v: str(v) if v > 0 else ""),
        textposition="outside",
        textfont=dict(size=9),
        hovertemplate="%{x}: <b>%{y:,}</b><extra></extra>",
    ))
    fig_daily.update_layout(
        title=dict(text="Sales — Last 30 Days", font=dict(size=14)),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        height=360,
        xaxis=dict(tickangle=-45, tickfont=dict(size=9), showgrid=False, zeroline=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(128,128,128,0.15)", zeroline=False),
        margin=dict(t=40, b=10, l=0, r=10),
        bargap=0.3,
    )
    st.plotly_chart(fig_daily, use_container_width=True)

with c_donut:
    mtd_sh = sh[sh["date"] >= month_start].copy()
    if not mtd_sh.empty:
        mtd_by_prod = (
            mtd_sh.groupby("product")["qty"]
            .sum()
            .reset_index()
            .rename(columns={"qty": "sales"})
        )
        # Bucket non-machine products as "Others"
        mtd_by_prod["label"] = mtd_by_prod["product"].apply(
            lambda p: p if p in PRODUCT_ORDER else "Others"
        )
        donut_df = (
            mtd_by_prod.groupby("label")["sales"]
            .sum()
            .reset_index()
        )
        donut_df = donut_df[donut_df["sales"] > 0]

        fig_donut = px.pie(
            donut_df,
            values="sales",
            names="label",
            color="label",
            color_discrete_map=PRODUCT_COLOR,
            hole=0.55,
            title="MTD Sales Split",
        )
        fig_donut.update_traces(
            texttemplate="%{label}<br><b>%{value:,}</b>",
            textposition="outside",
            hovertemplate="%{label}: %{value:,} (%{percent:.1%})<extra></extra>",
        )
        fig_donut.update_layout(
            height=360,
            showlegend=True,
            legend=dict(
                orientation="h", yanchor="bottom", y=-0.25,
                xanchor="center", x=0.5, font=dict(size=10)
            ),
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(t=40, b=10, l=0, r=0),
            title=dict(font=dict(size=14)),
        )
        st.plotly_chart(fig_donut, use_container_width=True)
    else:
        st.info("No MTD sales data for the current filter selection.")
