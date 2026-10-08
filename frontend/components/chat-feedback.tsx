"use client";

import { useId, useRef, useState } from "react";
import type { AnswerFeedback, ChatMessage } from "@/lib/chat/types";
import { FEEDBACK_REASONS } from "@/lib/chat/client";
import { useChat } from "./chat-provider";
import { Icon } from "./icons";
import "@/styles/chat-feedback.css";

export function ChatFeedback({ message, disabled }: { message: ChatMessage; disabled: boolean }) {
  const chat = useChat();
  const id = useId();
  const [editing, setEditing] = useState(false);
  const [reason, setReason] = useState(message.feedback?.reason ?? "");
  const [comment, setComment] = useState(message.feedback?.comment ?? "");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  if (message.role !== "assistant" || message.status !== "completed" || message.id === undefined) return null;
  async function save(value: AnswerFeedback | null) {
    if (busyRef.current) return;
    if (value && value.comment.length > 1000) { setError("의견은 1000자 이하로 입력해 주세요."); return; }
    busyRef.current = true; setBusy(true); setError(""); setNotice("");
    try {
      await chat.onFeedback(message.id!, value);
      setEditing(false); setNotice(value ? "평가를 저장했어요." : "평가를 취소했어요.");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "평가를 저장하지 못했어요. 다시 시도해 주세요."); }
    finally { busyRef.current = false; setBusy(false); }
  }
  return <section className="answer-feedback" aria-label="답변 평가" aria-busy={busy} onKeyDown={event => { if (event.key === "Enter") event.stopPropagation(); }}>
    <div className="answer-feedback-actions">
      <button type="button" aria-label="답변 좋아요" title="답변 좋아요" aria-pressed={message.feedback?.rating === "up"} disabled={disabled || busy} onClick={() => void save(message.feedback?.rating === "up" ? null : { rating: "up", reason: "", comment: "" })}><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M7 10v11H3V10h4Zm0 0 5-8a3 3 0 0 1 3 3v5h4a2 2 0 0 1 2 2l-1 7a2 2 0 0 1-2 2H7" /></svg></button>
      <button type="button" aria-label="답변 아쉬워요" title="답변 아쉬워요" aria-pressed={message.feedback?.rating === "down"} disabled={disabled || busy} onClick={() => { setReason(message.feedback?.reason ?? ""); setComment(message.feedback?.comment ?? ""); setEditing(true); setError(""); }}><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M7 14V3H3v11h4Zm0 0 5 8a3 3 0 0 0 3-3v-5h4a2 2 0 0 0 2-2l-1-7a2 2 0 0 0-2-2H7" /></svg></button>
      <button type="button" aria-label="이 답변만 삭제" title="이 답변만 지우고 질문과 다른 대화는 남겨요" disabled={disabled || busy} onClick={() => chat.onDeleteMessage(message.id!)}><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" /></svg></button>
      {message.feedback && <button type="button" aria-label="평가 취소" title="평가 취소" disabled={disabled || busy} onClick={() => void save(null)}><Icon name="close" size={16} /></button>}
    </div>
    {editing && <div style={{ display: "grid", gap: 8, marginTop: 10, maxWidth: 420 }}>
      <label htmlFor={`${id}-reason`}>사유 (선택)</label>
      <select id={`${id}-reason`} value={reason} disabled={disabled || busy} onKeyDown={event => { if (event.key === "Enter") event.preventDefault(); }} onChange={event => setReason(event.target.value)}><option value="">선택하지 않음</option>{Object.entries(FEEDBACK_REASONS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
      <label htmlFor={`${id}-comment`}>의견 (선택, 1000자 이하)</label>
      <textarea id={`${id}-comment`} value={comment} maxLength={1000} rows={3} disabled={disabled || busy} aria-describedby={`${id}-privacy`} onChange={event => setComment(event.target.value)} />
      <p id={`${id}-privacy`}>개인정보는 적지 마세요. 질문·답변과 평가를 관리자가 검토하며 자동 학습에는 사용하지 않아요.</p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}><button type="button" disabled={disabled || busy} onClick={() => void save({ rating: "down", reason, comment: comment.trim() })}>평가 저장</button><button type="button" disabled={busy} onClick={() => setEditing(false)}>닫기</button></div>
    </div>}
    {error && <p role="alert">{error}</p>}
    <span role="status">{busy ? "평가 저장 중…" : notice}</span>
  </section>;
}
