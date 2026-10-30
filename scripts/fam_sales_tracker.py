"""
fäm Properties partnership — daily sales tracker.

Runs on GitHub Actions at 09:00 UAE (05:00 UTC). Finds every UAE Shopify order
that used a fäm discount code (FAM:4 is applied automatically from
wisewell.ae/pages/fam; FAM4 is the manual fallback), appends new ones to the
fäm tracking sheet, and refreshes the Status column for every row.

Sheet columns: Client Name | Email | Order Date | Product Title | Order ID | Status

Rules
  • Only the checkout order counts. Recharge renewal orders that carry the same
    code ("Subscription Recurring Order" tag) are skipped.
  • Test orders are skipped (name contains "test" or a @wisewell.com email).
  • Order ID is the dedupe key, so reruns never add duplicates.
  • Status = Cancelled if the Shopify order was cancelled or every Recharge
    subscription created by that checkout is cancelled/expired; Active if any
    of them is active; Unknown if Recharge has no record of the checkout.
"""

import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# ── Config ─────────────────────────────────────────────────────────────────────
SHEET_ID    = "1BtDX5amN-NVKaX8cUDnuQS49N989yfSbJpWAHpwEVc0"
TAB_GID     = 0          # tab id from the sheet URL (gid=0); survives the tab being renamed
HEADERS     = ["Client Name", "Email", "Order Date", "Product Title", "Order ID", "Status"]
SHOP        = "wisewell-uae.myshopify.com"
SHOP_TOKEN  = os.environ["SHOPIFY_TOKEN_UAE"]
RC_KEY      = os.environ["RECHARGE_API_KEY"]
CODE_QUERY  = "discount_code:FAM*"
CODE_RE     = re.compile(r"^FAM", re.I)
UAE         = timezone(timedelta(hours=4))

RC_HEADERS = {"X-Recharge-Access-Token": RC_KEY, "X-Recharge-Version": "2021-11"}


# ── Google Sheets ──────────────────────────────────────────────────────────────
def get_sheets_creds():
    td = json.loads(os.environ["GOOGLE_TOKEN_JSON"])
    creds = Credentials(
        token=td.get("token"),
        refresh_token=td["refresh_token"],
        token_uri=td["token_uri"],
        client_id=td["client_id"],
        client_secret=td["client_secret"],
        scopes=td.get("scopes", ["https://www.googleapis.com/auth/spreadsheets"]),
    )
    creds.refresh(Request())
    return creds


def sheets_call(creds, method, path, **kw):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}{path}"
    r = requests.request(method, url, headers={"Authorization": f"Bearer {creds.token}"}, timeout=30, **kw)
    r.raise_for_status()
    return r.json()


def tab_title(creds):
    """Current name of the tracking tab, looked up by its permanent id."""
    meta = sheets_call(creds, "GET", "", params={"fields": "sheets.properties(sheetId,title)"})
    for sh in meta.get("sheets", []):
        if sh["properties"]["sheetId"] == TAB_GID:
            return sh["properties"]["title"]
    raise RuntimeError(f"No tab with gid={TAB_GID} in spreadsheet {SHEET_ID}")


def a1(tab, rng):
    return "'" + tab.replace("'", "''") + "'!" + rng


def read_rows(creds, tab):
    return sheets_call(creds, "GET", f"/values/{urllib.parse.quote(a1(tab, 'A1:F'))}").get("values", [])


# ── Shopify ────────────────────────────────────────────────────────────────────
ORDERS_Q = """
query($q: String!, $after: String) {
  orders(first: 100, after: $after, query: $q, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      name legacyResourceId createdAt cancelledAt email tags discountCodes
      billingAddress { name } shippingAddress { name }
      lineItems(first: 20) { edges { node { title variantTitle } } }
    } }
  }
}"""


def shopify_gql(query, variables):
    for attempt in range(3):
        r = requests.post(
            f"https://{SHOP}/admin/api/2024-10/graphql.json",
            json={"query": query, "variables": variables},
            headers={"X-Shopify-Access-Token": SHOP_TOKEN},
            timeout=30,
        )
        if r.status_code == 429 and attempt < 2:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            raise RuntimeError(body["errors"])
        return body["data"]


def fetch_fam_orders():
    orders, after = [], None
    while True:
        page = shopify_gql(ORDERS_Q, {"q": CODE_QUERY, "after": after})["orders"]
        orders += [e["node"] for e in page["edges"]]
        if not page["pageInfo"]["hasNextPage"]:
            return orders
        after = page["pageInfo"]["endCursor"]


def is_countable(o):
    if not any(CODE_RE.match(c or "") for c in o["discountCodes"]):
        return False
    if any(t.strip().lower() == "subscription recurring order" for t in o["tags"]):
        return False
    name = (o.get("billingAddress") or o.get("shippingAddress") or {}).get("name") or ""
    email = (o.get("email") or "").lower()
    if "test" in name.lower() or email.endswith("@wisewell.com"):
        return False
    return True


# ── Recharge ───────────────────────────────────────────────────────────────────
def rc_get(path):
    for attempt in range(3):
        try:
            r = requests.get(f"https://api.rechargeapps.com{path}", headers=RC_HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def order_status(o):
    if o.get("cancelledAt"):
        return "Cancelled"
    try:
        rc_orders = rc_get(f"/orders?external_order_id={o['legacyResourceId']}").get("orders", [])
        sub_ids = {li.get("purchase_item_id") for ro in rc_orders for li in ro.get("line_items", [])
                   if li.get("purchase_item_type") in (None, "subscription") and li.get("purchase_item_id")}
        statuses = [(rc_get(f"/subscriptions/{sid}").get("subscription") or {}).get("status", "").lower()
                    for sid in sub_ids]
    except Exception as exc:
        print(f"  [recharge] {o['name']}: {exc}", flush=True)
        return "Unknown"
    if "active" in statuses:
        return "Active"
    if statuses and all(s in ("cancelled", "expired") for s in statuses):
        return "Cancelled"
    return "Unknown"


# ── Row building ───────────────────────────────────────────────────────────────
def to_row(o, status):
    created = datetime.fromisoformat(o["createdAt"].replace("Z", "+00:00")).astimezone(UAE)
    products = "; ".join(
        f"{li['node']['title']} – {li['node']['variantTitle']}" if li["node"].get("variantTitle") else li["node"]["title"]
        for li in o["lineItems"]["edges"]
    )
    name = (o.get("billingAddress") or o.get("shippingAddress") or {}).get("name") or ""
    return [name, (o.get("email") or "").lower(), created.strftime("%Y-%m-%d"), products, o["name"], status]


def main():
    creds = get_sheets_creds()
    tab = tab_title(creds)
    rows = read_rows(creds, tab)
    if not rows or rows[0][:len(HEADERS)] != HEADERS:
        sheets_call(creds, "PUT", f"/values/{urllib.parse.quote(a1(tab, 'A1:F1'))}",
                    params={"valueInputOption": "RAW"}, json={"values": [HEADERS]})
        rows = [HEADERS] + rows[1:]
    existing = {r[4].strip(): i + 1 for i, r in enumerate(rows) if i > 0 and len(r) > 4 and r[4].strip()}

    orders = sorted((o for o in fetch_fam_orders() if is_countable(o)), key=lambda o: o["createdAt"])
    print(f"fäm orders found: {len(orders)} (already in sheet: {sum(o['name'] in existing for o in orders)})", flush=True)

    new_rows, status_updates = [], []
    for o in orders:
        status = order_status(o)
        if o["name"] in existing:
            row_no = existing[o["name"]]
            current = rows[row_no - 1][5] if len(rows[row_no - 1]) > 5 else ""
            if current != status:
                status_updates.append({"range": a1(tab, f"F{row_no}"), "values": [[status]]})
        else:
            new_rows.append(to_row(o, status))

    if status_updates:
        sheets_call(creds, "POST", "/values:batchUpdate",
                    json={"valueInputOption": "RAW", "data": status_updates})
    if new_rows:
        sheets_call(creds, "POST", f"/values/{urllib.parse.quote(a1(tab, 'A:F'))}:append",
                    params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                    json={"values": new_rows})

    print(f"added {len(new_rows)} new row(s), updated {len(status_updates)} status(es)", flush=True)
    for r in new_rows:
        print(f"  + {r[4]}  {r[2]}  {r[3]}  [{r[5]}]", flush=True)


if __name__ == "__main__":
    main()
