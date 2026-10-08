"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { inheritCourseState } from "@/lib/chat/course";
import { restoreWriterCourse } from "@/lib/chat/writer-state";
import type { AnswerFeedback, ChatAttachment, ChatContext, ChatCourse, ChatMessage, ChatStatus, ChatTimelineItem } from "@/lib/chat/types";
import type { ChatPlanning } from "@/lib/chat/planning";
import { MAX_MESSAGE_LENGTH, appendTimeline } from "@/lib/chat/types";
import {
  createChatSession, uploadChatAttachment, deleteChatAttachment, validateChatFile,
  ChatClientError,
  ChatStreamStoppedError,
  deleteChatMessages,
  deleteChatSession,
  editChatMessage,
  fetchChatHistory,
  fetchChatUsage,
  getChatStatus,
  listChatSessions,
  sendChatMessage,
  saveAnswerFeedback,
  USAGE_BUSY,
  type ChatMode,
} from "@/lib/chat/client";
import { commitChatLoad, fromToolDto, restoreChatMessages } from "@/lib/chat/history";
import type { ChatToolCallDto } from "@/lib/chat/wire";
import { useMemberAuth } from "@/lib/member-auth";
import { createClientId } from "@/lib/client-id";
import { chatComposerContent, inlineChatUrls, normalizeChatUrl } from "@/lib/chat/inline-urls";
import { ChatPopup } from "./chat-popup";

import type { ChatAttachmentDraft } from "@/lib/chat/types";

export type QueuedQuestion = { id: number; content: string; context?: ChatContext; attachments?: ChatAttachmentDraft[]; toolGroupIds?: string[]; edit?: { messageId: number; historyStamp: string } };
const QUEUE_LIMIT = 2;
function editHistoryStamp(messages: ChatMessage[], messageId: number) {
  const index = messages.findIndex(message => message.role === "user" && message.id === messageId);
  return index < 0 ? "" : JSON.stringify(messages.slice(index).map(({ id, role, content, status }) => [id, role, content, status]));
}
type ConversationSnapshot = {
  attachments?: ChatAttachmentDraft[];
  toolGroupIds?: string[];
  messages: ChatMessage[];
  draft: string;
  context?: ChatContext;
  failed: string;
  failedContext?: ChatContext;
  error: string;
  notice: string;
  queued?: QueuedQuestion[];
  queuePaused?: boolean;
};
/** 챗봇 코스를 받아 줄 화면 (루트 작성). apply 는 되돌리기 함수를 돌려준다. */
export type CourseTarget = {
  stadiumCode: string;
  stopCount: number;
  selectionKey?: string;
  getContext?: () => ChatContext;
  getVersion?: () => string;
  apply: (course: ChatCourse, how: "replace" | "append") => (() => boolean | void) | null;
};
export type AppliedCourse = { undo: (() => boolean | void) | null; message: string };
type ChatControls = ConversationSnapshot & {
  attachments: ChatAttachmentDraft[];
  toolGroupIds: string[];
  onToolGroupsChange: (ids: string[]) => void;
  onInlineToolSelect: (id: string, label: string, restored?: boolean) => void;
  onAttach: (sources: (File | string)[]) => void;
  onPasteText: (text: string) => string | null;
  onCommitUrls: (text: string, renewed?: boolean) => void;
  onCompositionChange: (composing: boolean) => void;
  onRemoveAttachment: (key: string) => void;
  onRetryAttachment: (key: string) => void;
  onCancelAttachment: (key: string) => void;
  queued: QueuedQuestion[];
  queuePaused: boolean;
  queueSendingId: number | null;
  queueWaitingForServer: boolean;
  editingQueuedId: number | null;
  onEditQueued: (id: number) => void;
  onRemoveQueued: (id: number) => void;
  onCancelQueuedEdit: () => void;
  onResumeQueue: () => void;
  writerSeconds: number | null;
  writerAnnouncement: string;
  stayHere: (explicit?: boolean) => void;
  goToWriter: () => void;
  submitQuestions: (messageId: number, text: string, onAccepted?: () => void) => Promise<"rejected" | "failed" | "succeeded">;
  openChat: (initialMessage?: string, context?: ChatContext) => void;
  onExpand: () => void;
  onMinimize: () => void;
  onClosePopup: () => void;
  status: ChatStatus | null;
  statusLoading: boolean;
  statusError: string;
  pending: string;
  streaming: string;
  /** Live delta/tool order for the in-flight turn only. */
  timeline: ChatTimelineItem[];
  /** 수정 중인 서버 저장 질문 id. 보내면 그 질문부터 이후 대화가 지워지고 답변을 새로 받는다. */
  editingMessageId: number | null;
  conversations: { id: string; title: string }[];
  activeConversationId: string;
  onDraftChange: (value: string) => void;
  onRefreshStatus: () => void;
  onSend: () => void;
  onRetry: () => void;
  onCancel: () => void;
  onReset: () => void;
  onSuggestion: (text: string, intent: ChatContext["intent"]) => void;
  onSelectConversation: (id: string) => void;
  /** 왼쪽 대화 목록에서 대화를 지운다 (화면에서 바로 빼고, 서버 기록 삭제는 가능한 경우에만 시도) */
  onDeleteConversation: (id: string) => void;
  onEditMessage: (id: number) => void;
  onCancelEdit: () => void;
  /** 저장된 질문과 그 이후 대화를 서버에서 지운다 (확인 후) */
  onDeleteMessage: (id: number) => void;
  onContextChange: (context?: ChatContext) => void;
  onFeedback: (id: number, feedback: AnswerFeedback | null) => Promise<void>;
  courseTarget: CourseTarget | null;
  registerCourseTarget: (target: CourseTarget | null) => void;
  openCourseInWriter: (course: ChatCourse) => void;
  takePendingCourse: () => ChatCourse | null;
  appliedCourses: ReadonlyMap<ChatCourse, AppliedCourse>;
  applyChatCourse: (course: ChatCourse, how: "replace" | "append") => void;
  undoChatCourse: (course: ChatCourse) => void;
};
const ChatControlsContext = createContext<ChatControls | null>(null);

export function useChat() {
  const value = useContext(ChatControlsContext);
  if (!value) throw new Error("useChat must be used inside ChatProvider");
  return value;
}

/**
 * 가이드 샘플 화면용 챗봇: 실제 대화·요청 없이 빈 대화 화면만 보여 준다 (연결 상태 표시는 실제 값을 따른다).
 */
export function ChatSampleProvider({ children }: { children: React.ReactNode }) {
  const real = useChat();
  const noop = () => {};
  const value: ChatControls = {
    ...real,
    attachments: [], toolGroupIds: [], onToolGroupsChange: noop, onInlineToolSelect: noop, onAttach: noop, onPasteText: () => null, onCommitUrls: noop, onCompositionChange: noop, onRemoveAttachment: noop, onRetryAttachment: noop, onCancelAttachment: noop,
    writerSeconds: null, writerAnnouncement: "", stayHere: noop, goToWriter: noop, submitQuestions: async () => "rejected",
    messages: [], draft: "", context: undefined,
    failed: "", error: "", notice: "",
    pending: "", streaming: "", timeline: [], editingMessageId: null,
    queued: [], queuePaused: false, queueSendingId: null, queueWaitingForServer: false, editingQueuedId: null,
    onEditQueued: noop, onRemoveQueued: noop, onCancelQueuedEdit: noop, onResumeQueue: noop,
    conversations: [{ id: "guide-sample", title: "새 대화" }], activeConversationId: "guide-sample",
    openChat: noop, onExpand: noop, onMinimize: noop, onClosePopup: noop,
    onDraftChange: noop, onRefreshStatus: noop, onSend: noop, onRetry: noop, onCancel: noop, onReset: noop,
    onSuggestion: noop, onSelectConversation: noop, onDeleteConversation: noop, onFeedback: async () => {},
    onEditMessage: noop, onCancelEdit: noop, onDeleteMessage: noop, onContextChange: noop,
    // 샘플 화면은 실제 챗봇 코스를 받지 않는다
    courseTarget: null, registerCourseTarget: noop, openCourseInWriter: noop, takePendingCourse: () => null,
    appliedCourses: new Map(), applyChatCourse: noop, undoChatCourse: noop,
  };
  return <ChatControlsContext.Provider value={value}>{children}</ChatControlsContext.Provider>;
}

export function ChatProvider({ children }: { children: React.ReactNode }) {
  const { status: memberStatus, user } = useMemberAuth();
  const accountId = memberStatus === "authenticated" ? user!.id : null;
  const identity = memberStatus === "authenticated" ? `member:${accountId}` : memberStatus;
  // 회원은 Bearer, 비회원은 서버가 심은 guest_id 쿠키로 같은 대화 API 를 쓴다.
  const mode: ChatMode | null = memberStatus === "authenticated" ? "member" : memberStatus === "anonymous" ? "guest" : null;
  const pathname = usePathname();
  const router = useRouter();
  const isChatPage = pathname === "/chat";
  const hasEmbeddedChat = pathname === "/routes/new";
  const [popupRequested, setPopupRequested] = useState(false);
  const popupOpen = popupRequested && !isChatPage && !hasEmbeddedChat;
  const [activeConversationId, setActiveConversationId] = useState("initial-chat");
  const [conversations, setConversations] = useState([{ id: "initial-chat", title: "새 대화" }]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const draftRef = useRef(draft);
  useEffect(() => { draftRef.current = draft; }, [draft]);
  const [attachments, setAttachments] = useState<ChatAttachmentDraft[]>([]);
  const [toolGroupIds, setToolGroupIds] = useState<string[]>([]);
  const attachmentRef = useRef<ChatAttachmentDraft[]>([]);
  const uploadRequests = useRef(new Map<string, AbortController>());
  const uploadPromises = useRef(new Map<string, Promise<void>>());
  // ponytail: sent previews live until identity change/unmount; prune per conversation if long sessions grow large.
  const sentPreviews = useRef(new Set<string>());
  const preparingSend = useRef(false);
  const composerVersion = useRef(0);
  const draftVersion = useRef(0);
  const composing = useRef(false);
  const removedUrls = useRef(new Map<string, Set<string>>());
  // Native textarea history stores text, not attachment metadata. Keep removed mappings locally until submit.
  const inlineHistory = useRef(new Map<string, Map<string, ChatAttachmentDraft>>());
  const inlineTools = useRef(new Map<string, Map<string, { marker: string; explicit: boolean }>>());
  const pasteNumber = useRef(0);
  const retiredInlineText = useRef(new Map<string, Map<string, string>>());
  const preEditComposer = useRef<{ draft: string; context?: ChatContext; attachments: ChatAttachmentDraft[]; toolGroupIds: string[]; notice: string } | null>(null);
  const sessionCreation = useRef<{ conversation: string; identity: string; promise: Promise<string> } | null>(null);
  const updateAttachments = useCallback((update: (items: ChatAttachmentDraft[]) => ChatAttachmentDraft[]) => { attachmentRef.current = update(attachmentRef.current); setAttachments(attachmentRef.current); }, []);
  const [context, setContext] = useState<ChatContext | undefined>();
  const [status, setStatus] = useState<ChatStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusError, setStatusError] = useState("");
  const [pending, setPending] = useState("");
  const [failed, setFailed] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [streaming, setStreaming] = useState("");
  const [timeline, setTimeline] = useState<ChatTimelineItem[]>([]);
  const [editingMessageId, setEditingMessageId] = useState<number | null>(null);
  const [queued, setQueued] = useState<QueuedQuestion[]>([]);
  const queuedRef = useRef<QueuedQuestion[]>([]);
  const queueSequenceRef = useRef(0);
  const [queuePaused, setQueuePaused] = useState(false);
  const [queueSendingId, setQueueSendingId] = useState<number | null>(null);
  const [serverBusyQueueId, setServerBusyQueueId] = useState<number | null>(null);
  const queueSendingRef = useRef<number | null>(null);
  const [editingQueuedId, setEditingQueuedId] = useState<number | null>(null);
  const editingQueuedRef = useRef<number | null>(null);
  const queueEditDraftRef = useRef("");
  const updateQueue = useCallback((items: QueuedQuestion[]) => {
    queuedRef.current = items;
    setQueued(items);
  }, []);
  const [chatIdentity, setChatIdentity] = useState(identity);
  const [courseTarget, setCourseTarget] = useState<CourseTarget | null>(null);
  const courseTargetRef = useRef<CourseTarget | null>(null);
  const courseTargetVersionRef = useRef(0);
  const [appliedCourses, setAppliedCourses] = useState<ReadonlyMap<ChatCourse, AppliedCourse>>(() => new Map());
  const appliedCoursesRef = useRef(appliedCourses);
  useEffect(() => { appliedCoursesRef.current = appliedCourses; }, [appliedCourses]);
  // 다른 화면(전체 채팅·팝업)에서 "루트 작성에서 열기"를 누르면 여기 두었다가 작성 화면이 가져간다.
  const pendingCourseRef = useRef<ChatCourse | null>(null);
  const historyRef = useRef<ChatMessage[]>([]);
  // 한 번에 하나의 전송·수정·삭제만 진행한다 (같은 화면의 중복 전송 방지).
  const requestRef = useRef<{ controller: AbortController; version: number; stopped: boolean } | null>(null);
  const statusRequestRef = useRef<AbortController | null>(null);
  const historyRequestRef = useRef<AbortController | null>(null);
  const syncRequestRef = useRef<AbortController | null>(null);
  const loadingConversationRef = useRef<string | null>(null);
  const requestVersion = useRef(0);
  const pendingRef = useRef("");
  const failedContextRef = useRef<ChatContext | undefined>(undefined);
  const retryRef = useRef<{ content: string; context?: ChatContext; messageId: number | null; attachments: ChatAttachmentDraft[]; toolGroupIds: string[]; conversation: string; identity: string } | null>(null);
  const returnPageRef = useRef({ url: "/", scrollY: 0 });
  const restorePageRef = useRef(false);
  const popupOpenerRef = useRef<HTMLElement | null>(null);
  const topButtonRef = useRef<HTMLButtonElement>(null);
  const identityRef = useRef(identity);
  const activeConversationRef = useRef(activeConversationId);
  const identityChanged = chatIdentity !== identity;
  const urlScope = useCallback(() => `${identityRef.current}:${activeConversationRef.current}:${editingMessageId ?? "draft"}`, [editingMessageId]);
  // Root layout keeps conversations alive across client-side page navigation.
  const backendSessions = useRef(new Map<string, string>());
  const archivedConversations = useRef(new Map<string, ConversationSnapshot>());
  const deletedConversations = useRef(new Set<string>());

  const [writerSeconds, setWriterSeconds] = useState<number | null>(null);
  const [writerAnnouncement, setWriterAnnouncement] = useState("");
  const writerOfferRef = useRef<{ deadline: number; conversation: string; path: string; identity: string } | null>(null);
  const offeredConversations = useRef(new Set<string>());
  const restoredDraftRef = useRef<{ content: string; draft: string; draftVersion: number; composerVersion: number } | null>(null);
  const offerCancelledRef = useRef(false);
  const stayHere = useCallback((explicit = false) => {
    const hadOffer = writerOfferRef.current !== null;
    offerCancelledRef.current = true;
    writerOfferRef.current = null;
    setWriterSeconds(null);
    setWriterAnnouncement(explicit && hadOffer ? "자동 이동을 취소했어요. 여기서 대화를 이어가요." : "");
  }, []);
  const goToWriter = useCallback(() => {
    const offer = writerOfferRef.current;
    stayHere();
    if (!offer || offer.conversation !== activeConversationRef.current || offer.identity !== identityRef.current || offer.path !== pathname || pathname === "/routes/new") return;
    setPopupRequested(false);
    router.push("/routes/new"); // Root provider retains request, conversation and draft; writer drafts are untouched.
  }, [pathname, router, stayHere]);
  useEffect(() => {
    offerCancelledRef.current = true;
    writerOfferRef.current = null;
    const timer = window.setTimeout(() => { setWriterSeconds(null); setWriterAnnouncement(""); }, 0);
    return () => window.clearTimeout(timer);
  }, [pathname, activeConversationId, identity]);
  useEffect(() => {
    if (writerSeconds === null) return;
    const timer = window.setInterval(() => {
      const offer = writerOfferRef.current;
      if (!offer) return;
      const seconds = Math.max(0, Math.ceil((offer.deadline - Date.now()) / 1000));
      setWriterSeconds(seconds);
      if (!seconds) goToWriter();
    }, 250);
    return () => window.clearInterval(timer);
  }, [writerSeconds, goToWriter]);

  const invalidateHistory = useCallback(() => {
    historyRequestRef.current?.abort();
    historyRequestRef.current = null;
    loadingConversationRef.current = null;
  }, []);

  const invalidateSync = useCallback(() => {
    syncRequestRef.current?.abort();
    syncRequestRef.current = null;
  }, []);

  const changeDraft = useCallback((value: string) => {
    stayHere();
    if (!loadingConversationRef.current) invalidateHistory();
    restoredDraftRef.current = null;
    for (const [marker, original] of retiredInlineText.current.get(urlScope()) ?? []) value = value.split(marker).join(original);
    const history = inlineHistory.current.get(urlScope()) ?? new Map<string, ChatAttachmentDraft>();
    const present = (item: ChatAttachmentDraft) => item.inlineText ? value.includes(item.inlineText) : item.kind === "url" ? inlineChatUrls(value).includes(item.sourceUrl ?? item.attachment?.url ?? item.name) : true;
    attachmentRef.current.forEach(item => { if (item.inlineText || item.kind === "url") history.set(item.key, item); });
    inlineHistory.current.set(urlScope(), history);
    for (const item of history.values()) {
      if (!present(item)) continue;
      if ((item.state === "failed" || item.state === "cancelled") && item.pastedText && item.inlineText) value = value.split(item.inlineText).join(item.pastedText);
      else if (!attachmentRef.current.some(current => current.key === item.key)) {
        const retained = attachmentRef.current.filter(current => present(current));
        if (retained.length >= 10 || item.kind === "url" && retained.filter(current => current.kind === "url").length >= 3) {
          if (item.pastedText && item.inlineText) value = value.split(item.inlineText).join(item.pastedText);
          setNotice("첨부는 합계 10개까지 가능해 복원한 자료를 원문으로 되돌렸어요.");
        } else updateAttachments(items => [...items.filter(current => present(current)), item]);
      }
    }
    updateAttachments(items => items.filter(present));
    const tools = inlineTools.current.get(urlScope());
    if (tools) setToolGroupIds(ids => [...new Set([...ids.filter(id => !tools.has(id) || tools.get(id)!.explicit), ...[...tools].filter(([, tool]) => value.includes(tool.marker)).map(([id]) => id)])]);
    const remaining = new Set(inlineChatUrls(value));
    const removed = removedUrls.current.get(urlScope());
    removed?.forEach(url => { if (!remaining.has(url)) removed.delete(url); });
    draftVersion.current += 1;
    draftRef.current = value;
    setDraft(value);
  }, [invalidateHistory, stayHere, urlScope, updateAttachments]);

  const loadStatus = useCallback((chatMode: ChatMode, controller: AbortController) => {
    return getChatStatus(chatMode, controller.signal).then(
      nextStatus => {
        if (!controller.signal.aborted) { setStatus(nextStatus); setStatusError(""); }
      },
      cause => {
        if (!controller.signal.aborted) {
          setStatus(null);
          setStatusError(cause instanceof Error ? cause.message : "연결 상태를 확인하지 못했어요.");
        }
      },
    ).finally(() => {
      if (!controller.signal.aborted) setStatusLoading(false);
    });
  }, []);

  const refreshStatus = useCallback(() => {
    statusRequestRef.current?.abort();
    if (!mode) {
      setStatus(null); setStatusLoading(memberStatus === "loading");
      setStatusError(memberStatus === "unavailable" ? "로그인 상태를 확인하지 못했어요." : "");
      return;
    }
    const controller = new AbortController();
    statusRequestRef.current = controller;
    setStatusLoading(true);
    setStatusError("");
    void loadStatus(mode, controller);
  }, [loadStatus, memberStatus, mode]);

  const closePopup = useCallback(() => {
    stayHere();
    setPopupRequested(false);
    requestAnimationFrame(() => {
      const opener = popupOpenerRef.current;
      if (opener?.isConnected) opener.focus({ preventScroll: true });
      else topButtonRef.current?.focus({ preventScroll: true });
    });
  }, [stayHere]);

  const expandChat = useCallback(() => {
    setPopupRequested(false);
    if (!isChatPage) {
      returnPageRef.current = { url: `${window.location.pathname}${window.location.search}${window.location.hash}`, scrollY: window.scrollY };
      router.push("/chat");
    }
  }, [isChatPage, router]);

  const minimizeChat = useCallback(() => {
    // Returning to the embedded assistant must not leave a hidden popup request.
    const destination = isChatPage
      ? new URL(returnPageRef.current.url, window.location.origin).pathname
      : pathname;
    setPopupRequested(destination !== "/routes/new");
    popupOpenerRef.current = null;
    if (isChatPage) {
      restorePageRef.current = true;
      router.push(returnPageRef.current.url, { scroll: false });
    }
  }, [isChatPage, pathname, router]);

  // 서버에는 중단을 저장하는 API 가 없다. 연결만 끊고, 받던 답변은 보관하지 않는다.
  const cancelRequest = useCallback(() => {
    stayHere();
    const active = requestRef.current;
    if (!active || active.stopped) return;
    active.stopped = true;
    active.controller.abort();
  }, [stayHere]);

  const runUpload = useCallback(async (item: ChatAttachmentDraft) => {
    if (!mode || requestRef.current || loadingConversationRef.current) return;
    const conversation = activeConversationRef.current, expectedIdentity = identityRef.current;
    const scope = urlScope(), version = composerVersion.current;
    const controller = new AbortController();
    uploadRequests.current.set(item.key, controller);
    updateAttachments(items => items.map(current => current.key === item.key ? { ...current, state: "uploading", error: undefined } : current));
    try {
      let sessionId = backendSessions.current.get(conversation);
      if (!sessionId) {
        if (sessionCreation.current?.conversation !== conversation) sessionCreation.current = { conversation, identity: expectedIdentity, promise: createChatSession(mode, "새 대화").then(room => { if (identityRef.current !== expectedIdentity || deletedConversations.current.has(conversation)) throw new Error("대화가 변경됐어요."); backendSessions.current.set(conversation, room.id); return room.id; }).finally(() => { if (sessionCreation.current?.conversation === conversation && sessionCreation.current.identity === expectedIdentity) sessionCreation.current = null; }) };
        sessionId = await sessionCreation.current.promise;
      }
      if (controller.signal.aborted) return;
      const attachment = await uploadChatAttachment(mode, sessionId, item.file ?? item.sourceUrl!, controller.signal);
      if (controller.signal.aborted || uploadRequests.current.get(item.key) !== controller || identityRef.current !== expectedIdentity || activeConversationRef.current !== conversation) {
        if (item.kind === "url" && identityRef.current === expectedIdentity && !attachmentRef.current.some(current => current.key === item.key || current.sourceUrl === item.sourceUrl || current.attachment?.id === attachment.id)) {
          void deleteChatAttachment(mode, sessionId, attachment.id).catch(() => {}); // Historical references are protected by the server's 409 guard.
        }
        return;
      }
      updateAttachments(items => items.map(current => current.key === item.key ? { ...current, attachment, state: "ready" } : current));
      const historical = inlineHistory.current.get(scope)?.get(item.key);
      if (historical) inlineHistory.current.get(scope)!.set(item.key, { ...historical, attachment, state: "ready" });
    } catch (cause) {
      if (controller.signal.aborted || uploadRequests.current.get(item.key) !== controller || identityRef.current !== expectedIdentity || activeConversationRef.current !== conversation) return;
      const error = cause instanceof Error ? cause.message : "첨부하지 못했어요. 다시 시도해 주세요.";
      updateAttachments(items => items.map(current => current.key === item.key ? { ...current, state: "failed", error } : current));
      const historical = inlineHistory.current.get(scope)?.get(item.key);
      if (historical) inlineHistory.current.get(scope)!.set(item.key, { ...historical, state: "failed", error });
      const failedPaste = attachmentRef.current.find(current => current.key === item.key) ?? inlineHistory.current.get(scope)?.get(item.key);
      if (failedPaste?.inlineText && failedPaste.pastedText && composerVersion.current === version && urlScope() === scope) {
        if (draftRef.current.includes(failedPaste.inlineText)) changeDraft(draftRef.current.split(failedPaste.inlineText).join(failedPaste.pastedText));
        updateAttachments(items => items.filter(current => current.key !== item.key));
        setNotice("붙여넣은 자료를 등록하지 못해 원문을 복원했어요.");
      }
    } finally { if (uploadRequests.current.get(item.key) === controller) uploadRequests.current.delete(item.key); }
  }, [mode, updateAttachments, changeDraft, urlScope]);
  const startUpload = useCallback((item: ChatAttachmentDraft) => {
    const promise = runUpload(item);
    uploadPromises.current.set(item.key, promise);
    void promise.finally(() => { if (uploadPromises.current.get(item.key) === promise) uploadPromises.current.delete(item.key); });
  }, [runUpload]);
  const cancelUpload = useCallback((key: string) => {
    uploadRequests.current.get(key)?.abort(); uploadRequests.current.delete(key);
    const item = attachmentRef.current.find(item => item.key === key);
    if (item?.inlineText && item.pastedText) {
      inlineHistory.current.get(urlScope())?.set(key, { ...item, state: "cancelled" });
      updateAttachments(items => items.filter(item => item.key !== key));
      if (draftRef.current.includes(item.inlineText)) changeDraft(draftRef.current.split(item.inlineText).join(item.pastedText));
    } else updateAttachments(items => items.map(item => item.key === key ? { ...item, state: "cancelled", error: "업로드를 취소했어요." } : item));
  }, [updateAttachments, changeDraft, urlScope]);
  const stopUploads = useCallback(() => {
    composerVersion.current += 1;
    const cancelled = (item: ChatAttachmentDraft): ChatAttachmentDraft => item.state === "uploading" ? { ...item, state: "cancelled", error: "업로드를 취소했어요. 다시 시도할 수 있어요." } : item;
    for (const history of inlineHistory.current.values()) for (const [key, item] of history) if (uploadRequests.current.has(key)) history.set(key, cancelled(item));
    uploadRequests.current.forEach(controller => controller.abort()); uploadRequests.current.clear();
    updateAttachments(items => items.map(cancelled));
  }, [updateAttachments]);
  const restorePreEditComposer = useCallback(() => {
    const saved = preEditComposer.current;
    if (!saved) return;
    stopUploads();
    attachmentRef.current.forEach(item => { if (item.preview && !sentPreviews.current.has(item.preview)) URL.revokeObjectURL(item.preview); });
    preEditComposer.current = null;
    setDraft(saved.draft); setContext(saved.context); setToolGroupIds(saved.toolGroupIds); setNotice(saved.notice);
    updateAttachments(() => saved.attachments);
    setEditingMessageId(null);
  }, [stopUploads, updateAttachments]);

  const attach = useCallback((sources: (File | string)[]) => {
    if (!mode || requestRef.current || loadingConversationRef.current) return;
    stayHere(); invalidateHistory(); invalidateSync();
    try {
      const urls = new Set(attachmentRef.current.filter(item => item.kind === "url").map(item => normalizeChatUrl(item.sourceUrl ?? item.attachment?.url ?? item.name)));
      const pending: ChatAttachmentDraft[] = [];
      for (const source of sources) {
        if (typeof source === "string") {
          const url = normalizeChatUrl(source);
          if (!url) throw new Error("공개 HTTP(S) 주소를 입력해 주세요.");
          if (urls.has(url)) continue;
          urls.add(url);
          pending.push({ key: createClientId(), name: url, kind: "url", size: 0, sourceUrl: url, state: "uploading" });
          continue;
        }
        const kind = typeof source === "string" ? "url" : validateChatFile(source);
        pending.push({ key: createClientId(), name: typeof source === "string" ? source : source.name, kind, size: typeof source === "string" ? 0 : source.size, ...(typeof source === "string" ? { sourceUrl: source } : { file: source }), state: "uploading" });
      }
      if (!pending.length) return true;
      if (urls.size > 3) throw new Error("주소는 3개까지 첨부할 수 있어요.");
      if (attachmentRef.current.length + pending.length > 10) throw new Error("파일·이미지·주소는 합계 10개까지 첨부할 수 있어요.");
      pending.forEach(item => { if (item.kind === "image" && item.file) item.preview = URL.createObjectURL(item.file); });
      // Reserve the entire batch synchronously before any upload can yield or another batch starts.
      updateAttachments(items => [...items, ...pending]);
      pending.forEach(startUpload);
      return true;
    } catch (cause) { setNotice(cause instanceof Error ? cause.message : "파일을 확인해 주세요."); return false; }
  }, [mode, stayHere, invalidateHistory, invalidateSync, updateAttachments, startUpload]);
  const pasteText = (text: string): string | null => {
    if (Array.from(text).length < 2000 || composing.current || !mode || requestRef.current || loadingConversationRef.current) return null;
    const number = pasteNumber.current = Math.max(pasteNumber.current, ...attachmentRef.current.map(item => Number(item.name.match(/^Pasted Text (\d+)\.txt$/)?.[1] ?? 0))) + 1;
    const marker = `[[ Text ${number} ]]`;
    const file = new File([text], `Pasted Text ${number}.txt`, { type: "text/plain" });
    if (!attach([file])) return null;
    updateAttachments(items => items.map(item => item.file === file ? { ...item, inlineText: marker, pastedText: text } : item));
    const scope = urlScope(), history = inlineHistory.current.get(scope) ?? new Map<string, ChatAttachmentDraft>();
    const item = attachmentRef.current.find(item => item.file === file)!;
    history.set(item.key, item); inlineHistory.current.set(scope, history);
    return marker;
  };
  const renderedComposerVersion = composerVersion.current;
  const commitUrls = (text: string, renewed = false) => {
    if (composing.current || identityRef.current !== identity || activeConversationRef.current !== activeConversationId || composerVersion.current !== renderedComposerVersion) return;
    const current = new Set(inlineChatUrls(draftRef.current));
    const removed = removedUrls.current.get(urlScope());
    const urls = inlineChatUrls(text).filter(url => current.has(url) && (renewed || !removed?.has(url)));
    if (urls.length) attach(urls);
  };

  const archiveCurrentConversation = useCallback(() => {
    stopUploads();
    if (loadingConversationRef.current === activeConversationId) return;
    const saved = preEditComposer.current;
    archivedConversations.current.set(activeConversationId, {
      messages: historyRef.current, draft: editingQueuedRef.current === null ? draft : queueEditDraftRef.current,
      context, failed, failedContext: failedContextRef.current, error, notice, attachments: attachmentRef.current, toolGroupIds, ...saved,
      queued: queuedRef.current, queuePaused,
    });
    restorePreEditComposer();
  }, [activeConversationId, context, draft, error, failed, notice, toolGroupIds, stopUploads, restorePreEditComposer, queuePaused]);

  const resetChat = useCallback(() => {
    if (requestRef.current) return;
    setEditingMessageId(null);
    stopUploads();
    if (!historyRef.current.length && !draft.trim() && !failed && !queuedRef.current.length && !attachmentRef.current.length && !toolGroupIds.length && !backendSessions.current.has(activeConversationId)) {
      invalidateHistory();
      setContext(undefined);
      setNotice("");
      return;
    }
    archiveCurrentConversation();
    updateQueue([]); setQueuePaused(false); setEditingQueuedId(null); editingQueuedRef.current = null;
    invalidateHistory();
    invalidateSync();
    const id = createClientId();
    activeConversationRef.current = id;
    setActiveConversationId(id);
    setConversations(current => [{ id, title: "새 대화" }, ...current]);
    historyRef.current = [];
    failedContextRef.current = undefined;
    setMessages([]);
    setDraft("");
    updateAttachments(() => []); setToolGroupIds([]);
    setPending("");
    setStreaming("");
    setTimeline([]);
    setFailed("");
    setError("");
    setNotice("");
    setContext(undefined);
  }, [activeConversationId, archiveCurrentConversation, draft, failed, invalidateHistory, invalidateSync, toolGroupIds, stopUploads, updateAttachments, updateQueue]);

  const showConversation = useCallback((saved: ConversationSnapshot, preserveDraft = false) => {
    if (!preserveDraft) { updateAttachments(() => saved.attachments ?? []); setToolGroupIds(saved.toolGroupIds ?? []); }
    updateQueue(saved.queued ?? []); setQueuePaused(saved.queuePaused ?? false);
    setEditingQueuedId(null); editingQueuedRef.current = null;
    historyRef.current = saved.messages;
    failedContextRef.current = saved.failedContext;
    setMessages(saved.messages);
    setDraft(current => preserveDraft && current.trim() ? current : saved.draft);
    setContext(saved.context);
    setFailed(saved.failed);
    setError(saved.error);
    setNotice(saved.notice);
    setStreaming("");
    setTimeline([]);
    setEditingMessageId(null);
  }, [updateAttachments, updateQueue]);

  const restoreConversation = useCallback(async (id: string, sessionId: string, controller: AbortController, expectedIdentity: string) => {
    try {
      const history = await fetchChatHistory(expectedIdentity.startsWith("member:") ? "member" : "guest", sessionId, controller.signal);
      const saved: ConversationSnapshot = { messages: restoreChatMessages(history), draft: "", failed: "", error: "", notice: "" };
      commitChatLoad(controller.signal, () => identityRef.current === expectedIdentity && activeConversationRef.current === id, () => {
        loadingConversationRef.current = null;
        archivedConversations.current.set(id, saved);
        showConversation(saved, true);
      });
    } catch (cause) {
      if (!controller.signal.aborted && identityRef.current === expectedIdentity && activeConversationRef.current === id) {
        loadingConversationRef.current = null;
        setNotice("");
        setError(cause instanceof Error ? cause.message : "대화 기록을 불러오지 못했어요.");
      }
    }
  }, [showConversation]);

  // 전송·수정·삭제가 끝나면 서버 기록을 다시 읽어 저장된 메시지 id·상태를 화면과 맞춘다.
  // 초안·안내 문구는 건드리지 않고, 다른 대화로 옮겼거나 새 요청이 시작됐으면 버린다.
  const syncConversation = useCallback((id: string, sessionId: string, expectedIdentity: string, sentContent?: string, sentAttachments?: ChatAttachment[]) => {
    syncRequestRef.current?.abort();
    const controller = new AbortController();
    syncRequestRef.current = controller;
    void fetchChatHistory(expectedIdentity.startsWith("member:") ? "member" : "guest", sessionId, controller.signal).then(history => {
      commitChatLoad(controller.signal, () => identityRef.current === expectedIdentity && activeConversationRef.current === id && !requestRef.current, () => {
        const local = historyRef.current;
        const localAttachments = new Map([...local.flatMap(message => message.attachments ?? []), ...(sentAttachments ?? [])].map(item => [item.id, item] as const));
        historyRef.current = restoreChatMessages(history, local).map(message => {
          const optimistic = message.role === "user" && sentContent === message.content ? [...local].reverse().find(item => item.role === "user" && item.content === sentContent) : undefined;
          return { ...message, ...(message.attachments ? { attachments: message.attachments.map(item => ({ ...localAttachments.get(item.id), ...item })) } : optimistic?.attachments ? { attachments: optimistic.attachments } : {}) };
        });
        archivedConversations.current.delete(id);
        setMessages(historyRef.current);
        // 실패·중단된 질문이 서버에 저장돼 있으면 따로 띄운 실패 말풍선은 거두고, 다시 시도는 그 질문 자리에서 한다.
        const stored = [...historyRef.current].reverse().find(message => message.role === "user");
        if (sentContent && stored?.id && stored.status !== "completed" && stored.content === sentContent) {
          setFailed("");
          if (retryRef.current) retryRef.current = { ...retryRef.current, messageId: stored.id };
        }
        // 연결은 끊겼지만 같은 질문이 답변과 함께 저장됐으면 실패 표시와 자동 복원된 초안만 거둔다.
        if (sentContent && stored?.status === "completed" && stored.content === sentContent && historyRef.current.at(-1)?.role === "assistant") {
          setFailed(""); setError(""); setNotice("");
          retryRef.current = null;
          failedContextRef.current = undefined;
          const restored = restoredDraftRef.current;
          if (restored?.content === sentContent && draftVersion.current === restored.draftVersion && composerVersion.current === restored.composerVersion) {
            setDraft(current => current === restored.draft ? "" : current);
            const storedIds = new Set(stored.attachments?.map(item => item.id));
            updateAttachments(items => items.filter(item => !item.attachment || !storedIds.has(item.attachment.id)));
            restoredDraftRef.current = null;
          }
        }
      });
    }, () => undefined);
  }, [updateAttachments]);

  const selectConversation = useCallback((id: string) => {
    if (requestRef.current || id === activeConversationId || deletedConversations.current.has(id)) return;
    const saved = archivedConversations.current.get(id);
    archiveCurrentConversation();
    invalidateHistory();
    invalidateSync();
    setActiveConversationId(id);
    activeConversationRef.current = id;
    updateQueue([]); setQueuePaused(false); setEditingQueuedId(null); editingQueuedRef.current = null;
    if (saved) { showConversation(saved); return; }
    const sessionId = backendSessions.current.get(id);
    if (!sessionId) return;
    const controller = new AbortController();
    historyRequestRef.current = controller;
    loadingConversationRef.current = id;
    historyRef.current = [];
    updateAttachments(() => []); setToolGroupIds([]);
    setMessages([]); setDraft(""); setFailed(""); setError(""); setNotice("대화 기록을 불러오고 있어요."); setStreaming(""); setTimeline([]); setEditingMessageId(null);
    void restoreConversation(id, sessionId, controller, identityRef.current);
  }, [activeConversationId, archiveCurrentConversation, invalidateHistory, invalidateSync, restoreConversation, showConversation, updateAttachments, updateQueue]);

  const deleteConversation = useCallback((id: string) => {
    // 답변을 받는 중인 대화는 지우지 않는다
    if (requestRef.current && id === activeConversationId) return;
    const sessionId = backendSessions.current.get(id);
    const remaining = conversations.filter(conversation => conversation.id !== id);
    if (remaining.length === conversations.length) return;
    if (id === activeConversationId) {
      const next = remaining[0];
      if (next) {
        selectConversation(next.id);
      } else {
        invalidateHistory();
        const fresh = createClientId();
        activeConversationRef.current = fresh;
        setActiveConversationId(fresh);
        updateQueue([]); setQueuePaused(false); setEditingQueuedId(null); editingQueuedRef.current = null;
        remaining.push({ id: fresh, title: "새 대화" });
        invalidateSync();
        historyRef.current = [];
        failedContextRef.current = undefined;
        restorePreEditComposer(); stopUploads(); updateAttachments(() => []); setToolGroupIds([]);
        setMessages([]); setDraft(""); setFailed(""); setError(""); setStreaming(""); setTimeline([]); setContext(undefined); setEditingMessageId(null);
      }
      setNotice("대화 내역을 지웠어요.");
    }
    deletedConversations.current.add(id);
    const prefix = `${identityRef.current}:${id}:`;
    for (const [scope, history] of inlineHistory.current) if (scope.startsWith(prefix)) {
      for (const key of history.keys()) { uploadRequests.current.get(key)?.abort(); uploadRequests.current.delete(key); uploadPromises.current.delete(key); }
      inlineHistory.current.delete(scope);
    }
    for (const scopes of [inlineTools.current, retiredInlineText.current, removedUrls.current]) for (const scope of scopes.keys()) if (scope.startsWith(prefix)) scopes.delete(scope);
    if (sessionCreation.current?.conversation === id) sessionCreation.current = null;
    if (retryRef.current?.conversation === id) retryRef.current = null;
    archivedConversations.current.delete(id);
    backendSessions.current.delete(id);
    setConversations(remaining);
    // 서버 기록 삭제가 실패해도 화면에서는 지운 상태를 유지한다
    if (sessionId && mode) void deleteChatSession(mode, sessionId).catch(() => undefined);
  }, [activeConversationId, conversations, invalidateHistory, invalidateSync, mode, selectConversation, stopUploads, updateAttachments, restorePreEditComposer, updateQueue]);

  useEffect(() => {
    if (identityRef.current === identity) return;
    identityRef.current = identity;
    stopUploads();
    sentPreviews.current.forEach(preview => URL.revokeObjectURL(preview));
    sentPreviews.current.clear();
    attachmentRef.current.forEach(item => { if (item.preview && !sentPreviews.current.has(item.preview)) URL.revokeObjectURL(item.preview); });
    preEditComposer.current?.attachments.forEach(item => { if (item.preview && !sentPreviews.current.has(item.preview)) URL.revokeObjectURL(item.preview); });
    preEditComposer.current = null;
    archivedConversations.current.forEach(saved => saved.attachments?.forEach(item => { if (item.preview && !sentPreviews.current.has(item.preview)) URL.revokeObjectURL(item.preview); }));
    updateAttachments(() => []); setToolGroupIds([]); sessionCreation.current = null;
    setChatIdentity(identity);
    requestVersion.current += 1;
    requestRef.current?.controller.abort();
    statusRequestRef.current?.abort();
    historyRequestRef.current?.abort();
    syncRequestRef.current?.abort();
    loadingConversationRef.current = null;
    requestRef.current = null;
    updateQueue([]); setQueuePaused(false); setQueueSendingId(null); queueSendingRef.current = null;
    setEditingQueuedId(null); editingQueuedRef.current = null; queueEditDraftRef.current = "";
    statusRequestRef.current = null;
    pendingRef.current = "";
    inlineHistory.current.clear(); inlineTools.current.clear(); retiredInlineText.current.clear(); removedUrls.current.clear(); deletedConversations.current.clear();
    backendSessions.current.clear();
    archivedConversations.current.clear();
    offeredConversations.current.clear();
    historyRef.current = [];
    failedContextRef.current = undefined;
    setActiveConversationId("initial-chat");
    activeConversationRef.current = "initial-chat";
    setConversations([{ id: "initial-chat", title: "새 대화" }]);
    setMessages([]);
    setDraft("");
    setContext(undefined);
    setStatus(null);
    setStatusLoading(memberStatus !== "unavailable");
    setStatusError(memberStatus === "unavailable" ? "로그인 상태를 확인하지 못했어요." : "");
    setPending("");
    setStreaming("");
    setTimeline([]);
    setEditingMessageId(null);
    setFailed("");
    setError("");
    setNotice("");
  }, [identity, memberStatus, stopUploads, updateAttachments, updateQueue]);

  // 회원은 계정의 대화를, 비회원은 guest_id 쿠키의 대화를 불러온다 (쿠키가 없으면 빈 목록).
  useEffect(() => {
    if (!mode || identityRef.current !== identity) return;
    invalidateHistory();
    const controller = new AbortController(), expectedIdentity = identity;
    historyRequestRef.current = controller;
    void listChatSessions(mode, controller.signal).then(sessions => {
      commitChatLoad(controller.signal, () => identityRef.current === expectedIdentity, () => {
        inlineHistory.current.clear(); inlineTools.current.clear(); retiredInlineText.current.clear(); removedUrls.current.clear(); deletedConversations.current.clear();
    backendSessions.current.clear();
        if (!sessions.length) return;
        const rooms = sessions.map(room => ({ id: `${mode}:${room.id}`, title: room.title || "새 대화" }));
        rooms.forEach((room, index) => backendSessions.current.set(room.id, sessions[index].id));
        const first = rooms[0];
        setConversations(rooms);
        setActiveConversationId(first.id);
        activeConversationRef.current = first.id;
        loadingConversationRef.current = first.id;
        setNotice("대화 기록을 불러오고 있어요.");
        void restoreConversation(first.id, sessions[0].id, controller, expectedIdentity);
      });
    }).catch(cause => {
      if (!controller.signal.aborted && identityRef.current === expectedIdentity) setError(cause instanceof Error ? cause.message : "대화방을 불러오지 못했어요.");
    });
    return () => controller.abort();
  }, [accountId, identity, invalidateHistory, mode, restoreConversation]);

  const registerCourseTarget = useCallback((target: CourseTarget | null) => {
    if (courseTargetRef.current?.stadiumCode !== target?.stadiumCode || courseTargetRef.current?.selectionKey !== target?.selectionKey) courseTargetVersionRef.current++;
    courseTargetRef.current = target;
    setCourseTarget(target);
  }, []);
  const prepareWriterCourse = useCallback((course: ChatCourse, restoreSaved = true) => {
    const message = historyRef.current.find(item => item.course === course && item.role === "assistant" && item.status === "completed");
    return restoreWriterCourse(course, identityRef.current, backendSessions.current.get(activeConversationRef.current), message?.id, undefined, restoreSaved);
  }, []);
  const applyChatCourse = useCallback((course: ChatCourse, how: "replace" | "append") => {
    const target = courseTargetRef.current;
    if (!target) return;
    const had = target.stopCount, otherStadium = Boolean(course.stadiumCode && course.stadiumCode !== target.stadiumCode);
    const undo = target.apply(prepareWriterCourse(course, false), how);
    const message = !undo ? "이 코스를 지도에 담지 못했어요. 구장을 확인해 주세요."
      : otherStadium ? "구장을 바꾸고 추천 코스를 옆 지도에 그렸어요."
      : how === "append" ? "내 코스 뒤에 이어 담았어요."
      : course.edit ? "요청한 변경을 지도에 반영했어요. 이동 시간과 경로도 갱신돼요."
      : had ? `옆 지도에 추천 코스를 그렸어요. 원래 담아둔 ${had}곳은 되돌리기로 복구할 수 있어요.`
      : "옆 지도에 추천 코스를 그렸어요. 순서는 내 코스에서 바꿀 수 있어요.";
    setAppliedCourses(current => new Map(current).set(course, { undo, message }));
  }, [prepareWriterCourse]);
  const undoChatCourse = useCallback((course: ChatCourse) => {
    const restored = appliedCoursesRef.current.get(course)?.undo?.();
    setAppliedCourses(current => new Map(current).set(course, { undo: null, message: restored === false
      ? "추천 적용 후 코스를 수정해 최신 내용을 유지했어요." : "담기 전 코스로 되돌렸어요." }));
  }, []);

  const enqueue = useCallback((content: string, selectedContext?: ChatContext, first = false, edit?: QueuedQuestion["edit"], input?: { attachments: ChatAttachmentDraft[]; toolGroupIds: string[] }) => {
    if (!content.trim() || content.length > MAX_MESSAGE_LENGTH || identityRef.current !== identity) return false;
    if (queuedRef.current.length >= QUEUE_LIMIT) {
      setNotice("예약은 최대 2개까지 가능해요. 기존 예약을 수정하거나 취소해 주세요.");
      return false;
    }
    const item = { id: ++queueSequenceRef.current, content: content.trim(), context: selectedContext, ...input, ...(edit ? { edit } : {}) };
    updateQueue(first ? [item, ...queuedRef.current] : [...queuedRef.current, item]);
    stayHere();
    setNotice("");
    return true;
  }, [identity, stayHere, updateQueue]);

  const cancelQueuedEdit = useCallback(() => {
    if (editingQueuedRef.current === null) return;
    editingQueuedRef.current = null; setEditingQueuedId(null);
    setDraft(queueEditDraftRef.current); queueEditDraftRef.current = "";
  }, []);
  const editQueued = useCallback((id: number) => {
    if (queueSendingRef.current === id) return;
    const item = queuedRef.current.find(item => item.id === id);
    if (!item) return;
    stayHere();
    if (editingQueuedRef.current === null) queueEditDraftRef.current = draft;
    editingQueuedRef.current = id; setEditingQueuedId(id);
    setEditingMessageId(null); setDraft(item.content);
  }, [draft, stayHere]);
  const removeQueued = useCallback((id: number) => {
    if (queueSendingRef.current === id) return;
    updateQueue(queuedRef.current.filter(item => item.id !== id));
    if (editingQueuedRef.current === id) cancelQueuedEdit();
  }, [cancelQueuedEdit, updateQueue]);

  const send = useCallback(async (text: string | undefined = undefined, options?: { context?: ChatContext; editId?: number | null; confirmedEdit?: string; preserveDraft?: boolean; queueId?: number; attachments?: ChatAttachmentDraft[]; toolGroupIds?: string[]; onAccepted?: () => void }): Promise<"rejected" | "failed" | "succeeded"> => {
    stayHere();
    const requestedTarget = courseTargetRef.current;
    const requestedTargetVersion = courseTargetVersionRef.current;
    const requestedCourseVersion = requestedTarget?.getVersion?.();
    const suppliedContext = options ? options.context : context;
    // 재시도·대화 전환 직후에도 현재 지도 구장을 사용한다.
    const selectedContext = requestedTarget
      ? requestedTarget.getContext?.() ?? { ...context, stadium: requestedTarget.stadiumCode, intent: "route" as const }
      : suppliedContext;
    const editId = options ? options.editId ?? null : editingMessageId;
    const fromComposer = text === undefined;
    if (fromComposer && !composing.current && !preparingSend.current && !requestRef.current && mode) {
      const urls = inlineChatUrls(draftRef.current);
      const suppressed = removedUrls.current.get(urlScope());
      if (urls.length && !attach(urls.filter(url => !suppressed?.has(url)))) return "rejected";
    }
    let content = fromComposer ? chatComposerContent(draftRef.current, attachmentRef.current) : text.trim();
    if (composing.current || preparingSend.current || requestRef.current || !content || !mode || identityRef.current !== identity || content.length > MAX_MESSAGE_LENGTH) return "rejected";
    const preparationConversation = activeConversationRef.current;
    const preparationDraftVersion = draftVersion.current;
    const preparationVersion = composerVersion.current;
    let selectedAttachments = options?.attachments ?? (options?.preserveDraft ? [] : attachmentRef.current.filter(item => item.inlineText ? draftRef.current.includes(item.inlineText) : item.kind === "url" && item.sourceUrl ? inlineChatUrls(draftRef.current).includes(item.sourceUrl) : true));
    const selectedGroups = options?.toolGroupIds ?? toolGroupIds;
    if (new Set(selectedAttachments.map(item => item.attachment?.id ?? item.key)).size > 10) { setNotice("파일·이미지·주소는 합계 10개까지 첨부할 수 있어요."); return "rejected"; }
    const selectedKeys = selectedAttachments.map(item => item.key);
    const uploads = selectedKeys.map(key => uploadPromises.current.get(key)).filter((promise): promise is Promise<void> => Boolean(promise));
    if (uploads.length) {
      preparingSend.current = true;
      try { await Promise.all(uploads); } finally { preparingSend.current = false; }
    }
    if (identityRef.current !== identity || activeConversationRef.current !== preparationConversation || composerVersion.current !== preparationVersion || draftVersion.current !== preparationDraftVersion || requestRef.current) return "rejected";
    if (!options?.attachments && !options?.preserveDraft) {
      if (selectedKeys.some(key => !attachmentRef.current.some(item => item.key === key))) return "rejected";
      selectedAttachments = attachmentRef.current.filter(item => selectedKeys.includes(item.key));
    }
    const unready = selectedAttachments.find(item => item.state !== "ready");
    if (unready) { setNotice(unready.error ?? "첨부 업로드를 완료하거나 실패한 자료를 제거한 뒤 보내 주세요."); return "rejected"; }
    if (fromComposer) {
      content = chatComposerContent(draftRef.current, selectedAttachments);
    }
    const submittedDraft = fromComposer ? draftRef.current : !options?.preserveDraft && restoredDraftRef.current?.content === content ? restoredDraftRef.current.draft : content;
    const inputOptions = { toolGroupIds: selectedGroups, attachmentIds: [...new Set(selectedAttachments.map(item => item.attachment!.id))] };
    if (identityRef.current !== identity || !content || requestRef.current || content.length > MAX_MESSAGE_LENGTH) return "rejected";
    if (loadingConversationRef.current === activeConversationId) {
      setNotice("대화 기록을 불러온 뒤 보내 주세요.");
      return "rejected";
    }
    if (!mode) { setError("로그인 상태를 확인한 뒤 다시 시도해 주세요."); return "rejected"; }
    if (sessionCreation.current?.conversation === activeConversationId) { setNotice("첨부 대화를 준비한 뒤 보내 주세요."); return "rejected"; }
    const sessionId = backendSessions.current.get(activeConversationId);
    const editIndex = editId === null ? -1 : historyRef.current.findIndex(message => message.id === editId && message.role === "user");
    if (editId !== null && (!sessionId || editIndex < 0)) { setEditingMessageId(null); setError("수정할 질문을 찾지 못했어요."); return "rejected"; }
    const historyStamp = editId === null ? "" : editHistoryStamp(historyRef.current, editId);
    if (editId !== null && options?.confirmedEdit !== historyStamp && !window.confirm("이 질문 이후의 대화는 모두 지워지고 답변을 새로 받아요. 계속할까요?")) return "rejected";
    const controller = new AbortController();
    const version = ++requestVersion.current;
    const conversationId = activeConversationId, expectedIdentity = identity;
    invalidateHistory();
    invalidateSync();
    const preserveDraft = options?.preserveDraft || preEditComposer.current !== null;
    selectedAttachments.forEach(item => { if (item.preview) sentPreviews.current.add(item.preview); });
    restorePreEditComposer();
    const active = { controller, version, stopped: false };
    requestRef.current = active;
    if (options?.queueId !== undefined) {
      queueSendingRef.current = options.queueId;
      setQueueSendingId(options.queueId);
    }
    const acknowledgeQueue = () => {
      if (options?.queueId !== undefined && queuedRef.current.some(item => item.id === options.queueId))
        updateQueue(queuedRef.current.filter(item => item.id !== options.queueId));
    };
    options?.onAccepted?.();
    offerCancelledRef.current = false;
    pendingRef.current = content;
    setPending(content);
    restoredDraftRef.current = null;
    if (!preserveDraft) {
      const scope = urlScope(), retired = retiredInlineText.current.get(scope) ?? new Map<string, string>();
      for (const item of [...(inlineHistory.current.get(scope)?.values() ?? []), ...attachmentRef.current]) if (item.inlineText) retired.set(item.inlineText, item.pastedText ?? `첨부 참고 자료: ${item.name}`);
      retiredInlineText.current.set(scope, retired);
      inlineHistory.current.delete(scope);
      setDraft(""); draftRef.current = "";
      updateAttachments(items => items.filter(item => !item.inlineText && !selectedKeys.includes(item.key)));
    }
    const acceptedDraftVersion = draftVersion.current;
    const acceptedComposerVersion = composerVersion.current;
    setEditingMessageId(null);
    setError("");
    setNotice("");
    setFailed("");
    setStreaming("");
    setTimeline([]);
    failedContextRef.current = undefined;
    retryRef.current = null;
    const userMessage: ChatMessage = { role: "user", content, toolGroupIds: selectedGroups, attachments: selectedAttachments.map(item => ({ ...item.attachment!, ...(item.preview ? { preview: item.preview } : {}) })) };
    const originalHistory = historyRef.current;
    const previous = editIndex >= 0 ? originalHistory.slice(0, editIndex) : originalHistory;
    historyRef.current = [...previous, userMessage];
    setMessages(historyRef.current);
    if (previous.length === 0) {
      setConversations(current => current.map(item => item.id === conversationId
        ? { ...item, title: content.replace(/\s+/g, " ").slice(0, 48) }
        : item));
    }
    let knownSession = sessionId;
    try {
      const onDelta = (piece: string, parentId: string | null) => {
        if (version !== requestVersion.current) return "rejected";
        acknowledgeQueue();
        setStreaming(current => current + piece);
        setTimeline(current => appendTimeline(current, piece, parentId));
      };
      const onTool = (tool: ChatToolCallDto) => {
        if (version !== requestVersion.current) return "rejected";
        acknowledgeQueue();
        setTimeline(current => appendTimeline(current, fromToolDto(tool)));
      };
      const onPlanning = (payload: ChatPlanning) => {
        if (version !== requestVersion.current || !payload.offer_writer || hasEmbeddedChat || offerCancelledRef.current || offeredConversations.current.has(conversationId)) return "rejected";
        offeredConversations.current.add(conversationId);
        writerOfferRef.current = { deadline: Date.now() + 20_000, conversation: conversationId, path: pathname, identity: expectedIdentity };
        setWriterSeconds(20);
        setWriterAnnouncement("20초 후 루트 작성 화면으로 자동 이동해요. 여기 머무르기를 누르면 자동 이동을 취소할 수 있어요.");
      };
      const response = editId !== null && sessionId
        ? await editChatMessage(mode, { sessionId, messageId: editId, content, context: selectedContext, ...inputOptions }, controller.signal, { onDelta, onTool, onPlanning })
        : await sendChatMessage(mode, { sessionId, content, context: selectedContext, ...inputOptions }, controller.signal, { onDelta, onTool, onPlanning });
      if (version !== requestVersion.current) return "rejected";
      const reply = { ...response, course: response.course ? inheritCourseState(response.course, previous.findLast(item => item.course)?.course) : undefined };
      acknowledgeQueue();
      setQueuePaused(false);
      knownSession = reply.sessionId;
      if (reply.sessionId) backendSessions.current.set(conversationId, reply.sessionId);
      const assistant: ChatMessage = { role: "assistant", content: reply.reply, planning: reply.planning, course: reply.course, coursePreferences: reply.coursePreferences, status: "completed", ...(reply.assistantMessageId ? { id: reply.assistantMessageId } : {}), ...(reply.tools?.length ? { tools: reply.tools } : {}), ...(reply.timeline?.length ? { timeline: reply.timeline } : {}) };
      const next: ChatMessage[] = [...previous, { ...userMessage, status: "completed" }, assistant];
      historyRef.current = next;
      setMessages(next);
      setStatus({ provider: reply.provider, model: reply.model, ready: reply.ready });
      const target = courseTargetRef.current;
      if (reply.course && requestedTarget && target) {
        // 이번 질문에서 다른 팀·구장을 지정했다면 코스가 정한 구장으로 지도도 옮긴다.
        // 요청 이후 직접 구장을 바꿨다면 (바꿨다가 돌아온 경우도) 최신 선택을 지킨다.
        if (requestedCourseVersion !== undefined
          ? target.getContext === requestedTarget.getContext && target.getVersion?.() === requestedCourseVersion
          : courseTargetVersionRef.current === requestedTargetVersion) {
          applyChatCourse(reply.course, "replace");
        } else {
          setAppliedCourses(current => new Map(current).set(reply.course!, { undo: null, message: "답변을 기다리는 중 구장이나 경로를 바꿔 새 선택을 유지했어요. 이 코스는 확인한 뒤 직접 담을 수 있어요." }));
        }
      }
      return "succeeded";
    } catch (cause) {
      if (version !== requestVersion.current) return "rejected";
      stayHere();
      if (cause instanceof ChatClientError && cause.sessionId) {
        knownSession = cause.sessionId;
        backendSessions.current.set(conversationId, cause.sessionId);
      }
      if (cause instanceof ChatClientError && cause.code === USAGE_BUSY && !active.stopped) {
        // 거절된 POST/PUT는 서버가 수락하지 않았다. 편집 대상과 승인 범위를 그대로 예약한다.
        if (editId !== null) { historyRef.current = originalHistory; setMessages(originalHistory); }
        if (options?.queueId !== undefined || enqueue(content, selectedContext, true,
          editId === null ? undefined : { messageId: editId, historyStamp }, { attachments: selectedAttachments, toolGroupIds: selectedGroups })) {
          setServerBusyQueueId(queuedRef.current[0]?.id ?? null);
          setQueuePaused(false);
          return "rejected";
        }
      }
      acknowledgeQueue();
      setQueuePaused(true);
      historyRef.current = previous;
      setMessages(previous);
      if (!preserveDraft && draftVersion.current === acceptedDraftVersion && composerVersion.current === acceptedComposerVersion) {
        setDraft(current => {
          if (current.trim()) return current;
          restoredDraftRef.current = { content, draft: submittedDraft, draftVersion: acceptedDraftVersion, composerVersion: acceptedComposerVersion };
          return submittedDraft;
        });
        selectedAttachments.forEach(item => { if (item.inlineText) retiredInlineText.current.get(urlScope())?.delete(item.inlineText); });
        updateAttachments(items => [...selectedAttachments.filter(sent => !items.some(item => item.key === sent.key)), ...items]);
      }
      retryRef.current = { content, context: selectedContext, messageId: editId, attachments: selectedAttachments, toolGroupIds: selectedGroups, conversation: conversationId, identity: expectedIdentity };
      if (active.stopped || cause instanceof ChatStreamStoppedError) {
        if (!preserveDraft && composerVersion.current === acceptedComposerVersion && draftVersion.current !== acceptedDraftVersion) {
          selectedAttachments.forEach(item => { if (item.inlineText) retiredInlineText.current.get(urlScope())?.delete(item.inlineText); });
        updateAttachments(items => [...selectedAttachments.filter(sent => !items.some(item => item.key === sent.key)), ...items]);
        }
        // 중단은 이 화면의 연결만 끊는다. 서버에는 답변 없는 질문이 남을 수 있어 기록을 다시 읽는다.
        setNotice(cause instanceof ChatStreamStoppedError ? cause.message : "답변 받기를 중단했어요. 받던 답변은 저장되지 않아요.");
      } else {
        setFailed(content);
        failedContextRef.current = selectedContext;
        setError(cause instanceof Error ? cause.message : "답변을 가져오지 못했어요. 다시 시도해 주세요.");
      }
      return "failed";
    } finally {
      if (version === requestVersion.current) {
        requestRef.current = null;
        queueSendingRef.current = null; setQueueSendingId(null);
        pendingRef.current = "";
        setPending("");
        setStreaming("");
        setTimeline([]);
        if (knownSession) syncConversation(conversationId, knownSession, expectedIdentity, content, userMessage.attachments);
      }
    }
  }, [activeConversationId, context, editingMessageId, identity, invalidateHistory, invalidateSync, mode, syncConversation, stayHere, hasEmbeddedChat, pathname, toolGroupIds, updateAttachments, restorePreEditComposer, attach, urlScope, applyChatCourse, enqueue, updateQueue]);

  const submitDraft = useCallback(() => {
    stayHere();
    const content = chatComposerContent(draftRef.current, attachmentRef.current);
    if (!content || content.length > MAX_MESSAGE_LENGTH || identityRef.current !== identity) return;
    const editingId = editingQueuedRef.current;
    if (editingId !== null) {
      updateQueue(queuedRef.current.map(item => item.id === editingId ? { ...item, content } : item));
      cancelQueuedEdit();
      return;
    }
    if (editingMessageId !== null) { void send(); return; }
    if (requestRef.current || queuedRef.current.length) {
      if (attachmentRef.current.some(item => item.state !== "ready")) { setNotice("첨부 업로드를 완료한 뒤 예약해 주세요."); return; }
      if (enqueue(content, context, false, undefined, { attachments: attachmentRef.current, toolGroupIds })) { setDraft(""); draftRef.current = ""; updateAttachments(() => []); }
    } else void send();
  }, [cancelQueuedEdit, context, editingMessageId, enqueue, identity, send, updateQueue, stayHere, toolGroupIds, updateAttachments]);

  useEffect(() => {
    if (!queued.length || pending || requestRef.current || queuePaused || editingQueuedId !== null || editingMessageId !== null
        || !mode || !status?.ready || statusLoading || identityChanged || loadingConversationRef.current) return;
    const controller = new AbortController();
    const item = queued[0], conversation = activeConversationId, owner = identity;
    let timer: number | undefined;
    const current = () => !controller.signal.aborted && identityRef.current === owner && activeConversationRef.current === conversation
      && queuedRef.current[0] === item && editingQueuedRef.current === null && !requestRef.current;
    async function advance() {
      try {
        const usage = await fetchChatUsage(mode!, controller.signal);
        if (!current()) return;
        if (usage.active_turn) {
          setServerBusyQueueId(item.id);
          timer = window.setTimeout(() => void advance(), 1200); return;
        }
        setServerBusyQueueId(null);
        if (!usage.can_send) {
          setQueuePaused(true); setNotice("사용 가능한 제공량이 없어 예약을 멈췄어요. 예약 내용은 남겨뒀어요."); return;
        }
        if (item.edit) {
          const sessionId = backendSessions.current.get(conversation);
          if (!sessionId) { setQueuePaused(true); setNotice("수정할 대화를 찾지 못했어요. 예약을 취소하고 다시 선택해 주세요."); return; }
          const history = await fetchChatHistory(mode!, sessionId, controller.signal);
          if (!current()) return;
          historyRef.current = restoreChatMessages(history, historyRef.current);
          setMessages(historyRef.current);
          if (editHistoryStamp(historyRef.current, item.edit.messageId) !== item.edit.historyStamp) {
            setQueuePaused(true); setNotice("예약을 기다리는 동안 대화가 바뀌었어요. 내용을 확인한 뒤 예약 계속을 눌러 주세요."); return;
          }
        }
        // 지도는 직전 답변의 적용 결과를 사용하고, 지도 없는 화면은 서버의 최신 코스를 이어받는다.
        const nextContext = item.context ? { ...item.context, currentCourse: undefined } : undefined;
        void send(item.content, { context: nextContext, editId: item.edit?.messageId ?? null,
          confirmedEdit: item.edit?.historyStamp, preserveDraft: true, queueId: item.id, attachments: item.attachments, toolGroupIds: item.toolGroupIds ?? [] });
      } catch {
        if (current()) { setQueuePaused(true); setNotice("연결을 확인하지 못해 예약을 잠시 멈췄어요. 예약 계속을 눌러 주세요."); }
      }
    }
    void advance();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [queued, pending, queuePaused, editingQueuedId, editingMessageId, mode, status?.ready, statusLoading, identityChanged, activeConversationId, identity, send]);

  const resumeQueue = useCallback(() => {
    const first = queuedRef.current[0];
    if (first?.edit) {
      const stamp = editHistoryStamp(historyRef.current, first.edit.messageId);
      if (!stamp) { setNotice("수정할 질문이 없어졌어요. 해당 예약을 취소해 주세요."); return; }
      if (stamp !== first.edit.historyStamp) {
        if (!window.confirm("이 질문 이후의 바뀐 대화도 지워지고 답변을 새로 받아요. 계속할까요?")) return;
        updateQueue(queuedRef.current.map(item => item.id === first.id ? { ...item, edit: { ...first.edit!, historyStamp: stamp } } : item));
      }
    }
    setQueuePaused(false); setNotice("");
  }, [updateQueue]);

  // 이 탭의 대기 질문은 페이지 이동에는 유지되지만 창을 닫으면 전송할 수 없다.
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (queuedRef.current.length || [...archivedConversations.current.values()].some(saved => saved.queued?.length)) {
        event.preventDefault(); event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);

  const submitQuestions = useCallback(async (messageId: number, text: string, onAccepted?: () => void) => {
    const index = historyRef.current.findIndex(m => m.id === messageId && m.role === "assistant" && m.planning);
    if (index < 0 || index !== historyRef.current.length - 1 || requestRef.current || queuedRef.current.length || !text.trim() || text.length > MAX_MESSAGE_LENGTH) return "rejected" as const;
    return send(text, { context, editId: null, preserveDraft: true, onAccepted });
  }, [context, send]);

  const editMessage = useCallback((id: number) => {
    stayHere();
    if (requestRef.current || loadingConversationRef.current) return;
    const target = historyRef.current.find(message => message.id === id && message.role === "user");
    if (!target) return;
    invalidateSync();
    stopUploads();
    if (!preEditComposer.current) preEditComposer.current = { draft, context, attachments: attachmentRef.current, toolGroupIds, notice };
    else attachmentRef.current.forEach(item => { if (item.preview && !sentPreviews.current.has(item.preview)) URL.revokeObjectURL(item.preview); });
    setEditingMessageId(id);
    let editDraft = target.content;
    const restoredAttachments: ChatAttachmentDraft[] = (target.attachments ?? []).map(attachment => {
      const number = attachment.name.match(/^Pasted Text (\d+)\.txt$/)?.[1];
      const inlineText = attachment.kind === "text" && number && target.content.includes(`첨부 참고 자료: ${attachment.name}`) ? `[[ Text ${number} ]]` : undefined;
      if (inlineText) editDraft = editDraft.split(`첨부 참고 자료: ${attachment.name}`).join(inlineText);
      return { key: createClientId(), name: attachment.name, kind: attachment.kind, size: attachment.size, attachment, state: "ready", ...(inlineText ? { inlineText } : {}) };
    });
    setDraft(editDraft);
    updateAttachments(() => restoredAttachments);
    setToolGroupIds(target.toolGroupIds ?? []);
    setNotice("질문을 고쳐 보내면 이 질문 이후의 대화는 지워져요.");
  }, [invalidateSync, stayHere, stopUploads, updateAttachments, draft, context, toolGroupIds, notice]);

  const cancelEdit = restorePreEditComposer;

  const deleteMessage = useCallback(async (id: number) => {
    stayHere();
    const sessionId = backendSessions.current.get(activeConversationId);
    if (requestRef.current || loadingConversationRef.current || !mode || !sessionId) return;
    const target = historyRef.current.find(message => message.id === id);
    if (!target || (target.role === "assistant" && target.status !== "completed")) return;
    const answerOnly = target.role === "assistant";
    if (!window.confirm(answerOnly ? "이 답변만 지울까요? 질문과 다른 대화는 그대로 남아요." : "이 질문과 이후 대화를 모두 지울까요? 지운 대화는 되돌릴 수 없어요.")) return;
    const controller = new AbortController();
    const version = ++requestVersion.current;
    const conversationId = activeConversationId, expectedIdentity = identity;
    invalidateSync();
    restorePreEditComposer();
    requestRef.current = { controller, version, stopped: false };
    setEditingMessageId(null);
    setError("");
    setNotice("대화를 지우고 있어요.");
    try {
      await deleteChatMessages(mode, sessionId, id, controller.signal);
      if (version !== requestVersion.current) return;
      const index = historyRef.current.findIndex(message => message.id === id);
      historyRef.current = answerOnly ? historyRef.current.filter(message => message.id !== id) : historyRef.current.slice(0, index);
      setMessages(historyRef.current);
      setFailed("");
      retryRef.current = null;
      setNotice(answerOnly ? "선택한 답변만 지웠어요." : "선택한 질문부터 이후 대화를 지웠어요.");
    } catch (cause) {
      if (version !== requestVersion.current) return;
      setNotice("");
      setError(cause instanceof Error ? cause.message : "대화를 지우지 못했어요.");
    } finally {
      if (version === requestVersion.current) {
        requestRef.current = null;
        syncConversation(conversationId, sessionId, expectedIdentity);
      }
    }
  }, [activeConversationId, identity, invalidateSync, mode, syncConversation, stayHere, restorePreEditComposer]);

  const feedbackRequests = useRef(new Set<string>());
  const saveFeedback = useCallback(async (id: number, feedback: AnswerFeedback | null) => {
    const sessionId = backendSessions.current.get(activeConversationId);
    const requestKey = JSON.stringify([identity, sessionId, id]);
    if (!mode || !sessionId || identityRef.current !== identity || requestRef.current || feedbackRequests.current.has(requestKey)) throw new Error("잠시 후 다시 평가해 주세요.");
    const target = historyRef.current.find(message => message.id === id && message.role === "assistant" && message.status === "completed");
    if (!target) throw new Error("저장된 완료 답변만 평가할 수 있어요.");
    const conversationId = activeConversationId, expectedIdentity = identity;
    feedbackRequests.current.add(requestKey);
    invalidateSync();
    try {
      const saved = await saveAnswerFeedback(mode, sessionId, id, feedback);
      if (identityRef.current !== expectedIdentity) return;
      if (activeConversationRef.current === conversationId) invalidateSync();
      const update = (items: ChatMessage[]) => items.map(message => message.id === id ? { ...message, feedback: saved } : message);
      if (activeConversationRef.current === conversationId) {
        historyRef.current = update(historyRef.current);
        setMessages(historyRef.current);
      }
      const archived = archivedConversations.current.get(conversationId);
      if (archived) archivedConversations.current.set(conversationId, { ...archived, messages: update(archived.messages) });
    } finally { feedbackRequests.current.delete(requestKey); }
  }, [activeConversationId, identity, invalidateSync, mode]);

  const retry = useCallback(() => {
    const target = retryRef.current;
    if (!target || target.conversation !== activeConversationRef.current || target.identity !== identityRef.current) return;
    // 서버에 실패한 질문이 남아 있으면 같은 자리에서 다시 받는다 (질문이 겹쳐 쌓이지 않게).
    const stored = target.messageId !== null && historyRef.current.some(message => message.id === target.messageId);
    const restored = restoredDraftRef.current;
    const preserveDraft = editingMessageId !== null || restored?.content !== target.content || draft !== restored.draft
      || draftVersion.current !== restored.draftVersion || composerVersion.current !== restored.composerVersion
      || attachmentRef.current.length !== target.attachments.length || attachmentRef.current.some((item, index) => item !== target.attachments[index])
      || toolGroupIds.length !== target.toolGroupIds.length || toolGroupIds.some((id, index) => id !== target.toolGroupIds[index]);
    void send(target.content, { context: target.context, editId: stored ? target.messageId : null, preserveDraft, attachments: target.attachments, toolGroupIds: target.toolGroupIds });
  }, [send, draft, editingMessageId, toolGroupIds]);

  const openCourseInWriter = useCallback((course: ChatCourse) => {
    pendingCourseRef.current = prepareWriterCourse(course);
    setPopupRequested(false);
    router.push(`/routes/new${course.stadiumCode ? `?stadium=${encodeURIComponent(course.stadiumCode)}` : ""}`);
  }, [router, prepareWriterCourse]);
  const takePendingCourse = useCallback(() => {
    const course = pendingCourseRef.current;
    pendingCourseRef.current = null;
    return course;
  }, []);

  const openChat = useCallback((initialMessage?: string, nextContext?: ChatContext) => {
    expandChat();
    if (nextContext) setContext(nextContext);
    if (requestRef.current || queuedRef.current.length) {
      if (initialMessage?.trim()) {
        if (!enqueue(initialMessage.slice(0, MAX_MESSAGE_LENGTH), nextContext ?? context)) changeDraft(initialMessage.slice(0, MAX_MESSAGE_LENGTH));
      }
      return;
    }
    if (initialMessage?.trim()) void send(initialMessage.slice(0, MAX_MESSAGE_LENGTH), { context: nextContext ?? context, editId: null });
  }, [changeDraft, context, expandChat, send, enqueue]);

  useEffect(() => {
    if (isChatPage || !restorePageRef.current) return;
    restorePageRef.current = false;
    const frame = requestAnimationFrame(() => window.scrollTo({ top: returnPageRef.current.scrollY, behavior: "instant" }));
    return () => cancelAnimationFrame(frame);
  }, [isChatPage]);

  useEffect(() => {
    if (!isChatPage && !popupOpen && !hasEmbeddedChat) return;
    statusRequestRef.current?.abort();
    if (!mode) return;
    const controller = new AbortController();
    statusRequestRef.current = controller;
    void loadStatus(mode, controller);
    return () => controller.abort();
  }, [accountId, hasEmbeddedChat, isChatPage, popupOpen, loadStatus, mode]);

  useEffect(() => () => {
    uploadRequests.current.forEach(controller => controller.abort());
    sentPreviews.current.forEach(preview => URL.revokeObjectURL(preview));
    sentPreviews.current.clear();
    attachmentRef.current.forEach(item => { if (item.preview && !sentPreviews.current.has(item.preview)) URL.revokeObjectURL(item.preview); });
    preEditComposer.current?.attachments.forEach(item => { if (item.preview && !sentPreviews.current.has(item.preview)) URL.revokeObjectURL(item.preview); });
    preEditComposer.current = null;
    archivedConversations.current.forEach(saved => saved.attachments?.forEach(item => { if (item.preview && !sentPreviews.current.has(item.preview)) URL.revokeObjectURL(item.preview); }));
    requestVersion.current += 1;
    requestRef.current?.controller.abort();
    statusRequestRef.current?.abort();
    historyRequestRef.current?.abort();
    syncRequestRef.current?.abort();
  }, []);

  const visibleStatus = mode ? status : null;
  const visibleStatusLoading = memberStatus === "loading" || (Boolean(mode) && statusLoading);
  const visibleStatusError = memberStatus === "unavailable" ? "로그인 상태를 확인하지 못했어요." : mode ? statusError : "";

  return (
    <ChatControlsContext.Provider value={{
      writerSeconds: identityChanged ? null : writerSeconds, writerAnnouncement: identityChanged ? "" : writerAnnouncement, stayHere, goToWriter, submitQuestions,
      attachments: identityChanged ? [] : attachments, toolGroupIds: identityChanged ? [] : toolGroupIds,
      onToolGroupsChange: ids => { if (!requestRef.current) { stayHere(); invalidateHistory(); const tools = inlineTools.current.get(urlScope()); tools?.forEach((tool, id) => { if (!ids.includes(id)) tool.explicit = false; else if (ids.filter(value => value === id).length > 1 || !toolGroupIds.includes(id)) tool.explicit = true; }); setToolGroupIds([...new Set(ids)]); } },
      onInlineToolSelect: (id, label, restored = false) => { if (!requestRef.current) { const scope = urlScope(), tools = inlineTools.current.get(scope) ?? new Map<string, { marker: string; explicit: boolean }>(); tools.set(id, { marker: `@${label}`, explicit: tools.get(id)?.explicit ?? (!restored && toolGroupIds.includes(id)) }); inlineTools.current.set(scope, tools); if (!restored) setToolGroupIds(ids => [...new Set([...ids, id])]); } },
      onAttach: attach, onPasteText: pasteText, onCommitUrls: commitUrls, onCancelAttachment: cancelUpload,
      onCompositionChange: value => { composing.current = value; },
      onRetryAttachment: key => { const item = attachmentRef.current.find(item => item.key === key); if (item && item.state !== "uploading") startUpload(item); },
      onRemoveAttachment: key => { if (requestRef.current) return; const item = attachmentRef.current.find(item => item.key === key); if (!item?.inlineText) { uploadRequests.current.get(key)?.abort(); uploadRequests.current.delete(key); } if (item?.inlineText && item.pastedText && draftRef.current.includes(item.inlineText)) changeDraft(draftRef.current.split(item.inlineText).join(item.pastedText)); if (item?.inlineText) { const scope = urlScope(), history = inlineHistory.current.get(scope) ?? new Map<string, ChatAttachmentDraft>(); history.set(item.key, item); inlineHistory.current.set(scope, history); } if (item?.kind === "url") { const url = normalizeChatUrl(item.sourceUrl ?? item.attachment?.url ?? item.name); if (url) { const scope = urlScope(); const removed = removedUrls.current.get(scope) ?? new Set<string>(); removed.add(url); removedUrls.current.set(scope, removed); } } if (item?.preview && !sentPreviews.current.has(item.preview)) URL.revokeObjectURL(item.preview); updateAttachments(items => items.filter(item => item.key !== key)); },
      openChat, onExpand: expandChat, onMinimize: minimizeChat, onClosePopup: closePopup,
      messages: identityChanged ? [] : messages,
      draft: identityChanged ? "" : draft,
      context: identityChanged ? undefined : context,
      status: visibleStatus,
      statusLoading: visibleStatusLoading,
      statusError: visibleStatusError,
      pending: identityChanged ? "" : pending,
      streaming: identityChanged ? "" : streaming,
      timeline: identityChanged ? [] : timeline,
      editingMessageId: identityChanged ? null : editingMessageId,
      queued: identityChanged ? [] : queued,
      queuePaused: identityChanged ? false : queuePaused,
      queueSendingId: identityChanged ? null : queueSendingId,
      queueWaitingForServer: !identityChanged && queued.length > 0 && queued[0].id === serverBusyQueueId,
      editingQueuedId: identityChanged ? null : editingQueuedId,
      onEditQueued: editQueued, onRemoveQueued: removeQueued, onCancelQueuedEdit: cancelQueuedEdit,
      onResumeQueue: resumeQueue,
      failed: identityChanged ? "" : failed,
      error: identityChanged ? "" : error,
      notice: identityChanged ? "" : notice,
      conversations: identityChanged ? [{ id: "initial-chat", title: "새 대화" }] : conversations,
      activeConversationId: identityChanged ? "initial-chat" : activeConversationId,
      onDraftChange: changeDraft, onRefreshStatus: () => void refreshStatus(),
      onSend: submitDraft, onRetry: retry,
      onCancel: cancelRequest, onReset: resetChat,
      onSuggestion: (text, intent) => { changeDraft(text); setContext(current => ({ ...current, intent })); },
      onSelectConversation: selectConversation,
      onDeleteConversation: deleteConversation,
      onEditMessage: editMessage, onCancelEdit: cancelEdit, onDeleteMessage: id => void deleteMessage(id),
      onContextChange: setContext, onFeedback: saveFeedback,
      courseTarget, registerCourseTarget, openCourseInWriter, takePendingCourse,
      appliedCourses, applyChatCourse, undoChatCourse,
    }}>
      {children}
      {!popupOpen && <button ref={topButtonRef} type="button" className="scroll-to-top" aria-label="맨 위로 이동" title="맨 위로 이동" onClick={() => window.scrollTo({ top: 0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" })}>
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m6 11 6-6 6 6M12 5v14" /></svg>
      </button>}
      {popupOpen && <ChatPopup />}
    </ChatControlsContext.Provider>
  );
}
