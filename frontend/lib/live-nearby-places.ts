import type { KakaoMaps } from "./kakao-maps";
import type { CategoryFilter, NearbyPlace, NearbyStadium } from "./nearby-places";
import { collectNearbyPlaces } from "./nearby-search";
import { classifyStadiumPoint } from "./stadium-search-scope";
import { kakaoLodgingPlace } from "./kakao-lodging";

// Reuse the reviewed ballpark frame, not the whole sports complex or just
// an 80 m circle. This is a candidate separation rule, not a surveyed boundary.
export function outsideReviewedStadium(place: { lat: number; lng: number }, _stadiumCode: string): boolean {
  void _stadiumCode; // Compatibility argument; expanded radii must check every reviewed venue.
  return classifyStadiumPoint(place).scope !== "internal";
}

export async function collectLiveNearbyPlaces(maps: KakaoMaps, stadium: NearbyStadium, signal: AbortSignal, preferred: () => CategoryFilter, onUpdate: (places: NearbyPlace[], completed: number, failures: number) => void, fetcher: typeof fetch = fetch) {
  return collectNearbyPlaces(maps, stadium, signal, preferred, (places, completed, failures) => {
    onUpdate(places.filter(place => classifyStadiumPoint(place).scope === "external").map(place => place.kind === "stay" ? kakaoLodgingPlace(place) : { ...place, source: "KAKAO" }), completed, failures);
  }, fetcher, true);
}
