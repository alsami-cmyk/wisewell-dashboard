"""
Wisewell Sales Dashboard — page 3
Mirrors the Looker Sales Dashboard with country, product, and date-range filters.

Data sources
  Monthly bar chart  → Monthly Sales tab (Jan-23 history) merged with Shopify/Recharge
                        for live period (Shopify KSA fills the Monthly Sales tab gap
                        from Jan-26 when the sheet formula stopped updating)
  Daily chart / KPIs → Shopify UAE + KSA + USA (live)
  Total Active Users → Monthly User Base tab (subscriptions + owners, owners never churn)
  ARR                → Recharge UAE + KSA + USA (ACTIVE subscriptions)
  Cancellation rate  → Dashboard Summary (global) / computed from Recharge (filtered)
"""

import calendar
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from utils import (
    PRODUCT_ORDER, PRODUCT_COLOR, SHARED_CSS,
    apply_fx, fmt_usd, get_fx, load_raw_data,
    load_all_shopify, load_recharge_cancellations, load_user_base_current,
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

# ── Additional CSS ─────────────────────────────────────────────────────────────
st.markdown(SHARED_CSS, unsafe_allow_html=True)
st.markdown("""
<style>
/* Section dividers */
.section-label {
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: .1em;
    text-transform: uppercase;
    color: #94a3b8;
    margin: 0 0 0.25rem 0;
}
/* KPI delta colouring */
[data-testid="stMetricDelta"] svg { display: none; }
/* Chart container subtle card */
[data-testid="stPlotlyChart"] {
    border-radius: 12px;
}
</style>
""", unsafe_allow_html=True)

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image(
        "https://cdn.prod.website-files.com/63e6996dee5c3840d6a4b55a/"
        "63e699c0a3ce5e6d68c11b15_Wisewell%20Logo%20White.svg",
        width=160,
    )
    st.markdown("---")

    st.markdown('<p class="section-label">Country</p>', unsafe_allow_html=True)
    country_sel = st.radio(
        "Country", ["All", "UAE", "KSA", "USA"],
        index=0, key="s_country", label_visibility="collapsed",
    )

    st.markdown('<p class="section-label">Product</p>', unsafe_allow_html=True)
    product_sel = st.radio(
        "Product", ["All"] + PRODUCT_ORDER,
        index=0, key="s_product", label_visibility="collapsed",
    )

    st.markdown("---")
    st.markdown('<p class="section-label">Chart date range</p>', unsafe_allow_html=True)
    today_d = date.today()
    date_range = st.date_input(
        "Range",
        value=(date(2025, 1, 1), today_d),
        min_value=date(2023, 1, 1),
        max_value=today_d,
        key="s_daterange",
        label_visibility="collapsed",
    )
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        chart_start, chart_end = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
    else:
        chart_start = pd.Timestamp(date(2025, 1, 1))
        chart_end   = pd.Timestamp(today_d)

    st.markdown("---")
    if st.button("↻ Force refresh", use_container_width=True, key="s_btn"):
        st.cache_data.clear()
        st.rerun()
    st.caption("Auto-refreshes every 5 min")

# ── Load all data ──────────────────────────────────────────────────────────────
shopify_df   = load_all_shopify()            # UAE + KSA + USA, Sep-25 onward
recharge_df  = apply_fx(load_raw_data(), get_fx())   # UAE + KSA + USA active subs
cancel_df    = load_recharge_cancellations()          # all Recharge rows for churn calc
monthly_vals = load_monthly_sales_raw()               # Monthly Sales tab (full history)
kpis         = load_dashboard_kpis()                  # pre-computed globals
ub_data      = load_user_base_current()               # user base from Monthly User Base tab

# ── Apply country + product filters ───────────────────────────────────────────
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

# ── Date helpers ───────────────────────────────────────────────────────────────
today          = pd.Timestamp.today().normalize()
month_start    = today.replace(day=1)
prev_m_end     = month_start - timedelta(days=1)
prev_m_start   = prev_m_end.replace(day=1)
days_elapsed   = today.day
days_in_month  = calendar.monthrange(today.year, today.month)[1]
days_remaining = max(1, days_in_month - days_elapsed + 1)

# ── KPI computations ───────────────────────────────────────────────────────────

# Sales (Shopify — live)
today_sales  = int(sh[sh["date"] == today]["qty"].sum())
mtd_sales    = int(sh[sh["date"] >= month_start]["qty"].sum())
mom_cutoff   = prev_m_start + timedelta(days=days_elapsed - 1)
mom_sales    = int(sh[(sh["date"] >= prev_m_start) & (sh["date"] <= mom_cutoff)]["qty"].sum())
daily_avg    = mtd_sales / days_elapsed if days_elapsed > 0 else 0
mom_delta    = mtd_sales - mom_sales

try:
    monthly_target = int(str(kpis.get("monthly_target", "0")).replace(",", ""))
except ValueError:
    monthly_target = 0
avg_needed = max(0.0, (monthly_target - mtd_sales) / days_remaining)

# Total Active Users — from Monthly User Base tab (subs + owners, owners never churn)
# For product filter: fall back to Recharge active subs only (ownership not split by product)
if product_sel == "All":
    if country_sel == "All":
        total_users = ub_data.get("global", 0)
        users_note  = ""
    elif country_sel in ub_data:
        total_users = ub_data.get(country_sel, 0)
        users_note  = ""
    else:
        # USA not tracked in Monthly User Base tab; compute from Recharge
        total_users = rc["subscription_id"].nunique()
        users_note  = " (subs only)"
else:
    total_users = rc["subscription_id"].nunique()
    users_note  = " (subs only)"

# ARR (Recharge active subscriptions in USD)
total_arr = rc["arr_usd"].sum()

# Cancellation rate — Dashboard Summary for global/all-product view (exact match)
if country_sel == "All" and product_sel == "All":
    raw_rate = kpis.get("cancellation_rate", "0%").replace("%", "").strip()
    try:    cancel_rate_str = f"{float(raw_rate):.2f}%"
    except: cancel_rate_str = kpis.get("cancellation_rate", "—")
else:
    cc_machine  = cc[cc["category"] == "Machine"]
    mtd_cancels = int(cc_machine[
        cc_machine["is_true_cancel"] &
        (cc_machine["cancelled_at_dt"] >= month_start) &
        (cc_machine["cancelled_at_dt"] <= today)
    ].shape[0])
    active_mach = rc[rc["category"] == "Machine"]["subscription_id"].nunique()
    cr = mtd_cancels / active_mach if active_mach > 0 else 0
    cancel_rate_str = f"{cr:.2%}"

# CAC from Dashboard Summary (global baseline; per-country spend not yet populated)
cac_raw = kpis.get("cac", "—")
cac_display = cac_raw if (country_sel == "All" and product_sel == "All") else f"{cac_raw}*"

# ── Monthly chart data ─────────────────────────────────────────────────────────
# Strategy:
#   Pre-Sep-25 (historical, hard-coded): use Monthly Sales tab
#   Sep-25 onward (live): use Shopify (fixes KSA tab formula gap from Jan-26)
LIVE_CUTOFF = pd.Timestamp("2025-09-01")

# 1. Historical slice from Monthly Sales tab (months before LIVE_CUTOFF)
months_raw, sales_raw = monthly_sales_series(monthly_vals, country_sel, product_sel)
hist_months, hist_vals = [], []
for m_label, v in zip(months_raw, sales_raw):
    try:    m_date = pd.to_datetime(m_label, format="%b-%y")
    except: continue
    if m_date < LIVE_CUTOFF:
        hist_months.append(m_label)
        hist_vals.append(v)

# 2. Live slice from Shopify (Sep-25 to chart_end)
live_agg = (
    sh[(sh["date"] >= LIVE_CUTOFF) & (sh["date"].notna())]
    .assign(month_period=lambda d: d["date"].dt.to_period("M"))
    .groupby("month_period")["qty"].sum()
    .reset_index()
)
live_agg["label"] = live_agg["month_period"].dt.strftime("%b-%y")
live_agg["month_dt"] = live_agg["month_period"].dt.to_timestamp()
live_dict = live_agg.set_index("label")["qty"].to_dict()

# Fill any live months that Shopify has no rows for (no orders = 0)
from dateutil.relativedelta import relativedelta  # noqa: E402
cur = LIVE_CUTOFF
while cur <= today:
    lbl = cur.strftime("%b-%y")
    if lbl not in live_dict:
        live_dict[lbl] = 0
    cur += relativedelta(months=1)

live_months = sorted(live_dict.keys(), key=lambda x: pd.to_datetime(x, format="%b-%y"))
live_vals   = [live_dict[m] for m in live_months]

# 3. Combine and apply chart date range filter
all_labels = hist_months + live_months
all_vals   = hist_vals   + live_vals

chart_months_f, chart_vals_f = [], []
for m_label, v in zip(all_labels, all_vals):
    try:    m_date = pd.to_datetime(m_label, format="%b-%y")
    except: continue
    if chart_start <= m_date <= chart_end:
        chart_months_f.append(m_label)
        chart_vals_f.append(v)

# ── Header ─────────────────────────────────────────────────────────────────────
parts   = [p for p in [country_sel if country_sel != "All" else "",
                        product_sel if product_sel != "All" else ""] if p]
subtitle = " · ".join(parts) if parts else "Global · All Products"

col_hdr, col_meta = st.columns([3, 1])
with col_hdr:
    st.title("📈 Sales Dashboard")
with col_meta:
    st.markdown("<br>", unsafe_allow_html=True)
    st.caption(f"**{subtitle}**  ·  {today.strftime('%d %b %Y')}")

st.markdown("---")

# ── KPI row ─────────────────────────────────────────────────────────────────────
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Today's Sales",      f"{today_sales:,}")
k2.metric(f"Active Users{users_note}", f"{total_users:,}")
k3.metric("ARR",                fmt_usd(total_arr))
k4.metric("Cancellation Rate",  cancel_rate_str)
k5.metric("CAC",                cac_display,
          help="* Filtered CAC: per-country ad spend not yet populated in sheet")
k6.metric("Monthly Target",     f"{monthly_target:,}")

st.markdown("---")

# ── Row 1: Monthly Sales bar  +  secondary KPI cards ──────────────────────────
c_chart, c_kpis = st.columns([3, 2])

with c_chart:
    # Bar colour: lighter teal for current (partial) month
    bar_colors = []
    for ml in chart_months_f:
        try:
            m = pd.to_datetime(ml, format="%b-%y")
            is_cur = (m.year == today.year and m.month == today.month)
        except Exception:
            is_cur = False
        bar_colors.append("#7dd3fc" if is_cur else "#0ea5e9")

    fig_m = go.Figure(go.Bar(
        x=chart_months_f, y=chart_vals_f,
        marker_color=bar_colors,
        text=[f"{v:,}" if v > 0 else "" for v in chart_vals_f],
        textposition="outside",
        textfont=dict(size=9, color="#94a3b8"),
        hovertemplate="<b>%{x}</b><br>Sales: %{y:,}<extra></extra>",
    ))
    fig_m.update_layout(
        title=dict(text="Monthly Sales", x=0, font=dict(size=13, color="#e2e5f0")),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        height=340,
        xaxis=dict(tickangle=-45, tickfont=dict(size=9, color="#94a3b8"),
                   showgrid=False, zeroline=False, linecolor="rgba(0,0,0,0)"),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.06)",
                   zeroline=False, tickfont=dict(size=9, color="#94a3b8")),
        margin=dict(t=44, b=8, l=4, r=8),
        bargap=0.3,
    )
    st.plotly_chart(fig_m, use_container_width=True)

with c_kpis:
    st.markdown("<br>", unsafe_allow_html=True)
    ka, kb = st.columns(2)
    ka.metric("MTD Sales",     f"{mtd_sales:,}")
    kb.metric("MoM Sales",     f"{mom_sales:,}",
              delta=f"{mom_delta:+,}",
              delta_color="normal")
    st.markdown("<br>", unsafe_allow_html=True)
    kc, kd = st.columns(2)
    kc.metric("Daily Average", f"{daily_avg:.1f}")
    kd.metric("Avg. Needed",   f"{avg_needed:.1f}",
              help=f"Sales/day to reach {monthly_target:,} monthly target")

# ── Row 2: Daily last-30-days  +  MTD split donut ─────────────────────────────
c_daily, c_donut = st.columns([3, 2])

with c_daily:
    thirty_ago = today - timedelta(days=29)
    daily_agg  = (
        sh[sh["date"] >= thirty_ago]
        .groupby("date")["qty"].sum()
        .reset_index()
        .rename(columns={"qty": "sales"})
    )
    all_dates  = pd.date_range(thirty_ago, today, freq="D").normalize()
    daily_full = (
        pd.DataFrame({"date": all_dates})
        .merge(daily_agg, on="date", how="left")
        .fillna({"sales": 0})
    )
    daily_full["sales"] = daily_full["sales"].astype(int)
    daily_full["label"] = daily_full["date"].dt.strftime("%-d %b")
    # Highlight today
    daily_full["color"] = daily_full["date"].apply(
        lambda d: "#7dd3fc" if d == today else "#6366f1"
    )

    fig_d = go.Figure(go.Bar(
        x=daily_full["label"], y=daily_full["sales"],
        marker_color=daily_full["color"].tolist(),
        text=daily_full["sales"].apply(lambda v: f"{v}" if v > 0 else ""),
        textposition="outside",
        textfont=dict(size=8, color="#94a3b8"),
        hovertemplate="<b>%{x}</b><br>Sales: %{y:,}<extra></extra>",
    ))
    fig_d.update_layout(
        title=dict(text="Sales — Last 30 Days", x=0, font=dict(size=13, color="#e2e5f0")),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        height=340,
        xaxis=dict(tickangle=-45, tickfont=dict(size=8, color="#94a3b8"),
                   showgrid=False, zeroline=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.06)",
                   zeroline=False, tickfont=dict(size=9, color="#94a3b8")),
        margin=dict(t=44, b=8, l=4, r=8),
        bargap=0.25,
    )
    st.plotly_chart(fig_d, use_container_width=True)

with c_donut:
    mtd_sh = sh[sh["date"] >= month_start]
    if not mtd_sh.empty:
        by_prod = (
            mtd_sh.groupby("product")["qty"].sum().reset_index()
            .rename(columns={"qty": "sales"})
        )
        by_prod["label"] = by_prod["product"].apply(
            lambda p: p if p in PRODUCT_ORDER else "Others"
        )
        donut_df = (
            by_prod.groupby("label")["sales"].sum().reset_index()
            .query("sales > 0")
        )
        fig_pie = px.pie(
            donut_df, values="sales", names="label",
            color="label", color_discrete_map=PRODUCT_COLOR,
            hole=0.58, title="MTD Sales Split",
        )
        fig_pie.update_traces(
            texttemplate="<b>%{label}</b><br>%{value:,}",
            textposition="outside",
            hovertemplate="%{label}: %{value:,} (%{percent:.1%})<extra></extra>",
            textfont_size=11,
        )
        fig_pie.update_layout(
            height=340, showlegend=False,
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(t=44, b=8, l=8, r=8),
            title=dict(x=0, font=dict(size=13, color="#e2e5f0")),
        )
        st.plotly_chart(fig_pie, use_container_width=True)
    else:
        st.info("No sales data for this filter in the current month.")

# ── Footer note ────────────────────────────────────────────────────────────────
st.markdown("---")
footnotes = []
if country_sel == "KSA":
    footnotes.append(
        "**KSA note:** Monthly Sales tab formula stopped updating from Jan-26. "
        "Monthly chart uses live Shopify data for Sep-25 onward."
    )
if cac_display.endswith("*"):
    footnotes.append(
        "**CAC:** Per-country marketing spend not yet populated in the sheet. "
        "Showing global CAC."
    )
if users_note:
    footnotes.append(
        "**Active Users:** Product-level filter shows active subscriptions only "
        "(ownership customers not broken down by product in source data)."
    )
if footnotes:
    with st.expander("ℹ️  Data notes", expanded=False):
        for fn in footnotes:
            st.markdown(f"- {fn}")
