import { distanceMeters, type NearbyPlace, type NearbyStadium } from "./nearby-places";

export type FacilityPin = {
  id: string; recordId: string; lat: number; lng: number; label: string;
  quality: "diagram_approximate" | "section_approximate" | "display_reference"; uncertaintyM: number; checkedAt: string;
  source: { stadium: string; pageUrl: string; imageUrl?: string; note: string };
};
export type StadiumFacility = {
  id: string; stadium: string; name: string; kind: "food" | "facility";
  affiliation: "stadium"; scope: "internal" | "exterior" | "unknown"; scopeLabel: string;
  floor: string; zone: string; sourceUrl: string; sourceCheckedAt: string;
  foodCategory?: "FOOD" | "CAFE" | "CONVENIENCE"; sourceLocation?: string; locationLabel?: string;
  evidenceType: "UNOFFICIAL"; operatingStatus: "unverified";
  locationStatus: "zone_only" | "approximate_pin" | "reference_pin"; pins: FacilityPin[];
};
export type StadiumFacilities = { stadium: string; records: StadiumFacility[]; count: number; pinCount: number; checkedAt: string; warning: string; review: { note: string; status: string; source?: FacilityPin["source"] } };

export function parseStadiumFacilities(value: unknown, code: string): StadiumFacilities {
  const data = value as StadiumFacilities | null;
  const sourceUrl = (url: unknown) => typeof url === "string" && /^https:\/\/myseatcheck\.com\//.test(url);
  if (!data || data.stadium !== code || !Array.isArray(data.records) || data.count !== data.records.length || typeof data.warning !== "string" || typeof data.review?.note !== "string") throw new Error("구장 시설 응답을 확인하지 못했어요.");
  const ids = new Set<string>(), pinIds = new Set<string>();
  if (data.review.source && (data.review.source.stadium !== code || !sourceUrl(data.review.source.pageUrl) || (data.review.source.imageUrl !== undefined && !sourceUrl(data.review.source.imageUrl)))) throw new Error("구장 안내도 출처를 확인하지 못했어요.");
  for (const record of data.records) {
    if (!record || ids.has(record.id) || !/^SC_(FOOD|FAC)_[A-Z]+_\d{3}$/.test(record.id) || record.stadium !== code || record.affiliation !== "stadium" || !["internal", "exterior", "unknown"].includes(record.scope) || !["food", "facility"].includes(record.kind) || ![record.name, record.floor, record.zone, record.scopeLabel, record.sourceCheckedAt].every(v => typeof v === "string") || !sourceUrl(record.sourceUrl) || !Array.isArray(record.pins)) throw new Error("구장 시설 응답을 확인하지 못했어요.");
    ids.add(record.id);
    if (record.foodCategory !== undefined && !["FOOD", "CAFE", "CONVENIENCE"].includes(record.foodCategory)) throw new Error("먹거리 분류를 확인하지 못했어요.");
    for (const pin of record.pins) {
      if (!pin || typeof pin.id !== "string" || pinIds.has(pin.id) || pin.recordId !== record.id || !Number.isFinite(pin.lat) || !Number.isFinite(pin.lng) || pin.lat < 33 || pin.lat > 39 || pin.lng < 124 || pin.lng > 132 || !Number.isFinite(pin.uncertaintyM) || (pin.quality === "display_reference" ? pin.uncertaintyM !== 0 : pin.uncertaintyM < 10 || pin.uncertaintyM > 100) || !["diagram_approximate", "section_approximate", "display_reference"].includes(pin.quality) || typeof pin.label !== "string" || pin.source?.stadium !== code || !sourceUrl(pin.source.pageUrl) || (pin.source.imageUrl !== undefined && !sourceUrl(pin.source.imageUrl))) throw new Error("구장 시설 핀을 확인하지 못했어요.");
      pinIds.add(pin.id);
    }
  }
  if (pinIds.size !== data.pinCount) throw new Error("구장 시설 핀 개수를 확인하지 못했어요.");
  return data;
}

export async function fetchStadiumFacilities(code: string, signal: AbortSignal): Promise<StadiumFacilities> {
  if (!/^[A-Z]{2,20}$/.test(code)) throw new Error("구장 코드를 확인해 주세요.");
  const response = await fetch(`/api/v1/places/stadium-facilities/?stadium=${code}`, { signal, cache: "no-store" });
  if (!response.ok) throw new Error("구장 시설 자료를 불러오지 못했어요.");
  return parseStadiumFacilities(await response.json(), code);
}

export function filterStadiumFacilities(records: StadiumFacility[], query: string, scope = "all", kind = "all") {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  return records.filter(row => (scope === "all" || row.scope === scope)
    && (kind === "all" || (kind === "cafe" ? row.foodCategory === "CAFE" : kind === "food" ? row.kind === "food" && row.foodCategory !== "CAFE" : row.kind === kind))
    && words.every(word => `${row.name} ${row.floor} ${row.zone} ${row.scopeLabel}`.toLocaleLowerCase().includes(word)));
}

export function facilityPlace(record: StadiumFacility, pin: FacilityPin, stadium: NearbyStadium): NearbyPlace {
  if (record.stadium !== stadium.code || !record.pins.some(value => value.id === pin.id)) throw new Error("다른 구장의 시설입니다.");
  return { placeId: `stadium-facility:${record.id}:${pin.id}`, name: record.name, lat: pin.lat, lng: pin.lng,
    kind: record.kind === "food" ? record.foodCategory === "CAFE" ? "cafe" : "food" : "indoor",
    category: record.kind === "food" ? record.foodCategory === "CAFE" ? "카페·디저트" : record.foodCategory === "CONVENIENCE" ? "편의점" : "먹거리" : record.scopeLabel, subcategory: record.scopeLabel,
    cuisine: "기타", address: `${stadium.name} · ${record.locationLabel ?? `${record.floor} · ${record.zone}`}`,
    distance: distanceMeters(stadium, pin), phone: "", detail: `${record.scopeLabel} · ${record.zone}`,
    source: "MYSEATCHECK", collectedAt: record.sourceCheckedAt, verificationStatus: "approximate",
    stadiumFacility: { scope: record.scope, floor: record.floor, zone: record.zone, uncertaintyM: pin.uncertaintyM, sourceUrl: record.sourceUrl, referencePin: pin.quality === "display_reference" },
  };
}
