"use client";

import Link from "next/link";
import { useMemberAuth } from "@/lib/member-auth";
import { activityHref, memberActivityLoginHref } from "@/lib/member-return-path";
import styles from "./community-member-page.module.css";

export function CommunityMemberLink({ memberId, nickname }: { memberId: number | null | undefined; nickname: string }) {
  const { status, reload } = useMemberAuth();
  if (typeof memberId !== "number" || !Number.isSafeInteger(memberId) || memberId <= 0) return <span>{nickname}</span>;
  if (status === "loading") return <span aria-busy="true" title="로그인 상태 확인 중">{nickname}</span>;
  if (status === "unavailable") return <button type="button" className={styles.memberLink} title="인증 상태를 확인하지 못했습니다. 눌러서 다시 확인하세요." onClick={() => void reload()}>{nickname}</button>;
  const href = status === "authenticated" ? activityHref(memberId) : memberActivityLoginHref(memberId);
  return <Link href={href} prefetch={false} className={styles.memberLink} aria-label={status === "authenticated" ? `${nickname}님의 작성 글과 댓글 보기` : `${nickname}님의 활동을 보려면 로그인`}>{nickname}</Link>;
}
