import { isRecord } from "./validation";
import type { ChatCourse, ChatCoursePlace } from "./types";

const PHASES = new Set(["BEFORE", "GAME", "AFTER"]);
const CATEGORIES = new Set(["FOOD", "CAFE", "SPOT", "STADIUM", "STAY", "WALK", "INDOOR"]);
const MODES = new Set(["walk", "car", "transit"]);
const MAX_PLACES = 12;

const text = (value: unknown, max: number) => typeof value === "string" && value.trim() ? value.trim().slice(0, max) : undefined;
const finite = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value : undefined;

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
    ...(placeId ? { placeId } : {}),
    ...(text(value.address, 500) ? { address: text(value.address, 500) } : {}),
    ...(text(value.reason, 120) ? { reason: text(value.reason, 120) } : {}),
    ...(text(value.time, 16) ? { time: text(value.time, 16) } : {}),
    ...(stayMin !== undefined ? { stayMin } : {}),
  };
}

/**
 * 챗봇 done 이벤트 → 지도에 담을 코스. 코스 추천이 아니거나 형식이 어긋나면 undefined.
 * 서버 응답은 신뢰하지 않고 좌표·길이를 다시 확인한다 (틀린 한 곳은 버리고 나머지는 쓴다).
 */
export function parseChatCourse(value: unknown): ChatCourse | undefined {
  if (!isRecord(value) || !Array.isArray(value.places)) return undefined;
  const places = value.places.slice(0, MAX_PLACES).map(parsePlace).filter((place): place is ChatCoursePlace => place !== null);
  if (!places.some(place => place.category !== "STADIUM")) return undefined;
  const travel = isRecord(value.travel) ? value.travel : {};
  const payload = isRecord(value.coursePayload) ? value.coursePayload : {};
  const mode = MODES.has(String(travel.mode)) ? travel.mode as ChatCourse["travelMode"] : undefined;
  const notes = Array.isArray(travel.lines) ? travel.lines.map(line => text(line, 160)).filter((line): line is string => Boolean(line)).slice(0, 3) : [];
  const stadiumCode = text(value.stadiumCode, 40);
  return {
    places, notes,
    ...(stadiumCode ? { stadiumCode } : {}),
    ...(mode ? { travelMode: mode } : {}),
    ...(text(travel.label, 20) ? { travelLabel: text(travel.label, 20) } : {}),
    ...(text(travel.summary, 160) ? { summary: text(travel.summary, 160) } : {}),
    ...(text(payload.title, 80) ? { title: text(payload.title, 80) } : {}),
    ...(text(payload.content, 12000) ? { content: text(payload.content, 12000) } : {}),
  };
}

export const COURSE_CATEGORY_LABEL: Record<ChatCoursePlace["category"], string> = {
  FOOD: "먹거리", CAFE: "카페·디저트", SPOT: "명소·산책", STADIUM: "야구장",
  STAY: "숙박", WALK: "산책", INDOOR: "실내 놀거리",
};
export const COURSE_PHASE_LABEL: Record<ChatCoursePlace["phase"], string> = {
  BEFORE: "경기 전", GAME: "경기", AFTER: "경기 후",
};

type CourseStop = { name: string; lat: number; lng: number; category: string; placeId?: string; visitId?: string; address?: string; isDrawnPoint?: boolean };

/**
 * 챗봇 코스 → 루트 작성 화면의 방문 순서(RouteStop). 구장도 한 지점으로 넣어 "경기 전 → 구장 → 경기 후"가 지도에 그대로 이어진다.
 * 지도에서 직접 담은 장소와 같은 모양(isDrawnPoint·visitId)으로 만들어 순서 바꾸기·삭제·저장이 똑같이 동작한다.
 */
export function courseToStops(course: ChatCourse, newId: () => string = () => crypto.randomUUID()): CourseStop[] {
  return course.places.map((place): CourseStop => ({
    visitId: newId(),
    name: place.name,
    lat: place.lat,
    lng: place.lng,
    category: COURSE_CATEGORY_LABEL[place.category],
    placeId: place.placeId && /^(?:\d+|collected:(?:SBIZ|PARK|TOUR):.+)$/.test(place.placeId) ? place.placeId : `chat:${place.category === "STADIUM" ? `stadium:${course.stadiumCode ?? place.name}` : newId()}`,
    ...(place.address ? { address: place.address } : {}),
    isDrawnPoint: true,
  }));
}
