import type { MemberUser } from "./api/auth";
export type { MemberUser } from "./api/auth";
export type MemberSnapshot = { status: "anonymous" | "authenticated" | "unavailable"; user: MemberUser | null };
export const normalizeMemberEmail = (email: string) => email.trim().toLowerCase();
export const isCurrentMember = (user: Pick<MemberUser, "id"> | null, expectedId: number) => user?.id === expectedId;
const refreshKey = "kbo_refresh";
let accessToken: string | null = null;
let authGeneration = 0;
let refreshing: { generation: number; promise: Promise<boolean> } | null = null;

export function saveMemberTokens(access: string, refresh: string) {
  authGeneration += 1;
  accessToken = access;
  sessionStorage.setItem(refreshKey, refresh);
}

export function clearMemberTokens() {
  authGeneration += 1;
  accessToken = null;
  sessionStorage.removeItem(refreshKey);
}

async function refreshMemberAccess() {
  const generation = authGeneration;
  if (refreshing?.generation === generation) return refreshing.promise;
  const refresh = sessionStorage.getItem(refreshKey);
  if (!refresh) return false;
  const promise = fetch("/api/v1/auth/token/refresh/", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh }), signal: AbortSignal.timeout(15000) })
    .then(async response => {
      const tokens = await response.json().catch(() => null);
      if (generation !== authGeneration) return accessToken !== null;
      if (!response.ok || typeof tokens?.access !== "string") { clearMemberTokens(); return false; }
      saveMemberTokens(tokens.access, typeof tokens.refresh === "string" ? tokens.refresh : refresh);
      return true;
    })
    .catch(() => false)
    .finally(() => { if (refreshing?.promise === promise) refreshing = null; });
  refreshing = { generation, promise };
  return promise;
}

export async function memberFetch(path: string, init: RequestInit = {}) {
  if (!accessToken) await refreshMemberAccess();
  if (!accessToken) return Response.json({ detail: "인증이 필요합니다." }, { status: 401 });
  const attemptedAccess = accessToken;
  const invoke = (token: string) => {
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${token}`);
    return fetch(path, { ...init, headers });
  };
  const response = await invoke(attemptedAccess);
  if (response.status !== 401) return response;
  if (accessToken === attemptedAccess) {
    accessToken = null;
  }
  if (!accessToken) await refreshMemberAccess();
  return accessToken ? invoke(accessToken) : response;
}

export async function logoutMember() {
  const refresh = sessionStorage.getItem(refreshKey);
  clearMemberTokens();
  return refresh
    ? fetch("/api/v1/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh }) })
    : Response.json({ detail: "로그인 정보가 없습니다." }, { status: 401 });
}

export function memberError(value: unknown, fallback: string) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fallback;
  const detail = Object.values(value).flat().find(item => typeof item === "string");
  return typeof detail === "string" ? detail : fallback;
}

export function createMemberRequestGate(timeoutMs = 10000) {
  let generation = 0;
  let active: AbortController | null = null;
  return {
    start() {
      active?.abort();
      const controller = new AbortController(), current = ++generation;
      let timedOut = false;
      active = controller;
      const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
      return { signal: controller.signal, current: () => current === generation, timedOut: () => timedOut, done: () => clearTimeout(timer) };
    },
    invalidate() { generation += 1; active?.abort(); active = null; },
  };
}

export async function loadLatestMember(gate: ReturnType<typeof createMemberRequestGate>, fetcher: (signal: AbortSignal) => Promise<Response> = signal => memberFetch("/api/v1/auth/user", { cache: "no-store", signal })) {
  const request = gate.start();
  try {
    const response = await fetcher(request.signal);
    if (!request.current()) return null;
    if (request.timedOut() || !response.ok) return { status: response.status === 401 ? "anonymous" : "unavailable", user: null } satisfies MemberSnapshot;
    const user = await response.json();
    return request.current() && !request.timedOut() ? { status: "authenticated", user } satisfies MemberSnapshot : null;
  } catch {
    return request.current() ? { status: "unavailable", user: null } satisfies MemberSnapshot : null;
  } finally { request.done(); }
}
