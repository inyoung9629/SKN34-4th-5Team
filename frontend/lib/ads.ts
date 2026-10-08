import type { components } from "./api/schema";

export type AdCreative = components["schemas"]["AdPublic"];
export type AdResponse = components["schemas"]["AdSlotResponse"];
export type AdPlacement = "home-club-banner" | "home-route-partner-banner";
export type AdMode = "mock" | "api" | "off";
export type AdDelivery = AdResponse & { preview: boolean };
export type AdQuery = { placement: AdPlacement; routeIds: string[] };

export function parseAdMode(value: string | undefined): AdMode {
  if (value === undefined) return "mock";
  return value === "mock" || value === "api" ? value : "off";
}

export const AD_MODE = parseAdMode(process.env.NEXT_PUBLIC_AD_MODE);

export function safeAdUrl(value: string): string | null {
  const text = value.trim();
  if (!text || /[\u0000-\u0020\\]/.test(text)) return null;
  if (text.startsWith("/") && !text.startsWith("//")) return text;
  try {
    const url = new URL(text);
    return url.protocol === "https:" && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

export function isActiveAd(ad: AdCreative, placement: AdPlacement, now: number): boolean {
  const start = Date.parse(ad.starts_at), end = Date.parse(ad.ends_at);
  return ad.active && ad.placement === placement && Boolean(ad.title.trim()) && Boolean(safeAdUrl(ad.destination_url)) && (!ad.image_url || Boolean(safeAdUrl(ad.image_url))) && Number.isFinite(start) && Number.isFinite(end) && start <= now && now < end;
}

export function deliveryLifetime(delivery: AdDelivery): number {
  if (!delivery.ad || !delivery.valid_until) return 0;
  const remaining = Math.min(Date.parse(delivery.valid_until), Date.parse(delivery.ad.ends_at)) - Date.parse(delivery.server_now);
  return Number.isFinite(remaining) ? Math.max(0, remaining) : 0;
}
