"use client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useMemberAuth } from "@/lib/member-auth";
import { logoutMember, memberError, memberFetch } from "@/lib/member-auth-request";
export function MemberHeaderActions() {
  const { status, user, setUser } = useMemberAuth();
  const router = useRouter();
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const [pointBalance, setPointBalance] = useState<number | null>(null);
  const [pointsLoading, setPointsLoading] = useState(false);
  const [pointsError, setPointsError] = useState("");
  const pointsRequest = useRef<AbortController | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const loadPointBalance = useCallback(() => {
    const controller = new AbortController();
    pointsRequest.current?.abort();
    pointsRequest.current = controller;
    setPointsLoading(true);
    setPointsError("");
    void memberFetch("/api/v1/fantasy/points/", {
      cache: "no-store",
      signal: controller.signal,
    }).then(async response => {
      if (!response.ok) throw new Error("보유 포인트를 불러오지 못했어요.");
      const result: unknown = await response.json();
      if (!result || typeof result !== "object" || !("balance" in result) || typeof result.balance !== "number") {
        throw new Error("보유 포인트 응답을 확인하지 못했어요.");
      }
      if (!controller.signal.aborted) setPointBalance(result.balance);
    }).catch(value => {
      if (controller.signal.aborted) return;
      setPointBalance(null);
      setPointsError(value instanceof Error ? value.message : "보유 포인트를 불러오지 못했어요.");
    }).finally(() => {
      if (!controller.signal.aborted) setPointsLoading(false);
    });
  }, []);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { setOpen(false); trigger.current?.focus(); } };
    document.addEventListener("pointerdown", outside); document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);
  useEffect(() => {
    if (!open || status !== "authenticated") return;
    window.addEventListener("fantasy-points-updated", loadPointBalance);
    return () => window.removeEventListener("fantasy-points-updated", loadPointBalance);
  }, [loadPointBalance, open, status]);
  useEffect(() => () => pointsRequest.current?.abort(), []);
  return <>
    {status === "loading" ? <span role="status">회원 확인 중…</span> : user ? <><div className="member-menu" ref={root}>
      <button ref={trigger} type="button" className="button button-primary header-signup" aria-expanded={open} aria-controls="member-menu-panel" onClick={() => {
        const nextOpen = !open;
        setOpen(nextOpen);
        if (nextOpen && status === "authenticated") loadPointBalance();
        else {
          pointsRequest.current?.abort();
          setPointsLoading(false);
        }
      }}>마이페이지</button>
      {open && <nav id="member-menu-panel" className="member-menu-panel" aria-label="마이페이지 메뉴">
        <div className="member-menu-balance" aria-live="polite">
          <span>보유 포인트</span>
          <strong>{pointsLoading ? "조회 중…" : pointBalance === null ? "확인 불가" : `${pointBalance.toLocaleString("ko-KR")}P`}</strong>
          {pointsError && <small role="alert">{pointsError}</small>}
        </div>
        <hr className="member-menu-divider" />
        <Link href="/mypage?tab=courses" onClick={() => setOpen(false)}>내 코스</Link>
        <Link href="/mypage?tab=likes" onClick={() => setOpen(false)}>찜한 코스</Link>
        <Link href="/mypage?tab=posts" onClick={() => setOpen(false)}>내가 쓴 글</Link>
        <hr className="member-menu-divider" />
        {status === "authenticated" && user.is_superuser === true && <>
          <Link href="/mypage?tab=feedback" onClick={() => setOpen(false)}>챗봇 답변 평가</Link>
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
