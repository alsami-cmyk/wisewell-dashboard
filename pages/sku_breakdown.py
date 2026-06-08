"""
SKU Breakdown — inventory & supply-chain view.

Data starts at Jan 2026 (configurable in get_sku_sales). Sources:
  - Recharge subscriptions for all markets (Machine category only)
  - Shopify UAE/KSA ownership rows (variant inferred from Lineitem
    sku + Lineitem name)

The page is fully segmentable by market, product, and channel. The
default view is "All", which lets you see overall colour distribution
and drill in from there.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils import (
    PRODUCT_COLOR,
    PRODUCT_ORDER,
    SHARED_CSS,
    fmt_usd,
    get_sku_sales,
)

st.markdown(SHARED_CSS, unsafe_allow_html=True)
st.markdown("## 📦 SKU Breakdown")
st.caption(
    "Inventory & supply-chain view of every machine sale since Jan 2026, "
    "broken out by product, colour, and SKU. Sources: Recharge subscriptions + "
    "Shopify ownership."
)

# ── Load data ────────────────────────────────────────────────────────────────
df = get_sku_sales(start_dt=pd.Timestamp("2026-01-01"))

if df.empty:
    st.warning("No sales found from Jan 2026 onwards.")
    st.stop()

# ── Colour palette: Black / White / Single / Unspecified ────────────────────
COLOUR_PALETTE = {
    "Black":       "#1f2937",  # dark slate
    "White":       "#e2e8f0",  # near-white
    "Single":      "#0ea5e9",  # cyan (single-colour products)
    "Unspecified": "#94a3b8",  # grey
}
COLOUR_ORDER = ["Black", "White", "Single", "Unspecified"]

# ── Filters ──────────────────────────────────────────────────────────────────
filter_col1, filter_col2, filter_col3, filter_col4 = st.columns([2, 2, 2, 2])

with filter_col1:
    min_d = df["date"].min().date()
    max_d = df["date"].max().date()
    date_range = st.date_input(
        "Date range",
        value=(min_d, max_d),
        min_value=min_d,
        max_value=max_d,
        key="sku_date_range",
    )
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        d_start, d_end = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
    else:
        d_start, d_end = pd.Timestamp(min_d), pd.Timestamp(max_d)

with filter_col2:
    available_markets = sorted(df["market"].unique().tolist())
    markets_sel = st.multiselect(
        "Region",
        options=available_markets,
        default=available_markets,
        key="sku_markets",
    )

with filter_col3:
    available_products = [p for p in PRODUCT_ORDER if p in df["product"].unique()]
    products_sel = st.multiselect(
        "Product",
        options=available_products,
        default=available_products,
        key="sku_products",
    )

with filter_col4:
    available_channels = sorted(df["channel"].unique().tolist())
    channels_sel = st.multiselect(
        "Channel",
        options=available_channels,
        default=available_channels,
        key="sku_channels",
    )

# ── Apply filters ────────────────────────────────────────────────────────────
mask = (
    (df["date"] >= d_start)
    & (df["date"] <= d_end)
    & (df["market"].isin(markets_sel) if markets_sel else False)
    & (df["product"].isin(products_sel) if products_sel else False)
    & (df["channel"].isin(channels_sel) if channels_sel else False)
)
fdf = df.loc[mask].copy()

if fdf.empty:
    st.info("No sales match the current filter selection.")
    st.stop()

# ── Headline KPIs ────────────────────────────────────────────────────────────
total_units = int(fdf["qty"].sum())
unique_skus = fdf.loc[fdf["sku"].astype(bool), "sku"].nunique()
black_share = fdf.loc[fdf["colour"] == "Black", "qty"].sum() / total_units if total_units else 0
white_share = fdf.loc[fdf["colour"] == "White", "qty"].sum() / total_units if total_units else 0

st.markdown("---")
k1, k2, k3, k4 = st.columns(4)
k1.metric("Total units (filtered)", f"{total_units:,}")
k2.metric("Distinct SKUs", f"{unique_skus:,}")
k3.metric("Black share", f"{black_share:.1%}")
k4.metric("White share", f"{white_share:.1%}")

# ── Chart 1: Units by product × colour (stacked bars) ────────────────────────
st.markdown("### Units by product · split by colour")

agg = (
    fdf.groupby(["product", "colour"], as_index=False)["qty"].sum()
    .pivot(index="product", columns="colour", values="qty")
    .fillna(0).astype(int)
)
# Order rows by canonical product order and columns by COLOUR_ORDER
agg = agg.reindex([p for p in PRODUCT_ORDER if p in agg.index])
cols_present = [c for c in COLOUR_ORDER if c in agg.columns]
agg = agg[cols_present]

# Compute per-product totals for percentage labels
row_totals = agg.sum(axis=1)

fig = go.Figure()
for colour in cols_present:
    pct_labels = [
        f"{int(v):,} ({v/row_totals[p]*100:.0f}%)" if row_totals[p] > 0 and v > 0 else ""
        for p, v in zip(agg.index, agg[colour])
    ]
    fig.add_trace(go.Bar(
        x=list(agg.index),
        y=list(agg[colour]),
        name=colour,
        marker_color=COLOUR_PALETTE[colour],
        marker_line=dict(color="#475569", width=1) if colour == "White" else None,
        text=pct_labels,
        textposition="inside",
        textfont=dict(color="#0f172a" if colour == "White" else "#e2e8f0", size=11),
        insidetextanchor="middle",
        hovertemplate=f"%{{x}}<br>{colour}: %{{y:,}}<extra></extra>",
    ))

# Total label above each stack
for prod, tot in row_totals.items():
    if tot > 0:
        fig.add_annotation(
            x=prod, y=tot, text=f"<b>{int(tot):,}</b>",
            showarrow=False, yshift=14,
            font=dict(color="#cbd5e1", size=12),
        )

fig.update_layout(
    barmode="stack",
    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#e2e8f0", size=12),
    height=420,
    margin=dict(l=10, r=10, t=30, b=30),
    xaxis=dict(showgrid=False, categoryorder="array",
               categoryarray=[p for p in PRODUCT_ORDER if p in agg.index]),
    yaxis=dict(gridcolor="rgba(148,163,184,0.15)", zeroline=False,
               title="Units"),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)
st.plotly_chart(fig, use_container_width=True)

# ── Chart 2 (optional): Monthly trend by colour ──────────────────────────────
with st.expander("📈 Monthly trend by colour"):
    fdf2 = fdf.copy()
    fdf2["month"] = fdf2["date"].dt.to_period("M").dt.to_timestamp()
    monthly = fdf2.groupby(["month", "colour"], as_index=False)["qty"].sum()
    months_sorted = sorted(monthly["month"].unique())
    fig2 = go.Figure()
    for colour in [c for c in COLOUR_ORDER if c in monthly["colour"].unique()]:
        m_subset = monthly[monthly["colour"] == colour].set_index("month").reindex(months_sorted).fillna(0)
        fig2.add_trace(go.Bar(
            x=[m.strftime("%b %Y") for m in months_sorted],
            y=m_subset["qty"].astype(int),
            name=colour,
            marker_color=COLOUR_PALETTE[colour],
            marker_line=dict(color="#475569", width=1) if colour == "White" else None,
            hovertemplate=f"%{{x}}<br>{colour}: %{{y:,}}<extra></extra>",
        ))
    fig2.update_layout(
        barmode="stack",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e2e8f0", size=12),
        height=360,
        margin=dict(l=10, r=10, t=10, b=30),
        xaxis=dict(showgrid=False),
        yaxis=dict(gridcolor="rgba(148,163,184,0.15)", zeroline=False, title="Units"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig2, use_container_width=True)

# ── Table: SKU-level breakdown ───────────────────────────────────────────────
st.markdown("### SKU-level breakdown")
st.caption(
    "One row per **(product · colour · SKU)** with unit counts and the "
    "colour split within each product. SKUs left blank mean the source "
    "row didn't carry one; the colour was inferred from the product name."
)

table = (
    fdf.groupby(["product", "colour", "sku"], as_index=False)["qty"]
    .sum()
    .sort_values(["product", "colour", "qty"], ascending=[True, True, False])
)

# Per-product totals for the within-product % share
product_totals = table.groupby("product")["qty"].transform("sum")
grand_total = table["qty"].sum()
table["% within product"] = (table["qty"] / product_totals * 100).round(1)
table["% of total"]       = (table["qty"] / grand_total * 100).round(1)

# Tidy display
display = table.rename(columns={
    "product": "Product",
    "colour":  "Colour",
    "sku":     "SKU",
    "qty":     "Units",
})
display["SKU"] = display["SKU"].replace("", "—")
display["% within product"] = display["% within product"].astype(str) + "%"
display["% of total"]       = display["% of total"].astype(str) + "%"

st.dataframe(
    display,
    hide_index=True,
    use_container_width=True,
    height=min(560, 50 + 36 * len(display)),
    column_config={
        "Units": st.column_config.NumberColumn(format="%d"),
    },
)

# ── Per-product colour split summary (collapsed table) ──────────────────────
st.markdown("### Per-product colour split")
split = (
    fdf.groupby(["product", "colour"], as_index=False)["qty"]
    .sum()
)
split_pivot = split.pivot(index="product", columns="colour", values="qty").fillna(0).astype(int)
split_pivot = split_pivot.reindex([p for p in PRODUCT_ORDER if p in split_pivot.index])
split_pivot = split_pivot[[c for c in COLOUR_ORDER if c in split_pivot.columns]]
split_pivot["Total"] = split_pivot.sum(axis=1)

# Add percentage columns
pct = split_pivot.div(split_pivot["Total"], axis=0).drop(columns="Total") * 100
pct.columns = [f"{c} %" for c in pct.columns]
combined = pd.concat([split_pivot, pct.round(1)], axis=1).reset_index()
combined = combined.rename(columns={"product": "Product"})
for c in [c for c in combined.columns if c.endswith(" %")]:
    combined[c] = combined[c].astype(str) + "%"

st.dataframe(combined, hide_index=True, use_container_width=True)

# ── Footnote ─────────────────────────────────────────────────────────────────
st.caption(
    f"Showing **{total_units:,}** units across **{unique_skus:,}** distinct SKUs · "
    f"{d_start:%d %b %Y} → {d_end:%d %b %Y} · "
    f"channels: {', '.join(channels_sel) if channels_sel else 'none'}"
)
