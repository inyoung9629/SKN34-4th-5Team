import type { KakaoMaps, KakaoPlace } from "./kakao-maps";
import { NEARBY_RADIUS, NEARBY_SEARCHES, normalizePlace, type CategoryFilter, type NearbyPlace, type NearbyStadium, type SearchSpec } from "./nearby-places";

type SearchPage = { places: KakaoPlace[]; hasNextPage: boolean; syncedAt?: string; totalCount?: number; pageableCount?: number };
type SearchArea = { lat: number; lng: number; radius: number };

function quadrantAreas(stadium: NearbyStadium): SearchArea[] {
  const offset = NEARBY_RADIUS / 2;
  const latStep = offset / 111195;
  const lngStep = latStep / Math.cos(stadium.lat * Math.PI / 180);
  // Four overlapping circles cover the original circle. Only normalizePlace's
  // distance from the actual stadium decides whether a result enters the map.
  return [-1, 1].flatMap(north => [-1, 1].map(east => ({
    lat: stadium.lat + north * latStep, lng: stadium.lng + east * lngStep,
    radius: Math.ceil(offset * Math.SQRT2 + 30),
  })));
}

type PlaceRequest = { method: "keyword" | "category"; keyword?: string; category?: string; lat: number; lng: number; radius?: number; page: number; size: number; sort: "accuracy" | "distance" };
export async function searchKakaoPlaces(query: PlaceRequest, signal: AbortSignal, fetcher: typeof fetch = fetch): Promise<SearchPage> {
  return requestPlaces("/api/v1/places/search/", query, signal, fetcher);
}

async function requestPlaces(endpoint: string, query: PlaceRequest, signal: AbortSignal, fetcher: typeof fetch): Promise<SearchPage> {
  signal.throwIfAborted();
  const response = await fetcher(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(query), cache: "no-store", signal: AbortSignal.any([signal, AbortSignal.timeout(8000)]) });
  let body: unknown;
  try { body = await response.json(); } catch { throw new Error("장소 검색 응답을 확인하지 못했어요."); }
  if (!response.ok) throw new Error(body && typeof body === "object" && typeof (body as { error?: unknown }).error === "string" ? (body as { error: string }).error : "일부 장소를 불러오지 못했어요.");
  if (!body || typeof body !== "object" || !Array.isArray((body as SearchPage).places) || typeof (body as SearchPage).hasNextPage !== "boolean") throw new Error("장소 검색 응답을 확인하지 못했어요.");
  return body as SearchPage;
}

export async function searchPage(_maps: KakaoMaps, stadium: NearbyStadium, spec: SearchSpec, page: number, signal: AbortSignal, fetcher: typeof fetch = fetch, live = false, area: SearchArea = { lat: stadium.lat, lng: stadium.lng, radius: NEARBY_RADIUS }): Promise<SearchPage> {
  const endpoint = live ? (spec.kind === "stay" ? "/api/v1/places/lodging/" : "/api/v1/places/live/") : "/api/v1/places/search/";
  return requestPlaces(endpoint, { method: spec.method, ...(spec.method === "keyword" ? { keyword: spec.query, ...(spec.group ? { category: spec.group } : {}) } : { category: spec.query }), ...area, size: 15, page, sort: spec.accuracy ? "accuracy" : "distance" }, signal, fetcher);
}

// Address geocodes may point at the entire sports complex (not the ballpark).
// Resolve the actual baseball venue before setting the search radius.
export async function resolveStadium(maps: KakaoMaps, stadium: NearbyStadium, signal: AbortSignal, fetcher: typeof fetch = fetch): Promise<NearbyStadium> {
  const result = await searchPage(maps, stadium, { kind: "sight", method: "keyword", query: stadium.name, accuracy: true }, 1, signal, fetcher);
  const compact = (value: string) => value.replace(/[\s-]/g, "").toLowerCase().replace(/kia/g, "기아");
  const venue = result.places.find((place) => /야구장/.test(place.category_name ?? "") && compact(place.place_name).includes(compact(stadium.name).replace(/^인천/, "")));
  if (!venue || !venue.x.trim() || !venue.y.trim() || !Number.isFinite(Number(venue.x)) || !Number.isFinite(Number(venue.y))) throw new Error("구장 위치를 확인하지 못했어요. 다시 불러와 주세요.");
  return { ...stadium, lat: Number(venue.y), lng: Number(venue.x), address: venue.road_address_name || stadium.address };
}

export async function collectNearbyPlaces(maps: KakaoMaps, stadium: NearbyStadium, signal: AbortSignal, preferred: () => CategoryFilter, onUpdate: (places: NearbyPlace[], completed: number, failures: number) => void, fetcher: typeof fetch = fetch, live = false) {
  signal.throwIfAborted();
  // All categories share one bounded queue, including read-only AD5 lodging.
  const area = { lat: stadium.lat, lng: stadium.lng, radius: NEARBY_RADIUS };
  const queue = NEARBY_SEARCHES.map((spec, order) => ({ spec, order, page: 1, area, depth: 0 }));
  const expanded = new Set<number>();
  let completed = 0, failures = 0;
  async function worker() {
    while (queue.length && !signal.aborted) {
      const category = preferred();
      queue.sort((a, b) => {
        const priority = (job: typeof a) => category === job.spec.kind ? -1 : job.spec.kind === "stay" ? 2 : job.spec.kind === "store" ? 1 : 0;
        return priority(a) - priority(b) || a.depth - b.depth || a.page - b.page || a.order - b.order;
      });
      const job = queue.shift()!;
      try {
        const result = await searchPage(maps, stadium, job.spec, job.page, signal, fetcher, live, job.area);
        signal.throwIfAborted();
        const normalized = result.places.map((place) => normalizePlace(place, stadium)).filter((place): place is NearbyPlace => place !== null);
        const truncated = typeof result.totalCount === "number" && typeof result.pageableCount === "number"
          ? result.totalCount > result.pageableCount
          : job.page === 3 && (result.hasNextPage || result.places.length === 15);
        if (live && job.depth === 0 && job.spec.method === "category" && truncated && !expanded.has(job.order)) {
          expanded.add(job.order);
          for (const area of quadrantAreas(stadium)) queue.push({ ...job, area, page: 1, depth: 1 });
        }
        if (result.hasNextPage && job.page < 3) queue.push({ ...job, page: job.page + 1 });
        else completed++;
        onUpdate(normalized, completed, failures);
      } catch (error) {
        if (signal.aborted) throw error;
        failures++; completed++; onUpdate([], completed, failures);
      }
    }
  }
  await Promise.all([worker(), worker()]);
  return { completed, failures };
}
