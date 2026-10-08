"use client";

import Link from "next/link";
import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { chatComposerContent } from "@/lib/chat/inline-urls";
import { MAX_MESSAGE_LENGTH } from "@/lib/chat/types";
import { useMemberAuth } from "@/lib/member-auth";
import { useChat } from "./chat-provider";
import { Icon } from "./icons";
import { ChatAttachmentCards, ChatComposerTools } from "./chat-composer-tools";
import { ChatInlineInput } from "./chat-inline-input";
import { ChatAnswer } from "./chat-answer";
import { ChatQuestions, ChatUserContent, ChatWriterOffer } from "./chat-planning";
import { ChatFeedback } from "./chat-feedback";
import { ChatCourseCard } from "./chat-course-card";
import { ChatCoursePreferences } from "./chat-course-preferences";
import { ChatPending } from "./chat-pending";
import { ChatProgress, ChatSubAgentStatus } from "./chat-progress";
import { ChatUsage } from "./chat-usage";
import { ChatQueue } from "./chat-queue";
import "@/styles/chat-workspace.css";

const SUGGESTIONS = [
  { icon: "route", label: "나에게 맞는 직관 코스", text: "잠실에서 첫 직관을 해요. 경기 전후 코스를 추천해 주세요.", intent: "route" },
  { icon: "stadium", label: "처음 가는 구장이 궁금해요", text: "처음 구장에 갈 때 어떤 정보를 확인하면 좋을까요?", intent: "stadium" },
  { icon: "book", label: "야구 규칙 쉽게 알아보기", text: "야구를 처음 보는 사람에게 기본 규칙을 쉽게 알려 주세요.", intent: "baseball" },
] as const;

export function ChatWorkspace() {
  const chat = useChat();
  const { status: authStatus, user } = useMemberAuth();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const settingsRef = useRef<HTMLDialogElement>(null);
  const settingsOpenerRef = useRef<HTMLButtonElement | null>(null);
  const settingsFromDrawerRef = useRef(false);
  const profileRef = useRef<HTMLButtonElement>(null);
  const mobileProfileRef = useRef<HTMLButtonElement>(null);
  const profileName = authStatus === "authenticated" ? user?.nickname?.trim() || user?.first_name?.trim() || user?.username?.trim() || "회원" : authStatus === "anonymous" ? "게스트" : authStatus === "loading" ? "계정 확인 중" : "계정 확인 불가";
  const profileAvatar = authStatus === "authenticated" && user?.avatar ? user.avatar : "/images/default-avatar.svg";
  const usageMode = authStatus === "authenticated" && user ? "member" : null;
  const usageIdentity = usageMode === "member" ? `member:${user!.id}` : authStatus;
  const inputRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const drawerRef = useRef<HTMLDialogElement>(null);
  const menuRef = useRef<HTMLButtonElement>(null);
  const minimizeRef = useRef<HTMLButtonElement>(null);
  const composingRef = useRef(false);
  const nearBottomRef = useRef(true);
  const lastConversationRef = useRef(chat.activeConversationId);
  const drawerFocusRef = useRef<"menu" | "composer">("menu");
  const busy = Boolean(chat.pending);
  const available = (authStatus === "authenticated" || authStatus === "anonymous") && Boolean(chat.status?.ready) && !chat.statusLoading && !chat.statusError;
  const reserving = busy || chat.queued.length > 0;
  const sendLabel = chat.editingQueuedId !== null ? "예약 수정 저장" : chat.editingMessageId !== null ? "수정한 질문 보내기" : reserving ? "질문 예약" : "질문 보내기";
  const canSubmit = available && Boolean(chatComposerContent(chat.draft, chat.attachments)) && (chat.editingQueuedId !== null || chat.editingMessageId !== null || !reserving || chat.queued.length < 2);
  const empty = chat.messages.length === 0 && !chat.pending && !chat.failed;
  const demo = chat.status?.provider === "demo";
  const guest = authStatus === "anonymous";
  const activeTitle = chat.conversations.find(conversation => conversation.id === chat.activeConversationId)?.title;

  useEffect(() => {
    const input = inputRef.current;
    if (input?.getAttribute("aria-disabled") === "true") minimizeRef.current?.focus({ preventScroll: true });
    else input?.focus({ preventScroll: true });
  }, []);

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 176)}px`;
  }, [chat.draft]);

  useEffect(() => {
    const changedConversation = lastConversationRef.current !== chat.activeConversationId;
    lastConversationRef.current = chat.activeConversationId;
    if (nearBottomRef.current || busy || changedConversation) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "instant" });
    }
  }, [chat.messages, chat.pending, chat.failed, chat.error, chat.activeConversationId, busy]);

  function closeDrawer(focusComposer = false) {
    drawerFocusRef.current = focusComposer ? "composer" : "menu";
    drawerRef.current?.close();
  }

  function startNewChat() {
    chat.onReset();
    closeDrawer(true);
    nearBottomRef.current = true;
    inputRef.current?.focus();
  }

  function send() {
    if (!canSubmit) return;
    nearBottomRef.current = true;
    chat.onSend();
  }

  useEffect(() => {
    if (usageMode) return;
    settingsFromDrawerRef.current = false;
    settingsOpenerRef.current = null;
    settingsRef.current?.close();
  }, [usageMode]);

  function openSettings(opener: HTMLButtonElement, mobile: boolean) {
    if (!usageMode) return;
    settingsOpenerRef.current = opener;
    settingsFromDrawerRef.current = mobile;
    if (mobile) drawerRef.current?.close();
    else { setSettingsOpen(true); settingsRef.current?.showModal(); }
  }

  function onDrawerClose() {
    if (drawerRef.current?.open || settingsRef.current?.open) return;
    if (settingsFromDrawerRef.current && usageMode) {
      setSettingsOpen(true);
      settingsRef.current?.showModal();
    } else if (drawerFocusRef.current === "composer") inputRef.current?.focus();
    else menuRef.current?.focus();
  }

  function onSettingsClose() {
    if (settingsRef.current?.open) return;
    setSettingsOpen(false);
    const fromDrawer = settingsFromDrawerRef.current;
    settingsFromDrawerRef.current = false;
    if (!usageMode) {
      if (window.matchMedia("(max-width: 767px)").matches) menuRef.current?.focus();
      else inputRef.current?.focus();
    } else if (fromDrawer && window.matchMedia("(max-width: 767px)").matches) {
      drawerRef.current?.showModal();
      mobileProfileRef.current?.focus();
    } else if (window.matchMedia("(max-width: 767px)").matches) menuRef.current?.focus();
    else (fromDrawer ? profileRef.current : settingsOpenerRef.current)?.focus();
  }

  function sidebarContent(mobile = false) {
    return (
      <>
        <div className="workspace-sidebar-heading">
          <Link href="/" className="workspace-brand" aria-label="KBO 홈으로" onClick={() => closeDrawer()}>KBO<span /></Link>
          {mobile && <button type="button" className="workspace-icon-button" aria-label="대화 목록 닫기" onClick={() => closeDrawer()}><Icon name="close" size={21} /></button>}
        </div>
        {<button type="button" className="workspace-new-chat" onClick={startNewChat} disabled={busy}>
          <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 5H5v14h14v-7M10 14l1-4 8-8 3 3-8 8-4 1ZM17 4l3 3" /></svg>
          새 대화
        </button>}
        <div className="workspace-history">
          <h2>최근 대화</h2>
          {chat.conversations.length ? <nav aria-label="최근 대화">{chat.conversations.map(conversation => (
            <div key={conversation.id} className={`workspace-history-row${conversation.id === chat.activeConversationId ? " is-current" : ""}`}>
              <button type="button" className={`workspace-history-item${conversation.id === chat.activeConversationId ? " is-current" : ""}`} aria-current={conversation.id === chat.activeConversationId ? "true" : undefined} disabled={busy} title={conversation.title} onClick={() => { chat.onSelectConversation(conversation.id); closeDrawer(); }}>
                <span>{conversation.title}</span>
              </button>
              {/* 마우스를 올리면 오른쪽에 나타나는 삭제 버튼 */}
              <button type="button" className="workspace-history-delete" aria-label={`${conversation.title} 대화 내역 지우기`} title="대화 내역 지우기" disabled={busy && conversation.id === chat.activeConversationId} onClick={() => { if (window.confirm("해당 대화 내역을 지우시겠습니까?")) chat.onDeleteConversation(conversation.id); }}>
                <Icon name="close" size={14} />
              </button>
            </div>
          ))}</nav> : <p className="workspace-history-empty">함께 나눈 이야기가<br />여기에 모여요.</p>}
        </div>
        {guest ? <Link href="/login" className="workspace-login" onClick={() => closeDrawer()}><Icon name="login" size={20} /><span>로그인</span></Link> : usageMode ? <button ref={mobile ? mobileProfileRef : profileRef} type="button" className="workspace-profile" aria-label={`${profileName} 설정 열기`} aria-haspopup="dialog" onClick={event => openSettings(event.currentTarget, mobile)}>
          <Image key={profileAvatar} src={profileAvatar} alt="" width={36} height={36} unoptimized onError={event => { event.currentTarget.src = "/images/default-avatar.svg"; }} />
          <span><strong>{profileName}</strong><small>{user?.is_staff || user?.is_superuser ? "관리자" : "일반 유저"}</small></span>
        </button> : <button type="button" className="workspace-profile" disabled><span role="status">{profileName}</span></button>}
      </>
    );
  }

  return (
    <div className="chat-workspace">
      <aside className="workspace-sidebar" aria-label="직관 도우미 메뉴">{sidebarContent()}</aside>
      <dialog ref={drawerRef} className="workspace-drawer" aria-label="직관 도우미 메뉴" onClose={onDrawerClose} onCancel={() => { drawerFocusRef.current = "menu"; }} onClick={event => { if (event.target === event.currentTarget) closeDrawer(); }}>
        <div className="workspace-drawer-content">{sidebarContent(true)}</div>
      </dialog>
      <dialog ref={settingsRef} className="workspace-settings" aria-labelledby="workspace-settings-title" onClose={onSettingsClose} onClick={event => { if (event.target === event.currentTarget) settingsRef.current?.close(); }}>
        {usageMode && <><div className="workspace-settings-heading"><h2 id="workspace-settings-title">설정</h2><button type="button" className="workspace-icon-button" aria-label="설정 닫기" autoFocus onClick={() => settingsRef.current?.close()}><Icon name="close" size={21} /></button></div>
        <section aria-labelledby="workspace-account-title">
          <h3 id="workspace-account-title">계정</h3>
          <div className="workspace-account-row">
            <Image key={profileAvatar} src={profileAvatar} alt="" width={36} height={36} unoptimized onError={event => { event.currentTarget.src = "/images/default-avatar.svg"; }} />
            <strong className="workspace-account-name">{profileName}</strong>
            <div className="workspace-account-actions">
              <Link href="/mypage?tab=profile" className="workspace-icon-button" aria-label="회원 정보 관리" title="회원 정보 관리"><Icon name="book" size={20} /></Link>
            </div>
          </div>
        </section>
        {settingsOpen && <ChatUsage key={usageIdentity} mode={usageMode} refreshKey={busy ? "busy" : chat.messages.length} />}
        <section className="workspace-subscription-row" aria-labelledby="workspace-subscription-title"><h3 id="workspace-subscription-title">구독</h3><p className="workspace-usage-soon">구독 요금제는 준비 중이에요.</p></section></>}
      </dialog>
      <main className="workspace-main">
        <header className="workspace-topbar">
          <button ref={menuRef} type="button" className="workspace-icon-button workspace-menu-button" aria-label="대화 목록 열기" aria-haspopup="dialog" onClick={() => drawerRef.current?.showModal()}><Icon name="menu" size={22} /></button>
          <div className="workspace-title"><h1>직관 도우미</h1><span className={`workspace-connection${available ? " is-ready" : ""}`} aria-live="polite"><i />{chat.statusLoading ? "연결 확인 중" : demo ? "예시 대화" : available ? "함께 준비해요" : "연결 확인 필요"}</span></div>
          {activeTitle && <p className="workspace-active-title" title={activeTitle}>{activeTitle}</p>}
          <div className="workspace-window-actions">
            <button ref={minimizeRef} type="button" className="workspace-icon-button workspace-minimize" aria-label="채팅 작게 보기" title="채팅 작게 보기" onClick={chat.onMinimize}>
              <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 4l5 5M9 4v5H4M20 20l-5-5M15 20v-5h5" /></svg>
            </button>
            <Link href="/" className="workspace-home-link"><Icon name="home" size={17} /><span>홈으로</span></Link>
          </div>
        </header>

        <ChatCoursePreferences />
        <div className={`workspace-transcript${empty ? " is-empty" : ""}`} ref={scrollRef} onScroll={event => { const element = event.currentTarget; nearBottomRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 100; }}>
          <div className="workspace-reading-column">
            {empty && <section className="workspace-welcome" aria-labelledby="workspace-welcome-title">
              <span className="workspace-welcome-mark"><Icon name="sparkles" size={29} /></span>
              <p className="workspace-welcome-eyebrow">야구가 있는 하루를, 함께</p>
              <h2 id="workspace-welcome-title">어떤 직관을 준비하고 있나요?</h2>
              <p className="workspace-welcome-description"><>직관 코스부터 구장 정보, 쉬운 야구 이야기까지.<br />궁금한 것을 편하게 물어보세요.</></p>
              {<div className="workspace-suggestions">{SUGGESTIONS.map(item => <button key={item.intent} type="button" onClick={() => { chat.onSuggestion(item.text, item.intent); inputRef.current?.focus(); }}><Icon name={item.icon} size={19} /><span>{item.label}</span></button>)}</div>}
            </section>}
            {chat.context?.stadium && <p className="workspace-context"><Icon name="pin" size={14} />{chat.context.stadium}에서의 하루</p>}
            <div className="workspace-messages" role="log" aria-label="직관 도우미 대화 내용" aria-live="polite" aria-relevant="additions">
              {chat.messages.map((message, index) => <article key={`${chat.activeConversationId}-${index}`} className={`workspace-message workspace-message-${message.role}`}>
                {message.role === "assistant" ? <><div className="workspace-assistant-label"><span><Icon name="sparkles" size={15} /></span>직관 도우미</div><ChatProgress items={message.timeline ?? []} />{message.content && <ChatAnswer text={message.content} places={message.course?.places} />}{message.status && message.status !== "completed" && <p className="workspace-message-note">끝까지 만들지 못한 답변이에요.</p>}{message.course && <ChatCourseCard course={message.course} />}<ChatQuestions message={message} disabled={!available || busy || index !== chat.messages.length - 1} /><ChatFeedback key={`${chat.activeConversationId}-${message.id}`} message={message} disabled={!available || busy} /></> : <><span className="sr-only">나</span><ChatAttachmentCards items={(message.attachments ?? []).filter(item => !(item.kind === "text" && /^Pasted Text \d+\.txt$/.test(item.name) && message.content.includes(`첨부 참고 자료: ${item.name}`)))} /><div className="workspace-user-bubble"><ChatUserContent attachments={message.attachments} content={message.content} planning={chat.messages[index - 1]?.role === "assistant" ? chat.messages[index - 1].planning : undefined} /></div>{message.answerDeleted && <p className="workspace-message-note">답변을 삭제했어요.</p>}{message.status && message.status !== "completed" && <p className="workspace-message-note">답변을 받지 못한 질문이에요.</p>}{message.id !== undefined && available && !busy && <div className="workspace-message-actions"><button type="button" aria-label="이 질문 수정" title="수정하면 이 질문 이후의 대화가 지워져요" onClick={() => { chat.onEditMessage(message.id!); inputRef.current?.focus(); }}><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m16 3 5 5-13 13H3v-5L16 3ZM14 5l5 5" /></svg></button><button type="button" aria-label="이 질문부터 삭제" title="이 질문과 이후 대화를 모두 지워요" onClick={() => chat.onDeleteMessage(message.id!)}><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" /></svg></button></div>}</>}
              </article>)}
              {chat.failed && <article className="workspace-message workspace-message-user"><span className="sr-only">나</span><div className="workspace-user-bubble"><ChatUserContent content={chat.failed} planning={chat.messages.at(-1)?.role === "assistant" ? chat.messages.at(-1)?.planning : undefined} /></div></article>}
              {busy && <article className="workspace-message workspace-message-assistant"><div className="workspace-assistant-label"><span><Icon name="sparkles" size={15} /></span>직관 도우미</div><ChatProgress items={chat.timeline} live /><ChatPending busy={busy} streaming={chat.streaming} className="workspace-thinking" /></article>}
            </div>
            <ChatWriterOffer />
        {chat.error && <div className="workspace-feedback is-error" role="alert"><p>{chat.error}</p><button type="button" onClick={chat.onRetry} disabled={busy || !available}>다시 시도</button></div>}
            {chat.statusError && <div className="workspace-feedback is-error" role="alert"><p>{chat.statusError}</p><button type="button" onClick={chat.onRefreshStatus} disabled={chat.statusLoading}>연결 다시 확인</button></div>}
            {!chat.statusError && chat.status && !chat.status.ready && !chat.statusLoading && <div className="workspace-feedback"><p>대화 연결을 준비하고 있어요.</p><button type="button" onClick={chat.onRefreshStatus}>연결 다시 확인</button></div>}
            {chat.notice && <p className="workspace-notice" role="status">{chat.notice}</p>}
          </div>
        </div>

        {<div className="workspace-composer-area">
          {chat.context?.currentCourse?.selectedPlace && <p className="workspace-notice">기준 장소: {chat.context.currentCourse.selectedPlace.name} · “여기 앞뒤에 추가해줘”</p>}
          <ChatSubAgentStatus items={busy ? chat.timeline : []} />
          <ChatQueue focusInput={() => inputRef.current?.focus()} />
          {chat.editingMessageId !== null && <p className="workspace-edit-banner" role="status">질문을 수정하고 있어요. 보내면 이 질문 이후의 대화는 지워져요. <button type="button" onClick={chat.onCancelEdit}>수정 취소</button></p>}
          <div className="workspace-composer" onDragOver={event => { if (event.dataTransfer.types.includes("Files")) event.preventDefault(); }} onDrop={event => { if (!event.dataTransfer.files.length) return; event.preventDefault(); event.stopPropagation(); if (!busy && available) chat.onAttach(Array.from(event.dataTransfer.files)); }} onPaste={event => { const images = Array.from(event.clipboardData.files).filter(file => file.type.startsWith("image/")); if (images.length && !busy && available) { event.preventDefault(); chat.onAttach(images); } }}>
          <ChatComposerTools disabled={false} available={available} hint={busy ? "답변을 준비하고 있어요" : "야구가 궁금한 모든 순간"} actions={<>{busy && <button type="button" className="workspace-send workspace-stop" aria-label="답변 생성 중단" title="답변 받기 중단 (받던 답변은 저장되지 않아요)" onClick={event => { event.preventDefault(); chat.onCancel(); }}><span /></button>}<button type="button" onClick={send} className="workspace-send" aria-label={sendLabel} title={sendLabel} disabled={!canSubmit}><Icon name="arrow" size={20} /></button></>}>
            <label className="sr-only" htmlFor="workspace-question">직관 도우미에게 질문</label>
            <ChatInlineInput id="workspace-question" inputRef={inputRef} disabled={false} available={available} onSend={send} onCompositionChange={value => { composingRef.current = value; }} />
          </ChatComposerTools>

          </div>
          <p className="workspace-footnote">{guest && !demo ? <><Link href="/login">로그인</Link>하면 계정에 대화가 저장돼요. 비회원 대화는 이 브라우저에서만 이어져요. </> : null}{demo ? "예시 답변이에요. 실제 일정과 장소 검색 결과는 포함되지 않아요." : "경기 일정과 구장 운영 정보는 방문 전 공식 안내를 확인해 주세요."}</p>
        </div>}
      </main>
    </div>
  );
}
