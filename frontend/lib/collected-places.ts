import { sameStop, type NearbyPlace } from "./nearby-places";
import type { RouteStop } from "./routes";

export type CollectedPlaces = {
  status: "ok"; snapshotId: string; stadium: string; radiusM: number;
  places: NearbyPlace[]; count: number; warning: string;
  lodging: { status: "details_unavailable"; message: string };
};

const cache = new Map<string, { expires: number; value: CollectedPlaces }>();
const pending = new Map<string, Promise<CollectedPlaces>>();

export function collectedSource(place: RouteStop): string {
  const source = place.placeId?.split(":");
  return source?.[0] === "collected" ? ({ SBIZ: "소상공인 상가정보", PARK: "전국도시공원", TOUR: "한국관광공사" }[source[1]] ?? "수집 데이터") : "";
}

// Unselected nearby places are list-only; never create thousands of overlays.
export function selectedPlacePins(places: NearbyPlace[], selected: RouteStop | null, stops: RouteStop[]): NearbyPlace[] {
  if (!selected || stops.some(stop => sameStop(stop, selected))) return [];
  const place = places.find(item => sameStop(item, selected));
  return place ? [place] : [];
}

function parseCatalogue(value: unknown, stadium: string): CollectedPlaces {
  const data = value as CollectedPlaces | null;
  if (!data || data.status !== "ok" || data.stadium !== stadium || typeof data.snapshotId !== "string" || !Array.isArray(data.places) || data.count !== data.places.length || data.radiusM !== 2500 || typeof data.warning !== "string" || data.lodging?.status !== "details_unavailable" || typeof data.lodging.message !== "string") throw new Error("수집 장소 응답을 확인하지 못했어요.");
  const ids = new Set<string>();
  for (const place of data.places) {
    if (!place || typeof place.placeId !== "string" || !/^collected:(SBIZ|PARK|TOUR):.+$/.test(place.placeId) || ids.has(place.placeId) || typeof place.name !== "string" || !Number.isFinite(place.lat) || !Number.isFinite(place.lng) || Math.abs(place.lat) > 90 || Math.abs(place.lng) > 180 || !["food", "cafe", "walk", "indoor", "store"].includes(place.kind) || !["category", "address", "phone", "detail", "cuisine"].every(key => typeof place[key as keyof NearbyPlace] === "string") || !Number.isFinite(place.distance)) throw new Error("수집 장소 응답을 확인하지 못했어요.");
    ids.add(place.placeId);
  }
  return data;
}

export async function fetchCollectedPlaces(stadium: string, signal: AbortSignal, fetcher: typeof fetch = fetch): Promise<CollectedPlaces> {
  signal.throwIfAborted();
  if (!/^[A-Z0-9_]{1,30}$/.test(stadium)) throw new Error("구장 코드를 확인해 주세요.");
  const stored = cache.get(stadium);
  if (stored && stored.expires > Date.now()) return stored.value;
  let request = pending.get(stadium);
  if (!request) {
    // Share a bounded request across remounts. Each caller independently ignores
    // its result after abort, so changing stadium cannot overwrite the new list.
    request = (async () => {
      const response = await fetcher(`/api/v1/places/collected/?stadium=${encodeURIComponent(stadium)}`, { signal: AbortSignal.timeout(15_000) });
      if (!response.ok) throw new Error("수집 장소 데이터를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.");
      const value = parseCatalogue(await response.json(), stadium);
      if (cache.size >= 9) cache.delete(cache.keys().next().value!);
      cache.set(stadium, { expires: Date.now() + 300_000, value });
      return value;
    })().finally(() => pending.delete(stadium));
    pending.set(stadium, request);
  }
  const result = await request;
  signal.throwIfAborted();
  return result;
}
