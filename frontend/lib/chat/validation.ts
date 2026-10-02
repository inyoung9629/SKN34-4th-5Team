import { MAX_HISTORY_MESSAGES, MAX_MESSAGE_LENGTH, MAX_REPLY_LENGTH } from "./types";
import type { ChatContext, ChatMessage, ChatRequest } from "./types";

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
  }
  if (value.sessionId !== undefined && (typeof value.sessionId !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.sessionId))) throw new ChatError("채팅방 번호를 확인해 주세요.");
  // Only the documented fields reach a provider; client-supplied model/system settings are discarded.
  return { messages, ...(value.sessionId !== undefined ? { sessionId: value.sessionId as string } : {}), ...(context ? { context } : {}) };
}
