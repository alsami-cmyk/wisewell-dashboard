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
MARKET_FLAG = {"UAE": "🇦🇪", "KSA": "🇸🇦", "USA": "🇺🇸"}


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


def _by_market_line(df: pd.DataFrame) -> str:
    """e.g. '🇦🇪 UAE 2 · 🇺🇸 USA 1' — only markets with >0 units."""
    if df.empty:
        return "—"
    counts = df.groupby("market")["quantity"].sum().astype(int)
    parts = [
        f"{MARKET_FLAG.get(m, '')} {m} {int(n)}"
        for m, n in counts.items() if n > 0
    ]
    return "  ·  ".join(parts) if parts else "—"


def build_report_message(today: date | None = None) -> str:
    """Return the clean Slack-markdown Sparkle daily report."""
    today = today or date.today()
    today_ts = pd.Timestamp(today)
    yday = today_ts - pd.Timedelta(days=1)
    month_start = today_ts.replace(day=1)

    fx = get_fx()
    rc = load_recharge_full()
    spark = rc[(rc["product"] == PRODUCT) & (rc["category"] == "Machine")].copy()

    created_day = spark["created_at_dt"].dt.normalize()

    # Yesterday's new subs
    y = spark[created_day == yday.normalize()]
    y_units = int(y["quantity"].sum()) if not y.empty else 0
    y_arr = _arr_usd(y, fx)

    # Month-to-date new subs (1st of month → yesterday inclusive)
    mtd = spark[(created_day >= month_start) & (created_day <= yday.normalize())]
    mtd_units = int(mtd["quantity"].sum()) if not mtd.empty else 0
    mtd_arr = _arr_usd(mtd, fx)

    # Currently-active base
    active = spark[spark["status"] == "ACTIVE"]
    a_units = int(active["quantity"].sum()) if not active.empty else 0
    a_arr = _arr_usd(active, fx)

    lines = [
        "✨ *Wisewell Sparkle — Daily Sales*",
        f"_{today_ts:%A, %d %b %Y}_",
        "",
        f"*Yesterday · {yday:%a %d %b}*",
    ]
    if y_units:
        lines += [
            f"• New subscriptions:  *{y_units}*",
            f"• By market:  {_by_market_line(y)}",
            f"• New ARR added:  *{_fmt_usd(y_arr)}*",
        ]
    else:
        lines += ["• No new Sparkle subscriptions. :zzz:"]

    lines += [
        "",
        f"*Month to date · {month_start:%b %Y}*",
        f"• New subscriptions:  *{mtd_units}*   ·   ARR added:  *{_fmt_usd(mtd_arr)}*",
        "",
        "*Active Sparkle base*",
        f"• Active subs:  *{a_units}*   ·   Run-rate ARR:  *{_fmt_usd(a_arr)}*",
        f"• By market:  {_by_market_line(active)}",
    ]
    return "\n".join(lines)


def _post_to_slack(message: str) -> None:
    """Optional self-delivery via Slack Web API (GitHub Actions path)."""
    import json
    import urllib.request

    token = os.environ["SLACK_BOT_TOKEN"]
    target = os.environ["SLACK_DM_TARGET"]  # user or channel id
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps({"channel": target, "text": message, "mrkdwn": True}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    resp = json.loads(urllib.request.urlopen(req).read().decode())
    if not resp.get("ok"):
        raise RuntimeError(f"Slack post failed: {resp.get('error')}")
    print("Posted to Slack.")


if __name__ == "__main__":
    msg = build_report_message()
    print(msg)
    if os.environ.get("SLACK_BOT_TOKEN") and os.environ.get("SLACK_DM_TARGET"):
        _post_to_slack(msg)
