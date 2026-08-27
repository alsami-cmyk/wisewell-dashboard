"""
chart_theme.py — the single source of truth for Plotly chart colours.

Lives in its OWN module, deliberately, for two reasons:

1. Separation of concerns: theming is presentation, not data-layer.
2. Deploy safety. Streamlit re-executes dashboard.py and pages/*.py from disk
   on every rerun, but plain imports (utils, this module) are cached in
   sys.modules for the life of the process. So a deploy that adds a new symbol
   to utils.py can leave the running process with a stale utils and no way to
   see it, which raises ImportError in every page until someone reboots. Adding
   the helper under a NEW module name sidesteps that: an as-yet-unimported
   module is always read fresh from disk.

Usage — call it explicitly on every figure:

    st.plotly_chart(style_fig(fig), use_container_width=True)

This replaced a monkeypatch of st.plotly_chart, which re-wrapped itself on
every rerun and eventually raised RecursionError. Explicit per-figure styling
has no global state, so that failure mode cannot recur.

style_fig is deliberately conservative: it sets only theme-dependent DEFAULTS
(backgrounds, base font colour, gridlines) and retunes annotation text authored
for a dark canvas. It never touches trace colours, marker colours or bar text —
those encode data or sit on coloured fills — and it leaves per-axis title
colours alone so intentional accents (e.g. a green ARR axis) survive.
"""

from __future__ import annotations

import logging

import streamlit as st

logger = logging.getLogger("wisewell")

CHART_THEME: dict[str, dict[str, str]] = {
    "dark":  {"ink": "#e2e8f0", "muted": "#94a3b8",
              "grid": "rgba(148,163,184,0.15)", "bg": "rgba(0,0,0,0)"},
    "light": {"ink": "#1e293b", "muted": "#475569",
              "grid": "rgba(71,85,105,0.18)",  "bg": "rgba(0,0,0,0)"},
}

# Text colours authored for the dark canvas; flipped to dark ink in light mode.
_DARK_CANVAS_TEXT = {
    "#e2e8f0", "#cbd5e1", "#94a3b8", "#f1f5f9", "#e5e7eb", "#f8fafc",
    "#cbd5f5", "#e2e5f0", "#ffffff", "white",
}


def active_theme() -> str:
    """'light' or 'dark' — the viewer's current dashboard theme."""
    try:
        return "light" if st.session_state.get("ui_theme") == "light" else "dark"
    except Exception:
        return "dark"


def style_fig(fig, theme: str | None = None):
    """Apply the active dashboard theme to a Plotly figure. Returns the figure.

    Safe to call on any figure, including None. Never raises: a styling
    failure must not take down a page.
    """
    if fig is None:
        return fig
    t = CHART_THEME.get(theme or active_theme(), CHART_THEME["dark"])
    try:
        fig.update_layout(
            paper_bgcolor=t["bg"],
            plot_bgcolor=t["bg"],
            font_color=t["ink"],
            legend_font_color=t["muted"],
        )
        fig.update_xaxes(gridcolor=t["grid"], tickfont_color=t["muted"], zeroline=False)
        fig.update_yaxes(gridcolor=t["grid"], tickfont_color=t["muted"], zeroline=False)
        # Annotations written for a dark background need flipping in light mode.
        for ann in (getattr(fig.layout, "annotations", None) or ()):
            fnt = getattr(ann, "font", None)
            col = getattr(fnt, "color", None)
            if isinstance(col, str) and col.strip().lower() in _DARK_CANVAS_TEXT:
                ann.font.color = t["ink"]
    except Exception:
        logger.exception("style_fig: failed to theme a figure (continuing unstyled)")
    return fig
