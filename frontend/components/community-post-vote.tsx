"use client";

import { useEffect, useRef, useState } from "react";
import Image from "next/image";
import { fetchCommunityVote, setCommunityVote, type CommunityVoteState } from "@/lib/community-api";
import { useMemberAuth } from "@/lib/member-auth";
import { nextCommunityVote, type CommunityVote } from "@/lib/community-votes";
import type { TeamCommunityPost } from "@/lib/team-community";
import styles from "./community-interactions.module.css";

function VoteLabel({ direction, count, selected = false }: { direction: "up" | "down"; count: number; selected?: boolean }) {
  return <>
    <Image className={styles.voteImage} src={`/images/community-vote-${direction}.png`} alt="" width={72} height={72} sizes="72px" />
    <span className={styles.voteLabel}>{direction === "up" ? "추천" : "비추천"} <strong>{count.toLocaleString("ko-KR")}</strong></span>
    <span className={styles.voteSelection} aria-hidden="true">{selected ? "선택됨" : ""}</span>
  </>;
}

export function CommunityPostVote({ post }: { post: TeamCommunityPost }) {
  const { user } = useMemberAuth();
  const actorId = user?.id ?? null;
  const downvotes = "downvotes" in post && typeof post.downvotes === "number" ? post.downvotes : 0;
  if (!actorId) return <div className={styles.vote} aria-label="게시글 추천 현황">
    <div className={styles.voteSummary}><VoteLabel direction="up" count={post.recommendations} /></div>
    <div className={styles.voteSummary}><VoteLabel direction="down" count={downvotes} /></div>
  </div>;
  return <CommunityPostVoteContent key={`${post.id}:${actorId ?? "anonymous"}:${post.recommendations}:${downvotes}`} post={post} actorId={actorId} />;
}

function CommunityPostVoteContent({ post, actorId }: { post: TeamCommunityPost; actorId: number | null }) {
  const mounted = useRef(true);
  const requestRef = useRef(0);
  const initialDownvotes = "downvotes" in post && typeof post.downvotes === "number" ? post.downvotes : 0;
  const [state, setState] = useState<CommunityVoteState>({ vote: null, recommendations: post.recommendations, downvotes: initialDownvotes });
  const [pending, setPending] = useState(Boolean(actorId));
  const [error, setError] = useState("");

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; requestRef.current += 1; };
  }, []);

  useEffect(() => {
    const requestId = ++requestRef.current;
    if (!actorId) return;
    fetchCommunityVote(post.id)
      .then(next => { if (requestRef.current === requestId) setState(next); })
      .catch(caught => { if (requestRef.current === requestId) setError(caught instanceof Error ? caught.message : "투표 상태를 불러오지 못했어요."); })
      .finally(() => { if (requestRef.current === requestId) setPending(false); });
    return () => { requestRef.current += 1; };
  }, [actorId, initialDownvotes, post.id, post.recommendations]);

  async function vote(requested: CommunityVote) {
    if (!actorId) { setError("추천하려면 로그인해 주세요."); return; }
    const requestId = ++requestRef.current;
    setPending(true); setError("");
    try {
      const next = await setCommunityVote(post.id, nextCommunityVote(state.vote, requested));
      if (requestRef.current === requestId && mounted.current) setState(next);
    } catch (caught) {
      if (requestRef.current === requestId && mounted.current) setError(caught instanceof Error ? caught.message : "투표를 반영하지 못했어요.");
    } finally {
      if (requestRef.current === requestId && mounted.current) setPending(false);
    }
  }

  return <div className={styles.vote} aria-label="게시글 추천">
    <button type="button" aria-pressed={state.vote === "up"} aria-busy={pending} disabled={pending} onClick={() => void vote("up")}><VoteLabel direction="up" count={state.recommendations} selected={state.vote === "up"} /></button>
    <button type="button" aria-pressed={state.vote === "down"} aria-busy={pending} disabled={pending} onClick={() => void vote("down")}><VoteLabel direction="down" count={state.downvotes} selected={state.vote === "down"} /></button>
    {error && <p role="alert" className={styles.errorNote}>{error}</p>}
  </div>;
}
