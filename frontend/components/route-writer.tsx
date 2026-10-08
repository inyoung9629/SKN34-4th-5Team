"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { ChatPopup } from "@/components/chat-popup";
import { CommunityRichEditor } from "@/components/community-rich-editor";
import { NearbyRoutePlanner } from "@/components/nearby-route-planner";
import { RouteGuideButton } from "@/components/route-guide";
import { adaptStadium } from "@/lib/baseball/adapters";
import { fetchBaseballStadiums } from "@/lib/baseball/client";
import type { Stadium } from "@/lib/stadiums";
import { routeContentToText } from "@/lib/route-content";
import { plainRichDoc, richText, type RichContentDoc } from "@/lib/community-rich-content";
import { useMemberAuth } from "@/lib/member-auth";
import { ChatSampleProvider, useChat } from "@/components/chat-provider";
import { retryRoutes, saveRoute, useRoutes, useRoutesError, useRoutesReady, type RouteStop, type TripRoute } from "@/lib/routes";
import { withCourseStart } from "@/lib/drawn-course";
import { createClientId } from "@/lib/client-id";
import { GUIDE_COURSE, type GuideCourseStop } from "@/lib/route-guide-events";
import { teamBoards } from "@/lib/team-community";
import { browserDraftStorage, createDraftAutosave, latestNewDraftStadium, readRouteDraft, recoverRouteDraft, removeRouteDraft, saveRouteDraft, type MemoryRouteDraft, type RouteDraftData } from "@/lib/route-draft";
import type { LegModes, TravelMode } from "@/lib/course-directions";
import { courseContext, courseGeometryKey, type CourseAction } from "@/lib/course-state";
import { useCourseState } from "./use-course-state";
import { chatRouteSelectionKey } from "@/lib/chat/route-path";
import type { ChatCourse, ChatRoutePath } from "@/lib/chat/types";

function WriterIcon({ kind }: { kind: "spark" | "pin" | "arrow" | "save" }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {kind === "spark" && <><path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4L12 3Z" /><path d="m20 2 .6 1.4L22 4l-1.4.6L20 6l-.6-1.4L18 4l1.4-.6L20 2Z" /></>}
      {kind === "pin" && <><path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0Z" /><circle cx="12" cy="10" r="2.5" /></>}
      {kind === "arrow" && <><path d="M5 12h14M13 6l6 6-6 6" /></>}
      {kind === "save" && <><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h12l4 4v12a2 2 0 0 1-2 2Z" /><path d="M7 3v6h10V3M7 21v-8h10v8" /></>}
    </svg>
  );
}

type WriterTab = "write" | "chat";
type PlannerMode = "places" | "draw";
type Confirmation = { title: string; description: string; label: string; action: () => void; cancelLabel?: string; cancelAction?: () => void };
const writerTabs: { id: WriterTab; label: string }[] = [{ id: "write", label: "루트 작성" }, { id: "chat", label: "챗봇" }];
const writerDrafts = new Map<string, MemoryRouteDraft>();
const noClientChange = () => () => {};
const clientReady = () => true;
const serverReady = () => false;
const stadiumKey = (value: string) => value.replace(/\s/g, "").toUpperCase();
// The course's own start: a point picked as "출발지" or a bare map point placed first.
const matchesStadium = (stadium: Stadium, value: string) => stadium.code === stadiumKey(value) || stadiumKey(stadium.name) === stadiumKey(value);

export default function RouteWriter({ editId, copyId, initialStadium }: { editId?: string; copyId?: string; initialStadium?: string }) {
  const hydrated = useSyncExternalStore(noClientChange, clientReady, serverReady);
  const { status: authStatus, reload: reloadMember, user } = useMemberAuth();
  const routes = useRoutes();
  const ready = useRoutesReady();
  const loadError = useRoutesError();
  const [stadiums, setStadiums] = useState<Stadium[] | null>(null);
  const [stadiumError, setStadiumError] = useState("");
  const [invalidCount, setInvalidCount] = useState(0);
  const [attempt, setAttempt] = useState(0);
  // Fixed per visit so the random default stadium does not change on every render.
  const [randomPick] = useState(() => Math.random());
  const [draftStadium, setDraftStadium] = useState<string | undefined>();
  useEffect(() => {
    if (authStatus !== "authenticated" && authStatus !== "anonymous") return;
    const controller = new AbortController();
    fetchBaseballStadiums(controller.signal).then(page => {
      const available = page.results.map(adaptStadium).filter((item): item is Stadium => item !== null);
      setDraftStadium(authStatus === "authenticated" && !initialStadium && !editId && !copyId
        ? latestNewDraftStadium(browserDraftStorage(), available.map(stadium => stadium.code)) : undefined);
      setStadiums(available); setInvalidCount(page.results.length - available.length);
    }).catch(cause => { if (!controller.signal.aborted) setStadiumError(cause instanceof Error ? cause.message : "구장 목록을 불러오지 못했어요."); });
    return () => controller.abort();
  }, [attempt, authStatus, user?.id, initialStadium, editId, copyId]);
  const sourceId = copyId ?? editId;
  const existing = sourceId ? routes.find((route) => route.id === sourceId || route.legacySourceId === sourceId) : undefined;
  if (authStatus === "loading") return <main className="container writer-empty"><p role="status"><span className="writer-spinner" aria-hidden="true" />로그인 상태를 확인하고 있어요.</p></main>;
  if (authStatus === "anonymous" && editId) return <main className="container writer-empty"><span className="eyebrow">MAKE YOUR GAME DAY</span><h1>저장된 코스 수정은 로그인 후 이용할 수 있어요</h1><p>로그인하지 않아도 새로운 코스를 지도에서 구성할 수 있어요.</p><Link href={`/login?next=${encodeURIComponent(`/routes/new?edit=${encodeURIComponent(editId)}`)}`} className="button button-primary">로그인하기</Link><Link href="/routes/new" className="button button-secondary">새 코스 만들어보기</Link></main>;
  if (authStatus === "unavailable") return <main className="container writer-empty" role="alert"><h1>로그인 상태를 확인하지 못했어요</h1><p>연결을 확인한 뒤 다시 시도해 주세요.</p><button type="button" className="button button-primary" onClick={() => void reloadMember()}>다시 확인</button></main>;
  if (sourceId && !ready) return <main className="container writer-empty"><p role="status"><span className="writer-spinner" aria-hidden="true" />저장된 루트를 불러오고 있어요.</p></main>;
  if (sourceId && !existing && loadError) return <main className="container writer-empty"><span className="eyebrow">MY ROUTE</span><h1>{loadError}</h1><p>이전 버전의 브라우저 코스만 목록에 남아 있을 수 있어요.</p><button type="button" className="button button-primary" onClick={() => void retryRoutes()}>다시 불러오기</button></main>;
  if (sourceId && (!existing || (editId && !existing.owned))) return <main className="container writer-empty"><span className="eyebrow">MY ROUTE</span><h1>수정할 루트를 찾을 수 없어요</h1><p>이 브라우저에서 편집 권한을 보관한 루트인지 확인하거나 새 루트를 만들어보세요.</p><Link href="/routes" className="button button-secondary">루트 목록으로</Link></main>;
  if (!hydrated) return <main className="container writer-empty"><p role="status"><span className="writer-spinner" aria-hidden="true" />임시저장 내용을 확인하고 있어요.</p></main>;
  if (stadiumError) return <main className="container writer-empty" role="alert"><h1>구장 목록을 불러오지 못했어요</h1><p>{stadiumError} 작성 중이던 기기 내 초안은 지우지 않았어요.</p><button type="button" onClick={() => { setStadiums(null); setStadiumError(""); setAttempt(value => value + 1); }}>다시 시도</button></main>;
  if (!stadiums) return <main className="container writer-empty"><p role="status"><span className="writer-spinner" aria-hidden="true" />DB에서 구장 목록을 불러오고 있어요.</p></main>;
  if (!stadiums.length) return <main className="container writer-empty"><h1>선택할 수 있는 구장이 없어요</h1><p>{invalidCount ? "적재된 구장 좌표를 확인해 주세요." : "구장 데이터가 적재된 뒤 다시 시도해 주세요."} 기존 초안은 유지됩니다.</p></main>;
  // Explicit stadium first; an ordinary return resumes the latest draft before
  // choosing a team's home/random stadium, which could resurrect an older story.
  const requested = existing?.stadium ?? initialStadium;
  const teamStadium = teamBoards.find(team => team.code === user?.team_code)?.stadium;
  const findStadium = (name?: string) => name ? stadiums.find(item => matchesStadium(item, name)) : undefined;
  const initial = requested ? findStadium(requested) : (authStatus === "authenticated" ? findStadium(draftStadium) : undefined) ?? findStadium(teamStadium) ?? stadiums[Math.floor(randomPick * stadiums.length)];
  if (!initial) return <main className="container writer-empty"><h1>선택한 구장을 사용할 수 없어요</h1><p>구장이 삭제됐거나 좌표를 확인할 수 없어요. 다른 구장을 직접 선택해 주세요. 기존 초안은 유지했습니다.</p><Link href="/routes/new" className="button button-secondary">구장 다시 선택하기</Link></main>;
  return <WriterForm key={`${authStatus}:${user?.id ?? "guest"}:${copyId ? "copy:" : "edit:"}${existing?.legacySourceId ?? sourceId ?? initial.code}`} stadiums={stadiums} initial={initial} copying={Boolean(copyId)} existing={existing} />;
}

/**
 * sample: 가이드용 샘플 화면. 같은 작성 화면이지만 임시저장·나가기 확인·챗봇 요청·코스 저장이 모두 꺼져 있고,
 * 가이드가 보내는 예시 코스만 받는다.
 */
function WriterForm({ stadiums, initial, existing, copying = false, sample = false }: { stadiums: Stadium[]; initial: Stadium; existing?: TripRoute; copying?: boolean; sample?: boolean }) {
  const router = useRouter();
  const { status: authStatus, user: draftOwner } = useMemberAuth();
  const showMemberFields = authStatus === "authenticated";
  const canPersistDraft = showMemberFields && !sample;
  const { onContextChange, context: chatContext, onReset: resetChat, registerCourseTarget, takePendingCourse } = useChat();
  const resetChatRef = useRef(resetChat);
  useLayoutEffect(() => { resetChatRef.current = resetChat; }, [resetChat]);
  const draftKey = sample ? `sample:${initial.code}` : existing ? `${copying ? "copy" : "edit"}:${existing.id}` : `new:${initial.code}`;
  const [storage] = useState(() => canPersistDraft ? browserDraftStorage() : undefined);
  const [storedDraft] = useState<ReturnType<typeof readRouteDraft>>(() => canPersistDraft ? readRouteDraft(storage, draftKey) : { raw: null });
  const [memoryDraft] = useState(() => canPersistDraft ? writerDrafts.get(draftKey) : undefined);
  const [recovery] = useState(() => recoverRouteDraft(storedDraft, memoryDraft));
  const [restoredDraft] = useState(() => {
    const candidate = recovery.data;
    return candidate && stadiums.some(stadium => stadium.code === candidate.stadiumCode) ? candidate : undefined;
  });
  const [copiedStory] = useState<RichContentDoc | null>(() => {
    if (!copying || !existing?.contentDoc) return null;
    const blocks = existing.contentDoc.blocks.filter(block => block.type !== "image");
    return { version: 1, blocks: blocks.length ? blocks : plainRichDoc("").blocks };
  });
  const { state: courseState, ref: courseRef, dispatch: dispatchCourse } = useCourseState(() => ({
    stadiumCode: restoredDraft?.stadiumCode ?? initial.code,
    title: restoredDraft?.title ?? existing?.title ?? "",
    content: restoredDraft?.content ?? (copiedStory ? richText(copiedStory) : existing?.content ?? ""),
    contentFormat: restoredDraft ? restoredDraft.contentFormat : copiedStory ? undefined : existing?.contentFormat,
    contentDoc: restoredDraft ? restoredDraft.contentDoc ?? null : copiedStory ?? existing?.contentDoc ?? null,
    duration: restoredDraft?.duration ?? existing?.duration ?? "반나절", tags: restoredDraft?.tags ?? existing?.tags ?? ["첫 직관"],
    stops: restoredDraft?.stops ?? existing?.stops ?? [], start: restoredDraft ? restoredDraft.start : existing?.start,
    tab: restoredDraft?.tab ?? "write", travelMode: restoredDraft?.travelMode ?? existing?.travelMode ?? "walk",
    legModes: restoredDraft?.legModes ?? existing?.legModes ?? {}, plannerCompleted: restoredDraft?.plannerCompleted ?? false,
    plannerMode: restoredDraft?.plannerMode,
    chatCourseKey: restoredDraft?.chatCourseKey?.startsWith(`chat:member:${draftOwner?.id}:`) ? restoredDraft.chatCourseKey : undefined,
  }));
  const { stadiumCode, title, content, contentFormat, contentDoc, duration, tags, stops, start, tab, travelMode,
    legModes = {}, plannerCompleted = false, plannerMode = "places", chatCourseKey } = courseState.data;
  const [initialDoc] = useState(() => restoredDraft?.contentDoc ?? copiedStory ?? existing?.contentDoc
    ?? plainRichDoc(routeContentToText(restoredDraft?.content ?? existing?.content ?? "", restoredDraft ? restoredDraft.contentFormat : existing?.contentFormat)));
  const linkedDraftRaw = useRef<{ key: string; raw: string | null } | null>(null);
  const [pathResult, setPathResult] = useState<{ key: string; path?: ChatRoutePath }>();
  const [selectedPlace, setSelectedPlace] = useState<RouteStop | null>(null);
  const selectedPlaceRef = useRef<RouteStop | null>(null);
  const changeSelectedPlace = useCallback((place: RouteStop | null) => {
    selectedPlaceRef.current = place;
    setSelectedPlace(place);
  }, []);
  const geometryKey = courseGeometryKey(courseState.data);
  const routePath = pathResult?.key === geometryKey ? pathResult.path : undefined;
  const pathRef = useRef(pathResult);
  const changeRoutePath = useCallback((path: ChatRoutePath | undefined) => {
    if (geometryKey !== courseGeometryKey(courseRef.current.data)) return;
    const next = { key: geometryKey, path };
    if (JSON.stringify(pathRef.current) === JSON.stringify(next)) return;
    pathRef.current = next; setPathResult(next);
  }, [geometryKey, courseRef]);
  const [autoCompleteCourse, setAutoCompleteCourse] = useState(false);
  const [error, setError] = useState("");
  const [draftStatus, setDraftStatus] = useState(() => storedDraft.error ? "브라우저 저장 공간을 읽지 못했어요. 변경 내용은 이 화면에만 남아 있어요." : storedDraft.raw && (!storedDraft.draft || !restoredDraft) ? "기존 임시저장 데이터를 확인할 수 없어 덮어쓰지 않았어요." : memoryDraft ? "이 화면에 남아 있던 미저장 변경을 복원했어요." : restoredDraft && storedDraft.draft ? `${new Date(storedDraft.draft.updatedAt).toLocaleString("ko-KR")} 임시저장을 복원했어요.` : "변경 사항 없음");
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  // 챗봇 코스를 담은 횟수·구장. 지도(planner)가 이 값이 바뀌면 "내 코스" 탭을 열고 코스 전체를 보여준다.
  const [courseApplied, setCourseApplied] = useState<{ version: number; stadiumCode: string; completed?: boolean } | null>(null);
  const dirty = useRef(recovery.dirty);
  // "작성 중" = something was changed (or restored) that has not been published yet.
  const touched = useRef(recovery.dirty || Boolean(restoredDraft));
  const discarded = useRef(false);
  const savingRef = useRef(false);
  const activeForm = useRef(true);
  useLayoutEffect(() => { activeForm.current = true; return () => { activeForm.current = false; }; }, []);
  const expectedRaw = useRef(recovery.expectedRaw);
  const draftContext = useRef(draftKey);
  const autosaveRef = useRef<ReturnType<typeof createDraftAutosave> | null>(null);
  const savedRouteRef = useRef<TripRoute | undefined>(existing && !copying && existing.owned ? existing : undefined);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const current = stadiums.find((stadium) => stadium.code === stadiumCode);
  const plainContent = contentDoc ? richText(contentDoc) : routeContentToText(content, contentFormat);
  const hasImages = Boolean(contentDoc?.blocks.some(block => block.type === "image"));
  const canSave = Boolean(authStatus === "authenticated" && title.trim() && plainContent.length <= 12000 && stops.length && !uploading);
  useLayoutEffect(() => {
    if (canPersistDraft && dirty.current) writerDrafts.set(draftContext.current, { data: courseState.data, expectedRaw: expectedRaw.current });
  }, [courseState, canPersistDraft]);

  // 일반 작성 초안을 지우고 나가도, 해당 대화에서 다시 열 편집본은 답변별로 보관한다.
  useEffect(() => {
    if (!canPersistDraft || !chatCourseKey) return;
    if (linkedDraftRaw.current?.key !== chatCourseKey) linkedDraftRaw.current = { key: chatCourseKey, raw: readRouteDraft(storage, chatCourseKey).raw };
    const result = saveRouteDraft(storage, chatCourseKey, courseRef.current.data, linkedDraftRaw.current.raw);
    if (result.status === "saved" || result.status === "unchanged") linkedDraftRaw.current.raw = result.raw;
    else setDraftStatus(result.status === "conflict" ? "다른 탭에서 이 대화의 코스를 수정해 복원용 저장을 멈췄어요." : "대화에 연결된 코스 상태를 이 브라우저에 보관하지 못했어요.");
  }, [canPersistDraft, storage, chatCourseKey, courseState, courseRef]);

  const flushDraft = useCallback(() => {
    if (!canPersistDraft) return true;
    if (discarded.current || !dirty.current) return true;
    const context = draftContext.current;
    const result = saveRouteDraft(storage, context, courseRef.current.data, expectedRaw.current);
    if (result.status === "saved") {
      expectedRaw.current = result.raw; dirty.current = false; writerDrafts.delete(context);
      setDraftStatus(`${new Date(result.draft.updatedAt).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })} 임시저장됨`);
      return true;
    }
    if (result.status === "unchanged") { expectedRaw.current = result.raw; dirty.current = false; writerDrafts.delete(context); setDraftStatus("변경 없이 임시저장 상태예요."); return true; }
    writerDrafts.set(context, { data: courseRef.current.data, expectedRaw: expectedRaw.current });
    setDraftStatus(result.status === "conflict" ? "다른 탭의 새 임시저장을 발견해 자동 저장을 멈췄어요. 새로고침 후 확인해 주세요." : "브라우저 저장 공간에 임시저장하지 못했어요. 변경 내용은 이 화면에만 남아 있어요.");
    return false;
  }, [storage, canPersistDraft, courseRef]);

  // Leaving the writer throws the work away, so the next visit starts from a clean form.
  const discardDraft = useCallback(() => {
    discarded.current = true; dirty.current = false; touched.current = false;
    autosaveRef.current?.stop(false);
    if (canPersistDraft) {
      writerDrafts.delete(draftContext.current);
      removeRouteDraft(storage, draftContext.current, expectedRaw.current);
    }
    resetChatRef.current();
  }, [storage, canPersistDraft]);

  useEffect(() => {
    if (!canPersistDraft) return;
    let active = false;
    const activate = window.setTimeout(() => { active = true; }, 0);
    const autosave = createDraftAutosave(flushDraft);
    autosaveRef.current = autosave;
    if (dirty.current) autosave.changed();
    const pagehide = () => autosave.flush();
    const visibility = () => { if (document.visibilityState === "hidden") autosave.flush(); };
    window.addEventListener("pagehide", pagehide);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      clearTimeout(activate);
      autosave.stop(active);
      if (autosaveRef.current === autosave) autosaveRef.current = null;
      window.removeEventListener("pagehide", pagehide);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [flushDraft, discardDraft, canPersistDraft]);

  const readCurrentContext = useCallback(() => {
    const context = courseContext(courseRef.current,
      pathRef.current?.key === courseGeometryKey(courseRef.current.data) ? pathRef.current.path : undefined, selectedPlaceRef.current);
    return { ...context, stadium: stadiums.find(item => item.code === context.stadium)?.name ?? context.stadium };
  }, [courseRef, stadiums]);
  useEffect(() => {
    if (!current) return;
    const next = readCurrentContext();
    if (JSON.stringify(next) !== JSON.stringify(chatContext)) onContextChange(next);
  }, [current, courseState, routePath, selectedPlace, chatContext, onContextChange, readCurrentContext]);

  useEffect(() => {
    if (sample) return;
    const protect = (event: BeforeUnloadEvent) => {
      if (touched.current && (!canPersistDraft || !flushDraft())) event.preventDefault();
    };
    const protectLink = (event: MouseEvent) => {
      if (!touched.current || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const anchor = (event.target as Element | null)?.closest<HTMLAnchorElement>("a[href]");
      if (!anchor || anchor.target === "_blank" || anchor.hasAttribute("download")) return;
      const next = new URL(anchor.href, window.location.href);
      if (next.origin === window.location.origin && next.pathname === window.location.pathname && next.search === window.location.search) return;
      event.preventDefault(); event.stopPropagation();
      setConfirmation({ title: "작성 중인 코스가 있습니다", description: canPersistDraft && courseRef.current.data.chatCourseKey ? "작성 화면을 비우고 이동할까요? 이 대화에 연결된 코스는 같은 브라우저에서 다시 불러올 수 있어요." : "페이지를 떠나시겠습니까? 떠나면 작성 중인 내용은 저장되지 않고, 다시 들어오면 처음부터 시작해요.", label: "떠나기", cancelLabel: "계속 작성하기", action: () => { discardDraft(); if (next.origin === window.location.origin) router.push(`${next.pathname}${next.search}${next.hash}`); else window.location.assign(next.href); } });
    };
    window.addEventListener("beforeunload", protect);
    document.addEventListener("click", protectLink, true);
    return () => { window.removeEventListener("beforeunload", protect); document.removeEventListener("click", protectLink, true); };
  }, [router, discardDraft, sample, canPersistDraft, flushDraft, courseRef]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!confirmation || !dialog) return;
    const opener = document.activeElement as HTMLElement | null;
    if (!dialog.open) dialog.showModal();
    return () => { if (dialog.open) dialog.close(); opener?.focus(); };
  }, [confirmation]);

  const markDirty = useCallback(() => {
    if (sample) return;
    touched.current = true;
    setError("");
    if (!canPersistDraft) return;
    dirty.current = true;
    writerDrafts.set(draftContext.current, { data: courseRef.current.data, expectedRaw: expectedRaw.current });
    setDraftStatus("저장되지 않은 변경이 있어요. 1초 뒤 자동 저장하고, 작성 중에는 5초마다 확인해요.");
    autosaveRef.current?.changed();
  }, [sample, canPersistDraft, courseRef]);
  const commitCourse = useCallback((action: CourseAction) => {
    const before = courseRef.current;
    const next = dispatchCourse(action);
    if (before !== next) markDirty();
    return next;
  }, [courseRef, dispatchCourse, markDirty]);
  const changeCourse = useCallback((patch: Partial<RouteDraftData>) => { commitCourse({ type: "patch", patch }); }, [commitCourse]);
  const changeStops = useCallback((next: RouteStop[]) => changeCourse({ stops: next }), [changeCourse]);
  const changeStart = useCallback((next: TripRoute["start"], originSource: "custom" | "current" = "custom") => {
    commitCourse({ type: "patch", patch: { start: next }, originSource });
  }, [commitCourse]);
  const changeTravelMode = useCallback((next: TravelMode) => changeCourse({ travelMode: next }), [changeCourse]);
  const changeCompletion = useCallback((completed: boolean) => changeCourse({ plannerCompleted: completed }), [changeCourse]);
  const changeLegModes = useCallback((next: LegModes) => changeCourse({ legModes: next }), [changeCourse]);
  const setTab = useCallback((next: WriterTab) => changeCourse({ tab: next }), [changeCourse]);
  const setPlannerMode = useCallback((next: PlannerMode) => changeCourse({ plannerMode: next }), [changeCourse]);
  const applyCourse = useCallback((course: ChatCourse, how: "replace" | "append") => {
    const target = course.stadiumCode ? stadiums.find(stadium => matchesStadium(stadium, course.stadiumCode!)) : stadiums.find(stadium => stadium.code === courseRef.current.data.stadiumCode);
    if (!target) return null;
    const before = courseRef.current;
    course = { ...course, places: course.places.map(place => place.category === "STADIUM"
      ? { ...place, name: target.name, lat: target.lat, lng: target.lng, address: target.address || place.address } : place) };
    const next = commitCourse({ type: "apply", course, stadiumCode: target.code, how });
    setCourseApplied({ version: next.revision, stadiumCode: next.data.stadiumCode, completed: next.data.plannerCompleted });
    return () => {
      const current = courseRef.current;
      const undone = commitCourse({ type: "undo", before, expectedRevision: next.revision });
      if (undone !== current) {
        setCourseApplied({ version: undone.revision, stadiumCode: undone.data.stadiumCode, completed: undone.data.plannerCompleted });
        return true;
      }
      setError("코스를 추가로 수정했어요. 최신 내용을 보호하기 위해 이전 추천 적용을 되돌리지 않았어요.");
      return false;
    };
  }, [stadiums, courseRef, commitCourse]);
  const applyCourseRef = useRef(applyCourse);
  useLayoutEffect(() => { applyCourseRef.current = applyCourse; }, [applyCourse]);
  const readCourseVersion = useCallback(() => JSON.stringify([courseRef.current.revision,
    chatRouteSelectionKey(pathRef.current?.key === courseGeometryKey(courseRef.current.data) ? pathRef.current.path : undefined)]), [courseRef]);
  const selectionKey = JSON.stringify([courseState.revision, chatRouteSelectionKey(routePath)]);
  useLayoutEffect(() => {
    registerCourseTarget({ stadiumCode, stopCount: stops.length, selectionKey, getContext: readCurrentContext, getVersion: readCourseVersion,
      apply: (course, how) => applyCourseRef.current(course, how) });
  }, [registerCourseTarget, stadiumCode, stops.length, selectionKey, readCurrentContext, readCourseVersion]);
  useEffect(() => () => registerCourseTarget(null), [registerCourseTarget]);
  useEffect(() => {
    // 전체 채팅 화면에서 "루트 작성 지도에서 열기"로 넘어온 코스
    const pending = takePendingCourse();
    if (!pending) return;
    const before = courseRef.current.data;
    if (pending.writerKey !== before.chatCourseKey && (before.stops.length || before.start || before.title.trim() || before.content.trim())) {
      setConfirmation({ title: "기존 대화의 코스를 불러올까요?", description: "현재 작성 중인 내용이 있어요. 불러오기를 선택하면 이 대화에 보관한 코스로 바뀝니다.", label: "코스 불러오기", cancelLabel: "계속 작성하기", action: () => { applyCourseRef.current(pending, "replace"); } });
    } else applyCourseRef.current(pending, "replace");
  }, [takePendingCourse, courseRef]);
  // 스포트라이트 가이드가 보내는 예시 코스: 챗봇이 만든 코스처럼 지도에 그리고 코스 완성까지 누른다. (샘플 화면만)
  useEffect(() => {
    if (!sample) return;
    const onCourse = (event: Event) => {
      const course = (event as CustomEvent<GuideCourseStop[]>).detail;
      if (!course?.length) return;
      const nextStops: RouteStop[] = course.map((stop) => stop.isMapPoint
        ? { ...stop, placeId: `map:${createClientId()}` }
        : { ...stop, visitId: createClientId(), isDrawnPoint: true });
      changeCourse({ stops: nextStops, plannerMode: "places", plannerCompleted: false });
      setAutoCompleteCourse(true);
    };
    window.addEventListener(GUIDE_COURSE, onCourse);
    return () => window.removeEventListener(GUIDE_COURSE, onCourse);
  }, [changeCourse, sample]);
  if (!current) return <main className="container writer-empty"><h1>초안의 구장을 사용할 수 없어요</h1><p>구장이 삭제됐거나 좌표를 확인할 수 없어요. 초안은 지우지 않았습니다.</p><Link href="/routes/new" className="button button-secondary">새 루트에서 구장 선택하기</Link></main>;
  const selectedStadium = current;
  function changeStadium(code: string) {
    if (code === stadiumCode) return;
    const change = () => { commitCourse({ type: "stadium", code }); setCourseApplied(null); };
    // 샘플 화면(가이드)에서는 정해진 구장으로 확인 없이 바꾼다
    if ((!stops.length && !start) || sample) { change(); return; }
    setConfirmation({ title: "다른 구장 주변을 둘러볼까요?", description: "구장을 바꾸면 선택한 방문 장소가 비워져요. 작성한 제목과 이야기는 그대로 남아요.", label: "구장 바꾸기", action: change });
  }
  function changeTab(next: WriterTab) { if (next !== tab) { setTab(next); markDirty(); } }
  function tabKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % writerTabs.length;
    else if (event.key === "ArrowLeft") next = (index + writerTabs.length - 1) % writerTabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = writerTabs.length - 1;
    else return;
    event.preventDefault(); changeTab(writerTabs[next].id); document.getElementById(`writer-tab-${writerTabs[next].id}`)?.focus();
  }
  async function saveCourse(askReview = true) {
    if (sample || savingRef.current) return;
    setError("");
    if (authStatus !== "authenticated") { setError("코스를 저장하려면 로그인해 주세요."); return; }
    if (!canSave) { setError(hasImages && authStatus !== "authenticated" ? "이미지가 있는 코스를 저장하려면 로그인해 주세요." : "코스 이름과 방문 장소를 확인해 주세요. 본문은 선택 사항이며 12,000자까지 작성할 수 있어요."); return; }
    savingRef.current = true; setSaving(true);
    const saved = savedRouteRef.current;
    const route: TripRoute = {
      id: saved?.id ?? "", title: title.trim(), stadium: selectedStadium.name, description: (plainContent.trim() || withCourseStart(stops, start).map((stop) => stop.name).join(" → ")).replace(/\s+/g, " ").slice(0, 100),
      content: content.trim(), ...(contentDoc ? { contentDoc } : {}), ...(contentFormat ? { contentFormat } : {}), tags, duration, cover: existing?.cover ?? "/images/stadium-night.jpg", stops, ...(start ? { start } : {}),
      travelMode, legModes,
      author: "익명", likes: saved?.likes ?? 0, views: saved?.views ?? 0, owned: true,
      isSample: false, createdAt: saved?.createdAt ?? new Date().toISOString(), ...(saved?.legacy ? { legacy: true } : {}), ...(saved?.legacySourceId ? { legacySourceId: saved.legacySourceId } : {}),
    };
    try {
      await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
      if (!activeForm.current) return;
      const persisted = await saveRoute(route);
      if (!activeForm.current) return;
      const oldContext = draftContext.current;
      removeRouteDraft(storage, oldContext, expectedRaw.current); writerDrafts.delete(oldContext);
      const canonicalContext = `edit:${persisted.id}`;
      draftContext.current = canonicalContext; expectedRaw.current = null;
      const url = new URL(window.location.href); url.searchParams.delete("copy"); url.searchParams.delete("stadium"); url.searchParams.set("edit", persisted.id); window.history.replaceState(window.history.state, "", url);
      savedRouteRef.current = persisted; dirty.current = false; touched.current = false;
      setDraftStatus("공개 저장을 완료했어요. 이후 변경은 이 코스의 새 임시저장으로 보관해요.");
      savingRef.current = false; setSaving(false);
      if (persisted.saveWarning) setError(persisted.saveWarning);
      const showSavedCourse = () => router.push(`/routes/${encodeURIComponent(persisted.id)}`);
      if (askReview) {
        setConfirmation({
          title: "코스 후기를 작성하시겠어요?",
          description: persisted.saveWarning ? `코스가 코스 둘러보기에 저장됐어요. ${persisted.saveWarning}` : "코스가 코스 둘러보기에 저장됐어요. 후기를 추가로 작성할 수 있어요.",
          label: "예", cancelLabel: "아니요", cancelAction: showSavedCourse,
          action: () => {
            setTab("write");
            requestAnimationFrame(() => requestAnimationFrame(() => {
              const panel = document.getElementById("writer-panel-write");
              panel?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "start" });
              panel?.focus({ preventScroll: true });
            }));
          },
        });
      } else if (!persisted.saveWarning) router.push("/routes");
    } catch (caught) { if (!activeForm.current) return; savingRef.current = false; setSaving(false); dirty.current = true; autosaveRef.current?.changed(); setError(caught instanceof Error ? caught.message : "저장하지 못했어요. 다시 시도해 주세요. 작성 내용은 이 화면에 남아 있어요."); }
  }
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sample || authStatus !== "authenticated") return;
    void saveCourse(false);
  }

  return (
    <main className="writer-page">
      <div className="container">
        <div className="writer-page-heading">
          <div><span className="eyebrow">MAKE YOUR GAME DAY</span><h1>{existing && !copying && !existing.isSample ? "나의 루트 수정하기" : "나만의 직관 루트 만들기"}</h1></div>
          <div className="writer-heading-actions">
            {sample ? <RouteGuideButton includeMemberSteps={showMemberFields} /> : <RouteGuideButton includeMemberSteps={showMemberFields} renderSample={() => <ChatSampleProvider><WriterForm stadiums={stadiums} initial={initial} sample /></ChatSampleProvider>} />}
            <Link href="/routes" className="writer-back">← 루트 둘러보기</Link>
          </div>
        </div>
        <div className="writer-mobile-tabs" role="tablist" aria-label="루트 작성 도구">{writerTabs.map((item, index) => <button type="button" role="tab" key={item.id} id={`writer-tab-${item.id}`} aria-controls={item.id === "write" ? (showMemberFields ? "writer-panel-write writer-panel-planner" : "writer-panel-planner") : "writer-panel-chat"} aria-selected={tab === item.id} tabIndex={tab === item.id ? 0 : -1} onClick={() => changeTab(item.id)} onKeyDown={(event) => tabKey(event, index)}>{item.id === "write" && !showMemberFields ? "코스 만들기" : item.label}</button>)}</div>
        <form ref={formRef} onSubmit={submit} className="writer-form" aria-busy={saving}>
          <fieldset disabled={saving} className="writer-layout" data-active-tab={tab} data-member-tools={showMemberFields ? "true" : "false"}>
            <legend className="sr-only">직관 루트 작성</legend>
            <section className="writer-card writer-planner-panel" id="writer-panel-planner" aria-labelledby="planner-heading">
              <div className="planner-heading-row">
                <div className="planner-mode-heading">
                  <h2 id="planner-heading" className="sr-only">코스 만들기 방법</h2>
                  <div className="planner-mode-tabs" role="group" aria-labelledby="planner-heading">
                    <button type="button" aria-pressed={plannerMode === "places"} disabled={plannerCompleted} onClick={() => setPlannerMode("places")}><span>01</span><strong>직접 코스 만들기</strong></button>
                    <button type="button" aria-pressed={plannerMode === "draw"} disabled={plannerCompleted} onClick={() => setPlannerMode("draw")}><span>02</span><strong>동선으로 코스 짜기</strong></button>
                  </div>
                  <p>{plannerMode === "places" ? "가고 싶은 장소를 골라 방문 순서대로 코스를 만들어보세요." : "지도 위를 차례로 눌러 한 가지 색상의 동선을 그려보세요."}</p>
                </div>
                <div className="writer-field planner-stadium-field"><label className="sr-only" htmlFor="route-stadium">구장 선택</label><select id="route-stadium" aria-label="구장 선택" value={stadiumCode} onChange={(event) => changeStadium(event.target.value)}>{stadiums.map((stadium) => <option key={stadium.code} value={stadium.code}>{stadium.name}</option>)}</select></div>
              </div>
              <NearbyRoutePlanner key={`${stadiumCode}:${plannerMode}`} plannerMode={plannerMode} stadium={current} stops={stops} onChange={changeStops} initialStart={start} onStartChange={changeStart} onCourseChange={changeCourse} courseRevision={courseState.revision} originSource={courseState.originSource} onRoutePathChange={changeRoutePath} onSelectedPlaceChange={changeSelectedPlace} initialTravelMode={travelMode} travelMode={travelMode} legModes={legModes} onLegModesChange={changeLegModes} courseApplied={courseApplied} onTravelModeChange={changeTravelMode} courseName={title} onCourseNameChange={(name) => changeCourse({ title: name })} allowSave={showMemberFields} onSaveCourse={() => saveCourse()} saving={saving} saveError={error} startWithAllPlaces={copying} onCompletionChange={changeCompletion} initialCompleted={plannerCompleted} autoComplete={autoCompleteCourse} onAutoCompleted={() => setAutoCompleteCourse(false)} guide={sample} />
            </section>
            {showMemberFields && <div className="writer-writing writer-panel" id="writer-panel-write" role="tabpanel" aria-labelledby="writer-tab-write" tabIndex={0}>
              <section className="writer-card">
                <div className="writer-section-title"><span>02</span><h2><label htmlFor="route-content">나만의 이야기를 담아보세요</label></h2></div>
                <div className="writer-field"><label htmlFor="route-title">루트 제목 <em>*</em></label><input id="route-title" value={title} onChange={(event) => changeCourse({ title: event.target.value })} maxLength={80} placeholder="예: 친구와 함께, 잠실에서 보내는 하루" required /><span className="writer-field-hint">함께 가는 사람에게 소개하듯 제목을 지어보세요. <b>{title.length}/80</b></span></div>
                <CommunityRichEditor id="route-content" label="직관 루트 이야기" placeholder="방문 순서와 나만의 이야기를 적어 주세요." maxLength={12000}
                  initial={initialDoc} notifyInitial={false} disabled={saving} imageUploadDisabled={sample || !showMemberFields}
                  onUploadingChange={setUploading} onError={setError} onChange={(doc, value) => changeCourse({ contentDoc: doc, content: value, contentFormat: undefined })} />
                <p className="writer-field-hint writer-content-tip">방문 순서, 이동 계획, 준비물을 적으면 함께 가는 사람에게 더 도움이 돼요.</p>
              </section>
            </div>}

            <aside className="writer-chat-panel writer-panel" id="writer-panel-chat" role="tabpanel" aria-labelledby="writer-tab-chat" tabIndex={0}>
              <ChatPopup embedded title="채팅으로 만드는 직관 코스" conversationLabel={null} welcomeTitle="어떤 조건의 코스를 원하시나요?" welcomeDescription={null} welcomeLink={{ href: "/routes", label: "코스 둘러보기" }} />
            </aside>
          </fieldset>
          {error && <div role="alert" className="writer-error">{error}</div>}
          {showMemberFields && <div className="writer-save-area">
            <p className="writer-draft-status" role="status" aria-live="polite"><strong>{draftStatus}</strong><span>이 브라우저에만 임시저장되며 공개되지 않아요. 변경 1초 후 자동 저장하며 작성 중에는 5초마다 확인해요.</span></p>
            <div className="writer-save-row"><p><strong>{canSave ? "나의 직관 루트가 준비됐어요." : "코스 이름과 방문 장소를 채워주세요."}</strong><span>{existing?.legacy ? "이전 코스는 다시 저장하면 코스 둘러보기에 공개돼요." : "코스는 코스 둘러보기에 공개되고 편집 권한만 이 브라우저에 저장돼요."}</span></p><div className="writer-save-actions"><button className="button button-secondary" type="button" disabled={saving || sample} onClick={() => { if (!canPersistDraft) return; dirty.current = true; flushDraft(); }}>임시저장</button><button className="button button-primary" type="submit" disabled={!canSave || saving || sample}>{saving ? <><span className="writer-spinner" aria-hidden="true" />저장하고 있어요</> : <><WriterIcon kind="save" />작성 완료</>}</button></div></div>
          </div>}
        </form>
        <dialog ref={dialogRef} className="writer-confirm-dialog" aria-labelledby="writer-confirm-title" aria-describedby="writer-confirm-description" onCancel={(event) => { event.preventDefault(); setConfirmation(null); }}>
          {confirmation && <><span className="writer-confirm-icon"><WriterIcon kind="save" /></span><h2 id="writer-confirm-title">{confirmation.title}</h2><p id="writer-confirm-description">{confirmation.description}</p><div className="writer-confirm-actions"><button type="button" className="button button-secondary" autoFocus onClick={() => { const action = confirmation.cancelAction; setConfirmation(null); action?.(); }}>{confirmation.cancelLabel ?? "계속 작성하기"}</button><button type="button" className="button button-primary" onClick={() => { const action = confirmation.action; setConfirmation(null); action(); }}>{confirmation.label}</button></div></>}
        </dialog>
      </div>
    </main>
  );
}
