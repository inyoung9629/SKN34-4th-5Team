"use client";

type GoogleSdkWindow = Window & {
  google?: { maps: { importLibrary(name: string): Promise<unknown> } };
  __googleLodgingPilotReady?: () => void;
};
let loading: Promise<void> | undefined;

export function loadGooglePlacesUiKit(key: string): Promise<void> {
  if (!key.trim()) return Promise.reject(new Error("브라우저용 Google 키가 필요해요."));
  if (loading) return loading;
  loading = new Promise<void>((resolve, reject) => {
    const sdkWindow = window as GoogleSdkWindow;
    let settled = false;
    const finish = (success: boolean) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      if (success) resolve();
      else reject(new Error("Google 연결 실패: API 활성화·결제·키의 로컬 웹사이트 제한을 확인하고 새로고침해 주세요."));
    };
    const timeout = setTimeout(() => finish(false), 20000);
    const ready = () => {
      const sdk = sdkWindow.google;
      if (!sdk) { finish(false); return; }
      sdk.maps.importLibrary("places").then(() => finish(true), () => finish(false));
    };
    if (sdkWindow.google?.maps.importLibrary) { ready(); return; }
    sdkWindow.__googleLodgingPilotReady = ready;
    const script = document.createElement("script");
    const query = new URLSearchParams({ key, v: "weekly", loading: "async", language: "ko", region: "KR", callback: "__googleLodgingPilotReady" });
    script.src = `https://maps.googleapis.com/maps/api/js?${query}`;
    script.async = true;
    script.referrerPolicy = "strict-origin-when-cross-origin";
    script.onerror = () => finish(false);
    document.head.appendChild(script);
  });
  // Keep a failed promise too: do not silently retry a billable SDK/request.
  return loading;
}
