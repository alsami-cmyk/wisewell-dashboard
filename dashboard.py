"""
Wisewell ARR Dashboard
──────────────────────
Run locally:   streamlit run dashboard.py
Share via URL: ngrok http 8501
"""

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from utils import (
    CATEGORY_COLOR, MARKET_COLOR, PRODUCT_ORDER, SHARED_CSS,
    apply_fx, fmt_usd, get_fx, load_raw_data, sidebar_filters,
)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Wisewell ARR Dashboard",
    page_icon="💧",
    layout="wide",
    initial_sidebar_state="expanded",
)
st_autorefresh(interval=5 * 60 * 1000, key="arr_refresh")
st.markdown(SHARED_CSS, unsafe_allow_html=True)

# ── Sidebar + data ────────────────────────────────────────────────────────────
market_sel, category_sel = sidebar_filters("arr")

df_raw = load_raw_data()
fx     = get_fx()
df_all = apply_fx(df_raw, fx)

df = df_all.copy()
if market_sel   != "All": df = df[df["market"]   == market_sel]
if category_sel != "All": df = df[df["category"] == category_sel]

# ── Header ────────────────────────────────────────────────────────────────────
st.title("💧 Wisewell ARR Dashboard")
st.caption(
    f"**Last synced:** {datetime.now().strftime('%d %b %Y · %H:%M')}  ·  "
    f"**FX:** 1 AED = ${fx['AED']:.4f} · 1 SAR = ${fx['SAR']:.4f} ({fx['source']})  ·  "
    f"Active subscriptions only"
)
st.markdown("---")

# ── KPI cards ─────────────────────────────────────────────────────────────────
total_arr   = df["arr_usd"].sum()
uae_arr     = df[df["market"] == "UAE"]["arr_usd"].sum()
ksa_arr     = df[df["market"] == "KSA"]["arr_usd"].sum()
machine_arr = df[df["category"] == "Machine"]["arr_usd"].sum()
filter_arr  = df[df["category"] == "Filter"]["arr_usd"].sum()
active_subs = df["subscription_id"].nunique()

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Total ARR",   fmt_usd(total_arr))
k2.metric("UAE ARR",     fmt_usd(uae_arr))
k3.metric("KSA ARR",     fmt_usd(ksa_arr))
k4.metric("Machine ARR", fmt_usd(machine_arr))
k5.metric("Filter ARR",  fmt_usd(filter_arr))
k6.metric("Active Subs", f"{active_subs:,}")

st.markdown("---")

# ── Chart row 1: ARR by product (grouped) + Market donut ─────────────────────
c1, c2 = st.columns([3, 2])

with c1:
    by_prod = (
        df.groupby(["product", "category"])["arr_usd"].sum().reset_index()
    )
    by_prod["product"] = pd.Categorical(by_prod["product"], PRODUCT_ORDER, ordered=True)
    by_prod = by_prod.sort_values("product")

    fig = px.bar(
        by_prod, x="product", y="arr_usd",
        color="category", color_discrete_map=CATEGORY_COLOR,
        barmode="group", text_auto=".3s",
        title="ARR by Product & Category",
        labels={"product": "", "arr_usd": "ARR (USD)", "category": ""},
    )
    fig.update_layout(
        plot_bgcolor="white", height=360,
        yaxis_tickprefix="$", yaxis_tickformat=",.0f",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=50, b=10),
    )
    fig.update_traces(textfont_size=11)
    st.plotly_chart(fig, use_container_width=True)

with c2:
    by_mkt = df.groupby("market")["arr_usd"].sum().reset_index()
    fig2 = px.pie(
        by_mkt, values="arr_usd", names="market",
        color="market", color_discrete_map=MARKET_COLOR,
        hole=0.55, title="ARR by Market",
    )
    fig2.update_traces(
        texttemplate="%{label}<br><b>%{percent:.1%}</b>",
        textposition="outside",
        hovertemplate="%{label}: $%{value:,.0f}<extra></extra>",
    )
    fig2.update_layout(height=360, showlegend=False, margin=dict(t=50, b=10))
    st.plotly_chart(fig2, use_container_width=True)

# ── Chart row 2: Stacked Machine vs Filter + Category donut ──────────────────
c3, c4 = st.columns([3, 2])

with c3:
    pivot = (
        df.groupby(["product", "category"])["arr_usd"].sum()
        .unstack(fill_value=0)
        .reindex(PRODUCT_ORDER)
        .fillna(0)
    )
    fig3 = go.Figure()
    for cat, color in CATEGORY_COLOR.items():
        if cat in pivot.columns:
            vals = pivot[cat]
            fig3.add_trace(go.Bar(
                name=cat, x=pivot.index, y=vals,
                marker_color=color,
                text=[f"${v:,.0f}" if v > 0 else "" for v in vals],
                textposition="inside", textfont_color="white", textfont_size=11,
            ))
    fig3.update_layout(
        barmode="stack", title="Machine vs Filter ARR per Product (Stacked)",
        plot_bgcolor="white", height=360,
        yaxis_tickprefix="$", yaxis_tickformat=",.0f",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis_title="", margin=dict(t=50, b=10),
    )
    st.plotly_chart(fig3, use_container_width=True)

with c4:
    by_cat = df.groupby("category")["arr_usd"].sum().reset_index()
    fig4 = px.pie(
        by_cat, values="arr_usd", names="category",
        color="category", color_discrete_map=CATEGORY_COLOR,
        hole=0.55, title="Machine vs Filter ARR",
    )
    fig4.update_traces(
        texttemplate="%{label}<br><b>%{percent:.1%}</b>",
        textposition="outside",
        hovertemplate="%{label}: $%{value:,.0f}<extra></extra>",
    )
    fig4.update_layout(height=360, showlegend=False, margin=dict(t=50, b=10))
    st.plotly_chart(fig4, use_container_width=True)

# ── Breakdown table ───────────────────────────────────────────────────────────
st.subheader("Full Breakdown")

table = (
    df.groupby(["market", "category", "product"])
    .agg(active_subs=("subscription_id", "nunique"),
         arr_local=("arr_local", "sum"),
         arr_usd=("arr_usd", "sum"))
    .reset_index()
)
table["product"] = pd.Categorical(table["product"], PRODUCT_ORDER, ordered=True)
table = table.sort_values(["category", "market", "product"])

total_row = pd.DataFrame([{
    "market": "ALL", "category": "—", "product": "—",
    "active_subs": table["active_subs"].sum(),
    "arr_local": table["arr_local"].sum(),
    "arr_usd": table["arr_usd"].sum(),
}])
table = pd.concat([table, total_row], ignore_index=True)

table["arr_usd_fmt"]   = table["arr_usd"].apply(lambda x: f"${x:,.0f}")
table["arr_local_fmt"] = table.apply(
    lambda r: f"{r['arr_local']:,.0f} {'AED' if r['market']=='UAE' else 'SAR' if r['market']=='KSA' else '(mixed)'}",
    axis=1,
)

st.dataframe(
    table[["market", "category", "product", "active_subs", "arr_local_fmt", "arr_usd_fmt"]]
    .rename(columns={
        "market": "Market", "category": "Category", "product": "Product",
        "active_subs": "Active Subs", "arr_local_fmt": "ARR (Local)", "arr_usd_fmt": "ARR (USD)",
    }),
    use_container_width=True,
    hide_index=True,
)
