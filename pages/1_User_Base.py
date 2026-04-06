"""
Wisewell User Base Dashboard — page 2
Exact same breakdown structure as ARR page, measured in subscriber counts.
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
    page_title="Wisewell User Base",
    page_icon="👥",
    layout="wide",
    initial_sidebar_state="expanded",
)
st_autorefresh(interval=5 * 60 * 1000, key="ub_refresh")
st.markdown(SHARED_CSS, unsafe_allow_html=True)

# ── Sidebar + data ────────────────────────────────────────────────────────────
market_sel, category_sel = sidebar_filters("ub")

df_raw = load_raw_data()
fx     = get_fx()
df_all = apply_fx(df_raw, fx)

df = df_all.copy()
if market_sel   != "All": df = df[df["market"]   == market_sel]
if category_sel != "All": df = df[df["category"] == category_sel]

# ── Header ────────────────────────────────────────────────────────────────────
st.title("👥 Wisewell User Base")
st.caption(
    f"**Last synced:** {datetime.now().strftime('%d %b %Y · %H:%M')}  ·  "
    f"Active subscriptions only"
)
st.markdown("---")

# ── KPI cards ─────────────────────────────────────────────────────────────────
total_subs   = df["subscription_id"].nunique()
uae_subs     = df[df["market"] == "UAE"]["subscription_id"].nunique()
ksa_subs     = df[df["market"] == "KSA"]["subscription_id"].nunique()
machine_subs = df[df["category"] == "Machine"]["subscription_id"].nunique()
filter_subs  = df[df["category"] == "Filter"]["subscription_id"].nunique()
avg_arr_sub  = df["arr_usd"].sum() / total_subs if total_subs else 0

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Total Subscribers",    f"{total_subs:,}")
k2.metric("UAE Subscribers",      f"{uae_subs:,}")
k3.metric("KSA Subscribers",      f"{ksa_subs:,}")
k4.metric("Machine Subscribers",  f"{machine_subs:,}")
k5.metric("Filter Subscribers",   f"{filter_subs:,}")
k6.metric("Avg ARR / Sub (USD)",  fmt_usd(avg_arr_sub))

st.markdown("---")

# ── Chart row 1: Subs by product (grouped) + Market donut ────────────────────
c1, c2 = st.columns([3, 2])

with c1:
    by_prod = (
        df.groupby(["product", "category"])["subscription_id"]
        .nunique()
        .reset_index()
        .rename(columns={"subscription_id": "subscribers"})
    )
    by_prod["product"] = pd.Categorical(by_prod["product"], PRODUCT_ORDER, ordered=True)
    by_prod = by_prod.sort_values("product")

    fig = px.bar(
        by_prod, x="product", y="subscribers",
        color="category", color_discrete_map=CATEGORY_COLOR,
        barmode="group", text_auto=True,
        title="Subscribers by Product & Category",
        labels={"product": "", "subscribers": "Subscribers", "category": ""},
    )
    fig.update_layout(
        plot_bgcolor="white", height=360,
        yaxis_tickformat=",",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=50, b=10),
    )
    fig.update_traces(textfont_size=11)
    st.plotly_chart(fig, use_container_width=True)

with c2:
    by_mkt = (
        df.groupby("market")["subscription_id"]
        .nunique()
        .reset_index()
        .rename(columns={"subscription_id": "subscribers"})
    )
    fig2 = px.pie(
        by_mkt, values="subscribers", names="market",
        color="market", color_discrete_map=MARKET_COLOR,
        hole=0.55, title="Subscribers by Market",
    )
    fig2.update_traces(
        texttemplate="%{label}<br><b>%{percent:.1%}</b>",
        textposition="outside",
        hovertemplate="%{label}: %{value:,}<extra></extra>",
    )
    fig2.update_layout(height=360, showlegend=False, margin=dict(t=50, b=10))
    st.plotly_chart(fig2, use_container_width=True)

# ── Chart row 2: Stacked Machine vs Filter + Category donut ──────────────────
c3, c4 = st.columns([3, 2])

with c3:
    pivot = (
        df.groupby(["product", "category"])["subscription_id"]
        .nunique()
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
                text=[f"{int(v):,}" if v > 0 else "" for v in vals],
                textposition="inside", textfont_color="white", textfont_size=11,
            ))
    fig3.update_layout(
        barmode="stack",
        title="Machine vs Filter Subscribers per Product (Stacked)",
        plot_bgcolor="white", height=360,
        yaxis_tickformat=",",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis_title="", margin=dict(t=50, b=10),
    )
    st.plotly_chart(fig3, use_container_width=True)

with c4:
    by_cat = (
        df.groupby("category")["subscription_id"]
        .nunique()
        .reset_index()
        .rename(columns={"subscription_id": "subscribers"})
    )
    fig4 = px.pie(
        by_cat, values="subscribers", names="category",
        color="category", color_discrete_map=CATEGORY_COLOR,
        hole=0.55, title="Machine vs Filter Subscribers",
    )
    fig4.update_traces(
        texttemplate="%{label}<br><b>%{percent:.1%}</b>",
        textposition="outside",
        hovertemplate="%{label}: %{value:,}<extra></extra>",
    )
    fig4.update_layout(height=360, showlegend=False, margin=dict(t=50, b=10))
    st.plotly_chart(fig4, use_container_width=True)

# ── Breakdown table ───────────────────────────────────────────────────────────
st.subheader("Full Breakdown")

table = (
    df.groupby(["market", "category", "product"])
    .agg(
        subscribers=("subscription_id", "nunique"),
        arr_usd=("arr_usd", "sum"),
    )
    .reset_index()
)
table["product"]     = pd.Categorical(table["product"], PRODUCT_ORDER, ordered=True)
table                = table.sort_values(["category", "market", "product"])
table["avg_arr_sub"] = table.apply(
    lambda r: r["arr_usd"] / r["subscribers"] if r["subscribers"] else 0, axis=1
)

total_row = pd.DataFrame([{
    "market":      "ALL",
    "category":    "—",
    "product":     "—",
    "subscribers": table["subscribers"].sum(),
    "arr_usd":     table["arr_usd"].sum(),
    "avg_arr_sub": table["arr_usd"].sum() / table["subscribers"].sum()
                   if table["subscribers"].sum() else 0,
}])
table = pd.concat([table, total_row], ignore_index=True)

table["arr_usd_fmt"]   = table["arr_usd"].apply(lambda x: f"${x:,.0f}")
table["avg_arr_fmt"]   = table["avg_arr_sub"].apply(lambda x: f"${x:,.0f}")

st.dataframe(
    table[["market", "category", "product", "subscribers", "arr_usd_fmt", "avg_arr_fmt"]]
    .rename(columns={
        "market":      "Market",
        "category":    "Category",
        "product":     "Product",
        "subscribers": "Subscribers",
        "arr_usd_fmt": "Total ARR (USD)",
        "avg_arr_fmt": "Avg ARR / Sub",
    }),
    use_container_width=True,
    hide_index=True,
)
