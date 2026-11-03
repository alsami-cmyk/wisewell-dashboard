"""
fäm Properties partnership — daily sales tracker.

Runs on GitHub Actions at 01:00 UAE (21:00 UTC). Finds every UAE Shopify order
that used a fäm discount code (FAM:4 is applied automatically from
wisewell.ae/pages/fam; FAM4 is the manual fallback), appends new ones to the
fäm tracking sheet, and refreshes Recurring Price and Status for every row.

Sheet columns: Client Name | Email | Order Date | Product Title | Recurring Price | Order ID | Status
Columns are found by their header name, so they can be reordered and other
columns can be added without breaking the sync.

Rules
  • Only orders that used one of fäm's exact codes (FAM_CODES) count. Matching
    the exact codes, not the "FAM" prefix, keeps any future code that happens
    to start with FAM (e.g. FAMILY20) out of the sheet and fäm's API. If fäm
    gets a new code, add it to FAM_CODES.
  • Only the checkout order counts. Recharge renewal orders that carry the same
    code ("Subscription Recurring Order" tag) are skipped.
  • Test orders are skipped (name contains "test" or a @wisewell.com email).
  • Order ID is the dedupe key, so reruns never add duplicates.
  • Recurring Price = what the customer is charged each billing cycle once the
    free months end: Recharge subscription price × quantity, summed over every
    subscription created by that checkout (Shopify list price if Recharge has
    no record), in AED.
  • Status = Cancelled if the Shopify order was cancelled or every Recharge
    subscription created by that checkout is cancelled/expired; Active if any
    of them is active; Unknown if Recharge has no record of the checkout.
"""

import json
import os
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# ── Config ─────────────────────────────────────────────────────────────────────
SHEET_ID    = "1BtDX5amN-NVKaX8cUDnuQS49N989yfSbJpWAHpwEVc0"
TAB_GID     = 0          # tab id from the sheet URL (gid=0); survives the tab being renamed
HEADERS     = ["Client Name", "Email", "Order Date", "Product Title", "Recurring Price", "Order ID", "Status"]
PRICE_FMT   = {"type": "CURRENCY", "pattern": '"AED "#,##0.00'}
SHOP        = "wisewell-uae.myshopify.com"
SHOP_TOKEN  = os.environ["SHOPIFY_TOKEN_UAE"]
RC_KEY      = os.environ["RECHARGE_API_KEY"]
CODE_QUERY  = "discount_code:FAM*"         # broad Shopify search; is_countable() keeps exact matches only
FAM_CODES   = {"FAM:4", "FAM4"}            # the codes on wisewell.ae/pages/fam (button links / typed fallback)
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


def col_letter(i):
    return chr(ord("A") + i)


def read_rows(creds, tab):
    # Unformatted so a price shows up as 99, not "AED 99.00", and compares cleanly.
    return sheets_call(creds, "GET", f"/values/{urllib.parse.quote(a1(tab, 'A1:Z'))}",
                       params={"valueRenderOption": "UNFORMATTED_VALUE"}).get("values", [])


def format_price_column(creds, idx):
    """Show the price column as AED, including rows the append just added."""
    sheets_call(creds, "POST", ":batchUpdate", json={"requests": [{"repeatCell": {
        "range": {"sheetId": TAB_GID, "startRowIndex": 1, "startColumnIndex": idx, "endColumnIndex": idx + 1},
        "cell": {"userEnteredFormat": {"numberFormat": PRICE_FMT}},
        "fields": "userEnteredFormat.numberFormat",
    }}]})


# ── Shopify ────────────────────────────────────────────────────────────────────
ORDERS_Q = """
query($q: String!, $after: String) {
  orders(first: 100, after: $after, query: $q, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      name legacyResourceId createdAt cancelledAt email tags discountCodes
      billingAddress { name } shippingAddress { name }
      lineItems(first: 20) { edges { node {
        title variantTitle quantity sellingPlan { name }
        originalUnitPriceSet { shopMoney { amount } }
      } } }
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
    if not any((c or "").strip().upper() in FAM_CODES for c in o["discountCodes"]):
        return False
    if any(t.strip().lower() == "subscription recurring order" for t in o["tags"]):
        return False
    name = (o.get("billingAddress") or o.get("shippingAddress") or {}).get("name") or ""
    email = (o.get("email") or "").lower()
    if "test" in name.lower() or email.endswith("@wisewell.com"):
        return False
    return True


def shopify_price(o):
    """List price of the subscription lines on the order (fallback when Recharge has no record)."""
    lines = [li["node"] for li in o["lineItems"]["edges"] if li["node"].get("sellingPlan")]
    if not lines:
        return ""
    return round(sum(float(li["originalUnitPriceSet"]["shopMoney"]["amount"]) * li["quantity"] for li in lines), 2)


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


def recharge_subs(o):
    """The Recharge subscriptions created by this checkout order."""
    rc_orders = rc_get(f"/orders?external_order_id={o['legacyResourceId']}").get("orders", [])
    sub_ids = {li.get("purchase_item_id") for ro in rc_orders for li in ro.get("line_items", [])
               if li.get("purchase_item_type") in (None, "subscription") and li.get("purchase_item_id")}
    return [rc_get(f"/subscriptions/{sid}").get("subscription") or {} for sid in sub_ids]


def order_info(o):
    """(Recurring Price, Status) for a checkout order."""
    try:
        subs = recharge_subs(o)
    except Exception as exc:
        print(f"  [recharge] {o['name']}: {exc}", flush=True)
        subs = []

    if subs:
        price = round(sum(float(s.get("price") or 0) * int(s.get("quantity") or 1) for s in subs), 2)
    else:
        price = shopify_price(o)

    statuses = [(s.get("status") or "").lower() for s in subs]
    if o.get("cancelledAt"):
        status = "Cancelled"
    elif "active" in statuses:
        status = "Active"
    elif statuses and all(s in ("cancelled", "expired") for s in statuses):
        status = "Cancelled"
    else:
        status = "Unknown"
    return price, status


# ── Row building ───────────────────────────────────────────────────────────────
def to_fields(o, price, status):
    created = datetime.fromisoformat(o["createdAt"].replace("Z", "+00:00")).astimezone(UAE)
    products = "; ".join(
        f"{li['node']['title']} – {li['node']['variantTitle']}" if li["node"].get("variantTitle") else li["node"]["title"]
        for li in o["lineItems"]["edges"]
    )
    name = (o.get("billingAddress") or o.get("shippingAddress") or {}).get("name") or ""
    return {
        "Client Name": name,
        "Email": (o.get("email") or "").lower(),
        "Order Date": created.strftime("%Y-%m-%d"),
        "Product Title": products,
        "Recurring Price": price,
        "Order ID": o["name"],
        "Status": status,
    }


def main():
    creds = get_sheets_creds()
    tab = tab_title(creds)
    rows = read_rows(creds, tab)
    header = [str(h).strip() for h in rows[0]] if rows else []
    if not any(header):
        sheets_call(creds, "PUT", f"/values/{urllib.parse.quote(a1(tab, 'A1'))}",
                    params={"valueInputOption": "RAW"}, json={"values": [HEADERS]})
        header, rows = list(HEADERS), [HEADERS]
    missing = [h for h in HEADERS if h not in header]
    if missing:
        raise RuntimeError(f"Tab '{tab}' has no column(s) {missing}. Header row is {header}")
    col = {h: header.index(h) for h in HEADERS}

    def cell(row, h):
        return row[col[h]] if len(row) > col[h] else ""

    existing = {str(cell(r, "Order ID")).strip(): i + 1 for i, r in enumerate(rows)
                if i > 0 and str(cell(r, "Order ID")).strip()}

    orders = sorted((o for o in fetch_fam_orders() if is_countable(o)), key=lambda o: o["createdAt"])
    print(f"fäm orders found: {len(orders)} (already in sheet: {sum(o['name'] in existing for o in orders)})", flush=True)

    new_rows, updates = [], []
    for o in orders:
        price, status = order_info(o)
        if o["name"] in existing:
            row_no = existing[o["name"]]
            for h, value in (("Recurring Price", price), ("Status", status)):
                if cell(rows[row_no - 1], h) != value:
                    updates.append({"range": a1(tab, f"{col_letter(col[h])}{row_no}"), "values": [[value]]})
        else:
            fields = to_fields(o, price, status)
            row = [""] * len(header)
            for h, value in fields.items():
                row[col[h]] = value
            new_rows.append(row)

    if updates:
        sheets_call(creds, "POST", "/values:batchUpdate",
                    json={"valueInputOption": "RAW", "data": updates})
    if new_rows:
        sheets_call(creds, "POST", f"/values/{urllib.parse.quote(a1(tab, 'A:' + col_letter(len(header) - 1)))}:append",
                    params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                    json={"values": new_rows})
    if updates or new_rows:
        format_price_column(creds, col["Recurring Price"])

    print(f"added {len(new_rows)} new row(s), updated {len(updates)} cell(s)", flush=True)
    for r in new_rows:
        print(f"  + {cell(r, 'Order ID')}  {cell(r, 'Order Date')}  {cell(r, 'Product Title')}  "
              f"AED {cell(r, 'Recurring Price')}  [{cell(r, 'Status')}]", flush=True)


if __name__ == "__main__":
    main()
