"use client";

import { safeMemberReturnPath } from "@/lib/member-return-path";
import { MemberAuthSwitchLink } from "@/components/member-auth-switch-link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useRef, useState } from "react";
import { AuthDialog } from "@/components/auth-dialog";
import { useAuthHydrated } from "@/components/auth-hydration";
import { signUp } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { teamBoards } from "@/lib/team-community";

type FieldName = "username" | "password" | "passwordConfirm" | "name" | "birthDate" | "gender" | "email" | "teamCode";
type SignupValues = Record<FieldName, string>;
type Agreement = "service" | "privacy" | "marketing";
const fieldOrder: FieldName[] = ["username", "password", "passwordConfirm", "name", "birthDate", "gender", "email", "teamCode"];
const fieldIds: Record<FieldName, string> = { username: "signup-id", password: "signup-password", passwordConfirm: "signup-password-confirm", name: "signup-name", birthDate: "signup-birth-date", gender: "signup-gender", email: "signup-email", teamCode: "signup-team-code" };
const policyContent: Record<Agreement, { title: string; description: string }> = {
  service: { title: "서비스 이용약관", description: "정식 서비스의 이용 조건, 회원의 권리와 의무, 게시물 운영 기준이 이곳에 안내될 예정이에요." },
  privacy: { title: "개인정보 수집·이용 안내", description: "수집 항목, 이용 목적, 보유 기간과 동의 거부에 관한 내용을 정식 서비스 시작 전에 안내할 예정이에요." },
  marketing: { title: "마케팅 정보 수신 안내", description: "이벤트와 서비스 소식의 수신 여부를 선택하는 항목이에요. 동의하지 않아도 회원가입할 수 있어요. 구체적인 수신 채널과 철회 방법은 서비스 시작 전에 안내할 예정이에요." },
};

function validate(values: SignupValues): Partial<Record<FieldName, string>> {
  const errors: Partial<Record<FieldName, string>> = {};
  if (!/^[A-Za-z0-9]{4,20}$/.test(values.username)) errors.username = "영문과 숫자로 4~20자를 입력해 주세요.";
  if (values.password.length < 8 || values.password.length > 128 || !/[0-9]/.test(values.password) || !/[A-Za-z]/.test(values.password) || /\s/.test(values.password)) errors.password = "영문과 숫자를 포함한 8~128자, 공백 없이 입력해 주세요.";
  if (!values.passwordConfirm) errors.passwordConfirm = "비밀번호를 한 번 더 입력해 주세요.";
  else if (values.passwordConfirm !== values.password) errors.passwordConfirm = "비밀번호가 서로 달라요. 다시 확인해 주세요.";
  if (!values.name.trim()) errors.name = "이름을 입력해 주세요.";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(values.birthDate) || !Number.isFinite(Date.parse(`${values.birthDate}T00:00:00Z`))) errors.birthDate = "생년월일을 입력해 주세요.";
  if (!['M', 'F'].includes(values.gender)) errors.gender = "성별을 선택해 주세요.";
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(values.email.trim())) errors.email = "올바른 이메일 주소를 입력해 주세요.";
  if (values.teamCode !== "" && !teamBoards.some(team => team.code === values.teamCode)) {
    errors.teamCode = "목록에 있는 응원팀을 선택해 주세요.";
  }
  return errors;
}

export default function SignupPage() {
  const router = useRouter();
  const hydrated = useAuthHydrated();
  const [values, setValues] = useState<SignupValues>({ username: "", password: "", passwordConfirm: "", name: "", birthDate: "", gender: "", email: "", teamCode: "" });
  const [touched, setTouched] = useState<Partial<Record<FieldName, boolean>>>({});
  const [message, setMessage] = useState("");
  const [visible, setVisible] = useState(false);
  const [agreements, setAgreements] = useState({ service: false, privacy: false, marketing: false });
  const [policy, setPolicy] = useState<Agreement | null>(null);
  const [busy, setBusy] = useState(false);
  const allAgreementRef = useRef<HTMLInputElement>(null);
  const feedbackRef = useRef<HTMLParagraphElement>(null);
  const errors = validate(values);
  const requiredAgreed = agreements.service && agreements.privacy;
  const allAgreed = requiredAgreed && agreements.marketing;
  const someAgreed = Object.values(agreements).some(Boolean);

  useEffect(() => {
    if (allAgreementRef.current) allAgreementRef.current.indeterminate = someAgreed && !allAgreed;
  }, [someAgreed, allAgreed]);

  function fieldProps(field: FieldName) {
    return {
      id: fieldIds[field],
      name: field,
      value: values[field],
      required: true,
      disabled: !hydrated,
      "aria-invalid": Boolean(touched[field] && errors[field]),
      "aria-describedby": `${fieldIds[field]}-hint`,
      onChange: (event: React.ChangeEvent<HTMLInputElement>) => { setValues((current) => ({ ...current, [field]: event.target.value })); setMessage(""); },
      onBlur: () => setTouched((current) => ({ ...current, [field]: true })),
    };
  }

  function hint(field: FieldName, text: string) {
    const error = touched[field] && errors[field];
    return <p id={`${fieldIds[field]}-hint`} className={error ? "auth-error" : "auth-field-hint"} aria-live="polite">{error || text}</p>;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setTouched({ username: true, password: true, passwordConfirm: true, name: true, birthDate: true, gender: true, email: true, teamCode: true });
    const firstInvalid = fieldOrder.find((field) => errors[field]);
    if (firstInvalid) {
      setMessage("");
      event.currentTarget.querySelector<HTMLInputElement | HTMLSelectElement>(`#${fieldIds[firstInvalid]}`)?.focus();
      return;
    }
    if (!requiredAgreed) return;
    setBusy(true); setMessage("");
    try {
      await signUp({ username: values.username, password: values.password, re_password: values.passwordConfirm, first_name: values.name.trim(), birth_date: values.birthDate, gender: values.gender as "M" | "F", email: values.email.trim(), team_code: values.teamCode }, AbortSignal.timeout(40000));
      const search = new URLSearchParams(window.location.search);
      const next = safeMemberReturnPath(search.get("next"));
      const query = new URLSearchParams({ registered: "1" });
      if (next) query.set("next", next);
      else if (search.get("next") === "admin") query.set("next", "admin");
      router.push(`/login?${query}`);
      return;
    } catch (error) {
      const mapping: Record<string, FieldName> = { username: "username", password: "password", re_password: "passwordConfirm", first_name: "name", birth_date: "birthDate", gender: "gender", email: "email", team_code: "teamCode" };
      const first = Object.keys(error instanceof ApiError ? error.fields ?? {} : {}).map(field => mapping[field]).find(Boolean);
      if (first) { setTouched(current => ({ ...current, [first]: true })); document.getElementById(fieldIds[first])?.focus(); }
      setMessage(error instanceof DOMException && error.name === "TimeoutError" ? "요청 결과를 확인하지 못했어요. 자동으로 다시 제출하지 말고 로그인 또는 아이디 찾기로 계정 생성 여부를 확인해 주세요." : error instanceof Error ? error.message : "회원가입 서버에 연결하지 못했어요.");
    } finally { setBusy(false); }
    requestAnimationFrame(() => feedbackRef.current?.focus());
  }

  return (
    <main className="auth-page auth-signup-page">
      <div className="auth-decoration" aria-hidden="true"><span /><span /><span /></div>
      <section className="auth-panel auth-signup-panel" aria-labelledby="signup-title">
        <p className="eyebrow">YOUR FIRST PITCH</p>
        <h1 id="signup-title">회원가입</h1>
        <p className="auth-description">좋아하는 야구에, 나만의 하루를 더해 보세요.</p>
        {message && <p ref={feedbackRef} tabIndex={-1} className="auth-feedback auth-feedback-top" role="alert">{message}</p>}
        <form onSubmit={submit} method="post" className="auth-form" noValidate>
          <div className="auth-field">
            <label htmlFor="signup-id">아이디 <span>필수</span></label>
            <input {...fieldProps("username")} autoComplete="username" placeholder="영문, 숫자 4~20자" minLength={4} maxLength={20} />
            {hint("username", "영문과 숫자를 사용할 수 있어요. 중복 아이디는 가입 요청 시 바로 안내해요.")}
          </div>
          <div className="auth-field">
            <label htmlFor="signup-password">비밀번호 <span>필수</span></label>
            <div className="auth-password">
              <input {...fieldProps("password")} type={visible ? "text" : "password"} autoComplete="new-password" placeholder="8자 이상 · 숫자·특수문자 포함" minLength={8} maxLength={128} />
              <button type="button" onClick={() => setVisible(!visible)} aria-label={visible ? "비밀번호 숨기기" : "비밀번호 보기"} aria-pressed={visible}>{visible ? "숨기기" : "보기"}</button>
            </div>
            {hint("password", "영문과 숫자를 포함한 8~128자, 공백 없이 입력해 주세요.")}
          </div>
          <div className="auth-field">
            <label htmlFor="signup-password-confirm">비밀번호 확인 <span>필수</span></label>
            <input {...fieldProps("passwordConfirm")} type={visible ? "text" : "password"} autoComplete="new-password" placeholder="비밀번호를 한 번 더 입력해 주세요" maxLength={128} />
            {hint("passwordConfirm", "위에서 입력한 비밀번호를 한 번 더 입력해 주세요.")}
          </div>
          <div className="auth-field">
            <label htmlFor="signup-name">이름 <span>필수</span></label>
            <input {...fieldProps("name")} autoComplete="name" placeholder="이름" maxLength={40} />
            {hint("name", "사용할 이름을 입력해 주세요.")}
          </div>
          <div className="auth-field"><label htmlFor="signup-birth-date">생년월일 <span>필수</span></label><input {...fieldProps("birthDate")} type="date" autoComplete="bday" />{hint("birthDate", "주민등록번호는 받지 않으며 생년월일만 저장해요.")}</div>
          <div className="auth-field"><label htmlFor="signup-gender">성별 <span>필수</span></label><select id="signup-gender" name="gender" required disabled={!hydrated} value={values.gender} aria-invalid={Boolean(touched.gender && errors.gender)} aria-describedby="signup-gender-hint" onChange={event => { setValues(current => ({ ...current, gender: event.target.value })); setMessage(""); }} onBlur={() => setTouched(current => ({ ...current, gender: true }))}><option value="">선택해 주세요</option><option value="M">남성</option><option value="F">여성</option></select>{hint("gender", "가입에 필요한 성별 값만 저장해요.")}</div>
          <div className="auth-field">
            <label htmlFor="signup-email">이메일 <span>필수</span></label>
            <input {...fieldProps("email")} type="email" autoComplete="email" placeholder="hello@example.com" maxLength={254} />
            {hint("email", "계정 안내를 받을 이메일을 입력해 주세요.")}
          </div>
          <div className="auth-field">
            <label htmlFor="signup-team-code">응원팀 / 홈구장 <span>선택</span></label>
            <select
              id={fieldIds.teamCode}
              name="teamCode"
              value={values.teamCode}
              disabled={!hydrated || busy}
              aria-invalid={Boolean(touched.teamCode && errors.teamCode)}
              aria-describedby={`${fieldIds.teamCode}-hint`}
              onChange={(event) => {
                const teamCode = event.target.value;
                setValues(current => ({ ...current, teamCode }));
                setMessage("");
              }}
              onBlur={() => setTouched(current => ({ ...current, teamCode: true }))}
            >
              <option value="">선택 안 함</option>
              {teamBoards.map(team => (
                <option key={team.code} value={team.code}>{team.name} · {team.stadium}</option>
              ))}
            </select>
            {hint("teamCode", "응원팀과 홈구장을 선택해 주세요. 선택하지 않아도 가입할 수 있으며, 가입 후 마이페이지에서 변경할 수 있어요.")}
          </div>
          <fieldset className="auth-agreements">
            <legend className="sr-only">정책 동의</legend>
            <label className="auth-check auth-check-all"><input ref={allAgreementRef} type="checkbox" checked={allAgreed} disabled={!hydrated} onChange={(event) => setAgreements({ service: event.target.checked, privacy: event.target.checked, marketing: event.target.checked })} /><span>전체 동의 <small>선택 항목 포함</small></span></label>
            {(["service", "privacy", "marketing"] as const).map((agreement) => (
              <div className="auth-agreement-item" key={agreement}>
                <label className="auth-check">
                  <input type="checkbox" required={agreement !== "marketing"} checked={agreements[agreement]} disabled={!hydrated} onChange={(event) => { setAgreements((current) => ({ ...current, [agreement]: event.target.checked })); setMessage(""); }} />
                  <span><b>{agreement === "marketing" ? "[선택]" : "[필수]"}</b> {agreement === "service" ? "서비스 이용약관 동의" : agreement === "privacy" ? "개인정보 수집·이용 동의" : "마케팅 정보 수신 동의"}</span>
                </label>
                <button type="button" className="auth-policy-link" onClick={() => setPolicy(agreement)} aria-label={`${policyContent[agreement].title} 보기`}>보기</button>
              </div>
            ))}
          </fieldset>
          <p className="auth-service-note">약관은 화면 확인용 초안이며, 현재 동의 내용은 기록하지 않아요.</p>
          <button className="button button-primary auth-submit" type="submit" disabled={!hydrated || !requiredAgreed || busy} aria-describedby="signup-submit-hint">{busy ? "가입 요청 중…" : "회원가입"}</button>
          <p id="signup-submit-hint" className="auth-submit-hint">{requiredAgreed ? "선택 항목에 동의하지 않아도 가입할 수 있어요." : "필수 약관 두 가지에 동의하면 가입 버튼이 활성화돼요."}</p>
        </form>
        <p className="auth-switch">이미 계정이 있으신가요? <MemberAuthSwitchLink to="/login" label="로그인" /></p>
      </section>
      <AuthDialog open={policy !== null} title={policy ? policyContent[policy].title : "정책 안내"} onClose={() => setPolicy(null)}>
        <span className="auth-draft-label">화면 확인용 초안</span>
        <p>{policy && policyContent[policy].description}</p>
        <p>정식 약관은 서비스 시작 전에 제공돼요. 이 화면에서는 개인정보를 수집하거나 동의 내용을 저장하지 않아요.</p>
      </AuthDialog>
    </main>
  );
}
