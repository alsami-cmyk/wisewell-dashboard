"""Sales Dashboard page — metrics from Recharge + Shopify + Marketing Spend."""

from __future__ import annotations

import calendar
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dateutil.relativedelta import relativedelta

from utils import (
    PRODUCT_COLOR, PRODUCT_ORDER,
    fmt_usd, get_fx, get_load_diagnostics,
    load_recharge_full, load_shopify_all, load_marketing_spend,
    load_user_base_series,
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

# ── Load source data (cached 5 min, parallel fetch) ──────────────────────────
try:
    rc_full = load_recharge_full()
    sh_all  = load_shopify_all()
    mkt     = load_marketing_spend()
    ub      = load_user_base_series()
    fx      = get_fx()

    errors, fetch_time = get_load_diagnostics()
    if errors:
        for tab, msg in errors.items():
            st.warning(f"⚠️  Could not load **{tab}**: {msg}")
except Exception as exc:
    st.error(
        f"**Data load failed** — Google Sheets API error. "
        f"Try refreshing.\n\n`{exc}`"
    )
    st.stop()

# Apply FX
if not rc_full.empty:
    rc_full = rc_full.copy()
    rc_full["arr_usd"] = rc_full.apply(
        lambda r: r["arr_local"] * fx.get(str(r["currency"]), 1.0), axis=1
    )
else:
    rc_full["arr_usd"] = 0.0

# ── Apply filters ─────────────────────────────────────────────────────────────
def _frc(df):
    d = df.copy()
    if country_sel != "All": d = d[d["market"] == country_sel]
    if product_sel != "All": d = d[d["product"] == product_sel]
    return d

def _fsh(df):
    d = df.copy()
    if country_sel != "All": d = d[d["country"] == country_sel]
    if product_sel != "All": d = d[d["product"] == product_sel]
    return d

rc = _frc(rc_full)
sh = _fsh(sh_all)

# ── Date helpers ──────────────────────────────────────────────────────────────
today          = pd.Timestamp.today().normalize()
month_start    = today.replace(day=1)
prev_m_end     = month_start - timedelta(days=1)
prev_m_start   = prev_m_end.replace(day=1)
days_elapsed   = today.day
days_in_month  = calendar.monthrange(today.year, today.month)[1]
days_remaining = max(1, days_in_month - days_elapsed + 1)

# ── Sales KPIs ────────────────────────────────────────────────────────────────
today_sales = int(sh[sh["date"] == today]["qty"].sum())
mtd_sales   = int(sh[sh["date"] >= month_start]["qty"].sum())
mom_cutoff  = prev_m_start + timedelta(days=days_elapsed - 1)
mom_sales   = int(
    sh[(sh["date"] >= prev_m_start) & (sh["date"] <= mom_cutoff)]["qty"].sum()
)
mom_delta  = mtd_sales - mom_sales
daily_avg  = mtd_sales / days_elapsed if days_elapsed > 0 else 0.0

# ── Active Users (from Monthly User Base tab) ────────────────────────────────
# User Base = cumulative running total of active subs + owners, maintained in the
# Monthly User Base calculated tab.  This is the authoritative source.
active_rc = rc[rc["status"] == "ACTIVE"]

if not ub.empty:
    if country_sel == "UAE":
        total_users = int(ub["uae"].iloc[-1])
    elif country_sel == "KSA":
        total_users = int(ub["ksa"].iloc[-1])
    elif country_sel == "USA":
        # USA not tracked in Monthly User Base; fall back to Recharge active count
        total_users = active_rc[active_rc["category"] == "Machine"]["subscription_id"].nunique()
    else:
        total_users = int(ub["global"].iloc[-1])
    users_note = ""
else:
    # Fallback: Recharge active machine subs only
    total_users = active_rc[active_rc["category"] == "Machine"]["subscription_id"].nunique()
    users_note = " (subs only)"

# ── ARR ───────────────────────────────────────────────────────────────────────
total_arr = float(active_rc["arr_usd"].sum())

# ── New machine customers this month ─────────────────────────────────────────
new_subs_rc = rc_full[
    (rc_full["category"] == "Machine")
    & rc_full["created_at_dt"].notna()
    & (rc_full["created_at_dt"] >= month_start)
    & (rc_full["created_at_dt"] <= today)
].copy()
if country_sel != "All":
    new_subs_rc = new_subs_rc[new_subs_rc["market"] == country_sel]
if product_sel != "All":
    new_subs_rc = new_subs_rc[new_subs_rc["product"] == product_sel]

new_own_sh = sh_all[
    sh_all["is_ownership"]
    & (sh_all["date"] >= month_start)
    & (sh_all["date"] <= today)
].copy()
if country_sel != "All":
    new_own_sh = new_own_sh[new_own_sh["country"] == country_sel]
if product_sel != "All":
    new_own_sh = new_own_sh[new_own_sh["product"] == product_sel]

new_customers_mtd = len(new_subs_rc) + len(new_own_sh)

# ── CAC ───────────────────────────────────────────────────────────────────────
cur_month_dt = pd.Timestamp(today.year, today.month, 1)
spend_row    = mkt[mkt["month_dt"] == cur_month_dt] if not mkt.empty else pd.DataFrame()

if not spend_row.empty:
    row = spend_row.iloc[0]
    if country_sel == "UAE":
        spend = float(row.get("uae_usd", 0))
    elif country_sel == "KSA":
        spend = float(row.get("ksa_usd", 0))
    elif country_sel == "USA":
        spend = 0.0
    else:
        spend = float(row.get("total_usd", 0))
    cac = spend / new_customers_mtd if (new_customers_mtd > 0 and spend > 0) else 0.0
    cac_display = f"${cac:,.0f}" if cac > 0 else "—"
    cac_note    = "" if spend > 0 else " (no spend data)"
else:
    spend       = 0.0
    cac_display = "—"
    cac_note    = " (spend tab empty)"

# ── Cancellation rate ─────────────────────────────────────────────────────────
cc_machine  = rc[rc["category"] == "Machine"]
mtd_cancels = int(cc_machine[
    cc_machine["is_true_cancel"]
    & (cc_machine["cancelled_at_dt"] >= month_start)
    & (cc_machine["cancelled_at_dt"] <= today)
].shape[0])

active_mach_for_rate = (
    rc[(rc["status"] == "ACTIVE") & (rc["category"] == "Machine")]
    ["subscription_id"].nunique()
)
cr = mtd_cancels / active_mach_for_rate if active_mach_for_rate > 0 else 0.0
cancel_rate_str = f"{cr:.2%}"

# ── Monthly chart (hybrid) ────────────────────────────────────────────────────
LIVE_CUTOFF = pd.Timestamp("2025-09-01")

rc_for_hist = rc_full[
    (rc_full["category"] == "Machine")
    & rc_full["created_at_dt"].notna()
    & (rc_full["created_at_dt"] < LIVE_CUTOFF)
].copy()
if country_sel != "All":
    rc_for_hist = rc_for_hist[rc_for_hist["market"] == country_sel]
if product_sel != "All":
    rc_for_hist = rc_for_hist[rc_for_hist["product"] == product_sel]

if not rc_for_hist.empty:
    hist_agg = (
        rc_for_hist
        .assign(mp=lambda d: d["created_at_dt"].dt.to_period("M"))
        .groupby("mp").size()
        .reset_index(name="qty")
    )
    hist_agg["label"]    = hist_agg["mp"].dt.strftime("%b-%y")
    hist_agg["month_dt"] = hist_agg["mp"].dt.to_timestamp()
else:
    hist_agg = pd.DataFrame(columns=["label", "month_dt", "qty"])

sh_live = sh_all[sh_all["date"] >= LIVE_CUTOFF].copy()
if country_sel != "All":
    sh_live = sh_live[sh_live["country"] == country_sel]
if product_sel != "All":
    sh_live = sh_live[sh_live["product"] == product_sel]

if not sh_live.empty:
    live_agg = (
        sh_live
        .assign(mp=lambda d: d["date"].dt.to_period("M"))
        .groupby("mp")["qty"].sum()
        .reset_index()
    )
    live_agg["label"]    = live_agg["mp"].dt.strftime("%b-%y")
    live_agg["month_dt"] = live_agg["mp"].dt.to_timestamp()
else:
    live_agg = pd.DataFrame(columns=["label", "month_dt", "qty"])

live_set = set(live_agg["label"])
fill_rows = []
cur = LIVE_CUTOFF
while cur <= today:
    lbl = cur.strftime("%b-%y")
    if lbl not in live_set:
        fill_rows.append({"label": lbl, "month_dt": cur, "qty": 0})
    cur += relativedelta(months=1)
if fill_rows:
    live_agg = pd.concat([live_agg, pd.DataFrame(fill_rows)], ignore_index=True)

all_monthly = (
    pd.concat(
        [hist_agg[["label", "month_dt", "qty"]],
         live_agg[["label", "month_dt", "qty"]]],
        ignore_index=True,
    )
    .sort_values("month_dt")
    .reset_index(drop=True)
)

mask = (all_monthly["month_dt"] >= chart_start) & (all_monthly["month_dt"] <= chart_end)
chart_data     = all_monthly[mask].copy()
chart_months_f = chart_data["label"].tolist()
chart_vals_f   = chart_data["qty"].tolist()

# ── Header ────────────────────────────────────────────────────────────────────
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

# ── KPI row ───────────────────────────────────────────────────────────────────
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Today's Sales",     f"{today_sales:,}")
k2.metric(f"Active Users{users_note}", f"{total_users:,}")
k3.metric("ARR",               fmt_usd(total_arr))
k4.metric("Cancellation Rate", cancel_rate_str)
k5.metric("CAC" + cac_note,    cac_display,
          help="Marketing Spend ÷ New Machine Customers (subs + ownership orders) MTD")
k6.metric("New This Month",    f"{new_customers_mtd:,}",
          help="New Recharge machine subscriptions + Shopify ownership orders MTD")

st.markdown("---")

# ── Monthly bar + secondary KPIs ─────────────────────────────────────────────
c_chart, c_kpis = st.columns([3, 2])

with c_chart:
    bar_colors = []
    for ml in chart_months_f:
        try:
            m      = pd.to_datetime(ml, format="%b-%y")
            is_cur = (m.year == today.year and m.month == today.month)
            is_hist = m < LIVE_CUTOFF
        except Exception:
            is_cur = is_hist = False
        if is_cur:
            bar_colors.append("#7dd3fc")
        elif is_hist:
            bar_colors.append("#6366f1")
        else:
            bar_colors.append("#0ea5e9")

    fig_m = go.Figure(go.Bar(
        x=chart_months_f, y=chart_vals_f,
        marker_color=bar_colors,
        text=[f"{v:,}" if v > 0 else "" for v in chart_vals_f],
        textposition="outside",
        textfont=dict(size=9, color="#94a3b8"),
        hovertemplate="<b>%{x}</b><br>%{y:,} units<extra></extra>",
    ))
    fig_m.update_layout(
        title=dict(
            text=(
                "Monthly Sales  "
                '<span style="font-size:10px;color:#94a3b8;">'
                "◼ <span style='color:#6366f1'>pre-Sep-25 (Recharge)</span>"
                "  ◼ <span style='color:#0ea5e9'>Sep-25+ (Shopify)</span>"
                "</span>"
            ),
            x=0, font=dict(size=13, color="#e2e5f0"),
        ),
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
    kb.metric("MoM Sales", f"{mom_sales:,}",
              delta=f"{mom_delta:+,}", delta_color="normal")
    st.markdown("<br>", unsafe_allow_html=True)
    kc, kd = st.columns(2)
    kc.metric("Daily Average", f"{daily_avg:.1f}")
    kd.metric("Days Elapsed",  f"{days_elapsed} / {days_in_month}")

# ── Daily last-30-days + MTD split donut ─────────────────────────────────────
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
    daily_full["color"] = daily_full["date"].apply(
        lambda d: "#7dd3fc" if d == today else "#6366f1"
    )

    fig_d = go.Figure(go.Bar(
        x=daily_full["label"], y=daily_full["sales"],
        marker_color=daily_full["color"].tolist(),
        text=daily_full["sales"].apply(lambda v: f"{v}" if v > 0 else ""),
        textposition="outside",
        textfont=dict(size=8, color="#94a3b8"),
        hovertemplate="<b>%{x}</b><br>%{y:,} units<extra></extra>",
    ))
    fig_d.update_layout(
        title=dict(text="Sales — Last 30 Days", x=0,
                   font=dict(size=13, color="#e2e5f0")),
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
        st.info("No Shopify sales data for this filter in the current month.")

# ── Row 3: User Base Over Time (full width) ──────────────────────────────────
if not ub.empty:
    st.markdown("---")

    # Pick the right column for the selected country
    if country_sel == "UAE":
        ub_col, ub_label = "uae", "UAE User Base"
    elif country_sel == "KSA":
        ub_col, ub_label = "ksa", "KSA User Base"
    else:
        ub_col, ub_label = "global", "Total User Base"

    ub_chart = ub[ub["month_dt"] >= chart_start].copy()
    if not ub_chart.empty:
        ub_chart["label"] = ub_chart["month_dt"].dt.strftime("%b-%y")

        fig_ub = go.Figure()
        fig_ub.add_trace(go.Scatter(
            x=ub_chart["label"],
            y=ub_chart[ub_col],
            mode="lines+markers+text",
            line=dict(color="#8b5cf6", width=2.5),
            marker=dict(size=6, color="#8b5cf6"),
            text=[f"{v:,}" for v in ub_chart[ub_col]],
            textposition="top center",
            textfont=dict(size=9, color="#94a3b8"),
            hovertemplate="<b>%{x}</b><br>%{y:,} users<extra></extra>",
        ))
        fig_ub.update_layout(
            title=dict(text=ub_label + " Over Time", x=0,
                       font=dict(size=13, color="#e2e5f0")),
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            height=320,
            xaxis=dict(tickangle=-45, tickfont=dict(size=9, color="#94a3b8"),
                       showgrid=False, zeroline=False),
            yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.06)",
                       zeroline=False, tickfont=dict(size=9, color="#94a3b8")),
            margin=dict(t=44, b=8, l=4, r=8),
        )
        st.plotly_chart(fig_ub, use_container_width=True)

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("---")
notes = []
if users_note:
    notes.append(
        "**Active Users:** USA not tracked in Monthly User Base tab; "
        "showing Recharge active machine subscriptions only."
    )
if cac_note:
    notes.append(f"**CAC:** {cac_note.strip(' ()')}")
notes.append(
    "**Monthly chart:** pre-Sep-25 = Recharge subscriptions; "
    "Sep-25+ = Shopify orders (subs + ownership)."
)
try:
    _errs, _t = get_load_diagnostics()
    notes.append(f"**Performance:** data synced in {_t:.1f}s (7 tabs, parallel).")
except Exception:
    pass
with st.expander("ℹ️  Data notes", expanded=False):
    for n in notes:
        st.markdown(f"- {n}")
