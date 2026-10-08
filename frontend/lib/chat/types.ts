import type { ChatPlanning } from "./planning";
import type { RouteDraftData } from "../route-draft";
export type CourseWriterState = { title: string; origin: ChatOrigin | null; completed: boolean };
export type ChatCoursePhase = "BEFORE" | "GAME" | "AFTER";
export type ChatCoursePlace = {
  phase: ChatCoursePhase; name: string; lat: number; lng: number;
  // STAY·WALK·INDOOR 는 백엔드가 카카오 실시간 조회로 더한 종류 (RAG 에 없는 숙박·산책·실내놀거리)
  category: "FOOD" | "CAFE" | "SPOT" | "STADIUM" | "STAY" | "WALK" | "INDOOR" | "CONVENIENCE"; placeId?: string; address?: string;
  reason?: string; time?: string; until?: string; completed?: boolean; stayMin?: number; stayOverride?: number; placeUrl?: string; visitId?: string;
};
export type CourseProgress = { startMinute?: number; gameEndMinute?: number };
export type CoursePreferences = { conditions: string[]; lockedPlaces: string[]; rejectedPlaces: string[] };
/** 챗봇이 짠 코스. 백엔드 done 이벤트의 places·travel·coursePayload 에서 온다 (lib/chat/course.ts). */
export type ChatCourse = {
  writerState?: CourseWriterState;
  /** 소유 대화의 답변과 연결되는 기기 내 초안. 서버 입력에서는 제외한다. */
  writerKey?: string;
  writerDraft?: RouteDraftData;
  places: ChatCoursePlace[];
  origin?: { lat: number; lng: number; name?: string };
  entryPoint?: { lat: number; lng: number };
  approachNotice?: string;
  stadiumCode?: string;
  travelMode?: "walk" | "car" | "transit";
  travelLabel?: string;
  summary?: string;
  timeWarning?: string;
  notes: string[];
  title?: string;
  content?: string;
  game?: { date: string; time: string };
  progress?: CourseProgress;
  edit?: boolean;
  legModes?: Record<string, "walk" | "car" | "transit">;
};
export type ChatToolStatus = "running" | "completed" | "failed";
// serializer/message.py _public_tool(). detail (args/result/sub-agent messages) only arrives for superusers.
export type ChatToolCall = {
  id: string; toolName: string; status: ChatToolStatus; kind: "tool" | "sub_agent"; parentId: string | null;
  title?: string; summary?: string; detail?: { args: Record<string, unknown>; result: string; messages?: Record<string, unknown>[] };
};
export type ChatMessageStatus = "pending" | "completed" | "failed" | "stopped";
// id·status·tools 는 서버에 저장된 메시지에만 있다 (id 는 서버가 만든 양의 정수). course 는 화면 표시용이다.
export type AnswerFeedback = { rating: "up" | "down"; reason: string; comment: string };
export type ChatAttachment = { id: string; kind: "image" | "text" | "url"; name: string; contentType: string; size: number; width: number | null; height: number | null; url: string | null; createdAt: string };
export type ChatAttachmentDraft = { key: string; name: string; kind: ChatAttachment["kind"]; size: number; file?: File; sourceUrl?: string; inlineText?: string; pastedText?: string; preview?: string; attachment?: ChatAttachment; state: "uploading" | "ready" | "failed" | "cancelled"; error?: string };
export type ChatToolGroup = { id: string; label: string };
export type ChatMessage = {
  attachments?: ChatAttachment[];
  toolGroupIds?: string[];
  coursePreferences?: CoursePreferences;
  answerDeleted?: boolean;
  feedback?: AnswerFeedback | null;
  role: "user" | "assistant";
  content: string;
  id?: number;
  status?: ChatMessageStatus;
  course?: ChatCourse;
  tools?: ChatToolCall[];
  timeline?: ChatTimelineItem[];
  planning?: ChatPlanning;
};
export type ChatOrigin = { lat: number; lng: number; name?: string };
export type ChatRoutePath = { points: ChatOrigin[]; breaks?: number[]; label: string; source: "drawn" | "directions" };
// origin: 코스 작성 화면에서 지도에 찍은 출발지. 백엔드 코스 챗봇이 이 지점부터 이어서 코스를 짠다.
export type ChatCurrentCourse = {
  writerState?: CourseWriterState;
  /** 클릭한 방문지 또는 아직 담지 않은 장소. 현재 코스와 별도로 전달한다. */
  selectedPlace?: ChatCoursePlace & { visitId: string; label: string };
  places: (ChatCoursePlace & { visitId: string; label: string })[];
  stadiumCode: string; travelMode: "walk" | "car" | "transit";
  legModes: Record<string, "walk" | "car" | "transit">;
  game?: ChatCourse["game"];
  progress?: CourseProgress;
};
export type ChatContext = { stadium?: string; intent?: "route" | "baseball" | "stadium"; origin?: ChatOrigin; routePath?: ChatRoutePath; currentCourse?: ChatCurrentCourse };
export type ChatRequest = { messages: ChatMessage[]; sessionId?: string; context?: ChatContext };
export type ChatStatus = { provider: "demo" | "openai" | "backend" | "guest"; model: string; ready: boolean };
export type ChatReply = ChatStatus & {
  coursePreferences?: CoursePreferences;
  reply: string;
  course?: ChatCourse;
  sessionId?: string;
  assistantMessageId?: number;
  tools?: ChatToolCall[];
  timeline?: ChatTimelineItem[];
  planning?: ChatPlanning;
};

export const MAX_MESSAGE_LENGTH = 2000;
export const MAX_HISTORY_MESSAGES = 12;
export const MAX_REPLY_LENGTH = 8000;
export const MAX_REQUEST_BYTES = 64000;

// Process log of one turn in arrival order: live from SSE, or rebuilt from done/history `steps` + `tools`.
// parentId groups a sub-agent's inner text/tools under that sub-agent's tool call; null = main agent.
export type ChatTimelineItem = { kind: "text"; text: string; parentId: string | null } | { kind: "tools"; tools: ChatToolCall[] };
const parentOf = (item: ChatTimelineItem) => item.kind === "text" ? item.parentId : item.tools[0].parentId ?? null;
/** Appends a text piece or tool event: it coalesces with the latest item of the same parent and kind, a known tool id updates in place. */
export function appendTimeline(items: ChatTimelineItem[], event: string | ChatToolCall, parentId: string | null = null): ChatTimelineItem[] {
  if (typeof event !== "string" && items.some(item => item.kind === "tools" && item.tools.some(tool => tool.id === event.id))) {
    return items.map(item => item.kind === "tools" ? { kind: "tools", tools: item.tools.map(tool => tool.id === event.id ? event : tool) } : item);
  }
  if (event === "") return items;
  const parent = typeof event === "string" ? parentId : event.parentId ?? null;
  const index = items.findLastIndex(item => parentOf(item) === parent), last = items[index];
  if (typeof event === "string") {
    return last?.kind === "text" ? items.with(index, { ...last, text: last.text + event }) : [...items, { kind: "text", text: event, parentId: parent }];
  }
  return last?.kind === "tools" ? items.with(index, { kind: "tools", tools: [...last.tools, event] }) : [...items, { kind: "tools", tools: [event] }];
}
/** Rebuilds a stored turn's timeline; without steps (older server) it is the tools in order. */
export function buildTimeline(steps: ({ type: "text"; text: string; parent_id?: string | null } | { type: "tool"; id: string })[], tools: ChatToolCall[]): ChatTimelineItem[] {
  let items: ChatTimelineItem[] = [];
  const used = new Set<string>();
  for (const step of steps) {
    const tool = step.type === "tool" ? tools.find(item => item.id === step.id) : undefined;
    if (step.type === "text") items = appendTimeline(items, step.text, step.parent_id ?? null);
    else if (tool) { used.add(tool.id); items = appendTimeline(items, tool); }
  }
  for (const tool of tools) if (!used.has(tool.id)) items = appendTimeline(items, tool);
  return items;
}
