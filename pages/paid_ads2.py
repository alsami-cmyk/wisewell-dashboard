"""
Paid Ads 2 — Full-funnel performance dashboard.

Combines Meta Ads spend (top of funnel) with Shopify pixel data (mid/bottom
funnel) to provide end-to-end attribution and diagnosis. Auto-flags anomalies
in CTR, CVR, ATC rate, and ROAS proxies.

Sections:
  1. North-Star snapshot (KPIs + delta vs previous period)
  2. Anomaly banner (auto-flagged irregularities)
  3. Full-funnel waterfall (impressions → orders, with conversion rates)
  4. Stage trend chart (rates over time)
  5. Efficiency trend chart (cost per stage over time)
  6. Source attribution (channel mix, paid vs organic)
  7. Top landing pages
  8. Campaign-level Meta breakdown
  9. Day-level detail table
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from utils import (
    fmt_usd,
    load_meta_ads_daily,
    load_meta_ads_campaign_daily,
    load_shopify_website_analytics,
    load_sessions_by_source,
    load_top_landing_pages,
)

st.markdown("## 🎯 Paid Ads 2 — Full-Funnel Diagnostic")
st.caption(
    "Top-of-funnel (Meta Ads) → mid-funnel (Shopify pixel) → bottom-funnel "
    "(orders). Pixel data has ~15–25% undercount vs Shopify-native — use trends, "
    "not absolutes."
)

# ── Data load ─────────────────────────────────────────────────────────────────
ads_daily      = load_meta_ads_daily()
funnel_daily   = load_shopify_website_analytics()
campaigns      = load_meta_ads_campaign_daily()
sources        = load_sessions_by_source()
landing_pages  = load_top_landing_pages()

if ads_daily.empty and funnel_daily.empty:
    st.warning("No Meta Ads or website funnel data available yet.")
    st.stop()

# Date range across all sources
date_candidates = []
for df in (ads_daily, funnel_daily):
    if not df.empty:
        date_candidates.extend([df["date"].min().date(), df["date"].max().date()])
min_date = min(date_candidates)
max_date = max(date_candidates)

# ── Filters ───────────────────────────────────────────────────────────────────
st.markdown("---")
fc1, fc2, fc3, fc4 = st.columns([1.2, 1.5, 1.8, 1.5])

with fc1:
    market_sel = st.selectbox("Market", ["All", "UAE", "KSA", "USA"], key="pa2_market")

with fc2:
    granularity = st.selectbox("Granularity", ["Daily", "Weekly"], key="pa2_gran")

today_d = max_date

with fc3:
    pri_preset = st.selectbox(
        "Date",
        ["Past 7 Days", "Past 30 Days", "Month to Date", "Year to Date", "Custom"],
        index=1,
        key="pa2_preset",
    )
    if pri_preset == "Past 7 Days":
        pri_start, pri_end = today_d - timedelta(days=6), today_d
    elif pri_preset == "Past 30 Days":
        pri_start, pri_end = today_d - timedelta(days=29), today_d
    elif pri_preset == "Month to Date":
        pri_start, pri_end = today_d.replace(day=1), today_d
    elif pri_preset == "Year to Date":
        pri_start, pri_end = today_d.replace(month=1, day=1), today_d
    else:
        _custom = st.date_input(
            "Custom range",
            value=(today_d - timedelta(days=29), today_d),
            min_value=min_date, max_value=max_date,
            key="pa2_pri",
        )
        pri_start, pri_end = (
            _custom if isinstance(_custom, (list, tuple)) and len(_custom) == 2
            else (today_d - timedelta(days=29), today_d)
        )

with fc4:
    cmp_mode = st.selectbox(
        "Compare to",
        ["Previous period", "Same period last year"],
        key="pa2_cmp",
    )

pri_days = (pri_end - pri_start).days + 1
if cmp_mode == "Previous period":
    cmp_end   = pri_start - timedelta(days=1)
    cmp_start = cmp_end - timedelta(days=pri_days - 1)
else:
    cmp_start = date(pri_start.year - 1, pri_start.month, pri_start.day)
    cmp_end   = date(pri_end.year   - 1, pri_end.month,   pri_end.day)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _filter(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if df.empty:
        return df
    mask = (df["date"].dt.date >= start) & (df["date"].dt.date <= end)
    if market_sel != "All" and "market" in df.columns:
        mask &= df["market"] == market_sel
    return df.loc[mask].copy()


def _agg_funnel(start: date, end: date) -> dict:
    a = _filter(ads_daily,    start, end)
    f = _filter(funnel_daily, start, end)
    spend       = float(a["spend_usd"].sum())   if not a.empty else 0.0
    clicks      = int(a["clicks"].sum())        if not a.empty else 0
    impressions = int(a["impressions"].sum())   if not a.empty else 0
    sessions    = int(f["sessions"].sum())      if not f.empty else 0
    new_sess    = int(f["new_sessions"].sum())  if "new_sessions" in f.columns else 0
    atc         = int(f["add_to_cart"].sum())   if not f.empty else 0
    reached     = int(f["reached_checkout"].sum())   if not f.empty else 0
    completed   = int(f["completed_checkout"].sum()) if not f.empty else 0

    return {
        "spend": spend, "clicks": clicks, "impressions": impressions,
        "sessions": sessions, "new_sessions": new_sess,
        "atc": atc, "reached": reached, "completed": completed,
        "ctr":           (clicks / impressions)  if impressions else 0.0,
        "cpc":           (spend  / clicks)       if clicks      else 0.0,
        "cpm":           (spend  / impressions * 1000) if impressions else 0.0,
        "cps":           (spend  / sessions)     if sessions    else 0.0,
        "cpatc":         (spend  / atc)          if atc         else 0.0,
        "cpp":           (spend  / completed)    if completed   else 0.0,
        "atc_rate":      (atc   / sessions)      if sessions    else 0.0,
        "checkout_rate": (reached / atc)         if atc         else 0.0,
        "purchase_rate": (completed / reached)   if reached     else 0.0,
        "cvr":           (completed / sessions)  if sessions    else 0.0,
    }


def _delta_pct(cur: float, prev: float) -> float | None:
    if prev == 0 or prev is None:
        return None
    return (cur - prev) / prev * 100


def _pct(x: float, decimals: int = 2) -> str:
    return f"{x*100:.{decimals}f}%"


def _delta_str(d: float | None, invert: bool = False, suffix: str = "%") -> str:
    if d is None:
        return ""
    sign = "▲" if d >= 0 else "▼"
    if invert:
        sign = "▼" if d >= 0 else "▲"
    return f"{sign} {abs(d):.1f}{suffix}"


pri  = _agg_funnel(pri_start, pri_end)
cmp_ = _agg_funnel(cmp_start, cmp_end)


# ── Section 1: North-Star Snapshot ────────────────────────────────────────────
st.markdown("### North-Star Snapshot")
st.caption(
    f"**{pri_start:%d %b %Y}** → **{pri_end:%d %b %Y}** "
    f"vs **{cmp_start:%d %b %Y}** → **{cmp_end:%d %b %Y}** "
    f"({pri_days} days)"
)

k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Spend",       fmt_usd(pri["spend"]),                   _delta_str(_delta_pct(pri["spend"],     cmp_["spend"])))
k2.metric("Sessions",    f"{pri['sessions']:,}",                  _delta_str(_delta_pct(pri["sessions"],  cmp_["sessions"])))
k3.metric("Add to Cart", f"{pri['atc']:,}",                       _delta_str(_delta_pct(pri["atc"],       cmp_["atc"])))
k4.metric("Orders",      f"{pri['completed']:,}",                 _delta_str(_delta_pct(pri["completed"], cmp_["completed"])))
k5.metric("CVR",         _pct(pri["cvr"]),                        _delta_str(_delta_pct(pri["cvr"],       cmp_["cvr"])))
k6.metric("Cost/Order",  fmt_usd(pri["cpp"])  if pri["cpp"]  else "—",  _delta_str(_delta_pct(pri["cpp"], cmp_["cpp"]), invert=True))

k7, k8, k9, k10, k11, k12 = st.columns(6)
k7.metric("Impressions", f"{pri['impressions']:,}",               _delta_str(_delta_pct(pri["impressions"], cmp_["impressions"])))
k8.metric("Clicks",      f"{pri['clicks']:,}",                    _delta_str(_delta_pct(pri["clicks"],   cmp_["clicks"])))
k9.metric("CTR",         _pct(pri["ctr"]),                        _delta_str(_delta_pct(pri["ctr"],      cmp_["ctr"])))
k10.metric("CPC",        fmt_usd(pri["cpc"]) if pri["cpc"]  else "—", _delta_str(_delta_pct(pri["cpc"], cmp_["cpc"]),    invert=True))
k11.metric("CPM",        fmt_usd(pri["cpm"]) if pri["cpm"]  else "—", _delta_str(_delta_pct(pri["cpm"], cmp_["cpm"]),    invert=True))
k12.metric("Cost/Sess",  fmt_usd(pri["cps"]) if pri["cps"]  else "—", _delta_str(_delta_pct(pri["cps"], cmp_["cps"]),    invert=True))


# ── Section 2: Anomaly Banner ─────────────────────────────────────────────────
st.markdown("---")
st.markdown("### 🚨 Anomalies (auto-flagged)")

flags: list[tuple[str, str]] = []  # (severity, message)

def _rolling_check(series: pd.Series, current: float, label: str, threshold: float = 0.7,
                   invert: bool = False, window: int = 7):
    """If current value < threshold × rolling mean (or > 1/threshold for invert)."""
    if len(series) < window + 1:
        return
    baseline = series.iloc[-window-1:-1].mean()
    if baseline <= 0:
        return
    ratio = current / baseline
    if invert:
        if ratio > (1 / threshold):
            flags.append(("🔴", f"**{label}** is {(ratio-1)*100:.0f}% above {window}d avg "
                                f"({current:.2f} vs avg {baseline:.2f})"))
    else:
        if ratio < threshold:
            flags.append(("🔴", f"**{label}** is {(1-ratio)*100:.0f}% below {window}d avg "
                                f"({current:.2f} vs avg {baseline:.2f})"))


# Build a daily merged frame for the primary period for rolling comparisons
def _daily_merged(df_funnel: pd.DataFrame, df_ads: pd.DataFrame) -> pd.DataFrame:
    f = df_funnel.copy() if not df_funnel.empty else pd.DataFrame()
    a = df_ads.copy()    if not df_ads.empty    else pd.DataFrame()
    if not f.empty:
        f["date"] = pd.to_datetime(f["date"]).dt.normalize()
        f_day = f.groupby("date", as_index=False).agg(
            sessions=("sessions","sum"), atc=("add_to_cart","sum"),
            reached=("reached_checkout","sum"), completed=("completed_checkout","sum"),
        )
    else:
        f_day = pd.DataFrame(columns=["date", "sessions", "atc", "reached", "completed"])
    if not a.empty:
        a["date"] = pd.to_datetime(a["date"]).dt.normalize()
        a_day = a.groupby("date", as_index=False).agg(
            spend=("spend_usd","sum"), clicks=("clicks","sum"), impressions=("impressions","sum"),
        )
    else:
        a_day = pd.DataFrame(columns=["date", "spend", "clicks", "impressions"])
    return pd.merge(f_day, a_day, on="date", how="outer").fillna(0).sort_values("date")


_funnel_for_check = _filter(funnel_daily, pri_start - timedelta(days=14), pri_end)
_ads_for_check    = _filter(ads_daily,    pri_start - timedelta(days=14), pri_end)
m_daily = _daily_merged(_funnel_for_check, _ads_for_check)

if not m_daily.empty:
    m_daily["ctr"]      = np.where(m_daily["impressions"] > 0, m_daily["clicks"] / m_daily["impressions"], 0)
    m_daily["cvr"]      = np.where(m_daily["sessions"]    > 0, m_daily["completed"] / m_daily["sessions"], 0)
    m_daily["atc_rate"] = np.where(m_daily["sessions"]    > 0, m_daily["atc"] / m_daily["sessions"], 0)
    m_daily["cpc"]      = np.where(m_daily["clicks"] > 0, m_daily["spend"] / m_daily["clicks"], 0)
    m_daily["cpp"]      = np.where(m_daily["completed"] > 0, m_daily["spend"] / m_daily["completed"], 0)

    # Last day in primary range
    last = m_daily[m_daily["date"].dt.date == pri_end]
    if not last.empty:
        last_row = last.iloc[0]
        # Rolling-window comparisons for the latest day
        prior = m_daily[m_daily["date"].dt.date < pri_end]
        if len(prior) >= 7:
            _rolling_check(prior["cvr"],      last_row["cvr"],      "CVR cliff (last day)")
            _rolling_check(prior["ctr"],      last_row["ctr"],      "CTR drop (last day)")
            _rolling_check(prior["atc_rate"], last_row["atc_rate"], "ATC rate drop (last day)")
            _rolling_check(prior["cpc"],      last_row["cpc"],      "CPC spike (last day)", invert=True)
            _rolling_check(prior["cpp"],      last_row["cpp"],      "Cost-per-order spike (last day)", invert=True)

    # Spend with no orders day
    bad_days = m_daily[(m_daily["spend"] > 100) & (m_daily["completed"] == 0)]
    if len(bad_days) > 0:
        for _, r in bad_days.tail(3).iterrows():
            flags.append(("🟡", f"Spent **{fmt_usd(r['spend'])}** on **{r['date']:%d %b}** with zero orders"))

    # Sessions with no clicks tracked
    sess_no_clicks = m_daily[(m_daily["sessions"] > 50) & (m_daily["clicks"] == 0)]
    if len(sess_no_clicks) > 0:
        flags.append(("🟡", f"{len(sess_no_clicks)} day(s) with sessions but no Meta clicks tracked — "
                            "could indicate Meta sync gap or organic-heavy traffic"))

# Period-level checks
if pri["spend"] > 0 and pri["completed"] == 0:
    flags.append(("🔴", "Spend > $0 but **zero orders** in selected period — pixel or campaign broken"))
if cmp_["cvr"] > 0 and pri["cvr"] < 0.7 * cmp_["cvr"]:
    flags.append(("🔴", f"CVR is **{(1 - pri['cvr']/cmp_['cvr'])*100:.0f}% below** previous period "
                        f"({_pct(pri['cvr'])} vs {_pct(cmp_['cvr'])})"))

if not flags:
    st.success("✅ No anomalies detected against the rolling baseline.")
else:
    for sev, msg in flags:
        if sev == "🔴":
            st.error(f"{sev} {msg}")
        else:
            st.warning(f"{sev} {msg}")


# ── Section 3: Full-Funnel Waterfall ──────────────────────────────────────────
st.markdown("---")
st.markdown("### 🔻 Full Funnel")

stages = [
    ("Impressions",  pri["impressions"]),
    ("Clicks",       pri["clicks"]),
    ("Sessions",     pri["sessions"]),
    ("Add to Cart",  pri["atc"]),
    ("Checkout",     pri["reached"]),
    ("Purchases",    pri["completed"]),
]
stage_labels = []
for i, (name, val) in enumerate(stages):
    if i == 0 or stages[i-1][1] == 0:
        rate_str = ""
    else:
        rate = val / stages[i-1][1] * 100
        rate_str = f"<br><span style='font-size:11px'>{rate:.1f}% from prev</span>"
    stage_labels.append(f"{name}<br><b>{val:,}</b>{rate_str}")

fig_funnel = go.Figure(go.Funnel(
    y=stage_labels,
    x=[max(v, 1) for _, v in stages],
    textinfo="none",
    marker={"color": ["#6366f1", "#818cf8", "#06b6d4", "#0ea5e9", "#10b981", "#22c55e"]},
))
fig_funnel.update_layout(
    margin=dict(l=10, r=10, t=10, b=10),
    height=420,
)
st.plotly_chart(fig_funnel, use_container_width=True)


# ── Section 4: Stage trends ───────────────────────────────────────────────────
st.markdown("### 📈 Funnel Stage Rates Over Time")

if not m_daily.empty:
    plot_df = m_daily[(m_daily["date"].dt.date >= pri_start) & (m_daily["date"].dt.date <= pri_end)].copy()
    if granularity == "Weekly":
        plot_df = (plot_df
                   .set_index("date")
                   .resample("W-MON")
                   .agg({"impressions":"sum","clicks":"sum","spend":"sum",
                         "sessions":"sum","atc":"sum","reached":"sum","completed":"sum"})
                   .reset_index())
        plot_df["ctr"]      = np.where(plot_df["impressions"] > 0, plot_df["clicks"] / plot_df["impressions"], 0)
        plot_df["atc_rate"] = np.where(plot_df["sessions"]    > 0, plot_df["atc"] / plot_df["sessions"], 0)
        plot_df["checkout_rate"] = np.where(plot_df["atc"] > 0, plot_df["reached"] / plot_df["atc"], 0)
        plot_df["cvr"]      = np.where(plot_df["sessions"]    > 0, plot_df["completed"] / plot_df["sessions"], 0)
    else:
        plot_df["ctr"]           = np.where(plot_df["impressions"] > 0, plot_df["clicks"] / plot_df["impressions"], 0)
        plot_df["atc_rate"]      = np.where(plot_df["sessions"]    > 0, plot_df["atc"] / plot_df["sessions"], 0)
        plot_df["checkout_rate"] = np.where(plot_df["atc"] > 0, plot_df["reached"] / plot_df["atc"], 0)
        plot_df["cvr"]           = np.where(plot_df["sessions"]    > 0, plot_df["completed"] / plot_df["sessions"], 0)

    if not plot_df.empty:
        fig_rates = go.Figure()
        fig_rates.add_trace(go.Scatter(x=plot_df["date"], y=plot_df["ctr"]*100,
                                       mode="lines+markers", name="CTR %",      line=dict(color="#6366f1")))
        fig_rates.add_trace(go.Scatter(x=plot_df["date"], y=plot_df["atc_rate"]*100,
                                       mode="lines+markers", name="ATC %",      line=dict(color="#06b6d4")))
        fig_rates.add_trace(go.Scatter(x=plot_df["date"], y=plot_df["checkout_rate"]*100,
                                       mode="lines+markers", name="Checkout %", line=dict(color="#0ea5e9")))
        fig_rates.add_trace(go.Scatter(x=plot_df["date"], y=plot_df["cvr"]*100,
                                       mode="lines+markers", name="CVR %",      line=dict(color="#22c55e")))
        fig_rates.update_layout(
            margin=dict(l=10, r=10, t=10, b=10),
            height=350,
            yaxis=dict(title="Rate (%)"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig_rates, use_container_width=True)
    else:
        st.info("No data for selected range.")


# ── Section 5: Efficiency trends ──────────────────────────────────────────────
st.markdown("### 💰 Cost Efficiency Over Time")

if not m_daily.empty:
    eff_df = m_daily[(m_daily["date"].dt.date >= pri_start) & (m_daily["date"].dt.date <= pri_end)].copy()
    if granularity == "Weekly":
        eff_df = (eff_df.set_index("date").resample("W-MON")
                  .agg({"spend":"sum","clicks":"sum","sessions":"sum",
                        "atc":"sum","completed":"sum"})
                  .reset_index())
    eff_df["cpc"]   = np.where(eff_df["clicks"]    > 0, eff_df["spend"] / eff_df["clicks"],    0)
    eff_df["cps"]   = np.where(eff_df["sessions"]  > 0, eff_df["spend"] / eff_df["sessions"],  0)
    eff_df["cpatc"] = np.where(eff_df["atc"]       > 0, eff_df["spend"] / eff_df["atc"],       0)
    eff_df["cpp"]   = np.where(eff_df["completed"] > 0, eff_df["spend"] / eff_df["completed"], 0)

    if not eff_df.empty:
        fig_eff = make_subplots(specs=[[{"secondary_y": True}]])
        fig_eff.add_trace(go.Bar(x=eff_df["date"], y=eff_df["spend"], name="Spend $",
                                 marker_color="rgba(99,102,241,0.4)"), secondary_y=False)
        fig_eff.add_trace(go.Scatter(x=eff_df["date"], y=eff_df["cpc"],   mode="lines+markers",
                                     name="CPC $",       line=dict(color="#06b6d4")), secondary_y=True)
        fig_eff.add_trace(go.Scatter(x=eff_df["date"], y=eff_df["cps"],   mode="lines+markers",
                                     name="Cost/Session $", line=dict(color="#0ea5e9")), secondary_y=True)
        fig_eff.add_trace(go.Scatter(x=eff_df["date"], y=eff_df["cpp"],   mode="lines+markers",
                                     name="Cost/Order $",   line=dict(color="#ef4444")), secondary_y=True)
        fig_eff.update_layout(
            margin=dict(l=10, r=10, t=10, b=10),
            height=350,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        fig_eff.update_yaxes(title_text="Spend ($)",   secondary_y=False)
        fig_eff.update_yaxes(title_text="Per-unit ($)", secondary_y=True)
        st.plotly_chart(fig_eff, use_container_width=True)


# ── Section 6: Source attribution ─────────────────────────────────────────────
st.markdown("---")
st.markdown("### 🌐 Traffic Source Attribution")

if sources.empty:
    st.info("Source attribution data starts populating tomorrow's first nightly aggregation. "
            "It uses the new pixel fields (utm/referrer/fbclid/gclid).")
else:
    src = _filter(sources, pri_start, pri_end)
    if src.empty:
        st.info("No source data for selected period yet.")
    else:
        ch_agg = (src.groupby("channel", as_index=False)
                  .agg(sessions=("sessions","sum"), atc=("add_to_cart","sum"),
                       reached=("reached_checkout","sum"), completed=("completed_checkout","sum"))
                  .sort_values("sessions", ascending=False))
        ch_agg["cvr_pct"] = np.where(ch_agg["sessions"] > 0,
                                     ch_agg["completed"] / ch_agg["sessions"] * 100, 0).round(2)
        ch_agg["atc_pct"] = np.where(ch_agg["sessions"] > 0,
                                     ch_agg["atc"] / ch_agg["sessions"] * 100, 0).round(2)

        cc1, cc2 = st.columns([1.2, 1.5])

        with cc1:
            fig_src = go.Figure(go.Pie(
                labels=ch_agg["channel"],
                values=ch_agg["sessions"],
                hole=0.45,
                textinfo="label+percent",
            ))
            fig_src.update_layout(margin=dict(l=10,r=10,t=10,b=10), height=350,
                                  showlegend=False, title="Sessions by channel")
            st.plotly_chart(fig_src, use_container_width=True)

        with cc2:
            display = ch_agg.rename(columns={
                "channel":"Channel", "sessions":"Sessions", "atc":"ATC",
                "reached":"Checkout", "completed":"Orders",
                "atc_pct":"ATC %", "cvr_pct":"CVR %",
            })
            st.dataframe(
                display[["Channel","Sessions","ATC","Checkout","Orders","ATC %","CVR %"]],
                hide_index=True, use_container_width=True,
            )


# ── Section 7: Top landing pages ──────────────────────────────────────────────
st.markdown("---")
st.markdown("### 🏠 Top Landing Pages")

if landing_pages.empty:
    st.info("Landing page data starts populating tomorrow's first nightly aggregation.")
else:
    lp = _filter(landing_pages, pri_start, pri_end)
    if lp.empty:
        st.info("No landing page data for selected period yet.")
    else:
        lp_agg = (lp.groupby(["market","page_path"], as_index=False)
                    .agg(sessions=("sessions","sum"), atc=("add_to_cart","sum"))
                    .sort_values(["market","sessions"], ascending=[True,False]))
        lp_agg["atc_rate_pct"] = np.where(lp_agg["sessions"] > 0,
                                          lp_agg["atc"] / lp_agg["sessions"] * 100, 0).round(2)
        if market_sel != "All":
            lp_agg = lp_agg[lp_agg["market"] == market_sel]
        lp_agg = lp_agg.head(15)
        lp_agg = lp_agg.rename(columns={
            "market":"Market","page_path":"Page","sessions":"Sessions",
            "atc":"ATCs","atc_rate_pct":"ATC %",
        })
        st.dataframe(lp_agg, hide_index=True, use_container_width=True)


# ── Section 8: Campaign-level breakdown ───────────────────────────────────────
st.markdown("---")
st.markdown("### 🎯 Meta Campaign Performance")

if campaigns.empty:
    st.info("Campaign-level data populates after the next Meta sync runs (every 12h). "
            "Run `bash scripts/run_meta_sync.sh` to trigger immediately.")
else:
    c = _filter(campaigns, pri_start, pri_end)
    if c.empty:
        st.info("No campaign data for selected period.")
    else:
        c_agg = (c.groupby(["market","campaign_name","objective","status"], as_index=False)
                  .agg(spend=("spend_usd","sum"), clicks=("clicks","sum"),
                       impressions=("impressions","sum")))
        c_agg["ctr_pct"] = np.where(c_agg["impressions"] > 0,
                                    c_agg["clicks"] / c_agg["impressions"] * 100, 0).round(2)
        c_agg["cpc"]     = np.where(c_agg["clicks"] > 0, c_agg["spend"] / c_agg["clicks"], 0).round(2)
        c_agg["cpm"]     = np.where(c_agg["impressions"] > 0,
                                    c_agg["spend"] / c_agg["impressions"] * 1000, 0).round(2)
        c_agg = c_agg.sort_values("spend", ascending=False)
        c_agg = c_agg.rename(columns={
            "market":"Market","campaign_name":"Campaign","objective":"Objective","status":"Status",
            "spend":"Spend $","clicks":"Clicks","impressions":"Impressions",
            "ctr_pct":"CTR %","cpc":"CPC $","cpm":"CPM $",
        })
        st.dataframe(c_agg, hide_index=True, use_container_width=True)


# ── Section 9: Day-level detail ───────────────────────────────────────────────
st.markdown("---")
with st.expander("🔍 Day-level detail (export-ready)"):
    if m_daily.empty:
        st.info("No daily data.")
    else:
        detail = m_daily[(m_daily["date"].dt.date >= pri_start) & (m_daily["date"].dt.date <= pri_end)].copy()
        detail["CTR %"]      = (np.where(detail["impressions"] > 0, detail["clicks"]/detail["impressions"]*100, 0)).round(2)
        detail["CVR %"]      = (np.where(detail["sessions"]    > 0, detail["completed"]/detail["sessions"]*100, 0)).round(2)
        detail["ATC %"]      = (np.where(detail["sessions"]    > 0, detail["atc"]/detail["sessions"]*100, 0)).round(2)
        detail["CPC $"]      = (np.where(detail["clicks"]    > 0, detail["spend"]/detail["clicks"], 0)).round(2)
        detail["CPP $"]      = (np.where(detail["completed"] > 0, detail["spend"]/detail["completed"], 0)).round(2)
        detail["date"] = detail["date"].dt.strftime("%Y-%m-%d")
        detail = detail.rename(columns={
            "date":"Date","spend":"Spend $","clicks":"Clicks","impressions":"Impressions",
            "sessions":"Sessions","atc":"ATCs","reached":"Checkout","completed":"Orders",
        })
        cols = ["Date","Spend $","Impressions","Clicks","CTR %","CPC $",
                "Sessions","ATCs","ATC %","Checkout","Orders","CVR %","CPP $"]
        st.dataframe(detail[cols], hide_index=True, use_container_width=True)
        st.download_button(
            "Download CSV",
            detail[cols].to_csv(index=False),
            file_name=f"paid_ads2_{pri_start}_{pri_end}.csv",
            mime="text/csv",
        )


st.caption("All cost-per metrics use Meta spend; conversion metrics use Shopify pixel data. "
           "Pixel undercounts vs Shopify-native by ~15–25% — interpret trends, not absolutes.")
