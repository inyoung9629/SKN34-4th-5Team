"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { getMemberUser, listAdminMembers, updateAdminRole, type AdminMember, type MemberUser } from "@/lib/api/auth";
import { actOnAdminReport, deleteAdminPost, hideAdminPost, listAdminPosts, listAdminReports, reportReasonLabel, reportStatusLabel, sanctionLabel, type AdminPost, type AdminReport, type AdminSanction } from "@/lib/api/admin-community";
import type { Page } from "@/lib/api/types";
import { memberRoleLabel } from "@/lib/member-policy";
import { getTeamBoard, getTeamBoardHref } from "@/lib/team-community";
import styles from "./admin-panels.module.css";

const PAGE_SIZE = 20;
const errorText = (cause: unknown, fallback: string) => cause instanceof Error ? cause.message : fallback;
const dateText = (value: string | null) => value ? new Date(value).toLocaleDateString("ko-KR", { timeZone: "Asia/Seoul" }) : "-";
const postHref = (post: Pick<AdminPost, "board" | "team_code" | "source_id">) => post.board === "free"
  ? `/community?post=${encodeURIComponent(post.source_id)}`
  : getTeamBoardHref(post.team_code, post.source_id);
const boardLabel = (post: Pick<AdminPost, "board" | "team_code">) => post.board === "free" ? "자유" : getTeamBoard(post.team_code)?.name ?? post.team_code;

function Pager({ page, count, busy, label, onChange }: { page: number; count: number; busy: boolean; label: string; onChange: (page: number) => void }) {
  return <nav className={styles.pages} aria-label={label}>
    <button type="button" disabled={page === 1 || busy} onClick={() => onChange(page - 1)}>이전</button>
    <span>{page} / {Math.max(1, Math.ceil(count / PAGE_SIZE))}</span>
    <button type="button" disabled={page * PAGE_SIZE >= count || busy} onClick={() => onChange(page + 1)}>다음</button>
  </nav>;
}

function SearchBox({ query, busy, label, placeholder, onSearch }: { query: string; busy: boolean; label: string; placeholder: string; onSearch: (query: string) => void }) {
  return <form className={styles.search} onSubmit={event => { event.preventDefault(); onSearch(String(new FormData(event.currentTarget).get("q") ?? "").trim()); }}>
    <input name="q" aria-label={label} placeholder={placeholder} maxLength={150} defaultValue={query} />
    <button disabled={busy} type="submit">검색</button>
  </form>;
}

/** 목록 조회 공통 상태: 검색어·페이지가 바뀌거나 reload가 늘면 다시 불러온다. */
function usePagedList<T>(load: (params: URLSearchParams, signal: AbortSignal) => Promise<Page<T> | null>, fallback: string) {
  const [data, setData] = useState<Page<T> | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ page: String(page) });
    if (query) params.set("q", query);
    void Promise.resolve().then(async () => {
      if (controller.signal.aborted) return;
      setLoading(true); setError("");
      try {
        const result = await load(params, controller.signal);
        if (!result) throw new Error(fallback);
        if (!controller.signal.aborted) setData(result);
      } catch (cause) { if (!controller.signal.aborted) { setData(null); setError(errorText(cause, fallback)); } }
      finally { if (!controller.signal.aborted) setLoading(false); }
    });
    return () => controller.abort();
  }, [load, fallback, query, page, reload]);
  // 마지막 항목을 지워 빈 페이지가 되면 앞 페이지로 돌아간다
  const refresh = (removedLast = false) => { if (removedLast && page > 1) setPage(value => value - 1); else setReload(value => value + 1); };
  return { data, error, loading, query, page, setPage, refresh, search: (next: string) => { setQuery(next); setPage(1); } };
}

export function AdminMembersPanel() {
  const [me, setMe] = useState<MemberUser | null>(null);
  const [notice, setNotice] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [load] = useState(() => async (params: URLSearchParams, signal: AbortSignal) => {
    const [user, members] = await Promise.all([getMemberUser(signal), listAdminMembers(params, signal)]);
    if (user) setMe(user);
    return members;
  });
  const list = usePagedList<AdminMember>(load, "회원 목록 응답을 확인하지 못했어요.");
  async function changeRole(member: AdminMember) {
    if (busy) return;
    const action = member.is_staff ? "회수" : "부여";
    if (!window.confirm(`${member.username} 회원의 운영 관리자 권한을 ${action}하시겠어요?`)) return;
    setBusy(true); setActionError(""); setNotice("");
    try {
      await updateAdminRole(member.id, { is_staff: !member.is_staff });
      setNotice(`${member.username} 회원의 운영 관리자 권한을 ${action}했어요.`);
      list.refresh();
    } catch (cause) { setActionError(errorText(cause, "권한 변경에 실패했어요.")); }
    finally { setBusy(false); }
  }
  const { data } = list;
  return <section className={styles.panel} aria-labelledby="admin-members-title">
    <h2 id="admin-members-title">회원 관리</h2>
    <p className={styles.intro}>회원 정보를 확인하고 운영 관리자 권한을 관리하세요. <Link href="/admin/baseball">야구 데이터 관리 →</Link></p>
    {list.loading && !data && <p role="status">회원 목록을 불러오고 있어요.</p>}
    {list.error && <div className={styles.feedback} role="alert"><p>{list.error}</p><button type="button" onClick={() => list.refresh()}>다시 확인</button></div>}
    {actionError && <p className={styles.feedback} role="alert">{actionError}</p>}
    {notice && <p role="status" className={styles.feedback}>{notice}</p>}
    {data && <>
      {me && <div className={styles.identity}><strong>{me.username}</strong><span>{memberRoleLabel(me)}</span><span>전체 회원 {data.count}명</span></div>}
      <SearchBox query={list.query} busy={busy} label="회원 검색" placeholder="회원 번호 또는 아이디" onSearch={list.search} />
      <p className={styles.scrollHint} id="members-scroll-hint">표가 잘리면 좌우로 스크롤해 나머지 항목을 확인하세요.</p>
      <div className={styles.tableScroll} role="region" aria-label="회원 목록 가로 스크롤" aria-describedby="members-scroll-hint" tabIndex={0}><table><caption className="sr-only">회원 목록과 관리자 권한</caption>
        <thead><tr>{["회원 번호", "아이디", "권한", "상태", "가입일", "권한 관리"].map(label => <th key={label}>{label}</th>)}</tr></thead>
        <tbody>{data.results.map(member => <tr key={member.id}>
          <td className={styles.nowrap}>{member.id}</td><td className={styles.longText}>{member.username}</td><td className={styles.nowrap}>{memberRoleLabel(member)}</td><td className={styles.nowrap}>{member.is_active ? "활성" : "비활성"}</td><td className={styles.nowrap}>{member.date_joined.slice(0, 10)}</td>
          <td className={styles.nowrap}>{me?.is_superuser && member.id !== me.id && !member.is_superuser && member.is_active
            ? <button type="button" disabled={busy} onClick={() => void changeRole(member)}>{member.is_staff ? "운영 관리자 회수" : "운영 관리자 부여"}</button>
            : <span className={styles.muted}>변경 불가</span>}</td>
        </tr>)}
        {!data.results.length && <tr><td colSpan={6}>검색 결과가 없어요.</td></tr>}</tbody>
      </table></div>
      <Pager page={list.page} count={data.count} busy={busy} label="회원 목록 페이지" onChange={list.setPage} />
      <p className={styles.intro}>운영 관리자는 회원을 조회할 수 있으며, 권한 부여·회수는 마스터 관리자만 할 수 있어요.</p>
    </>}
  </section>;
}

export function AdminPostsPanel() {
  const [notice, setNotice] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const list = usePagedList<AdminPost>(listAdminPosts, "게시글 목록을 불러오지 못했어요.");
  async function hide(post: AdminPost) {
    if (busy || list.loading || post.is_hidden || !window.confirm(`#${post.post_number} "${post.title}" 게시글을 숨길까요?\n신고 관리의 숨김과 동일하게 처리하며, 게시글은 삭제하지 않습니다.`)) return;
    setBusy(true); setActionError(""); setNotice("");
    try {
      await hideAdminPost(post.source_id);
      setNotice(`#${post.post_number} 게시글을 숨겼어요.`);
      list.refresh();
    } catch (cause) { setActionError(errorText(cause, "게시글을 숨기지 못했어요.")); }
    finally { setBusy(false); }
  }
  async function remove(post: AdminPost) {
    if (busy || !window.confirm(`#${post.post_number} "${post.title}" 게시글을 삭제하시겠어요?\n댓글과 신고 기록도 함께 삭제되고 되돌릴 수 없어요.`)) return;
    setBusy(true); setActionError(""); setNotice("");
    try {
      await deleteAdminPost(post.source_id);
      setNotice(`#${post.post_number} 게시글을 삭제했어요.`);
      list.refresh(list.data?.results.length === 1);
    } catch (cause) { setActionError(errorText(cause, "게시글을 삭제하지 못했어요.")); }
    finally { setBusy(false); }
  }
  const { data } = list;
  return <section className={styles.panel} aria-labelledby="admin-posts-title">
    <h2 id="admin-posts-title">게시글 관리</h2>
    <p className={styles.intro}>커뮤니티 게시글을 확인하고 운영 정책에 따라 숨기거나 삭제하세요. 숨김은 신고 관리와 동일한 기준으로 처리돼요.</p>
    {list.loading && !data && <p role="status">게시글을 불러오고 있어요.</p>}
    {list.error && <div className={styles.feedback} role="alert"><p>{list.error}</p><button type="button" onClick={() => list.refresh()}>다시 확인</button></div>}
    {actionError && <p className={styles.feedback} role="alert">{actionError}</p>}
    {notice && <p role="status" className={styles.feedback}>{notice}</p>}
    {data && <>
      <SearchBox query={list.query} busy={busy} label="게시글 검색" placeholder="제목, 작성자 또는 글 번호" onSearch={list.search} />
      <p className={styles.scrollHint} id="posts-scroll-hint">표가 잘리면 좌우로 스크롤해 나머지 항목을 확인하세요.</p>
      <div className={styles.tableScroll} role="region" aria-label="게시글 목록 가로 스크롤" aria-describedby="posts-scroll-hint" tabIndex={0}><table><caption className="sr-only">커뮤니티 게시글 목록</caption>
        <thead><tr>{["글 번호", "게시판", "제목", "작성자", "작성일", "조회", "댓글", "신고", "관리"].map(label => <th key={label}>{label}</th>)}</tr></thead>
        <tbody>{data.results.map(post => <tr key={post.source_id}>
          <td className={styles.nowrap}>{post.post_number}</td><td className={styles.nowrap}>{boardLabel(post)}</td>
          <td><Link href={postHref(post)} target="_blank">{post.title}</Link>{post.is_sample && <span className={styles.muted}> (예시)</span>}{post.is_hidden && <span className={styles.hiddenTag}>숨김</span>}</td>
          <td className={styles.longText}>{post.author}</td><td className={styles.nowrap}>{dateText(post.created_at)}</td><td className={styles.nowrap}>{post.views}</td><td className={styles.nowrap}>{post.comment_count}</td>
          <td className={styles.nowrap}><span className={`${styles.count}${post.report_count ? ` ${styles.hot}` : ""}`}>{post.report_count}</span></td>
          <td className={styles.nowrap}><div className={styles.actions}><button type="button" className={styles.hide} disabled={busy || list.loading || post.is_hidden} onClick={() => void hide(post)}>{post.is_hidden ? "숨김 처리됨" : "숨김"}</button><button type="button" className={styles.delete} disabled={busy} onClick={() => void remove(post)}>삭제</button></div></td>
        </tr>)}
        {!data.results.length && <tr><td colSpan={9}>게시글이 없어요.</td></tr>}</tbody>
      </table></div>
      <Pager page={list.page} count={data.count} busy={busy} label="게시글 목록 페이지" onChange={list.setPage} />
    </>}
  </section>;
}

const SANCTIONS: AdminSanction[] = ["none", "7d", "30d", "permanent"];

/** 글 삭제 전 작성자 계정 처분을 고르는 팝업 */
function SanctionDialog({ report, busy, onCancel, onConfirm }: { report: AdminReport; busy: boolean; onCancel: () => void; onConfirm: (sanction: AdminSanction) => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [sanction, setSanction] = useState<AdminSanction>("none");
  const owner = report.post.owner;
  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) dialog.showModal();
    return () => { if (dialog?.open) dialog.close(); };
  }, []);
  return <dialog ref={dialogRef} className={styles.dialog} aria-labelledby="sanction-title" onCancel={event => { event.preventDefault(); if (!busy) onCancel(); }}>
    <form method="dialog" onSubmit={event => { event.preventDefault(); onConfirm(sanction); }}>
      <h3 id="sanction-title">해당 계정에 대한 처분을 결정하십시오</h3>
      <p className={styles.dialogPost}>#{report.post.post_number} {report.post.title}</p>
      <dl className={styles.account}>
        <div><dt>닉네임</dt><dd>{owner ? owner.nickname || "(닉네임 없음)" : report.post.author}</dd></div>
        <div><dt>아이디</dt><dd>{owner ? owner.username : "계정 정보 없음"}</dd></div>
      </dl>
      <fieldset className={styles.sanctions} disabled={busy}>
        <legend>처분</legend>
        {SANCTIONS.map(value => <label key={value} className={sanction === value ? styles.selected : undefined}>
          <input type="radio" name="sanction" value={value} checked={sanction === value} onChange={() => setSanction(value)} />
          {sanctionLabel[value]}
        </label>)}
      </fieldset>
      <p className={styles.dialogNote}>확인하면 게시글이 삭제되고(댓글·신고 포함) 되돌릴 수 없어요.</p>
      <div className={styles.dialogActions}>
        <button type="button" disabled={busy} onClick={onCancel}>취소</button>
        <button type="submit" className={styles.delete} disabled={busy}>{busy ? "처리 중…" : "삭제하고 처분"}</button>
      </div>
    </form>
  </dialog>;
}

export function AdminReportsPanel() {
  const [notice, setNotice] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState<AdminReport | null>(null);
  const list = usePagedList<AdminReport>(listAdminReports, "신고 목록을 불러오지 못했어요.");
  async function run(report: AdminReport, action: "hold" | "unhold" | "hide" | "delete", sanction: AdminSanction = "none") {
    if (busy) return;
    if (action === "hide" && !window.confirm(`#${report.post.post_number} "${report.post.title}" 게시글을 숨기시겠어요?\n숨긴 글은 커뮤니티에서 보이지 않아요.`)) return;
    setBusy(true); setActionError(""); setNotice("");
    try {
      await actOnAdminReport(report.id, action, sanction);
      if (action === "hold") setNotice(`#${report.post.post_number} 신고를 보류했어요.`);
      else if (action === "unhold") setNotice(`#${report.post.post_number} 보류를 취소하고 처리 대기로 변경했어요.`);
      else if (action === "hide") setNotice(`#${report.post.post_number} 게시글을 숨겼어요.`);
      else {
        setNotice(`#${report.post.post_number} 게시글을 삭제했어요.`);
        setDeleting(null);
      }
      // 삭제하면 같은 글의 신고가 모두 사라지므로 목록을 새로 불러온다
      list.refresh(action === "delete" && list.data?.results.length === 1);
    } catch (cause) { setActionError(errorText(cause, "신고를 처리하지 못했어요.")); }
    finally { setBusy(false); }
  }
  const { data } = list;
  const waiting = data?.results.filter(report => report.status === "pending").length ?? 0;
  return <section className={styles.panel} aria-labelledby="admin-reports-title">
    <h2 id="admin-reports-title">신고 관리</h2>
    <p className={styles.intro}>회원이 신고한 게시글을 확인하세요. 판단을 미룰 때는 보류, 공개를 막을 때는 숨김, 글을 지울 때는 삭제를 누르세요.</p>
    {list.loading && !data && <p role="status">신고 목록을 불러오고 있어요.</p>}
    {list.error && <div className={styles.feedback} role="alert"><p>{list.error}</p><button type="button" onClick={() => list.refresh()}>다시 확인</button></div>}
    {actionError && <p className={styles.feedback} role="alert">{actionError}</p>}
    {notice && <p role="status" className={styles.feedback}>{notice}</p>}
    {data && <>
      <p className={styles.identity}><strong>전체 신고</strong><span>{data.count}건{waiting ? ` · 이 페이지 처리 대기 ${waiting}건` : ""}</span></p>
      <p className={styles.scrollHint} id="reports-scroll-hint">표가 잘리면 좌우로 스크롤해 나머지 항목을 확인하세요.</p>
      <div className={styles.tableScroll} role="region" aria-label="신고 목록 가로 스크롤" aria-describedby="reports-scroll-hint" tabIndex={0}><table><caption className="sr-only">게시글 신고 목록</caption>
        <thead><tr>{["신고일", "게시글", "작성자", "신고자", "사유", "상세 내용", "상태", "처리"].map(label => <th key={label}>{label}</th>)}</tr></thead>
        <tbody>{data.results.map(report => <tr key={report.id}>
          <td className={styles.nowrap}>{dateText(report.created_at)}</td>
          <td><Link href={postHref(report.post)} target="_blank">#{report.post.post_number} {report.post.title}</Link></td>
          <td className={styles.longText}>{report.post.author}{report.post.owner && <span className={styles.muted}> ({report.post.owner.username})</span>}</td>
          <td className={styles.longText}>{report.reporter}</td><td className={styles.nowrap}>{reportReasonLabel[report.reason] ?? report.reason}</td>
          <td className={styles.longText}>{report.detail || <span className={styles.muted}>없음</span>}</td>
          <td className={styles.nowrap}><span className={`${styles.status} ${styles[`status_${report.status}`] ?? ""}`}>{reportStatusLabel[report.status] ?? report.status}</span></td>
          <td className={styles.nowrap}><div className={styles.actions}>
            <button type="button" className={styles.hold} disabled={busy || report.post.is_hidden || report.status === "hidden"} onClick={() => void run(report, report.status === "held" ? "unhold" : "hold")}>{report.status === "held" ? "보류 취소" : "보류"}</button>
            <button type="button" className={styles.hide} disabled={busy || report.post.is_hidden} onClick={() => void run(report, "hide")}>숨김</button>
            <button type="button" className={styles.delete} disabled={busy} onClick={() => { setActionError(""); setDeleting(report); }}>삭제</button>
          </div></td>
        </tr>)}
        {!data.results.length && <tr><td colSpan={8}>처리할 신고가 없어요.</td></tr>}</tbody>
      </table></div>
      <Pager page={list.page} count={data.count} busy={busy} label="신고 목록 페이지" onChange={list.setPage} />
    </>}
    {deleting && <SanctionDialog key={deleting.id} report={deleting} busy={busy} onCancel={() => setDeleting(null)} onConfirm={sanction => void run(deleting, "delete", sanction)} />}
  </section>;
}
