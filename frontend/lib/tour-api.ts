import type { NearbyStadium } from "./nearby-places";
import type { TourResult } from "./tour-places";

type Fetcher = (url: string, init?: RequestInit) => Promise<Response>;

export async function fetchTourPlaces(stadium: NearbyStadium, fetcher: Fetcher = fetch, signal?: AbortSignal): Promise<TourResult> {
  const query = new URLSearchParams({ stadium: stadium.code, lat: String(stadium.lat), lng: String(stadium.lng) });
  const response = await fetcher(`/api/v1/tourism/?${query}`, { cache: "no-store", redirect: "error", signal: signal ?? AbortSignal.timeout(30_000) });
  if (!response.ok) throw new Error("Tourism places unavailable");
  const result: unknown = await response.json();
  if (!result || typeof result !== "object" || !Array.isArray((result as TourResult).places) || !["ok", "partial", "unconfigured", "error"].includes((result as TourResult).status) || typeof (result as TourResult).truncated !== "boolean") throw new Error("Invalid tourism response");
  return result as TourResult;
}
