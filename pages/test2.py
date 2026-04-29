"""
Test 2 — Churn Analysis (Recharge-style).

Mirrors the Test page layout with churn-focused scorecards & charts.
Scope: Machine subscriptions only · true cancellations only (swaps excluded).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils import (
    PRODUCT_COLOR,
    PRODUCT_ORDER,
    get_active_subscriptions,
    load_recharge_full,
)

# ── Page header ───────────────────────────────────────────────────────────────
st.markdown("## 🔄 Churn analysis")
st.caption("Machine subscriptions · true cancels only (swaps/conversions excluded).")

# ── Inline filter bar ─────────────────────────────────────────────────────────
today_d = date.today()

c1, c2, c3, c4, c5 = st.columns([2.4, 2.4, 1.4, 1.8, 1.4])

with c1:
    preset = st.selectbox(
        "Date",
        ["Month to Date", "Past 7 Days", "Year to Date", "Custom"],
        key="t2_preset",
    )
    if preset == "Month to Date":
        p_start, p_end = today_d.replace(day=1), today_d
    elif preset == "Past 7 Days":
        p_start, p_end = today_d - timedelta(days=6), today_d
    elif preset == "Year to Date":
        p_start, p_end = today_d.replace(month=1, day=1), today_d
    else:
        _custom = st.date_input("Custom range", value=(today_d.replace(day=1), today_d), key="t2_daterange")
        p_start, p_end = (_custom if isinstance(_custom, (list, tuple)) and len(_custom) == 2
                          else (today_d.replace(day=1), today_d))
period_days       = max(1, (p_end - p_start).days)
cmp_end_default   = p_start - timedelta(days=1)
cmp_start_default = cmp_end_default - timedelta(days=period_days)

with c2:
    cmp_range = st.date_input(
        "Compare to",
        value=(cmp_start_default, cmp_end_default),
        key="t2_cmp_range",
    )

with c3:
    granularity = st.selectbox(
        "Display", ["Daily", "Monthly", "Quarterly"], index=0, key="t2_gran",
    )

with c4:
    product_sel = st.selectbox(
        "Product", ["All"] + PRODUCT_ORDER, key="t2_product",
    )

with c5:
    country_sel = st.selectbox(
        "Country", ["All", "UAE", "KSA", "USA"], key="t2_country",
    )

if isinstance(cmp_range, (list, tuple)) and len(cmp_range) == 2:
    cmp_start, cmp_end = cmp_range
else:
    cmp_start, cmp_end = cmp_start_default, cmp_end_default

p_start_ts   = pd.Timestamp(p_start)
p_end_ts     = pd.Timestamp(p_end)
cmp_start_ts = pd.Timestamp(cmp_start)
cmp_end_ts   = pd.Timestamp(cmp_end)

mkt_filter  = None if country_sel == "All" else country_sel
prod_filter = None if product_sel == "All" else product_sel


# ── Helpers ───────────────────────────────────────────────────────────────────
def _apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df
    if mkt_filter and "market" in out.columns:
        out = out[out["market"] == mkt_filter]
    if prod_filter and "product" in out.columns:
        out = out[out["product"] == prod_filter]
    return out


def _active_at(ts: pd.Timestamp) -> int:
    """Active machine subs count at a timestamp."""
    a = _apply_filters(get_active_subscriptions(as_of=ts))
    return int(a["qty"].sum()) if a is not None and not a.empty else 0


def _churns_in(start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> pd.DataFrame:
    """True cancels within [start, end] — filtered by market/product."""
    rc = load_recharge_full()
    if rc.empty:
        return rc
    rc_m = rc[rc["category"] == "Machine"].copy()
    rc_m = _apply_filters(rc_m)
    if rc_m is None or rc_m.empty:
        return pd.DataFrame()
    mask = (
        rc_m["is_true_cancel"]
        & rc_m["cancelled_at_dt"].notna()
        & (rc_m["cancelled_at_dt"] >= start_ts)
        & (rc_m["cancelled_at_dt"] <= end_ts)
    )
    return rc_m.loc[mask].copy()


def _period_metrics(start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> dict:
    active_start = _active_at(start_ts)
    churn_df     = _churns_in(start_ts, end_ts)
    churned      = int(churn_df["quantity"].sum()) if not churn_df.empty else 0
    rate         = churned / active_start if active_start > 0 else 0.0

    # Avg lifetime (months) of churned subs in window
    if not churn_df.empty and "created_at_dt" in churn_df.columns:
        lt_days = (churn_df["cancelled_at_dt"] - churn_df["created_at_dt"]).dt.days
        lt_days = lt_days[lt_days.notna() & (lt_days >= 0)]
        avg_lt_months = float(lt_days.mean() / 30.44) if len(lt_days) else 0.0
    else:
        avg_lt_months = 0.0

    # Top cancellation reason
    if not churn_df.empty and "cancellation_reason" in churn_df.columns:
        reason_counts = (
            churn_df.groupby("cancellation_reason")["quantity"].sum().sort_values(ascending=False)
        )
        top_reason = reason_counts.index[0] if len(reason_counts) else "—"
    else:
        top_reason = "—"

    return {
        "rate":          rate,
        "active_start":  active_start,
        "churned":       churned,
        "avg_lifetime":  avg_lt_months,
        "top_reason":    top_reason,
    }


def _delta_pct(cur_val: float, prev_val: float) -> float | None:
    if prev_val == 0:
        return None if cur_val == 0 else (100.0 if cur_val > 0 else -100.0)
    return (cur_val - prev_val) / prev_val * 100


def _fmt_delta(delta: float | None) -> str:
    if delta is None:
        return "—"
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.1f}%"


# ── Compute current + comparison ──────────────────────────────────────────────
cur_m  = _period_metrics(p_start_ts,   p_end_ts)
prev_m = _period_metrics(cmp_start_ts, cmp_end_ts)

# ── Scorecards ────────────────────────────────────────────────────────────────
st.markdown("---")
k1, k2, k3, k4, k5 = st.columns(5)

k1.metric(
    "CANCELLATION RATE",
    f"{cur_m['rate']:.2%}",
    delta=_fmt_delta(_delta_pct(cur_m["rate"], prev_m["rate"])),
    delta_color="inverse",
    help="Churned in period ÷ active at start of period.",
)
k2.metric(
    "ACTIVE SUBS AT START",
    f"{cur_m['active_start']:,}",
    delta=_fmt_delta(_delta_pct(cur_m["active_start"], prev_m["active_start"])),
    help="Active machine subscriptions at the first day of the selected period.",
)
k3.metric(
    "CHURNED CUSTOMERS",
    f"{cur_m['churned']:,}",
    delta=_fmt_delta(_delta_pct(cur_m["churned"], prev_m["churned"])),
    delta_color="inverse",
)
k4.metric(
    "AVG LIFETIME (MONTHS)",
    f"{cur_m['avg_lifetime']:.1f}",
    delta=_fmt_delta(_delta_pct(cur_m["avg_lifetime"], prev_m["avg_lifetime"])),
    help="Average months between created_at and cancelled_at for churned subs in the window.",
)
k5.metric(
    "TOP CANCELLATION REASON",
    cur_m["top_reason"],
    help="Most common normalised reason among churned subs in the window.",
)

# ── Period buckets ────────────────────────────────────────────────────────────
if granularity == "Daily":
    bucket_dates = pd.date_range(p_start_ts, p_end_ts, freq="D")
    period_code = "D"
elif granularity == "Monthly":
    # Month starts inside the window
    bucket_dates = pd.date_range(
        p_start_ts.to_period("M").to_timestamp(),
        p_end_ts,
        freq="MS",
    )
    period_code = "M"
else:  # Quarterly
    bucket_dates = pd.date_range(
        p_start_ts.to_period("Q").to_timestamp(),
        p_end_ts,
        freq="QS",
    )
    period_code = "Q"

if len(bucket_dates) == 0:
    bucket_dates = pd.DatetimeIndex([p_start_ts])


# ── Chart 1: Cancellation rate / count over time ──────────────────────────────
st.markdown("---")
mode_col, _ = st.columns([2, 6])
with mode_col:
    mode = st.radio(
        "Metric",
        ["Churn rate", "Cancellation count"],
        index=1,
        horizontal=True,
        key="t2_chart1_mode",
        label_visibility="collapsed",
    )

# Clamp buckets to window and compute metric per bucket
today_ts = pd.Timestamp.today().normalize()
rate_x, rate_y, count_y = [], [], []
for i, bstart in enumerate(bucket_dates):
    # end of bucket = next bucket start − 1 day, or p_end_ts
    if i + 1 < len(bucket_dates):
        bend = bucket_dates[i + 1] - pd.Timedelta(days=1)
    else:
        bend = p_end_ts
    # Clamp
    bstart_c = max(bstart, p_start_ts)
    bend_c   = min(bend,   p_end_ts, today_ts)
    if bstart_c > bend_c:
        continue

    active = _active_at(bstart_c)
    churn_df = _churns_in(bstart_c, bend_c)
    churned  = int(churn_df["quantity"].sum()) if not churn_df.empty else 0
    rate     = (churned / active) if active > 0 else 0.0

    rate_x.append(bstart_c)
    rate_y.append(rate)
    count_y.append(churned)

fig_rate = go.Figure()
# Sub-monthly buckets (e.g. daily) produce tiny churn rates — use 2 decimals
# so variation is visible. Monthly/quarterly buckets stay at 1 decimal.
_use_two_dp = granularity == "Daily"
_label_fmt  = "{:.2f}%" if _use_two_dp else "{:.1f}%"
_axis_fmt   = ".2%" if _use_two_dp else ".1%"
_hover_fmt  = ".3%" if _use_two_dp else ".2%"

if mode == "Churn rate":
    fig_rate.add_trace(
        go.Scatter(
            x=rate_x,
            y=rate_y,
            mode="lines+markers+text",
            line=dict(color="#ef4444", width=2.5),
            marker=dict(color="#ef4444", size=7, line=dict(color="#1e293b", width=1)),
            text=[_label_fmt.format(v * 100) for v in rate_y],
            textposition="top center",
            textfont=dict(color="#e2e8f0", size=11),
            cliponaxis=False,
            hovertemplate="%{x|%b %d, %Y}<br>Rate: %{y:" + _hover_fmt + "}<extra></extra>",
        )
    )
    title = f"<b>CHURN RATE OVER TIME · {granularity.upper()}</b>"
    y_fmt = _axis_fmt
else:
    fig_rate.add_trace(
        go.Bar(
            x=rate_x,
            y=count_y,
            marker_color="#ef4444",
            text=[f"{int(v):,}" for v in count_y],
            textposition="outside",
            textfont=dict(color="#e2e8f0", size=11),
            cliponaxis=False,
            hovertemplate="%{x|%b %d, %Y}<br>Cancellations: %{y:,}<extra></extra>",
        )
    )
    title = f"<b>CANCELLATIONS OVER TIME · {granularity.upper()}</b>"
    y_fmt = ",d"

fig_rate.update_layout(
    title=dict(text=title, font=dict(size=12, color="#94a3b8"), x=0.01, xanchor="left"),
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#e2e8f0", size=11),
    height=340,
    margin=dict(l=10, r=10, t=45, b=30),
    xaxis=dict(showgrid=False),
    yaxis=dict(gridcolor="rgba(148,163,184,0.15)", zeroline=False, tickformat=y_fmt),
    showlegend=False,
)
st.plotly_chart(fig_rate, use_container_width=True)


# ── Chart 2: Cancellation reasons, stacked by product ─────────────────────────
st.markdown("---")
churn_period = _churns_in(p_start_ts, p_end_ts)

if churn_period.empty:
    st.info("No cancellations in the selected period.")
else:
    # Aggregate by reason × product
    reason_prod = (
        churn_period
        .groupby(["cancellation_reason", "product"], as_index=False)["quantity"]
        .sum()
    )
    # Order reasons by total desc
    reason_totals = (
        reason_prod.groupby("cancellation_reason")["quantity"].sum()
        .sort_values(ascending=True)  # ascending because plotly horizontal bar draws bottom-up
    )
    reason_order = reason_totals.index.tolist()

    fig_reasons = go.Figure()

    if prod_filter:
        # Single product — one trace
        sub = reason_prod[reason_prod["product"] == prod_filter]
        sub = sub.set_index("cancellation_reason").reindex(reason_order, fill_value=0).reset_index()
        fig_reasons.add_trace(
            go.Bar(
                y=sub["cancellation_reason"],
                x=sub["quantity"],
                orientation="h",
                marker_color=PRODUCT_COLOR.get(prod_filter, "#818cf8"),
                text=[str(int(v)) if v > 0 else "" for v in sub["quantity"]],
                textposition="inside",
                textfont=dict(color="white", size=11),
                name=prod_filter,
                hovertemplate="%{y}<br>" + prod_filter + ": %{x}<extra></extra>",
            )
        )
    else:
        # Stack per product
        for prod in PRODUCT_ORDER:
            sub = reason_prod[reason_prod["product"] == prod]
            if sub.empty:
                continue
            sub = sub.set_index("cancellation_reason").reindex(reason_order, fill_value=0).reset_index()
            fig_reasons.add_trace(
                go.Bar(
                    y=sub["cancellation_reason"],
                    x=sub["quantity"],
                    orientation="h",
                    marker_color=PRODUCT_COLOR.get(prod, "#818cf8"),
                    text=[str(int(v)) if v > 0 else "" for v in sub["quantity"]],
                    textposition="inside",
                    textfont=dict(color="white", size=11),
                    name=prod,
                    hovertemplate="%{y}<br>" + prod + ": %{x}<extra></extra>",
                )
            )

    # Annotations: total count + % of all cancels, placed at the right edge
    grand_total = float(reason_totals.sum()) if reason_totals.sum() else 0.0
    max_total   = float(reason_totals.max()) if len(reason_totals) else 0.0
    annotations = []
    for reason in reason_order:
        total = float(reason_totals.loc[reason])
        pct   = (total / grand_total * 100) if grand_total > 0 else 0.0
        annotations.append(dict(
            x=total,
            y=reason,
            text=f"<b>{int(total)}</b>  ·  {pct:.0f}%",
            showarrow=False,
            xanchor="left",
            yanchor="middle",
            font=dict(color="#e2e8f0", size=11),
            xshift=8,  # small gap right of the bar
        ))

    fig_reasons.update_layout(
        barmode="stack",
        title=dict(
            text="<b>CANCELLATION REASONS BY PRODUCT</b>",
            font=dict(size=12, color="#94a3b8"), x=0.01, xanchor="left",
        ),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e2e8f0", size=11),
        height=max(340, 34 * len(reason_order) + 80),
        margin=dict(l=10, r=120, t=45, b=30),  # extra right margin for the callouts
        # Extend x-axis ~18% past the longest bar so the right-side
        # "<count> · <pct>%" callouts have room to render.
        xaxis=dict(
            gridcolor="rgba(148,163,184,0.15)", zeroline=False,
            range=[0, max_total * 1.18 if max_total > 0 else 1],
        ),
        yaxis=dict(showgrid=False, categoryorder="array", categoryarray=reason_order),
        legend=dict(orientation="h", yanchor="top", y=1.12, xanchor="right", x=1),
        annotations=annotations,
    )
    st.plotly_chart(fig_reasons, use_container_width=True)


# ── Chart 3: Lifetime at cancellation (histogram) ─────────────────────────────
st.markdown("---")
if churn_period.empty or "created_at_dt" not in churn_period.columns:
    st.info("No lifetime data for the selected period.")
else:
    lt_days = (churn_period["cancelled_at_dt"] - churn_period["created_at_dt"]).dt.days
    lt_months = lt_days.dropna().clip(lower=0) / 30.44

    # Expand by quantity so histogram reflects unit counts
    qty = churn_period.loc[lt_months.index, "quantity"].astype(int)
    expanded = pd.Series(lt_months.values).repeat(qty.values).reset_index(drop=True)

    # Define buckets
    bucket_edges = [0, 1, 3, 6, 12, 18, 24, 36, float("inf")]
    bucket_labels = ["0–1 mo", "1–3 mo", "3–6 mo", "6–12 mo", "12–18 mo", "18–24 mo", "24–36 mo", "36+ mo"]
    cats = pd.cut(expanded, bins=bucket_edges, labels=bucket_labels, right=False)
    hist = cats.value_counts().reindex(bucket_labels, fill_value=0)

    fig_lt = go.Figure()
    fig_lt.add_trace(
        go.Bar(
            x=list(hist.index),
            y=list(hist.values),
            marker_color="#f59e0b",
            text=[str(int(v)) for v in hist.values],
            textposition="outside",
            textfont=dict(color="#e2e8f0", size=11),
            cliponaxis=False,
            hovertemplate="%{x}<br>Churned: %{y}<extra></extra>",
        )
    )
    mean_mo = float(expanded.mean()) if len(expanded) else 0.0
    med_mo  = float(expanded.median()) if len(expanded) else 0.0
    fig_lt.update_layout(
        title=dict(
            text=f"<b>LIFETIME AT CANCELLATION</b>  ·  Mean {mean_mo:.1f} mo · Median {med_mo:.1f} mo",
            font=dict(size=12, color="#94a3b8"), x=0.01, xanchor="left",
        ),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e2e8f0", size=11),
        height=340,
        margin=dict(l=10, r=10, t=45, b=30),
        xaxis=dict(showgrid=False),
        yaxis=dict(gridcolor="rgba(148,163,184,0.15)", zeroline=False),
        showlegend=False,
    )
    st.plotly_chart(fig_lt, use_container_width=True)


# ── Footnote ──────────────────────────────────────────────────────────────────
st.caption(
    f"Window: **{p_start.strftime('%b %d, %Y')} – {p_end.strftime('%b %d, %Y')}** "
    f"vs **{cmp_start.strftime('%b %d, %Y')} – {cmp_end.strftime('%b %d, %Y')}**  ·  "
    f"Display: **{granularity}**  ·  "
    f"Product: **{product_sel}**  ·  Country: **{country_sel}**"
)
