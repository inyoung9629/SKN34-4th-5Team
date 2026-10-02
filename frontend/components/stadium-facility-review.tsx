"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { stadiums } from "@/lib/stadiums";
import { stadiumBoundaries } from "@/lib/stadium-boundaries";
import { stadiumLocationAudit } from "@/lib/stadium-locations";
import { stadiumBoundaryFrame } from "@/lib/stadium-boundary-frame";
import { loadKakaoMaps, type KakaoMaps, type KakaoOverlay } from "@/lib/kakao-maps";
import type { FacilityPin, StadiumFacility } from "@/lib/stadium-facilities";
import { StadiumFacilityList, useStadiumFacilities } from "./stadium-facility-list";
import styles from "./stadium-facility-review.module.css";

type Selection = { row: StadiumFacility; pin: FacilityPin };
function FacilityMap({code, records, selected, onSelect}: {code: string; records: StadiumFacility[]; selected?: Selection; onSelect: (selection: Selection) => void}) {
  const node = useRef<HTMLDivElement>(null);
  const [maps, setMaps] = useState<KakaoMaps>();
  const [error, setError] = useState("");
  const handleSelect = useRef(onSelect);
  useEffect(() => {handleSelect.current = onSelect;}, [onSelect]);
  useEffect(() => {let active = true; loadKakaoMaps().then(m => {if(active) setMaps(m);}).catch(e => {if(active) setError(String(e));}); return () => {active = false;};}, []);
  useEffect(() => {
    if (!maps || !node.current) return;
    const element = node.current;
    const stadium = stadiums.find(s => s.code === code)!;
    const key = code as keyof typeof stadiumBoundaries.stadiums;
    const map = new maps.Map(element, {center: new maps.LatLng(stadium.lat,stadium.lng),level:2,scrollwheel:false});
    map.setMapTypeId(maps.MapTypeId.HYBRID);
    const overlays: KakaoOverlay[] = [];
    const frame = stadiumBoundaryFrame(stadiumLocationAudit.stadiums[key],stadiumBoundaries.stadiums[key].rings,0);
    overlays.push(new maps.Polyline({map,path:frame.path.map(([lat,lng])=>new maps.LatLng(lat,lng)),strokeWeight:3,strokeColor:"#168463",strokeOpacity:1,strokeStyle:"solid"}));
    const points = records.flatMap(row => row.pins.map(pin => ({row,pin})));
    for (const {row,pin} of points) {
      const button = document.createElement("button");
      button.type = "button"; button.className = `${styles.dot} ${selected?.pin.id === pin.id ? styles.active : ""}`;
      button.title = `${row.name} · ${row.floor} · ${pin.label}`;
      button.setAttribute("aria-label",button.title);
      button.onclick = event => {event.stopPropagation(); maps.event.preventMap(); handleSelect.current({row,pin});};
      overlays.push(new maps.CustomOverlay({map,position:new maps.LatLng(pin.lat,pin.lng),content:button,xAnchor:.5,yAnchor:.5,clickable:true,zIndex:selected?.pin.id === pin.id ? 10 : 3}));
    }
    if (selected) {
      const pin = selected.pin;
      overlays.push(new maps.Circle({map,center:new maps.LatLng(pin.lat,pin.lng),radius:pin.uncertaintyM,strokeWeight:1,strokeColor:"#dd932b",strokeOpacity:.9,strokeStyle:"dash",fillColor:"#f2b64f",fillOpacity:.16}));
    }
    const fit = () => {map.relayout(); const bounds = new maps.LatLngBounds(); frame.path.forEach(([lat,lng])=>bounds.extend(new maps.LatLng(lat,lng))); map.setBounds(bounds,45,45,45,45);};
    fit(); const observer = new ResizeObserver(fit); observer.observe(element);
    return () => {observer.disconnect(); overlays.forEach(o=>o.setMap(null)); element.replaceChildren();};
  },[maps,code,records,selected]);
  return <><div ref={node} className={styles.map} aria-label="구장 소속 먹거리 근사 핀 지도" />{error && <p role="alert">{error}</p>}</>;
}

const EMPTY: StadiumFacility[] = [];
export function StadiumFacilityReview() {
  const [code,setCode] = useState("JAMSIL");
  const [query,setQuery] = useState("");
  const [floor,setFloor] = useState("all");
  const [selection,setSelection] = useState<Selection>();
  const result = useStadiumFacilities(code);
  const allRecords = result.data?.records ?? EMPTY;
  const records = useMemo(() => floor === "all" ? allRecords : allRecords.filter(row=>row.floor === floor), [floor,allRecords]);
  const selected = selection?.row.stadium === code && records.some(row=>row.id === selection.row.id) ? selection : undefined;
  return <main className={styles.page}>
    <Link href="/dev/stadium-locations">← 구장 범위 확인</Link><h1>구장 먹거리·시설 위치 검토</h1>
    <p>구장 내부 / 구장 외부 부속 / 내외부 미확인을 분리했습니다. 코스 자동 생성의 포함·제외 정책은 변경하지 않았습니다.</p>
    <div className={styles.tabs} role="group" aria-label="구장 선택">{stadiums.map(s=><button type="button" key={s.code} aria-pressed={code===s.code} onClick={()=>{setCode(s.code);setSelection(undefined);setFloor("all");setQuery("");}}>{s.name}</button>)}</div>
    <div className={styles.layout}><section>
      <label>지도에 표시할 층 <select value={floor} onChange={e=>setFloor(e.target.value)}><option value="all">전체 층</option>{[...new Set(allRecords.map(r=>r.floor))].sort().map(f=><option key={f} value={f}>{f || "층 미확인"}</option>)}</select></label>
      <FacilityMap code={code} records={records} selected={selected} onSelect={setSelection}/>
      <p>점: 매장 근사 위치 · 초록: 구장 전체 건물 확인 범위 · 노랑 원: 선택 핀의 검토 여유(실측 정확도 보증 아님). 층이 다르면 핀이 겹칠 수 있어요.</p>
      {selected && <section className={styles.selected} aria-label="선택한 구장 매장"><h2>{selected.row.name}</h2><p>{selected.row.scopeLabel} · {selected.row.floor} · {selected.row.zone}</p><p>{selected.pin.label} · {selected.pin.quality === "diagram_approximate" ? "안내도" : "통로·구역"} 기반 근사 위치 · 검토 여유 {selected.pin.uncertaintyM}m</p><p>{selected.pin.source.note}</p><a href={selected.row.sourceUrl} target="_blank" rel="noreferrer">자리어때 매장·구역 원문 ↗</a>{selected.pin.source.imageUrl && <> · <a href={selected.pin.source.imageUrl} target="_blank" rel="noreferrer">참고한 안내도 원본 ↗</a></>}</section>}
      <p>{result.data?.review.note}</p>
      {result.data?.review.source && <p><a href={result.data.review.source.pageUrl} target="_blank" rel="noreferrer">이 구장에서 확인한 자리어때 사진 페이지 ↗</a>{result.data.review.source.imageUrl && <> · <a href={result.data.review.source.imageUrl} target="_blank" rel="noreferrer">구장 전체 안내도 원본 ↗</a></>}</p>}
      <p>위치 확인에는 자리어때 안내도와 구장 건물 방향을 참고했습니다. 사진은 복제·재배포하지 않고 원문에 연결합니다. <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">건물 외곽 © OpenStreetMap contributors · ODbL</a></p>
    </section><aside><label className={styles.search}>구장 매장 검색<input type="search" value={query} onChange={e=>setQuery(e.target.value)} placeholder="매장명, 층, 1루·3루" /></label><StadiumFacilityList key={code} data={result.data} error={result.error} query={query} selectedId={selected?.pin.id} onSelect={(row,pin)=>{setFloor(row.floor);setSelection({row,pin});}}/></aside></div>
  </main>;
}
