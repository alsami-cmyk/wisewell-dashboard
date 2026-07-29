"""
Wisewell Sparkle — daily sales report.

Computes a quick overview of Wisewell Sparkle subscription sales and returns a
clean Slack-markdown message. Intended to run every morning (10am UAE) and be
delivered as a Slack DM.

Two ways it gets delivered:
  • The message text is built by build_report_message() below, which the
    daily scheduled agent posts to Slack via the Slack tool.
  • If run directly with SLACK_BOT_TOKEN + SLACK_DM_TARGET set in the env, it
    posts the message itself via Slack's chat.postMessage (for a GitHub
    Actions cron alternative).

Scope of the report (as of the run date `today`):
  • Yesterday   — new Sparkle subs created yesterday (count + ARR added),
                  split by market.
  • Month to date — new Sparkle subs created this calendar month.
  • Active base  — all currently-active Sparkle subs (count + annualised
                  run-rate), split by market.

Sparkle is a Machine-category subscription in Recharge ("Wisewell Sparkle
Subscription" in both the US and UAE); recurring_price comes straight from the
Recharge tabs and is converted to USD via the live FX helper.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pandas as pd

# utils lives one level up from scripts/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import get_fx, load_recharge_full  # noqa: E402

PRODUCT = "Sparkle"


def _fmt_usd(v: float) -> str:
    return f"${v:,.0f}"


def _arr_usd(df: pd.DataFrame, fx: dict) -> float:
    """Annualised run-rate in USD for the given Sparkle rows."""
    if df.empty:
        return 0.0
    freq = df["charge_interval_frequency"].replace(0, 1).fillna(1)
    arr_local = df["recurring_price"].fillna(0) * df["quantity"].fillna(1) * (12.0 / freq)
    rate = df["currency"].fillna("USD").map(lambda c: fx.get(c, 1.0))
    return float((arr_local * rate).sum())


def _market_rows(df: pd.DataFrame, fx: dict) -> tuple[list[tuple[str, int, float]], int, float]:
    """Per-market (label, subs, arr_usd) for markets with >0 subs, plus totals."""
    rows: list[tuple[str, int, float]] = []
    tot_subs, tot_arr = 0, 0.0
    for m in ("UAE", "KSA", "USA"):
        sub = df[df["market"] == m]
        subs = int(sub["quantity"].sum()) if not sub.empty else 0
        if subs <= 0:
            continue
        arr = _arr_usd(sub, fx)
        rows.append((m, subs, arr))
        tot_subs += subs
        tot_arr += arr
    return rows, tot_subs, tot_arr


def _table(rows: list[tuple[str, int, float]], tot_subs: int, tot_arr: float,
           value_header: str) -> str:
    """Monospace code-block table: Market | Subs | <value_header>.

    A code block keeps columns aligned and renders identically across every
    Slack surface (native mrkdwn has no real table support).
    """
    def _row(label: str, subs: int, arr: float) -> str:
        return f"{label:<8}{subs:>6}{_fmt_usd(arr):>15}"

    lines = [f"{'Market':<8}{'Subs':>6}{value_header:>15}"]
    lines.append("─" * 29)
    for label, subs, arr in rows:
        lines.append(_row(label, subs, arr))
    lines.append("─" * 29)
    lines.append(_row("Total", tot_subs, tot_arr))
    return "```\n" + "\n".join(lines) + "\n```"


def build_report_message(today: date | None = None) -> str:
    """Return the clean Slack Sparkle daily report (yesterday + active base)."""
    today = today or date.today()
    today_ts = pd.Timestamp(today)
    yday = today_ts - pd.Timedelta(days=1)

    fx = get_fx()
    rc = load_recharge_full()
    spark = rc[(rc["product"] == PRODUCT) & (rc["category"] == "Machine")].copy()
    created_day = spark["created_at_dt"].dt.normalize()

    # Yesterday's new subs
    y = spark[created_day == yday.normalize()]
    y_rows, y_subs, y_arr = _market_rows(y, fx)

    # Currently-active base
    active = spark[spark["status"] == "ACTIVE"]
    a_rows, a_subs, a_arr = _market_rows(active, fx)

    lines = [
        "✨ *Wisewell Sparkle — Daily Sales*",
        f"_{today_ts:%A, %d %b %Y}_",
        "",
        f"*Yesterday · {yday:%a %d %b}*",
    ]
    if y_subs:
        lines.append(_table(y_rows, y_subs, y_arr, "ARR Added"))
    else:
        lines.append("_No new Sparkle subscriptions._")

    lines += [
        "",
        "*Active Sparkle base*",
    ]
    if a_subs:
        lines.append(_table(a_rows, a_subs, a_arr, "Run-Rate"))
    else:
        lines.append("_No active Sparkle subscriptions._")

    return "\n".join(lines)


def _slack_api(method: str, token: str, payload: dict) -> dict:
    import json
    import urllib.request

    req = urllib.request.Request(
        f"https://slack.com/api/{method}",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    resp = json.loads(urllib.request.urlopen(req).read().decode())
    if not resp.get("ok"):
        raise RuntimeError(f"Slack {method} failed: {resp.get('error')}")
    return resp


def _post_to_slack(message: str) -> None:
    """Self-delivery via Slack Web API (GitHub Actions path).

    Requires a bot token with `chat:write` (+ `im:write` to open the DM).
    SLACK_DM_TARGET is a Slack user id (Uxxxx) → we open the DM channel and
    post there. If a channel id (Cxxxx) is given, we post to it directly.
    """
    token = os.environ["SLACK_BOT_TOKEN"]
    target = os.environ["SLACK_DM_TARGET"]

    channel = target
    if target.startswith("U"):  # user id → open the DM channel first
        opened = _slack_api("conversations.open", token, {"users": target})
        channel = opened["channel"]["id"]

    _slack_api("chat.postMessage", token, {"channel": channel, "text": message})
    print("Posted to Slack.")


if __name__ == "__main__":
    msg = build_report_message()
    print(msg)
    if os.environ.get("SLACK_BOT_TOKEN") and os.environ.get("SLACK_DM_TARGET"):
        _post_to_slack(msg)
