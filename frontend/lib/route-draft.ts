import { isLegModes, type LegModes, type TravelMode } from "./course-directions";
import type { RouteStop, TripRoute } from "./routes";
import type { RouteContentFormat } from "./route-content";
import { getStadium } from "./stadiums";
import { createClientId } from "./client-id";
import { isLodgingReference, referenceOnlyStop } from "./google-lodging";
import { isRichContentDoc, type RichContentDoc } from "./community-rich-content";
import { isCourseProgress } from "./chat/course";

export const ROUTE_DRAFT_PREFIX = "kbo-trip-route-draft-v1:";
export const ROUTE_DRAFT_VERSION = 1;

export type RouteDraftData = {
  stadiumCode: string;
  title: string;
  content: string;
  contentFormat?: RouteContentFormat;
  contentDoc?: RichContentDoc | null;
  duration: string;
  tags: string[];
  stops: RouteStop[];
  start?: TripRoute["start"];
  tab: "write" | "chat";
  travelMode: TravelMode;
  legModes?: LegModes;
  plannerCompleted?: boolean;
  plannerMode?: "places" | "draw";
  chatCourseKey?: string;
};

export type StoredRouteDraft = {
  version: typeof ROUTE_DRAFT_VERSION;
  revision: string;
  updatedAt: string;
  data: RouteDraftData;
};

export type MemoryRouteDraft = { data: RouteDraftData; expectedRaw: string | null };

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;
type TimerLike = Pick<typeof globalThis, "setTimeout" | "clearTimeout" | "setInterval" | "clearInterval">;

const isOptionalString = (value: unknown) => value === undefined || typeof value === "string";
const areValidCoordinates = (lat: unknown, lng: unknown) => typeof lat === "number" && typeof lng === "number" && Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180;
const hasOnlyKeys = (value: Record<string, unknown>, allowed: string[]) => Object.keys(value).every(key => allowed.includes(key));
const isStop = (value: unknown): value is RouteStop => {
  if (!value || typeof value !== "object") return false;
  const stop = value as Record<string, unknown>;
  return hasOnlyKeys(stop, ["name", "lat", "lng", "category", "placeId", "visitId", "address", "tourContentId", "isMapPoint", "isDrawnPoint", "coursePlace", "courseGame", "courseProgress"])
    && (stop.courseProgress === undefined || isCourseProgress(stop.courseProgress))
    && (stop.coursePlace === undefined || Boolean(stop.coursePlace && typeof stop.coursePlace === "object" && !Array.isArray(stop.coursePlace)
      && typeof (stop.coursePlace as Record<string, unknown>).name === "string"))
    && (stop.courseGame === undefined || Boolean(stop.courseGame && typeof stop.courseGame === "object" && !Array.isArray(stop.courseGame)
      && /^\d{4}-\d{2}-\d{2}$/.test(String((stop.courseGame as Record<string, unknown>).date))
      && /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(String((stop.courseGame as Record<string, unknown>).time))))
    && typeof stop.name === "string" && typeof stop.category === "string"
    && (isLodgingReference(stop as RouteStop) || areValidCoordinates(stop.lat, stop.lng))
    && ["placeId", "visitId", "address", "tourContentId"].every(key => isOptionalString(stop[key]))
    && ["isMapPoint", "isDrawnPoint"].every(key => stop[key] === undefined || typeof stop[key] === "boolean");
};

export function isRouteDraftData(value: unknown): value is RouteDraftData {
  if (!value || typeof value !== "object") return false;
  const data = value as Record<string, unknown>;
  return hasOnlyKeys(data, ["stadiumCode", "title", "content", "contentDoc", "contentFormat", "duration", "tags", "stops", "start", "tab", "travelMode", "legModes", "plannerCompleted", "plannerMode", "chatCourseKey"])
    && (data.plannerCompleted === undefined || typeof data.plannerCompleted === "boolean")
    && (data.plannerMode === undefined || data.plannerMode === "places" || data.plannerMode === "draw")
    && (data.chatCourseKey === undefined || (typeof data.chatCourseKey === "string" && data.chatCourseKey.length <= 200 && data.chatCourseKey.startsWith("chat:")))
    && (data.legModes === undefined || isLegModes(data.legModes))
    && typeof data.stadiumCode === "string" && Boolean(getStadium(data.stadiumCode)) && typeof data.title === "string" && typeof data.content === "string"
    && (data.contentFormat === undefined || data.contentFormat === "html") && typeof data.duration === "string"
    && (data.contentDoc === undefined || data.contentDoc === null || isRichContentDoc(data.contentDoc))
    && Array.isArray(data.tags) && data.tags.every(tag => typeof tag === "string")
    && Array.isArray(data.stops) && data.stops.every(isStop)
    && (data.start === undefined || (data.start !== null && typeof data.start === "object" && areValidCoordinates((data.start as Record<string, unknown>).lat, (data.start as Record<string, unknown>).lng)))
    && (data.tab === "write" || data.tab === "chat")
    && (data.travelMode === "walk" || data.travelMode === "car" || data.travelMode === "transit");
}

export function parseRouteDraft(raw: string | null): StoredRouteDraft | undefined {
  if (!raw) return undefined;
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object") return undefined;
    const draft = value as Record<string, unknown>;
    return draft.version === ROUTE_DRAFT_VERSION && typeof draft.revision === "string" && typeof draft.updatedAt === "string"
      && !Number.isNaN(Date.parse(draft.updatedAt)) && isRouteDraftData(draft.data) ? { ...draft, data: { ...draft.data, stops: draft.data.stops.map(referenceOnlyStop) } } as StoredRouteDraft : undefined;
  } catch { return undefined; }
}

export function browserDraftStorage(): StorageLike | undefined {
  try { return typeof window === "undefined" ? undefined : window.localStorage; } catch { return undefined; }
}

export function readRouteDraft(storage: StorageLike | undefined, context: string): { draft?: StoredRouteDraft; raw: string | null; error?: true } {
  if (!storage) return { raw: null, ...(typeof window === "undefined" ? {} : { error: true as const }) };
  try { const raw = storage.getItem(ROUTE_DRAFT_PREFIX + context); return { draft: parseRouteDraft(raw), raw }; }
  catch { return { raw: null, error: true }; }
}

/** A new writer resumes the last draft, not a random stadium's older draft.
 * Return its storage key's stadium: the selected stadium may have changed since
 * the draft was first opened, so using data.stadiumCode would load another slot.
 */
export function latestNewDraftStadium(storage: StorageLike | undefined, stadiumCodes: string[]): string | undefined {
  let latest: { code: string; timestamp: number } | undefined;
  for (const code of stadiumCodes) {
    const draft = readRouteDraft(storage, `new:${code}`).draft;
    if (!draft || !stadiumCodes.includes(draft.data.stadiumCode)) continue;
    const timestamp = Date.parse(draft.updatedAt);
    if (!latest || timestamp > latest.timestamp) latest = { code, timestamp };
  }
  return latest?.code;
}

export function recoverRouteDraft(stored: ReturnType<typeof readRouteDraft>, memory?: MemoryRouteDraft) {
  return memory ? { data: memory.data, expectedRaw: memory.expectedRaw, dirty: true as const } : { data: stored.draft?.data, expectedRaw: stored.raw, dirty: false as const };
}

export function saveRouteDraft(storage: StorageLike | undefined, context: string, data: RouteDraftData, expectedRaw: string | null): { status: "saved"; draft: StoredRouteDraft; raw: string } | { status: "unchanged"; raw: string | null } | { status: "conflict" | "error" } {
  if (!storage || !isRouteDraftData(data)) return { status: "error" };
  data = { ...data, stops: data.stops.map(referenceOnlyStop) };
  const key = ROUTE_DRAFT_PREFIX + context;
  try {
    const currentRaw = storage.getItem(key);
    const current = parseRouteDraft(currentRaw);
    const serializedData = JSON.stringify(data);
    if (current && JSON.stringify(current.data) === serializedData) return { status: "unchanged", raw: currentRaw };
    if (currentRaw && !current) return { status: "conflict" };
    if (currentRaw !== expectedRaw) return { status: "conflict" };
    const draft: StoredRouteDraft = { version: ROUTE_DRAFT_VERSION, revision: createClientId(), updatedAt: new Date().toISOString(), data };
    const raw = JSON.stringify(draft);
    storage.setItem(key, raw);
    return { status: "saved", draft, raw };
  } catch { return { status: "error" }; }
}

export function removeRouteDraft(storage: StorageLike | undefined, context: string, expectedRaw: string | null): boolean {
  if (!storage) return false;
  try {
    const key = ROUTE_DRAFT_PREFIX + context;
    if (storage.getItem(key) !== expectedRaw) return false;
    storage.removeItem(key); return true;
  } catch { return false; }
}

export function createDraftAutosave(flush: () => void, timers: TimerLike = globalThis) {
  let stopped = false;
  let debounce: ReturnType<typeof setTimeout> | undefined;
  const periodic = timers.setInterval(() => { if (!stopped) flush(); }, 5000);
  return {
    changed() {
      if (stopped) return;
      if (debounce !== undefined) timers.clearTimeout(debounce);
      debounce = timers.setTimeout(() => { debounce = undefined; if (!stopped) flush(); }, 1000);
    },
    flush() { if (!stopped) flush(); },
    stop(flushFirst = false) {
      if (stopped) return;
      if (flushFirst) flush();
      stopped = true;
      if (debounce !== undefined) timers.clearTimeout(debounce);
      timers.clearInterval(periodic);
    },
  };
}
