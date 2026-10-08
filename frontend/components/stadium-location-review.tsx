"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { loadKakaoMaps, type KakaoMaps, type KakaoOverlay } from "@/lib/kakao-maps";
import { addressGeocodedStadiums, stadiums } from "@/lib/stadiums";
import { stadiumLocationAudit } from "@/lib/stadium-locations";
import { stadiumBoundaries } from "@/lib/stadium-boundaries";
import { stadiumBoundaryFrame } from "@/lib/stadium-boundary-frame";
import { stadiumComplexReviews, stadiumComplexReviewRings } from "@/lib/stadium-complex-reviews";
import { distanceMeters, NEARBY_RADIUS } from "@/lib/nearby-places";
import styles from "./stadium-location-review.module.css";

type Code = keyof typeof stadiumLocationAudit.stadiums;

function ReviewMap({ maps, code, wide, satellite, compare }: { maps: KakaoMaps; code: Code; wide: boolean; satellite: boolean; compare: boolean }) {
  const node = useRef<HTMLDivElement>(null);
  const stadium = stadiums.find(item => item.code === code)!;
  const original = addressGeocodedStadiums.find(item => item.code === code)!;
  const point = stadiumLocationAudit.stadiums[code];
  useEffect(() => {
    const element = node.current;
    if (!element) return;
    // Give every SDK instance its own host. In development React can mount an
    // effect twice; a retired SDK instance must never write late tiles into the
    // container used by the next instance.
    const viewport = document.createElement("div");
    viewport.style.width = "100%";
    viewport.style.height = "100%";
    element.replaceChildren(viewport);
    const map = new maps.Map(viewport, { center: new maps.LatLng(point.lat, point.lng), level: wide ? 7 : 4, scrollwheel: false });
    map.setMapTypeId(satellite ? maps.MapTypeId.HYBRID : maps.MapTypeId.ROADMAP);
    const overlays: KakaoOverlay[] = [];
    function marker(name: string, lat: number, lng: number, kind: string) {
      const label = document.createElement("div"); label.className = `${styles.pin} ${styles[kind]}`;
      label.textContent = name;
      if (kind === "current") { label.textContent = "⚾"; label.setAttribute("aria-label", `${stadium.name} 1군 구장 중심점`); label.title = `${stadium.name} 1군 구장 중심점`; }
      overlays.push(new maps.CustomOverlay({ map, position: new maps.LatLng(lat, lng), content: label, xAnchor: .5, yAnchor: 1, zIndex: kind === "current" ? 10 : 3 }));
    }
    if (wide) overlays.push(new maps.Circle({ map, center: new maps.LatLng(point.lat, point.lng), radius: NEARBY_RADIUS, strokeWeight: 2, strokeColor: "#2367c8", strokeOpacity: .8, strokeStyle: "dash", fillColor: "#3b82f6", fillOpacity: .06 }));
    // Keep a rectangle where it fits; otherwise trim unused corners using only
    // reviewed stadium/field vertices. Never use a nearby independent venue.
    const frame = stadiumBoundaryFrame(point, stadiumBoundaries.stadiums[code].rings, stadiumLocationAudit.boundaryPaddingM);
    const complex = stadiumComplexReviewRings(code, frame.path);
    const framePath = (complex?.hole ?? frame.path).map(([lat,lng]) => new maps.LatLng(lat,lng));
    if (complex) {
      // Outline only: leave the basemap's light-blue fill visible so the trace
      // can be compared directly. The unchanged green frame is the inner edge.
      for (const outer of complex.outers) {
        const outerPath = outer.map(([lat,lng]) => new maps.LatLng(lat,lng));
        overlays.push(new maps.Polyline({ map, path: outerPath, strokeWeight: 3, strokeColor: "#dc2626", strokeOpacity: 1, strokeStyle: "solid" }));
      }
    }
    overlays.push(new maps.Polyline({ map, path: framePath, strokeWeight: 6, strokeColor: "#fff", strokeOpacity: .95, strokeStyle: "solid" }));
    overlays.push(new maps.Polyline({ map, path: framePath, strokeWeight: 3, strokeColor: "#168463", strokeOpacity: 1, strokeStyle: "solid" }));
    marker(`⚾ ${stadium.name} · 1군`, point.lat, point.lng, "current");
    if (compare) marker("기존 주소 좌표", original.lat, original.lng, "previous");
    // Other venue names remain on the basemap; large custom labels would hide
    // the blue complex edge that this page is meant to compare.
    const fit = () => {
      map.relayout();
      const bounds = new maps.LatLngBounds();
      const vertices = wide ? [[point.lat-.02246,point.lng-.02246/Math.cos(point.lat*Math.PI/180)],[point.lat+.02246,point.lng+.02246/Math.cos(point.lat*Math.PI/180)]] : [...(complex?.outers.flat() ?? []), ...frame.path];
      vertices.forEach(([lat,lng]) => bounds.extend(new maps.LatLng(lat,lng)));
      const padding = !wide ? 16 : 50;
      map.setBounds(bounds, padding, padding, padding, padding);
    };
    fit();
    const observer = new ResizeObserver(fit); observer.observe(element);
    return () => { observer.disconnect(); overlays.forEach(overlay => overlay.setMap(null)); viewport.remove(); };
  }, [maps, code, point, original, stadium, wide, satellite, compare]);
  return <div ref={node} className={styles.map} aria-label={`${stadium.name} ${!wide ? "메인 구장과 단지 경계 검토 지도" : "1군 야구장 핀과 반경 2.5km 지도"}`} />;
}

export function StadiumLocationReview({ initialCode = "JAMSIL" }: { initialCode?: Code }) {
  const [code, setCode] = useState<Code>(initialCode);
  const [wide, setWide] = useState(false);
  const [satellite, setSatellite] = useState(false);
  const [compare, setCompare] = useState(false);
  const [maps, setMaps] = useState<KakaoMaps | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { let active = true; loadKakaoMaps().then(value => { if (active) setMaps(value); }).catch(reason => { if (active) setError(String(reason instanceof Error ? reason.message : reason)); }); return () => { active = false; }; }, []);
  const point = stadiumLocationAudit.stadiums[code];
  const frame = stadiumBoundaryFrame(point, stadiumBoundaries.stadiums[code].rings, stadiumLocationAudit.boundaryPaddingM);
  const current = stadiums.find(item => item.code === code)!;
  const complexReview = stadiumComplexReviews[code];
  return <main className={styles.page}>
    <Link href="/routes/new">← 직관 코스 만들기</Link>
    <h1>1군 야구장 위치 확인</h1>
    <p><Link href="/dev/stadium-food">구장 내부·외부 먹거리 핀 검토 →</Link></p>
    <p>그라운드만이 아니라 구장 건물 전체·관중석·확인된 부속동과 인접 출입 공간을 포함하는 확인 범위입니다. 공식 안내도·사진과 공개 시설 좌표를 대조했으며, 입장 게이트·측량 좌표는 아닙니다.</p>
    <div className={styles.tabs} role="group" aria-label="구장 선택">{stadiums.map(stadium => <button type="button" key={stadium.code} aria-pressed={code === stadium.code} onClick={() => setCode(stadium.code as Code)}>{stadium.name}</button>)}</div>
    <div id="stadium-review-map" className={styles.toolbar}><strong>{current.name}</strong><div className={styles.controls}><button type="button" aria-pressed={satellite} onClick={() => setSatellite(!satellite)}>{satellite ? "일반 지도 보기" : "위성 지도 보기"}</button><button type="button" aria-pressed={compare} onClick={() => setCompare(!compare)}>이전 핀 비교</button><button type="button" onClick={() => setWide(!wide)}>{wide ? "구장 확대" : "반경 전체 보기"}</button></div></div>
    {maps ? <ReviewMap maps={maps} code={code} wide={wide} satellite={satellite} compare={compare} /> : <div className={styles.map} role="status">{error || "지도 연결 중…"}</div>}
    <p className={styles.legend}>파랑: 1군 구장 핀{wide ? "·반경 2.5km" : ""}　초록: 1군 구장 내부 분류(+{stadiumLocationAudit.boundaryPaddingM}m 인접 출입 공간 여유)　{complexReview && <><strong style={{ color: "#b91c1c" }}>빨강: 하늘색 단지의 외곽 · 초록 내부를 뺀 나머지는 검색 제외</strong>　</>}회색: 이전 핀 비교 시 표시</p>
    {complexReview && <><p>빨간 테두리는 카카오 일반 지도에 보이는 하늘색 단지 외곽을 따라 작성한 검토용 경계입니다. 초록 테두리 안의 먹거리·시설은 1군 구장 내부로 분류하고, 빨간 테두리 안에서 초록 영역을 뺀 곳은 검색·코스 후보에서 제외합니다. 경계선 위를 포함하며 두 영역이 겹치면 초록을 우선합니다. 기존 초록선은 출입 공간 여유 때문에 일부 빨간 외곽과 겹치거나 바깥으로 나갈 수 있으며, 초록선을 빨간선에 맞춰 줄이지 않았습니다. 카카오가 제공한 공식 경계 좌표나 법적 부지 경계는 아닙니다. <strong>동일한 경계를 지도 검색·챗봇 후보·RAG 조회에 적용했습니다.</strong></p><p>{complexReview.note} · 외곽 검토일 {complexReview.checkedAt}</p></>}
    <p>현재 테두리: {frame.shape === "rectangle" ? "사각형" : "빈 모서리를 줄인 다각형"}. 구장 외곽의 확인 좌표를 모두 포함하는 표시 영역이며 법적 부지 경계는 아닙니다. 공유 주차장·종합운동장 전체를 구장 시설로 합치지 않으며, 작은 빈 공간까지 제외한 정밀 경계는 아닙니다. 위성 사진은 촬영 시점에 따라 현재 시설과 차이가 있을 수 있습니다.</p>
    <p>{point.note} · 새 중심: {point.lat.toFixed(7)}, {point.lng.toFixed(7)}</p>
    <p>선택 제외 표시는 시설 혼동 방지용입니다. 그 시설의 모든 경기 종류나 현재 사용 상태를 확정하는 표시는 아닙니다. 각 경기의 실제 개최 구장 코드를 우선하며, 미등록 구장은 임의로 가까운 야구장에 연결하지 않습니다.</p>
    <p><a href={point.officialUrl} target="_blank" rel="noreferrer">공식 시설 안내 ↗</a> · <a href={`https://www.openstreetmap.org/${point.osmType}/${point.osmId}`} target="_blank" rel="noreferrer">시설 지도 원본 ↗</a> · <a href={stadiumLocationAudit.licenseUrl} target="_blank" rel="noreferrer">© OpenStreetMap contributors · ODbL</a> · 확인일 {stadiumLocationAudit.checkedAt}</p>
    {"boundarySources" in point && <p>경계 확인 자료: {point.boundarySources.map((url, index) => <a key={url} href={url} target="_blank" rel="noreferrer">{index > 0 ? " · " : ""}시설 요소 {index + 1} ↗</a>)}</p>}
    {"reviewSources" in point && <p>안내도·전경·시설 문서: {point.reviewSources.map((url, index) => <a key={url} href={url} target="_blank" rel="noreferrer">{index > 0 ? " · " : ""}대조 자료 {index + 1} ↗</a>)}</p>}
    <p>이전 반경으로 수집한 데이터는 새 중심으로 재필터링합니다. 새 반경 중 기존 수집 원 바깥은 아직 미수집입니다.</p>
    <table className={styles.table}><caption>9개 구장 중심점 변경 요약 · 구장명을 누르면 해당 지도를 표시합니다.</caption><thead><tr><th>구장</th><th>좌표 이동</th><th>확인 기준</th></tr></thead><tbody>{stadiums.map(stadium => <tr key={stadium.code}><td><button type="button" onClick={() => setCode(stadium.code as Code)}>{stadium.name}</button></td><td>{Math.round(distanceMeters(stadium, addressGeocodedStadiums.find(item => item.code === stadium.code)!))}m</td><td>{stadiumLocationAudit.stadiums[stadium.code as Code].note}</td></tr>)}</tbody></table>
  </main>;
}
