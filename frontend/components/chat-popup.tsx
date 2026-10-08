"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { chatComposerContent } from "@/lib/chat/inline-urls";
import { MAX_MESSAGE_LENGTH } from "@/lib/chat/types";
import { useMemberAuth } from "@/lib/member-auth";
import { ChatAnswer } from "./chat-answer";
import { ChatQuestions, ChatUserContent, ChatWriterOffer } from "./chat-planning";
import { ChatFeedback } from "./chat-feedback";
import { ChatCourseCard } from "./chat-course-card";
import { ChatCoursePreferences } from "./chat-course-preferences";
import { ChatPending } from "./chat-pending";
import { ChatQueue } from "./chat-queue";
import { ChatProgress, ChatSubAgentStatus } from "./chat-progress";
import { useChat } from "./chat-provider";
import { Icon } from "./icons";
import { ChatAttachmentCards, ChatComposerTools } from "./chat-composer-tools";
import { ChatInlineInput } from "./chat-inline-input";
import "@/styles/chat-popup.css";

const SUGGESTIONS = [
  { icon: "route", label: "직관 코스 추천", text: "잠실에서 첫 직관을 해요. 경기 전후 코스를 추천해 주세요.", intent: "route" },
  { icon: "stadium", label: "구장 정보", text: "처음 구장에 갈 때 어떤 정보를 확인하면 좋을까요?", intent: "stadium" },
  { icon: "book", label: "쉬운 야구 규칙", text: "야구를 처음 보는 사람에게 기본 규칙을 쉽게 알려 주세요.", intent: "baseball" },
] as const;

type ChatPopupProps = {
  embedded?: boolean;
  title?: string;
  conversationLabel?: string | null;
  welcomeTitle?: string;
  welcomeDescription?: ReactNode | null;
  welcomeLink?: { href: string; label: string };
};

export function ChatPopup({
  embedded = false,
  title = "직관 도우미",
  conversationLabel = "나의 직관 이야기",
  welcomeTitle = "어떤 직관을 준비하고 있나요?",
  welcomeDescription = <>직관 코스부터 구장 정보, 야구 이야기까지.<br />궁금한 것을 편하게 물어보세요.</>,
  welcomeLink,
}: ChatPopupProps) {
  const chat = useChat();
  const { status: authStatus } = useMemberAuth();
  const inputRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const toolbarRef = useRef<HTMLDivElement>(null);
  const conversationButtonRef = useRef<HTMLButtonElement>(null);
  const [conversationsOpen, setConversationsOpen] = useState(false);
  const composingRef = useRef(false);
  const nearBottomRef = useRef(true);
  const lastConversationRef = useRef(chat.activeConversationId);
  const busy = Boolean(chat.pending);
  const available = (authStatus === "authenticated" || authStatus === "anonymous") && Boolean(chat.status?.ready) && !chat.statusLoading && !chat.statusError;
  const reserving = busy || chat.queued.length > 0;
  const sendLabel = chat.editingQueuedId !== null ? "예약 수정 저장" : chat.editingMessageId !== null ? "수정한 질문 보내기" : reserving ? "질문 예약" : "질문 보내기";
  const canSubmit = available && Boolean(chatComposerContent(chat.draft, chat.attachments)) && (chat.editingQueuedId !== null || chat.editingMessageId !== null || !reserving || chat.queued.length < 2);
  const empty = chat.messages.length === 0 && !chat.pending && !chat.failed;
  const demo = chat.status?.provider === "demo";
  const guest = authStatus === "anonymous";
  const titleId = embedded ? "writer-chat-title" : "chat-popup-title";
  const conversationId = embedded ? "writer-chat-conversation" : "chat-popup-conversation";
  const questionId = embedded ? "writer-chat-question" : "chat-popup-question";

  useEffect(() => {
    if (!conversationsOpen) return;
    function dismiss(event: PointerEvent) {
      if (event.target instanceof Node && !toolbarRef.current?.contains(event.target)) setConversationsOpen(false);
    }
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [conversationsOpen]);

  useEffect(() => {
    if (embedded) return;
    const input = inputRef.current;
    if (input && input.getAttribute("aria-disabled") !== "true") input.focus({ preventScroll: true });
    else closeRef.current?.focus({ preventScroll: true });
  }, [embedded]);

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 104)}px`;
  }, [chat.draft]);

  useEffect(() => {
    const changedConversation = lastConversationRef.current !== chat.activeConversationId;
    lastConversationRef.current = chat.activeConversationId;
    if (nearBottomRef.current || busy || changedConversation) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "instant" });
    }
  }, [chat.messages, chat.pending, chat.failed, chat.error, chat.activeConversationId, busy]);

  function send() {
    if (!canSubmit) return;
    nearBottomRef.current = true;
    chat.onSend();
  }

  return (
    <section className={`chat-popup${embedded ? " chat-popup-embedded" : ""}`} role={embedded ? "region" : "dialog"} aria-modal={embedded ? undefined : false} aria-labelledby={titleId} onKeyDown={event => {
      if (!embedded && event.key === "Escape" && !event.nativeEvent.isComposing && !composingRef.current) {
        event.preventDefault();
        event.stopPropagation();
        chat.onClosePopup();
      }
    }}>
      <header className="chat-popup-header">
        <span className="chat-popup-mark"><Icon name="sparkles" size={21} /></span>
        <div className="chat-popup-heading"><h2 id={titleId}>{title}</h2><span className={`chat-popup-connection${available ? " is-ready" : ""}`} aria-live="polite"><i />{chat.statusLoading ? "연결 확인 중" : demo ? "예시 대화" : available ? "직관 도우미 연결됨" : "연결 확인 필요"}</span></div>
        <div className="chat-popup-header-actions">
          <button type="button" className="chat-popup-icon-button" aria-label="채팅 크게 보기" title="채팅 크게 보기" onClick={chat.onExpand}><svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7" /></svg></button>
          {!embedded && <button ref={closeRef} type="button" className="chat-popup-icon-button" aria-label="직관 도우미 닫기" title="직관 도우미 닫기" onClick={chat.onClosePopup}><Icon name="close" size={20} /></button>}
        </div>
      </header>

      <div ref={toolbarRef} className="chat-popup-toolbar" onBlur={event => {
        if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) setConversationsOpen(false);
      }} onKeyDown={event => {
        if (conversationsOpen && event.key === "Escape" && !event.nativeEvent.isComposing) {
          event.preventDefault();
          event.stopPropagation();
          setConversationsOpen(false);
          conversationButtonRef.current?.focus();
        }
      }}>
        <button ref={conversationButtonRef} type="button" className="chat-popup-history-toggle" aria-label={conversationsOpen ? "대화 목록 닫기" : "대화 목록 열기"} aria-expanded={conversationsOpen} aria-controls={conversationId} title={conversationLabel ?? undefined} disabled={busy} onClick={() => setConversationsOpen(open => !open)}>
          <Icon name="menu" size={14} /><span>대화 목록</span><span className="chat-popup-history-count">{chat.conversations.length}</span><Icon name="chevron" size={12} className="chat-popup-history-chevron" />
        </button>
        {conversationsOpen && <nav id={conversationId} className="chat-popup-history" aria-label="대화 목록">
          <p className="chat-popup-history-heading">최근 대화</p>
          <ul>{chat.conversations.map(conversation => <li key={conversation.id} className={`chat-popup-history-row${conversation.id === chat.activeConversationId ? " is-current" : ""}`}>
            <button type="button" className="chat-popup-history-delete" aria-label={`${conversation.title} 대화 내역 지우기`} title="대화 내역 지우기" disabled={busy} onClick={() => {
              if (!window.confirm("해당 대화 내역을 지우시겠습니까?")) return;
              chat.onDeleteConversation(conversation.id);
              setConversationsOpen(false);
              conversationButtonRef.current?.focus();
            }}><Icon name="close" size={14} /></button>
            <button type="button" className="chat-popup-history-item" aria-current={conversation.id === chat.activeConversationId ? "true" : undefined} disabled={busy} title={conversation.title} onClick={() => {
              chat.onSelectConversation(conversation.id);
              setConversationsOpen(false);
              nearBottomRef.current = true;
              inputRef.current?.focus();
            }}><span>{conversation.title}</span>{conversation.id === chat.activeConversationId && <Icon name="check" size={14} />}</button>
          </li>)}</ul>
        </nav>}
        {<button type="button" className="chat-popup-new" disabled={busy} onClick={() => { setConversationsOpen(false); chat.onReset(); nearBottomRef.current = true; inputRef.current?.focus(); }}><span aria-hidden="true">+</span>새 대화</button>}
      </div>
      <ChatCoursePreferences />

      <div ref={scrollRef} className={`chat-popup-transcript${empty ? " is-empty" : ""}`} onScroll={event => { const element = event.currentTarget; nearBottomRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80; }}>
        {empty && <div className="chat-popup-welcome">
          <span className="chat-popup-welcome-mark"><Icon name="sparkles" size={24} /></span>
          <h3>{welcomeTitle}</h3>
          {welcomeDescription && <p>{welcomeDescription}</p>}
          {<div className="chat-popup-suggestions">{welcomeLink ? <Link href={welcomeLink.href}><Icon name="route" size={17} />{welcomeLink.label}</Link> : SUGGESTIONS.map(item => <button key={item.intent} type="button" onClick={() => { chat.onSuggestion(item.intent === "route" && chat.context?.stadium ? `${chat.context.stadium}에서 첫 직관을 해요. 경기 전후 코스를 추천해 주세요.` : item.text, item.intent); inputRef.current?.focus(); }}><Icon name={item.icon} size={17} />{item.label}</button>)}</div>}
        </div>}
        {chat.context?.stadium && <p className="chat-popup-context"><Icon name="pin" size={13} />{chat.context.stadium}에서의 하루</p>}
        {chat.context?.routePath && <p className="chat-popup-context"><Icon name="route" size={13} />{chat.context.routePath.label} 기준 · 구장 2.5km 이내</p>}
        <div className="chat-popup-messages" role="log" aria-label="직관 도우미 대화 내용" aria-live="polite" aria-relevant="additions">
          {chat.messages.map((message, index) => <article key={`${chat.activeConversationId}-${index}`} className={`chat-popup-message chat-popup-message-${message.role}`}>
            {message.role === "assistant" ? <><div className="chat-popup-assistant-label"><Icon name="sparkles" size={14} />직관 도우미</div><ChatProgress items={message.timeline ?? []} />{message.content && <ChatAnswer text={message.content} places={message.course?.places} />}{message.status && message.status !== "completed" && <p className="chat-popup-message-note">끝까지 만들지 못한 답변이에요.</p>}{message.course && <ChatCourseCard course={message.course} />}<ChatQuestions message={message} disabled={!available || busy || index !== chat.messages.length - 1} /><ChatFeedback key={`${chat.activeConversationId}-${message.id}`} message={message} disabled={!available || busy} /></> : <><span className="sr-only">나</span><ChatAttachmentCards items={(message.attachments ?? []).filter(item => !(item.kind === "text" && /^Pasted Text \d+\.txt$/.test(item.name) && message.content.includes(`첨부 참고 자료: ${item.name}`)))} /><div className="chat-popup-user-bubble"><ChatUserContent attachments={message.attachments} content={message.content} planning={chat.messages[index - 1]?.role === "assistant" ? chat.messages[index - 1].planning : undefined} /></div>{message.answerDeleted && <p className="chat-popup-message-note">답변을 삭제했어요.</p>}{message.status && message.status !== "completed" && <p className="chat-popup-message-note">답변을 받지 못한 질문이에요.</p>}{message.id !== undefined && available && !busy && <div className="chat-popup-message-actions"><button type="button" aria-label="이 질문 수정" title="수정하면 이 질문 이후의 대화가 지워져요" onClick={() => { chat.onEditMessage(message.id!); inputRef.current?.focus(); }}><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m16 3 5 5-13 13H3v-5L16 3ZM14 5l5 5" /></svg></button><button type="button" aria-label="이 질문부터 삭제" title="이 질문과 이후 대화를 모두 지워요" onClick={() => chat.onDeleteMessage(message.id!)}><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" /></svg></button></div>}</>}
          </article>)}
          {chat.failed && <article className="chat-popup-message chat-popup-message-user"><span className="sr-only">나</span><div className="chat-popup-user-bubble"><ChatUserContent content={chat.failed} planning={chat.messages.at(-1)?.role === "assistant" ? chat.messages.at(-1)?.planning : undefined} /></div></article>}
          {busy && <article className="chat-popup-message chat-popup-message-assistant"><div className="chat-popup-assistant-label"><Icon name="sparkles" size={14} />직관 도우미</div><ChatProgress items={chat.timeline} live /><ChatPending busy={busy} streaming={chat.streaming} className="chat-popup-thinking" /></article>}
        </div>
        <ChatWriterOffer />
        {chat.error && <div className="chat-popup-feedback is-error" role="alert"><p>{chat.error}</p><button type="button" onClick={chat.onRetry} disabled={busy || !available}>다시 시도</button></div>}
        {chat.statusError && <div className="chat-popup-feedback is-error" role="alert"><p>{chat.statusError}</p><button type="button" onClick={chat.onRefreshStatus} disabled={chat.statusLoading}>연결 다시 확인</button></div>}
        {!chat.statusError && chat.status && !chat.status.ready && !chat.statusLoading && <div className="chat-popup-feedback"><p>대화 연결을 준비하고 있어요.</p><button type="button" onClick={chat.onRefreshStatus}>연결 다시 확인</button></div>}
        {chat.notice && <p className="chat-popup-notice" role="status">{chat.notice}</p>}
      </div>

      {<div className="chat-popup-composer-area">
        {chat.context?.currentCourse?.selectedPlace && <p className="chat-popup-context"><Icon name="pin" size={13} />기준 장소: {chat.context.currentCourse.selectedPlace.name} · “여기 앞뒤에 추가해줘”</p>}
          <ChatSubAgentStatus items={busy ? chat.timeline : []} />
        <ChatQueue focusInput={() => inputRef.current?.focus()} />
        {chat.editingMessageId !== null && <p className="chat-popup-edit-banner" role="status">질문을 수정하고 있어요. 보내면 이 질문 이후의 대화는 지워져요. <button type="button" onClick={chat.onCancelEdit}>수정 취소</button></p>}
        <div className="chat-popup-composer" onDragOver={event => { if (event.dataTransfer.types.includes("Files")) event.preventDefault(); }} onDrop={event => { if (!event.dataTransfer.files.length) return; event.preventDefault(); event.stopPropagation(); if (!busy && available) chat.onAttach(Array.from(event.dataTransfer.files)); }} onPaste={event => { const images = Array.from(event.clipboardData.files).filter(file => file.type.startsWith("image/")); if (images.length && !busy && available) { event.preventDefault(); chat.onAttach(images); } }}>
          <ChatComposerTools disabled={false} available={available} hint={chat.draft.length > MAX_MESSAGE_LENGTH * .8 ? `${chat.draft.length}/${MAX_MESSAGE_LENGTH}` : busy ? "답변을 준비하고 있어요" : "야구가 궁금한 모든 순간"} actions={<>{busy && <button type="button" className="chat-popup-send chat-popup-stop" aria-label="답변 생성 중단" title="답변 받기 중단 (받던 답변은 저장되지 않아요)" onClick={event => { event.preventDefault(); chat.onCancel(); }}><span /></button>}<button type="button" className="chat-popup-send" aria-label={sendLabel} title={sendLabel} disabled={!canSubmit} onClick={send}><Icon name="arrow" size={19} /></button></>}>
          <label className="sr-only" htmlFor={questionId}>직관 도우미에게 질문</label>
          <ChatInlineInput id={questionId} inputRef={inputRef} disabled={false} available={available} onSend={send} onCompositionChange={value => { composingRef.current = value; }} />
          </ChatComposerTools>

        </div>
        <p className="chat-popup-footnote">{guest && !demo ? <><Link href="/login">로그인</Link>하면 계정에 대화가 저장돼요. 비회원 대화는 이 브라우저에서만 이어져요. </> : null}{demo ? "예시 답변이에요. 실제 검색 결과는 포함되지 않아요." : "일정과 구장 운영 정보는 방문 전 공식 안내를 확인해 주세요."}</p>
      </div>}
    </section>
  );
}
