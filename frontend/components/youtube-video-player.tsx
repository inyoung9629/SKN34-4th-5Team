"use client";

import { useEffect, useRef, useState } from "react";
import { loadYouTubeIframeApi } from "@/lib/youtube/iframe-api";
import { createYouTubePlayback, type PlaybackStatus } from "@/lib/youtube/playback";

export function YouTubeVideoPlayer({ videoId, title, active = true, preview = false, start }: {
  videoId: string; title: string; active?: boolean; preview?: boolean; start?: number;
}) {
  const host = useRef<HTMLSpanElement>(null);
  const playback = useRef<ReturnType<typeof createYouTubePlayback> | null>(null);
  const [status, setStatus] = useState<PlaybackStatus>("loading");
  const [errorCode, setErrorCode] = useState<number>();
  const [revision, setRevision] = useState(0);
  const wantsPlayback = useRef(active);

  useEffect(() => {
    wantsPlayback.current = active;
    playback.current?.setActive(active);
  }, [active]);

  useEffect(() => {
    let disposed = false;
    let instance: ReturnType<typeof createYouTubePlayback> | undefined;
    void loadYouTubeIframeApi().then(api => {
      if (disposed || !host.current) return;
      instance = createYouTubePlayback(host.current, api, {
        videoId, title, preview, start,
        onStatus: (next, code) => { setStatus(next); setErrorCode(code); },
      });
      playback.current = instance;
      instance.setActive(wantsPlayback.current);
    }).catch(() => { if (!disposed) setStatus("error"); });
    return () => {
      disposed = true;
      instance?.destroy();
      playback.current = null;
    };
  }, [videoId, title, preview, start, revision]);

  function retry() {
    setStatus("loading");
    setErrorCode(undefined);
    setRevision(value => value + 1);
  }

  const unavailable = errorCode === 100 || errorCode === 101 || errorCode === 150;
  return (
    <span className={`youtube-player${preview ? " youtube-player-preview" : ""}`} data-playback={status} data-active={active}>
      <span className="youtube-player-host" ref={host} />
      {preview ? (
        active && status !== "playing" && <span className="highlight-preview-status">{status === "error" || status === "blocked" ? "클릭해서 크게 보기" : "미리보기 불러오는 중…"}</span>
      ) : (
        <>
          {status === "loading" && <span className="youtube-player-loading" role="status">영상을 불러오고 있어요…</span>}
          {status === "error" && <span className="youtube-player-error" role="status"><strong>{unavailable ? "이 영상은 외부 사이트에서 재생할 수 없어요." : "YouTube 플레이어에 연결하지 못했어요."}</strong><span>{errorCode ? `재생 오류 ${errorCode}` : "연결 상태를 확인하고 다시 시도해 주세요."}</span><span><button type="button" onClick={retry}>다시 시도</button><a href={`https://www.youtube.com/watch?v=${videoId}`} target="_blank" rel="noopener noreferrer">YouTube에서 보기 ↗</a></span></span>}
          {status === "blocked" && <button className="youtube-player-play" type="button" onClick={() => playback.current?.play()}>▶ 영상 재생</button>}
        </>
      )}
    </span>
  );
}
