"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { createMemberRequestGate, isCurrentMember, loadLatestMember, type MemberUser } from "./member-auth-request";
import { resetRouteLikes } from "./routes";
export type { MemberUser } from "./member-auth-request";
type AuthState = { status: "loading" | "anonymous" | "authenticated" | "unavailable"; user: MemberUser | null; reload: () => Promise<void>; setUser: (user: MemberUser | null, expectedUserId?: number) => boolean };
const MemberAuthContext = createContext<AuthState | null>(null);

export function MemberAuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<Omit<AuthState, "reload" | "setUser">>({ status: "loading", user: null });
  const currentUser = useRef<MemberUser | null>(null);
  const gate = useMemo(() => createMemberRequestGate(), []);
  const reload = useCallback(async () => {
    const next = await loadLatestMember(gate);
    if (next) {
      resetRouteLikes(next.status === "authenticated" ? next.user?.id ?? null : null);
      currentUser.current = next.user; setState(next);
    }
  }, [gate]);
  useEffect(() => { void Promise.resolve().then(reload); return () => gate.invalidate(); }, [gate, reload]);
  const setUser = useCallback((user: MemberUser | null, expectedUserId?: number) => {
    if (user && expectedUserId !== undefined && !isCurrentMember(currentUser.current, expectedUserId)) return false;
    gate.invalidate(); resetRouteLikes(user?.id ?? null);
    currentUser.current = user; setState({ status: user ? "authenticated" : "anonymous", user }); return true;
  }, [gate]);
  return <MemberAuthContext value={{ ...state, reload, setUser }}>{children}</MemberAuthContext>;
}

export function useMemberAuth() {
  const value = useContext(MemberAuthContext);
  if (!value) throw new Error("MemberAuthProvider가 필요합니다.");
  return value;
}
