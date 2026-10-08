"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useMemberAuth } from "@/lib/member-auth";
import { logoutMember, memberError } from "@/lib/member-auth-request";
export function MemberHeaderActions() {
  const { status, user, setUser } = useMemberAuth();
  const router = useRouter();
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { setOpen(false); trigger.current?.focus(); } };
    document.addEventListener("pointerdown", outside); document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);
  return <>
    {status === "loading" ? <span role="status">회원 확인 중…</span> : user ? <>
    <div className="member-menu" ref={root}>
      <button ref={trigger} type="button" className="button button-primary header-signup" aria-expanded={open} aria-controls="member-menu-panel" onClick={() => setOpen(!open)}>마이페이지</button>
      {open && <nav id="member-menu-panel" className="member-menu-panel" aria-label="마이페이지 메뉴">
        <Link href="/mypage?tab=courses" onClick={() => setOpen(false)}>내 코스</Link>
        <Link href="/mypage?tab=likes" onClick={() => setOpen(false)}>찜한 코스</Link>
        <Link href="/mypage?tab=posts" onClick={() => setOpen(false)}>내가 쓴 글</Link>
        <hr className="member-menu-divider" />
        {status === "authenticated" && user.is_superuser === true && <>
          {status === "authenticated" && user.is_superuser === true && <Link href="/mypage?tab=feedback" onClick={() => setOpen(false)}>챗봇 답변 평가</Link>}
          <hr className="member-menu-divider" />
        </>}
        <Link href="/mypage?tab=profile" onClick={() => setOpen(false)}>회원 정보</Link>
        <hr className="member-menu-divider" />
        <button type="button" onClick={async () => { setOpen(false); setError(""); try { const response = await logoutMember(); const result = await response.json().catch(() => ({})); setUser(null); router.push("/"); if (!response.ok && response.status !== 401) setError(memberError(result, "서버의 로그아웃 여부를 확인하지 못했어요.")); } catch { setUser(null); router.push("/"); setError("서버의 로그아웃 여부를 확인하지 못했어요."); } }}>로그아웃</button>
      </nav>}
    </div></> : <><Link className="login-link" href="/login">로그인</Link><Link className="button button-primary header-signup" href="/signup">회원가입</Link>{status === "unavailable" && <span role="status">회원 서버 확인 필요</span>}</>}
    {error && <span role="alert">{error}</span>}
  </>;
}
