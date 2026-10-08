"use client";

import { use, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import boardStyles from "@/components/community-board.module.css";
import { useRouter } from "next/navigation";
import { useRouteNumber } from "@/lib/route-number";
import { RouteContent } from "@/components/route-content";
import { RouteMap } from "@/components/route-map";
import { RouteDetailSkeleton } from "@/components/route-skeleton";
import { routeContentToText } from "@/lib/route-content";
import { coursePointLabel, withCourseStart } from "@/lib/drawn-course";
import { useMemberAuth } from "@/lib/member-auth";
import { stadiums } from "@/lib/stadiums";
import { deleteRoute, loadRouteLike, recordRouteView, retryRoutes, toggleRouteLike, useLikedRoutes, useRoutes, useRoutesError, useRoutesReady } from "@/lib/routes";

function DeleteRouteDialog({ title, error, busy, onCancel, onDelete }: { title: string; error: string; busy: boolean; onCancel: () => void; onDelete: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog?.showModal();
    return () => { dialog?.close(); opener?.focus(); };
  }, []);
  return <dialog ref={dialogRef} className="route-confirm-dialog" aria-labelledby="route-delete-title" aria-describedby="route-delete-description" onCancel={event => { event.preventDefault(); if (!busy) onCancel(); }}><div className="route-dialog-icon" aria-hidden="true">×</div><h2 id="route-delete-title">이 코스를 삭제할까요?</h2><p className="route-delete-name">{title}</p><p id="route-delete-description">저장한 내용과 방문 장소가 삭제돼요.<br/>삭제한 코스는 되돌릴 수 없어요.</p>{error && <p className="route-error" role="alert">{error}</p>}<div className="route-dialog-actions"><button type="button" className="button button-secondary" autoFocus disabled={busy} onClick={onCancel}>취소</button><button type="button" className="button button-primary" disabled={busy} onClick={onDelete}>{busy ? "삭제 중…" : "삭제하기"}</button></div></dialog>;
}

export default function RouteDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { status: authStatus } = useMemberAuth();
  const authenticated = authStatus === "authenticated";
  const routes = useRoutes();
  const likedRoutes = useLikedRoutes();
  const liked = authenticated && likedRoutes.includes(id);
  const ready = useRoutesReady();
  const loadError = useRoutesError();
  const router = useRouter();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [shareFallback, setShareFallback] = useState("");
  const storedRoute = routes.find(item => item.id === id);
  const routeApiId = storedRoute?.apiId ?? storedRoute?.id;
  const routeNumber = useRouteNumber(storedRoute?.id, storedRoute?.routeNumber);
  const route = useMemo(() => storedRoute && { ...storedRoute, stops: withCourseStart(storedRoute.stops, storedRoute.start) }, [storedRoute]);

  useEffect(() => {
    if (ready && routeApiId) { recordRouteView(id); if (authenticated) void loadRouteLike(id).catch(() => {}); }
  }, [ready, routeApiId, id, authenticated]);

  if (!ready || deleting) return <RouteDetailSkeleton/>;
  if (!route && loadError) return <main className="container route-empty route-not-found header-aligned-content"><h1>{loadError}</h1><p>잠시 후 다시 시도해 주세요.</p><button type="button" className="button button-primary" onClick={() => void retryRoutes()}>다시 불러오기</button><Link className="button button-secondary" href="/routes">코스 둘러보기</Link></main>;
  if (!route) return <main className="container route-empty route-not-found header-aligned-content"><h1>코스를 찾을 수 없어요</h1><p>삭제된 코스이거나 존재하지 않는 코스일 수 있어요.</p><Link className="button button-primary" href="/routes">코스 둘러보기</Link></main>;

  const removeRoute = async () => {
    setDeleting(true);
    try { await deleteRoute(route.id); router.push("/routes?deleted=1"); }
    catch (caught) { setDeleting(false); setDeleteError(caught instanceof Error ? caught.message : "삭제하지 못했어요. 다시 시도해 주세요."); }
  };
  const shareRoute = async () => {
    setError(""); setFeedback(""); setShareFallback("");
    const url = `${window.location.origin}/routes/${encodeURIComponent(route.id)}`;
    const text = `${route.title}\n${route.stadium} · ${route.duration}\n\n${route.stops.map((stop, index) => `${coursePointLabel(route.stops, index)}. ${stop.name}`).join("\n")}\n\n${routeContentToText(route.content, route.contentFormat)}`;
    const shareData: ShareData = route.isSample ? { title: route.title, url } : { title: route.title, text };
    try {
      if (navigator.share) { await navigator.share(shareData); return; }
      await navigator.clipboard.writeText(route.isSample ? url : text);
      setFeedback(route.isSample ? "코스 링크를 복사했어요." : "코스 내용을 복사했어요. 원하는 곳에 붙여 넣어 공유해 보세요.");
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") return;
      setShareFallback(route.isSample ? url : text);
      setFeedback("자동 복사를 사용할 수 없어요. 아래 내용을 직접 복사해 주세요.");
    }
  };

  return (
    <main className="container route-detail-page header-aligned-content">
      <Link href="/routes" className="route-back-link"><span aria-hidden="true">←</span> 코스 둘러보기</Link>

      <article className={`${boardStyles.postDetail} route-post-detail`}>
      <header className={boardStyles.postHeader}>
        <h1 className={boardStyles.postHeading}><strong>{route.stadium}</strong><span>{route.title}</span></h1>
        <div className={boardStyles.postMeta}>
          <div className={boardStyles.postMetaInfo}>
            <span aria-label={`코스 번호 ${routeNumber}`}>{routeNumber}</span>
            <span>{route.author}</span>
            <time dateTime={route.createdAt}>{new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Seoul" }).format(new Date(route.createdAt))}</time>
          </div>
          <div className={boardStyles.postMetrics}><span>조회 <b>{route.views ?? 0}</b></span><span>추천 <b>{route.likes}</b></span></div>
        </div>
      </header>

      <section className="route-detail-map" aria-label="코스 지도와 게시글">
        <RouteMap key={route.id} stops={route.stops} start={route.start} travelMode={route.travelMode} legModes={route.legModes} stadium={stadiums.find(stadium => stadium.name === route.stadium)} allowOriginSelection={authenticated && !route.isSample} mapFirst travelAside={
        <aside className="route-detail-sidebar">
          {route.isSample && <div className="route-summary-box"><span className="route-summary-symbol" aria-hidden="true">↗</span><h2>설레는 직관,<br />이 코스로 시작해볼까요?</h2><p>마음에 드는 코스에 좋아요를 남기고<br />나만의 하루도 작성해보세요.</p><button className={`button route-like-button${liked ? " is-liked" : ""}`} aria-pressed={liked} disabled={!authenticated} onClick={() => { if (!authenticated) return; void toggleRouteLike(route.id).then(nextLiked => { setError(""); setFeedback(nextLiked ? "이 코스에 좋아요를 남겼어요." : "좋아요를 취소했어요."); }).catch(caught => setError(caught instanceof Error ? caught.message : "좋아요를 저장하지 못했어요.")); }}><svg viewBox="0 0 24 24" fill={liked ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z" /></svg>{liked ? "좋아요 취소" : "좋아요"}<span>{route.likes}</span></button><button type="button" className="button button-secondary route-share-button" onClick={shareRoute}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M12 15V3m-4 4 4-4 4 4M5 12v8h14v-8"/></svg>공유하기</button>{authenticated && <button type="button" className="button button-primary" onClick={() => { if (window.confirm("코스 만들기 페이지로 이동하시겠습니까?")) router.push(`/routes/new?copy=${encodeURIComponent(route.id)}`); }}>코스 수정하기</button>}{authenticated && <Link className="route-sidebar-write" href="/routes/new">나만의 코스 작성하기 <span aria-hidden="true">→</span></Link>}</div>}
          {route.owned && authenticated && <div className="route-owner-actions"><p>{route.legacy ? "다시 저장하기 전까지 이 브라우저에만 남는 이전 코스예요." : "이 브라우저에 편집 권한이 저장된 공개 코스예요."}</p><div><Link href={`/routes/new?edit=${encodeURIComponent(route.id)}`}>수정하기</Link><button type="button" onClick={() => { setDeleteError(""); setConfirmDelete(true); }}>삭제하기</button></div></div>}
          {!route.isSample && !route.owned && authenticated && <div className="route-owner-actions"><p>공개된 코스예요.</p><div><Link href={`/routes/new?copy=${encodeURIComponent(route.id)}`}>복사하기</Link></div></div>}
          {error && <p role="alert" className="route-error">{error}</p>}
          {feedback && <p role="status" className="route-feedback">{feedback}</p>}
          {shareFallback && <label className="route-share-fallback">{route.isSample ? "코스 링크" : "공유할 코스 내용"}<textarea value={shareFallback} readOnly rows={route.isSample ? 2 : 6} autoFocus onFocus={event => event.currentTarget.select()}/></label>}
          <p className="route-demo-note">{route.isSample ? "샘플 코스입니다. 실제 방문 전 경기 일정과 장소 운영 정보를 확인해 주세요." : route.legacy ? "이전 코스는 다시 저장하면 코스 둘러보기에 공개되고 편집 권한이 발급돼요." : route.owned ? "공개 코스이며 편집 권한만 이 브라우저에 저장돼요." : "공개 코스입니다. 복사해서 나만의 코스로 만들 수 있어요."}</p>
        </aside>
        }>
          <section className={`${boardStyles.postBody} route-detail-post-content`} aria-label="작성한 내용"><RouteContent content={route.content} format={route.contentFormat} contentDoc={route.contentDoc}/><div className="route-detail-tags">{route.tags.map(tag => <span key={tag}>#{tag}</span>)}</div></section>
        </RouteMap>
      </section>

      </article>
      <nav className="route-pagination" aria-label="코스 이동"><Link className="button button-secondary" href="/routes"><span aria-hidden="true">←</span> 코스 둘러보기</Link></nav>
      {confirmDelete && <DeleteRouteDialog title={route.title} error={deleteError} busy={deleting} onCancel={() => setConfirmDelete(false)} onDelete={removeRoute}/>}
    </main>
  );
}
