"use client";

import { useEffect, useRef, useState } from "react";
import { getStadium } from "@/lib/stadiums";
import { loadKakaoMaps, type KakaoMap, type KakaoMaps, type KakaoOverlay } from "@/lib/kakao-maps";
import { loadGooglePlacesUiKit } from "@/lib/google-places-ui-kit";
import { parsePilotReference, serializePilotReference, projectUiKitPlace, PILOT_COLLECTED_ID, PILOT_REQUEST_LIMIT, PILOT_STORAGE_KEY, type PilotSelection, type UiKitPlace } from "@/lib/google-lodging-pilot";
import styles from "./google-lodging-pilot.module.css";

const stadium = getStadium("MUNHAK")!;
const browserKey = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY?.trim() ?? "";
type PlaceWidget = HTMLElement & { place?: UiKitPlace; places?: UiKitPlace[] };

function contentConfig() {
  const config = document.createElement("gmp-place-content-config");
  config.append(document.createElement("gmp-place-address"), document.createElement("gmp-place-type"));
  return config;
}

export function GoogleLodgingPilot() {
  const mapElement = useRef<HTMLDivElement>(null);
  const widgetHost = useRef<HTMLDivElement>(null);
  const busyRef = useRef(false);
  const requestCount = useRef(0);
  const operation = useRef(0);
  const widgetCleanup = useRef<(() => void) | null>(null);
  const [mapState, setMapState] = useState<{ maps: KakaoMaps; map: KakaoMap } | null>(null);
  const [selection, setSelection] = useState<PilotSelection | null>(null);
  const [busy, setBusy] = useState(false);
  const [requests, setRequests] = useState(0);
  const [status, setStatus] = useState("Google 실조회 대기 중 · 버튼을 누르기 전에는 조회하지 않습니다.");
  const [mapStatus, setMapStatus] = useState("카카오 지도 연결 중");
  const [storageStatus, setStorageStatus] = useState("아직 테스트 코스를 저장하지 않았습니다.");

  useEffect(() => {
    let active = true;
    const invalidateOperation = () => { operation.current++; };
    if (!["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname)) return;
    // Keep Kakao's existing no-referrer request behavior on this Google-origin-enabled page.
    loadKakaoMaps({ referrerPolicy: "no-referrer" }).then(maps => {
      if (!active || !mapElement.current) return;
      const map = new maps.Map(mapElement.current, { center: new maps.LatLng(stadium.lat, stadium.lng), level: 6 });
      setMapState({ maps, map });
      setMapStatus("구장 위치 표시 완료 · 숙소 선택 대기");
    }).catch(() => { if (active) setMapStatus("카카오 지도 연결 실패: 로컬 도메인 및 지도 키 설정을 확인해 주세요."); });
    return () => { active = false; invalidateOperation(); widgetCleanup.current?.(); };
  }, []);

  useEffect(() => {
    if (!mapState) return;
    const { maps, map } = mapState;
    const overlays: KakaoOverlay[] = [];
    const origin = new maps.LatLng(stadium.lat, stadium.lng);
    const addDot = (position: typeof origin, lodging: boolean) => {
      const dot = document.createElement("div");
      dot.className = `${styles.dot} ${lodging ? styles.lodging : styles.stadium}`;
      dot.title = lodging ? "선택한 숙소 (Google Places UI Kit)" : stadium.name;
      dot.setAttribute("role", "img");
      dot.setAttribute("aria-label", dot.title);
      overlays.push(new maps.CustomOverlay({ map, position, content: dot, xAnchor: .5, yAnchor: .5, zIndex: 3 }));
    };
    addDot(origin, false);
    if (selection) {
      const destination = new maps.LatLng(selection.lat, selection.lng);
      addDot(destination, true);
      overlays.push(new maps.Polyline({ map, path: [origin, destination], strokeWeight: 3, strokeColor: "#176cd3", strokeOpacity: .8, strokeStyle: "dash", endArrow: true }));
      const bounds = new maps.LatLngBounds();
      bounds.extend(origin); bounds.extend(destination);
      map.setBounds(bounds, 48, 48, 48, 48);
    } else map.setCenter(origin);
    return () => overlays.forEach(overlay => overlay.setMap(null));
  }, [mapState, selection]);

  async function query(kind: "hotel" | "motel" | "id", id?: string) {
    if (!browserKey || busyRef.current || requestCount.current >= PILOT_REQUEST_LIMIT) return;
    if (!["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname)) {
      setStatus("로컬 주소에서만 테스트할 수 있습니다."); return;
    }
    const run = ++operation.current;
    busyRef.current = true; setBusy(true); setSelection(null);
    widgetCleanup.current?.();
    widgetHost.current?.replaceChildren();
    const current = () => operation.current === run;
    const finish = (message: string) => {
      if (!current()) return;
      busyRef.current = false; setBusy(false); setStatus(message);
    };
    setStatus("Google Places UI Kit 연결 중…");
    try {
      await loadGooglePlacesUiKit(browserKey);
      if (!current() || !widgetHost.current) return;
      const widget = document.createElement(kind === "id" ? "gmp-place-details" : "gmp-place-search") as PlaceWidget;
      widget.append(contentConfig());
      const request = document.createElement(kind === "id" ? "gmp-place-details-place-request" : "gmp-place-nearby-search-request");
      if (kind === "id") request.setAttribute("place", id ?? PILOT_COLLECTED_ID);
      else {
        widget.setAttribute("selectable", "");
        request.setAttribute("included-primary-types", kind);
        request.setAttribute("max-result-count", "5");
        request.setAttribute("rank-preference", "DISTANCE");
        request.setAttribute("location-restriction", `2500@${stadium.lat},${stadium.lng}`);
      }
      widget.append(request);
      const timeout = setTimeout(() => {
        widgetCleanup.current?.();
        finish("조회 시간 초과: 키 설정과 Places UI Kit 활성화를 확인해 주세요. 자동 재시도하지 않습니다.");
      }, 25000);
      const select = (place?: UiKitPlace) => {
        if (!current()) return;
        const selected = projectUiKitPlace(place);
        setSelection(selected);
        setStatus(selected ? "장소 선택 완료 · 주소와 분류는 Google 카드에서 확인하세요." : "장소 좌표가 없어 지도에 표시할 수 없습니다.");
      };
      const onLoad = () => {
        clearTimeout(timeout);
        if (!current()) return;
        if (kind === "id") {
          finish("저장한 ID를 새로 조회했습니다."); select(widget.place);
        } else finish(`Google 결과 ${widget.places?.length ?? 0}건 · 카드를 선택하면 지도에 점이 표시됩니다.`);
      };
      const onError = () => {
        clearTimeout(timeout);
        if (!current()) return;
        setSelection(null);
        finish("Google 조회 실패: API·결제·키 제한 설정을 확인해 주세요. 결과를 임의로 대체하지 않습니다.");
      };
      const onSelect = (event: Event) => select((event as Event & { place?: UiKitPlace }).place);
      widget.addEventListener("gmp-load", onLoad);
      widget.addEventListener("gmp-error", onError);
      widget.addEventListener("gmp-select", onSelect);
      widgetCleanup.current = () => {
        clearTimeout(timeout);
        widget.removeEventListener("gmp-load", onLoad);
        widget.removeEventListener("gmp-error", onError);
        widget.removeEventListener("gmp-select", onSelect);
        widget.remove();
      };
      requestCount.current++; setRequests(requestCount.current);
      setStatus("Google에 조회 요청을 보냈습니다. 응답 대기 중…");
      widgetHost.current.append(widget);
    } catch {
      finish("Google 연결 실패: 브라우저용 키·API 활성화·결제를 확인한 뒤 새로고침해 주세요.");
    }
  }

  function save() {
    if (!selection) return;
    try {
      localStorage.setItem(PILOT_STORAGE_KEY, serializePilotReference(selection.id));
      setStorageStatus("저장 완료: 구장 코드 + Google 장소 ID만 저장했습니다. 이름·주소·분류·좌표는 저장하지 않았습니다.");
    } catch { setStorageStatus("브라우저 저장 공간에 접근할 수 없어 저장하지 못했습니다."); }
  }

  function restore() {
    try {
      const reference = parsePilotReference(localStorage.getItem(PILOT_STORAGE_KEY));
      if (!reference) { setStorageStatus("저장한 테스트 코스가 없습니다."); return; }
      void query("id", reference.placeId);
    } catch { setStorageStatus("브라우저 저장 공간에 접근할 수 없습니다."); }
  }

  const disabled = !browserKey || busy || requests >= PILOT_REQUEST_LIMIT;
  return <main className={styles.page}>
    <div className={styles.label}>LOCAL PILOT · 기존 코스와 별도</div>
    <h1>인천 SSG 랜더스필드 숙박 연동 테스트</h1>
    <p>구장 반경 2.5km에서 호텔·모텔을 각각 최대 5곳 조회합니다. 화면당 요청은 최대 {PILOT_REQUEST_LIMIT}회입니다.</p>
    {!browserKey && <aside className={styles.notice}>
      <h2>브라우저용 Google 키 설정 대기</h2>
      <p>서버 수집용 키는 브라우저에 노출하지 않습니다. 별도 웹사이트 제한 키를 <code>frontend/.env.local</code>의 <code>NEXT_PUBLIC_GOOGLE_MAPS_API_KEY</code>에 설정해 주세요.</p>
      <p>Places UI Kit와 Maps JavaScript API를 활성화하고, 허용 웹사이트에 <code>http://127.0.0.1/*</code>와 <code>http://localhost/*</code>를 추가해 주세요. 설정 후 프론트 서버 재시작 또는 새로고침이 필요합니다.</p>
    </aside>}
    <div className={styles.controls}>
      <button type="button" disabled={disabled} onClick={() => void query("hotel")}>호텔 5곳 조회</button>
      <button type="button" disabled={disabled} onClick={() => void query("motel")}>모텔 5곳 조회</button>
      <button type="button" disabled={disabled} onClick={() => void query("id")}>수집한 ID 1건 조회</button>
      <button type="button" disabled={!selection || busy} onClick={save}>선택한 숙소 ID 저장</button>
      <button type="button" disabled={disabled} onClick={restore}>저장한 ID 다시 조회</button>
    </div>
    <p className={styles.status} role="status">{status} · 요청 {requests}/{PILOT_REQUEST_LIMIT}</p>
    <div className={styles.columns}>
      <section><h2>Google 주소·분류 카드</h2><div className={styles.results}>
        <div ref={widgetHost} />
        {requests === 0 && <p>실조회 전입니다. 아직 숙소 정보나 분류가 확인되지 않았습니다.</p>}
      </div></section>
      <section><h2>카카오 지도 · 선택 위치</h2><div ref={mapElement} className={styles.map} aria-label="구장과 선택 숙소 지도" />
        <p className={styles.legend}>빨간 점: 구장 · 파란 점: 선택 숙소 · 점선: 직선 연결 (실제 길찾기 아님)</p>
        <p className={styles.status}>{mapStatus}{selection && mapState ? " · 숙소 좌표 반영됨" : ""}</p>
      </section>
    </div>
    <aside className={styles.notice}>
      <p role="status">{storageStatus}</p>
      <p>이 화면은 표시 가능성을 확인하는 실험입니다. 기존 코스 저장·챗봇·길찾기 API에는 전송하지 않습니다. Google 카드의 분류가 국내 숙박업 신고 분류와 일치하는지도 별도로 확인해야 합니다.</p>
    </aside>
  </main>;
}
