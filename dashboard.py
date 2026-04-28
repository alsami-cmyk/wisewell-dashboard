"""
Wisewell Dashboard — entry point.
Renders the shared sidebar and routes to pages.
"""

# Streamlit hot-reload re-runs this script without restarting Python, which can
# leave a stale version of utils in sys.modules. Evicting it here forces a fresh
# import every time so the latest function definitions are always used.
import sys
sys.modules.pop("utils", None)

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from utils import SHARED_CSS

# ── Page config (must be first Streamlit call) ────────────────────────────────
st.set_page_config(
    page_title="Wisewell Dashboard",
    page_icon="💧",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Health-check endpoint (for UptimeRobot / cron keep-alive) ─────────────────
if st.query_params.get("health"):
    st.write("OK")
    st.stop()

st_autorefresh(interval=5 * 60 * 1000, key="auto_refresh")
st.markdown(SHARED_CSS, unsafe_allow_html=True)
st.markdown("""
<style>
.section-label {
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: .1em;
    text-transform: uppercase;
    color: #94a3b8;
    margin: 0 0 0.3rem 0;
}
[data-testid="stMetricDelta"] svg { display: none; }
[data-testid="stPlotlyChart"] { border-radius: 12px; }

/* Bump the st.logo() image ~3mm (about 11px) larger than its small default. */
[data-testid="stLogo"],
[data-testid="stSidebarLogo"] img,
[data-testid="stHeaderLogo"] img,
[data-testid="stLogoSpacer"] img {
    height: 36px !important;
    max-height: 36px !important;
    width: auto !important;
}
</style>
""", unsafe_allow_html=True)

# ── Branding ──────────────────────────────────────────────────────────────────
# Small vertical white wordmark, placed via st.logo() so it sits in the
# top-left of the main app (and the top of the sidebar above navigation).
import os
_LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "wisewell_logo.png")
if os.path.exists(_LOGO_PATH):
    st.logo(_LOGO_PATH, size="small")

# ── Sidebar (force-refresh only — page-level filters live in each page) ───────
with st.sidebar:
    st.markdown("---")
    if st.button("↻ Force refresh", use_container_width=True, key="s_btn"):
        st.cache_data.clear()
        st.rerun()
    st.caption("Auto-refreshes every 5 min")

# ── Page router ───────────────────────────────────────────────────────────────
pg = st.navigation([
    st.Page("pages/executive_summary.py", title="Executive Summary", icon="🎯"),
    st.Page("pages/test.py",              title="Sales",             icon="📈"),
    st.Page("pages/test2.py",             title="Retention",         icon="🔄"),
    st.Page("pages/cohort.py",            title="Cohort Analysis",   icon="📊"),
])
pg.run()
