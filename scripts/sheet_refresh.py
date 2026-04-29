"""
Wisewell Failed Payments — Daily Sheet Refresh
Runs on GitHub Actions at 07:00 UAE (03:00 UTC) every day.
Fetches live data from Recharge and rebuilds Machine Debt, Filter Debt, and Dashboard tabs.
CRM tracking columns (O–AM) are preserved for existing customers.
"""

import json
import os
import time
import requests
from collections import defaultdict, Counter
from datetime import date, timedelta
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request


# ── Config ─────────────────────────────────────────────────────────────────────
RC_KEY        = os.environ["RECHARGE_API_KEY"]
SHEET_ID      = "11GM7gxK6FG3gP7cJ7KEEitFnHVe8uxp03HNRgiqRHPc"
MACH_GID      = 1094358245
FILT_GID      = 990903311
DASH_GID      = 1448003323
SUCCESS_CACHE = "scripts/success_counts.json"

RC_HEADERS = {
    "X-Recharge-Access-Token": RC_KEY,
    "X-Recharge-Version": "2021-11",
}


# ── Google Sheets auth ─────────────────────────────────────────────────────────
def get_sheets_creds():
    token_json = os.environ["GOOGLE_TOKEN_JSON"]
    td = json.loads(token_json)
    creds = Credentials(
        token=td.get("token"),
        refresh_token=td["refresh_token"],
        token_uri=td["token_uri"],
        client_id=td["client_id"],
        client_secret=td["client_secret"],
        scopes=td.get("scopes", ["https://www.googleapis.com/auth/spreadsheets"]),
    )
    if not creds.valid:
        creds.refresh(Request())
    return creds


# ── Recharge helpers ───────────────────────────────────────────────────────────
def rc_get(url, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=RC_HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise


def fetch_all_error_charges():
    """Fetch all error-status charges from Recharge via daily-window scan."""
    charges, seen_ids = [], set()
    start   = date(2024, 4, 1)
    end     = date.today() + timedelta(days=1)
    current = start
    total   = (end - start).days
    done    = 0
    while current < end:
        nxt = current + timedelta(days=1)
        url = (
            f"https://api.rechargeapps.com/charges?status=error"
            f"&created_at_min={current.isoformat()}T00:00:00"
            f"&created_at_max={nxt.isoformat()}T00:00:00&limit=250"
        )
        try:
            for c in rc_get(url).get("charges", []):
                if c["id"] not in seen_ids:
                    seen_ids.add(c["id"])
                    charges.append(c)
        except Exception:
            pass
        current = nxt
        done += 1
        if done % 100 == 0:
            print(f"  {done}/{total} days scanned, {len(charges)} charges", flush=True)
    return charges


def fetch_success_counts(charges, cache_path):
    """Return email → success charge count. Only fetches missing emails."""
    # Load cache
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            counts = json.load(f)
    else:
        counts = {}

    # Build email → customer_id
    email_to_cid = {}
    for c in charges:
        email = (c.get("customer") or {}).get("email", "")
        cid   = (c.get("customer") or {}).get("id", "")
        if email and cid and email not in email_to_cid:
            email_to_cid[email] = cid

    # Fetch only missing
    missing = [e for e in email_to_cid if e not in counts]
    print(f"  Fetching success counts for {len(missing)} new customers...", flush=True)
    for i, email in enumerate(missing):
        cid = email_to_cid[email]
        try:
            url  = f"https://api.rechargeapps.com/charges?customer_id={cid}&status=success&limit=250"
            data = rc_get(url)
            counts[email] = len(data.get("charges", []))
        except Exception:
            counts[email] = 0
        if (i + 1) % 20 == 0:
            print(f"    {i+1}/{len(missing)}", flush=True)
        time.sleep(0.05)

    # Save updated cache
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(counts, f)

    return counts


# ── Analysis ───────────────────────────────────────────────────────────────────
def is_filter_product(title):
    return "filter" in title.lower()


def build_analysis(charges, success_counts):
    today     = date.today()
    today_str = today.isoformat()

    by_email = defaultdict(list)
    for c in charges:
        email = (c.get("customer") or {}).get("email", "")
        if email:
            by_email[email].append(c)

    results = []
    for email, clist in by_email.items():
        earliest  = min(c["created_at"][:10] for c in clist)
        days_since = (today - date.fromisoformat(earliest)).days

        # Per-subscription max price (handles multi-sub customers)
        per_sub_max   = {}
        per_sub_title = {}
        for c in clist:
            aid   = c.get("address_id")
            price = float(c.get("total_price", 0))
            if price > per_sub_max.get(aid, 0):
                per_sub_max[aid]   = price
                items = c.get("line_items", [])
                per_sub_title[aid] = items[0].get("title", "Unknown") if items else "Unknown"

        charge_price = sum(per_sub_max.values())
        main_aid     = max(per_sub_max, key=per_sub_max.get)
        product      = per_sub_title[main_aid]
        is_filt      = is_filter_product(product)

        # Billing frequency: filter = 6-month cycles, machines = monthly
        freq_days      = 180 if is_filt else 30
        billing_cycles = max(1, round(days_since / freq_days))
        est_debt       = charge_price * billing_cycles

        # Customer info
        cust    = (clist[0].get("customer") or {})
        billing = clist[0].get("billing_address") or {}
        fname   = (billing.get("first_name") or cust.get("first_name") or "").strip()
        lname   = (billing.get("last_name")  or cust.get("last_name")  or "").strip()
        name    = (fname + " " + lname).strip() or email.split("@")[0]
        phone   = (billing.get("phone") or cust.get("phone") or "").strip()
        city    = (billing.get("city") or "").strip()

        # Qty
        qty = len(per_sub_max) if len(per_sub_max) > 1 else 1
        if qty == 1:
            for c in clist:
                items = c.get("line_items", [])
                if items:
                    q = int(items[0].get("quantity", 1) or 1)
                    qty = max(qty, q)

        has_future_retry = any(
            c.get("retry_date") and c["retry_date"][:10] > today_str
            for c in clist
        )
        error_types  = list({c.get("error_type", "") for c in clist if c.get("error_type")})
        max_attempts = max((c.get("times_attempted", 0) or 0) for c in clist)
        succ         = success_counts.get(email, 0)
        latest       = max(c["created_at"][:10] for c in clist)

        if has_future_retry:
            stage = "AUTO FLOW"
        else:
            if days_since > 90:
                stage = "ESCALATE"
            elif days_since >= 31 and succ >= 4:
                stage = "INTERNAL — STANDARD"
            elif days_since >= 31:
                stage = "INTERNAL — PRIORITY"
            else:
                stage = "AUTO FLOW"

        results.append({
            "email":          email,
            "full_name":      name,
            "phone":          phone,
            "city":           city,
            "product":        product,
            "qty":            qty,
            "monthly_fee":    charge_price,
            "est_debt":       round(est_debt, 2),
            "billing_cycles": billing_cycles,
            "days_since":     days_since,
            "earliest":       earliest,
            "latest":         latest,
            "succ_cycles":    succ,
            "max_attempts":   max_attempts,
            "error_types":    error_types,
            "stage":          stage,
            "is_filter":      is_filt,
            "cohort":         earliest[:7],
        })

    return results


# ── Sheet helpers ──────────────────────────────────────────────────────────────
def sheets_get(creds, range_name):
    resp = requests.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{range_name}",
        headers={"Authorization": f"Bearer {creds.token}"},
    )
    return resp.json().get("values", [])


def sheets_clear(creds, range_name):
    requests.post(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{range_name}:clear",
        headers={"Authorization": f"Bearer {creds.token}"},
    )


def sheets_put(creds, range_name, values):
    resp = requests.put(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/{range_name}"
        f"?valueInputOption=USER_ENTERED",
        headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
        json={"values": values},
    )
    if not resp.ok:
        print(f"  sheets_put error on {range_name}: {resp.status_code} {resp.text[:200]}")


def get_crm_map(creds, tab_name):
    """Read cols O–AM (CRM tracking) keyed by email (col C = index 2)."""
    rows = sheets_get(creds, f"{tab_name}!A2:AM1000")
    crm  = {}
    for row in rows:
        email = row[2].strip().lower() if len(row) > 2 else ""
        if not email: continue
        data  = row[14:] if len(row) > 14 else []
        if any(v.strip() for v in data):
            crm[email] = data
    return crm


# ── Build rows ─────────────────────────────────────────────────────────────────
STAGE_ORDER = {"ESCALATE": 0, "INTERNAL — PRIORITY": 1, "INTERNAL — STANDARD": 2, "AUTO FLOW": 3}


def make_sheet_rows(records, crm_map):
    rows = []
    for r in sorted(records, key=lambda x: (STAGE_ORDER.get(x["stage"], 4), -x["est_debt"])):
        core = [
            r["stage"],
            r["full_name"],
            r["email"],
            r["phone"],
            r["city"],
            r["product"],
            str(r["qty"]),
            str(int(r["monthly_fee"])),
            str(int(r["est_debt"])),
            str(r["billing_cycles"]),   # J: Billing Cycles Missed
            str(r["days_since"]),       # K: Days Overdue
            str(r["succ_cycles"]),      # L: Successful Billing Cycles
            r["earliest"],              # M: First Failure
            r["latest"],                # N: Last Error
        ]
        crm = crm_map.get(r["email"].strip().lower(), [])
        rows.append(core + crm)
    return rows


# ── Dashboard rebuild ──────────────────────────────────────────────────────────
def update_dashboard(creds, machine_r, filt_r):
    mach_debt    = sum(r["est_debt"] for r in machine_r)
    filt_debt    = sum(r["est_debt"] for r in filt_r)
    mach_escalate = sum(1 for r in machine_r if r["stage"] == "ESCALATE")
    avg_days     = round(sum(r["days_since"] for r in machine_r) / len(machine_r)) if machine_r else 0

    # KPI row
    sheets_put(creds, "Dashboard!A4:L4", [[
        str(len(machine_r)), "", f"AED {int(mach_debt):,}", "",
        str(len(filt_r)),    "", f"AED {int(filt_debt):,}", "",
        str(mach_escalate),  "", f"{avg_days} days", "",
    ]])

    # Machine stage breakdown
    mstage_count = Counter(r["stage"] for r in machine_r)
    mstage_debt  = {s: sum(r["est_debt"] for r in machine_r if r["stage"] == s) for s in mstage_count}
    stage_rows   = []
    labels = [
        ("AUTO FLOW",            "🔵  AUTO FLOW"),
        ("INTERNAL — STANDARD",  "🟢  INTERNAL — STANDARD"),
        ("INTERNAL — PRIORITY",  "🟠  INTERNAL — PRIORITY"),
        ("ESCALATE",             "🔴  ESCALATE"),
    ]
    for key, label in labels:
        c = mstage_count.get(key, 0)
        d = mstage_debt.get(key, 0)
        pct = f"{d/mach_debt*100:.1f}%" if mach_debt else "0%"
        avg = str(round(d / c)) if c else "0"
        stage_rows.append([label, str(c), f"{int(d):,}", pct, avg])
    stage_rows.append(["TOTAL", str(len(machine_r)), f"{int(mach_debt):,}", "1",
                        str(round(mach_debt / len(machine_r)) if machine_r else 0)])
    sheets_put(creds, "Dashboard!A8:E13", stage_rows)

    # Filter overview
    fstage_count = Counter(r["stage"] for r in filt_r)
    fstage_debt  = {s: sum(r["est_debt"] for r in filt_r if r["stage"] == s) for s in fstage_count}
    filter_rows  = []
    for key, label, note in [
        ("AUTO FLOW", "🔵  AUTO FLOW", "Still being retried by Recharge"),
        ("ESCALATE",  "🔴  ESCALATE",  "No payment = no filter replacement (machine rendered unusable)"),
    ]:
        c = fstage_count.get(key, 0)
        d = fstage_debt.get(key, 0)
        filter_rows.append([label, str(c), f"{int(d):,}", note])
    filter_rows.append(["TOTAL", str(len(filt_r)), f"{int(filt_debt):,}",
                         "Lower urgency — leverage filter dependency"])
    sheets_put(creds, "Dashboard!A15:D18", filter_rows)

    # Cohort analysis
    cohorts = defaultdict(lambda: {"n": 0, "debt": 0, "days": [], "succ": [], "stages": Counter()})
    for r in machine_r:
        c = r["cohort"]
        cohorts[c]["n"]      += 1
        cohorts[c]["debt"]   += r["est_debt"]
        cohorts[c]["days"].append(r["days_since"])
        cohorts[c]["succ"].append(r["succ_cycles"])
        cohorts[c]["stages"][r["stage"]] += 1

    cohort_rows = []
    for coh in sorted(cohorts):
        d = cohorts[coh]
        cohort_rows.append([
            coh,
            str(d["n"]),
            f"{int(d['debt']):,}",
            str(round(sum(d["days"]) / len(d["days"]))) if d["days"] else "0",
            str(round(sum(d["succ"]) / len(d["succ"]), 1)) if d["succ"] else "0",
            str(d["stages"].get("ESCALATE", 0)),
            str(d["stages"].get("INTERNAL — PRIORITY", 0)),
            str(d["stages"].get("INTERNAL — STANDARD", 0)),
            str(d["stages"].get("AUTO FLOW", 0)),
        ])
    cohort_rows.append([
        "TOTAL", str(len(machine_r)), f"{int(mach_debt):,}", "", "",
        str(sum(cohorts[c]["stages"].get("ESCALATE", 0)           for c in cohorts)),
        str(sum(cohorts[c]["stages"].get("INTERNAL — PRIORITY", 0) for c in cohorts)),
        str(sum(cohorts[c]["stages"].get("INTERNAL — STANDARD", 0) for c in cohorts)),
        str(sum(cohorts[c]["stages"].get("AUTO FLOW", 0)           for c in cohorts)),
    ])

    # Clear old cohort rows then write new ones (leave rows 50+ untouched — analyst section)
    sheets_clear(creds, "Dashboard!A22:I49")
    sheets_put(creds, "Dashboard!A22", cohort_rows)
    print(f"  Dashboard: {len(machine_r)} machine, {len(filt_r)} filter, {len(cohort_rows)-1} cohorts")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print("=== Wisewell Daily Sheet Refresh ===", flush=True)
    today = date.today()

    # 1. Fetch error charges
    print(f"\n[1/5] Fetching Recharge error charges...", flush=True)
    charges = fetch_all_error_charges()
    print(f"  {len(charges)} charges across {len({(c.get('customer') or {}).get('email','') for c in charges})} customers")

    # 2. Success counts (cached)
    print("\n[2/5] Loading success counts...", flush=True)
    success_counts = fetch_success_counts(charges, SUCCESS_CACHE)

    # 3. Build analysis
    print("\n[3/5] Building analysis...", flush=True)
    results  = build_analysis(charges, success_counts)
    machine  = [r for r in results if not r["is_filter"]]
    filt     = [r for r in results if r["is_filter"]]
    print(f"  Machine: {len(machine)}, AED {sum(r['est_debt'] for r in machine):,.0f}")
    print(f"  Filter:  {len(filt)}, AED {sum(r['est_debt'] for r in filt):,.0f}")

    # 4. Auth + preserve CRM data
    print("\n[4/5] Refreshing sheet...", flush=True)
    creds = get_sheets_creds()
    mach_crm = get_crm_map(creds, "🔧 Machine Debt")
    filt_crm = get_crm_map(creds, "🔄 Filter Debt")
    print(f"  CRM data preserved: {len(mach_crm)} machine, {len(filt_crm)} filter customers")

    # Write machine tab
    sheets_clear(creds, "🔧 Machine Debt!A2:AM2000")
    time.sleep(0.4)
    sheets_put(creds, "🔧 Machine Debt!A2", make_sheet_rows(machine, mach_crm))
    time.sleep(0.4)

    # Write filter tab
    sheets_clear(creds, "🔄 Filter Debt!A2:AM2000")
    time.sleep(0.4)
    sheets_put(creds, "🔄 Filter Debt!A2", make_sheet_rows(filt, filt_crm))
    time.sleep(0.4)

    # Update dashboard
    update_dashboard(creds, machine, filt)

    # 5. Append row to Recovery Tracker
    print("\n[5/6] Updating Recovery Tracker...", flush=True)
    mach_debt_total = round(sum(r["est_debt"] for r in machine))
    filt_debt_total = round(sum(r["est_debt"] for r in filt))
    total_debt      = mach_debt_total + filt_debt_total
    existing_dates  = sheets_get(creds, "📈 Recovery Tracker!A:A")
    today_str       = today.isoformat()
    # Avoid duplicate rows for same date
    if not any(row and row[0] == today_str for row in existing_dates):
        next_row = len(existing_dates) + 1
        sheets_put(creds, f"📈 Recovery Tracker!A{next_row}:H{next_row}", [[
            today_str,
            str(len(machine)),
            str(mach_debt_total),
            str(len(filt)),
            str(filt_debt_total),
            str(total_debt),
            "",   # Day Δ — formula set at build time per row; leave blank, sheet handles it
            f"=SUMIF('📞 Activity Log'!$A:$A,A{next_row},'📞 Activity Log'!$L:$L)",
        ]])
        # Back-fill delta formula for this row
        if next_row > 2:
            sheets_put(creds, f"📈 Recovery Tracker!G{next_row}",
                [[f"=F{next_row}-F{next_row-1}"]])
        print(f"  Appended row {next_row} for {today_str}")
    else:
        print(f"  Row for {today_str} already exists — skipping")
    time.sleep(0.3)

    # 6. Save snapshot metadata
    print("\n[6/6] Saving snapshot...", flush=True)
    snap = {
        "date":             today.isoformat(),
        "machine_customers": len(machine),
        "filter_customers":  len(filt),
        "machine_debt":      round(sum(r["est_debt"] for r in machine)),
        "filter_debt":       round(sum(r["est_debt"] for r in filt)),
        "escalate_machine":  sum(1 for r in machine if r["stage"] == "ESCALATE"),
        "escalate_filter":   sum(1 for r in filt   if r["stage"] == "ESCALATE"),
    }
    with open("scripts/last_snapshot.json", "w") as f:
        json.dump(snap, f, indent=2)

    print(f"\n✅ Refresh complete — {today.isoformat()} | Sheet + Recovery Tracker updated")
    print(f"   {len(results)} total customers | AED {snap['machine_debt'] + snap['filter_debt']:,} total debt")


if __name__ == "__main__":
    main()
