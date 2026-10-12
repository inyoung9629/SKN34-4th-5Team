import type { KakaoMap, KakaoMaps } from "./kakao-maps";

/** Show a completed course immediately, then restore its bounds if an in-flight gesture moves the map again. */
export function createCourseMapFocus(map: KakaoMap, maps: KakaoMaps, fit: () => void) {
  let dragging = false, zooming = false, refitAfterInteraction = false;
  let frame: number | null = null, disposed = false;
  const show = () => {
    if (disposed) return;
    if (frame !== null) cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      frame = null;
      if (disposed) return;
      refitAfterInteraction = dragging || zooming;
      fit();
    });
  };
  const listeners: [string, () => void][] = [
    ["dragstart", () => { dragging = true; }],
    ["dragend", () => { dragging = false; }],
    ["zoom_start", () => { zooming = true; }],
    ["idle", () => {
      if (dragging) return;
      zooming = false;
      if (refitAfterInteraction) { refitAfterInteraction = false; show(); }
    }],
  ];
  listeners.forEach(([event, listener]) => maps.event.addListener(map, event, listener));
  return { show, dispose() {
    disposed = true;
    if (frame !== null) cancelAnimationFrame(frame);
    listeners.forEach(([event, listener]) => maps.event.removeListener(map, event, listener));
  } };
}
