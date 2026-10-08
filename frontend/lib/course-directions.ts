export type TravelMode = "walk" | "car" | "transit";
export type LegModes = Record<string, TravelMode>;
export type TravelPoint = { lat: number; lng: number };
export type TravelLeg = { status: "ok" | "error"; mode?: TravelMode; distance?: number; seconds?: number; paths: TravelPoint[][]; instructions: string[]; error?: string };
export type CourseDirections = { mode: TravelMode; legs: TravelLeg[]; distance: number | null; seconds: number | null };
export const TRAVEL_MODES = [{ id: "walk", label: "도보", color: "#278469" }, { id: "car", label: "자동차", color: "#2868dc" }, { id: "transit", label: "대중교통", color: "#8054b5" }] as const;
export function travelTime(seconds: number) {
  const minutes = Math.ceil(seconds / 60);
  return minutes >= 60 ? `${Math.floor(minutes / 60)}시간${minutes % 60 ? ` ${minutes % 60}분` : ""}` : `${minutes}분`;
}
export function travelDistance(meters: number) { return meters < 1000 ? `${Math.round(meters)}m` : `${(meters / 1000).toFixed(1)}km`; }
export function validTravelPoint(value: unknown): value is TravelPoint {
  if (!value || typeof value !== "object") return false;
  const p = value as TravelPoint;
  return typeof p.lat === "number" && typeof p.lng === "number" && Number.isFinite(p.lat) && Number.isFinite(p.lng) && Math.abs(p.lat) <= 90 && Math.abs(p.lng) <= 180;
}

export function legKey(from: TravelPoint, to: TravelPoint) {
  return `${from.lat.toFixed(6)},${from.lng.toFixed(6)}>${to.lat.toFixed(6)},${to.lng.toFixed(6)}`;
}
export function isLegModes(value: unknown): value is LegModes {
  return Boolean(value && typeof value === "object" && !Array.isArray(value)
    && Object.entries(value).length <= 144 && Object.entries(value).every(([key, mode]) =>
      /^-?\d{1,3}\.\d{6},-?\d{1,3}\.\d{6}>-?\d{1,3}\.\d{6},-?\d{1,3}\.\d{6}$/.test(key)
      && ["walk", "car", "transit"].includes(String(mode))));
}
export function courseLegModes(points: TravelPoint[], fallback: TravelMode, overrides: LegModes = {}) {
  return points.slice(1).map((point, index) => overrides[legKey(points[index], point)] ?? fallback);
}
export function activeLegModes(points: TravelPoint[], overrides: LegModes = {}): LegModes {
  return Object.fromEntries(points.slice(1).flatMap((point, index) => {
    if (!validTravelPoint(points[index]) || !validTravelPoint(point)) return [];
    const key = legKey(points[index], point);
    return overrides[key] ? [[key, overrides[key]]] : [];
  }));
}

/** 구간별 캐시를 공유해 한 구간의 수단 변경이 다른 구간의 재조회로 이어지지 않게 한다. */
export async function fetchCourseDirections(points: TravelPoint[], modes: TravelMode[], fallback: TravelMode,
  cache: Map<string, TravelLeg>, signal: AbortSignal, fetcher: typeof fetch = fetch): Promise<CourseDirections> {
  const legs = await Promise.all(points.slice(1).map(async (to, index): Promise<TravelLeg> => {
    const from = points[index], mode = modes[index] ?? fallback;
    const key = `${mode}:${legKey(from, to)}`;
    const cached = cache.get(key);
    if (cached) return cached;
    try {
      const response = await fetcher("/api/v1/travel/directions/", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "directions", mode, points: [from, to].map(({ lat, lng }) => ({ lat, lng })) }), signal });
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.error === "string" ? body.error : "경로를 조회하지 못했어요.");
      const leg = body.legs?.[0] as TravelLeg | undefined;
      if (!leg || !["ok", "error"].includes(leg.status) || !Array.isArray(leg.paths) || !Array.isArray(leg.instructions)
        || (leg.status === "ok" && (!Number.isFinite(leg.seconds) || !Number.isFinite(leg.distance)))) throw new Error("경로 응답을 확인하지 못했어요.");
      const result = { ...leg, mode };
      if (!signal.aborted && result.status === "ok") cache.set(key, result);
      return result;
    } catch (error) {
      if (signal.aborted) throw error;
      return { mode, status: "error", paths: [], instructions: [], error: error instanceof Error ? error.message : "경로를 조회하지 못했어요." };
    }
  }));
  const complete = legs.every(leg => leg.status === "ok");
  return { mode: fallback, legs, seconds: complete ? legs.reduce((sum, leg) => sum + leg.seconds!, 0) : null,
    distance: complete ? legs.reduce((sum, leg) => sum + leg.distance!, 0) : null };
}
