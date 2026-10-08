import { MAX_HISTORY_MESSAGES, MAX_MESSAGE_LENGTH, MAX_REPLY_LENGTH } from "./types";
import type { ChatContext, ChatCurrentCourse, ChatMessage, ChatRequest } from "./types";
import { isCourseProgress, parseChatCourse } from "./course";
import { isLegModes } from "../course-directions";

export class ChatError extends Error {
  constructor(message: string, public status = 400, public fields?: Record<string, string[]>) { super(message); }
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseChatRequest(value: unknown): ChatRequest {
  if (!isRecord(value) || !Array.isArray(value.messages) || !value.messages.length || value.messages.length > MAX_HISTORY_MESSAGES) {
    throw new ChatError("대화 내용을 확인해 주세요. 한 번에 최근 12개 메시지까지 보낼 수 있어요.");
  }
  const messages: ChatMessage[] = value.messages.map(message => {
    if (!isRecord(message) || (message.role !== "user" && message.role !== "assistant") || typeof message.content !== "string") {
      throw new ChatError("메시지 형식이 올바르지 않아요.");
    }
    const content = message.content.trim();
    const limit = message.role === "user" ? MAX_MESSAGE_LENGTH : MAX_REPLY_LENGTH;
    if (!content || content.length > limit) throw new ChatError("메시지가 비어 있거나 너무 길어요.");
    return { role: message.role, content };
  });
  if (messages.at(-1)?.role !== "user") throw new ChatError("마지막 메시지는 질문이어야 해요.");

  let context: ChatContext | undefined;
  if (value.context !== undefined) {
    if (!isRecord(value.context)) throw new ChatError("대화 문맥 형식이 올바르지 않아요.");
    context = {};
    if (value.context.currentCourse !== undefined) {
      const raw = value.context.currentCourse;
      const parsed = isRecord(raw) ? parseChatCourse({ ...raw, edit: true, travel: { mode: raw.travelMode } }, true) : undefined;
      if (!isRecord(raw) || !Array.isArray(raw.places) || raw.places.length > 12
        || !parsed || parsed.places.length !== raw.places.length || typeof raw.stadiumCode !== "string" || raw.stadiumCode.length > 40
        || !["walk", "car", "transit"].includes(String(raw.travelMode)) || !isLegModes(raw.legModes)
        || (raw.progress !== undefined && !isCourseProgress(raw.progress))
        || (raw.writerState !== undefined && !parsed.writerState)
        || raw.places.some(p => !isRecord(p) || typeof p.visitId !== "string" || !p.visitId || p.visitId.length > 255
          || typeof p.label !== "string" || !p.label || p.label.length > 20)
        || new Set(parsed.places.map(p => p.visitId)).size !== parsed.places.length) throw new ChatError("수정할 코스를 확인해 주세요.");
      let selectedPlace: ChatCurrentCourse["selectedPlace"];
      if (raw.selectedPlace !== undefined) {
        const selected = raw.selectedPlace;
        const selection = isRecord(selected) ? parseChatCourse({ places: [selected], edit: true })?.places[0] : undefined;
        if (!isRecord(selected) || !selection || typeof selected.visitId !== "string" || !selected.visitId || selected.visitId.length > 255
          || !["FOOD", "CAFE", "SPOT", "STADIUM", "STAY", "WALK", "INDOOR", "CONVENIENCE"].includes(String(selected.category))
          || !["BEFORE", "GAME", "AFTER"].includes(String(selected.phase))
          || typeof selected.label !== "string" || !selected.label || selected.label.length > 20) throw new ChatError("선택한 장소를 확인해 주세요.");
        const existingIndex = parsed.places.findIndex(p => p.visitId === selected.visitId);
        selectedPlace = existingIndex >= 0 ? { ...parsed.places[existingIndex], visitId: selected.visitId, label: (raw.places[existingIndex] as { label: string }).label }
          : { ...selection, visitId: selected.visitId, label: selected.label };
      }
      const rawPlaces = raw.places as { label: string }[];
      context.currentCourse = { places: parsed.places.map((p, i) => ({ ...p, visitId: p.visitId!, label: rawPlaces[i].label })),
        ...(selectedPlace ? { selectedPlace } : {}),
        ...(parsed.writerState ? { writerState: parsed.writerState } : {}),
        stadiumCode: raw.stadiumCode, travelMode: parsed.travelMode!, legModes: raw.legModes, ...(parsed.game ? { game: parsed.game } : {}),
        ...(parsed.progress ? { progress: parsed.progress } : {}) };
    }
    if (value.context.stadium !== undefined) {
      if (typeof value.context.stadium !== "string" || value.context.stadium.length > 100) {
        throw new ChatError("구장 이름을 확인해 주세요.");
      }
      context.stadium = value.context.stadium.trim();
    }
    if (value.context.intent !== undefined) {
      if (typeof value.context.intent !== "string" || !["route", "baseball", "stadium"].includes(value.context.intent)) throw new ChatError("대화 주제를 확인해 주세요.");
      context.intent = value.context.intent as ChatContext["intent"];
    }
    if (value.context.origin !== undefined) {
      const origin = value.context.origin;
      if (!isRecord(origin) || typeof origin.lat !== "number" || typeof origin.lng !== "number"
        || !Number.isFinite(origin.lat) || !Number.isFinite(origin.lng)
        || Math.abs(origin.lat) > 90 || Math.abs(origin.lng) > 180) throw new ChatError("출발지 좌표를 확인해 주세요.");
      context.origin = { lat: origin.lat, lng: origin.lng };
    }
    if (value.context.routePath !== undefined) {
      const path = value.context.routePath;
      if (!isRecord(path) || !Array.isArray(path.points) || path.points.length < 2 || path.points.length > 128
        || typeof path.label !== "string" || path.label.length > 80 || !["drawn", "directions"].includes(String(path.source))) throw new ChatError("선택한 경로를 확인해 주세요.");
      const points = path.points.map(point => {
        if (!isRecord(point) || typeof point.lat !== "number" || typeof point.lng !== "number" || !Number.isFinite(point.lat) || !Number.isFinite(point.lng)
          || Math.abs(point.lat) > 90 || Math.abs(point.lng) > 180) throw new ChatError("경로 좌표를 확인해 주세요.");
        return { lat: point.lat, lng: point.lng };
      });
      const breaks = path.breaks ?? [];
      if (!Array.isArray(breaks) || breaks.length > 127 || breaks.some(i => !Number.isInteger(i) || i < 1 || i >= points.length)) throw new ChatError("경로 구간을 확인해 주세요.");
      context.routePath = { points, breaks, label: path.label, source: path.source as "drawn" | "directions" };
    }
  }
  if (value.sessionId !== undefined && (typeof value.sessionId !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.sessionId))) throw new ChatError("채팅방 번호를 확인해 주세요.");
  // Only the documented fields reach a provider; client-supplied model/system settings are discarded.
  return { messages, ...(value.sessionId !== undefined ? { sessionId: value.sessionId as string } : {}), ...(context ? { context } : {}) };
}
