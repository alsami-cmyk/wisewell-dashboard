"""
Paid Ads Analysis — Meta performance dashboard.

Metrics: Spend, Clicks, Impressions, CTR, CPC, CPM.
Filters: Market · Primary date range · Comparison period · Granularity.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from utils import fmt_usd, load_meta_ads_daily

st.markdown("## 📢 Paid Ads Analysis — Meta")
st.caption("Data source: Meta Ads only. Google Ads will be added once API access is approved.")

# ── Data ─────────────────────────────────────────────────────────────────────
df_all = load_meta_ads_daily()

if df_all.empty:
    st.warning("No Meta Ads data found. Run the sync script to populate 'Meta Ads Daily - Claude'.")
    st.stop()

min_date = df_all["date"].min().date()
max_date = df_all["date"].max().date()

# ── Filters ───────────────────────────────────────────────────────────────────
st.markdown("---")
fc1, fc2, fc3, fc4 = st.columns([1.2, 1.2, 1.8, 1.2])

with fc1:
    market_sel = st.selectbox("Market", ["All", "UAE", "KSA", "USA"], key="pa_market")

with fc2:
    granularity = st.selectbox("Granularity", ["Daily", "Weekly", "Monthly"], key="pa_gran")

with fc3:
    pri_preset = st.selectbox("Period", ["MTD", "Past 7 Days", "YTD", "Custom"], key="pa_preset")

with fc4:
    cmp_mode = st.selectbox(
        "Compare to",
        ["Previous period", "Same period last year", "Custom"],
        key="pa_cmp_mode",
    )

# Resolve primary range from preset
today_pa = max_date
if pri_preset == "MTD":
    pri_start, pri_end = today_pa.replace(day=1), today_pa
elif pri_preset == "Past 7 Days":
    pri_start, pri_end = today_pa - timedelta(days=6), today_pa
elif pri_preset == "YTD":
    pri_start, pri_end = today_pa.replace(month=1, day=1), today_pa
else:
    custom_range = st.date_input(
        "Custom period",
        value=(max(min_date, today_pa - timedelta(days=29)), today_pa),
        min_value=min_date, max_value=max_date,
        key="pa_pri",
    )
    pri_start, pri_end = (custom_range if isinstance(custom_range, (list, tuple)) and len(custom_range) == 2
                          else (max(min_date, today_pa - timedelta(days=29)), today_pa))

pri_days = (pri_end - pri_start).days + 1

# Resolve comparison range
if cmp_mode == "Previous period":
    cmp_end   = pri_start - timedelta(days=1)
    cmp_start = cmp_end   - timedelta(days=pri_days - 1)
elif cmp_mode == "Same period last year":
    cmp_start = date(pri_start.year - 1, pri_start.month, pri_start.day)
    cmp_end   = date(pri_end.year   - 1, pri_end.month,   pri_end.day)
else:
    cmp_cols = st.columns([1, 1, 4])
    with cmp_cols[0]:
        cmp_start = st.date_input("Comparison start", value=pri_start - timedelta(days=pri_days), key="pa_cs")
    with cmp_cols[1]:
        cmp_end   = st.date_input("Comparison end",   value=pri_start - timedelta(days=1),        key="pa_ce")

# ── Helpers ───────────────────────────────────────────────────────────────────
def _filter(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    mask = (df["date"].dt.date >= start) & (df["date"].dt.date <= end)
    if market_sel != "All":
        mask &= df["market"] == market_sel
    return df.loc[mask].copy()


def _agg(df: pd.DataFrame) -> dict:
    spend       = df["spend_usd"].sum()
    clicks      = int(df["clicks"].sum())
    impressions = int(df["impressions"].sum())
    ctr   = clicks / impressions * 100 if impressions > 0 else 0.0
    cpc   = spend  / clicks             if clicks      > 0 else 0.0
    cpm   = spend  / impressions * 1000 if impressions > 0 else 0.0
    return dict(spend=spend, clicks=clicks, impressions=impressions,
                ctr=ctr, cpc=cpc, cpm=cpm)


def _delta_pct(cur: float, prev: float) -> float | None:
    if prev == 0:
        return None if cur == 0 else (100.0 if cur > 0 else -100.0)
    return (cur - prev) / prev * 100


def _fmt_delta(d: float | None, inverse: bool = False) -> str:
    if d is None:
        return "—"
    sign = "+" if d >= 0 else ""
    val  = f"{sign}{d:.1f}%"
    return val


def _resample(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    df = df.copy()
    df = df.set_index("date")
    num = df[["spend_usd", "clicks", "impressions"]].resample(freq).sum()
    num["ctr_pct"] = num.apply(
        lambda r: r["clicks"] / r["impressions"] * 100 if r["impressions"] > 0 else 0.0, axis=1
    )
    num["cpc_usd"] = num.apply(
        lambda r: r["spend_usd"] / r["clicks"] if r["clicks"] > 0 else 0.0, axis=1
    )
    num["cpm_usd"] = num.apply(
        lambda r: r["spend_usd"] / r["impressions"] * 1000 if r["impressions"] > 0 else 0.0, axis=1
    )
    return num.reset_index()


# ── Compute periods ───────────────────────────────────────────────────────────
pri_df  = _filter(df_all, pri_start, pri_end)
cmp_df  = _filter(df_all, cmp_start, cmp_end)
pri_kpi = _agg(pri_df)
cmp_kpi = _agg(cmp_df)

freq = "W-MON" if granularity == "Weekly" else ("MS" if granularity == "Monthly" else "D")

pri_ts = _resample(pri_df, freq) if not pri_df.empty else pd.DataFrame()
cmp_ts = _resample(cmp_df, freq) if not cmp_df.empty else pd.DataFrame()

# ── KPI Scorecards ────────────────────────────────────────────────────────────
st.markdown("---")
pri_label = f"{pri_start.strftime('%b %d')} – {pri_end.strftime('%b %d, %Y')}"
cmp_label = f"{cmp_start.strftime('%b %d')} – {cmp_end.strftime('%b %d, %Y')}"
st.caption(f"**Primary:** {pri_label} &nbsp;|&nbsp; **Comparison:** {cmp_label} &nbsp;|&nbsp; Market: **{market_sel}**")

k1, k2, k3, k4, k5, k6 = st.columns(6)

k1.metric(
    "SPEND",
    fmt_usd(pri_kpi["spend"]),
    delta=_fmt_delta(_delta_pct(pri_kpi["spend"], cmp_kpi["spend"])),
    delta_color="inverse",
    help="Total ad spend in USD for the selected period.",
)
k2.metric(
    "CLICKS",
    f"{pri_kpi['clicks']:,}",
    delta=_fmt_delta(_delta_pct(pri_kpi["clicks"], cmp_kpi["clicks"])),
    help="Total link clicks.",
)
k3.metric(
    "IMPRESSIONS",
    f"{pri_kpi['impressions']:,}",
    delta=_fmt_delta(_delta_pct(pri_kpi["impressions"], cmp_kpi["impressions"])),
    help="Total ad impressions.",
)
k4.metric(
    "CTR",
    f"{pri_kpi['ctr']:.2f}%",
    delta=_fmt_delta(_delta_pct(pri_kpi["ctr"], cmp_kpi["ctr"])),
    help="Click-through rate = clicks ÷ impressions.",
)
k5.metric(
    "CPC",
    f"${pri_kpi['cpc']:,.2f}",
    delta=_fmt_delta(_delta_pct(pri_kpi["cpc"], cmp_kpi["cpc"])),
    delta_color="inverse",
    help="Cost per click = spend ÷ clicks.",
)
k6.metric(
    "CPM",
    f"${pri_kpi['cpm']:,.2f}",
    delta=_fmt_delta(_delta_pct(pri_kpi["cpm"], cmp_kpi["cpm"])),
    delta_color="inverse",
    help="Cost per 1,000 impressions = spend ÷ impressions × 1,000.",
)

# ── Spend + CTR over time ─────────────────────────────────────────────────────
st.markdown("---")
st.markdown("### Spend & CTR over time")

_LAYOUT = dict(
    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#e2e8f0", size=11),
    margin=dict(l=10, r=10, t=30, b=30),
    xaxis=dict(showgrid=False),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
)

fig_spend = go.Figure()

if not pri_ts.empty:
    fig_spend.add_trace(go.Bar(
        x=pri_ts["date"], y=pri_ts["spend_usd"],
        name=f"Spend — {pri_label}",
        marker_color="#818cf8", opacity=0.85,
        hovertemplate="%{x|%b %d}<br>Spend: $%{y:,.0f}<extra></extra>",
    ))
    fig_spend.add_trace(go.Scatter(
        x=pri_ts["date"], y=pri_ts["ctr_pct"],
        name=f"CTR — {pri_label}",
        mode="lines+markers",
        line=dict(color="#10b981", width=2),
        marker=dict(size=4),
        yaxis="y2",
        hovertemplate="%{x|%b %d}<br>CTR: %{y:.2f}%<extra></extra>",
    ))

if not cmp_ts.empty:
    fig_spend.add_trace(go.Bar(
        x=cmp_ts["date"], y=cmp_ts["spend_usd"],
        name=f"Spend — {cmp_label}",
        marker_color="#818cf8", opacity=0.30,
        hovertemplate="%{x|%b %d}<br>Spend (comp): $%{y:,.0f}<extra></extra>",
    ))
    fig_spend.add_trace(go.Scatter(
        x=cmp_ts["date"], y=cmp_ts["ctr_pct"],
        name=f"CTR — {cmp_label}",
        mode="lines",
        line=dict(color="#10b981", width=1.5, dash="dot"),
        yaxis="y2",
        opacity=0.5,
        hovertemplate="%{x|%b %d}<br>CTR (comp): %{y:.2f}%<extra></extra>",
    ))

fig_spend.update_layout(
    **_LAYOUT, height=380, barmode="overlay",
    yaxis=dict(
        title=dict(text="Spend (USD)", font=dict(color="#818cf8")),
        gridcolor="rgba(148,163,184,0.15)", zeroline=False,
        tickprefix="$", tickformat=",.0f",
    ),
    yaxis2=dict(
        title=dict(text="CTR (%)", font=dict(color="#10b981")),
        overlaying="y", side="right",
        showgrid=False, zeroline=False,
        ticksuffix="%", tickformat=".2f",
    ),
)
st.plotly_chart(fig_spend, use_container_width=True)

# ── CPC + CPM over time ───────────────────────────────────────────────────────
st.markdown("---")
col_cpc, col_cpm = st.columns(2)


def _line_chart(
    pri: pd.DataFrame, cmp: pd.DataFrame,
    col: str, label: str, color: str,
    y_prefix: str = "$", y_suffix: str = "",
    title: str = "",
) -> go.Figure:
    fig = go.Figure()
    if not pri.empty:
        fig.add_trace(go.Scatter(
            x=pri["date"], y=pri[col], name=pri_label,
            mode="lines+markers",
            line=dict(color=color, width=2.5),
            marker=dict(size=5),
            hovertemplate=f"%{{x|%b %d}}<br>{label}: {y_prefix}%{{y:,.2f}}{y_suffix}<extra></extra>",
        ))
    if not cmp.empty:
        fig.add_trace(go.Scatter(
            x=cmp["date"], y=cmp[col], name=cmp_label,
            mode="lines",
            line=dict(color=color, width=1.5, dash="dot"),
            opacity=0.5,
            hovertemplate=f"%{{x|%b %d}}<br>{label} (comp): {y_prefix}%{{y:,.2f}}{y_suffix}<extra></extra>",
        ))
    fig.update_layout(
        **_LAYOUT, height=300,
        yaxis=dict(
            gridcolor="rgba(148,163,184,0.15)", zeroline=False,
            tickprefix=y_prefix, tickformat=",.2f",
        ),
    )
    return fig


with col_cpc:
    st.markdown("### CPC over time")
    st.plotly_chart(
        _line_chart(pri_ts, cmp_ts, "cpc_usd", "CPC", "#f59e0b"),
        use_container_width=True,
    )

with col_cpm:
    st.markdown("### CPM over time")
    st.plotly_chart(
        _line_chart(pri_ts, cmp_ts, "cpm_usd", "CPM", "#06b6d4"),
        use_container_width=True,
    )

# ── Clicks + Impressions over time ────────────────────────────────────────────
st.markdown("---")
st.markdown("### Clicks & Impressions over time")

fig_vol = go.Figure()
if not pri_ts.empty:
    fig_vol.add_trace(go.Bar(
        x=pri_ts["date"], y=pri_ts["impressions"],
        name=f"Impressions — {pri_label}",
        marker_color="#a78bfa", opacity=0.7,
        yaxis="y2",
        hovertemplate="%{x|%b %d}<br>Impressions: %{y:,}<extra></extra>",
    ))
    fig_vol.add_trace(go.Scatter(
        x=pri_ts["date"], y=pri_ts["clicks"],
        name=f"Clicks — {pri_label}",
        mode="lines+markers",
        line=dict(color="#38bdf8", width=2.5),
        marker=dict(size=5),
        hovertemplate="%{x|%b %d}<br>Clicks: %{y:,}<extra></extra>",
    ))
if not cmp_ts.empty:
    fig_vol.add_trace(go.Bar(
        x=cmp_ts["date"], y=cmp_ts["impressions"],
        name=f"Impressions — {cmp_label}",
        marker_color="#a78bfa", opacity=0.25,
        yaxis="y2",
        hovertemplate="%{x|%b %d}<br>Impressions (comp): %{y:,}<extra></extra>",
    ))
    fig_vol.add_trace(go.Scatter(
        x=cmp_ts["date"], y=cmp_ts["clicks"],
        name=f"Clicks — {cmp_label}",
        mode="lines",
        line=dict(color="#38bdf8", width=1.5, dash="dot"),
        opacity=0.5,
        hovertemplate="%{x|%b %d}<br>Clicks (comp): %{y:,}<extra></extra>",
    ))

fig_vol.update_layout(
    **_LAYOUT, height=340, barmode="overlay",
    yaxis=dict(
        title=dict(text="Clicks", font=dict(color="#38bdf8")),
        gridcolor="rgba(148,163,184,0.15)", zeroline=False,
    ),
    yaxis2=dict(
        title=dict(text="Impressions", font=dict(color="#a78bfa")),
        overlaying="y", side="right",
        showgrid=False, zeroline=False,
    ),
)
st.plotly_chart(fig_vol, use_container_width=True)

# ── Market breakdown ──────────────────────────────────────────────────────────
st.markdown("---")
mkt_col1, mkt_col2 = st.columns(2)

MARKET_COLORS = {"UAE": "#818cf8", "KSA": "#10b981", "USA": "#f59e0b"}

# Aggregate primary period by market (always show all markets regardless of filter)
pri_all_mkts = _filter(
    df_all.assign(_dummy=True), pri_start, pri_end
) if market_sel == "All" else None
mkt_df = df_all[
    (df_all["date"].dt.date >= pri_start) & (df_all["date"].dt.date <= pri_end)
].groupby("market").agg(
    spend_usd=("spend_usd", "sum"),
    clicks=("clicks", "sum"),
    impressions=("impressions", "sum"),
).reset_index()
mkt_df["ctr_pct"] = mkt_df.apply(
    lambda r: r["clicks"] / r["impressions"] * 100 if r["impressions"] > 0 else 0.0, axis=1
)
mkt_df["cpc_usd"] = mkt_df.apply(
    lambda r: r["spend_usd"] / r["clicks"] if r["clicks"] > 0 else 0.0, axis=1
)
mkt_df["cpm_usd"] = mkt_df.apply(
    lambda r: r["spend_usd"] / r["impressions"] * 1000 if r["impressions"] > 0 else 0.0, axis=1
)
mkt_df = mkt_df.sort_values("spend_usd", ascending=False)

with mkt_col1:
    st.markdown("### Spend by market")
    if mkt_df.empty:
        st.info("No data for selected period.")
    else:
        fig_pie = go.Figure(go.Pie(
            labels=mkt_df["market"],
            values=mkt_df["spend_usd"],
            hole=0.55,
            marker=dict(
                colors=[MARKET_COLORS.get(m, "#64748b") for m in mkt_df["market"]],
                line=dict(color="#0f172a", width=2),
            ),
            textinfo="label+percent",
            textfont=dict(color="#e2e8f0", size=12),
            hovertemplate="%{label}<br>Spend: $%{value:,.0f}<br>%{percent}<extra></extra>",
            sort=False,
        ))
        fig_pie.update_layout(
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#e2e8f0"), height=300,
            margin=dict(l=10, r=10, t=10, b=10),
            showlegend=False,
        )
        st.plotly_chart(fig_pie, use_container_width=True)

with mkt_col2:
    st.markdown("### CTR by market")
    if mkt_df.empty:
        st.info("No data for selected period.")
    else:
        fig_ctr_mkt = go.Figure(go.Bar(
            x=mkt_df["ctr_pct"],
            y=mkt_df["market"],
            orientation="h",
            marker_color=[MARKET_COLORS.get(m, "#64748b") for m in mkt_df["market"]],
            text=[f"{v:.2f}%" for v in mkt_df["ctr_pct"]],
            textposition="outside",
            textfont=dict(color="#e2e8f0", size=11),
            hovertemplate="%{y}<br>CTR: %{x:.2f}%<extra></extra>",
        ))
        fig_ctr_mkt.update_layout(
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#e2e8f0"), height=300,
            margin=dict(l=10, r=80, t=10, b=10),
            xaxis=dict(showgrid=False, zeroline=False, ticksuffix="%"),
            yaxis=dict(showgrid=False),
        )
        st.plotly_chart(fig_ctr_mkt, use_container_width=True)

# ── Market summary table ──────────────────────────────────────────────────────
st.markdown("---")
st.markdown("### Market summary")

if mkt_df.empty:
    st.info("No data for selected period.")
else:
    display = mkt_df[["market", "spend_usd", "clicks", "impressions", "ctr_pct", "cpc_usd", "cpm_usd"]].copy()
    display.columns = ["Market", "Spend (USD)", "Clicks", "Impressions", "CTR (%)", "CPC (USD)", "CPM (USD)"]

    # Totals row
    totals = {
        "Market": "TOTAL",
        "Spend (USD)": display["Spend (USD)"].sum(),
        "Clicks":      display["Clicks"].sum(),
        "Impressions": display["Impressions"].sum(),
        "CTR (%)":     display["Clicks"].sum() / display["Impressions"].sum() * 100
                       if display["Impressions"].sum() > 0 else 0.0,
        "CPC (USD)":   display["Spend (USD)"].sum() / display["Clicks"].sum()
                       if display["Clicks"].sum() > 0 else 0.0,
        "CPM (USD)":   display["Spend (USD)"].sum() / display["Impressions"].sum() * 1000
                       if display["Impressions"].sum() > 0 else 0.0,
    }
    display = pd.concat([display, pd.DataFrame([totals])], ignore_index=True)

    display["Spend (USD)"] = display["Spend (USD)"].map(lambda v: f"${v:,.2f}")
    display["Clicks"]      = display["Clicks"].map(lambda v: f"{int(v):,}")
    display["Impressions"] = display["Impressions"].map(lambda v: f"{int(v):,}")
    display["CTR (%)"]     = display["CTR (%)"].map(lambda v: f"{v:.2f}%")
    display["CPC (USD)"]   = display["CPC (USD)"].map(lambda v: f"${v:.2f}")
    display["CPM (USD)"]   = display["CPM (USD)"].map(lambda v: f"${v:.2f}")

    st.dataframe(display, use_container_width=True, hide_index=True)

st.caption(
    f"Source: Meta Ads Daily - Claude · "
    f"Primary: **{pri_label}** · Comparison: **{cmp_label}** · "
    f"Market: **{market_sel}** · Granularity: **{granularity}**"
)
