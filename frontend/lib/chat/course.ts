import type { ChatCourse, ChatCoursePlace, CoursePreferences, CourseProgress, CourseWriterState } from "./types";
import { isLegModes } from "../course-directions";
import type { RouteStop } from "../routes";

const PHASES = new Set(["BEFORE", "GAME", "AFTER"]);
const CATEGORIES = new Set(["FOOD", "CAFE", "SPOT", "STADIUM", "STAY", "WALK", "INDOOR", "CONVENIENCE"]);
const MODES = new Set(["walk", "car", "transit"]);
const MAX_PLACES = 12;
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);

const text = (value: unknown, max: number) => typeof value === "string" && value.trim() ? value.trim().slice(0, max) : undefined;
const finite = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value : undefined;

export function isCourseProgress(value: unknown): value is CourseProgress {
  return isRecord(value) && Object.keys(value).length > 0
    && Object.entries(value).every(([key, n]) => ["startMinute", "gameEndMinute"].includes(key)
      && typeof n === "number" && Number.isInteger(n) && n >= 0 && n < 2880);
}

export function parseCoursePreferences(value: unknown): CoursePreferences | undefined {
  if (!isRecord(value)) return undefined;
  const list = (key: string, max: number) => Array.isArray(value[key])
    ? (value[key] as unknown[]).flatMap(item => typeof item === "string" && item.trim() ? [item.slice(0, 255)] : []).slice(0, max) : [];
  return { conditions: list("conditions", 20), lockedPlaces: list("lockedPlaces", 12), rejectedPlaces: list("rejectedPlaces", 100) };
}

function sourceUrl(value: unknown): string | undefined {
  if (typeof value !== "string" || value.length > 2000 || /[\s\u0000-\u001f]/.test(value)) return undefined;
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || !url.hostname || url.username || url.password) return undefined;
    if (url.hostname === "place.map.kakao.com") url.protocol = "https:";
    return url.href;
  } catch { return undefined; }
}

function parseOrigin(value: unknown): ChatCourse["origin"] {
  if (!isRecord(value)) return undefined;
  const lat = finite(value.lat), lng = finite(value.lng);
  if (lat === undefined || lng === undefined || Math.abs(lat) > 90 || Math.abs(lng) > 180) return undefined;
  return { lat, lng, ...(text(value.name, 100) ? { name: text(value.name, 100) } : {}) };
}

function parsePlace(value: unknown): ChatCoursePlace | null {
  if (!isRecord(value)) return null;
  const name = text(value.name, 255), lat = finite(value.lat), lng = finite(value.lng);
  if (!name || lat === undefined || lng === undefined || Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
  const phase = PHASES.has(String(value.phase)) ? value.phase as ChatCoursePlace["phase"] : "BEFORE";
  const category = CATEGORIES.has(String(value.category)) ? value.category as ChatCoursePlace["category"] : "SPOT";
  const placeId = typeof value.placeId === "number" ? String(value.placeId) : text(value.placeId, 255);
  const stayMin = finite(value.stayMin);
  return {
    phase, name, lat, lng, category,
    ...(text(value.visitId, 255) ? { visitId: text(value.visitId, 255) } : {}),
    ...(placeId ? { placeId } : {}),
    ...(text(value.address, 500) ? { address: text(value.address, 500) } : {}),
    ...(text(value.reason, 120) ? { reason: text(value.reason, 120) } : {}),
    ...(text(value.time, 16) ? { time: text(value.time, 16) } : {}),
    ...(text(value.until, 16) ? { until: text(value.until, 16) } : {}),
    ...(value.completed === true ? { completed: true } : {}),
    ...(sourceUrl(value.placeUrl) ? { placeUrl: sourceUrl(value.placeUrl) } : {}),
    ...(stayMin !== undefined ? { stayMin } : {}),
    ...(typeof value.stayOverride === "number" && Number.isInteger(value.stayOverride) && value.stayOverride >= 1 && value.stayOverride <= 720 ? { stayOverride: value.stayOverride } : {}),
  };
}

/**
 * 챗봇 done 이벤트 → 지도에 담을 코스. 코스 추천이 아니거나 형식이 어긋나면 undefined.
 * 서버 응답은 신뢰하지 않고 좌표·길이를 다시 확인한다 (틀린 한 곳은 버리고 나머지는 쓴다).
 */
export function parseChatCourse(value: unknown, allowEmptyContext = false): ChatCourse | undefined {
  if (!isRecord(value) || !Array.isArray(value.places)) return undefined;
  const places = value.places.slice(0, MAX_PLACES).map(parsePlace).filter((place): place is ChatCoursePlace => place !== null);
  // 빈 장소 목록은 명시적인 작성 상태를 보낸 요청에서만 허용한다. 빈 추천 카드는 만들지 않는다.
  if ((!places.length && !(allowEmptyContext && value.places.length === 0 && parseWriterState(value.writerState)))
    || (value.edit !== true && !places.some(place => place.category !== "STADIUM"))) return undefined;
  const travel = isRecord(value.travel) ? value.travel : {};
  const payload = isRecord(value.coursePayload) ? value.coursePayload : {};
  const mode = MODES.has(String(travel.mode)) ? travel.mode as ChatCourse["travelMode"] : undefined;
  const notes = Array.isArray(travel.lines) ? travel.lines.map(line => text(line, 160)).filter((line): line is string => Boolean(line)).slice(0, 3) : [];
  const stadiumCode = text(value.stadiumCode, 40);
  return {
    places, notes,
    ...(parseWriterState(value.writerState) ? { writerState: parseWriterState(value.writerState) } : {}),
    ...(isCourseProgress(value.progress) ? { progress: { ...value.progress } } : {}),
    ...(value.edit === true ? { edit: true } : {}),
    ...(isLegModes(value.legModes) ? { legModes: value.legModes } : {}),
    ...(isRecord(value.game) && typeof value.game.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value.game.date)
      && typeof value.game.time === "string" && /^([01]\d|2[0-3]):[0-5]\d$/.test(value.game.time) ? { game: { date: value.game.date, time: value.game.time } } : {}),
    ...(parseOrigin(value.origin) ? { origin: parseOrigin(value.origin) } : {}),
    ...(parseOrigin(value.entryPoint) ? { entryPoint: parseOrigin(value.entryPoint) } : {}),
    ...(text(value.approachNotice, 1000) ? { approachNotice: text(value.approachNotice, 1000) } : {}),
    ...(stadiumCode ? { stadiumCode } : {}),
    ...(text(value.timeWarning, 1000) ? { timeWarning: text(value.timeWarning, 1000) } : {}),
    ...(mode ? { travelMode: mode } : {}),
    ...(text(travel.label, 20) ? { travelLabel: text(travel.label, 20) } : {}),
    ...(text(travel.summary, 160) ? { summary: text(travel.summary, 160) } : {}),
    ...(text(payload.title, 80) ? { title: text(payload.title, 80) } : {}),
    ...(text(payload.content, 12000) ? { content: text(payload.content, 12000) } : {}),
  };
}

export function parseWriterState(value: unknown): CourseWriterState | undefined {
  if (!isRecord(value) || Object.keys(value).length !== 3 || typeof value.title !== "string" || value.title.length > 80
    || typeof value.completed !== "boolean" || !("origin" in value)) return undefined;
  const origin = value.origin === null ? null : parseOrigin(value.origin);
  if (origin === undefined || (isRecord(value.origin) && value.origin.name !== undefined
    && (typeof value.origin.name !== "string" || value.origin.name.length > 100))) return undefined;
  return { title: value.title, origin, completed: value.completed };
}

/** 구버전 수정 답변에서 누락된 메타데이터만 같은 코스의 이전 답변에서 이어받는다. */
export function inheritCourseState(course: ChatCourse, previous?: ChatCourse): ChatCourse {
  if (!course.edit || !previous || course.stadiumCode !== previous.stadiumCode) return course;
  const writer = course.writerState ?? (previous.writerState ? { ...previous.writerState,
    origin: course.origin ?? previous.writerState.origin } : undefined);
  return { ...course, title: course.title ?? previous.title, content: course.content ?? previous.content,
    origin: writer ? writer.origin ?? undefined : course.origin ?? previous.origin,
    ...(writer ? { writerState: writer } : {}) };
}

export const COURSE_CATEGORY_LABEL: Record<ChatCoursePlace["category"], string> = {
  FOOD: "먹거리", CAFE: "카페·디저트", SPOT: "명소·산책", STADIUM: "야구장",
  STAY: "숙박", WALK: "산책", INDOOR: "실내 놀거리",
  CONVENIENCE: "편의점",
};
export const COURSE_PHASE_LABEL: Record<ChatCoursePlace["phase"], string> = {
  BEFORE: "경기 전", GAME: "경기", AFTER: "경기 후",
};

/**
 * 챗봇 코스 → 루트 작성 화면의 방문 순서(RouteStop). 구장도 한 지점으로 넣어 "경기 전 → 구장 → 경기 후"가 지도에 그대로 이어진다.
 * 지도에서 직접 담은 장소와 같은 모양(isDrawnPoint·visitId)으로 만들어 순서 바꾸기·삭제·저장이 똑같이 동작한다.
 */
export function courseToStops(course: ChatCourse, newId: () => string = () => crypto.randomUUID(), previous: RouteStop[] = []): RouteStop[] {
  return course.places.map((place): RouteStop => {
    const fixed = course.edit ? previous.find((stop, index) => courseVisitId(stop, index) === place.visitId
      && stop.name === place.name && stop.lat === place.lat && stop.lng === place.lng && stop.placeId === place.placeId) : undefined;
    const metadata = { coursePlace: place, ...(course.game ? { courseGame: course.game } : {}), ...(course.progress ? { courseProgress: course.progress } : {}) };
    if (fixed) {
      const updated = { ...fixed, ...metadata };
      if (!course.game) delete updated.courseGame;
      if (!course.progress) delete updated.courseProgress;
      return updated;
    }
    return {
    visitId: place.visitId ?? newId(),
    name: place.name,
    lat: place.lat,
    lng: place.lng,
    category: COURSE_CATEGORY_LABEL[place.category],
    placeId: place.placeId && /^(?:\d+|collected:(?:SBIZ|PARK|TOUR):.+|stadium-facility:SC_(?:FOOD|FAC)_[A-Z]+_\d{3}:[\w-]+)$/.test(place.placeId) ? place.placeId : `chat:${place.category === "STADIUM" ? `stadium:${course.stadiumCode ?? place.name}` : newId()}`,
    ...(place.address ? { address: place.address } : {}),
    isDrawnPoint: true,
    ...metadata,
  }; });
}

export function courseVisitId(stop: RouteStop, index: number): string {
  return stop.visitId ?? `stop:${index}:${stop.placeId ?? `${stop.lat},${stop.lng}`}`;
}
