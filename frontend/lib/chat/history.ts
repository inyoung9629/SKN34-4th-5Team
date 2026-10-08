import type { ChatMessage, ChatToolCall } from "./types";
import { parsePlanning } from "./planning";
import { inheritCourseState, parseChatCourse, parseCoursePreferences } from "./course";
import { buildTimeline } from "./types";
import type { ChatAttachmentDto, ChatMessageDto, ChatToolCallDto } from "./wire";
import type { ChatAttachment } from "./types";

export const fromAttachmentDto = (item: ChatAttachmentDto): ChatAttachment => ({ id: item.id, kind: item.kind, name: item.name, contentType: item.content_type, size: item.size, width: item.width, height: item.height, url: item.url, createdAt: item.created_at });

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
export function restoreChatMessages(history: ChatMessageDto[], previous: ChatMessage[] = []): ChatMessage[] {
  let lastCourse: ChatMessage["course"];
  return history.map(item => {
    const tools = item.tools.map(fromToolDto), timeline = buildTimeline(item.steps ?? [], tools), planning = parsePlanning(item.planning);
    let course = item.role === "assistant" && item.status === "completed" && !item.answer_deleted ? parseChatCourse(item.course) : undefined;
    if (course) { course = inheritCourseState(course, lastCourse); lastCourse = course; }
    const priorCourse = previous.find(message => message.id === item.id && message.role === "assistant")?.course;
    // 완료 답변 ID는 불변이며 재생성 답변에는 새 ID가 부여된다. 같은 답변의 지도 되돌리기 상태를 유지한다.
    if (course && priorCourse) course = priorCourse;
    return {
      id: item.id, role: item.role, content: item.content, status: item.status,
      ...(item.attachments ? { attachments: item.attachments.map(fromAttachmentDto) } : {}),
      ...(item.tool_group_ids ? { toolGroupIds: item.tool_group_ids } : {}),
      ...(item.answer_deleted ? { answerDeleted: true } : {}),
      ...(item.feedback !== undefined ? { feedback: item.feedback } : {}),
      ...(planning ? { planning } : {}),
      ...(course ? { course } : {}),
      ...(item.role === "assistant" && item.status === "completed" && parseCoursePreferences(item.coursePreferences) ? { coursePreferences: parseCoursePreferences(item.coursePreferences) } : {}),
      ...(tools.length ? { tools } : {}), ...(timeline.length ? { timeline } : {}),
    };
  });
}
