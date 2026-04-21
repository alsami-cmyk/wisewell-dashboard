"""
Sales Dashboard — all metrics computed from raw Recharge / Shopify / Offline tabs.
No pre-calculated spreadsheet tabs used for live metrics.
Pre-Sep-2025 history blended in from hardcoded Monthly Sales / Monthly User Base tabs.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from utils import (
    MARKET_COLOR, PRODUCT_COLOR, PRODUCT_ORDER,
    fmt_usd, get_fx, get_load_diagnostics,
    get_all_machine_sales, get_monthly_sales_blended,
    get_active_subscriptions, get_active_ownership,
    compute_cancellation_rate, load_marketing_spend, load_recharge_full,
    get_monthly_user_base_blended, load_shopify_store_analytics,
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

# ── Load & warm caches ─────────────────────────────────────────────────────────
try:
    # Trigger parallel fetch by calling a loader (all tabs fetched in one batch)
    rc_full     = load_recharge_full()
    mkt         = load_marketing_spend()
    fx          = get_fx()
    errors, fetch_time = get_load_diagnostics()
    if errors:
        for tab, msg in errors.items():
            st.warning(f"⚠️  Could not load **{tab}**: {msg}")
except Exception as exc:
    st.error(f"**Data load failed** — Google Sheets API error. Try refreshing.\n\n`{exc}`")
    st.stop()

# Apply FX to Recharge
if not rc_full.empty and "arr_local" in rc_full.columns:
    rc_full = rc_full.copy()
    rc_full["arr_usd"] = rc_full.apply(
        lambda r: r["arr_local"] * fx.get(str(r["currency"]), 1.0), axis=1
    )
else:
    if not rc_full.empty:
        rc_full["arr_usd"] = 0.0

# ── Date helpers ───────────────────────────────────────────────────────────────
today         = pd.Timestamp.today().normalize()
month_start   = today.replace(day=1)
prev_m_end    = month_start - timedelta(days=1)
prev_m_start  = prev_m_end.replace(day=1)
days_elapsed  = today.day
days_in_month = calendar.monthrange(today.year, today.month)[1]

# ── Helper: filter a DataFrame by the sidebar market / product selectors ───────
def _mkt(df: pd.DataFrame, mkt_col: str = "market") -> pd.DataFrame:
    if country_sel not in ("All", ""):
        df = df[df[mkt_col] == country_sel]
    return df

def _prod(df: pd.DataFrame, prod_col: str = "product") -> pd.DataFrame:
    if product_sel not in ("All", ""):
        df = df[df[prod_col] == product_sel]
    return df

def _filter(df: pd.DataFrame, mkt_col="market", prod_col="product") -> pd.DataFrame:
    return _prod(_mkt(df, mkt_col), prod_col)


# ═══════════════════════════════════════════════════════════════════════════════
# SALES KPIs
# ═══════════════════════════════════════════════════════════════════════════════

# All sales for this month and prior (for today, MTD, MoM)
all_sales_live = get_all_machine_sales(
    start_dt=prev_m_start,
    end_dt=today,
)
sales_filtered = _filter(all_sales_live)

today_sales = int(sales_filtered[sales_filtered["date"] == today]["qty"].sum())
mtd_sales   = int(sales_filtered[sales_filtered["date"] >= month_start]["qty"].sum())

# MoM: same number of days into the prior month
prev_mom_end  = prev_m_start + timedelta(days=days_elapsed - 1)
mom_sales     = int(sales_filtered[
    (sales_filtered["date"] >= prev_m_start) &
    (sales_filtered["date"] <= prev_mom_end)
]["qty"].sum())

mom_delta = mtd_sales - mom_sales
daily_avg = mtd_sales / days_elapsed if days_elapsed > 0 else 0.0

# ── Active Users (subscriptions + ownership) ──────────────────────────────────
_mkt_filter = country_sel if country_sel not in ("All", "") else None
_prd_filter = product_sel if product_sel not in ("All", "") else None

active_subs = get_active_subscriptions(as_of=today)
active_own  = get_active_ownership(as_of=today)

def _sum_active(df: pd.DataFrame) -> int:
    d = df.copy()
    if _mkt_filter:
        d = d[d["market"] == _mkt_filter]
    if _prd_filter:
        d = d[d["product"] == _prd_filter]
    return int(d["qty"].sum())

total_subs   = _sum_active(active_subs)
total_owners = _sum_active(active_own)
total_users  = total_subs + total_owners

# ── ARR ────────────────────────────────────────────────────────────────────────
rc_active = rc_full[rc_full["status"] == "ACTIVE"].copy() if not rc_full.empty else pd.DataFrame()
if not rc_active.empty:
    if _mkt_filter:
        rc_active = rc_active[rc_active["market"] == _mkt_filter]
    if _prd_filter:
        rc_active = rc_active[rc_active["product"] == _prd_filter]
total_arr = float(rc_active["arr_usd"].sum()) if "arr_usd" in rc_active.columns else 0.0

# ── New machine customers this month (subs + ownership) ───────────────────────
sales_mtd_only = _filter(all_sales_live[all_sales_live["date"] >= month_start])
new_customers_mtd = int(sales_mtd_only["qty"].sum())

# ── CAC ────────────────────────────────────────────────────────────────────────
cur_month_dt  = pd.Timestamp(today.year, today.month, 1)
spend_row     = mkt[mkt["month_dt"] == cur_month_dt] if not mkt.empty else pd.DataFrame()
if not spend_row.empty:
    row = spend_row.iloc[0]
    spend = float(
        row.get("uae_usd", 0) if country_sel == "UAE"
        else row.get("ksa_usd", 0) if country_sel == "KSA"
        else 0.0 if country_sel == "USA"
        else row.get("total_usd", 0)
    )
    cac         = spend / new_customers_mtd if (new_customers_mtd > 0 and spend > 0) else 0.0
    cac_display = f"${cac:,.0f}" if cac > 0 else "—"
    cac_note    = "" if spend > 0 else " (no spend data)"
else:
    spend = 0.0
    cac_display = "—"
    cac_note    = " (spend tab empty)"

# ── Cancellation rate ─────────────────────────────────────────────────────────
cr_data = compute_cancellation_rate(
    as_of=today,
    market=_mkt_filter,
    product=_prd_filter,
)
cancel_rate_str = f"{cr_data['rate']:.2%}"


# ═══════════════════════════════════════════════════════════════════════════════
# MONTHLY SALES CHART DATA
# ═══════════════════════════════════════════════════════════════════════════════
monthly_blended = get_monthly_sales_blended()

# Apply filters
chart_monthly = _filter(monthly_blended)

# Aggregate by month
chart_monthly = (
    chart_monthly
    .groupby("month_dt", as_index=False)["qty"].sum()
    .sort_values("month_dt")
)
chart_monthly["label"] = chart_monthly["month_dt"].dt.strftime("%b-%y")

# Apply date range from sidebar
mask = (
    (chart_monthly["month_dt"] >= chart_start) &
    (chart_monthly["month_dt"] <= chart_end)
)
chart_data   = chart_monthly[mask].copy()
chart_months = chart_data["label"].tolist()
chart_vals   = chart_data["qty"].astype(int).tolist()


# ═══════════════════════════════════════════════════════════════════════════════
# HEADER
# ═══════════════════════════════════════════════════════════════════════════════
parts    = [p for p in [
    country_sel if country_sel != "All" else "",
    product_sel if product_sel != "All" else "",
] if p]
subtitle = " · ".join(parts) if parts else "Global · All Products"

col_hdr, col_meta = st.columns([3, 1])
with col_hdr:
    st.title("📈 Sales Dashboard")
with col_meta:
    st.markdown("<br>", unsafe_allow_html=True)
    st.caption(f"**{subtitle}**  ·  {today.strftime('%d %b %Y')}")

st.markdown("---")


# ═══════════════════════════════════════════════════════════════════════════════
# KPI ROW
# ═══════════════════════════════════════════════════════════════════════════════
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Today's Sales",     f"{today_sales:,}")
k2.metric("Active Users",      f"{total_users:,}",
          help=f"Machine subscribers ({total_subs:,}) + ownership customers ({total_owners:,})")
k3.metric("ARR",               fmt_usd(total_arr))
k4.metric("Cancellation Rate", cancel_rate_str,
          help="Extrapolated monthly rate: (MTD true cancels ÷ days elapsed × days in month) ÷ active machine subs at prior month-end")
k5.metric("CAC" + cac_note,    cac_display,
          help="Marketing Spend ÷ New Machine Customers (subs + ownership) MTD")
k6.metric("New This Month",    f"{new_customers_mtd:,}",
          help="New machine subscriptions + ownership orders MTD")

with st.expander("🔍 Cancellation rate breakdown", expanded=False):
    cr = cr_data
    st.markdown(
        f"| | |\n|---|---|\n"
        f"| MTD true cancellations | **{cr['mtd_cancels']}** |\n"
        f"| Days elapsed / days in month | **{cr['days_elapsed']} / {cr['days_in_month']}** |\n"
        f"| Extrapolated full-month cancels | **{cr['extrapolated_cancels']:.1f}** |\n"
        f"| Active machine subs at prior month-end | **{cr['active_at_start']:,}** |\n"
        f"| **Cancellation rate** | **{cr['rate']:.4%}** |"
    )

st.markdown("---")


# ═══════════════════════════════════════════════════════════════════════════════
# ROW 2: Monthly bar chart + secondary KPIs
# ═══════════════════════════════════════════════════════════════════════════════
c_chart, c_kpis = st.columns([3, 2])

with c_chart:
    bar_colors = []
    for ml in chart_months:
        try:
            m      = pd.to_datetime(ml, format="%b-%y")
            is_cur = (m.year == today.year and m.month == today.month)
        except Exception:
            is_cur = False
        bar_colors.append("#7dd3fc" if is_cur else "#0ea5e9")

    fig_m = go.Figure(go.Bar(
        x=chart_months, y=chart_vals,
        marker_color=bar_colors,
        text=[f"{v:,}" if v > 0 else "" for v in chart_vals],
        textposition="outside",
        textfont=dict(size=9, color="#94a3b8"),
        hovertemplate="<b>%{x}</b><br>%{y:,} units<extra></extra>",
    ))
    fig_m.update_layout(
        title=dict(text="Monthly Sales (Subscriptions + Ownership)", x=0,
                   font=dict(size=13, color="#e2e5f0")),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        height=340,
        xaxis=dict(tickangle=-45, tickfont=dict(size=9, color="#94a3b8"),
                   showgrid=False, zeroline=False, linecolor="rgba(0,0,0,0)"),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.06)",
                   zeroline=False, tickfont=dict(size=9, color="#94a3b8")),
        margin=dict(t=56, b=8, l=4, r=8), bargap=0.3,
    )
    st.plotly_chart(fig_m, use_container_width=True)

with c_kpis:
    st.markdown("<br>", unsafe_allow_html=True)
    ka, kb = st.columns(2)
    ka.metric("MTD Sales", f"{mtd_sales:,}")
    kb.metric("vs Prior MoM", f"{mom_sales:,}",
              delta=f"{mom_delta:+,}", delta_color="normal")
    st.markdown("<br>", unsafe_allow_html=True)
    kc, kd = st.columns(2)
    kc.metric("Daily Avg", f"{daily_avg:.1f}")
    kd.metric("Days Elapsed", f"{days_elapsed} / {days_in_month}")

st.markdown("---")


# ═══════════════════════════════════════════════════════════════════════════════
# ROW 3: Daily last-30 days + MTD Product Donut
# ═══════════════════════════════════════════════════════════════════════════════
c_daily, c_donut = st.columns([3, 2])

with c_daily:
    thirty_ago    = today - timedelta(days=29)
    sales_30d     = get_all_machine_sales(start_dt=thirty_ago, end_dt=today)
    sales_30d_f   = _filter(sales_30d)
    daily_agg     = (
        sales_30d_f.groupby("date")["qty"].sum().reset_index().rename(columns={"qty": "sales"})
    )
    all_dates     = pd.date_range(thirty_ago, today, freq="D").normalize()
    daily_full    = (
        pd.DataFrame({"date": all_dates})
        .merge(daily_agg, on="date", how="left")
        .fillna({"sales": 0})
    )
    daily_full["sales"] = daily_full["sales"].astype(int)
    daily_full["label"] = daily_full["date"].dt.strftime("%-d %b")
    daily_full["color"] = daily_full["date"].apply(
        lambda d: "#7dd3fc" if d == today else "#0ea5e9"
    )

    fig_d = go.Figure(go.Bar(
        x=daily_full["label"], y=daily_full["sales"],
        marker_color=daily_full["color"].tolist(),
        text=daily_full["sales"].apply(lambda v: str(v) if v > 0 else ""),
        textposition="outside",
        textfont=dict(size=8, color="#94a3b8"),
        hovertemplate="<b>%{x}</b><br>%{y:,} units<extra></extra>",
    ))
    fig_d.update_layout(
        title=dict(text="Sales — Last 30 Days", x=0, font=dict(size=13, color="#e2e5f0")),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        height=340,
        xaxis=dict(tickangle=-45, tickfont=dict(size=8, color="#94a3b8"),
                   showgrid=False, zeroline=False),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.06)",
                   zeroline=False, tickfont=dict(size=9, color="#94a3b8")),
        margin=dict(t=44, b=8, l=4, r=8), bargap=0.25,
    )
    st.plotly_chart(fig_d, use_container_width=True)

with c_donut:
    sales_mtd_prod = _filter(all_sales_live[all_sales_live["date"] >= month_start])
    if not sales_mtd_prod.empty:
        by_prod = (
            sales_mtd_prod.groupby("product")["qty"].sum().reset_index()
            .rename(columns={"qty": "sales"})
            .query("sales > 0")
        )
        fig_pie = px.pie(
            by_prod, values="sales", names="product",
            color="product", color_discrete_map=PRODUCT_COLOR,
            hole=0.58, title="MTD Sales by Product",
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
        st.info("No sales data in the current month for this filter.")

st.markdown("---")


# ═══════════════════════════════════════════════════════════════════════════════
# ROW 4: User Base Over Time — stacked by product, with product selector
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown(
    '<p style="font-size:13px; font-weight:600; color:#e2e5f0; margin-bottom:0.5rem;">'
    "Total User Base Over Time</p>",
    unsafe_allow_html=True,
)

# Product selector for this chart
ub_col_sel, _ = st.columns([3, 1])
with ub_col_sel:
    ub_products = st.multiselect(
        "Products",
        options=PRODUCT_ORDER,
        default=PRODUCT_ORDER,
        key="ub_prod_sel",
        label_visibility="collapsed",
    )

ub_blended = get_monthly_user_base_blended()
if not ub_blended.empty:
    ub_chart = ub_blended.copy()
    if _mkt_filter:
        ub_chart = ub_chart[ub_chart["market"] == _mkt_filter]
    else:
        ub_chart = ub_chart.groupby(["month_dt", "product"], as_index=False)["total"].sum()

    # Apply selected products
    ub_chart = ub_chart[ub_chart["product"].isin(ub_products)] if ub_products else ub_chart
    # Apply date range
    ub_chart = ub_chart[
        (ub_chart["month_dt"] >= chart_start) & (ub_chart["month_dt"] <= chart_end)
    ]

    if not ub_chart.empty:
        ub_chart["label"] = ub_chart["month_dt"].dt.strftime("%b-%y")
        months_sorted = sorted(ub_chart["month_dt"].unique())
        x_labels = [pd.Timestamp(m).strftime("%b-%y") for m in months_sorted]

        fig_ub = go.Figure()
        for prod in [p for p in PRODUCT_ORDER if p in ub_chart["product"].unique()]:
            prod_data = ub_chart[ub_chart["product"] == prod]
            prod_dict = dict(zip(prod_data["month_dt"], prod_data["total"]))
            y_vals    = [int(prod_dict.get(m, 0)) for m in months_sorted]
            fig_ub.add_trace(go.Scatter(
                x=x_labels, y=y_vals,
                name=prod,
                mode="lines+markers",
                stackgroup="one",
                line=dict(color=PRODUCT_COLOR.get(prod, "#94a3b8"), width=2),
                marker=dict(size=4),
                hovertemplate=f"<b>%{{x}}</b><br>{prod}: %{{y:,}}<extra></extra>",
            ))

        fig_ub.update_layout(
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            height=360,
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                font=dict(size=10, color="#94a3b8"),
            ),
            xaxis=dict(tickangle=-45, tickfont=dict(size=9, color="#94a3b8"),
                       showgrid=False, zeroline=False),
            yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.06)",
                       zeroline=False, tickfont=dict(size=9, color="#94a3b8")),
            margin=dict(t=20, b=8, l=4, r=8),
            hovermode="x unified",
        )
        st.plotly_chart(fig_ub, use_container_width=True)
    else:
        st.info("No user base data for the selected filters and date range.")
else:
    st.info("User base data loading…")

# ═══════════════════════════════════════════════════════════════════════════════
# ROW 5: Online Store Performance (Shopify)
# ═══════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown(
    '<p style="font-size:13px; font-weight:600; color:#e2e5f0; margin-bottom:0.5rem;">'
    "🛒 Online Store Performance (Shopify · MTD)</p>",
    unsafe_allow_html=True,
)

shopify_df = load_shopify_store_analytics()

if shopify_df.empty:
    st.info(
        "**Shopify not connected yet.** "
        "Add `SHOPIFY_STORE_UAE` / `SHOPIFY_TOKEN_UAE` (and KSA, USA equivalents) "
        "to Streamlit Cloud secrets to enable this section."
    )
else:
    # Apply country filter
    shop = shopify_df.copy()
    if _mkt_filter:
        shop = shop[shop["market"] == _mkt_filter]

    if shop.empty:
        st.info("No Shopify data for the selected market.")
    else:
        total_orders     = int(shop["orders"].sum())
        total_rev        = float(shop["revenue_local"].sum())
        sessions_avail   = shop["sessions"].notna().any()
        total_sessions   = int(shop["sessions"].fillna(0).sum()) if sessions_avail else None
        avg_conv         = float(shop["conversion_rate"].mean()) if sessions_avail and shop["conversion_rate"].notna().any() else None

        # AOV: weighted average across markets
        aov_orders = shop[shop["orders"] > 0]
        avg_aov_usd = 0.0
        if not aov_orders.empty:
            currency_map = {"UAE": "AED", "KSA": "SAR", "USA": "USD"}
            weighted_rev = sum(
                row["revenue_local"] * fx.get(currency_map.get(row["market"], "USD"), 1.0)
                for _, row in aov_orders.iterrows()
            )
            avg_aov_usd = weighted_rev / total_orders if total_orders > 0 else 0.0

        sh1, sh2, sh3, sh4 = st.columns(4)
        sh1.metric(
            "Sessions MTD",
            f"{total_sessions:,}" if total_sessions is not None else "—",
            help=(
                "Online store visits. "
                "Requires Shopify Advanced / Plus plan — shows '—' on lower plans."
            ),
        )
        sh2.metric(
            "Conversion Rate",
            f"{avg_conv:.2%}" if avg_conv is not None else "—",
            help="Sessions that resulted in an order. Requires Advanced / Plus plan.",
        )
        sh3.metric(
            "Shopify Orders MTD",
            f"{total_orders:,}",
            help="Orders placed via Online Store (all plans)",
        )
        sh4.metric(
            "Avg Order Value",
            fmt_usd(avg_aov_usd) if avg_aov_usd > 0 else "—",
            help="Revenue ÷ orders, converted to USD at current FX",
        )

        # Per-market breakdown when viewing Global
        if not _mkt_filter and len(shop) > 1:
            with st.expander("Market breakdown", expanded=False):
                cols = st.columns(len(shop))
                for col, (_, row) in zip(cols, shop.iterrows()):
                    col.markdown(f"**{row['market']}**")
                    col.metric("Orders",     f"{int(row['orders']):,}")
                    col.metric("Sessions",   f"{int(row['sessions']):,}" if pd.notna(row["sessions"]) else "—")
                    col.metric("Conv. Rate", f"{row['conversion_rate']:.2%}" if pd.notna(row["conversion_rate"]) else "—")

        if not sessions_avail:
            st.caption(
                "ℹ️  Sessions and conversion rate require **Shopify Advanced or Plus** for API access. "
                "Order count and AOV above are available on all plans."
            )


# ── Footer ─────────────────────────────────────────────────────────────────────
st.markdown("---")
with st.expander("ℹ️  Data notes", expanded=False):
    st.markdown(
        "- **Sales** = subscriptions (Recharge `created_at` × `quantity`) "
        "+ ownership (Shopify unit columns × value) + Offline orders.\n"
        "- **Active Users** = Recharge `ACTIVE` subscriptions × quantity "
        "+ ownership users (seed Aug-2025 + Shopify/Offline additions − Returns).\n"
        "- **Cancellation rate** = extrapolated MTD true cancels "
        "÷ active machine subscribers at last day of prior month.\n"
        "- **Pre-Sep-2025** monthly sales from hardcoded spreadsheet data (final truth).\n"
        f"- Data synced in **{fetch_time:.1f}s** ({len([k for k,v in errors.items() if v])} errors)."
    )
