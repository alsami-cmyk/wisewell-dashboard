/**
 * Wisewell UAE — Shopify Web Pixel
 *
 * Add in Shopify Admin → Settings → Customer Events → Add custom pixel
 * Paste this entire file as the pixel code.
 *
 * Replace ENDPOINT with your Google Apps Script deployment URL.
 */

const ENDPOINT = "https://script.google.com/macros/s/YOUR_DEPLOYMENT_ID/exec";
const MARKET   = "UAE";

function send(event_type, data) {
  fetch(ENDPOINT, {
    method: "POST",
    body: JSON.stringify({ source: "pixel", market: MARKET, event_type, ...data }),
    keepalive: true,
  }).catch(() => {});  // fire-and-forget
}

analytics.subscribe("page_viewed", (event) => {
  send("page_viewed", {
    timestamp:  event.timestamp,
    session_id: event.clientId,
    page_path:  event.context?.document?.location?.pathname || "",
  });
});

analytics.subscribe("product_added_to_cart", (event) => {
  const line = event.data?.cartLine;
  send("product_added_to_cart", {
    timestamp:     event.timestamp,
    session_id:    event.clientId,
    product_id:    line?.merchandise?.product?.id || "",
    product_title: line?.merchandise?.product?.title || "",
    value:         line?.cost?.totalAmount?.amount || "",
    currency:      line?.cost?.totalAmount?.currencyCode || "",
  });
});

analytics.subscribe("checkout_started", (event) => {
  const co = event.data?.checkout;
  send("checkout_started", {
    timestamp:   event.timestamp,
    session_id:  event.clientId,
    checkout_id: co?.token || "",
    value:       co?.totalPrice?.amount || "",
    currency:    co?.totalPrice?.currencyCode || "",
  });
});

analytics.subscribe("checkout_completed", (event) => {
  const co = event.data?.checkout;
  send("checkout_completed", {
    timestamp:   event.timestamp,
    session_id:  event.clientId,
    order_id:    co?.order?.id || "",
    checkout_id: co?.token || "",
    value:       co?.totalPrice?.amount || "",
    currency:    co?.totalPrice?.currencyCode || "",
  });
});
