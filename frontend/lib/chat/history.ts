import type { ChatMessage, ChatToolCall } from "./types";
import { buildTimeline } from "./types";
import type { ChatMessageDto, ChatToolCallDto } from "./wire";

export const fromToolDto = (tool: ChatToolCallDto): ChatToolCall => ({
  id: tool.id, toolName: tool.tool_name, status: tool.status, kind: tool.kind ?? "tool", parentId: tool.parent_id ?? null,
  ...(tool.title ? { title: tool.title } : {}), ...(tool.summary ? { summary: tool.summary } : {}), ...(tool.detail ? { detail: tool.detail } : {}),
});

export function commitChatLoad(signal: AbortSignal, isCurrent: () => boolean, commit: () => void): boolean {
  if (signal.aborted || !isCurrent()) return false;
  commit();
  return true;
}

// The server returns every stored item in canonical turn order (project_history); preserve that
// order as-is, do not re-sort by id. A turn with no answer (pending/failed/stopped before any
// reply) has only the user row, which then carries that turn's tools; once answered, tools move
// to the assistant row and the user row's tools are empty.
export function restoreChatMessages(history: ChatMessageDto[]): ChatMessage[] {
  return history.map(item => {
    const tools = item.tools.map(fromToolDto), timeline = buildTimeline(item.steps ?? [], tools);
    return {
      id: item.id, role: item.role, content: item.content, status: item.status,
      ...(tools.length ? { tools } : {}), ...(timeline.length ? { timeline } : {}),
    };
  });
}
