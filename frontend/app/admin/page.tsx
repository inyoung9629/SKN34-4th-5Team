"use client";

import Link from "next/link";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { useMemberAuth } from "@/lib/member-auth";
import { AdminMembersPanel, AdminPostsPanel, AdminReportsPanel } from "@/components/admin-panels";
import styles from "./page.module.css";

export function AdminDashboard() {
  const { status, user, reload } = useMemberAuth();
  const search = useSearchParams();
  const tabs = [
    { id: "members", title: "회원 관리", description: "회원 검색 · 계정 상태 및 권한 확인" },
    { id: "posts", title: "게시글 관리", description: "게시글 검색 · 신고 현황 확인 · 삭제" },
    { id: "reports", title: "신고 관리", description: "신고 검토 · 보류 · 숨김 · 계정 처분" },
  ];
  const selected = tabs.find(tab => tab.id === search.get("tab"))?.id ?? "members";
  if (status === "loading") return <main className={`container ${styles.page}`}><p role="status">관리자 권한을 확인하고 있어요.</p></main>;
  if (status === "unavailable") return <main className={`container ${styles.page}`}><h1>권한 확인 실패</h1><p role="alert">회원 서버에 연결하지 못했어요.</p><button onClick={() => void reload()}>다시 확인</button></main>;
  if (status !== "authenticated" || !user) return <main className={`container ${styles.page}`}><h1>관리자 로그인 필요</h1><Link href="/login?next=admin">로그인</Link></main>;
  if (!user.is_active || !user.is_staff) return <main className={`container ${styles.page}`}><h1>접근 권한이 없습니다</h1><p>관리자 계정만 이용할 수 있어요.</p><Link href="/">메인으로 돌아가기</Link></main>;
  return <main className={`container ${styles.page}`}>
    <header className={styles.dashboardHeader}><div><h1>관리자 대시보드</h1><p className={styles.intro}>회원과 커뮤니티 운영을 한곳에서 관리하세요.</p></div><Link href="/">사이트로 돌아가기 →</Link></header>
    <nav className={`${styles.cards} ${user.is_superuser ? styles.withFeedback : ""}`} aria-label="관리 메뉴">
      {tabs.map(tab => <Link key={tab.id} href={`/admin?tab=${tab.id}`} aria-current={selected === tab.id ? "page" : undefined} className={styles.card}><strong>{tab.title}</strong><span>{tab.description}</span></Link>)}
      {user.is_superuser && <Link href="/admin/feedback" className={styles.card}><strong>챗봇 답변 평가 검토</strong><span>사용자 평가 · 질문과 답변 확인</span></Link>}
    </nav>
    <div className={styles.tools}><Link href="/admin/baseball">야구 데이터 관리 →</Link></div>
    <div key={`${user.id}:${selected}`}>
      {selected === "posts" ? <AdminPostsPanel /> : selected === "reports" ? <AdminReportsPanel /> : <AdminMembersPanel />}
    </div>
  </main>;
}

export default function AdminPage() {
  return <Suspense fallback={<main className="container"><p role="status">대시보드를 불러오고 있어요.</p></main>}><AdminDashboard /></Suspense>;
}
