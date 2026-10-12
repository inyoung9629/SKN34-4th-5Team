"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { createCommunityComment, deleteCommunityComment, fetchCommunityComments, updateCommunityComment, type CommunityComment } from "@/lib/community-api";
import { useMemberAuth } from "@/lib/member-auth";
import { getTeamBoard, getTeamBoardHref, type TeamCommunityPost } from "@/lib/team-community";
import { CommunityMemberLink } from "./community-member-link";
import { communityCategoryHref } from "@/lib/community-category-filter";
import styles from "./community-interactions.module.css";

const errorMessage = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback;
type Props = { post: TeamCommunityPost; posts: TeamCommunityPost[]; teamCode?: string; isFree?: boolean; writeHref?: string; category?: string };

export function CommunityPostBottom(props: Props) {
  const { user } = useMemberAuth();
  const actorId = user?.id ?? null;
  return <CommunityPostBottomContent key={`${props.post.id}:${actorId ?? "anonymous"}`} {...props} actorId={actorId} />;
}

function CommunityPostBottomContent({ post, posts, teamCode, isFree = false, writeHref, actorId, category = "" }: Props & { actorId: number | null }) {
  const postHref = (item: TeamCommunityPost) => {
    const href = isFree ? `/community?post=${encodeURIComponent(item.id)}` : getTeamBoardHref(item.teamCode, item.id);
    return category ? `${href}&category=${encodeURIComponent(category)}` : href;
  };
  const router = useRouter();
  const mounted = useRef(true);
  const requestRef = useRef(0);
  const [comments, setComments] = useState<CommunityComment[]>([]);
  const [order, setOrder] = useState<"oldest" | "newest">("oldest");
  const [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editingContent, setEditingContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; requestRef.current += 1; };
  }, []);

  useEffect(() => {
    const requestId = ++requestRef.current;
    fetchCommunityComments(post.id, order)
      .then(next => { if (requestRef.current === requestId) setComments(next); })
      .catch(reason => { if (requestRef.current === requestId) setError(errorMessage(reason, "댓글을 불러오지 못했어요.")); })
      .finally(() => { if (requestRef.current === requestId) setLoading(false); });
    return () => { requestRef.current += 1; };
  }, [post.id, order, reload]);

  async function createComment(event: FormEvent) {
    event.preventDefault();
    const content = draft.trim();
    if (!actorId) { setError("댓글을 등록하려면 로그인해 주세요."); return; }
    if (!content) { setError("댓글 내용을 입력해 주세요."); return; }
    setSaving(true); setError(""); setMessage("");
    try {
      await createCommunityComment(post.id, content);
      if (!mounted.current) return;
      setDraft(""); setMessage("댓글을 등록했어요."); setReload(value => value + 1);
    } catch (reason) {
      if (mounted.current) setError(errorMessage(reason, "댓글을 등록하지 못했어요."));
    } finally {
      if (mounted.current) setSaving(false);
    }
  }

  async function updateComment(event: FormEvent, commentId: number) {
    event.preventDefault();
    const content = editingContent.trim();
    if (!actorId || !content) { setError(!actorId ? "로그인이 필요해요." : "댓글 내용을 입력해 주세요."); return; }
    setSaving(true); setError(""); setMessage("");
    try {
      await updateCommunityComment(commentId, content);
      if (!mounted.current) return;
      setEditingId(null); setEditingContent(""); setMessage("댓글을 수정했어요."); setReload(value => value + 1);
    } catch (reason) {
      if (mounted.current) setError(errorMessage(reason, "댓글을 수정하지 못했어요."));
    } finally {
      if (mounted.current) setSaving(false);
    }
  }

  async function removeComment(commentId: number) {
    if (!actorId || !window.confirm("댓글을 삭제할까요?")) return;
    setSaving(true); setError(""); setMessage("");
    try {
      await deleteCommunityComment(commentId);
      if (!mounted.current) return;
      setMessage("댓글을 삭제했어요."); setReload(value => value + 1);
    } catch (reason) {
      if (mounted.current) setError(errorMessage(reason, "댓글을 삭제하지 못했어요."));
    } finally {
      if (mounted.current) setSaving(false);
    }
  }

  const index = posts.findIndex(item => item.id === post.id);
  const next = index >= 0 ? posts[index + 1] : undefined;
  const previous = index > 0 ? posts[index - 1] : undefined;
  return <>
    <section className={styles.authorProfile} aria-label="작성자 프로필">
      <img src="/images/default-avatar.svg" width="56" height="56" alt="작성자 기본 프로필" />
      <div><strong><CommunityMemberLink memberId={post.authorId} nickname={post.author} /></strong><p>{!isFree && <>{getTeamBoard(post.teamCode)?.shortName} · </>}작성자</p></div>
    </section>
    <section className={styles.comments} aria-label="댓글">
      <header><h3>댓글 <b>{loading ? (post.commentCount ?? 0) : comments.length}</b></h3><div><button type="button" aria-pressed={order === "oldest"} onClick={() => { if (order !== "oldest") { setLoading(true); setError(""); setOrder("oldest"); } }}>등록순</button><button type="button" aria-pressed={order === "newest"} onClick={() => { if (order !== "newest") { setLoading(true); setError(""); setOrder("newest"); } }}>최신순</button><button type="button" disabled={loading} onClick={() => { setLoading(true); setError(""); setReload(value => value + 1); }}>↻ 새로고침</button></div></header>
      {loading ? <p className={styles.commentsEmpty}>댓글을 불러오는 중이에요.</p> : comments.length === 0 ? <p className={styles.commentsEmpty}>아직 등록된 댓글이 없어요.</p> : <ul className={styles.commentList}>
        {comments.map(comment => <li key={comment.id}>
          {editingId === comment.id ? <form onSubmit={event => void updateComment(event, comment.id)} className={styles.editForm}>
            <textarea aria-label="수정할 댓글 내용" value={editingContent} onChange={event => setEditingContent(event.target.value)} maxLength={2000} required />
            <div><button type="submit" disabled={saving}>저장</button><button type="button" disabled={saving} onClick={() => setEditingId(null)}>취소</button></div>
          </form> : <>
            <div className={styles.commentMeta}><strong><CommunityMemberLink memberId={comment.authorId} nickname={comment.author} /></strong><time dateTime={comment.createdAt}>{new Date(comment.createdAt).toLocaleString("ko-KR")}</time></div>
            <p>{comment.content}</p>
            {comment.authorId === actorId && <div className={styles.commentActions}><button type="button" disabled={saving} onClick={() => { setEditingId(comment.id); setEditingContent(comment.content); }}>수정</button><button type="button" disabled={saving} onClick={() => void removeComment(comment.id)}>삭제</button></div>}
          </>}
        </li>)}
      </ul>}
      {actorId ? <form className={styles.commentForm} onSubmit={event => void createComment(event)}>
        <textarea aria-label="댓글 내용" placeholder={actorId ? "댓글을 입력해 주세요." : "로그인 후 댓글을 작성할 수 있어요."} value={draft} onChange={event => setDraft(event.target.value)} maxLength={2000} required disabled={!actorId || saving} />
        <button type="submit" disabled={!actorId || saving}>{saving ? "처리 중" : "등록"}</button>
      </form> : <p className={styles.bottomNote}>댓글 작성은 <Link href="/login">로그인</Link> 후 이용할 수 있어요.</p>}
      {message && <p role="status" className={styles.bottomNote}>{message}</p>}
      {error && <p role="alert" className={styles.errorNote}>{error}</p>}
    </section>
    <nav className={styles.articleNavigation} aria-label="게시글 이동">
      <div><Link href={communityCategoryHref(isFree ? "/community" : getTeamBoardHref(teamCode), category)}>목록</Link>{next ? <Link href={postHref(next)}>다음글</Link> : <button disabled>다음글</button>}{previous ? <Link href={postHref(previous)}>이전글</Link> : <button disabled>이전글</button>}</div>
      <div>{writeHref && actorId && <Link className={styles.writeButton} href={writeHref}>글쓰기</Link>}<button type="button" onClick={() => router.back()}>이전페이지</button><button type="button" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}>맨위로 ↑</button></div>
    </nav>
  </>;
}
