"use client";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { useMemberAuth, type MemberUser } from "@/lib/member-auth";
import { clearMemberTokens, normalizeMemberEmail } from "@/lib/member-auth-request";
import { requestEmailChange, updatePassword, verifyEmailChange } from "@/lib/api/auth";
import styles from "@/app/mypage/page.module.css";

export function MemberAccountSettings({ user, onChanged }: { user: MemberUser; onChanged: (user: MemberUser) => void }) {
  return <div className={styles.accountSettings}>
      <EmailChangeButton user={user} onChanged={onChanged} />
      <fieldset><legend>내 활동 공개 범위</legend>
        <label><input type="checkbox" name="public-courses" defaultChecked={user.visibility.courses} />내 코스 공개</label>
        <label><input type="checkbox" name="public-posts" defaultChecked={user.visibility.posts} />내 글·댓글 활동 목록 공개</label>
        <label><input type="checkbox" name="public-likes" defaultChecked={user.visibility.likes} />좋아요, 추천 목록 공개</label>
      </fieldset>
      <fieldset><legend>알림 설정</legend>
        <label><input type="checkbox" name="notify-comments" defaultChecked={user.notifications.comments} />내 글의 댓글·답글</label>
        <label><input type="checkbox" name="notify-courses" defaultChecked={user.notifications.courses} />내 코스의 반응</label>
        <label><input type="checkbox" name="notify-announcements" defaultChecked={user.notifications.announcements} />공지·회원 소식</label>
      </fieldset>
      <p className={styles.settingNote}>글·댓글 활동 목록의 공개 설정은 멤버 활동 화면에 적용돼요. 본인은 비공개 상태에서도 조회할 수 있어요. 알림 발송과 다른 항목의 공개 정책 연결은 별도 작업이에요.</p>

  </div>;
}

export function PasswordChangeButton() {
  const router = useRouter();
  const { setUser } = useMemberAuth();
  const [open, setOpen] = useState(false);
  const [passwordMessage, setPasswordMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal();
  }, [open]);
  return <>
    <div className={styles.passwordRow}><span>비밀번호</span><button type="button" className="button button-secondary" onClick={() => { setPasswordMessage(""); setOpen(true); }}>변경</button></div>
    {open && createPortal(<dialog ref={dialog} className={`auth-dialog ${styles.passwordDialog}`} onCancel={event => { if (busy) event.preventDefault(); else setOpen(false); }} onClose={() => { if (!busy) setOpen(false); }} aria-labelledby="password-dialog-title">
      <div className="auth-dialog-heading"><h2 id="password-dialog-title">비밀번호 변경</h2><button type="button" disabled={busy} onClick={() => setOpen(false)} aria-label="비밀번호 변경 닫기">×</button></div>
    <form onSubmit={async event => {
      event.preventDefault(); event.stopPropagation(); if (busy) return;
      const form = event.currentTarget, values = new FormData(form);
      const currentPassword = String(values.get("currentPassword") ?? ""), newPassword = String(values.get("newPassword") ?? "");
      if (newPassword !== values.get("confirmPassword")) { setPasswordMessage("새 비밀번호 확인이 일치하지 않아요."); return; }
      setBusy(true); setPasswordMessage("");
      try {
        await updatePassword({ current_password: currentPassword, new_password: newPassword, new_password_confirm: String(values.get("confirmPassword") ?? "") }, true, AbortSignal.timeout(15000));
        clearMemberTokens();
        setUser(null); form.reset(); setPasswordMessage("비밀번호를 변경했어요. 다시 로그인해 주세요.");
        window.setTimeout(() => router.push("/login"), 500);
      } catch (error) { setPasswordMessage(error instanceof Error ? error.message : "비밀번호를 변경하지 못했어요."); }
      finally { setBusy(false); }
    }}>
      <label>현재 비밀번호<input name="currentPassword" type="password" autoComplete="current-password" maxLength={128} required disabled={busy} /></label>
      <label>새 비밀번호<input name="newPassword" type="password" autoComplete="new-password" minLength={8} maxLength={128} required disabled={busy} /></label>
      <label>새 비밀번호 확인<input name="confirmPassword" type="password" autoComplete="new-password" minLength={8} maxLength={128} required disabled={busy} /></label>
      <p className={styles.settingNote}>영문과 숫자를 포함한 8~128자, 공백 제외. 변경 후 다시 로그인해야 해요.</p>
      <button className="button button-primary" type="submit" disabled={busy}>{busy ? "변경 중…" : "변경 완료"}</button>{passwordMessage && <p role="status">{passwordMessage}</p>}
    </form>
    </dialog>, document.body)}
  </>;
}

function EmailChangeButton({ user, onChanged }: { user: MemberUser; onChanged: (user: MemberUser) => void }) {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [address, setAddress] = useState("");
  const [code, setCode] = useState("");
  const [requestId, setRequestId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (open && !dialog.current?.open) dialog.current?.showModal(); }, [open]);
  async function submit(action: "send" | "verify") {
    if (busy) return;
    const normalizedAddress = normalizeMemberEmail(address);
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedAddress)) { setMessage("새 이메일 주소를 확인해 주세요."); return; }
    if (action === "verify" && (!requestId || !/^\d{6}$/.test(code))) { setMessage("메일로 받은 6자리 인증 코드를 입력해 주세요."); return; }
    setBusy(true); setMessage("");
    if (action === "send") { setRequestId(null); setCode(""); }
    try {
      if (action === "send") {
        const result = await requestEmailChange({ email: normalizedAddress }, AbortSignal.timeout(15000));
        if (!result) throw new Error("이메일 인증 요청을 확인하지 못했어요.");
        if (typeof result.request_id !== "string" || !result.request_id) throw new Error("인증 요청을 확인하지 못했어요.");
        setRequestId(result.request_id); setMessage("새 이메일로 인증 코드를 보냈어요. 받은 코드를 입력해 주세요.");
      } else {
        const result = await verifyEmailChange({ request_id: requestId ?? "", code }, AbortSignal.timeout(15000));
        if (!result) throw new Error("이메일 인증 결과를 확인하지 못했어요.");
        if (result.verified !== true || typeof result.email !== "string" || normalizeMemberEmail(result.email) !== normalizedAddress) throw new Error("이메일 인증 결과를 확인하지 못했어요.");
        onChanged({ ...user, email: result.email });
        setOpen(false);
      }
    } catch (error) { setMessage(error instanceof DOMException && error.name === "TimeoutError" ? "요청 결과를 확인하지 못했어요. 새로고침해 현재 이메일을 확인해 주세요." : error instanceof Error ? error.message : "이메일 인증을 진행하지 못했어요."); }
    finally { setBusy(false); }
  }
  return <>
    <div className={styles.passwordRow}><span>이메일{user.email && <small className={styles.emailValue}>{user.email}</small>}</span><button type="button" className="button button-secondary" onClick={() => { setMessage(""); setAddress(""); setCode(""); setRequestId(null); setOpen(true); }}>변경</button></div>
    {open && createPortal(<dialog ref={dialog} className={`auth-dialog ${styles.passwordDialog}`} onCancel={event => { if (busy) event.preventDefault(); else setOpen(false); }} onClose={() => { if (!busy) setOpen(false); }} aria-labelledby="email-dialog-title">
      <div className="auth-dialog-heading"><h2 id="email-dialog-title">이메일 변경</h2><button type="button" disabled={busy} onClick={() => setOpen(false)} aria-label="이메일 변경 닫기">×</button></div>
      <form onSubmit={event => { event.preventDefault(); event.stopPropagation(); void submit(requestId ? "verify" : "send"); }}>
        <label>새 이메일 주소<input name="newEmail" type="email" required maxLength={254} autoComplete="email" placeholder="이메일 주소를 입력해 주세요" value={address} disabled={busy} onChange={event => { setAddress(event.target.value); setRequestId(null); setCode(""); setMessage(""); }} /></label>
        <button type="button" className="button button-secondary" disabled={busy} onClick={() => void submit("send")}>{requestId ? "인증 코드 다시 받기" : "인증 코드 받기"}</button>
        <label>인증 코드<input name="verificationCode" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} placeholder="메일로 받은 6자리 코드" value={code} disabled={busy || !requestId} onChange={event => setCode(event.target.value.replace(/[^0-9]/g, ""))} /></label>
        <p className={styles.settingNote}>변경할 이메일로 받은 코드를 확인해야 이메일이 변경돼요.</p>
        <button type="submit" className="button button-primary" disabled={busy || !requestId || code.length !== 6}>{busy ? "처리 중…" : "인증하고 이메일 변경"}</button>
        {message && <p role="status">{message}</p>}
      </form>
    </dialog>, document.body)}
  </>;
}

const NICKNAME_PATTERN = /^[A-Za-z가-힣]{1,12}$/;

// Nickname lives outside the profile form: Enter never saves it, and a change needs an explicit confirmation.
export function NicknameChangeButton({ nickname, locked, onSave }: { nickname: string; locked: boolean; onSave: (nickname: string) => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (open && !dialog.current?.open) dialog.current?.showModal(); }, [open]);
  const start = () => { if (locked) return; setValue(nickname); setMessage(""); setOpen(true); };
  async function confirm() {
    if (busy) return;
    const next = value.trim();
    if (!NICKNAME_PATTERN.test(next)) { setMessage("닉네임은 한글·영문만 1~12자로 입력해 주세요."); return; }
    if (next === nickname) { setMessage("현재 닉네임과 같아요."); return; }
    setBusy(true); setMessage("");
    try { await onSave(next); setOpen(false); }
    catch (error) { setMessage(error instanceof Error ? error.message : "닉네임을 변경하지 못했어요."); }
    finally { setBusy(false); }
  }
  return <>
    <div className={styles.passwordRow}>
      <span>닉네임</span>
      <button type="button" className={styles.nicknameBox} disabled={locked} onClick={start} aria-label={`닉네임 ${nickname} 변경하기`}>{nickname}</button>
      <button type="button" className="button button-secondary" disabled={locked} onClick={start}>변경</button>
    </div>
    {open && createPortal(<dialog ref={dialog} className={`auth-dialog ${styles.passwordDialog}`} onCancel={event => { if (busy) event.preventDefault(); else setOpen(false); }} onClose={() => { if (!busy) setOpen(false); }} aria-labelledby="nickname-dialog-title" aria-describedby="nickname-dialog-question">
      <div className="auth-dialog-heading"><h2 id="nickname-dialog-title">닉네임 변경</h2><button type="button" disabled={busy} onClick={() => setOpen(false)} aria-label="닉네임 변경 닫기">×</button></div>
      <form onSubmit={event => { event.preventDefault(); event.stopPropagation(); }}>
        <label>새 닉네임<input name="newNickname" value={value} maxLength={12} autoComplete="nickname" disabled={busy} onChange={event => { setValue(event.target.value); setMessage(""); }} onKeyDown={event => { if (event.key === "Enter") event.preventDefault(); }} /></label>
        <p className={styles.settingNote}>한글·영문만 최대 12자. 변경 후 6개월 동안 다시 바꿀 수 없어요.</p>
        <p id="nickname-dialog-question"><strong>닉네임을 변경하시겠습니까?</strong></p>
        <div className={styles.dialogActions}>
          <button type="button" className="button button-secondary" disabled={busy} onClick={() => setOpen(false)}>취소</button>
          <button type="button" className="button button-primary" disabled={busy} onClick={() => void confirm()}>{busy ? "변경 중…" : "확인"}</button>
        </div>
        {message && <p role="status">{message}</p>}
      </form>
    </dialog>, document.body)}
  </>;
}
