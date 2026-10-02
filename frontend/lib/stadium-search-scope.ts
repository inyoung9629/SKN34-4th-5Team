import { stadiumBoundaries } from "./stadium-boundaries";
import { stadiumLocationAudit } from "./stadium-locations";
import { stadiumBoundaryFrame } from "./stadium-boundary-frame";
import { stadiumComplexReviews } from "./stadium-complex-reviews";

type Coordinate = readonly [number, number];
type Point = { lat: number; lng: number };
export type StadiumArea = { scope: "internal" | "excluded_complex" | "external" | "unknown"; stadium: string | null };

const zones = Object.entries(stadiumLocationAudit.stadiums).map(([code, location]) => ({
  code,
  main: stadiumBoundaryFrame(location, stadiumBoundaries.stadiums[code as keyof typeof stadiumBoundaries.stadiums].rings, stadiumLocationAudit.boundaryPaddingM).path,
  outers: [stadiumComplexReviews[code].outer, ...(stadiumComplexReviews[code].additionalOuters ?? [])],
}));

export function containsStadiumPoint(path: readonly Coordinate[], { lat, lng }: Point): boolean {
  let inside = false;
  for (let i = 0, j = path.length - 1; i < path.length; j = i++) {
    const [y1, x1] = path[j], [y2, x2] = path[i];
    const cross = (lng - x1) * (y2 - y1) - (lat - y1) * (x2 - x1);
    if (Math.abs(cross) < 1e-14 && Math.min(x1, x2) <= lng && lng <= Math.max(x1, x2) && Math.min(y1, y2) <= lat && lat <= Math.max(y1, y2)) return true;
    if ((y1 > lat) !== (y2 > lat) && lng < (x2 - x1) * (lat - y1) / (y2 - y1) + x1) inside = !inside;
  }
  return inside;
}

// Same priority and inclusive edges as backend/travel/stadium_scope.py.
export function classifyStadiumPoint(point: Point): StadiumArea {
  if (!Number.isFinite(point.lat) || !Number.isFinite(point.lng)) return { scope: "unknown", stadium: null };
  const main = zones.find(zone => containsStadiumPoint(zone.main, point));
  if (main) return { scope: "internal", stadium: main.code };
  const complex = zones.find(zone => zone.outers.some(ring => containsStadiumPoint(ring, point)));
  return complex ? { scope: "excluded_complex", stadium: complex.code } : { scope: "external", stadium: null };
}
