"use client";

import { useEffect, useRef, useState } from "react";
import { useMemberAuth } from "@/lib/member-auth";
import { FEEDBACK_REASONS, fetchAdminFeedback, fetchAdminFeedbackDetail, type AdminAnswerFeedback } from "@/lib/chat/client";
import panelStyles from "./admin-panels.module.css";
import styles from "./admin-feedback-panel.module.css";
import { AdminFeedbackMetadata } from "./admin-feedback-metadata";

export function AdminFeedbackPanel() {
  const { status, user } = useMemberAuth();
  const allowed = status === "authenticated" && user?.is_superuser === true;
  const identity = allowed ? user.id : null;
  const [page, setPage] = useState(1);
  const [rating, setRating] = useState("");
  const [reason, setReason] = useState("");
  const [reload, setReload] = useState(0);
  const [data, setData] = useState<{ count: number; results: AdminAnswerFeedback[] } | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [detail, setDetail] = useState<AdminAnswerFeedback | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [detailReload, setDetailReload] = useState(0);
  const detailTitle = useRef<HTMLHeadingElement>(null);
  const selectionButton = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    if (!allowed) return;
    const controller = new AbortController();
    queueMicrotask(() => {
      if (!controller.signal.aborted) { setData(null); setDetail(null); setSelected(null); setError(""); setBusy(true); }
    });
    void fetchAdminFeedback(page, rating, reason, controller.signal).then(value => { if (!controller.signal.aborted) setData(value); }, cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "목록 조회 실패"); }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [allowed, identity, page, rating, reason, reload]);
  useEffect(() => {
    if (!allowed || selected === null) return;
    const controller = new AbortController();
    queueMicrotask(() => { if (!controller.signal.aborted) { setDetail(null); setError(""); } });
    void fetchAdminFeedbackDetail(selected, controller.signal).then(value => { if (!controller.signal.aborted) setDetail(value); }, cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "상세 조회 실패"); });
    return () => controller.abort();
  }, [allowed, identity, selected, detailReload]);
  useEffect(() => {
    if (allowed && detail && detail.id === selected && window.matchMedia("(max-width: 900px)").matches) {
      detailTitle.current?.focus({ preventScroll: true });
      detailTitle.current?.scrollIntoView({ block: "start" });
    }
  }, [allowed, detail, selected]);
  return <section className={`${panelStyles.panel} ${styles.panel}`} aria-labelledby="admin-feedback-title">
    <h2 id="admin-feedback-title">평가 목록</h2>
    {!allowed ? <p role="status">{status === "loading" ? "권한 확인 중…" : "최고 관리자만 평가를 조회할 수 있어요."}</p> : <>
      <p className={panelStyles.intro}>평가와 사유를 선택해 검토할 답변을 찾아보세요.</p>
      <div className={styles.filters}>
        <label>평가 <select value={rating} onChange={event => { setRating(event.target.value); setPage(1); }}><option value="">전체</option><option value="up">좋아요</option><option value="down">아쉬워요</option></select></label>
        <label>사유 <select value={reason} onChange={event => { setReason(event.target.value); setPage(1); }}><option value="">전체</option>{Object.entries(FEEDBACK_REASONS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <button type="button" disabled={busy} onClick={() => setReload(value => value + 1)}>새로고침</button>
      </div>
      {error && <p className={styles.error} role="alert">{error}</p>}
      <div className={styles.workspace}>
      <div className={styles.results}>
      <p className={styles.summary} role="status">{busy ? "평가 불러오는 중…" : data ? `조회된 평가 ${data.count}건` : ""}</p>
      {data && <><ul className={styles.list}>{data.results.map(row => <li key={row.id}><button type="button" aria-pressed={selected === row.id} onClick={event => { selectionButton.current = event.currentTarget; setError(""); if (selected === row.id && !detail) setDetailReload(value => value + 1); setSelected(row.id); }}><span className={styles.badge}>{row.rating === "up" ? "좋아요" : "아쉬워요"}</span><span className={styles.question}>{row.question.slice(0, 100)}</span><small>{new Date(row.updated_at).toLocaleString("ko-KR")} · {selected === row.id ? "선택됨" : "상세 보기"}</small></button></li>)}</ul>{!data.results.length && <div className={styles.empty}><h3>{rating || reason ? "조건에 맞는 평가가 없어요" : "아직 등록된 평가가 없어요"}</h3><p>{rating || reason ? "평가 또는 사유를 전체로 바꿔 다시 확인해 보세요." : "사용자가 챗봇 답변에 평가를 남기면 이곳에서 확인할 수 있어요."}</p></div>}
        <nav className={styles.pager} aria-label="평가 페이지"><button type="button" disabled={busy || page === 1} onClick={() => setPage(value => value - 1)}>이전</button><span>{page} / {Math.max(1, Math.ceil(data.count / 20))}</span><button type="button" disabled={busy || page * 20 >= data.count} onClick={() => setPage(value => value + 1)}>다음</button></nav></>}
      </div>
      <div className={styles.inspector}>
      {selected === null && <div className={styles.selectionHint}><h2>평가 상세</h2><p>목록에서 평가를 선택하면 질문과 답변을 확인할 수 있어요.</p></div>}
      {selected !== null && !detail && !error && <p role="status">상세 불러오는 중…</p>}
      {detail && selected === detail.id && <section className={styles.detail} aria-label="평가 상세"><div className={styles.detailHeader}><h2 ref={detailTitle} tabIndex={-1}>평가 #{detail.id}</h2><button type="button" onClick={() => { setSelected(null); setDetail(null); selectionButton.current?.focus({ preventScroll: !window.matchMedia("(max-width: 900px)").matches }); }}>상세 닫기</button></div><dl><dt>평가</dt><dd>{detail.rating === "up" ? "좋아요" : "아쉬워요"}</dd><dt>사유</dt><dd>{FEEDBACK_REASONS[detail.reason as keyof typeof FEEDBACK_REASONS] ?? "없음"}</dd><dt>의견</dt><dd>{detail.comment || "없음"}</dd><dt>질문 스냅샷</dt><dd className={styles.snapshot}>{detail.question}</dd><dt>답변 스냅샷</dt><dd className={styles.snapshot}>{detail.answer}</dd></dl><AdminFeedbackMetadata sessionId={detail.session_id} answerId={detail.answer_id} messageId={detail.message_id} metadata={detail.metadata} /></section>}
      </div>
      </div>
      <p className={styles.note}>평가 당시 질문·답변을 검토합니다. 자동 학습·LangSmith 연동은 하지 않습니다.</p>
    </>}
  </section>;
}
