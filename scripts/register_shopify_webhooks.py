"""
Registers Shopify webhooks for checkout and order events.
These fire server-side (100% reliable, no JS required).

Usage: python3 scripts/register_shopify_webhooks.py
"""
import requests

ENDPOINT = "https://script.google.com/macros/s/AKfycbz4Pk2bYDiY3fWU_7pJ4nITF_V7APq16xOX-nFgQRDHkuQ6wWWpQoWjuZyMpvza2mq5/exec"

STORES = {
    "UAE": ("wisewell-uae.myshopify.com", "SHOPIFY_TOKEN_UAE"),
    # "KSA": ("YOUR-KSA-STORE.myshopify.com", "SHOPIFY_TOKEN_KSA"),
    # "USA": ("sebastien-566.myshopify.com",   "SHOPIFY_TOKEN_USA"),
}

TOPICS = [
    "checkouts/create",   # someone reaches checkout
    "checkouts/update",   # checkout updated (payment info entered etc.)
    "orders/create",      # purchase completed
]


def list_webhooks(store, token):
    r = requests.get(
        f"https://{store}/admin/api/2025-04/webhooks.json",
        headers={"X-Shopify-Access-Token": token},
        timeout=15,
    )
    return r.json().get("webhooks", [])


def register_webhook(store, token, topic, endpoint):
    r = requests.post(
        f"https://{store}/admin/api/2025-04/webhooks.json",
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        json={"webhook": {"topic": topic, "address": endpoint, "format": "json"}},
        timeout=15,
    )
    return r.json()


for market, (store, token) in STORES.items():
    print(f"\n{'='*55}")
    print(f"  {market} — {store}")
    print("="*55)

    existing = list_webhooks(store, token)
    existing_topics = {w["topic"] for w in existing}
    print(f"  Existing webhooks: {existing_topics or 'none'}")

    for topic in TOPICS:
        if topic in existing_topics:
            print(f"  ✓ Already registered: {topic}")
            continue
        result = register_webhook(store, token, topic, ENDPOINT)
        if "webhook" in result:
            wh = result["webhook"]
            print(f"  ✅ Registered: {topic} → id={wh['id']}")
        else:
            print(f"  ✗ Failed {topic}: {result}")

print("\nDone. Shopify will now POST to your Apps Script endpoint on each event.")
