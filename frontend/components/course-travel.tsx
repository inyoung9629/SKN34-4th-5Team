"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { requestCurrentLocation } from "@/lib/geolocation";
import { TRAVEL_MODES, activeLegModes, courseLegModes, fetchCourseDirections, legKey, travelDistance, travelTime, type CourseDirections, type LegModes, type TravelLeg, type TravelMode, type TravelPoint } from "@/lib/course-directions";
import type { RouteStop } from "@/lib/routes";
import { canRequestDirections, isLodgingReference, locatedStop } from "@/lib/google-lodging";
import type { KakaoMap, KakaoMaps, KakaoOverlay } from "@/lib/kakao-maps";

import { coursePointLabel } from "@/lib/drawn-course";
import { directionMarker } from "@/lib/route-presentation";

import { offsetRouteBand, splitRouteOverlaps } from "@/lib/route-overlaps";
import type { CSSProperties, ReactNode } from "react";

const LEG_COLORS = ["#3478dc", "#d76a32", "#8954b9", "#218777", "#c44776", "#9b7928", "#467b90", "#a65346", "#6663b5", "#52853d", "#ae549a", "#55718c"];
const legColor = (index: number) => LEG_COLORS[index % LEG_COLORS.length];

type ControlledCourse = { start?: TravelPoint; mode: TravelMode; revision: number; originSource: "custom" | "current";
  onStartChange: (start: TravelPoint | undefined, source?: "custom" | "current") => void };
export function useCourseDirections(stops: RouteStop[], enabled = true, initialStart?: TravelPoint, replaceOrigin?: (point: TravelPoint) => boolean, initialMode: TravelMode = "walk", onModeChange?: (mode: TravelMode) => void, onPickingStart?: () => void, savedLegModes?: LegModes, onLegModesChange?: (modes: LegModes) => void, controlled?: ControlledCourse) {
  const [legSelection, setLegSelection] = useState<{ key: string; index: number } | null>(null);
  const [localMode, setModeState] = useState<TravelMode>(initialMode);
  const mode = controlled?.mode ?? localMode;
  const [localLegModes, setLocalLegModes] = useState<LegModes>({});
  const legModes = savedLegModes ?? localLegModes;
  const updateLegModes = (next: LegModes) => { setLocalLegModes(next); onLegModesChange?.(next); };
  const setMode = (next: TravelMode) => { if (!controlled) { setModeState(next); updateLegModes({}); } onModeChange?.(next); };
  const cache = useRef(new Map<string, TravelLeg>());
  const [localOrigin, setOrigin] = useState<"first" | "current" | "custom">(initialStart ? "custom" : "first");
  const origin = controlled ? controlled.start ? controlled.originSource : "first" : localOrigin;
  const [customLocation, setCustomLocation] = useState<TravelPoint | null>(initialStart ?? null);
  const [picking, setPicking] = useState(false);
  const [location, setLocation] = useState<TravelPoint | null>(null);
  const [locating, setLocating] = useState(false);
  const [locationError, setLocationError] = useState("");
  const locationRequest = useRef(0);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ key: string; data?: CourseDirections; error?: string } | null>(null);
  const startLocation = controlled ? controlled.start ?? null : origin === "current" ? location : origin === "custom" ? customLocation : null;
  const controlRef = useRef(controlled);
  useLayoutEffect(() => { controlRef.current = controlled; });
  const revision = controlled?.revision;
  const [pickerRevision, setPickerRevision] = useState(revision);
  if (pickerRevision !== revision) {
    setPickerRevision(revision); setPicking(false); setLocating(false); setLocationError("");
  }
  useEffect(() => { locationRequest.current++; }, [revision]);
  const allPoints = useMemo(() => startLocation ? [startLocation, ...stops] : stops, [startLocation, stops]);
  const points = useMemo(() => allPoints.filter(locatedStop), [allPoints]);
  const googleCourse = stops.some(stop => isLodgingReference(stop));
  const unresolved = points.length !== allPoints.length;
  const ready = enabled && canRequestDirections(stops) && stops.length > 0 && points.length >= 2 && (origin === "first" || Boolean(startLocation)) && !locating && !picking;
  const direct = useMemo<CourseDirections | undefined>(() => googleCourse && !unresolved && points.length >= 2 ? {
    mode, seconds: null, distance: null,
    legs: points.slice(1).map((point, index) => ({ status: "error", paths: [[points[index], point]], instructions: [], error: "방문 순서 직선 연결 · 실제 이동 경로/시간 미제공" })),
  } : undefined, [googleCourse, unresolved, points, mode]);
  const modes = courseLegModes(points, mode, legModes);
  const routeKey = JSON.stringify(points.map(({ lat, lng }) => ({ lat, lng })));
  const payload = JSON.stringify({ mode, modes, points: points.map(({ lat, lng }) => ({ lat, lng })) });
  const requestKey = `${payload}:${attempt}`;
  useEffect(() => () => { locationRequest.current++; }, []);
  useEffect(() => {
    if (!ready) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const args = JSON.parse(payload) as { mode: TravelMode; modes: TravelMode[]; points: TravelPoint[] };
        const body = await fetchCourseDirections(args.points, args.modes, args.mode, cache.current, controller.signal);
        if (!controller.signal.aborted) setResult({ key: requestKey, data: body });
      } catch (error) {
        if (!controller.signal.aborted) setResult({ key: requestKey, error: error instanceof Error ? error.message : "경로를 조회하지 못했어요." });
      }
    }, 450);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [payload, requestKey, ready]);
  function chooseFirst() { locationRequest.current++; if (controlled) controlled.onStartChange(undefined); else setOrigin("first"); setPicking(false); setLocating(false); setLocationError(""); }
  function chooseCurrent() {
    onPickingStart?.();
    setOrigin("current"); setPicking(false); setLocation(null); setLocationError("");
    const sequence = ++locationRequest.current;
    const requestedRevision = controlled?.revision;
    setLocating(true);
    requestCurrentLocation((point) => {
      if (locationRequest.current !== sequence || controlRef.current?.revision !== requestedRevision) return;
      if (controlRef.current) controlRef.current.onStartChange(point, "current");
      else if (replaceOrigin?.(point)) { setLocation(null); setOrigin("first"); }
      else setLocation(point);
      setLocating(false);
    }, (message) => {
      if (locationRequest.current !== sequence || controlRef.current?.revision !== requestedRevision) return;
      onPickingStart?.();
      setLocating(false); setOrigin("custom"); setPicking(true);
      setLocationError(message);
    });
  }
  function chooseCustom() { locationRequest.current++; onPickingStart?.(); setLocating(false); setLocationError(""); setOrigin("custom"); setPicking(true); }
  function pickLocation(point: TravelPoint) {
    locationRequest.current++;
    setLocating(false);
    if (controlled) controlled.onStartChange(point, "custom");
    else if (replaceOrigin?.(point)) { setCustomLocation(null); setOrigin("first"); }
    else { setCustomLocation(point); setOrigin("custom"); }
    setPicking(false); setLocationError("");
  }
  const syncStart = useCallback((point?: TravelPoint) => {
    locationRequest.current++;
    setCustomLocation(point ?? null); setLocation(null);
    setOrigin(point ? "custom" : "first"); setPicking(false); setLocating(false); setLocationError("");
  }, []);
  function cancelPicking() { setPicking(false); setLocationError(""); if (!customLocation) setOrigin("first"); }
  function reset() {
    locationRequest.current++;
    setLegSelection(null); setMode("walk"); setOrigin("first");
    setCustomLocation(null); setLocation(null); setPicking(false);
    setLocating(false); setLocationError(""); setResult(null);
  }
  const current = ready && result?.key === requestKey ? result : null;
  const selectedLeg = legSelection?.key === routeKey ? legSelection.index : null;
  const selectLeg = (index: number | null) => setLegSelection(index === null ? null : { key: routeKey, index });
  const setSelectedMode = (next: TravelMode) => {
    if (selectedLeg === null) setMode(next);
    else updateLegModes({ ...activeLegModes(points, legModes), [legKey(points[selectedLeg], points[selectedLeg + 1])]: next });
  };
  return { selectedLeg, selectLeg, mode, modes, setMode, syncMode: setModeState, syncStart, setSelectedMode, origin, chooseFirst, chooseCurrent, chooseCustom, pickLocation, cancelPicking, reset, picking, location: startLocation, locating, locationError, points, ready, loading: ready && !current, data: googleCourse ? direct : current?.data, googleCourse, unresolved, error: current?.error, retry: () => { cache.current.clear(); setAttempt((value) => value + 1); } };
}
type TravelState = ReturnType<typeof useCourseDirections>;

export function useTravelOverlay(map: KakaoMap | null, maps: KakaoMaps | null, travel: TravelState, options: { lineColor?: string } = {}) {
  const lineColor = options.lineColor;
  const { data, location, origin, picking, pickLocation, selectedLeg } = travel;
  const [viewport, setViewport] = useState(0);
  const routeBands = useMemo(() => splitRouteOverlaps(data?.legs.map((leg) => leg.paths) ?? []), [data]);
  useEffect(() => {
    if (!map || !maps) return;
    const idle = () => setViewport((value) => value + 1);
    maps.event.addListener(map, "idle", idle);
    return () => maps.event.removeListener(map, "idle", idle);
  }, [map, maps]);
  useEffect(() => {
    if (!map || !maps || !picking) return;
    const pick = (event: { latLng: { getLat(): number; getLng(): number } }) => pickLocation({ lat: event.latLng.getLat(), lng: event.latLng.getLng() });
    maps.event.addListener(map, "click", pick);
    return () => maps.event.removeListener(map, "click", pick);
  }, [map, maps, picking, pickLocation]);
  useEffect(() => {
    if (!map || !maps) return;
    const overlays: KakaoOverlay[] = [];
    const projection = map.getProjection();
    const legs = (data?.legs ?? []).map((leg, index) => ({ leg, index }));
    // Draw the focused leg last so overlapping paths cannot cover it.
    legs.sort((a, b) => Number(a.index === selectedLeg) - Number(b.index === selectedLeg));
    const placedArrows: { x: number; y: number }[] = [];
    const bounds = map.getBounds();
    const cornerA = projection.containerPointFromCoords(bounds.getSouthWest());
    const cornerB = projection.containerPointFromCoords(bounds.getNorthEast());
    const view = { left: Math.min(cornerA.x, cornerB.x), right: Math.max(cornerA.x, cornerB.x), top: Math.min(cornerA.y, cornerB.y), bottom: Math.max(cornerA.y, cornerB.y) };
    const stopPixels = travel.points.map((point) => projection.containerPointFromCoords(new maps.LatLng(point.lat, point.lng)));
    const anchorCoordinate = map.getCenter();
    const anchor = projection.containerPointFromCoords(anchorCoordinate);
    const surface = document.createElement("div");
    surface.className = "course-route-surface";
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    const width = Math.max(1, view.right - view.left), height = Math.max(1, view.bottom - view.top);
    svg.setAttribute("width", String(width)); svg.setAttribute("height", String(height));
    svg.setAttribute("viewBox", `${view.left} ${view.top} ${width} ${height}`);
    svg.setAttribute("aria-hidden", "true");
    surface.style.width = `${width}px`; surface.style.height = `${height}px`;
    surface.style.transform = `translate(${view.left + width / 2 - anchor.x}px, ${view.top + height / 2 - anchor.y}px)`;
    surface.appendChild(svg);
    const projected = routeBands.map((band) => ({ ...band, points: band.path.map((point) => projection.containerPointFromCoords(new maps.LatLng(point.lat, point.lng))) }));
    const lineWidth = (count: number) => count === 1 ? 6 : Math.min(12, count * 4);
    const stroke = (points: { x: number; y: number }[], color: string, width: number, opacity: number, legs: number[], lane?: number) => {
      const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
      line.setAttribute("points", points.map((p) => `${p.x},${p.y}`).join(" "));
      line.setAttribute("fill", "none"); line.setAttribute("stroke", color);
      line.setAttribute("stroke-width", String(width)); line.setAttribute("stroke-opacity", String(opacity));
      line.setAttribute("stroke-linejoin", "round"); line.setAttribute("stroke-linecap", lane !== undefined && legs.length > 1 ? "butt" : "round");
      line.setAttribute("data-legs", legs.join(","));
      if (lane !== undefined) line.setAttribute("data-leg", String(lane));
      svg.appendChild(line);
    };
    // All outlines first. No per-piece white strokes that cut across other
    // colours, no native endArrow, and round joins instead of pointed mitres.
    projected.forEach((band) => stroke(band.points, "#ffffff", lineWidth(band.legs.length) + 2, .9, band.legs));
    const lanes = projected.flatMap((band) => band.legs.map((leg, index) => {
      const width = lineWidth(band.legs.length) / band.legs.length;
      return { band, leg, width, offset: (index - (band.legs.length - 1) / 2) * width };
    }));
    lanes.sort((a, b) => Number(a.leg === selectedLeg) - Number(b.leg === selectedLeg));
    lanes.forEach(({ band, leg, width, offset }) => stroke(offsetRouteBand(band.points, offset), lineColor ?? legColor(leg), width, selectedLeg === null || selectedLeg === leg ? 1 : .22, band.legs, leg));
    for (const { leg, index } of legs) {
      if ((selectedLeg !== null && selectedLeg !== index) || placedArrows.length >= 3) continue;
      const paths = leg.paths.map((path) => path.map((point) => ({ ...point, ...projection.containerPointFromCoords(new maps.LatLng(point.lat, point.lng)) })));
      const candidate = directionMarker(paths, view, stopPixels, placedArrows);
      if (!candidate) continue;
      let x = candidate.x, y = candidate.y;
      // Keep the existing sparse direction cue inside this leg's coloured half.
      let nearest = Infinity;
      for (const lane of lanes.filter((lane) => lane.leg === index)) for (let i = 1; i < lane.band.points.length; i++) {
        const a = lane.band.points[i - 1], b = lane.band.points[i];
        const dx = b.x - a.x, dy = b.y - a.y, length = Math.hypot(dx, dy);
        if (length < .001) continue;
        const t = Math.max(0, Math.min(1, ((candidate.x - a.x) * dx + (candidate.y - a.y) * dy) / (length * length)));
        const distance = Math.hypot(candidate.x - a.x - t * dx, candidate.y - a.y - t * dy);
        if (distance < nearest) { nearest = distance; x = a.x + t * dx - dy / length * lane.offset; y = a.y + t * dy + dx / length * lane.offset; }
      }
      const arrow = document.createElementNS("http://www.w3.org/2000/svg", "path");
      arrow.setAttribute("class", "course-route-direction"); arrow.setAttribute("data-leg", String(index));
      arrow.setAttribute("d", "M-3 -4L2 0L-3 4");
      arrow.setAttribute("transform", `translate(${x} ${y}) rotate(${candidate.angle * 180 / Math.PI})`);
      arrow.style.setProperty("--route-color", lineColor ?? legColor(index));
      svg.appendChild(arrow); placedArrows.push(candidate);
    }
    if (data) overlays.push(new maps.CustomOverlay({ map, position: anchorCoordinate, content: surface, xAnchor: .5, yAnchor: .5, zIndex: 2 }));
    if (location) {
      const label = document.createElement("span"); label.className = "course-current-marker"; label.textContent = origin === "custom" ? "출발" : "내 위치";
      overlays.push(new maps.CustomOverlay({ map, position: new maps.LatLng(location.lat, location.lng), content: label, yAnchor: 1, zIndex: 14 }));
    }
    return () => overlays.forEach((overlay) => overlay.setMap(null));
  }, [map, maps, data, location, origin, selectedLeg, viewport, travel.points, routeBands, lineColor]);
}

export function CourseTravelPanel({ travel, stops, onFit, showDirections = true, originReplacement }: { travel: TravelState; stops: RouteStop[]; onFit?: () => void; showDirections?: boolean; originReplacement?: ReactNode }) {
  const { mode, origin, data } = travel;
  const [showOrigins, setShowOrigins] = useState(false);
  const firstLabel = stops.length === 0 ? "첫 번째 지점" : (stops[0]?.isMapPoint || stops[0]?.isDrawnPoint) ? "코스 출발지" : "코스 1번";
  const names = origin !== "first" ? [origin === "current" ? "내 위치" : "선택한 출발지", ...stops.map((stop) => stop.name)] : stops.map((stop) => stop.name);
  const pointLabels = stops.map((_, index) => coursePointLabel(stops, index, origin !== "first"));
  if (origin !== "first") pointLabels.unshift(origin === "current" ? "내 위치" : "출발");
  const legLabel = (index: number) => `${pointLabels[index]} → ${pointLabels[index + 1]}`;
  const selectedMode = travel.selectedLeg === null ? (travel.modes.every(value => value === travel.modes[0]) ? travel.modes[0] ?? mode : null) : travel.modes[travel.selectedLeg];
  return <section className="course-travel" aria-label={showDirections ? "코스 이동 경로와 예상 시간" : "출발지 선택"}>
    <div className="course-travel-heading"><strong>{showDirections ? "코스 이동 안내" : "출발지 선택"}</strong>{showDirections && onFit && data && <button type="button" onClick={onFit}>경로 전체 보기</button>}</div>
    {originReplacement ?? <><button type="button" className="course-origin-trigger" aria-expanded={showOrigins} onClick={() => setShowOrigins((show) => !show)}>시작 위치 설정 <span>{origin === "first" ? firstLabel : origin === "current" ? "내 위치" : "지도에서 지정"} ▾</span></button>
    {showOrigins && <div className="course-origin" role="group" aria-label="코스 출발지">
      <button type="button" aria-pressed={origin === "first"} onClick={() => { travel.chooseFirst(); setShowOrigins(false); }}>{firstLabel}에서 출발</button>
      <button type="button" aria-pressed={origin === "current"} onClick={() => { travel.chooseCurrent(); setShowOrigins(false); }}>내 위치에서 출발</button>
      <button type="button" aria-pressed={origin === "custom"} onClick={() => { travel.chooseCustom(); setShowOrigins(false); }}>지도에서 직접 지정</button>
    </div>}</>}
    {showDirections && !travel.googleCourse && <>
      <div className="course-leg-focus" role="group" aria-label="지도 구간 강조">
        <button type="button" aria-pressed={travel.selectedLeg === null} onClick={() => travel.selectLeg(null)}>전체</button>
        {travel.points.slice(1).map((_, index) => <button type="button" key={index} style={{ "--leg-color": legColor(index) } as CSSProperties} aria-label={`${index + 1}구간: ${names[index]}에서 ${names[index + 1]}까지 강조`} aria-pressed={travel.selectedLeg === index} onClick={() => travel.selectLeg(index)}><span className="course-leg-swatch" aria-hidden="true" />{legLabel(index)}</button>)}
      </div>
      <p className="course-mode-target">{travel.selectedLeg === null ? "전체 구간 이동수단" : `${legLabel(travel.selectedLeg)} 이동수단`}</p>
      <div className="course-modes" role="group" aria-label="이동 수단">{TRAVEL_MODES.map((item) => <button type="button" key={item.id} aria-pressed={selectedMode === item.id} onClick={() => travel.setSelectedMode(item.id)}>{item.label}</button>)}</div>
      <p className="course-travel-note">{travel.selectedLeg === null ? "구간을 선택하면 해당 구간의 이동수단만 바꿀 수 있어요." : `${names[travel.selectedLeg]} → ${names[travel.selectedLeg + 1]}에만 적용해요.`}</p>
    </>}
    <div aria-live="polite" className="course-travel-status">
      {travel.googleCourse && <p>실시간 조회 숙소가 포함되어 현재 실제 길찾기·이동 시간은 제공하지 않아요. {travel.unresolved ? "숙소 위치 확인 버튼으로 조회하면 연결선이 표시돼요." : "지도 선은 방문 순서의 직선 연결이에요."}</p>}
      {travel.picking && travel.locationError && <p role="alert">{travel.locationError}</p>}{travel.picking ? <p>지도에서 출발할 위치를 눌러 주세요. <button type="button" onClick={travel.cancelPicking}>지정 취소</button></p> : travel.locating ? <p>내 위치를 확인하고 있어요.</p> : travel.locationError ? <p role="alert">{travel.locationError}</p> : travel.googleCourse ? null : !showDirections ? <p>{stops.length === 0 ? travel.location ? "출발 위치를 정했어요. 코스에 방문할 장소를 추가해 주세요." : "첫 번째 지점에서 출발하거나, 내 위치·지도에서 출발지를 먼저 정할 수 있어요." : "코스 완성을 누르면 예상 이동 시간을 확인할 수 있어요."}</p> : !travel.ready ? <p>{stops.length === 0 ? "코스에 방문 장소를 추가해 주세요." : "코스에 두 곳 이상 담거나 별도의 시작 위치를 설정해 보세요."}</p> : travel.loading ? <p>{(TRAVEL_MODES.find((item) => item.id === selectedMode)?.label ?? "구간별")} 경로를 조회하고 있어요…</p> : travel.error ? <p role="alert">{travel.error}</p> : data && <>
        {data.seconds !== null && data.distance !== null ? <p className="course-travel-total"><strong>약 {travelTime(data.seconds)}</strong><span>총 {travelDistance(data.distance)} · 이동 시간</span></p> : <p role="alert">조회하지 못한 구간이 있어 전체 시간을 계산할 수 없어요.</p>}
        {travel.selectedLeg !== null && <div className="course-leg-stepper">
          <button type="button" aria-label="이전 구간 강조" disabled={travel.selectedLeg === 0} onClick={() => travel.selectLeg(travel.selectedLeg! - 1)}>‹</button>
          <span>{travel.selectedLeg + 1} / {data.legs.length} 구간</span>
          <button type="button" aria-label="다음 구간 강조" disabled={travel.selectedLeg === data.legs.length - 1} onClick={() => travel.selectLeg(travel.selectedLeg! + 1)}>›</button>
        </div>}
        <p className="course-travel-note">같은 길을 지나는 구간은 색을 나란히 나눠 표시해요.</p>
        <ol className="course-travel-legs">{data.legs.map((leg, index) => <li key={index} style={{ "--leg-color": legColor(index) } as CSSProperties} className={travel.selectedLeg === index ? "is-active" : undefined}>
          <button type="button" className="course-leg-row" aria-pressed={travel.selectedLeg === index} onClick={() => travel.selectLeg(index)}>
            <span className="course-leg-order"><span className="course-leg-swatch" aria-hidden="true" />{legLabel(index)} · {TRAVEL_MODES.find(item => item.id === travel.modes[index])?.label}</span>
            <span className="course-leg-names">{names[index]} → {names[index + 1]}</span>
            <b>{leg.status === "ok" ? travelTime(leg.seconds!) : "조회 불가"}</b>
          </button>
          <details><summary>상세 안내</summary>{leg.status === "ok" ? <><p>{travelDistance(leg.distance!)}</p><ul>{leg.instructions.map((text, step) => <li key={step}>{text}</li>)}</ul></> : <p>{leg.error}</p>}</details>
        </li>)}</ol>
      </>}
    </div>
    {showDirections && travel.ready && !travel.loading && <button type="button" className="course-travel-retry" onClick={travel.retry}>경로 다시 조회</button>}
    {showDirections && !travel.googleCourse && <p className="course-travel-note">방문 순서대로 계산한 예상 이동 시간이며, 식사·관람 등 체류 시간은 제외돼요.{travel.modes.includes("transit") && " 구간별 대중교통 경로를 합산하며 실제 대기·환승 시간은 달라질 수 있어요."}{travel.modes.includes("car") && " 교통 상황과 주차 시간에 따라 달라질 수 있어요."}</p>}
  </section>;
}
