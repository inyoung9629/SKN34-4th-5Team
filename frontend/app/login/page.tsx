"use client";

import Link from "next/link";
import { safeMemberReturnPath } from "@/lib/member-return-path";
import { MemberAuthSwitchLink } from "@/components/member-auth-switch-link";
import { saveMemberTokens } from "@/lib/member-auth-request";
import { FormEvent, useEffect, useRef, useState } from "react";
import { AuthDialog } from "@/components/auth-dialog";
import { useAuthHydrated } from "@/components/auth-hydration";
import { getMemberUser, requestPasswordReset, requestUsername, signIn, updatePassword } from "@/lib/api/auth";

type LoginErrors = { username?: string; password?: string };

export default function LoginPage() {
  const [busy, setBusy] = useState(false);
  const hydrated = useAuthHydrated();
  const [message, setMessage] = useState("");
  const [errors, setErrors] = useState<LoginErrors>({});
  const [visible, setVisible] = useState(false);
  const [help, setHelp] = useState<"id" | "password" | "reset" | null>(null);
  const [reset, setReset] = useState<{ uid: string; token: string } | null>(null);
  const [helpMessage, setHelpMessage] = useState("");
  const [helpBusy, setHelpBusy] = useState(false);
  const feedbackRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.hash.slice(1));
    const uid = params.get("uid"), token = params.get("token");
    const timer = window.setTimeout(() => {
      if (uid && token) { setReset({ uid, token }); setHelp("reset"); history.replaceState(null, "", "/login"); }
      else if (new URLSearchParams(window.location.search).get("registered") === "1") setMessage("회원가입이 완료됐어요. 새 계정으로 로그인해 주세요.");
    });
    return () => window.clearTimeout(timer);
  }, []);

  function closeHelp() {
    if (help === "reset") { history.replaceState(null, "", "/login"); setReset(null); }
    setHelp(null); setHelpMessage("");
  }

  async function submitHelp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (helpBusy || !help) return;
    const form = event.currentTarget, values = new FormData(form);
    const newPassword = String(values.get("newPassword") ?? ""), confirmation = String(values.get("confirmPassword") ?? "");
    const email = String(values.get("email") ?? "").trim();
    if (help === "reset" && newPassword !== confirmation) { setHelpMessage("새 비밀번호 확인이 일치하지 않아요."); return; }
    setHelpBusy(true); setHelpMessage("");
    try {
      const signal = AbortSignal.timeout(40000);
      if (help === "id") await requestUsername({ email }, signal);
      else if (help === "password") await requestPasswordReset({ email }, signal);
      else await updatePassword({ uid: reset?.uid ?? "", token: reset?.token ?? "", new_password: newPassword, new_password_confirm: confirmation }, false, signal);
      if (help === "reset") { history.replaceState(null, "", "/login"); setReset(null); setHelp(null); setMessage("비밀번호를 재설정했어요. 새 비밀번호로 로그인해 주세요."); }
      else setHelpMessage(help === "id" ? "계정이 확인되면 가입 이메일로 아이디를 보냈어요." : "계정이 확인되면 가입 이메일로 재설정 링크를 보냈어요.");
    } catch (error) { setHelpMessage(error instanceof DOMException && error.name === "TimeoutError" ? "요청 결과를 확인하지 못했어요. 메일함을 확인한 뒤 다시 요청해 주세요." : error instanceof Error ? error.message : "서버에 연결하지 못했어요."); }
    finally { setHelpBusy(false); }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const fields = new FormData(form);
    const nextErrors: LoginErrors = {};
    if (!String(fields.get("username") ?? "").trim()) nextErrors.username = "아이디를 입력해 주세요.";
    if (!String(fields.get("password") ?? "")) nextErrors.password = "비밀번호를 입력해 주세요.";
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) {
      setMessage("");
      form.querySelector<HTMLInputElement>(nextErrors.username ? "#login-id" : "#login-password")?.focus();
      return;
    }
    setBusy(true);
    try {
      const result = await signIn({ username: String(fields.get("username") ?? ""), password: String(fields.get("password") ?? "") });
      if (!result) throw new Error("로그인 응답을 확인하지 못했어요.");
      saveMemberTokens(result.access, result.refresh);
      // 관리자 계정은 관리 메뉴가 있는 마이페이지로, 그 외에는 메인으로 이동한다
      const me = await getMemberUser().catch(() => null);
      const rawNext = new URLSearchParams(window.location.search).get("next");
      const memberNext = safeMemberReturnPath(rawNext);
      const fallback = me?.is_staff || me?.is_superuser ? "/mypage" : "/";
      window.location.assign(memberNext ?? (rawNext === "admin" ? "/admin" : fallback));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "로그인 서버에 연결하지 못했어요.");
    } finally { setBusy(false); }
    requestAnimationFrame(() => feedbackRef.current?.focus());
  }

  return (
    <main className="auth-page">
      <div className="auth-decoration" aria-hidden="true"><span /><span /><span /></div>
      <section className="auth-panel" aria-labelledby="login-title">
        <Link href="/" className="auth-wordmark" aria-label="KBO ROUTE 홈으로">KBO<span>ROUTE</span></Link>
        <p className="eyebrow">WELCOME BACK</p>
        <h1 id="login-title">로그인</h1>
        <p className="auth-description">나만의 직관 코스, 이어서 만들어 볼까요?</p>
        {message && <p ref={feedbackRef} tabIndex={-1} className="auth-feedback auth-feedback-top" role="alert">{message}</p>}
        <button className="auth-kakao" type="button" disabled aria-describedby="kakao-login-status">
          <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path fill="currentColor" d="M12 3C6.48 3 2 6.4 2 10.6c0 2.7 1.84 5.07 4.62 6.42L5.44 21l4.62-2.91c.63.08 1.28.12 1.94.12 5.52 0 10-3.4 10-7.61S17.52 3 12 3Z" /></svg>
          카카오 로그인 준비 중
        </button>
        <p id="kakao-login-status" className="auth-service-note">현재 카카오 계정 로그인은 지원하지 않아요. 아이디 로그인을 이용해 주세요.</p>
        <div className="auth-divider"><span>또는 아이디로 로그인</span></div>
        <form onSubmit={submit} method="post" className="auth-form" noValidate>
          <div className="auth-field">
            <label htmlFor="login-id">아이디</label>
            <input id="login-id" name="username" autoComplete="username" placeholder="아이디를 입력해 주세요" required disabled={!hydrated} maxLength={40} aria-invalid={Boolean(errors.username)} aria-describedby={errors.username ? "login-id-error" : undefined} onChange={() => { setErrors((current) => ({ ...current, username: undefined })); setMessage(""); }} />
            {errors.username && <p className="auth-error" id="login-id-error">{errors.username}</p>}
          </div>
          <div className="auth-field">
            <label htmlFor="login-password">비밀번호</label>
            <div className="auth-password">
              <input id="login-password" name="password" type={visible ? "text" : "password"} autoComplete="current-password" placeholder="비밀번호를 입력해 주세요" required disabled={!hydrated} maxLength={128} aria-invalid={Boolean(errors.password)} aria-describedby={errors.password ? "login-password-error" : undefined} onChange={() => { setErrors((current) => ({ ...current, password: undefined })); setMessage(""); }} />
              <button type="button" onClick={() => setVisible(!visible)} aria-label={visible ? "비밀번호 숨기기" : "비밀번호 보기"} aria-pressed={visible}>{visible ? "숨기기" : "보기"}</button>
            </div>
            {errors.password && <p className="auth-error" id="login-password-error">{errors.password}</p>}
          </div>
          <button className="button button-primary auth-submit" type="submit" disabled={!hydrated || busy}>{busy ? "로그인 중…" : "로그인"}</button>
        </form>
        <nav className="auth-help-links" aria-label="계정 도움말">
          <button type="button" onClick={() => setHelp("id")}>아이디 찾기</button>
          <button type="button" onClick={() => setHelp("password")}>비밀번호 찾기</button>
          <MemberAuthSwitchLink to="/signup" label="회원가입" />
        </nav>
        <p className="auth-service-note auth-bottom-note">팀 계정으로 로그인하면 챗봇을 이용할 수 있어요.</p>
        <Link href="/routes" className="auth-browse">먼저 직관 코스 둘러보기 <span aria-hidden="true">↗</span></Link>
      </section>
      <AuthDialog open={help !== null} title={help === "reset" ? "비밀번호 재설정" : help === "password" ? "비밀번호 찾기" : "아이디 찾기"} onClose={closeHelp}>
        <form onSubmit={submitHelp} className="auth-form">
          {help === "reset" ? <><label>새 비밀번호<input name="newPassword" type="password" autoComplete="new-password" minLength={8} maxLength={128} required disabled={helpBusy} /></label><label>새 비밀번호 확인<input name="confirmPassword" type="password" autoComplete="new-password" minLength={8} maxLength={128} required disabled={helpBusy} /></label><p>영문과 숫자를 포함한 8~128자, 공백 없이 입력해 주세요.</p></> : <label>가입 이메일<input name="email" type="email" autoComplete="email" maxLength={254} required disabled={helpBusy} /></label>}
          <button className="button button-primary" type="submit" disabled={helpBusy}>{helpBusy ? "처리 중…" : help === "reset" ? "비밀번호 재설정" : "메일 보내기"}</button>
          {helpMessage && <p role="status">{helpMessage}</p>}
        </form>
      </AuthDialog>
    </main>
  );
}
