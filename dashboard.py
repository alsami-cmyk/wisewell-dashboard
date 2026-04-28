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

/* ── Compact font sizes for the WiseClaude chat in the sidebar ──────────── */
section[data-testid="stSidebar"] h4 {
    font-size: 0.92rem !important;
    margin-bottom: 0.25rem !important;
}
section[data-testid="stSidebar"] [data-testid="stChatMessage"] p,
section[data-testid="stSidebar"] [data-testid="stChatMessage"] li,
section[data-testid="stSidebar"] [data-testid="stChatMessage"] td,
section[data-testid="stSidebar"] [data-testid="stChatMessage"] th {
    font-size: 0.72rem !important;
    line-height: 1.35 !important;
}
section[data-testid="stSidebar"] [data-testid="stChatMessage"] {
    padding: 0.35rem 0.5rem !important;
}
section[data-testid="stSidebar"] [data-testid="stChatMessage"] strong {
    font-size: 0.72rem !important;
}
section[data-testid="stSidebar"] [data-testid="stChatInput"] textarea {
    font-size: 0.78rem !important;
}
section[data-testid="stSidebar"] button p {
    font-size: 0.74rem !important;
    line-height: 1.25 !important;
}
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
    font-size: 0.68rem !important;
}
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
    font-size: 0.68rem !important;
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

# ── Sidebar (chat + force-refresh — page filters live in each page) ──────────

# Hard cap on Anthropic API spend per chat session. Hitting this stops the
# agent and the user must click "New chat" to reset. Tweak as desired.
CHAT_SESSION_BUDGET_USD = 0.50

# Chat state (session-scoped — cleared by "New chat" button)
st.session_state.setdefault("_chat_messages", [])
st.session_state.setdefault("_chat_cost_usd", 0.0)

SUGGESTED_PROMPTS = [
    "What were our Nano+ sales over the last 10 days?",
    "What are some worrying churn observations this week?",
]

with st.sidebar:
    st.markdown("---")
    st.markdown("#### 💬 Ask WiseClaude")

    msgs    = st.session_state["_chat_messages"]
    used    = float(st.session_state["_chat_cost_usd"])
    budget  = CHAT_SESSION_BUDGET_USD
    pct     = min(used / budget, 1.0) if budget > 0 else 0.0

    # Suggestion chips when chat is empty
    if not msgs:
        st.caption("Try one of these:")
        for i, sug in enumerate(SUGGESTED_PROMPTS):
            if st.button(sug, key=f"sug_{i}", use_container_width=True):
                st.session_state["_pending_question"] = sug
                st.rerun()

    # Chat history (only render when there's at least one message)
    if msgs:
        chat_box = st.container(height=320, border=True)
        with chat_box:
            for m in msgs:
                with st.chat_message(m["role"]):
                    st.markdown(m["content"])

    # Budget meter
    bar_col = "🟢" if pct < 0.5 else ("🟡" if pct < 0.9 else "🔴")
    st.progress(pct)
    st.caption(f"{bar_col} Session spend: **${used:.3f} / ${budget:.2f}**")

    # New chat button
    if st.button("🔄 New chat", use_container_width=True, key="chat_new"):
        st.session_state["_chat_messages"] = []
        st.session_state["_chat_cost_usd"] = 0.0
        st.rerun()

    # Input — chat_input pinned to bottom of sidebar by Streamlit
    prompt = st.chat_input("Ask anything about Wisewell's data…", key="sb_chat")
    if not prompt and st.session_state.get("_pending_question"):
        prompt = st.session_state.pop("_pending_question")

    if prompt:
        if used >= budget:
            st.error(
                f"Session budget reached (${budget:.2f}). "
                "Click *New chat* to start over."
            )
        else:
            st.session_state["_chat_messages"].append(
                {"role": "user", "content": prompt}
            )
            with st.spinner("Analysing your data…"):
                try:
                    from chat_agent import BudgetExceeded, run_agent
                    response, new_total = run_agent(
                        prompt,
                        st.session_state["_chat_messages"],
                        cost_budget_usd=budget,
                        cost_used_usd=used,
                    )
                    st.session_state["_chat_cost_usd"] = new_total
                except BudgetExceeded as e:
                    response = f"⚠️ {e}"
                except Exception as exc:
                    response = f"⚠️ **Error:** `{type(exc).__name__}: {exc}`"
            st.session_state["_chat_messages"].append(
                {"role": "assistant", "content": response}
            )
            st.rerun()

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
