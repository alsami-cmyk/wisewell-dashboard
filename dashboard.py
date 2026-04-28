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

# ── Sidebar (Ask Claude + force-refresh only — page filters live in each page)
# Chat state — cleared every time the user clicks the open button, so each
# session of the dialog starts fresh.
st.session_state.setdefault("_chat_open",     False)
st.session_state.setdefault("_chat_messages", [])


@st.dialog("💬 Ask Claude — Wisewell Data Assistant", width="large")
def _ask_claude_dialog():
    """Modal chat dialog. State persists across reruns within the dialog;
    sidebar button clears state on each fresh open."""
    msgs_key = "_chat_messages"

    # Suggested prompts as quick-tap buttons (only when chat is empty)
    if not st.session_state[msgs_key]:
        st.caption("Try one of these or type your own question:")
        sc1, sc2 = st.columns(2)
        suggestions = [
            "How many Nano Tanks did we sell in the last 6 months?",
            "What are some worrying retention trends?",
            "Compare UAE vs KSA churn this month",
            "What's our trailing 30-day CAC by market?",
        ]
        for i, sug in enumerate(suggestions):
            col = sc1 if i % 2 == 0 else sc2
            if col.button(sug, key=f"sug_{i}", use_container_width=True):
                st.session_state["_pending_question"] = sug
                st.rerun()

    # Chat history container (scrollable)
    chat_box = st.container(height=420, border=True)
    with chat_box:
        for msg in st.session_state[msgs_key]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    # Read input — either from chat_input or a suggested-prompt button
    prompt = st.chat_input("Ask anything about Wisewell's data…")
    if not prompt and st.session_state.get("_pending_question"):
        prompt = st.session_state.pop("_pending_question")

    if prompt:
        st.session_state[msgs_key].append({"role": "user", "content": prompt})
        with chat_box:
            with st.chat_message("user"):
                st.markdown(prompt)
            with st.chat_message("assistant"):
                with st.spinner("Analysing your data…"):
                    try:
                        from chat_agent import run_agent
                        response = run_agent(prompt, st.session_state[msgs_key])
                    except Exception as exc:
                        response = f"⚠️ **Error:** `{type(exc).__name__}: {exc}`"
                st.markdown(response)
        st.session_state[msgs_key].append({"role": "assistant", "content": response})
        st.rerun()

    # Footer: clear chat & close button
    st.divider()
    cols = st.columns([1, 1, 4])
    if cols[0].button("🔄 New chat", use_container_width=True, key="chat_clear"):
        st.session_state[msgs_key] = []
        st.rerun()
    if cols[1].button("Close", use_container_width=True, key="chat_close"):
        st.session_state[msgs_key] = []
        st.session_state["_chat_open"] = False
        st.rerun()


with st.sidebar:
    st.markdown("---")
    if st.button("💬 Ask Claude", use_container_width=True, key="s_chat_open",
                 type="primary"):
        st.session_state["_chat_messages"] = []  # always start fresh
        st.session_state["_chat_open"]     = True
        st.rerun()

    if st.button("↻ Force refresh", use_container_width=True, key="s_btn"):
        st.cache_data.clear()
        st.rerun()
    st.caption("Auto-refreshes every 5 min")

# Render dialog if flagged open
if st.session_state["_chat_open"]:
    _ask_claude_dialog()

# ── Page router ───────────────────────────────────────────────────────────────
pg = st.navigation([
    st.Page("pages/executive_summary.py", title="Executive Summary", icon="🎯"),
    st.Page("pages/test.py",              title="Sales",             icon="📈"),
    st.Page("pages/test2.py",             title="Retention",         icon="🔄"),
    st.Page("pages/cohort.py",            title="Cohort Analysis",   icon="📊"),
])
pg.run()
