"use client";

import Link from "next/link";
import { AdminFeedbackPanel } from "@/components/admin-feedback-panel";
import styles from "./page.module.css";

export default function FeedbackAdminPage() {
  return <main className={`container ${styles.page}`}>
    <Link href="/admin" className={styles.back}>← 관리자 대시보드</Link>
    <header className={styles.header}><h1>챗봇 답변 평가 검토</h1><p>사용자가 남긴 평가와 당시의 질문·답변을 함께 확인하세요.</p></header>
    <AdminFeedbackPanel />
  </main>;
}
