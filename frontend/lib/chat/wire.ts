// Hand-typed wire DTOs for /api/v2/chat/ (backend/llm/serializer/message.py, views/sse.py).
// Message ids are server-issued positive integers kept from the v1 wire (serializer wire_history); sequence_no is
// the 1-based position in the returned list. done.message_id is the same id as a digit string.
import type { ChatPlanning } from "./planning";
import type { AnswerFeedback, ChatContext } from "./types";

export type ChatMessageStatus = "pending" | "completed" | "failed" | "stopped";
export type ChatToolStatus = "running" | "completed" | "failed";

export type ChatSessionDto = { id: string; title: string; created_at: string; updated_at: string };
export type ChatToolDetailDto = { args: Record<string, unknown>; result: string; messages?: Record<string, unknown>[] };
// kind/parent_id default to "tool"/null for older servers. detail is sent only to superusers on GET history.
export type ChatToolCallDto = {
  id: string; tool_name: string; status: ChatToolStatus;
  kind?: "tool" | "sub_agent"; parent_id?: string | null; title?: string; summary?: string; detail?: ChatToolDetailDto;
};
// Ordered process log of a turn (final answer excluded); tool steps reference `tools` by id.
export type ChatStepDto = { type: "text"; text: string; parent_id?: string | null } | { type: "tool"; id: string };
export type ChatAttachmentDto = { id: string; kind: "image" | "text" | "url"; name: string; content_type: string; size: number; width: number | null; height: number | null; url: string | null; created_at: string };
export type ChatMessageDto = {
  tool_group_ids?: string[];
  attachments?: ChatAttachmentDto[];
  coursePreferences?: unknown;
  answer_deleted?: boolean;
  feedback?: AnswerFeedback | null;
  id: number;
  sequence_no: number;
  role: "user" | "assistant";
  content: string;
  status: ChatMessageStatus;
  tools: ChatToolCallDto[];
  steps?: ChatStepDto[];
  planning?: ChatPlanning;
  course?: unknown; // Public coordinate payload, validated by parseChatCourse before use.
  created_at: string;
  updated_at: string;
};

export type ChatMessageRequestDto = { content: string; context?: ChatContext };
export type ChatMessageUpdateRequestDto = ChatMessageRequestDto & { message_id: number };
export type ChatMessageDeleteRequestDto = { message_id: number };

// serializer/message.py project_event()/done_payload(): delta{text} / tool{id, tool_name, status} /
// done{message_id, assistant_message, tools} / error{detail} / stopped{}.
export type ChatSseEvent =
  | { event: "delta"; data: { text: string; parent_id?: string } }
  | { event: "tool"; data: ChatToolCallDto }
  | { event: "planning"; data: ChatPlanning }
  | { event: "done"; data: { message_id: string; assistant_message: string; tools: ChatToolCallDto[]; steps?: ChatStepDto[]; course?: unknown } }
  | { event: "error"; data: { detail: string } }
  | { event: "stopped"; data: Record<string, never> };
