"""
Wisewell Failed Payments — Snapshot Reporter
Runs on GitHub Actions (Mon/Wed/Fri 9am UAE time).
Fetches live data from Recharge, computes KPIs, sends Slack report.
"""

import json
import os
import requests
import sys
from collections import defaultdict
from datetime import date, timedelta

# ── Config ────────────────────────────────────────────────────────────────────
RC_KEY   = os.environ["RECHARGE_API_KEY"]
SLACK_WH = os.environ["SLACK_WEBHOOK_URL"]
SHEET_URL = "https://docs.google.com/spreadsheets/d/11GM7gxK6FG3gP7cJ7KEEitFnHVe8uxp03HNRgiqRHPc"
SNAPSHOT_FILE = "scripts/last_snapshot.json"

RC_HEADERS = {
    "X-Recharge-Access-Token": RC_KEY,
    "X-Recharge-Version": "2021-11",
}

# ── Recharge helpers ──────────────────────────────────────────────────────────
def rc_get(url, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=RC_HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries - 1:
                import time; time.sleep(2 ** attempt)
            else:
                raise

def fetch_all_error_charges():
    """Fetch all current error-status charges from Recharge (date-window pagination)."""
    charges = []
    seen_ids = set()
    start   = date(2024, 4, 1)
    end     = date.today() + timedelta(days=1)
    current = start

    while current < end:
        next_day = current + timedelta(days=1)
        url = (
            f"https://api.rechargeapps.com/charges?status=error"
            f"&created_at_min={current.isoformat()}T00:00:00"
            f"&created_at_max={next_day.isoformat()}T00:00:00"
            f"&limit=250"
        )
        try:
            data = rc_get(url)
            for c in data.get("charges", []):
                if c["id"] not in seen_ids:
                    seen_ids.add(c["id"])
                    charges.append(c)
        except Exception:
            pass
        current = next_day

    return charges

# ── Analysis ──────────────────────────────────────────────────────────────────
def get_priority(est_debt, days):
    if est_debt >= 3000 and days >= 90: return "P1"
    if est_debt >= 1000 or  days >= 60: return "P2"
    return "P3"

def build_snapshot(charges):
    today     = date.today()
    today_str = today.isoformat()

    by_email = defaultdict(list)
    for c in charges:
        email = (c.get("customer") or {}).get("email", "")
        if email:
            by_email[email].append(c)

    # Per-product debt totals
    email_to_product = {}
    for c in charges:
        email = (c.get("customer") or {}).get("email", "")
        items = c.get("line_items", [])
        if email and items and email not in email_to_product:
            email_to_product[email] = items[0].get("title", "Unknown")

    past_flow_count = 0
    in_flow_count   = 0
    variant_count   = 0
    p1 = p2 = p3   = 0
    total_debt      = 0.0
    new_7d          = 0
    product_debt    = defaultdict(float)
    product_count   = defaultdict(int)

    cutoff_7d = (today - timedelta(days=7)).isoformat()

    for email, clist in by_email.items():
        earliest    = min(c["created_at"][:10] for c in clist)
        days_since  = (today - date.fromisoformat(earliest)).days

        # Sum max charge price per address_id to capture multiple subscriptions
        per_sub_max = {}
        for c in clist:
            aid = c.get("address_id")
            p = float(c.get("total_price", 0))
            per_sub_max[aid] = max(per_sub_max.get(aid, 0), p)
        charge_price = sum(per_sub_max.values())

        # Billing frequency — assume monthly unless it's a Filter Subscription
        # (6-month billing at AED 330–990/cycle detected by price range)
        max_single = max(per_sub_max.values())
        freq_days = 180 if max_single in (330.0, 660.0, 990.0) else 30
        billing_cycles = max(1, round(days_since / freq_days))
        est_debt = charge_price * billing_cycles

        has_future_retry = any(
            c.get("retry_date") and c["retry_date"][:10] > today_str
            for c in clist
        )
        has_variant = any(
            c.get("external_variant_not_found") or
            "VARIANT" in (c.get("error_type") or "").upper()
            for c in clist
        )

        if has_variant:
            variant_count += 1
        elif has_future_retry:
            in_flow_count += 1
        else:
            past_flow_count += 1
            total_debt += est_debt
            pri = get_priority(est_debt, days_since)
            if pri == "P1": p1 += 1
            elif pri == "P2": p2 += 1
            else: p3 += 1

            prod = email_to_product.get(email, "Unknown")
            product_debt[prod]  += est_debt
            product_count[prod] += 1

        if earliest >= cutoff_7d:
            new_7d += 1

    total_customers = len(by_email)

    return {
        "date":              today_str,
        "total_customers":   total_customers,
        "past_flow":         past_flow_count,
        "in_flow":           in_flow_count,
        "variant_error":     variant_count,
        "p1": p1, "p2": p2, "p3": p3,
        "total_debt":        round(total_debt),
        "new_7d":            new_7d,
        "product_debt":      dict(sorted(product_debt.items(), key=lambda x: -x[1])),
        "product_count":     dict(product_count),
    }

# ── Load/save snapshot ────────────────────────────────────────────────────────
def load_last_snapshot():
    if os.path.exists(SNAPSHOT_FILE):
        with open(SNAPSHOT_FILE) as f:
            return json.load(f)
    return None

def save_snapshot(snap):
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump(snap, f, indent=2)

# ── Slack formatting ──────────────────────────────────────────────────────────
def delta_str(current, previous, key, prefix="", invert=False):
    """Return a coloured delta string. invert=True means increase is bad."""
    if previous is None:
        return ""
    diff = current[key] - previous[key]
    if diff == 0:
        return "  _(no change)_"
    up_arrow   = "📈" if not invert else "🔴"
    down_arrow = "📉" if not invert else "🟢"
    arrow = up_arrow if diff > 0 else down_arrow
    sign  = "+" if diff > 0 else ""
    return f"  {arrow} {sign}{prefix}{diff:,}"

def build_slack_message(snap, prev):
    day_name = date.fromisoformat(snap["date"]).strftime("%A %d %b %Y")

    # Delta helpers
    def d(key, prefix="", invert=False):
        return delta_str(snap, prev, key, prefix, invert)

    lines = [
        f"*📊 Wisewell Failed Payments Report — {day_name}*",
        f"<{SHEET_URL}|Open Google Sheet>",
        "",
        "*── OVERVIEW ──*",
        f"• Total customers in error:  *{snap['total_customers']:,}*{d('total_customers', invert=True)}",
        f"• Past automated flow:       *{snap['past_flow']:,}*{d('past_flow', invert=True)}",
        f"• Still in auto-flow:        *{snap['in_flow']:,}*",
        f"• Variant error accounts:    *{snap['variant_error']:,}*{d('variant_error', invert=True)}",
        f"• New entrants (last 7d):    *{snap['new_7d']:,}*",
        "",
        "*── DEBT (Past-Flow Only) ──*",
        f"• Total est. debt:  *AED {snap['total_debt']:,}*{d('total_debt', 'AED ', invert=True)}",
        f"• P1 (call today):  *{snap['p1']:,}*{d('p1', invert=True)}",
        f"• P2:               *{snap['p2']:,}*",
        f"• P3:               *{snap['p3']:,}*",
        "",
        "*── BY PRODUCT ──*",
    ]

    for prod, debt in list(snap["product_debt"].items())[:6]:
        count = snap["product_count"].get(prod, 0)
        lines.append(f"• {prod}: *{count}* customers — AED *{round(debt):,}*")

    if snap["variant_error"] > 0:
        lines += [
            "",
            f"⚠️ *{snap['variant_error']} VARIANT_ERROR account(s)* — check Shopify variant status",
        ]

    if prev:
        lines += ["", f"_vs. last report ({prev['date']})_"]

    return "\n".join(lines)

# ── Send to Slack ─────────────────────────────────────────────────────────────
def send_slack(message):
    resp = requests.post(
        SLACK_WH,
        json={"text": message},
        timeout=10,
    )
    resp.raise_for_status()
    print(f"Slack message sent ({resp.status_code})")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Fetching error charges from Recharge...")
    charges = fetch_all_error_charges()
    print(f"  {len(charges)} charges fetched")

    print("Building snapshot...")
    snap = build_snapshot(charges)
    print(f"  Snapshot: {json.dumps(snap, indent=2)}")

    prev = load_last_snapshot()
    msg  = build_slack_message(snap, prev)

    print("Sending Slack message...")
    send_slack(msg)

    save_snapshot(snap)
    print("Done.")

if __name__ == "__main__":
    main()
