import { parsePlanning, type ChatPlanning } from "./planning";
import { parseChatCourse, parseCoursePreferences } from "./course";
import { memberError, memberFetch } from "../member-auth-request";
import { isRecord, parseChatRequest } from "./validation";
import type { AnswerFeedback, ChatContext, ChatCourse, ChatReply, ChatStatus, CoursePreferences } from "./types";
import { MAX_REPLY_LENGTH, buildTimeline } from "./types";
import { fromAttachmentDto, fromToolDto } from "./history";
import type { ChatAttachment, ChatToolGroup } from "./types";
import type { ChatAttachmentDto } from "./wire";
import type { ChatMessageDto, ChatSessionDto, ChatStepDto, ChatToolCallDto } from "./wire";

// Members and guests share one API. Members authenticate with Bearer (memberFetch); guests send no
// Authorization header and are identified by the server-set HttpOnly guest_id cookie that same-origin
// fetch already includes by default.
export type ChatMode = "member" | "guest";

const SESSIONS = "/api/v2/chat/sessions/";
const REQUEST_TIMEOUT_MS = 55_000;
const MESSAGE_ID = /^[1-9]\d*$/;
const isMessageId = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) > 0;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const STATUS: Record<ChatMode, ChatStatus> = {
  member: { provider: "backend", model: "팀 챗봇", ready: true },
  guest: { provider: "guest", model: "팀 챗봇", ready: true },
};
export const GUEST_STATUS = STATUS.guest;

export type ChatStreamCallbacks = { onPlanning?: (payload: ChatPlanning) => void; onDelta?: (piece: string, parentId: string | null) => void; onTool?: (tool: ChatToolCallDto) => void };
export type ChatInputOptions = { toolGroupIds?: string[]; attachmentIds?: string[] };
export type ChatSendRequest = ChatInputOptions & { sessionId?: string; content: string; context?: ChatContext };
export type ChatEditRequest = ChatInputOptions & { sessionId: string; messageId: number; content: string; context?: ChatContext };

export class ChatClientError extends Error {
  constructor(message: string, public status: number, public uncertain = false, public sessionId?: string, public code?: string) { super(message); }
}

// Backend-owned usage DTO (GET /api/v2/chat/usage/, backend/llm/service/usage.py balance()). 1 credit = 1000 tokens.
export type ChatUsageDto = {
  plan: "member" | "guest"; period: string; timezone: string | null; resets_at: string | null; tokens_per_credit: number;
  limit_tokens: number; used_tokens: number; reserved_tokens: number; remaining_tokens: number; remaining_credits: string; can_send: boolean;
  active_turn: boolean; unknown_calls: number; accounting_state: "known" | "unknown";
};
export const USAGE_EXHAUSTED = "usage_exhausted";
export const USAGE_BUSY = "usage_busy";
const isCount = (value: unknown) => Number.isSafeInteger(value) && Number(value) >= 0;
const isUsage = (value: unknown): value is ChatUsageDto => isRecord(value) && (value.plan === "member" || value.plan === "guest") &&
  typeof value.period === "string" && (value.timezone === null || typeof value.timezone === "string") && (value.resets_at === null || typeof value.resets_at === "string") &&
  ["tokens_per_credit", "limit_tokens", "used_tokens", "reserved_tokens", "remaining_tokens"].every(key => isCount(value[key])) &&
  typeof value.remaining_credits === "string" && /^\d+\.\d{3}$/.test(value.remaining_credits) && typeof value.can_send === "boolean" && typeof value.active_turn === "boolean" && isCount(value.unknown_calls) &&
  (value.accounting_state === "known" || value.accounting_state === "unknown");

export async function fetchChatUsage(mode: ChatMode, signal?: AbortSignal): Promise<ChatUsageDto> {
  const value = await request(mode, "/api/v2/chat/usage/", { method: "GET" }, signal, readJson);
  if (!isUsage(value)) throw new ChatClientError("사용량 응답을 확인하지 못했어요.", 502);
  return value;
}

export class ChatStreamStoppedError extends ChatClientError {
  constructor(sessionId: string) { super("다른 변경으로 답변 생성이 중단됐어요.", 409, false, sessionId); }
}

function fallback(status: number) {
  if (status === 401) return "팀 계정 로그인을 확인해 주세요.";
  if (status === 403) return "이 대화를 이용할 권한이 없어요.";
  if (status === 404) return "대화를 찾지 못했어요. 새 대화를 시작해 주세요.";
  if (status === 402) return "사용 가능한 크레딧을 모두 사용했어요.";
  if (status === 429) return "요청이 많아요. 잠시 후 다시 시도해 주세요.";
  if (status >= 500) return "챗봇 서버에서 요청을 처리하지 못했어요.";
  return "입력 내용을 확인해 주세요.";
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

async function request<T>(mode: ChatMode, path: string, init: RequestInit, signal: AbortSignal | undefined, read: (response: Response) => Promise<T>, timeoutMs: number | null = REQUEST_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  if (signal?.aborted) abort();
  signal?.addEventListener("abort", abort, { once: true });
  // LLM 스트림은 총시간 제한 없이 외부 signal로만 중단한다.
  const timeout = timeoutMs === null ? undefined : window.setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  const mutating = init.method !== "GET";
  const fetcher = mode === "member" ? memberFetch : fetch;
  try {
    const response = await fetcher(path, { ...init, cache: "no-store", signal: controller.signal });
    if (!response.ok) {
      const data = await readJson(response).catch(() => null);
      throw new ChatClientError(
        response.status >= 500 ? fallback(response.status)
          : isRecord(data) && typeof data.detail === "string" && data.detail.trim()
            ? data.detail : memberError(data, fallback(response.status)),
        response.status,
        response.status >= 500 && mutating,
        undefined,
        isRecord(data) && (data.code === USAGE_EXHAUSTED || data.code === USAGE_BUSY) ? data.code : undefined,
      );
    }
    return await read(response);
  } catch (error) {
    if (error instanceof ChatClientError) throw error;
    if (error instanceof SyntaxError) throw new ChatClientError("응답을 읽지 못했어요.", 502, mutating);
    if (timedOut) throw new ChatClientError("응답 시간이 지났지만 서버 처리 여부는 확인할 수 없어요.", 504, mutating);
    if (controller.signal.aborted) throw new ChatClientError("요청이 중단됐어요.", 499, mutating);
    throw new ChatClientError("연결이 끊겨 서버 처리 여부를 확인할 수 없어요.", 502, mutating);
  } finally {
    window.clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}

// Total streamed delta text per reply (main + sub-agents): generous for multi-step replies, still stops runaway streams.
export const MAX_STREAM_LENGTH = MAX_REPLY_LENGTH * 8;

const json = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const sessionPath = (sessionId: string) => {
  if (!UUID.test(sessionId)) throw new ChatClientError("대화 번호를 확인해 주세요.", 400);
  return `${SESSIONS}${sessionId}/`;
};
const isSession = (value: unknown): value is ChatSessionDto => isRecord(value) && typeof value.id === "string" && UUID.test(value.id) && typeof value.title === "string";
const isTool = (value: unknown): value is ChatToolCallDto => isRecord(value) && typeof value.id === "string" && Boolean(value.id) &&
  typeof value.tool_name === "string" && Boolean(value.tool_name) && ["running", "completed", "failed"].includes(String(value.status)) &&
  (value.kind === undefined || value.kind === "tool" || value.kind === "sub_agent") && isParent(value.parent_id) &&
  (value.title === undefined || typeof value.title === "string") && (value.summary === undefined || typeof value.summary === "string") && (value.detail === undefined || isDetail(value.detail));
const isParent = (value: unknown) => value === undefined || value === null || (typeof value === "string" && Boolean(value));
const isDetail = (value: unknown) => isRecord(value) && isRecord(value.args) && typeof value.result === "string" &&
  (value.messages === undefined || (Array.isArray(value.messages) && value.messages.every(message => isRecord(message) && typeof message.role === "string" && typeof message.content === "string")));
const isStep = (value: unknown): value is ChatStepDto => isRecord(value) &&
  ((value.type === "text" && typeof value.text === "string" && isParent(value.parent_id)) || (value.type === "tool" && typeof value.id === "string" && Boolean(value.id)));
const isSteps = (value: unknown) => value === undefined || (Array.isArray(value) && value.every(isStep));
export const TOOL_GROUP_IDS = ["web_research", "schedule", "standings", "players", "baseball_stats", "rules", "stadium_info", "carry_in", "parking_transport", "community", "nearby_places", "tourism", "directions", "courses", "weather", "day_plan"];
const isGroups = (value: unknown): value is string[] => Array.isArray(value) && value.every(id => typeof id === "string" && TOOL_GROUP_IDS.includes(id)) && new Set(value).size === value.length;
const isAttachment = (value: unknown): value is ChatAttachmentDto => isRecord(value) && typeof value.id === "string" && UUID.test(value.id) && ["image", "text", "url"].includes(String(value.kind)) && typeof value.name === "string" && typeof value.content_type === "string" && isCount(value.size) && (value.width === null || isCount(value.width)) && (value.height === null || isCount(value.height)) && typeof value.created_at === "string" && (value.kind === "url" ? typeof value.url === "string" && /^https?:\/\//.test(value.url) : value.url === null || typeof value.url === "string" && new RegExp(`^/api/v2/chat/sessions/[0-9a-f-]{36}/attachments/${value.id}/$`, "i").test(value.url));
const isMessage = (value: unknown): value is ChatMessageDto => isRecord(value) && isMessageId(value.id) && isMessageId(value.sequence_no) &&
  (value.role === "user" || value.role === "assistant") && typeof value.content === "string" &&
  ["pending", "completed", "failed", "stopped"].includes(String(value.status)) &&
  Array.isArray(value.tools) && value.tools.every(isTool) && isSteps(value.steps) && typeof value.created_at === "string" && typeof value.updated_at === "string" &&
  (value.feedback === undefined || value.feedback === null || isAnswerFeedback(value.feedback)) &&
  (value.tool_group_ids === undefined || isGroups(value.tool_group_ids)) && (value.attachments === undefined || (Array.isArray(value.attachments) && value.attachments.every(isAttachment)));
// DELETE answers 204 with no body.
const readEmpty = async (response: Response) => { await response.text(); };

// Frames are `event: <name>\ndata: <json>\n\n` (backend/llm/views/sse.py): delta{text}* / tool{id,
// tool_name, status}* then exactly one terminal event: done{message_id, assistant_message, tools},
// error{detail}, or stopped{}. A stream that closes with none is a dropped connection.
async function readStream(response: Response, sessionId: string, callbacks: ChatStreamCallbacks): Promise<{ reply: string; assistantMessageId: number; tools: ChatToolCallDto[]; steps: ChatStepDto[]; planning?: ChatPlanning; course?: ChatCourse; coursePreferences?: CoursePreferences }> {
  if (!response.body || !response.headers.get("Content-Type")?.toLowerCase().startsWith("text/event-stream")) {
    throw new ChatClientError("스트림 응답을 확인하지 못했어요.", 502, true, sessionId);
  }
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffer = "", streamed = 0, result: { reply: string; assistantMessageId: number; tools: ChatToolCallDto[]; steps: ChatStepDto[]; planning?: ChatPlanning; course?: ChatCourse; coursePreferences?: CoursePreferences } | null = null;
  const consume = (frame: string) => {
    // SSE 주석은 연결 유지 신호다. 답변/도구/완료 이벤트로 취급하지 않는다.
    const fields = frame.split(/\r?\n/).filter(line => !line.startsWith(":"));
    if (fields.every(line => !line.trim())) return;
    const [eventLine, ...lines] = fields;
    const event = eventLine?.startsWith("event:") ? eventLine.slice(6).trim() : "";
    const raw = lines.filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
    let value: unknown;
    try { value = JSON.parse(raw); } catch { throw new ChatClientError("스트림 응답 형식이 올바르지 않아요.", 502, true, sessionId); }
    if (!isRecord(value) || result) throw new ChatClientError("스트림 응답 순서가 올바르지 않아요.", 502, true, sessionId);
    if (event === "delta") {
      // Deltas carry pre-tool and sub-agent text too, so they get the looser stream cap; done.assistant_message keeps MAX_REPLY_LENGTH.
      if (typeof value.text !== "string" || !isParent(value.parent_id) || streamed + value.text.length > MAX_STREAM_LENGTH) throw new ChatClientError("답변이 너무 길어요.", 502, true, sessionId);
      streamed += value.text.length;
      callbacks.onDelta?.(value.text, typeof value.parent_id === "string" ? value.parent_id : null);
      return;
    }
    if (event === "tool") {
      if (!isTool(value)) throw new ChatClientError("도구 호출 정보를 확인하지 못했어요.", 502, true, sessionId);
      callbacks.onTool?.(value);
      return;
    }
    if (event === "planning") {
      const payload = parsePlanning(value);
      if (payload) callbacks.onPlanning?.(payload);
      return; // Invalid optional UI falls back to the normal text answer.
    }
    if (event === "done") {
      if (typeof value.message_id !== "string" || !MESSAGE_ID.test(value.message_id) || !isMessageId(Number(value.message_id)) || typeof value.assistant_message !== "string" || value.assistant_message.length > MAX_REPLY_LENGTH ||
        !Array.isArray(value.tools) || !value.tools.every(isTool) || !isSteps(value.steps)) {
        throw new ChatClientError("최종 답변을 확인하지 못했어요.", 502, true, sessionId);
      }
      result = { reply: value.assistant_message, assistantMessageId: Number(value.message_id), tools: value.tools, steps: (value.steps as ChatStepDto[] | undefined) ?? [], planning: parsePlanning(value.planning), course: parseChatCourse(value.course), coursePreferences: parseCoursePreferences(value.coursePreferences) };
      return;
    }
    if (event === "error") {
      // The stream failed after the server attempted the turn; the final save may or may not have
      // committed, so the client can't assume persistence failed and must reconcile via history.
      throw new ChatClientError(typeof value.detail === "string" && value.detail && value.detail.length <= 200 ? value.detail : fallback(502), 502, true, sessionId,
        value.code === USAGE_EXHAUSTED ? USAGE_EXHAUSTED : undefined);
    }
    if (event === "stopped") throw new ChatStreamStoppedError(sessionId);
    throw new ChatClientError("알 수 없는 스트림 응답을 받았어요.", 502, true, sessionId);
  };
  try {
    while (!result) {
      const next = await reader.read();
      buffer += decoder.decode(next.value, { stream: !next.done });
      let boundary;
      while ((boundary = buffer.search(/\r?\n\r?\n/)) >= 0) {
        const frame = buffer.slice(0, boundary), separator = buffer.slice(boundary).startsWith("\r\n\r\n") ? 4 : 2;
        buffer = buffer.slice(boundary + separator);
        if (frame.trim()) consume(frame);
      }
      if (buffer.length > MAX_STREAM_LENGTH * 2) throw new ChatClientError("스트림 응답이 너무 길어요.", 502, true, sessionId);
      if (next.done) break;
    }
    const remainingFields = buffer.split(/\r?\n/).filter(line => !line.startsWith(":"));
    if (!result || remainingFields.some(line => line.trim())) throw new ChatClientError("답변이 끝나기 전에 연결이 끊겼어요.", 502, true, sessionId);
    return result;
  } finally { await reader.cancel().catch(() => undefined); }
}

export async function listChatSessions(mode: ChatMode, signal?: AbortSignal): Promise<ChatSessionDto[]> {
  const sessions = await request(mode, SESSIONS, { method: "GET" }, signal, readJson);
  if (!Array.isArray(sessions) || !sessions.every(isSession)) throw new ChatClientError("대화 목록 응답을 확인하지 못했어요.", 502);
  return sessions;
}

// For a guest, the first create issues the guest_id cookie.
export async function createChatSession(mode: ChatMode, title: string, signal?: AbortSignal): Promise<ChatSessionDto> {
  const room = await request(mode, SESSIONS, json("POST", { title }), signal, readJson);
  if (!isSession(room)) throw new ChatClientError("대화 생성 응답을 확인하지 못했어요.", 502, true);
  return room;
}

export async function renameChatSession(mode: ChatMode, sessionId: string, title: string, signal?: AbortSignal): Promise<ChatSessionDto> {
  const room = await request(mode, sessionPath(sessionId), json("PATCH", { title }), signal, readJson);
  if (!isSession(room) || room.id !== sessionId) throw new ChatClientError("대화 수정 응답을 확인하지 못했어요.", 502, true, sessionId);
  return room;
}

export async function deleteChatSession(mode: ChatMode, sessionId: string, signal?: AbortSignal): Promise<void> {
  await request(mode, sessionPath(sessionId), { method: "DELETE" }, signal, readEmpty);
}

export const FEEDBACK_REASONS = { incorrect: "정보가 부정확해요", irrelevant: "질문과 맞지 않아요", incomplete: "설명이 부족해요", other: "기타" };
export function isAnswerFeedback(value: unknown): value is AnswerFeedback {
  return isRecord(value) && (value.rating === "up" || value.rating === "down") && typeof value.reason === "string" &&
    (value.reason === "" || Object.hasOwn(FEEDBACK_REASONS, value.reason)) && typeof value.comment === "string" && value.comment.length <= 1000;
}
export async function saveAnswerFeedback(mode: ChatMode, sessionId: string, messageId: number, feedback: AnswerFeedback | null): Promise<AnswerFeedback | null> {
  if (!isMessageId(messageId) || (feedback !== null && !isAnswerFeedback(feedback))) throw new ChatClientError("평가 내용을 확인해 주세요.", 400);
  const result = await request(mode, `${sessionPath(sessionId)}feedback/`, json("PUT", { message_id: messageId, rating: feedback?.rating ?? null, reason: feedback?.reason ?? "", comment: feedback?.comment ?? "" }), undefined, readJson);
  if (!isRecord(result) || (result.feedback !== null && !isAnswerFeedback(result.feedback))) throw new ChatClientError("평가 응답을 확인하지 못했어요.", 502);
  return result.feedback;
}

export type AdminAnswerFeedback = AnswerFeedback & { id: number; session_id: string; answer_id: string; message_id: number; question: string; answer: string; metadata: Record<string, unknown>; created_at: string; updated_at: string };
const isAdminFeedback = (value: unknown): value is AdminAnswerFeedback => isRecord(value) && isMessageId(value.id) && isMessageId(value.message_id) &&
  typeof value.session_id === "string" && UUID.test(value.session_id) && typeof value.answer_id === "string" && typeof value.question === "string" && typeof value.answer === "string" && isRecord(value.metadata) && typeof value.created_at === "string" && typeof value.updated_at === "string" && isAnswerFeedback(value);
export async function fetchAdminFeedback(page: number, rating: string, reason: string, signal?: AbortSignal): Promise<{ count: number; results: AdminAnswerFeedback[] }> {
  const params = new URLSearchParams({ page: String(page), rating, reason });
  const result = await request("member", `/api/v2/chat/admin/feedback/?${params}`, { method: "GET" }, signal, readJson);
  if (!isRecord(result) || !isCount(result.count) || !Array.isArray(result.results) || !result.results.every(isAdminFeedback)) throw new ChatClientError("평가 목록을 확인하지 못했어요.", 502);
  return { count: Number(result.count), results: result.results };
}
export async function fetchAdminFeedbackDetail(id: number, signal?: AbortSignal): Promise<AdminAnswerFeedback> {
  if (!isMessageId(id)) throw new ChatClientError("평가 번호를 확인해 주세요.", 400);
  const result = await request("member", `/api/v2/chat/admin/feedback/${id}/`, { method: "GET" }, signal, readJson);
  if (!isAdminFeedback(result)) throw new ChatClientError("평가 상세를 확인하지 못했어요.", 502);
  return result;
}

export async function fetchChatHistory(mode: ChatMode, sessionId: string, signal?: AbortSignal): Promise<ChatMessageDto[]> {
  const messages = await request(mode, `${sessionPath(sessionId)}messages/`, { method: "GET" }, signal, readJson);
  if (!Array.isArray(messages) || !messages.every(isMessage)) throw new ChatClientError("대화 기록 응답을 확인하지 못했어요.", 502, false, sessionId);
  return messages;
}

/** Deletes the given user message and every later message in the session. */
export async function deleteChatMessages(mode: ChatMode, sessionId: string, messageId: number, signal?: AbortSignal): Promise<void> {
  if (!isMessageId(messageId)) throw new ChatClientError("메시지 번호를 확인해 주세요.", 400);
  await request(mode, `${sessionPath(sessionId)}messages/`, json("DELETE", { message_id: messageId }), signal, readEmpty);
}

export async function getChatStatus(mode: ChatMode, signal?: AbortSignal): Promise<ChatStatus> {
  await listChatSessions(mode, signal);
  return STATUS[mode];
}

// Reuses the shared request validator for content length and the structured context the v2 chain reads.
function parseInput(content: string, context?: ChatContext) {
  const parsed = parseChatRequest({ messages: [{ role: "user", content }], ...(context ? { context } : {}) });
  return { content: parsed.messages[0].content, ...(parsed.context ? { context: parsed.context } : {}) };
}

async function streamReply(mode: ChatMode, method: "POST" | "PUT", sessionId: string, body: unknown, signal: AbortSignal | undefined, callbacks: ChatStreamCallbacks): Promise<ChatReply> {
  try {
    const init = json(method, body);
    init.headers = { ...init.headers as Record<string, string>, Accept: "text/event-stream" };
    const result = await request(mode, `${sessionPath(sessionId)}messages/`, init, signal, response => readStream(response, sessionId, callbacks), null);
    const { steps, ...rest } = result, tools = result.tools.map(fromToolDto);
    return { ...STATUS[mode], ...rest, tools, timeline: buildTimeline(steps, tools), sessionId };
  } catch (error) {
    if (error instanceof ChatClientError && error.sessionId === undefined) error.sessionId = sessionId;
    throw error;
  }
}

export async function sendChatMessage(mode: ChatMode, body: ChatSendRequest, signal?: AbortSignal, callbacks: ChatStreamCallbacks = {}): Promise<ChatReply> {
  const input = { ...parseInput(body.content, body.context), ...inputOptions(body) };
  const sessionId = body.sessionId ?? (await createChatSession(mode, input.content.slice(0, 80), signal)).id;
  return streamReply(mode, "POST", sessionId, input, signal, callbacks);
}

function inputOptions(body: ChatInputOptions) {
  if (body.toolGroupIds !== undefined && !isGroups(body.toolGroupIds)) throw new ChatClientError("선택한 기능을 확인해 주세요.", 400);
  if (body.attachmentIds !== undefined && (!Array.isArray(body.attachmentIds) || body.attachmentIds.length > 10 || body.attachmentIds.some(id => !UUID.test(id)) || new Set(body.attachmentIds).size !== body.attachmentIds.length)) throw new ChatClientError("첨부 자료를 확인해 주세요.", 400);
  return { ...(body.toolGroupIds !== undefined ? { tool_group_ids: body.toolGroupIds } : {}), ...(body.attachmentIds !== undefined ? { attachment_ids: body.attachmentIds } : {}) };
}

export async function fetchChatToolGroups(mode: ChatMode, signal?: AbortSignal): Promise<ChatToolGroup[]> {
  const value = await request(mode, "/api/v2/chat/tool-groups/", { method: "GET" }, signal, readJson);
  if (!Array.isArray(value) || !value.every(item => isRecord(item) && typeof item.id === "string" && TOOL_GROUP_IDS.includes(item.id) && typeof item.label === "string")) throw new ChatClientError("기능 목록을 불러오지 못했어요.", 502);
  return value;
}

export function validateChatFile(file: File): ChatAttachment["kind"] {
  const image = /\.(jpe?g|png|webp)$/i.test(file.name) && ["image/jpeg", "image/png", "image/webp"].includes(file.type);
  const text = /\.(txt|md)$/i.test(file.name) && ["", "text/plain", "text/markdown", "text/x-markdown"].includes(file.type);
  if (!image && !text) throw new ChatClientError("JPEG·PNG·WebP 이미지 또는 TXT·MD 문서를 선택해 주세요.", 400);
  if (!file.size || file.size > (image ? 10 : 2) * 1024 * 1024) throw new ChatClientError(image ? "이미지는 10MB 이하로 첨부해 주세요." : "문서는 2MB 이하로 첨부해 주세요.", 400);
  return image ? "image" : "text";
}
export async function uploadChatAttachment(mode: ChatMode, sessionId: string, source: File | string, signal?: AbortSignal): Promise<ChatAttachment> {
  let init: RequestInit;
  if (typeof source === "string") {
    init = { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: source }) };
  } else {
    validateChatFile(source);
    const body = new FormData();
    body.append("file", source.type ? source : new File([source], source.name, { type: /\.md$/i.test(source.name) ? "text/markdown" : "text/plain", lastModified: source.lastModified }));
    init = { method: "POST", body };
  }
  const value = await request(mode, `${sessionPath(sessionId)}attachments/`, init, signal, readJson);
  if (!isAttachment(value)) throw new ChatClientError("첨부 응답을 확인하지 못했어요.", 502);
  return fromAttachmentDto(value);
}
export async function deleteChatAttachment(mode: ChatMode, sessionId: string, id: string): Promise<void> {
  if (!UUID.test(id)) throw new ChatClientError("첨부 번호를 확인해 주세요.", 400);
  await request(mode, `${sessionPath(sessionId)}attachments/${id}/`, { method: "DELETE" }, undefined, async () => undefined);
}
export async function fetchChatAttachmentBlob(mode: ChatMode, sessionId: string, id: string, signal?: AbortSignal): Promise<Blob> {
  if (!UUID.test(id)) throw new ChatClientError("첨부 번호를 확인해 주세요.", 400);
  return request(mode, `${sessionPath(sessionId)}attachments/${id}/`, { method: "GET" }, signal, response => response.blob());
}

/** Replaces a persisted user message: the server drops it and everything after, then streams a new answer. */
export async function editChatMessage(mode: ChatMode, body: ChatEditRequest, signal?: AbortSignal, callbacks: ChatStreamCallbacks = {}): Promise<ChatReply> {
  if (!isMessageId(body.messageId)) throw new ChatClientError("메시지 번호를 확인해 주세요.", 400);
  return streamReply(mode, "PUT", body.sessionId, { message_id: body.messageId, ...parseInput(body.content, body.context), ...inputOptions(body) }, signal, callbacks);
}
