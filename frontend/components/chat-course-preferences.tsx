"use client";

import { useChat } from "./chat-provider";
import "@/styles/chat-course-preferences.css";

export function ChatCoursePreferences() {
  const chat = useChat();
  const value = chat.messages.findLast(message => message.role === "assistant" && message.coursePreferences)?.coursePreferences;
  if (!value) return null;
  const items = [
    ...value.conditions.map(text => ({ label: text, request: `코스 조건 '${text}'을 해제해줘` })),
    ...value.lockedPlaces.map(name => ({ label: `${name} 고정`, request: `${name} 고정을 해제해줘` })),
    ...value.rejectedPlaces.map(name => ({ label: `${name} 제외`, request: `${name} 장소 제외를 해제해줘. 다시 추천해도 돼` })),
  ];
  if (!items.length) return null;
  return <details className="chat-course-preferences">
    <summary>이 대화의 코스 조건 <span>{items.length}</span></summary>
    <div className="chat-course-preference-list">{items.map((item, index) => <button type="button" key={`${item.label}:${index}`} disabled={Boolean(chat.pending)}
      title="누르면 해제 요청을 입력해요" aria-label={`${item.label} 해제 요청 입력`} onClick={() => chat.onDraftChange(item.request)}>{item.label}<span aria-hidden="true">×</span></button>)}</div>
    <p>조건을 누르면 해제 요청을 입력할 수 있어요. 새 대화에는 이어지지 않아요.</p>
  </details>;
}
