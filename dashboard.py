"""
Wisewell Dashboard — entry point.
Renders the shared sidebar and routes to pages.
"""

# NOTE: Do NOT do `sys.modules.pop("utils", None)` here. It races with Python's
# import system on Streamlit Cloud (Python 3.14) and intermittently crashes
# the page with `KeyError: 'utils'`. Module freshness on redeploys is handled
# by Streamlit Cloud restarting the process — the pop was never necessary.

import hashlib
import hmac
import time
from datetime import datetime, timedelta

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

# ── Password gate (with persistent "remember me" cookie) ──────────────────────
# Set DASHBOARD_PASSWORD in Streamlit secrets to enable. If unset, the dashboard
# is open (useful for local dev).
#
# st.session_state alone is per-websocket-session, so it was lost on a refresh, a
# new tab, or an app reboot — which meant re-entering the password constantly.
# On success we now also store a SIGNED token in a browser cookie, so a returning
# visitor is recognised for AUTH_COOKIE_DAYS without being prompted again.
#
# The cookie holds "<expiry>.<hmac>", never the password itself, and is signed
# with AUTH_COOKIE_SECRET (falling back to the password, so ROTATING THE PASSWORD
# INVALIDATES every outstanding cookie). A tampered or expired token fails
# verification and the visitor simply sees the form again.
#
# Everything cookie-related is wrapped defensively: if the component is missing or
# misbehaves we fall back to the old session-only behaviour. An auth gate must
# degrade to "ask for the password" — never to a crash, and never to an open door.
AUTH_COOKIE_NAME = "ww_auth"
AUTH_COOKIE_DAYS = 30
# The same signed token is ALSO carried in the URL. Cookies set by a Streamlit
# component live in a sandboxed iframe, and browsers now block third-party
# cookies by default, so the cookie alone could not be relied on. Query params
# are handled server-side by Streamlit and cannot be blocked, which makes this
# the mechanism that actually keeps a visitor signed in.
AUTH_QS_KEY = "k"


def _auth_secret() -> str:
    """Key used to sign the remember-me token."""
    for key in ("AUTH_COOKIE_SECRET", "DASHBOARD_PASSWORD"):
        try:
            val = st.secrets[key]
            if val:
                return str(val)
        except Exception:
            continue
    return ""


def _make_auth_token(days: int = AUTH_COOKIE_DAYS) -> str:
    exp = str(int(time.time()) + days * 86400)
    sig = hmac.new(_auth_secret().encode(), exp.encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def _auth_token_valid(token) -> bool:
    secret = _auth_secret()
    if not secret or not token:
        return False
    try:
        exp_str, sig = str(token).split(".", 1)
        if int(exp_str) < time.time():
            return False           # expired
    except Exception:
        return False               # malformed
    expected = hmac.new(secret.encode(), exp_str.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def _cookie_jar():
    """CookieManager, or None if the component is unavailable."""
    try:
        import extra_streamlit_components as stx
        return stx.CookieManager(key="ww_cookie_manager")
    except Exception:
        return None


def _require_password() -> None:
    try:
        expected = st.secrets["DASHBOARD_PASSWORD"]
    except (KeyError, FileNotFoundError):
        return  # no password configured → skip gate

    # Signed token in the URL — survives refresh, new tab, bookmark and reboot.
    try:
        if _auth_token_valid(st.query_params.get(AUTH_QS_KEY)):
            st.session_state["auth_ok"] = True
    except Exception:
        pass

    if st.session_state.get("auth_ok"):
        # Re-assert the token so switching pages never drops it from the URL.
        try:
            if not _auth_token_valid(st.query_params.get(AUTH_QS_KEY)):
                st.query_params[AUTH_QS_KEY] = _make_auth_token()
        except Exception:
            pass
        return

    jar = _cookie_jar()

    # The cookie component needs one round-trip before it can report anything.
    # Without this probe the login form would flash on every first load even for a
    # remembered visitor. The session flag makes it happen at most once.
    if jar is not None and not st.session_state.get("_auth_cookie_probed"):
        st.session_state["_auth_cookie_probed"] = True
        try:
            jar.get_all()
            st.rerun()
        except Exception:
            pass

    # Already remembered from a previous visit?
    if jar is not None:
        try:
            if _auth_token_valid(jar.get(AUTH_COOKIE_NAME)):
                st.session_state["auth_ok"] = True
                return
        except Exception:
            pass

    # Render a centered login form
    _, c, _ = st.columns([1, 1.2, 1])
    with c:
        st.markdown(
            "<div style='text-align:center; padding-top:60px;'>"
            "<h2 style='color:#e2e8f0; margin-bottom:8px;'>💧 Wisewell Dashboard</h2>"
            "<p style='color:#94a3b8; font-size:14px; margin-bottom:32px;'>"
            "Please enter the access password to continue.</p>"
            "</div>",
            unsafe_allow_html=True,
        )
        with st.form("login_form", clear_on_submit=False):
            pw = st.text_input("Password", type="password", label_visibility="collapsed",
                               placeholder="Password")
            remember = st.checkbox(f"Keep me signed in for {AUTH_COOKIE_DAYS} days", value=True)
            ok = st.form_submit_button("Sign in", use_container_width=True, type="primary")
        if ok:
            if pw == expected:
                st.session_state["auth_ok"] = True
                token = _make_auth_token()
                if remember:
                    # URL token: the reliable path.
                    try:
                        st.query_params[AUTH_QS_KEY] = token
                    except Exception:
                        pass
                    # Cookie: a bonus when the browser allows it. Set it BEFORE
                    # any rerun — an immediate rerun can abort the component's
                    # write round-trip, which is why the cookie alone failed.
                    if jar is not None:
                        try:
                            jar.set(
                                AUTH_COOKIE_NAME, token,
                                expires_at=datetime.now() + timedelta(days=AUTH_COOKIE_DAYS),
                                key="ww_auth_set",
                            )
                        except Exception:
                            pass
                st.rerun()
            else:
                st.error("Incorrect password.")
    st.stop()


def _sign_out() -> None:
    """Clear the session and forget the remember-me cookie."""
    st.session_state["auth_ok"] = False
    try:
        if AUTH_QS_KEY in st.query_params:
            del st.query_params[AUTH_QS_KEY]
    except Exception:
        pass
    jar = _cookie_jar()
    if jar is not None:
        try:
            jar.delete(AUTH_COOKIE_NAME, key="ww_auth_del")
        except Exception:
            pass
    st.rerun()


_require_password()

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

    # Only meaningful when the password gate is active; with a remember-me
    # cookie in play there has to be a way to deliberately forget this browser.
    if st.session_state.get("auth_ok"):
        if st.button("🔓 Sign out", use_container_width=True, key="s_signout"):
            _sign_out()

# ── Dark / light theme ────────────────────────────────────────────────────────
# One central toggle. dashboard.py runs on every page load (the pages render
# inside pg.run() below), so defining the button + theme CSS + the Plotly
# recolour wrapper here covers ALL pages with no per-page edits.
st.session_state.setdefault("ui_theme", "dark")
_theme = st.session_state["ui_theme"]

# Small toggle button, pinned to the top-right of the main area on every page.
st.markdown(
    "<style>"
    ".theme-toggle-row [data-testid='stButton'] button {"
    "  padding:0.15rem 0.55rem; min-height:0; border-radius:8px;"
    "  font-size:1rem; line-height:1.1; }"
    "</style>",
    unsafe_allow_html=True,
)
st.markdown("<div class='theme-toggle-row'>", unsafe_allow_html=True)
_tt_spacer, _tt_btn = st.columns([13, 1])
with _tt_btn:
    if st.button("🌙" if _theme == "dark" else "☀️",
                 key="theme_toggle",
                 help="Switch to light mode" if _theme == "dark" else "Switch to dark mode"):
        st.session_state["ui_theme"] = "light" if _theme == "dark" else "dark"
        st.rerun()
st.markdown("</div>", unsafe_allow_html=True)

# Light-mode chrome overrides (the base theme in config.toml is dark). The
# sidebar deliberately stays dark in both modes — a dark rail beside a light
# canvas is a common, intentional look and keeps the chat legible.
if _theme == "light":
    st.markdown("""
    <style>
    [data-testid="stAppViewContainer"],
    [data-testid="stMain"],
    [data-testid="stMain"] .block-container { background-color: #ffffff !important; }
    [data-testid="stHeader"] { background: rgba(255,255,255,0.9) !important; }

    /* Main-area text → dark ink (scoped so the dark sidebar is untouched) */
    [data-testid="stMain"] h1, [data-testid="stMain"] h2, [data-testid="stMain"] h3,
    [data-testid="stMain"] h4, [data-testid="stMain"] h5, [data-testid="stMain"] h6,
    [data-testid="stMain"] p, [data-testid="stMain"] li, [data-testid="stMain"] span,
    [data-testid="stMain"] label, [data-testid="stMain"] .stMarkdown,
    [data-testid="stMain"] [data-testid="stMetricValue"] { color: #0f172a !important; }
    [data-testid="stMain"] [data-testid="stCaptionContainer"],
    [data-testid="stMain"] [data-testid="stCaptionContainer"] p { color: #475569 !important; }

    /* Metric cards → light surface with a soft border */
    [data-testid="stMain"] [data-testid="stMetric"],
    [data-testid="stMain"] div[data-testid="metric-container"] {
        background:#f8fafc !important; border:1px solid #e2e8f0 !important;
        border-radius:12px; padding:1rem 1.25rem; }
    [data-testid="stMain"] [data-testid="stMetricLabel"] { color:#64748b !important; }

    /* Inputs / selectboxes / dataframes on the light canvas */
    [data-testid="stMain"] [data-baseweb="select"] > div {
        background:#ffffff !important; border-color:#cbd5e1 !important; }
    [data-testid="stMain"] [data-baseweb="select"] * { color:#0f172a !important; }
    [data-testid="stMain"] [data-testid="stDataFrame"] { background:#ffffff !important; }
    </style>
    """, unsafe_allow_html=True)

# ── Page router ───────────────────────────────────────────────────────────────
pg = st.navigation([
    st.Page("pages/executive_summary.py", title="Executive Summary", icon="🎯"),
    st.Page("pages/test.py",              title="Sales",             icon="📈"),
    st.Page("pages/test2.py",             title="Retention",         icon="🔄"),
    st.Page("pages/cohort.py",            title="Cohort Analysis",   icon="📊"),
    st.Page("pages/sku_breakdown.py",     title="SKU Breakdown",     icon="📦"),
])
pg.run()
