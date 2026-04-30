/**
 * Wisewell UAE — Shopify Web Pixel
 *
 * Add in Shopify Admin → Settings → Customer Events → Add custom pixel
 * Paste this entire file as the pixel code.
 */

const ENDPOINT = "https://script.google.com/macros/s/AKfycbz4Pk2bYDiY3fWU_7pJ4nITF_V7APq16xOX-nFgQRDHkuQ6wWWpQoWjuZyMpvza2mq5/exec";
const MARKET   = "UAE";

function send(event_type, data) {
  fetch(ENDPOINT, {
    method: "POST",
    body: JSON.stringify({ source: "pixel", market: MARKET, event_type, ...data }),
    keepalive: true,
  }).catch(() => {});
}

function getAttribution(event) {
  const search   = event.context?.document?.location?.search || "";
  const referrer = event.context?.document?.referrer || "";
  const params   = {};
  search.replace(/^\?/, "").split("&").forEach(kv => {
    const [k, v] = kv.split("=").map(s => { try { return decodeURIComponent(s || ""); } catch (e) { return s || ""; } });
    if (k) params[k] = v || "";
  });
  return {
    referrer:     referrer,
    utm_source:   params.utm_source   || "",
    utm_medium:   params.utm_medium   || "",
    utm_campaign: params.utm_campaign || "",
    utm_content:  params.utm_content  || "",
    fbclid:       params.fbclid       || "",
    gclid:        params.gclid        || "",
  };
}

analytics.subscribe("page_viewed", (event) => {
  const attr = getAttribution(event);
  send("page_viewed", {
    timestamp:  event.timestamp,
    session_id: event.clientId,
    page_path:  event.context?.document?.location?.pathname || "",
    ...attr,
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
