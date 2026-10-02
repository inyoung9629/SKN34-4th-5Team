// Pilot storage must never grow into a Google place-content database.
export const PILOT_STORAGE_KEY = "kbo:google-lodging-pilot:v1";
export const PILOT_REQUEST_LIMIT = 4;
// Existing ID from 20260927T092810Z/MUNHAK/google_lodging_ids.jsonl (not a verified hotel).
export const PILOT_COLLECTED_ID = "ChIJ-YJG7nR5ezURBua8dC789y8";
export type PilotReference = { version: 1; stadium: "MUNHAK"; placeId: string };
export type PilotSelection = { id: string; lat: number; lng: number };
export type UiKitPlace = { id?: string; location?: { lat(): number; lng(): number } | null };

export function serializePilotReference(placeId: string): string {
  if (!placeId.trim() || placeId.length > 512) throw new Error("유효하지 않은 장소 ID");
  const reference: PilotReference = { version: 1, stadium: "MUNHAK", placeId };
  return JSON.stringify(reference);
}

export function parsePilotReference(raw: string | null): PilotReference | null {
  try {
    const value: unknown = JSON.parse(raw ?? "null");
    if (!value || typeof value !== "object") return null;
    const item = value as Record<string, unknown>;
    if (item.version !== 1 || item.stadium !== "MUNHAK" || typeof item.placeId !== "string") return null;
    return JSON.parse(serializePilotReference(item.placeId)) as PilotReference;
  } catch { return null; }
}

export function projectUiKitPlace(place: UiKitPlace | undefined): PilotSelection | null {
  if (!place?.id || !place.location) return null;
  const lat = place.location.lat();
  const lng = place.location.lng();
  if (!Number.isFinite(lat) || !Number.isFinite(lng) || Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
  return { id: place.id, lat, lng };
}
