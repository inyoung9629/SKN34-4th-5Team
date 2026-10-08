import { apiRequest } from "./client";
import { AD_MODE, deliveryLifetime, isActiveAd, type AdDelivery, type AdQuery, type AdResponse } from "../ads";
import { getMockDelivery } from "../ad-mocks";

export async function loadHomeAd(query: AdQuery, signal: AbortSignal): Promise<AdDelivery | null> {
  signal.throwIfAborted();
  if (AD_MODE === "off") return null;
  let delivery: AdDelivery;
  if (AD_MODE === "mock") {
    delivery = getMockDelivery(query.placement);
  } else {
    const params = new URLSearchParams({ placement: query.placement });
    for (const id of [...new Set(query.routeIds)].slice(0, 3)) params.append("route_ids", id);
    const response = await apiRequest<AdResponse>(`/api/v1/ads/slots/?${params}`, { signal, cache: "no-store" });
    if (!response) return null;
    delivery = { ...response, preview: false };
  }
  signal.throwIfAborted();
  const now = Date.parse(delivery.server_now);
  if (!Number.isFinite(now) || !delivery.ad || !isActiveAd(delivery.ad, query.placement, now) || deliveryLifetime(delivery) <= 0) return null;
  if (!delivery.preview && (!delivery.token || !delivery.exposure_id)) return null;
  return delivery;
}

export async function trackHomeAd(delivery: AdDelivery, kind: "impression" | "click"): Promise<void> {
  if (AD_MODE !== "api" || delivery.preview || !delivery.ad || !delivery.token) return;
  await apiRequest("/api/v1/ads/events/", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event_id: crypto.randomUUID(), token: delivery.token, kind }),
    keepalive: true, signal: AbortSignal.timeout(5000),
  });
}
