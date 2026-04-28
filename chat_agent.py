"""
chat_agent.py — Wisewell data-analyst agent powered by Claude.

Used by the "💬 Ask Claude" sidebar dialog in dashboard.py.

Architecture
============
The agent is a tool-using Claude Sonnet 4.5 instance. Tools wrap the
existing utils.py data-layer functions, so anything the dashboard can
show is something the chatbot can answer with consistent numbers.

For simple questions ("how many UAE Model 1 subs are active?") Claude
calls one tool with a tight filter and returns instantly. For analysis
questions ("any worrying retention trends?") it loops through several
tools, reasons over the results, and summarises.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from typing import Any

import pandas as pd
import streamlit as st

# ── Anthropic client ─────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def _client():
    """Cached Anthropic client. Reads ANTHROPIC_API_KEY from Streamlit secrets."""
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise RuntimeError(
            "anthropic SDK not installed. Add `anthropic>=0.40.0` to requirements.txt."
        ) from e
    api_key = (
        st.secrets.get("ANTHROPIC_API_KEY", None)
        if hasattr(st, "secrets") else None
    ) or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not found in Streamlit secrets or environment."
        )
    return Anthropic(api_key=api_key)


MODEL_ID = "claude-sonnet-4-5-20250929"

PRODUCTS = ["Model 1", "Nano+", "Bubble", "Flat", "Nano Tank"]
MARKETS  = ["UAE", "KSA", "USA"]


# ─────────────────────────────────────────────────────────────────────────────
# Tool implementations — each calls existing utils.py functions and returns a
# small JSON-serialisable summary, NOT raw DataFrames.
# ─────────────────────────────────────────────────────────────────────────────
def _ts(d: str) -> pd.Timestamp:
    return pd.Timestamp(d).normalize()


def _filter_by_mp(df: pd.DataFrame, market: str | None, product: str | None) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df
    if market and "market" in out.columns:
        out = out[out["market"] == market]
    if product and "product" in out.columns:
        out = out[out["product"] == product]
    return out


def query_sales(start_date: str, end_date: str,
                market: str | None = None, product: str | None = None) -> dict:
    """All machine sales (subs + ownership) in the window."""
    from utils import get_all_machine_sales
    df = get_all_machine_sales(start_dt=_ts(start_date), end_dt=_ts(end_date))
    df = _filter_by_mp(df, market, product)
    if df is None or df.empty:
        return {"total_qty": 0, "by_market": {}, "by_product": {},
                "subscriptions": 0, "ownership": 0}
    by_mkt   = df.groupby("market")["qty"].sum().to_dict()
    by_prod  = df.groupby("product")["qty"].sum().to_dict()
    subs_qty = int(df.loc[~df["is_ownership"], "qty"].sum())
    own_qty  = int(df.loc[df["is_ownership"],  "qty"].sum())
    return {
        "window": f"{start_date} → {end_date}",
        "filter": {"market": market, "product": product},
        "total_qty": int(df["qty"].sum()),
        "subscriptions": subs_qty,
        "ownership": own_qty,
        "by_market":  {k: int(v) for k, v in by_mkt.items()},
        "by_product": {k: int(v) for k, v in by_prod.items()},
    }


def query_active_users(as_of: str,
                       market: str | None = None,
                       product: str | None = None) -> dict:
    """Active user base (machine subs + owners) at a point in time."""
    from utils import get_active_subscriptions, get_active_ownership
    ts = _ts(as_of)
    subs = _filter_by_mp(get_active_subscriptions(as_of=ts), market, product)
    own  = _filter_by_mp(get_active_ownership(as_of=ts),     market, product)
    subs_total = int(subs["qty"].sum()) if subs is not None and not subs.empty else 0
    own_total  = int(own["qty"].sum())  if own is not None and not own.empty  else 0

    def _bd(df):
        if df is None or df.empty:
            return {}
        return {f"{r['market']}/{r['product']}": int(r["qty"]) for _, r in df.iterrows()}

    return {
        "as_of": as_of,
        "filter": {"market": market, "product": product},
        "total_users":     subs_total + own_total,
        "active_subs":     subs_total,
        "active_owners":   own_total,
        "subs_breakdown":  _bd(subs),
        "owner_breakdown": _bd(own),
    }


def query_churn(start_date: str, end_date: str,
                market: str | None = None, product: str | None = None) -> dict:
    """True cancellations in window + rate (cancels / active subs at start)."""
    from utils import load_recharge_full, get_active_subscriptions
    rc = load_recharge_full()
    if rc.empty:
        return {"churned": 0, "active_at_start": 0, "rate": 0.0,
                "avg_lifetime_months": 0.0}
    rc_m = rc[rc["category"] == "Machine"].copy()
    if market:  rc_m = rc_m[rc_m["market"]  == market]
    if product: rc_m = rc_m[rc_m["product"] == product]

    s_ts, e_ts = _ts(start_date), _ts(end_date)
    mask = (
        rc_m["is_true_cancel"]
        & rc_m["cancelled_at_dt"].notna()
        & (rc_m["cancelled_at_dt"] >= s_ts)
        & (rc_m["cancelled_at_dt"] <= e_ts)
    )
    churn_df = rc_m.loc[mask]
    churned  = int(churn_df["quantity"].sum())

    # Active subs at start (denominator) — Recharge only, machine-subs convention
    subs_start = _filter_by_mp(get_active_subscriptions(as_of=s_ts), market, product)
    active_start = int(subs_start["qty"].sum()) if subs_start is not None and not subs_start.empty else 0

    rate = (churned / active_start) if active_start > 0 else 0.0

    # Avg lifetime
    avg_lt = 0.0
    if not churn_df.empty:
        lt_days = (churn_df["cancelled_at_dt"] - churn_df["created_at_dt"]).dt.days
        lt_days = lt_days[lt_days.notna() & (lt_days >= 0)]
        if len(lt_days):
            avg_lt = float(lt_days.mean() / 30.44)

    return {
        "window":                f"{start_date} → {end_date}",
        "filter":                {"market": market, "product": product},
        "churned":               churned,
        "active_at_start":       active_start,
        "rate":                  round(rate, 4),
        "rate_pct":              round(rate * 100, 2),
        "avg_lifetime_months":   round(avg_lt, 1),
    }


def query_cancellation_reasons(start_date: str, end_date: str,
                               market: str | None = None,
                               product: str | None = None) -> dict:
    """Top cancellation reasons in the window."""
    from utils import load_recharge_full
    rc = load_recharge_full()
    if rc.empty:
        return {"total": 0, "reasons": {}}
    rc_m = rc[rc["category"] == "Machine"].copy()
    if market:  rc_m = rc_m[rc_m["market"]  == market]
    if product: rc_m = rc_m[rc_m["product"] == product]

    s_ts, e_ts = _ts(start_date), _ts(end_date)
    mask = (
        rc_m["is_true_cancel"]
        & rc_m["cancelled_at_dt"].notna()
        & (rc_m["cancelled_at_dt"] >= s_ts)
        & (rc_m["cancelled_at_dt"] <= e_ts)
    )
    df = rc_m.loc[mask]
    if df.empty:
        return {"window": f"{start_date} → {end_date}", "total": 0, "reasons": {}}

    grouped = (
        df.groupby("cancellation_reason")["quantity"]
        .sum().sort_values(ascending=False).to_dict()
    )
    total = int(sum(grouped.values()))
    return {
        "window":  f"{start_date} → {end_date}",
        "filter":  {"market": market, "product": product},
        "total":   total,
        "reasons": {k: {"count": int(v), "pct": round(v / total * 100, 1)}
                    for k, v in grouped.items()},
    }


def query_cohort_retention(cohort_month: str, k_months: int = 6,
                           market: str | None = None,
                           product: str | None = None) -> dict:
    """% of a signup-month cohort still active after k_months."""
    from utils import load_recharge_full
    rc = load_recharge_full()
    rc = rc[rc["category"] == "Machine"].copy()
    if market:  rc = rc[rc["market"]  == market]
    if product: rc = rc[rc["product"] == product]
    rc = rc.dropna(subset=["created_at_dt"])
    cm = pd.Timestamp(cohort_month).to_period("M").to_timestamp()
    cohort = rc[rc["created_at_dt"].dt.to_period("M").dt.to_timestamp() == cm]
    size   = int(cohort["quantity"].sum())
    if size == 0:
        return {"cohort_month": cm.strftime("%b %Y"), "size": 0,
                "retention_curve": {}}
    today = pd.Timestamp.today().normalize()
    curve = {}
    for k in range(min(k_months + 1, 13)):
        k_end = (cm + pd.DateOffset(months=k + 1)) - pd.Timedelta(days=1)
        if k_end > today:
            break
        active_mask = (
            cohort["cancelled_at_dt"].isna()
            | (~cohort["is_true_cancel"])
            | (cohort["cancelled_at_dt"] > k_end)
        )
        active = int(cohort.loc[active_mask, "quantity"].sum())
        curve[f"M{k}"] = {"active": active, "pct": round(active / size * 100, 1)}
    return {
        "cohort_month": cm.strftime("%b %Y"),
        "filter":       {"market": market, "product": product},
        "size":         size,
        "retention_curve": curve,
    }


def query_marketing_spend(start_date: str, end_date: str,
                          market: str | None = None) -> dict:
    """Day-prorated marketing spend in USD over window."""
    from utils import load_marketing_spend
    mkt = load_marketing_spend()
    if mkt is None or mkt.empty:
        return {"total_usd": 0.0}
    col_map = {"UAE": "uae_usd", "KSA": "ksa_usd", "USA": "usa_usd"}
    col = col_map.get(market, "total_usd")

    s_ts, e_ts = _ts(start_date), _ts(end_date)
    month_to_spend = {ms: float(v) if pd.notna(v) else 0.0
                      for ms, v in zip(mkt["month_dt"], mkt[col])}
    total = 0.0
    for day in pd.date_range(s_ts, e_ts, freq="D"):
        m_start = day.to_period("M").to_timestamp()
        days_in_month = (m_start + pd.offsets.MonthEnd(0)).day
        total += month_to_spend.get(m_start, 0.0) / days_in_month
    return {
        "window":    f"{start_date} → {end_date}",
        "filter":    {"market": market},
        "total_usd": round(total, 2),
    }


def query_arr(as_of: str, market: str | None = None) -> dict:
    """ARR (USD) from active Machine + Filter subs at the as_of date."""
    from utils import load_recharge_full, get_fx
    rc = load_recharge_full()
    if rc.empty:
        return {"arr_usd": 0.0}
    if market:
        rc = rc[rc["market"] == market]
    ts = _ts(as_of)
    mask = (
        rc["category"].isin(["Machine", "Filter"])
        & rc["created_at_dt"].notna()
        & (rc["created_at_dt"] <= ts)
        & (rc["cancelled_at_dt"].isna() | (rc["cancelled_at_dt"] > ts))
    )
    active = rc.loc[mask]
    if active.empty:
        return {"arr_usd": 0.0}
    freq      = active["charge_interval_frequency"].replace(0, 1).fillna(1)
    price     = active["recurring_price"].fillna(0)
    qty       = active["quantity"].fillna(0)
    arr_local = price * qty * (12.0 / freq)
    fx        = get_fx()
    currency  = active["currency"].fillna("USD")
    arr_usd   = arr_local * currency.map(lambda c: fx.get(c, 1.0))
    return {
        "as_of":   as_of,
        "filter":  {"market": market},
        "arr_usd": round(float(arr_usd.sum()), 2),
    }


def get_data_summary() -> dict:
    """High-level orientation: today's date, products, markets, available data range."""
    from utils import load_recharge_full
    today = date.today().isoformat()
    rc = load_recharge_full()
    earliest = rc["created_at_dt"].min().date().isoformat() if not rc.empty else None
    return {
        "today":    today,
        "markets":  MARKETS,
        "products": PRODUCTS,
        "data_range": {
            "earliest_recharge_signup": earliest,
            "live_data_starts":         "2025-09-01",
        },
        "notes": (
            "Machine subscriptions and ownership purchases are both tracked. "
            "Churn refers to true cancellations only (excludes swaps/conversions). "
            "ARR includes Machine + Filter subs (recurring revenue)."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tool registry — schemas for Claude's tool-use API
# ─────────────────────────────────────────────────────────────────────────────
_DATE = {"type": "string", "description": "ISO date YYYY-MM-DD."}
_MARKET = {"type": "string", "enum": MARKETS, "description": "Country market filter."}
_PRODUCT = {"type": "string", "enum": PRODUCTS, "description": "Product filter."}

TOOLS = [
    {
        "name": "get_data_summary",
        "description": "Get a high-level summary: today's date, available markets, "
                       "products, and data ranges. Use this first if you need to "
                       "orient yourself before answering.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "query_sales",
        "description": "Count of new machine sales (subscriptions + ownership) in a "
                       "date window. Combines Recharge, Shopify, and Offline sources.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": _DATE,
                "end_date":   _DATE,
                "market":     _MARKET,
                "product":    _PRODUCT,
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "query_active_users",
        "description": "Active user base at a point in time = active machine "
                       "subscriptions + active ownership. Use this for 'how many "
                       "users do we have' style questions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "as_of":   _DATE,
                "market":  _MARKET,
                "product": _PRODUCT,
            },
            "required": ["as_of"],
        },
    },
    {
        "name": "query_churn",
        "description": "True cancellations in a window plus churn rate (cancels ÷ "
                       "active machine subs at start of window).",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": _DATE,
                "end_date":   _DATE,
                "market":     _MARKET,
                "product":    _PRODUCT,
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "query_cancellation_reasons",
        "description": "Top cancellation reasons for true cancels in a window, "
                       "with counts and percentages.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": _DATE,
                "end_date":   _DATE,
                "market":     _MARKET,
                "product":    _PRODUCT,
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "query_cohort_retention",
        "description": "Retention curve for a signup-month cohort: how many of "
                       "those signups were still active at month 0, 1, 2, ... "
                       "Swap/conversion cancels count as retained.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cohort_month": {"type": "string",
                                 "description": "First day of the cohort month, "
                                                "e.g. '2025-09-01'."},
                "k_months":     {"type": "integer", "minimum": 1, "maximum": 12,
                                 "description": "How many months out to compute. "
                                                "Default 6."},
                "market":       _MARKET,
                "product":      _PRODUCT,
            },
            "required": ["cohort_month"],
        },
    },
    {
        "name": "query_marketing_spend",
        "description": "Marketing spend in USD over a date window. Day-prorated "
                       "from monthly totals so sub-month windows are accurate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": _DATE,
                "end_date":   _DATE,
                "market":     _MARKET,
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "query_arr",
        "description": "ARR (USD) from active Machine + Filter subscriptions at "
                       "a point in time. Ownership purchases excluded (one-time).",
        "input_schema": {
            "type": "object",
            "properties": {
                "as_of":  _DATE,
                "market": _MARKET,
            },
            "required": ["as_of"],
        },
    },
]

_TOOL_FN = {
    "get_data_summary":           get_data_summary,
    "query_sales":                query_sales,
    "query_active_users":         query_active_users,
    "query_churn":                query_churn,
    "query_cancellation_reasons": query_cancellation_reasons,
    "query_cohort_retention":     query_cohort_retention,
    "query_marketing_spend":      query_marketing_spend,
    "query_arr":                  query_arr,
}


# ─────────────────────────────────────────────────────────────────────────────
# System prompt
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = f"""\
You are Wisewell's data analyst assistant, embedded in their internal Streamlit
dashboard. Your job is to answer questions about the business using the tools
provided.

# Business context
Wisewell sells water filtration machines and recurring filter subscriptions.

- Markets: UAE, KSA, USA
- Machine products: Model 1, Nano+, Bubble, Flat, Nano Tank
- Two revenue models per machine: Subscription (recurring) and Ownership (one-time)
- Filter subs renew monthly/quarterly per machine
- Today's date is {date.today().isoformat()}

# Conventions
- "Active users" = active machine subs + active machine owners
- "Churn" = true cancellations only — swaps/conversions are NOT churn (they
  count as retained)
- "ARR" = annualised recurring revenue from Machine + Filter subs in USD
- "Sales" combine subscription signups + ownership purchases
- All currency values are in USD unless stated otherwise

# How to answer
1. For simple lookups (sales counts, user totals), call ONE tool and report
   the answer concisely. Use bold for the headline number.
2. For analytical / open-ended questions ("any worrying trends?"), call
   multiple tools, reason over the results, and write a short structured
   answer with bullet points and the specific numbers backing each claim.
3. Always cite specific numbers — never make up figures.
4. If a tool returns 0 or empty, say so explicitly. Don't gloss over gaps.
5. Keep answers tight. Bullet points and short paragraphs over walls of text.
6. Use markdown. Tables for breakdowns. Bold for key numbers.
7. Don't preamble — answer directly. Skip "Great question!" and similar.

# Comparing periods
For "trailing X days vs previous X days" or "this month vs last month",
make TWO tool calls (one per window) and compute deltas yourself.

If a user's question is ambiguous (e.g. "how are we doing?"), pick a
reasonable interpretation and answer; don't ask for clarification unless
it's truly impossible to proceed.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Agent loop
# ─────────────────────────────────────────────────────────────────────────────
def run_agent(user_message: str, history: list[dict] | None = None,
              max_iterations: int = 8) -> str:
    """
    Run a tool-using Claude agent loop and return the final text response.

    Parameters
    ----------
    user_message : str
        The current user turn (already stored in `history` is fine; this is
        kept for clarity/future use).
    history : list[dict]
        Full chat history including the latest user message:
        [{"role": "user"|"assistant", "content": str}, ...]
    """
    client = _client()

    # Convert UI history to Anthropic messages format
    messages: list[dict[str, Any]] = []
    for msg in history or []:
        # Skip empty assistant messages (could happen mid-stream)
        if not msg.get("content"):
            continue
        messages.append({"role": msg["role"], "content": msg["content"]})

    for _ in range(max_iterations):
        resp = client.messages.create(
            model=MODEL_ID,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if resp.stop_reason == "tool_use":
            # Save the assistant's tool-use block
            messages.append({"role": "assistant", "content": resp.content})
            # Execute each tool_use block, return tool_results
            tool_results: list[dict[str, Any]] = []
            for block in resp.content:
                if getattr(block, "type", None) == "tool_use":
                    fn = _TOOL_FN.get(block.name)
                    if fn is None:
                        result = {"error": f"unknown tool: {block.name}"}
                    else:
                        try:
                            result = fn(**(block.input or {}))
                        except Exception as exc:
                            result = {"error": f"{type(exc).__name__}: {exc}"}
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    })
            messages.append({"role": "user", "content": tool_results})
            continue

        # Final assistant response
        text_parts = [
            getattr(b, "text", "") for b in resp.content
            if getattr(b, "type", None) == "text"
        ]
        return "\n".join(text_parts).strip() or "_(no response)_"

    return "⚠️ Hit max iterations without a final answer. Try rephrasing."
