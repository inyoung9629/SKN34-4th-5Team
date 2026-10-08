"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useMemberAuth } from "@/lib/member-auth";
import { loadRouteLike, toggleRouteLike, useLikedRoutes, type TripRoute } from "@/lib/routes";

type Props = {
  route: TripRoute;
  children: (parts: { button: ReactNode; notice: ReactNode }) => ReactNode;
};

export function RouteLikeButton({ route, children }: Props) {
  const { status, user } = useMemberAuth();
  const authenticated = status === "authenticated";
  const userId = authenticated ? user?.id ?? null : null;
  const likedRoutes = useLikedRoutes();
  const liked = authenticated && likedRoutes.includes(route.id);
  const identityKey = `${userId ?? "guest"}:${route.id}`;
  const [readyKey, setReadyKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [retry, setRetry] = useState(0);
  const requestVersion = useRef(0);
  const sending = useRef(false);
  const ready = readyKey === identityKey;

  useEffect(() => {
    const version = ++requestVersion.current;
    let active = true;
    sending.current = false;
    queueMicrotask(() => {
      if (!active) return;
      setReadyKey(""); setBusy(false); setError(""); setFeedback("");
    });
    if (userId !== null && !route.legacy) {
      void loadRouteLike(route.id).then(
        () => { if (active && version === requestVersion.current) setReadyKey(identityKey); },
        caught => {
          if (active && version === requestVersion.current) {
            setError(caught instanceof Error ? caught.message : "좋아요 상태를 확인하지 못했어요.");
          }
        },
      );
    }
    return () => { active = false; requestVersion.current += 1; };
  }, [userId, route.id, route.legacy, identityKey, retry]);

  async function handleLike() {
    if (status === "anonymous") {
      setFeedback("좋아요는 로그인 후 사용할 수 있어요.");
      return;
    }
    if (!authenticated || !ready || route.legacy || sending.current) return;
    const version = requestVersion.current;
    sending.current = true;
    setBusy(true); setError(""); setFeedback("");
    try {
      const nextLiked = await toggleRouteLike(route.id);
      if (version === requestVersion.current) {
        setFeedback(nextLiked ? "좋아요를 남겼어요." : "좋아요를 취소했어요.");
      }
    } catch (caught) {
      if (version === requestVersion.current) {
        setError(caught instanceof Error ? caught.message : "좋아요를 저장하지 못했어요.");
      }
    } finally {
      if (version === requestVersion.current) { sending.current = false; setBusy(false); }
    }
  }

  const disabled = status === "loading" || status === "unavailable" ||
    Boolean(route.legacy) || busy || (authenticated && !ready);
  const button = (
    <button type="button" className={`route-card-like-button${liked ? " is-liked" : ""}`}
      aria-label={`${liked ? "좋아요 취소" : "좋아요"} · ${route.likes}개`}
      aria-pressed={liked} aria-busy={busy} disabled={disabled} onClick={() => void handleLike()}>
      <svg viewBox="0 0 24 24" fill={liked ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
        <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z" />
      </svg>
      <span>{route.likes}</span>
    </button>
  );
  const notice = <>
    {route.legacy && <p className="route-card-like-notice">서버에 저장된 코스만 좋아요를 남길 수 있어요.</p>}
    {status === "unavailable" && <p className="route-card-like-notice" role="status">로그인 상태를 확인하지 못해 좋아요를 사용할 수 없어요.</p>}
    {feedback && <p className="route-card-like-notice" role="status">{feedback}</p>}
    {error && <div className="route-card-like-notice"><p role="alert">{error}</p>
      <button type="button" className="route-card-like-retry" disabled={busy} onClick={() => setRetry(value => value + 1)}>상태 다시 확인</button>
    </div>}
  </>;
  return children({ button, notice });
}
