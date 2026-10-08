"use client";

import { useChat } from "./chat-provider";
import "@/styles/chat-queue.css";

export function ChatQueue({ focusInput }: { focusInput: () => void }) {
  const chat = useChat();
  if (!chat.queued.length) return null;
  return <section className="chat-queue" aria-label="예약한 질문">
    <div className="chat-queue-heading">
      <span role="status">{chat.queuePaused ? "예약 일시 정지" : chat.queueWaitingForServer ? "이전 요청 처리 대기" : "다음에 보낼 질문"} <b>{chat.queued.length}/2</b></span>
      {chat.queuePaused && <button type="button" onClick={chat.onResumeQueue} disabled={chat.editingQueuedId !== null}>예약 계속</button>}
    </div>
    <ol>{chat.queued.map((item, index) => <li key={item.id} className={chat.editingQueuedId === item.id ? "is-editing" : undefined}>
      <span className="chat-queue-number">{index + 1}</span>
      <span className="chat-queue-text" title={item.content}>{item.content}</span>
      {chat.queueSendingId === item.id ? <span className="chat-queue-sending">전송 중</span> : <span className="chat-queue-actions">
        <button type="button" aria-label={`${index + 1}번째 예약 수정`} onClick={() => { chat.onEditQueued(item.id); focusInput(); }}>수정</button>
        <button type="button" aria-label={`${index + 1}번째 예약 취소`} onClick={() => chat.onRemoveQueued(item.id)}>취소</button>
      </span>}
    </li>)}</ol>
    {chat.editingQueuedId !== null
      ? <p>입력창에서 고친 뒤 저장해 주세요. <button type="button" onClick={chat.onCancelQueuedEdit}>수정 취소</button></p>
      : <p>{chat.queuePaused ? "예약 내용은 유지돼요. 준비되면 계속해 주세요." : chat.queueWaitingForServer
        ? "이전 요청이 서버에서 아직 처리 중이에요. 끝나면 자동으로 전송해요." : "답변이 끝나면 순서대로 전송해요."}</p>}
  </section>;
}
