import { browserDraftStorage, readRouteDraft, type RouteDraftData } from "../route-draft";
import { currentCourse } from "./current-course";
import type { ChatCourse, CourseWriterState } from "./types";

export function draftWriterState(draft: RouteDraftData): CourseWriterState {
  const first = draft.stops[0];
  const start = draft.start ?? (first && (first.placeId === "route:origin" || first.isMapPoint) ? first : undefined);
  return { title: draft.title, completed: draft.plannerCompleted ?? false,
    origin: start ? { lat: start.lat, lng: start.lng, ...(start.name ? { name: start.name } : {}) } : null };
}

/** 서버에서 읽은 소유 대화/답변 ID가 있을 때만 그 답변에 연결된 기기 초안을 읽는다. */
export function restoreWriterCourse(course: ChatCourse, identity: string, sessionId?: string, messageId?: number,
  storage = browserDraftStorage(), restoreSaved = true): ChatCourse {
  if (!sessionId || !messageId || !Number.isSafeInteger(messageId)) return course;
  const writerKey = `chat:${identity}:${sessionId}:${messageId}`;
  if (!restoreSaved) return { ...course, writerKey };
  const draft = readRouteDraft(storage, writerKey).draft?.data;
  if (!draft || draft.chatCourseKey !== writerKey || draft.stadiumCode !== course.stadiumCode) return { ...course, writerKey };
  const writerState = draftWriterState(draft);
  const current = currentCourse(draft.stops, draft.stadiumCode, draft.travelMode, draft.legModes ?? {}, draft.start, writerState);
  return { ...course, ...(current ? { places: current.places, game: current.game, progress: current.progress } : {}),
    travelMode: draft.travelMode, legModes: draft.legModes ?? {}, title: draft.title,
    origin: writerState.origin ?? undefined, writerState, writerKey, writerDraft: draft };
}
