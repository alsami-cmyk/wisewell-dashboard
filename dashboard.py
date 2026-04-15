"""
Wisewell Dashboard — entry point.
Renders the shared sidebar and routes to Sales / Retention pages.
"""

from datetime import date

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from utils import PRODUCT_ORDER, SHARED_CSS

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
</style>
""", unsafe_allow_html=True)

# ── Sidebar (shared across all pages) ─────────────────────────────────────────
with st.sidebar:
    st.markdown(
        """
        <div style="padding: 0.5rem 0 1rem 0;">
          <span style="font-size:1.6rem; font-weight:800; color:#e2e8f0;
                       letter-spacing:0.05em;">💧 WISEWELL</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("---")

    st.markdown('<p class="section-label">Country</p>', unsafe_allow_html=True)
    st.radio(
        "Country", ["All", "UAE", "KSA", "USA"],
        index=0, key="s_country", label_visibility="collapsed",
    )

    st.markdown('<p class="section-label">Product</p>', unsafe_allow_html=True)
    st.radio(
        "Product", ["All"] + PRODUCT_ORDER,
        index=0, key="s_product", label_visibility="collapsed",
    )

    st.markdown("---")
    st.markdown('<p class="section-label">Date range</p>', unsafe_allow_html=True)
    today_d = date.today()
    st.date_input(
        "Range",
        value=(date(2025, 1, 1), today_d),
        min_value=date(2022, 1, 1),
        max_value=today_d,
        key="s_daterange",
        label_visibility="collapsed",
    )

    st.markdown("---")
    if st.button("↻ Force refresh", use_container_width=True, key="s_btn"):
        st.cache_data.clear()
        st.rerun()
    st.caption("Auto-refreshes every 5 min")

# ── Page router ───────────────────────────────────────────────────────────────
pg = st.navigation([
    st.Page("pages/sales.py",     title="Sales",     icon="📈"),
    st.Page("pages/retention.py", title="Retention",  icon="🔄"),
])
pg.run()
