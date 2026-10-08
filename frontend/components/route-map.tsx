"use client";

import { useEffect, useEffectEvent, useId, useRef, useState, useMemo, type ReactNode } from "react";
import { loadKakaoMaps, type KakaoMap, type KakaoMaps, type KakaoOverlay, type KakaoPlace, type MapClickEvent } from "@/lib/kakao-maps";
import { searchKakaoPlaces } from "@/lib/nearby-search";
import { areValidCoordinates, type RouteStop } from "@/lib/routes";
import { coursePointLabel } from "@/lib/drawn-course";
import { CourseTravelPanel, useCourseDirections, useTravelOverlay } from "./course-travel";
import { Icon } from "./icons";
import { googleLodgingId, isLodgingReference, kakaoLodgingReference } from "@/lib/google-lodging";
import { GoogleLodgingPanel } from "./google-lodging-panel";
import { KakaoLodgingPanel } from "./kakao-lodging-panel";
import type { NearbyStadium } from "@/lib/nearby-places";
import type { LegModes, TravelMode, TravelPoint } from "@/lib/course-directions";

type Props = { stops: RouteStop[]; stadium?: NearbyStadium; searchable?: boolean; allowOriginSelection?: boolean; mapFirst?: boolean; children?: ReactNode; travelAside?: ReactNode; onAddStop?: (stop: RouteStop) => void; travelMode?: TravelMode; legModes?: LegModes; start?: TravelPoint };

export function RouteMap({ stops: storedStops, stadium, searchable = false, allowOriginSelection = true, mapFirst = false, children, travelAside, onAddStop, travelMode, legModes: initialLegModes, start }: Props) {
  const [resolved, setResolved] = useState<Record<string, RouteStop>>({});
  const stops = useMemo(() => storedStops.map(stop => isLodgingReference(stop) && resolved[stop.placeId!] ? { ...stop, name: resolved[stop.placeId!].name, lat: resolved[stop.placeId!].lat, lng: resolved[stop.placeId!].lng } : stop), [storedStops, resolved]);
  const [legModes, setLegModes] = useState(initialLegModes ?? {});
  const travel = useCourseDirections(stops, true, start, undefined, travelMode, undefined, undefined, legModes, setLegModes);
  const [fitRequest, setFitRequest] = useState(0);
  const [mapState, setMapState] = useState<KakaoMap | null>(null);
  const mapNode = useRef<HTMLDivElement>(null);
  useEffect(() => { if (travel.picking) mapNode.current?.scrollIntoView({ behavior: "smooth", block: "center" }); }, [travel.picking]);
  const mapRef = useRef<KakaoMap | null>(null);
  const [sdk, setSdk] = useState<KakaoMaps | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<KakaoPlace[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchNote, setSearchNote] = useState("");
  const [selected, setSelected] = useState<RouteStop | null>(null);
  const [notice, setNotice] = useState("");
  const sequence = useRef(0);
  const searchController = useRef<AbortController | null>(null);
  const searchId = useId();
  const first = stops.find(stop => !googleLodgingId(stop) && areValidCoordinates(stop.lat, stop.lng));
  useTravelOverlay(mapState, sdk, travel);
  const fitRoute = useEffectEvent((map: KakaoMap, maps: KakaoMaps) => {
    const points = [...travel.points, ...(travel.data?.legs.flatMap((leg) => leg.paths.flat()) ?? [])].filter(stop => areValidCoordinates(stop.lat, stop.lng)).map(stop => new maps.LatLng(stop.lat, stop.lng));
    if (points.length > 1) { const bounds = new maps.LatLngBounds(); points.forEach(point => bounds.extend(point)); map.setBounds(bounds, 45, 35, 35, 35); }
    else if (points[0]) map.setCenter(points[0]);
  });

  useEffect(() => {
    let cancelled = false;
    loadKakaoMaps().then(value => { if (!cancelled) { setSdk(value); setError(""); } }).catch(() => {
      if (!cancelled) setError("지도를 불러오지 못했어요. 다시 시도하거나 카카오맵에서 장소를 확인해 주세요.");
    });
    return () => { cancelled = true; sequence.current += 1; searchController.current?.abort(); };
  }, [attempt]);

  const isPickingStart = useEffectEvent(() => travel.picking);
  useEffect(() => {
    if (!sdk || !mapNode.current) return;
    const map = new sdk.Map(mapNode.current, { center: new sdk.LatLng(37.5162, 127.07594), level: 5, draggable: true, scrollwheel: true, disableDoubleClickZoom: false });
    mapRef.current = map;
    const frame = requestAnimationFrame(() => setMapState(map));
    const onClick = (event: MapClickEvent) => {
      const lat = event.latLng.getLat(), lng = event.latLng.getLng();
      if (searchable && !isPickingStart() && areValidCoordinates(lat, lng)) { setSelected({ name: "", lat, lng, category: "내 장소" }); setNotice(""); }
    };
    if (searchable) sdk.event.addListener(map, "click", onClick);
    const observer = new ResizeObserver(() => { const center = map.getCenter(); map.relayout(); if (!searchable) fitRoute(map, sdk); else map.setCenter(center); });
    observer.observe(mapNode.current);
    return () => { cancelAnimationFrame(frame); observer.disconnect(); if (searchable) sdk.event.removeListener(map, "click", onClick); mapRef.current = null; };
  }, [sdk, searchable]);

  useEffect(() => {
    const map = mapRef.current;
    if (!sdk || !map) return;
    const overlays: KakaoOverlay[] = [];
    stops.filter(stop => areValidCoordinates(stop.lat, stop.lng)).map((stop) => {
      const point = new sdk.LatLng(stop.lat, stop.lng);
      const label = document.createElement("span");
      label.className = (stop.isMapPoint || stop.isDrawnPoint) ? "planner-drawn-pin" : "route-map-pin";
      const originalIndex = stops.indexOf(stop);
      const text = document.createElement("span"); text.textContent = coursePointLabel(stops, originalIndex); label.appendChild(text);
      label.title = `${coursePointLabel(stops, originalIndex)}. ${stop.name}`;
      label.setAttribute("aria-label", label.title);
      overlays.push(new sdk.CustomOverlay({ map, position: point, content: label, yAnchor: 1, zIndex: 2 }));
      return point;
    });

    fitRoute(map, sdk);
    return () => overlays.forEach(overlay => overlay.setMap(null));
  }, [sdk, stops, mapState]);

  useEffect(() => { if (mapState && sdk && travel.data) fitRoute(mapState, sdk); }, [mapState, sdk, travel.data, fitRequest]);

  async function search() {
    if (!query.trim() || !sdk || !mapRef.current || searching) return;
    const request = ++sequence.current;
    searchController.current?.abort();
    const controller = new AbortController();
    searchController.current = controller;
    setSearching(true); setSearchNote(""); setResults([]); setSelected(null); setNotice("");
    const center = mapRef.current.getCenter();
    try {
      const result = await searchKakaoPlaces({ method: "keyword", keyword: query.trim(), lat: center.getLat(), lng: center.getLng(), page: 1, size: 5, sort: "accuracy" }, controller.signal);
      if (request !== sequence.current || controller.signal.aborted) return;
      const validPlaces = result.places.filter(place => place.x.trim() && place.y.trim() && areValidCoordinates(Number(place.y), Number(place.x)));
      setResults(validPlaces);
      if (!validPlaces.length) setSearchNote("검색 결과가 없어요. 지역이나 장소 이름을 바꿔 보세요.");
    } catch (reason) {
      if (request !== sequence.current || controller.signal.aborted) return;
      setSearchNote(reason instanceof DOMException && reason.name === "TimeoutError" ? "검색이 오래 걸리고 있어요. 다시 검색해 주세요." : "장소 검색에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.");
    } finally { if (request === sequence.current) setSearching(false); }
  }

  function add(stop: RouteStop) {
    if (!onAddStop) return;
    if (!stop.name.trim() || !areValidCoordinates(stop.lat, stop.lng)) { setNotice("장소의 위치를 확인하지 못했어요. 다른 장소를 선택해 주세요."); return; }
    if (stops.length >= 12) { setNotice("방문 장소는 최대 12곳까지 추가할 수 있어요."); return; }
    if (stops.some(item => item.name === stop.name && item.lat === stop.lat && item.lng === stop.lng)) { setNotice("이미 추가한 장소예요."); return; }
    onAddStop(stop); setSelected(null); setNotice(`${stop.name}을(를) 방문 장소에 추가했어요.`);
  }

  return (
    <section className="route-map" aria-label={searchable ? "방문 장소 검색과 지도" : "코스 지도"}>
      {!mapFirst && <CourseTravelPanel travel={travel} stops={stops} originReplacement={allowOriginSelection ? undefined : false} onFit={() => setFitRequest((value) => value + 1)} />}
      {searchable && <div className="route-map-search" role="search">
        <label htmlFor={searchId} className="sr-only">지도에서 장소 검색</label>
        <Icon name="search" size={19} />
        <input id={searchId} value={query} maxLength={100} onChange={event => { setQuery(event.target.value); sequence.current += 1; searchController.current?.abort(); setSearching(false); setResults([]); setSearchNote(""); }} onKeyDown={event => { if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); search(); } }} placeholder="구장, 맛집, 카페 이름으로 검색" />
        <button type="button" onClick={search} disabled={!sdk || searching || !query.trim()}>{searching ? <><span className="ui-spinner" />검색 중</> : "검색"}</button>
      </div>}
      <div className={`route-map-stage${travel.picking ? " is-picking-start" : ""}`}>
        {travel.picking && <div className="course-pick-hint">지도를 눌러 출발 위치를 지정하세요 <button type="button" onClick={travel.cancelPicking}>취소</button></div>}
        <div ref={mapNode} className="route-map-canvas" aria-label="카카오 지도" />
        {!sdk && <div className="route-map-placeholder" role="status"><Icon name="stadium" size={38} /><strong>{error ? "지도를 잠시 불러오지 못했어요" : "코스를 지도에 펼치는 중"}</strong><p>{error || "방문할 장소와 순서를 준비하고 있어요."}</p>{error ? <button className="button button-secondary" type="button" onClick={() => { setError(""); setAttempt(value => value + 1); }}>다시 불러오기</button> : <span className="ui-spinner" />}</div>}
        {sdk && <span className="route-map-label">{searchable ? "지도에서 위치를 눌러 직접 추가할 수 있어요" : "코스 미리보기"}</span>}
      </div>
      {children}
      <p className="route-map-caption">{travel.googleCourse ? "숙소 위치는 확인 버튼으로 불러와요. 조회된 코스는 직선 연결이며 실제 길찾기가 아니에요." : "선택한 이동 수단의 실제 경로를 표시해요. 조회할 수 없는 구간은 선을 표시하지 않아요."}{first && <> <a href={`https://map.kakao.com/link/map/${encodeURIComponent(first.name)},${first.lat},${first.lng}`} target="_blank" rel="noreferrer">카카오맵 열기 ↗</a></>}</p>
      {storedStops.some(stop => googleLodgingId(stop)) && <GoogleLodgingPanel stops={storedStops} onSelect={stop => setResolved(previous => ({ ...previous, [stop.placeId!]: stop }))} />}
      {storedStops.some(stop => kakaoLodgingReference(stop)) && <KakaoLodgingPanel key={stadium?.code} stadium={stadium} stops={stops} onSelect={stop => setResolved(previous => ({ ...previous, [stop.placeId!]: stop }))} />}
      {mapFirst && <div className={travelAside ? "route-travel-columns" : undefined}><CourseTravelPanel travel={travel} stops={stops} originReplacement={allowOriginSelection ? undefined : false} onFit={() => setFitRequest((value) => value + 1)} />{travelAside}</div>}
      {searching && <p className="route-map-note" role="status">장소를 검색하고 있어요.</p>}
      {searchNote && <p className="route-map-note" role="status">{searchNote}</p>}
      {results.length > 0 && <ul className="route-map-results" aria-label="장소 검색 결과">{results.map(place => <li key={place.id}><div><strong>{place.place_name}</strong><span>{place.road_address_name || place.address_name}</span></div><button type="button" aria-label={`${place.place_name} 방문 장소에 추가`} onClick={() => add({ name: place.place_name, lat: Number(place.y), lng: Number(place.x), category: place.category_group_name || "방문 장소" })}>추가 +</button></li>)}</ul>}
      {selected && <div className="route-map-selected"><label htmlFor={`${searchId}-name`}>선택한 위치의 이름</label><input id={`${searchId}-name`} placeholder="예: 친구와 만날 장소" maxLength={70} value={selected.name} onChange={event => setSelected({ ...selected, name: event.target.value })} /><button className="button button-primary" type="button" disabled={!selected.name.trim()} onClick={() => add({ ...selected, name: selected.name.trim() })}>방문 장소에 추가</button></div>}
      {notice && <p className="route-map-note" role="status">{notice}</p>}
    </section>
  );
}
