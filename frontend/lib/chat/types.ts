export type ChatCoursePhase = "BEFORE" | "GAME" | "AFTER";
export type ChatCoursePlace = {
  phase: ChatCoursePhase; name: string; lat: number; lng: number;
  // STAY·WALK·INDOOR 는 백엔드가 카카오 실시간 조회로 더한 종류 (RAG 에 없는 숙박·산책·실내놀거리)
  category: "FOOD" | "CAFE" | "SPOT" | "STADIUM" | "STAY" | "WALK" | "INDOOR"; placeId?: string; address?: string;
  reason?: string; time?: string; stayMin?: number;
};
/** 챗봇이 짠 코스. 백엔드 done 이벤트의 places·travel·coursePayload 에서 온다 (lib/chat/course.ts). */
export type ChatCourse = {
  places: ChatCoursePlace[];
  stadiumCode?: string;
  travelMode?: "walk" | "car" | "transit";
  travelLabel?: string;
  summary?: string;
  notes: string[];
  title?: string;
  content?: string;
};
export type ChatToolStatus = "running" | "completed" | "failed";
// serializer/message.py _public_tool(). detail (args/result/sub-agent messages) only arrives for superusers.
export type ChatToolCall = {
  id: string; toolName: string; status: ChatToolStatus; kind: "tool" | "sub_agent"; parentId: string | null;
  title?: string; summary?: string; detail?: { args: Record<string, unknown>; result: string; messages?: Record<string, unknown>[] };
};
export type ChatMessageStatus = "pending" | "completed" | "failed" | "stopped";
// id·status·tools 는 서버에 저장된 메시지에만 있다 (id 는 서버가 만든 양의 정수). course 는 화면 표시용이다.
export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  id?: number;
  status?: ChatMessageStatus;
  course?: ChatCourse;
  tools?: ChatToolCall[];
  timeline?: ChatTimelineItem[];
};
export type ChatOrigin = { lat: number; lng: number };
// origin: 코스 작성 화면에서 지도에 찍은 출발지. 백엔드 코스 챗봇이 이 지점부터 이어서 코스를 짠다.
export type ChatContext = { stadium?: string; intent?: "route" | "baseball" | "stadium"; origin?: ChatOrigin };
export type ChatRequest = { messages: ChatMessage[]; sessionId?: string; context?: ChatContext };
export type ChatStatus = { provider: "demo" | "openai" | "backend" | "guest"; model: string; ready: boolean };
export type ChatReply = ChatStatus & {
  reply: string;
  sessionId?: string;
  assistantMessageId?: number;
  tools?: ChatToolCall[];
  timeline?: ChatTimelineItem[];
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
