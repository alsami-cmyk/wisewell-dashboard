"""
Shopify OAuth token capture.
Usage: python3 scripts/shopify_oauth.py
"""
import http.server
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser

import requests

CLIENT_ID     = "8d4a1a7c30ca4ef07a55d9bc0e874b24"
CLIENT_SECRET = "SHOPIFY_USA_CLIENT_SECRET"  # set before running
SCOPES        = "read_orders,read_analytics,read_checkouts"
REDIRECT_URI  = "http://localhost:3001/callback"
PORT          = 3001

STORES = {
    "USA": "sebastien-566.myshopify.com",
}

captured = {}


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        captured.update(params)
        print(f"\n[callback received] params: {json.dumps(params, indent=2)}")

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Authorization received. You can close this tab.</h2>")

    def log_message(self, *args):
        pass


server = http.server.HTTPServer(("localhost", PORT), CallbackHandler)
thread = threading.Thread(target=server.serve_forever)
thread.daemon = True
thread.start()
print(f"Listening on http://localhost:{PORT} ...")

for market, shop in STORES.items():
    print(f"\n{'='*55}")
    print(f"  {market}  —  {shop}")
    print("="*55)

    captured.clear()
    state = secrets.token_hex(16)

    auth_url = (
        f"https://{shop}/admin/oauth/authorize"
        f"?client_id={CLIENT_ID}"
        f"&scope={SCOPES}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI, safe='')}"
        f"&state={state}"
    )

    print(f"Opening browser for {market}...")
    webbrowser.open(auth_url)
    print("Waiting up to 120s for callback...")

    for _ in range(240):
        if "code" in captured:
            break
        time.sleep(0.5)

    if "code" not in captured:
        print(f"TIMEOUT — no code received for {market}.")
        continue

    print(f"\nState sent    : {state}")
    print(f"State received: {captured.get('state')}")

    code = captured["code"]
    print(f"Code          : {code[:10]}...")

    print(f"\nExchanging code for access token...")
    try:
        resp = requests.post(
            f"https://{shop}/admin/oauth/access_token",
            json={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "code": code},
            timeout=15,
        )
        print(f"Response status : {resp.status_code}")
        print(f"Response body   : {resp.text}")
        resp.raise_for_status()
        data  = resp.json()
        token = data.get("access_token", "")
        print(f"\n✅  {market} ACCESS TOKEN: {token}")
        print(f"\nAdd to Streamlit secrets:")
        print(f'  SHOPIFY_STORE_{market} = "{shop}"')
        print(f'  SHOPIFY_TOKEN_{market} = "{token}"')

        # Save to file so it's not lost
        out_path = f"/Users/sami/Desktop/Claude Code/.shopify_{market.lower()}_token.txt"
        with open(out_path, "w") as f:
            f.write(f"SHOPIFY_STORE_{market} = \"{shop}\"\n")
            f.write(f"SHOPIFY_TOKEN_{market} = \"{token}\"\n")
        print(f"\nAlso saved to: {out_path}")

    except Exception as e:
        print(f"\n✗ Token exchange failed: {e}")

server.shutdown()
print("\nDone.")
