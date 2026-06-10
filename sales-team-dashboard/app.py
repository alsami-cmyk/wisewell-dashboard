"""
Wisewell · Sales Team Dashboard
A standalone Streamlit app.
Reads live data from the Sales Tracker Google Sheet every 5 minutes.
"""

from __future__ import annotations

import calendar
import json
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from streamlit_autorefresh import st_autorefresh

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Wisewell · Sales Team",
    page_icon="🏆",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Constants ──────────────────────────────────────────────────────────────────
SALES_SHEET_ID = "1zvnS62G88U17sxru4zTVrnzaORL0H4Am-T3Witxe_2M"
AGENTS         = ["Paloma", "Omar", "Yasmina"]
AGENT_COLOR    = {"Paloma": "#7c6dfa", "Omar": "#00d4a0", "Yasmina": "#ff6b9d"}
SCOPES              = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
DEFAULT_TARGET      = 350
INBOUND_TAB         = "Inbound Queries"

# ── Shared CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
div[data-testid="metric-container"] {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 1rem 1.25rem;
}
[data-testid="stMetricValue"] { font-size: 1.55rem; font-weight: 700; }
[data-testid="stMetricLabel"] { font-size: 0.78rem; color: #64748b;
                                 text-transform: uppercase; letter-spacing: .05em; }
#MainMenu, footer { visibility: hidden; }
section[data-testid="stSidebar"] > div:first-child { background-color: #0f172a !important; }
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] .stMarkdown { color: #e2e8f0 !important; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# AUTO-REFRESH
# ══════════════════════════════════════════════════════════════════════════════
st_autorefresh(interval=5 * 60 * 1000, key="sales_autorefresh")


# ══════════════════════════════════════════════════════════════════════════════
# GOOGLE SHEETS AUTH
# ══════════════════════════════════════════════════════════════════════════════
def _get_credentials():
    """Service account (production) or token.json (local dev)."""
    try:
        info = json.loads(st.secrets["GOOGLE_SERVICE_ACCOUNT"])
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    except (KeyError, FileNotFoundError):
        pass

    # Local dev: look for token.json in this dir, then parent dir
    for path in [
        os.path.join(os.path.dirname(__file__), "token.json"),
        os.path.join(os.path.dirname(__file__), "..", "token.json"),
    ]:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    tok = json.load(f)
                scopes = tok.get("scopes", SCOPES)
            except Exception:
                scopes = SCOPES
            return Credentials.from_authorized_user_file(path, scopes)

    raise FileNotFoundError(
        "No Google credentials found. Add GOOGLE_SERVICE_ACCOUNT to secrets, "
        "or place token.json in this directory (or its parent)."
    )


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=300, show_spinner="Syncing with Google Sheets…")
def load_sales_data() -> pd.DataFrame:
    """Fetch Paloma, Omar, Yasmina tabs → unified, cleaned DataFrame."""
    creds = _get_credentials()
    svc   = build("sheets", "v4", credentials=creds, cache_discovery=False)

    def _find_col(keywords, columns):
        """Find first column whose stripped-lowercase name contains any keyword."""
        for kw in keywords:
            c = next((c for c in columns if kw in c.lower().strip()), None)
            if c:
                return c
        return None

    def _parse_dates(raw: pd.Series) -> pd.Series:
        parsed = pd.to_datetime(raw, format="%d-%b-%Y", errors="coerce")
        mask = parsed.isna() & raw.ne("") & raw.ne("nan")
        if mask.any():
            parsed[mask] = pd.to_datetime(raw[mask], format="%d %B %Y", errors="coerce")
        still = parsed.isna() & raw.ne("") & raw.ne("nan")
        if still.any():
            parsed[still] = pd.to_datetime(raw[still], errors="coerce")
        return parsed.dt.normalize()

    frames = []
    for agent in AGENTS:
        try:
            rows = (
                svc.spreadsheets().values()
                .get(spreadsheetId=SALES_SHEET_ID, range=f"'{agent}'")
                .execute()
                .get("values", [])
            )
        except Exception as exc:
            st.warning(f"⚠️ Could not load **{agent}**'s tab: {exc}")
            continue

        if len(rows) < 2:
            continue

        max_cols = max(len(r) for r in rows)
        padded   = [r + [""] * (max_cols - len(r)) for r in rows]
        # Strip every header cell to avoid whitespace mismatches between tabs
        headers  = [c.strip() for c in padded[0]]
        tab_df   = pd.DataFrame(padded[1:], columns=headers)

        # Resolve columns within THIS tab's headers
        cols      = tab_df.columns.tolist()
        date_col  = _find_col(["date"], cols)
        order_col = _find_col(["order number", "order num"], cols)
        ag_col    = _find_col(["sales agent"], cols)
        prod_col  = _find_col(["product"], cols)
        type_col  = _find_col(["order type"], cols)

        if not date_col:
            continue

        raw = tab_df[date_col].astype(str).str.strip()
        tab_df["date"]       = _parse_dates(raw)
        tab_df["agent"]      = tab_df[ag_col].astype(str).str.strip()      if ag_col    else agent
        tab_df["product"]    = tab_df[prod_col].astype(str).str.strip()    if prod_col  else ""
        tab_df["order_type"] = tab_df[type_col].astype(str).str.strip()    if type_col  else ""
        tab_df["order_num"]  = tab_df[order_col].astype(str).str.strip()   if order_col else ""

        frames.append(tab_df[["date", "agent", "product", "order_type", "order_num"]])

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)

    valid = (
        df["date"].notna() &
        df["agent"].isin(AGENTS) &
        df["order_num"].ne("") &
        df["order_num"].ne("nan")
    )
    return df.loc[valid].reset_index(drop=True)


@st.cache_data(ttl=300, show_spinner=False)
def load_inbound_data() -> pd.DataFrame:
    """Fetch 'Inbound Queries' tab → daily date + total inbound DataFrame."""
    creds = _get_credentials()
    svc   = build("sheets", "v4", credentials=creds, cache_discovery=False)
    try:
        rows = (
            svc.spreadsheets().values()
            .get(spreadsheetId=SALES_SHEET_ID, range=f"'{INBOUND_TAB}'")
            .execute()
            .get("values", [])
        )
    except Exception as exc:
        st.warning(f"⚠️ Could not load Inbound Queries tab: {exc}")
        return pd.DataFrame()

    if len(rows) < 2:
        return pd.DataFrame()

    max_cols = max(len(r) for r in rows)
    padded   = [r + [""] * (max_cols - len(r)) for r in rows]
    df       = pd.DataFrame(padded[1:], columns=[c.strip() for c in padded[0]])

    # Find date and total columns
    date_col  = next((c for c in df.columns if c.strip() == ""), df.columns[0])  # first col is blank-header date
    # Use positional: col 0 = date, col -1 = Total
    df.columns = [c if c.strip() else ("date_raw" if i == 0 else f"col_{i}")
                  for i, c in enumerate(df.columns)]
    total_col = next((c for c in df.columns if "total" in c.lower()), None)
    if not total_col:
        # last column
        total_col = df.columns[-1]

    raw    = df["date_raw"].astype(str).str.strip()
    parsed = pd.to_datetime(raw, format="%d %b, %Y", errors="coerce")
    still  = parsed.isna() & raw.ne("") & raw.ne("nan")
    if still.any():
        parsed[still] = pd.to_datetime(raw[still], errors="coerce")
    df["date"] = parsed.dt.normalize()

    df["total"] = pd.to_numeric(df[total_col].astype(str).str.replace(",", ""), errors="coerce").fillna(0).astype(int)

    valid = df["date"].notna() & (df["total"] > 0)
    return df.loc[valid, ["date", "total"]].reset_index(drop=True)


def _rgba(hex_color: str, alpha: float = 0.1) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ══════════════════════════════════════════════════════════════════════════════
# LOAD DATA
# ══════════════════════════════════════════════════════════════════════════════
try:
    df_all = load_sales_data()
except Exception as exc:
    st.error(f"**Failed to load sales data:** {exc}")
    st.stop()

if df_all.empty:
    st.warning("No sales records found in the tracker.")
    st.stop()

try:
    df_inbound = load_inbound_data()
except Exception:
    df_inbound = pd.DataFrame()

# ── Available months (for sidebar filter) ─────────────────────────────────────
today = pd.Timestamp.today().normalize()
_cur_period = today.to_period("M")
# Only show months up to (and including) the current month — no future months
_periods = sorted(
    p for p in df_all["date"].dropna().dt.to_period("M").unique()
    if p <= _cur_period
)
# Ensure current month is always present
if not _periods or _periods[-1] != _cur_period:
    _periods.append(_cur_period)
_month_labels      = [p.strftime("%B %Y") for p in _periods]
_default_month_idx = len(_month_labels) - 1


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR: CONTROLS
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("""
    <div style="padding:0.5rem 0 1rem;">
      <span style="font-size:1.4rem;font-weight:800;color:#e2e8f0;letter-spacing:.05em;">
        🏆 SALES TEAM
      </span>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("---")

    st.markdown('<p style="font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#94a3b8;">Month</p>', unsafe_allow_html=True)
    selected_month_label = st.selectbox(
        "Month", _month_labels, index=_default_month_idx,
        key="sales_month", label_visibility="collapsed",
    )

    st.markdown("---")

    st.markdown('<p style="font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#94a3b8;">Team Monthly Target</p>', unsafe_allow_html=True)
    monthly_target = st.number_input(
        "Target", min_value=1, max_value=10_000,
        value=DEFAULT_TARGET, step=100,
        key="sales_target", label_visibility="collapsed",
    )
    st.caption(f"Quarterly: {monthly_target * 3:,}")

    st.markdown("---")
    if st.button("↻ Force refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Auto-refreshes every 5 min")


# ══════════════════════════════════════════════════════════════════════════════
# DATE CONTEXT  (based on selected month)
# ══════════════════════════════════════════════════════════════════════════════
_sel_period   = _periods[_month_labels.index(selected_month_label)]
sel_year      = _sel_period.year
sel_month     = _sel_period.month
month_start   = pd.Timestamp(sel_year, sel_month, 1)
days_in_month = calendar.monthrange(sel_year, sel_month)[1]

if (sel_year, sel_month) == (today.year, today.month):
    days_elapsed = today.day
    days_left    = days_in_month - days_elapsed
else:
    # Past month: treat as fully elapsed
    days_elapsed = days_in_month
    days_left    = 0

month_end = month_start + pd.offsets.MonthEnd(0)

month_df = df_all[
    (df_all["date"].dt.year  == sel_year) &
    (df_all["date"].dt.month == sel_month)
].copy()

# Previous month (same elapsed days, for MoM delta)
prev_m_end   = month_start - pd.Timedelta(days=1)
prev_m_start = prev_m_end.replace(day=1)
prev_cutoff  = prev_m_start + pd.Timedelta(days=days_elapsed - 1)
prev_df = df_all[
    (df_all["date"] >= prev_m_start) &
    (df_all["date"] <= prev_cutoff)
]


# ══════════════════════════════════════════════════════════════════════════════
# PER-AGENT STATS
# ══════════════════════════════════════════════════════════════════════════════
agent_stats: dict[str, dict] = {}

for agent in AGENTS:
    adf   = month_df[month_df["agent"] == agent]
    count = len(adf)

    pace   = count / days_elapsed if days_elapsed > 0 else 0.0
    proj   = round(pace * days_in_month)

    prod_counts = adf["product"].str.strip().value_counts().to_dict()

    chart_end = month_end if days_left == 0 else today
    daily     = adf.groupby("date").size().reset_index(name="cnt")
    all_d     = pd.DataFrame({"date": pd.date_range(month_start, chart_end, freq="D")})
    ddf       = all_d.merge(daily, on="date", how="left").fillna(0)
    ddf["cnt"] = ddf["cnt"].astype(int)
    ddf["cumulative"] = ddf["cnt"].cumsum()

    agent_stats[agent] = {
        "count":       count,
        "prev_cnt":    len(prev_df[prev_df["agent"] == agent]),
        "pace":        pace,
        "proj":        proj,
        "subs":        len(adf[adf["order_type"] == "Subscription"]),
        "owns":        len(adf[adf["order_type"] == "Ownership"]),
        "prod_counts": prod_counts,
        "daily_full":  ddf,
    }

team_total  = sum(s["count"]    for s in agent_stats.values())
team_prev   = sum(s["prev_cnt"] for s in agent_stats.values())
team_target = monthly_target
team_pct    = team_total / team_target * 100 if team_target > 0 else 0

# ── Inbound queries for selected month ────────────────────────────────────────
if not df_inbound.empty:
    month_inbound = df_inbound[
        (df_inbound["date"].dt.year  == sel_year) &
        (df_inbound["date"].dt.month == sel_month)
    ].copy()
    month_inbound_total = int(month_inbound["total"].sum())
else:
    month_inbound       = pd.DataFrame()
    month_inbound_total = 0

conv_rate = (team_total / month_inbound_total * 100) if month_inbound_total > 0 else None


# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════
hc1, hc2 = st.columns([3, 1])
with hc1:
    st.markdown(
        f"""<h2 style="margin:0;font-size:1.5rem;font-weight:800;letter-spacing:-0.4px;">
        🏆 Sales Team Dashboard</h2>
        <p style="margin:4px 0 0;color:#64748b;font-size:0.82rem;">
        {selected_month_label} &nbsp;·&nbsp; Live from Google Sheets
        &nbsp;·&nbsp; Auto-refreshes every 5 min
        </p>""",
        unsafe_allow_html=True,
    )
with hc2:
    days_left_label = f"{days_left}d left" if days_left > 0 else "Month complete"
    st.markdown(
        f"""<div style="text-align:right;padding-top:8px;">
        <span style="background:#f0f9ff;border:1px solid #bae6fd;color:#0369a1;
                     padding:5px 13px;border-radius:7px;font-size:0.76rem;font-weight:600;">
        🎯 Team target: {monthly_target:,} &nbsp;·&nbsp; {days_left_label}
        </span></div>""",
        unsafe_allow_html=True,
    )

st.markdown("---")


# ══════════════════════════════════════════════════════════════════════════════
# TEAM KPIs
# ══════════════════════════════════════════════════════════════════════════════
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Team Sales (MTD)",   f"{team_total:,}",
          delta=f"{team_total - team_prev:+,} vs same point last month")
k2.metric("Conversion Rate",
          f"{conv_rate:.1f}%" if conv_rate is not None else "—",
          help="Team sales ÷ total inbound queries for the month")
k3.metric("Team Target",        f"{team_target:,}")
k4.metric("% to Target",        f"{team_pct:.1f}%")
k5.metric("Days Remaining",     f"{days_left} of {days_in_month}")

st.markdown(
    f"""<div style="margin:10px 0 4px;">
    <div style="display:flex;justify-content:space-between;font-size:0.72rem;
                color:#64748b;margin-bottom:6px;">
      <span>Team progress to {selected_month_label} target</span>
      <span style="font-weight:600;color:#0f172a;">{team_total:,} / {team_target:,}</span>
    </div></div>""",
    unsafe_allow_html=True,
)
st.progress(min(team_pct / 100, 1.0))
st.markdown("---")


# ══════════════════════════════════════════════════════════════════════════════
# AGENT CARDS
# ══════════════════════════════════════════════════════════════════════════════
st.markdown(
    '<p style="font-size:0.72rem;font-weight:700;text-transform:uppercase;'
    'letter-spacing:1.2px;color:#94a3b8;margin-bottom:6px;">Agent Performance</p>',
    unsafe_allow_html=True,
)

cols3 = st.columns(3)
for i, agent in enumerate(AGENTS):
    s     = agent_stats[agent]
    color = AGENT_COLOR[agent]

    # Top products mini-list
    top_prods = sorted(s["prod_counts"].items(), key=lambda x: x[1], reverse=True)[:3]
    top_html  = "".join(
        f'<div style="display:flex;justify-content:space-between;font-size:0.72rem;'
        f'padding:3px 0;border-bottom:1px solid #f1f5f9;">'
        f'<span style="color:#475569;">{p}</span>'
        f'<span style="font-weight:700;color:{color};">{c}</span></div>'
        for p, c in top_prods
    )

    with cols3[i]:
        st.markdown(
            f"""<div style="background:white;border:1px solid #e2e8f0;
                border-top:4px solid {color};border-radius:14px;
                padding:22px 20px 18px;height:100%;">
              <div style="margin-bottom:16px;">
                <div style="font-size:0.72rem;font-weight:700;text-transform:uppercase;
                            letter-spacing:1px;color:{color};">{agent}</div>
                <div style="font-size:0.78rem;color:#64748b;margin-top:2px;">Sales Agent</div>
              </div>
              <div style="font-size:3.4rem;font-weight:900;color:{color};line-height:1;
                          letter-spacing:-2px;margin-bottom:20px;">{s['count']}</div>
              <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:1px;
                          background:#e2e8f0;border-radius:10px;overflow:hidden;
                          margin-bottom:16px;">
                <div style="background:white;padding:10px 6px;text-align:center;">
                  <div style="font-size:1.15rem;font-weight:800;color:{color};">{s['proj']}</div>
                  <div style="font-size:0.62rem;color:#94a3b8;text-transform:uppercase;
                              letter-spacing:0.3px;margin-top:1px;">Projected</div>
                </div>
                <div style="background:white;padding:10px 6px;text-align:center;">
                  <div style="font-size:1.15rem;font-weight:800;">{s['pace']:.1f}</div>
                  <div style="font-size:0.62rem;color:#94a3b8;text-transform:uppercase;
                              letter-spacing:0.3px;margin-top:1px;">Per Day</div>
                </div>
                <div style="background:white;padding:10px 6px;text-align:center;">
                  <div style="font-size:1.15rem;font-weight:800;">{s['subs']}</div>
                  <div style="font-size:0.62rem;color:#94a3b8;text-transform:uppercase;
                              letter-spacing:0.3px;margin-top:1px;">Subs</div>
                </div>
                <div style="background:white;padding:10px 6px;text-align:center;">
                  <div style="font-size:1.15rem;font-weight:800;color:#f59e0b;">{s['owns']}</div>
                  <div style="font-size:0.62rem;color:#94a3b8;text-transform:uppercase;
                              letter-spacing:0.3px;margin-top:1px;">Owned</div>
                </div>
              </div>
              <div style="font-size:0.68rem;font-weight:700;text-transform:uppercase;
                          letter-spacing:0.8px;color:#94a3b8;margin-bottom:6px;">Top Products</div>
              {top_html}
            </div>""",
            unsafe_allow_html=True,
        )

st.markdown("<br>", unsafe_allow_html=True)
st.markdown("---")


# ══════════════════════════════════════════════════════════════════════════════
# TREND CHART + LEADERBOARD
# ══════════════════════════════════════════════════════════════════════════════
c_trend, c_lb = st.columns([3, 1])

with c_trend:
    st.markdown(
        '<p style="font-size:0.72rem;font-weight:700;text-transform:uppercase;'
        'letter-spacing:1.2px;color:#94a3b8;margin-bottom:4px;">'
        f'Cumulative Sales — {selected_month_label}</p>',
        unsafe_allow_html=True,
    )

    fig_t = go.Figure()

    for agent in AGENTS:
        s   = agent_stats[agent]
        ddf = s["daily_full"]
        fig_t.add_trace(go.Scatter(
            x=ddf["date"].dt.strftime("%-d %b").tolist(),
            y=ddf["cumulative"].tolist(),
            name=agent,
            line=dict(color=AGENT_COLOR[agent], width=2.5),
            mode="lines+markers", marker=dict(size=4),
            fill="tozeroy", fillcolor=_rgba(AGENT_COLOR[agent], 0.07),
            hovertemplate=f"<b>%{{x}}</b> · {agent}: %{{y}}<extra></extra>",
        ))

    fig_t.update_layout(
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        height=300,
        legend=dict(orientation="h", y=1.06, font=dict(size=11, color="#64748b")),
        xaxis=dict(tickfont=dict(size=9, color="#94a3b8"), showgrid=False, zeroline=False),
        yaxis=dict(tickfont=dict(size=9, color="#94a3b8"), showgrid=True,
                   gridcolor="rgba(0,0,0,0.05)", zeroline=False),
        margin=dict(t=28, b=8, l=4, r=8),
        hovermode="x unified",
    )
    st.plotly_chart(fig_t, use_container_width=True)

with c_lb:
    st.markdown(
        '<p style="font-size:0.72rem;font-weight:700;text-transform:uppercase;'
        'letter-spacing:1.2px;color:#94a3b8;margin-bottom:8px;">Leaderboard</p>',
        unsafe_allow_html=True,
    )
    ranked  = sorted(AGENTS, key=lambda a: agent_stats[a]["count"], reverse=True)
    max_cnt = max(agent_stats[a]["count"] for a in ranked) or 1

    for rank, agent in enumerate(ranked):
        s     = agent_stats[agent]
        color = AGENT_COLOR[agent]
        bar_w = s["count"] / max_cnt * 100
        sep   = "border-top:1px solid #f1f5f9;" if rank > 0 else ""
        st.markdown(
            f"""<div style="display:flex;align-items:center;gap:10px;padding:13px 0;{sep}">
              <div style="flex:1;min-width:0;">
                <div style="font-size:0.85rem;font-weight:700;color:{color};">{agent}</div>
                <div style="font-size:0.71rem;color:#94a3b8;">
                  {s['pace']:.1f}/day &nbsp;·&nbsp; proj {s['proj']}
                </div>
                <div style="background:#f1f5f9;border-radius:99px;height:5px;
                            overflow:hidden;margin-top:7px;">
                  <div style="background:{color};width:{bar_w:.1f}%;
                              height:100%;border-radius:99px;"></div>
                </div>
              </div>
              <div style="font-size:1.55rem;font-weight:900;color:{color};
                          min-width:40px;text-align:right;">{s['count']}</div>
            </div>""",
            unsafe_allow_html=True,
        )

# ── Conversion Rate Over Time ─────────────────────────────────────────────────
if not month_inbound.empty and month_inbound_total > 0:
    st.markdown(
        '<p style="font-size:0.72rem;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:1.2px;color:#94a3b8;margin-bottom:4px;">'
        f'Conversion Rate Over Time — {selected_month_label}</p>',
        unsafe_allow_html=True,
    )

    # Build daily cumulative sales (team) and cumulative inbound for the month
    chart_end_cr = month_end if days_left == 0 else today
    all_days_cr  = pd.DataFrame({"date": pd.date_range(month_start, chart_end_cr, freq="D")})

    # Daily sales (team total)
    daily_sales = month_df.groupby("date").size().reset_index(name="sales")
    daily_cr    = all_days_cr.merge(daily_sales, on="date", how="left").fillna(0)
    daily_cr["sales"] = daily_cr["sales"].astype(int)

    # Daily inbound
    daily_cr = daily_cr.merge(month_inbound[["date", "total"]].rename(columns={"total": "inbound"}),
                               on="date", how="left").fillna(0)
    daily_cr["inbound"] = daily_cr["inbound"].astype(int)

    # Cumulative
    daily_cr["cum_sales"]   = daily_cr["sales"].cumsum()
    daily_cr["cum_inbound"] = daily_cr["inbound"].cumsum()

    # Conversion rate: only where cumulative inbound > 0
    daily_cr["conv_rate"] = daily_cr.apply(
        lambda r: r["cum_sales"] / r["cum_inbound"] * 100 if r["cum_inbound"] > 0 else None,
        axis=1,
    )
    cr_plot = daily_cr.dropna(subset=["conv_rate"])

    fig_cr = go.Figure()
    fig_cr.add_trace(go.Scatter(
        x=cr_plot["date"].dt.strftime("%-d %b").tolist(),
        y=cr_plot["conv_rate"].tolist(),
        mode="lines+markers",
        marker=dict(size=4, color="#7c6dfa"),
        line=dict(color="#7c6dfa", width=2.5),
        fill="tozeroy",
        fillcolor="rgba(124,109,250,0.08)",
        hovertemplate="<b>%{x}</b> · Conv. rate: %{y:.1f}%<extra></extra>",
        name="Conversion Rate",
    ))
    fig_cr.update_layout(
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        height=220,
        showlegend=False,
        xaxis=dict(tickfont=dict(size=9, color="#94a3b8"), showgrid=False, zeroline=False),
        yaxis=dict(tickfont=dict(size=9, color="#94a3b8"), showgrid=True,
                   gridcolor="rgba(0,0,0,0.05)", zeroline=False,
                   ticksuffix="%"),
        margin=dict(t=12, b=8, l=4, r=8),
        hovermode="x unified",
    )
    st.plotly_chart(fig_cr, use_container_width=True)

st.markdown("---")


# ══════════════════════════════════════════════════════════════════════════════
# PRODUCT MIX + ORDER TYPES
# ══════════════════════════════════════════════════════════════════════════════
c_prod, c_type = st.columns([3, 1])

with c_prod:
    st.markdown(
        '<p style="font-size:0.72rem;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:1.2px;color:#94a3b8;margin-bottom:4px;">Product Mix — {selected_month_label}</p>',
        unsafe_allow_html=True,
    )
    prod_order = month_df["product"].str.strip().value_counts().index.tolist()
    fig_prod   = go.Figure()
    for agent in AGENTS:
        s = agent_stats[agent]
        fig_prod.add_trace(go.Bar(
            name=agent,
            x=prod_order,
            y=[s["prod_counts"].get(p, 0) for p in prod_order],
            marker_color=AGENT_COLOR[agent],
            marker_line=dict(width=0),
            opacity=0.85,
        ))
    fig_prod.update_layout(
        barmode="stack",
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        height=250,
        legend=dict(orientation="h", y=1.1, font=dict(size=11, color="#64748b")),
        xaxis=dict(tickfont=dict(size=10, color="#94a3b8"), showgrid=False),
        yaxis=dict(tickfont=dict(size=9, color="#94a3b8"), showgrid=True,
                   gridcolor="rgba(0,0,0,0.04)", zeroline=False),
        margin=dict(t=28, b=8, l=4, r=8),
    )
    st.plotly_chart(fig_prod, use_container_width=True)

with c_type:
    st.markdown(
        '<p style="font-size:0.72rem;font-weight:700;text-transform:uppercase;'
        'letter-spacing:1.2px;color:#94a3b8;margin-bottom:4px;">Order Types</p>',
        unsafe_allow_html=True,
    )
    n_subs = len(month_df[month_df["order_type"] == "Subscription"])
    n_owns = len(month_df[month_df["order_type"] == "Ownership"])
    fig_ty = go.Figure(go.Pie(
        labels=["Subscription", "Ownership"],
        values=[n_subs, n_owns],
        hole=0.62,
        marker=dict(colors=["#7c6dfa", "#f59e0b"], line=dict(width=0)),
    ))
    fig_ty.update_traces(
        texttemplate="<b>%{label}</b><br>%{value}",
        textposition="outside", textfont_size=11,
    )
    fig_ty.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", height=250, showlegend=False,
        margin=dict(t=8, b=8, l=8, r=8),
    )
    st.plotly_chart(fig_ty, use_container_width=True)

st.markdown("---")


# ══════════════════════════════════════════════════════════════════════════════
# RECENT ACTIVITY
# ══════════════════════════════════════════════════════════════════════════════
st.markdown(
    '<p style="font-size:0.72rem;font-weight:700;text-transform:uppercase;'
    'letter-spacing:1.2px;color:#94a3b8;margin-bottom:8px;">Recent Activity</p>',
    unsafe_allow_html=True,
)
recent = (
    month_df.sort_values("date", ascending=False)
    .head(25)
    .assign(Date=lambda x: x["date"].dt.strftime("%-d %b %Y"))
    [["Date", "agent", "product", "order_type", "order_num"]]
    .rename(columns={"agent": "Agent", "product": "Product",
                     "order_type": "Type", "order_num": "Order #"})
)
st.dataframe(recent, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# FOOTER
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.caption(
    f"📊 {len(df_all):,} total records &nbsp;·&nbsp; "
    f"{len(month_df):,} in {selected_month_label} &nbsp;·&nbsp; "
    f"Refreshed: {pd.Timestamp.now().strftime('%H:%M')} &nbsp;·&nbsp; "
    f"Auto-refreshes every 5 min"
)
