import type { RouteStop } from "./routes";

export const GOOGLE_LODGING_PREFIX = "google-ui-kit:";
export const GOOGLE_LODGING_NAME = "선택한 숙소";
export const LODGING_CATEGORIES = [
  { id: "hotel", label: "호텔" },
  { id: "motel", label: "모텔" },
  { id: "inn", label: "여관" },
] as const;
export type LodgingCategory = typeof LODGING_CATEGORIES[number]["id"];
export const LODGING_SEARCH_LIMIT = 20;
export const LODGING_RADIUS_METERS = 2500;

export function lodgingResultMessage(kind: LodgingCategory, count: number): string {
  const label = LODGING_CATEGORIES.find(category => category.id === kind)!.label;
  if (count === 0) return `${label} 검색 결과가 없어요. 실제로 숙소가 없는 뜻은 아니며, 다른 분류도 확인해 보세요.`;
  return `${label} ${count}곳 · 가까운 순${count >= LODGING_SEARCH_LIMIT ? " · 검색 한도에 도달해 일부 숙소가 빠질 수 있어요." : ""}`;
}
export function googleLodgingId(stop: { placeId?: string }): string | null {
  const id = stop.placeId?.startsWith(GOOGLE_LODGING_PREFIX) ? stop.placeId.slice(GOOGLE_LODGING_PREFIX.length) : "";
  return /^[A-Za-z0-9_-]{1,220}$/.test(id) ? id : null;
}

// Preserve the user's search filter with the ID, not the provider's subtype.
export function kakaoLodgingReference(stop: { placeId?: string }): { kind: "all" | "hotel" | "motel" | "inn"; id: string } | null {
  const match = /^kakao-lodging:(all|hotel|motel|inn):([0-9]{1,100})$/.exec(stop.placeId ?? "");
  return match ? { kind: match[1] as "all" | "hotel" | "motel" | "inn", id: match[2] } : null;
}

export function isLodgingReference(stop: { placeId?: string }): boolean {
  return Boolean(googleLodgingId(stop) || kakaoLodgingReference(stop));
}

// Google names, addresses and types stay inside the official widget. The label
// below is ours, not a classification inferred from a search filter or a name.
export function googleLodgingStop(id: string, lat = NaN, lng = NaN): RouteStop {
  const stop = { placeId: `${GOOGLE_LODGING_PREFIX}${id}`, name: GOOGLE_LODGING_NAME, category: "숙박", lat, lng };
  if (!googleLodgingId(stop)) throw new Error("유효하지 않은 Google 장소 ID");
  return stop;
}

// NaN means unresolved in memory (never 0,0 or the stadium). JSON writes null.
// Every durable boundary uses this allowlist; coordinates are session-only.
export function referenceOnlyStop(stop: RouteStop): RouteStop {
  const id = googleLodgingId(stop);
  if (id) return { ...googleLodgingStop(id), ...(stop.visitId ? { visitId: stop.visitId } : {}) };
  if (kakaoLodgingReference(stop)) return { placeId: stop.placeId, name: "선택한 숙소", category: "숙박", lat: NaN, lng: NaN, ...(stop.visitId ? { visitId: stop.visitId } : {}) };
  return stop;
}

export function locatedStop(stop: { lat: number; lng: number }): boolean {
  return Number.isFinite(stop.lat) && Number.isFinite(stop.lng) && Math.abs(stop.lat) <= 90 && Math.abs(stop.lng) <= 180;
}

export function canRequestDirections(stops: RouteStop[]): boolean {
  // The current directions service persists request coordinates indefinitely.
  return stops.every(stop => !isLodgingReference(stop) && locatedStop(stop));
}
