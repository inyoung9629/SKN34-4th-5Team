"use client";

import { useEffect, useState } from "react";
import { fetchStadiumFacilities, filterStadiumFacilities, type FacilityPin, type StadiumFacilities, type StadiumFacility } from "@/lib/stadium-facilities";
import styles from "./stadium-facility-list.module.css";

export function useStadiumFacilities(code: string, enabled = true) {
  const [result, setResult] = useState<{code: string; data?: StadiumFacilities; error?: string}>({code});
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    fetchStadiumFacilities(code, controller.signal).then(data => {
      if (!controller.signal.aborted) setResult({code, data});
    }).catch(error => {
      if (!controller.signal.aborted) setResult({code, error: error instanceof Error ? error.message : "구장 시설 조회 실패"});
    });
    return () => controller.abort();
  }, [code, enabled]);
  return enabled && result.code === code ? result : { code };
}

export function StadiumFacilityList({ data, error, query, onSelect, selectedId }: {
  data?: StadiumFacilities; error?: string; query: string;
  onSelect: (row: StadiumFacility, pin: FacilityPin) => void; selectedId?: string;
}) {
  const [scope, setScope] = useState("all");
  const [kind, setKind] = useState("all");
  const [limit, setLimit] = useState(30);
  if (error) return <p role="alert">{error} 다른 출처로 자동 대체하지 않아요.</p>;
  if (!data) return <p role="status">구장 소속 먹거리·시설을 불러오는 중…</p>;
  const rows = filterStadiumFacilities(data.records, query, scope, kind);
  return <section className={styles.panel} aria-label="구장 소속 먹거리와 시설">
    <p>주변 독립 상점과 구분한 구장 소속 목록 · {data.count}건 / 근사 핀 {data.pinCount}개</p>
    <div className={styles.filters}>
      <label>위치 <select aria-label="구장 내외부 구분" value={scope} onChange={e => {setScope(e.target.value); setLimit(30);}}><option value="all">전체</option><option value="internal">구장 내부</option><option value="exterior">구장 외부 부속</option><option value="unknown">내외부 미확인</option></select></label>
      <label>종류 <select aria-label="구장 시설 종류" value={kind} onChange={e => {setKind(e.target.value); setLimit(30);}}><option value="all">먹거리·시설</option><option value="food">먹거리</option><option value="facility">편의시설</option></select></label>
    </div>
    <p className={styles.warning}>{data.warning}</p>
    <p role="status">검색 결과 {rows.length}건</p>
    <ul className={styles.list}>{rows.slice(0,limit).map(row => <li key={row.id}>
      <strong>{row.name}</strong><span className={styles.badge}>{row.scopeLabel}</span>
      <p>{row.floor} · {row.zone}</p>
      {row.pins.length ? row.pins.map(pin => <button type="button" key={pin.id} aria-pressed={selectedId === pin.id} onClick={() => onSelect(row,pin)}>{pin.label} · 근사 위치 보기</button>) : <small>구역만 확인 · 매장 좌표 미확인</small>}
      <small>원본 확인 {row.sourceCheckedAt} · 현재 영업 미확인</small>
      <a href={row.sourceUrl} target="_blank" rel="noreferrer">자리어때 매장·구역 원문 ↗</a>
    </li>)}</ul>
    {rows.length > limit && <button type="button" onClick={() => setLimit(limit + 30)}>더 보기 ({Math.min(limit,rows.length)}/{rows.length})</button>}
  </section>;
}
