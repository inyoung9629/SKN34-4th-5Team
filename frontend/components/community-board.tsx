"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { deleteCommunityPost, getCommunityPost, retryCommunityPosts, useCommunityPosts } from "@/lib/community-api";
import { useMemberAuth } from "@/lib/member-auth";
import { getCommunityPostHref, getCommunityWriteHref, getTeamBoard, getTeamBoardHref, teamBoards, type TeamCommunityPost } from "@/lib/team-community";
import { CommunityNavigation } from "./community-navigation";
import { CommunityPostBottom } from "./community-post-bottom";
import { CommunityPostContent } from "./community-post-content";
import { CommunityPostVote } from "./community-post-vote";
import { PostCategory } from "./post-category";
import { PostCommentCount } from "./post-comment-count";
import { PostReportButton } from "./post-report-button";
import { CommunityMemberLink } from "./community-member-link";
import styles from "./community-board.module.css";

const boards = {
  free: { title: "자유 게시판", description: "응원하는 팀에 관계없이 모든 야구팬과 자유롭게 이야기를 나눠보세요." },
  teams: { title: "팀 게시판", description: "응원하는 팀을 골라 같은 팀 팬들과 야구 이야기를 나눠보세요." },
} as const;

const formatDate = (value: string | null, detail = false) => value ? new Intl.DateTimeFormat("ko-KR", detail
  ? { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Seoul" }
  : { year: "2-digit", month: "2-digit", day: "2-digit", timeZone: "Asia/Seoul" }).format(new Date(value)) : "—";

function authorId(post: TeamCommunityPost) {
  return "authorId" in post && typeof post.authorId === "number" ? post.authorId : null;
}

export function CommunityBoard({ section, teamCode = "", postId = "" }: { section: keyof typeof boards; teamCode?: string; postId?: string }) {
  const router = useRouter();
  const { user } = useMemberAuth();
  const community = useCommunityPosts();
  const team = section === "teams" ? getTeamBoard(teamCode) : undefined;
  const board = boards[section];
  const detailKey = `${section}:${postId}`;
  const detailRequest = useRef("");
  const [detail, setDetail] = useState<{ key: string; post: TeamCommunityPost | null; loading: boolean; error: string }>({ key: "", post: null, loading: false, error: "" });
  const [deleting, setDeleting] = useState(false);
  const [actionError, setActionError] = useState("");

  useEffect(() => {
    if (!postId) { detailRequest.current = ""; return; }
    if (detailRequest.current === detailKey) return;
    detailRequest.current = detailKey;
    setDetail({ key: detailKey, post: null, loading: true, error: "" });
    void getCommunityPost(postId).then(post => {
      if (detailRequest.current !== detailKey) return;
      setDetail(post.board === section ? { key: detailKey, post, loading: false, error: "" } : { key: detailKey, post: null, loading: false, error: "게시글을 찾을 수 없어요." });
    }).catch(cause => {
      if (detailRequest.current === detailKey) setDetail({ key: detailKey, post: null, loading: false, error: cause instanceof Error ? cause.message : "게시글을 불러오지 못했어요." });
    });
  }, [detailKey, postId, section]);

  const allPosts = community.posts.filter(post => post.board === section);
  const searchContext = `${section}:${teamCode}`;
  const [search, setSearch] = useState({ context: searchContext, team: team?.code ?? "all", field: "all", query: "" });
  const activeSearch = search.context === searchContext ? search : { context: searchContext, team: team?.code ?? "all", field: "all", query: "" };
  const query = activeSearch.query.trim().toLocaleLowerCase("ko-KR");
  const posts = allPosts.filter(post => {
    if (activeSearch.team !== "all" && post.teamCode !== activeSearch.team) return false;
    const text = activeSearch.field === "author" ? post.author : activeSearch.field === "title" ? post.title : `${post.title} ${post.content}`;
    return text.toLocaleLowerCase("ko-KR").includes(query);
  });
  const scope = JSON.stringify([searchContext, activeSearch.team, activeSearch.field, query]);
  const [pagination, setPagination] = useState({ scope, page: 1 });
  const pageCount = Math.max(1, Math.ceil(posts.length / 20));
  const page = pagination.scope === scope ? Math.min(pagination.page, pageCount) : 1;
  const visiblePosts = posts.slice((page - 1) * 20, page * 20);
  const pageStart = Math.floor((page - 1) / 5) * 5 + 1;
  const pages = Array.from({ length: Math.min(5, pageCount - pageStart + 1) }, (_, index) => pageStart + index);
  const selectedPost = postId && detail.key === detailKey ? detail.post : null;
  const postHref = (code: string, id: string) => getCommunityPostHref({ id, board: section, teamCode: code });
  const listHref = section === "free" ? "/community" : getTeamBoardHref(team?.code);
  const writeHref = getCommunityWriteHref(section, team?.code);
  const owned = Boolean(selectedPost && user && authorId(selectedPost) === user.id);

  async function removePost() {
    if (!selectedPost || deleting || !window.confirm("이 게시글을 삭제할까요? 삭제한 글은 되돌릴 수 없어요.")) return;
    setDeleting(true); setActionError("");
    try { await deleteCommunityPost(selectedPost.id); detailRequest.current = ""; router.replace(listHref); }
    catch (cause) { setActionError(cause instanceof Error ? cause.message : "게시글을 삭제하지 못했어요."); }
    finally { setDeleting(false); }
  }

  return <main className={`container ${styles.page} header-aligned-content`}>
    <p className="eyebrow">COMMUNITY</p><h1>{board.title}</h1><p className={styles.description}>{board.description}</p>
    <CommunityNavigation active={section} teamCode={team?.code} />
    {section === "teams" && <nav className={styles.teamTabs} aria-label="팀 게시판 선택"><Link href={getTeamBoardHref()} aria-current={!team ? "page" : undefined}>전체</Link>{teamBoards.map(item => <Link key={item.code} href={getTeamBoardHref(item.code)} aria-current={team?.code === item.code ? "page" : undefined}>{item.shortName}</Link>)}</nav>}
    {community.error && community.posts.length > 0 && <p role="alert">{community.error} <button type="button" onClick={() => void retryCommunityPosts()}>다시 시도</button></p>}
    {postId && detail.key === detailKey && detail.loading && <p role="status">게시글을 불러오고 있어요.</p>}
    {postId && detail.key === detailKey && detail.error && <p role="alert">{detail.error} <Link href={listHref}>목록으로</Link></p>}
    {selectedPost && <article className={styles.postDetail}>
      <header className={styles.postHeader}>
        <h2 className={styles.postHeading}>{section === "teams" && <strong>{getTeamBoard(selectedPost.teamCode)?.shortName}</strong>}<PostCategory category={selectedPost.category} freeBoard={section === "free"} /><span>{selectedPost.title}<PostCommentCount count={selectedPost.commentCount} /></span></h2>
        <div className={styles.postMeta}><div className={styles.postMetaInfo}><span aria-label={`게시글 번호 ${selectedPost.postNumber}`}>{selectedPost.postNumber}</span><span><CommunityMemberLink memberId={selectedPost.authorId} nickname={selectedPost.author} /></span><time dateTime={selectedPost.createdAt ?? undefined}>{formatDate(selectedPost.createdAt, true)}</time></div><div className={styles.postMetrics}><span>조회 <b>{selectedPost.views}</b></span><span>추천 <b>{selectedPost.recommendations}</b></span><span>댓글 <b>{selectedPost.commentCount ?? 0}</b></span></div></div>
        {owned && <div className={styles.ownerActions}><Link href={`/community/write?edit=${encodeURIComponent(selectedPost.id)}`}>수정</Link><button type="button" onClick={() => void removePost()} disabled={deleting}>{deleting ? "삭제 중…" : "삭제"}</button></div>}
        {actionError && <p role="alert">{actionError}</p>}
      </header>
      <div className={`${styles.postBody} ${styles.teamPostBody} ${styles.reportableBody}`}><CommunityPostContent content={selectedPost.content} document={selectedPost.contentDoc} /><PostReportButton postNumber={selectedPost.postNumber} postId={selectedPost.id} /></div>
      <CommunityPostVote post={selectedPost} />
      <CommunityPostBottom key={selectedPost.id} post={selectedPost} posts={posts} teamCode={team?.code} isFree={section === "free"} writeHref={writeHref} />
    </article>}
    <section aria-label={`${board.title} 글 목록`}>
      {user && <div className={styles.listWriteActions}><Link href={writeHref}>글쓰기</Link></div>}
      {community.loading && community.posts.length === 0 ? <p role="status">게시글을 불러오고 있어요.</p> : community.error && community.posts.length === 0 ? <p role="alert">{community.error} <button type="button" onClick={() => void retryCommunityPosts()}>다시 시도</button></p> : <div className={styles.tableScroll} role="region" aria-label={`${board.title} 목록, 좁은 화면에서는 좌우로 스크롤`} tabIndex={0}>
        <table className={styles.boardTable}><caption className="sr-only">{board.title} 게시글 목록. 구분은 게시글 고유 번호입니다.</caption><colgroup><col className={styles.numberCol} />{section === "teams" && <col className={styles.teamCol} />}<col /><col className={styles.authorCol} /><col className={styles.dateCol} /><col className={styles.countCol} /><col className={styles.countCol} /></colgroup><thead><tr>{["구분", ...(section === "teams" ? ["팀"] : []), "제목", "글쓴이", "작성일", "조회", "추천"].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead>
          <tbody>{visiblePosts.length ? visiblePosts.map(post => <tr key={post.id} className={post.id === postId ? styles.selectedRow : undefined}><td>{post.postNumber}</td>{section === "teams" && <td className={styles.teamCell}>{getTeamBoard(post.teamCode)?.shortName}</td>}<td className={styles.titleCell}><Link href={postHref(post.teamCode, post.id)} aria-current={post.id === postId ? "page" : undefined}><PostCategory category={post.category} freeBoard={section === "free"} /> {post.title}<PostCommentCount count={post.commentCount} /></Link></td><td><CommunityMemberLink memberId={post.authorId} nickname={post.author} /></td><td>{formatDate(post.createdAt)}</td><td>{post.views}</td><td>{post.recommendations}</td></tr>) : <tr><td colSpan={section === "free" ? 6 : 7} className={styles.emptyCell}>{query || activeSearch.team !== "all" ? "검색 결과가 없어요." : "등록된 게시글이 없어요."}</td></tr>}</tbody>
        </table>
      </div>}
      {user && <div className={styles.listWriteActions}><Link href={writeHref}>글쓰기</Link></div>}
      {pageCount > 1 && <nav className={styles.pagination} aria-label="게시판 페이지"><button type="button" aria-label="이전 페이지" disabled={page === 1} onClick={() => setPagination({ scope, page: page - 1 })}>‹</button>{pages.map(number => <button type="button" key={number} aria-label={`${number}페이지`} aria-current={page === number ? "page" : undefined} onClick={() => setPagination({ scope, page: number })}>{number}</button>)}<button type="button" aria-label="다음 페이지" disabled={page === pageCount} onClick={() => setPagination({ scope, page: page + 1 })}>›</button></nav>}
      <form key={searchContext} className={styles.searchForm} role="search" aria-label="게시글 검색" onSubmit={event => { event.preventDefault(); const data = new FormData(event.currentTarget); setSearch({ context: searchContext, team: String(data.get("team") ?? "all"), field: String(data.get("field") ?? "all"), query: String(data.get("query") ?? "") }); setPagination({ scope: "", page: 1 }); }}>
        {section === "teams" && <select name="team" aria-label="검색할 팀" defaultValue={team?.code ?? "all"}><option value="all">전체 팀</option>{teamBoards.map(item => <option key={item.code} value={item.code}>{item.name}</option>)}</select>}<select name="field" aria-label="검색 옵션" defaultValue="all"><option value="all">제목+본문</option><option value="title">제목</option><option value="author">닉네임</option></select><input name="query" type="search" aria-label="검색어" placeholder="검색어를 입력하세요" /><button type="submit">검색</button>
      </form>
    </section>
  </main>;
}
