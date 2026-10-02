import type { KakaoPlace } from "./kakao-maps";
import { normalizePlace, NEARBY_RADIUS, sameStop, type NearbyPlace, type NearbyStadium } from "./nearby-places";
import { kakaoLodgingReference } from "./google-lodging";
import type { RouteStop } from "./routes";

export const KAKAO_LODGING_FILTERS = [
  { id: "all", label: "숙박 전체" },
  { id: "hotel", label: "호텔" },
  { id: "motel", label: "모텔" },
  { id: "inn", label: "여관·여인숙" },
] as const;
export type KakaoLodgingFilter = typeof KAKAO_LODGING_FILTERS[number]["id"];

export function lodgingSubtype(category: string): string {
  const parts = category.split(">").map(part => part.trim());
  const types = [
    [/호텔/, "호텔"], [/모텔/, "모텔"], [/여관|여인숙/, "여관·여인숙"],
  ] as const;
  const matched = types.filter(([pattern]) => parts.some(part => pattern.test(part)));
  if (matched.length === 2 && !matched.some(([, label]) => label === "호텔")) return "모텔·여관(통합 분류)";
  return matched.length === 1 ? matched[0][1] : matched.length > 1 || !parts.some(part => part !== "숙박" && part) ? "분류 확인 필요" : "기타 숙박";
}

export function lodgingDetailUrl(stop: RouteStop): string | undefined {
  const reference = kakaoLodgingReference(stop);
  return reference ? `https://place.map.kakao.com/${reference.id}` : undefined;
}

// Shared list entries keep the reference-only persistence boundary of lodging.
export function kakaoLodgingPlace(place: NearbyPlace, kind: KakaoLodgingFilter = "all"): NearbyPlace {
  const id = kakaoLodgingReference(place)?.id ?? place.placeId;
  if (place.kind !== "stay" || !/^[0-9]{1,100}$/.test(id)) throw new Error("숙박 검색 응답을 확인하지 못했어요.");
  return { ...place, placeId: `kakao-lodging:${kind}:${id}`, source: "KAKAO", subcategory: lodgingSubtype(place.detail) };
}

export function refreshKakaoLodgingStops(stops: RouteStop[], place: RouteStop): RouteStop[] {
  return stops.map(stop => kakaoLodgingReference(place) && sameStop(stop, place)
    ? { ...stop, name: place.name, address: place.address, lat: place.lat, lng: place.lng } : stop);
}

export async function searchKakaoLodging(stadium: NearbyStadium, kind: KakaoLodgingFilter, signal: AbortSignal, fetcher: typeof fetch = fetch): Promise<{ places: NearbyPlace[]; partial: boolean }> {
  if (!KAKAO_LODGING_FILTERS.some(filter => filter.id === kind)) throw new Error("숙박 검색 분류를 확인해 주세요.");
  const keywords = kind === "all" ? [""] : kind === "inn" ? ["여관", "여인숙"] : [kind === "hotel" ? "호텔" : "모텔"];
  const found = new Map<string, NearbyPlace>();
  let partial = false;
  for (const keyword of keywords) {
    for (let page = 1; page <= 3; page++) {
      signal.throwIfAborted();
      try {
        const response = await fetcher("/api/v1/places/lodging/", {
          method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store",
          body: JSON.stringify({ method: keyword ? "keyword" : "category", ...(keyword ? { keyword } : {}), category: "AD5", lat: stadium.lat, lng: stadium.lng, radius: NEARBY_RADIUS, page, size: 15, sort: "distance" }),
          signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]),
        });
        const body = await response.json();
        if (!response.ok) throw new Error(typeof body?.error === "string" ? body.error : "숙박 검색에 연결하지 못했어요.");
        if (!body || !Array.isArray(body.places) || body.places.length > 15 || typeof body.hasNextPage !== "boolean") throw new Error("숙박 검색 응답을 확인하지 못했어요.");
        for (const item of body.places as KakaoPlace[]) {
          if (!item || !/^[0-9]{1,100}$/.test(item.id) || item.category_group_code !== "AD5" || ![item.place_name, item.x, item.y, item.road_address_name, item.address_name].every(value => typeof value === "string")) throw new Error("숙박 검색 응답을 확인하지 못했어요.");
          const place = normalizePlace(item, stadium);
          if (!place || place.kind !== "stay") continue;
          const subtype = lodgingSubtype(item.category_name ?? "");
          const label = KAKAO_LODGING_FILTERS.find(filter => filter.id === kind)!.label;
          const sharedInnMotel = (kind === "motel" || kind === "inn") && subtype === "모텔·여관(통합 분류)";
          if (kind !== "all" && subtype !== label && !sharedInnMotel) continue;
          found.set(item.id, kakaoLodgingPlace(place, kind));
        }
        if (!body.hasNextPage) break;
      } catch (error) {
        if (signal.aborted) throw signal.reason;
        if (!found.size) throw error;
        partial = true;
        break;
      }
    }
  }
  signal.throwIfAborted();
  return { places: [...found.values()].sort((a, b) => a.distance - b.distance), partial };
}
