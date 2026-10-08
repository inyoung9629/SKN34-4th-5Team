"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api/client";
import type { CommunityMemberSummaryDto, CommunityMemberPostDto, CommunityMemberCommentDto, CommunityMemberPageDto } from "@/lib/api/content";
import { fetchCommunityMember, fetchCommunityMemberPosts, fetchCommunityMemberComments } from "@/lib/community-member-api";
import { useMemberAuth } from "@/lib/member-auth";
import { activityHref, memberActivityLoginHref, type ActivityTab } from "@/lib/member-return-path";
import { getCommunityPostHref, getTeamBoard } from "@/lib/team-community";
import styles from "./community-member-page.module.css";

type Props = { memberId: number; tab: ActivityTab; page: number };
type ActivityData =
  | { kind: "posts"; member: CommunityMemberSummaryDto; list: CommunityMemberPageDto<CommunityMemberPostDto> }
  | { kind: "comments"; member: CommunityMemberSummaryDto; list: CommunityMemberPageDto<CommunityMemberCommentDto> };
type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; data: ActivityData }
  | { kind: "unauthorized" }
  | { kind: "private" }
  | { kind: "error"; message: string; status: number | null };

const dateFormatter = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
});
function boardLabel(board: "free" | "teams", teamCode: string) {
  return board === "free" ? "자유게시판" : `${getTeamBoard(teamCode)?.shortName ?? "팀"} 게시판`;
}
function failureMessage(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 403) return "이 회원의 활동을 조회할 권한이 없습니다.";
    if (error.status === 404) return "회원 또는 요청한 활동 페이지를 찾을 수 없습니다.";
    return error.message;
  }
  if (error instanceof Error && error.name === "TimeoutError") return "요청 시간이 초과되었습니다. 다시 시도해 주세요.";
  return "멤버 활동을 불러오지 못했습니다. 다시 시도해 주세요.";
}

export function CommunityMemberPage(props: Props) {
  const { status, user, reload } = useMemberAuth();
  const router = useRouter();
  const loginHref = memberActivityLoginHref(props.memberId, props.tab, props.page);
  useEffect(() => {
    if (status === "anonymous") router.replace(loginHref);
  }, [status, router, loginHref]);
  if (status === "loading") return <main className={`container ${styles.page}`}><p role="status">로그인 상태를 확인하고 있습니다.</p></main>;
  if (status === "unavailable") return <main className={`container ${styles.page}`}><h1>멤버 활동</h1><p role="alert">로그인 상태를 확인하지 못했습니다.</p><button type="button" className={styles.actionButton} onClick={() => void reload()}>다시 확인</button></main>;
  if (status !== "authenticated" || !user) return <main className={`container ${styles.page}`}><p role="status">로그인 화면으로 이동하고 있습니다.</p><Link href={loginHref}>로그인하기</Link></main>;
  return <ActivityContent key={`${user.id}:${props.memberId}:${props.tab}:${props.page}`} {...props} viewerId={user.id} />;
}

function ActivityContent({ memberId, tab, page, viewerId }: Props & { viewerId: number }) {
  const router = useRouter();
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const loginHref = memberActivityLoginHref(memberId, tab, page);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const member = await fetchCommunityMember(memberId, controller.signal);
        if (!member.activityVisible && viewerId !== member.id) {
          if (!controller.signal.aborted) setState({ kind: "private" });
          return;
        }
        let data: ActivityData;
        if (tab === "posts") {
          const list = await fetchCommunityMemberPosts(memberId, page, controller.signal);
          data = { kind: "posts", member, list };
        } else {
          const list = await fetchCommunityMemberComments(memberId, page, controller.signal);
          data = { kind: "comments", member, list };
        }
        if (!controller.signal.aborted) setState({ kind: "ready", data });
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && error.status === 401) {
          setState({ kind: "unauthorized" });
          router.replace(loginHref);
          return;
        }
        if (error instanceof ApiError && error.status === 403) {
          setState({ kind: "private" });
          return;
        }
        setState({ kind: "error", message: failureMessage(error), status: error instanceof ApiError ? error.status : null });
      }
    }
    void load();
    return () => controller.abort();
  }, [memberId, tab, page, viewerId, retry, router, loginHref]);

  return <main className={`container ${styles.page}`}>
    <p className={styles.eyebrow}>COMMUNITY MEMBER</p>
    <header className={styles.header}>
      <h1>{state.kind === "ready" ? `${state.data.member.nickname}님의 활동` : "멤버 활동"}</h1>
      <p>작성한 게시글과 댓글을 최신순으로 확인할 수 있습니다.</p>
    </header>
    <nav className={styles.tabs} aria-label="멤버 활동 종류">
      <Link href={activityHref(memberId, "posts")} aria-current={tab === "posts" ? "page" : undefined} prefetch={false} scroll={false}>작성한 글</Link>
      <Link href={activityHref(memberId, "comments")} aria-current={tab === "comments" ? "page" : undefined} prefetch={false} scroll={false}>작성한 댓글</Link>
    </nav>
    <section aria-label={tab === "posts" ? "작성한 글" : "작성한 댓글"}>
      {state.kind === "loading" && <p className={styles.notice} role="status">{tab === "posts" ? "게시글" : "댓글"}을 불러오고 있습니다.</p>}
      {state.kind === "unauthorized" && <p className={styles.notice} role="status">인증이 만료되었습니다. <Link href={loginHref}>로그인하기</Link></p>}
      {state.kind === "private" && <p className={styles.notice} role="status">이 회원의 글·댓글 활동 목록은 비공개입니다.</p>}
      {state.kind === "error" && <div className={styles.notice}>
        <p role="alert">{state.message}</p>
        {state.status !== 403 && state.status !== 404 && <button type="button" className={styles.actionButton} onClick={() => { setState({ kind: "loading" }); setRetry(value => value + 1); }}>다시 시도</button>}
        {page > 1 && <Link href={activityHref(memberId, tab)}>첫 페이지로 이동</Link>}
      </div>}
      {state.kind === "ready" && <>
        <p className={styles.summary}>총 {state.data.list.count.toLocaleString("ko-KR")}개 · 최신순</p>
        {state.data.list.results.length === 0 ? <p className={styles.notice}>{tab === "posts" ? "작성한 게시글이 없습니다." : "작성한 댓글이 없습니다."}</p>
          : state.data.kind === "posts" ? <ul className={styles.list}>{state.data.list.results.map(post => <li key={post.id}>
            <Link className={styles.item} href={getCommunityPostHref(post)}>
              <span className={styles.board}>{boardLabel(post.board, post.teamCode)}</span>
              <span className={styles.title}>{post.title}</span>
              {post.createdAt ? <time dateTime={post.createdAt}>{dateFormatter.format(new Date(post.createdAt))}</time> : <span>작성일 없음</span>}
            </Link>
          </li>)}</ul> : <ul className={styles.list}>{state.data.list.results.map(comment => <li key={comment.id}>
            <Link className={`${styles.item} ${styles.commentItem}`} href={getCommunityPostHref({ id: comment.postId, board: comment.board, teamCode: comment.teamCode })}>
              <p className={styles.commentContent}>{comment.content}</p>
              <div className={styles.commentMeta}>
                <span className={styles.board}>{boardLabel(comment.board, comment.teamCode)}</span>
                <span className={styles.originalPost}>원문: {comment.postTitle}</span>
                <time dateTime={comment.createdAt}>{dateFormatter.format(new Date(comment.createdAt))}</time>
              </div>
            </Link>
          </li>)}</ul>}
        <nav className={styles.pagination} aria-label="활동 목록 페이지">
          {page > 1 ? <Link href={activityHref(memberId, tab, page - 1)} prefetch={false} scroll={false}>이전</Link> : <span aria-disabled="true">이전</span>}
          <span aria-current="page">{page}페이지</span>
          {state.data.list.next !== null && page < 2_147_483_647 ? <Link href={activityHref(memberId, tab, page + 1)} prefetch={false} scroll={false}>다음</Link> : <span aria-disabled="true">다음</span>}
        </nav>
      </>}
    </section>
    <footer className={styles.footer}><Link href="/community">자유게시판</Link><Link href="/community/teams">팀 게시판</Link></footer>
  </main>;
}
