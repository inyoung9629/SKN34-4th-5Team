"use client";

import { useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import { KAKAO_LODGING_FILTERS, lodgingDetailUrl, searchKakaoLodging, type KakaoLodgingFilter } from "@/lib/kakao-lodging";
import { kakaoLodgingReference, locatedStop } from "@/lib/google-lodging";
import { sameStop, type NearbyPlace, type NearbyStadium } from "@/lib/nearby-places";
import type { RouteStop } from "@/lib/routes";
import styles from "./kakao-lodging-panel.module.css";

export type KakaoLodgingPanelHandle = { search: (kind: KakaoLodgingFilter) => void };
type Props = { ref?: Ref<KakaoLodgingPanelHandle>; stadium?: NearbyStadium; stops: RouteStop[]; onSelect: (place: NearbyPlace) => void; onResults?: (places: NearbyPlace[]) => void };

export function KakaoLodgingPanel({ ref, stadium, stops, onSelect, onResults }: Props) {
  const [kind, setKind] = useState<KakaoLodgingFilter>("all");
  const [places, setPlaces] = useState<NearbyPlace[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("숙박 검색 버튼을 누르면 선택한 구장 주변을 조회해요.");
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const sequence = useRef(0);
  useEffect(() => () => { sequence.current++; controller.current?.abort(); }, [stadium?.code]);

  async function search(next: KakaoLodgingFilter, restore?: RouteStop) {
    if (!stadium) { setError("코스의 구장을 확인할 수 없어요. 코스 만들기에서 구장을 선택해 다시 검색해 주세요."); return; }
    const request = ++sequence.current;
    controller.current?.abort();
    const active = new AbortController(); controller.current = active;
    setKind(next); setLoading(true); setError(""); setMessage("숙소를 검색하고 있어요.");
    setPlaces([]);
    try {
      const result = await searchKakaoLodging(stadium, next, active.signal);
      if (request !== sequence.current || active.signal.aborted) return;
      setPlaces(result.places); onResults?.(result.places);
      setMessage(`${result.places.length}곳 · 구장에서 가까운 순${result.partial ? " · 일부 검색에 실패했어요. 다시 검색해 주세요." : ""}`);
      if (restore) {
        const match = result.places.find(place => sameStop(place, restore));
        if (match) onSelect({ ...match, placeId: restore.placeId!, ...(restore.visitId ? { visitId: restore.visitId } : {}) });
        else setError("이번 검색에서 저장한 숙소를 찾지 못했어요. 카카오맵 상세 링크로 확인하거나 다른 숙소를 선택해 주세요. 위치를 임의로 지정하지 않아요.");
      }
    } catch (reason) {
      if (request !== sequence.current || active.signal.aborted) return;
      setError(reason instanceof Error ? reason.message : "숙박 검색을 완료하지 못했어요."); setMessage("");
    } finally { if (request === sequence.current) setLoading(false); }
  }
  useImperativeHandle(ref, () => ({ search: next => { void search(next); } }));
  const references = stops.filter(stop => kakaoLodgingReference(stop) && !locatedStop(stop));

  return <section className={styles.panel} aria-label="숙박 찾기" aria-busy={loading}>
    <h3>숙박 찾기</h3>
    {stadium && <div className={styles.filters}>{KAKAO_LODGING_FILTERS.map(filter => <button type="button" key={filter.id} aria-pressed={kind === filter.id} disabled={loading} onClick={() => void search(filter.id)}>{filter.label}</button>)}</div>}
    {references.map(stop => <div className={styles.restore} key={stop.visitId ?? stop.placeId}>
      <button type="button" disabled={loading || !stadium} onClick={() => void search(kakaoLodgingReference(stop)!.kind, stop)}>코스 {stops.indexOf(stop) + 1}번 숙소 위치 확인</button>
      <a href={lodgingDetailUrl(stop)} target="_blank" rel="noreferrer">카카오맵 상세 ↗</a>
    </div>)}
    <p role="status">{message}</p>{error && <p role="alert">{error}</p>}
    <ul className={styles.results}>{places.map(place => <li key={place.placeId}>
      <button type="button" aria-label={`${place.name} 지도에서 선택`} onClick={() => onSelect(place)}>
        <strong>{place.name}</strong><span>{place.subcategory} · 구장에서 {Math.round(place.distance)}m</span><span>{place.address || "주소 정보 없음"}</span>
      </button>
      <a href={lodgingDetailUrl(place)} target="_blank" rel="noreferrer">상세 ↗</a>
    </li>)}</ul>
    <small>카카오맵 · 반경 2.5km · 검색어당 최대 45곳. 모든 숙소를 보장하지 않아요. 카카오가 모텔·여관을 통합 제공하면 구분을 단정하지 않아요.</small>
  </section>;
}
