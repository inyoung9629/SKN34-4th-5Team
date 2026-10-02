import type { KakaoPlace } from "./kakao-maps";
import type { Stadium } from "./stadiums";
import type { RouteStop } from "./routes";

export const NEARBY_RADIUS = 2500;
export const MAX_ROUTE_STOPS = 12;
export const PLACE_CATEGORIES = [
  { id: "food", label: "먹거리", color: "#d55d39", icon: "M5 3v6m3-6v6M3 3v4a4 4 0 0 0 8 0V3M7 11v10M18 3v18m0-18c-4 3-4 9 0 9" },
  { id: "cafe", label: "카페·디저트", color: "#95623f", icon: "M4 8h12v8a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4V8Zm12 1h2a3 3 0 1 1 0 6h-2M6 3v2m4-2v2m4-2v2" },
  { id: "walk", label: "산책", color: "#278469", icon: "m12 2-6 7h3l-5 7h16l-5-7h3l-6-7Zm0 14v6" },
  { id: "sight", label: "관광 명소", color: "#3978c4", icon: "M3 7h5l2-3h4l2 3h5v13H3V7Zm13 6a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z" },
  { id: "indoor", label: "실내 놀거리", color: "#8054b5", icon: "M7 7h10c3 0 4 4 4 9 0 4-4 3-6-1H9c-2 4-6 5-6 1 0-5 1-9 4-9Zm0 3v4m-2-2h4m7-1h.1m2 3h.1" },
  { id: "store", label: "편의점", color: "#358597", icon: "M4 3h16l2 6H2l2-6Zm0 6v12h16V9M9 21v-7h6v7" },
  { id: "stay", label: "숙박", color: "#64718c", icon: "M3 4v17m18-9v9M3 17h18M3 8h6v5H3m6 0V8h8a4 4 0 0 1 4 4v5" },
] as const;
export type PlaceCategory = typeof PLACE_CATEGORIES[number]["id"];
export type CategoryFilter = PlaceCategory | "all";
export const CUISINES = ["전체", "한식", "중식", "일식", "양식", "분식", "치킨", "기타"] as const;
export type Cuisine = typeof CUISINES[number];
export type NearbyPlace = RouteStop & { placeId: string; kind: PlaceCategory; cuisine: Cuisine; address: string; phone: string; detail: string; distance: number; subcategory?: string; source?: string; collectedAt?: string; referenceMonth?: string; verificationStatus?: string; cafeType?: string | null; stadiumFacility?: { scope: "internal" | "exterior" | "unknown"; floor: string; zone: string; uncertaintyM: number; sourceUrl: string }; stadiumAffiliation?: { stadium: string; status: "candidate"; scope: "unknown"; label: string; basis: string; coordinateStatus: "shop_position_unverified" } | null };
export type NearbyStadium = Pick<Stadium, "code" | "name" | "lat" | "lng" | "address">;
export type SearchSpec = { kind: PlaceCategory; method: "category" | "keyword"; query: string; group?: string; accuracy?: boolean };

export const NEARBY_SEARCHES: SearchSpec[] = [
  { kind: "food", method: "category", query: "FD6" },
  { kind: "cafe", method: "category", query: "CE7" },
  { kind: "walk", method: "keyword", query: "공원" },
  { kind: "sight", method: "category", query: "AT4" },
  { kind: "indoor", method: "category", query: "CT1" },
  ...["한식", "중식", "일식", "양식", "분식"].map((query): SearchSpec => ({ kind: "food", method: "keyword", query, group: "FD6" })),
  ...["산책로", "둘레길"].map((query): SearchSpec => ({ kind: "walk", method: "keyword", query })),
  ...["볼링장", "보드게임카페", "방탈출", "영화관"].map((query): SearchSpec => ({ kind: "indoor", method: "keyword", query })),
  { kind: "store", method: "category", query: "CS2" },
  { kind: "stay", method: "category", query: "AD5" },
];

export function distanceMeters(a: { lat: number; lng: number }, b: { lat: number; lng: number }) {
  const rad = Math.PI / 180;
  const h = Math.sin((b.lat - a.lat) * rad / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin((b.lng - a.lng) * rad / 2) ** 2;
  return 6371000 * 2 * Math.asin(Math.sqrt(Math.min(1, h)));
}

function roadBuilding(address: string) {
  return address.match(/\S+(?:로|길)\s+\d+(?:-\d+)?(?=\s|$|,|\()/)?.[0].replace(/\s+/g, " ");
}

// Kakao has no inside-stadium flag. Match the venue address and explicit tenant
// names, with a small central exclusion. This is conservative, not a surveyed polygon.
export function isStadiumFacility(place: KakaoPlace, stadium: NearbyStadium) {
  const stadiumRoad = roadBuilding(stadium.address);
  if (stadiumRoad && roadBuilding(place.road_address_name) === stadiumRoad) return true;
  const name = place.place_name.replace(/\s/g, "");
  if (/야구장|스카이돔|랜더스필드|위즈파크|볼파크|라이온즈파크|챔피언스필드|NC파크|NC구장/i.test(name) && !/역|사거리|앞점|입구점/.test(name)) return true;
  if (/야구장/.test(place.category_name ?? "")) return true;
  return distanceMeters(stadium, { lat: Number(place.y), lng: Number(place.x) }) < 80;
}

// Exact first-level categories selected by the user, including all descendants.
// Kakao returns "수목원,식물원" as one category and AT4 can include sports paths.
export const ALLOWED_TOURISM_CATEGORIES = new Set(["도보여행", "동물원", "문화유적", "수목원", "식물원", "수목원,식물원", "저수지", "전망대", "천문대", "테마거리", "테마파크", "호수", "스포츠,레저"]);

export function kakaoTourismSubcategory(place: KakaoPlace): string | null {
  const parts = (place.category_name ?? "").split(">").map((part) => part.trim().replace(/\s*,\s*/g, ","));
  if (parts[0] === "여행" && parts[1] === "관광,명소") return parts[2] ?? "";
  if (place.category_group_code === "AT4") return parts[0] === "스포츠,레저" ? parts[0] : "";
  return null;
}

export function classifyPlace(place: KakaoPlace): PlaceCategory | null {
  const detail = place.category_name ?? "";
  const text = `${detail} ${place.place_name}`;
  const group = place.category_group_code;
  if (group === "HP8" || group === "PM9") return null;
  const tourism = kakaoTourismSubcategory(place);
  if (tourism !== null) {
    if (!ALLOWED_TOURISM_CATEGORIES.has(tourism)) return null;
    if (tourism === "도보여행") return "walk";
    if (/실내동물원|보드게임|방탈출|볼링|영화관|박물관|미술관|전시관|실내.*(?:스포츠|놀이터)|오락실/.test(text)) return "indoor";
    return "sight";
  }
  if (group === "CS2") return "store";
  if (group === "AD5") return "stay";
  if (/보드게임|방탈출|볼링|영화관|박물관|미술관|전시관|실내.*(?:스포츠|놀이터)|오락실/.test(text)) return "indoor";
  if (group === "CE7" || /카페|제과|디저트/.test(detail)) return "cafe";
  if (group === "FD6") return "food";
  if (/도보여행|도시근린공원|도시공원|산책로|둘레길|수변공원|어린이공원|체육공원|유원지/.test(detail) || /(?:^| > )공원(?:$| > )/.test(detail)) return "walk";
  return null;
}

// Prefer the activity/facility category over a deeper chain or brand name.
export function indoorNameLabel(name: string): string | undefined {
  for (const [pattern, label] of [
    [/보드게임|보드카페/, "보드게임카페"], [/방탈출/, "방탈출카페"],
    [/볼링/, "볼링장"], [/영화관/, "영화관"], [/박물관/, "박물관"],
    [/미술관|갤러리|화랑/, "미술관·갤러리"], [/전시관|전시실/, "전시관"],
    [/과학관/, "과학관"], [/문학관/, "문학관"], [/기념관/, "기념관"],
    [/공연장|극장|아트홀/, "공연장"], [/아쿠아리움|수족관/, "아쿠아리움"],
    [/도서관/, "도서관"], [/실내동물원/, "실내동물원"], [/오락실/, "오락실"],
  ] as const) if (pattern.test(name)) return label;
  return undefined;
}

function kakaoIndoorSubcategory(place: KakaoPlace): string | undefined {
  const parts = (place.category_name ?? "").split(">").map((part) => part.trim()).filter(Boolean);
  const facility = parts.find((part) => /보드|방탈출|볼링|영화관|박물관|미술관|전시관|과학관|문학관|기념관|공연장|극장|아트홀|갤러리|화랑|아쿠아리움|수족관|도서관|실내|오락실/.test(part));
  return facility ?? indoorNameLabel(place.place_name) ?? (parts.length > 1 ? parts.at(-1) : undefined);
}

function kakaoBasicSubcategory(place: KakaoPlace, kind: PlaceCategory): string | undefined {
  const parts = (place.category_name ?? "").split(">").map((part) => part.trim()).filter(Boolean);
  if (kind === "cafe") {
    const type = parts.find((part) => /커피전문점|테마카페|디저트카페|전통찻집|제과|베이커리|아이스크림|떡,|한과|도넛|주스전문점|초콜릿/.test(part));
    return type ?? (parts.some((part) => /카페/.test(part)) ? "카페" : undefined);
  }
  if (kind === "stay") {
    const index = parts.indexOf("숙박");
    return index >= 0 ? parts[index + 1] : undefined;
  }
  return undefined;
}

export function normalizePlace(place: KakaoPlace, stadium: NearbyStadium): NearbyPlace | null {
  const lat = Number(place.y), lng = Number(place.x);
  if (!place.id || !place.place_name.trim() || !place.x.trim() || !place.y.trim() || !Number.isFinite(lat) || !Number.isFinite(lng) || Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
  const distance = distanceMeters(stadium, { lat, lng });
  if (distance > NEARBY_RADIUS || isStadiumFacility(place, stadium)) return null;
  const kind = classifyPlace(place);
  if (!kind) return null;
  const detail = place.category_name ?? place.category_group_name;
  const cuisine = CUISINES.find((c) => c !== "전체" && c !== "기타" && detail.split(" > ").includes(c)) ?? "기타";
  const subcategory = kakaoTourismSubcategory(place) || (kind === "indoor" ? kakaoIndoorSubcategory(place) : kakaoBasicSubcategory(place, kind));
  return { placeId: place.id, name: place.place_name, lat, lng, kind, cuisine, category: PLACE_CATEGORIES.find((c) => c.id === kind)!.label, address: place.road_address_name || place.address_name, phone: place.phone ?? "", detail, distance, subcategory };
}

export function mergePlaces(existing: NearbyPlace[], incoming: NearbyPlace[]) {
  const unique = new Map(existing.map((place) => [place.placeId, place]));
  for (const place of incoming) {
    const previous = unique.get(place.placeId);
    if (previous) { unique.set(place.placeId, { ...previous, ...place, tourContentId: previous.tourContentId ?? place.tourContentId }); continue; }
    const match = [...unique.values()].find((candidate) => sameTourLocation(candidate, place));
    if (!match) { unique.set(place.placeId, place); continue; }
    // Preserve Kakao's identity/coordinates/category regardless of response order.
    // Retain TourAPI's ID as an alias for previously saved or selected tour stops.
    const kakao = match.placeId.startsWith("tour:") ? place : match;
    const combined = { ...kakao, tourContentId: match.tourContentId ?? place.tourContentId };
    unique.delete(match.placeId);
    unique.set(combined.placeId, combined);
  }
  return [...unique.values()].sort((a, b) => a.distance - b.distance || a.placeId.localeCompare(b.placeId));
}

function sameTourLocation(a: NearbyPlace, b: NearbyPlace) {
  if (a.tourContentId && a.tourContentId === b.tourContentId) return true;
  if (a.placeId.startsWith("tour:") === b.placeId.startsWith("tour:")) return false;
  const name = (value: string) => value.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
  if (name(a.name) !== name(b.name)) return false;
  const distance = distanceMeters(a, b);
  const road = roadBuilding(a.address);
  return distance <= 80 || (Boolean(road) && road === roadBuilding(b.address) && distance <= 250);
}

export function visiblePlaces(places: NearbyPlace[], filter: CategoryFilter | readonly PlaceCategory[], cuisine: Cuisine = "전체") {
  return places.filter((place) => {
    const enabled = typeof filter === "string" ? filter === "all" || filter === place.kind : filter.includes(place.kind);
    return enabled && (place.kind !== "food" || cuisine === "전체" || place.cuisine === cuisine);
  });
}

export function sameStop(a: RouteStop, b: RouteStop) {
  if (a.tourContentId && a.tourContentId === b.tourContentId) return true;
  const lodgingId = (value?: string) => /^kakao-lodging:(?:all|hotel|motel|inn):([0-9]{1,100})$/.exec(value ?? "")?.[1];
  const firstLodging = lodgingId(a.placeId);
  if (firstLodging && firstLodging === lodgingId(b.placeId)) return true;
  return Boolean(a.placeId && b.placeId ? a.placeId === b.placeId : a.name === b.name && distanceMeters(a, b) < 15);
}

export function moveStop(stops: RouteStop[], index: number, direction: -1 | 1) {
  const next = index + direction;
  if (index < 0 || index >= stops.length || next < 0 || next >= stops.length) return stops;
  const result = [...stops];
  [result[index], result[next]] = [result[next], result[index]];
  return result;
}

export function clusterPlaces(places: NearbyPlace[], project: (place: NearbyPlace) => { x: number; y: number }, cellSize: number) {
  const cells = new Map<string, NearbyPlace[]>();
  for (const place of places) {
    const { x, y } = project(place);
    const key = cellSize > 0 ? `${Math.floor(x / cellSize)}:${Math.floor(y / cellSize)}` : place.placeId;
    const group = cells.get(key) ?? [];
    group.push(place); cells.set(key, group);
  }
  return [...cells.values()];
}
