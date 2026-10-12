"use client";

import Image from "next/image";
import { createContext, useEffect, useId, useRef, useState, type ReactNode } from "react";

import type { ChatAttachment, ChatAttachmentDraft, ChatToolGroup } from "@/lib/chat/types";
import { fetchChatAttachmentBlob, fetchChatToolGroups } from "@/lib/chat/client";
import { useMemberAuth } from "@/lib/member-auth";
import { useChat } from "./chat-provider";
import { Icon } from "./icons";
import "@/styles/chat-composer-tools.css";

export const ChatToolGroupsContext = createContext<ChatToolGroup[]>([]);
export const ChatToolSelectionContext = createContext<{ current: ((group: ChatToolGroup) => void) | null } | null>(null);

function AttachmentImage({ attachment, preview }: { attachment?: ChatAttachment; preview?: string }) {
  const { status } = useMemberAuth();
  const [source, setSource] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (preview || !attachment?.url || attachment.kind !== "image" || !["authenticated", "anonymous"].includes(status)) return;
    const sessionId = attachment.url.split("/")[5];
    const controller = new AbortController();
    let objectUrl = "";
    void fetchChatAttachmentBlob(status === "authenticated" ? "member" : "guest", sessionId, attachment.id, controller.signal).then(blob => {
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob); setSource(objectUrl);
    }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [attachment, preview, status]);
  return preview || source ? /* Private/local blobs cannot use the image optimizer. */ <Image unoptimized width={96} height={76} src={preview || source} alt={attachment?.name || "첨부 이미지"} /> : <span className="chat-attachment-image-placeholder">{failed ? "미리보기 실패" : "이미지"}</span>;
}

const toolPresentation: Record<string, { icon: Parameters<typeof Icon>[0]["name"]; description: string }> = {
  web_research: { icon: "book", description: "웹" },
  schedule: { icon: "calendar", description: "야구" },
  standings: { icon: "trophy", description: "야구" },
  players: { icon: "userPlus", description: "야구" },
  highlight: { icon: "sparkles", description: "영상" },
  baseball_stats: { icon: "chart", description: "야구" },
  rules: { icon: "book", description: "야구" },
  stadium_info: { icon: "stadium", description: "구장" },
  carry_in: { icon: "stadium", description: "구장" },
  parking_transport: { icon: "car", description: "교통" },
  community: { icon: "chat", description: "커뮤니티" },
  nearby_places: { icon: "pin", description: "구장" },
  tourism: { icon: "map", description: "여행" },
  directions: { icon: "route", description: "교통" },
  courses: { icon: "heart", description: "코스" },
  day_plan: { icon: "clock", description: "코스" },
};
export const presentationFor = (id: string) => toolPresentation[id] ?? { icon: "sparkles" as const, description: "정보" };

const sizeLabel = (size: number) => size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(size / 1024)} KB`;

export function ChatAttachmentCards({ items, drafts = false, disabled = false }: { items: (ChatAttachment | ChatAttachmentDraft)[]; drafts?: boolean; disabled?: boolean }) {
  const chat = useChat();
  items = items.filter(item => item.kind !== "url" && !("key" in item && item.inlineText));
  if (!items.length) return null;
  return <div className="chat-attachment-list" aria-label={drafts ? "보낼 첨부 자료" : "첨부 자료"}>{items.map(item => {
    const draft = "key" in item ? item : undefined;
    const attachment = draft ? draft.attachment : item as ChatAttachment;
    const state = draft?.state ?? "ready";
    return <div key={draft?.key ?? attachment!.id} className={`chat-attachment-card is-${item.kind} is-${state}`}>
      {item.kind === "image" ? <AttachmentImage attachment={attachment} preview={draft?.preview ?? (attachment as (ChatAttachment & { preview?: string }) | undefined)?.preview} /> : <span className="chat-attachment-symbol"><Icon name={item.kind === "url" ? "link" : "book"} size={22} /></span>}
      <div className="chat-attachment-info"><strong title={item.name}>{item.name}</strong><small>{item.kind === "url" ? "URL 자료" : sizeLabel(item.size)} · {state === "uploading" ? "업로드 중" : state === "failed" ? "업로드 실패" : state === "cancelled" ? "취소됨" : "첨부됨"}</small></div>
      {draft && <button type="button" className="chat-attachment-remove" aria-label={`${item.name} 첨부 제거`} disabled={disabled} onClick={() => chat.onRemoveAttachment(draft.key)}><Icon name="close" size={14} /></button>}
      {draft && state === "uploading" && <button type="button" className="chat-attachment-retry" disabled={disabled} onClick={() => chat.onCancelAttachment(draft.key)}>취소</button>}
      {draft && ["failed", "cancelled"].includes(state) && <div className="chat-attachment-error" role="status"><span>{draft.error}</span><button type="button" disabled={disabled} onClick={() => chat.onRetryAttachment(draft.key)}>다시 시도</button></div>}
    </div>;
  })}</div>;
}

export function ChatComposerTools({ disabled, available, children, hint, actions }: { disabled: boolean; available: boolean; children: ReactNode; hint: ReactNode; actions: ReactNode }) {
  const chat = useChat();
  const { status, user } = useMemberAuth();
  const [groups, setGroups] = useState<ChatToolGroup[]>([]);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const menu = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const toolSelection = useRef<((group: ChatToolGroup) => void) | null>(null);
  const id = useId();
  useEffect(() => {
    if (!["authenticated", "anonymous"].includes(status)) return;
    const controller = new AbortController();
    void fetchChatToolGroups(status === "authenticated" ? "member" : "guest", controller.signal).then(value => { setGroups(value); setError(""); }).catch(cause => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [status, user?.id, reload]);
  const visibleGroups = groups.filter(group => group.id !== "weather");
  const blocked = disabled || !available;
  function closeMenu() { menu.current?.hidePopover(); opener.current?.focus({ preventScroll: true }); }
  return <>
    <ChatAttachmentCards items={chat.attachments} drafts disabled={disabled} />
    <ChatToolGroupsContext.Provider value={visibleGroups}><ChatToolSelectionContext.Provider value={toolSelection}>{children}</ChatToolSelectionContext.Provider></ChatToolGroupsContext.Provider>
    <div className="chat-composer-toolbar">
      <div className="chat-composer-add-row">
        <button ref={opener} type="button" className="chat-composer-add" disabled={blocked} aria-label="자료 첨부 및 기능 선택" aria-expanded={menuOpen} popoverTarget={id}><span aria-hidden="true">+</span></button>
        <span className="chat-composer-hint">{hint}</span>
      </div>
      <div className="chat-composer-actions">{actions}</div>
    </div>
    <input ref={fileInput} type="file" className="sr-only" tabIndex={-1} multiple accept="image/jpeg,image/png,image/webp,.txt,.md" aria-label="첨부 파일 선택" onChange={event => { chat.onAttach(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
    <div ref={menu} id={id} popover="auto" className="chat-composer-menu" onToggle={event => {
      const opened = event.newState === "open"; setMenuOpen(opened);
      if (opened && menu.current && opener.current) {
        const rect = opener.current.getBoundingClientRect();
        menu.current.style.left = `${Math.max(16, Math.min(rect.left, window.innerWidth - 336))}px`;
        const bottom = Math.max(16, Math.min(window.innerHeight - 96, window.innerHeight - rect.top + 8));
        menu.current.style.bottom = `${bottom}px`;
        menu.current.style.maxHeight = `${Math.max(64, Math.min(480, window.innerHeight - bottom - 16))}px`;
        menu.current.querySelector<HTMLButtonElement>("button")?.focus();
      }
    }} onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeMenu(); } }}>
      <h3 className="chat-menu-section">추가</h3>
      <button type="button" disabled={blocked} onClick={() => { closeMenu(); fileInput.current?.click(); }}><Icon name="paperclip" size={18} /><span className="chat-menu-copy"><span>이미지 또는 문서 첨부</span><small>이미지·텍스트·마크다운 파일</small></span></button>
      <fieldset disabled={blocked}><legend>도구</legend><p>여러 개 선택할 수 있어요. 자동 판단에 더해져요.</p>
        {error ? <div role="alert"><p>{error}</p><button type="button" onClick={() => setReload(value => value + 1)}>목록 다시 불러오기</button></div> : !groups.length ? <p role="status">도구 목록을 불러오고 있어요.</p> : visibleGroups.map(group => <button type="button" key={group.id} className="chat-tool-row" disabled={blocked} aria-labelledby={`${id}-${group.id}-label`} aria-describedby={`${id}-${group.id}-description`} data-selected={chat.toolGroupIds.includes(group.id)} onClick={() => { menu.current?.hidePopover(); toolSelection.current?.(group); }}><Icon name={presentationFor(group.id).icon} size={18} /><span className="chat-menu-copy"><span id={`${id}-${group.id}-label`}>{group.label}</span><small id={`${id}-${group.id}-description`}>{presentationFor(group.id).description}</small></span></button>)}
      </fieldset>
    </div>
  </>;
}
