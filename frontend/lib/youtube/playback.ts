import type { YouTubePlayer, YouTubePlayerApi } from "./iframe-api";

export type PlaybackStatus = "loading" | "ready" | "playing" | "paused" | "blocked" | "error";

// YouTube replaces its mount node. Keep that node outside React's ownership,
// and never send player commands until onReady (methods do not exist earlier).
export function createYouTubePlayback(
  host: HTMLElement,
  api: YouTubePlayerApi,
  options: { videoId: string; title: string; preview: boolean; start?: number; onStatus: (status: PlaybackStatus, code?: number) => void },
) {
  let disposed = false;
  let ready = false;
  let active = false;
  let player: YouTubePlayer;
  const iframe = document.createElement("iframe");
  iframe.title = options.title;
  iframe.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
  iframe.allowFullscreen = !options.preview;
  iframe.referrerPolicy = "strict-origin-when-cross-origin";
  if (options.preview) iframe.tabIndex = -1;
  const params = new URLSearchParams({
    enablejsapi: "1", origin: window.location.origin, playsinline: "1", mute: "1",
    controls: options.preview ? "0" : "1", disablekb: options.preview ? "1" : "0", rel: "0",
  });
  if (options.start) params.set("start", String(options.start));
  iframe.src = `https://www.youtube.com/embed/${options.videoId}?${params}`;
  host.appendChild(iframe);
  const report = (status: PlaybackStatus, code?: number) => {
    if (!disposed) options.onStatus(status, code);
  };
  const timeout = window.setTimeout(() => report("error"), 20_000);
  const sync = () => {
    if (disposed || !ready) return;
    if (active) {
      player.mute();
      player.playVideo();
    } else player.pauseVideo();
  };
  player = new api.Player(iframe, {
    events: {
      onReady: event => {
        if (disposed) return;
        window.clearTimeout(timeout);
        player = event.target;
        ready = true;
        report("ready");
        sync();
      },
      onStateChange: event => {
        if (disposed) return;
        // A delayed play acknowledgement must not restart a preview we left.
        if (options.preview && !active && event.data === 1) {
          sync();
          return;
        }
        if (event.data === 1) report("playing");
        else if (event.data === 2 || event.data === 0) report("paused");
      },
      onError: event => { window.clearTimeout(timeout); report("error", event.data); },
      onAutoplayBlocked: () => report("blocked"),
    },
  });
  return {
    setActive(value: boolean) { active = value; sync(); },
    play() { active = true; sync(); },
    destroy() {
      disposed = true;
      window.clearTimeout(timeout);
      player.destroy();
      iframe.remove();
    },
  };
}
