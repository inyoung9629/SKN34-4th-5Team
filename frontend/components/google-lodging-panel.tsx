"use client";

import { useEffect, useImperativeHandle, useLayoutEffect, useRef, useState, type Ref } from "react";
import { loadGooglePlacesUiKit } from "@/lib/google-places-ui-kit";
import { googleLodgingId, googleLodgingStop, LODGING_CATEGORIES, LODGING_RADIUS_METERS, LODGING_SEARCH_LIMIT, lodgingResultMessage, type LodgingCategory } from "@/lib/google-lodging";
import { projectUiKitPlace, type UiKitPlace } from "@/lib/google-lodging-pilot";
import type { RouteStop } from "@/lib/routes";
import styles from "./google-lodging-panel.module.css";

type Props = {
  ref?: Ref<GoogleLodgingPanelHandle>;
  stadium?: { code: string; name: string; lat: number; lng: number };
  stops: RouteStop[];
  onSelect: (stop: RouteStop) => void;
};
export type GoogleLodgingPanelHandle = { search: (kind: LodgingCategory) => void };
type Widget = HTMLElement & { place?: UiKitPlace; places?: UiKitPlace[] };
const key = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY?.trim() ?? "";
// Shared across panel unmounts / stadium changes; not a billing-account quota.
let pageQueries = 0;
const LIMIT = 12;

export function GoogleLodgingPanel({ ref, stadium, stops, onSelect }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const operation = useRef(0);
  const pending = useRef(false);
  const cleanup = useRef<(() => void) | null>(null);
  const callback = useRef(onSelect);
  useLayoutEffect(() => { callback.current = onSelect; }, [onSelect]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("호텔·모텔·여관 중 원하는 분류를 선택해 주세요.");
  const [activeKind, setActiveKind] = useState<LodgingCategory | null>(null);
  const [count, setCount] = useState(0);
  useEffect(() => () => { operation.current++; cleanup.current?.(); }, []);
  useImperativeHandle(ref, () => ({ search: kind => { void query(kind); } }));

  async function query(kind: LodgingCategory | "id", id?: string) {
    if (!key || pending.current) return;
    if (pageQueries >= LIMIT) { setStatus("이 페이지의 숙박 조회 한도(12회)에 도달했어요. 새로고침하면 다시 조회할 수 있어요."); return; }
    if (kind !== "id" && !stadium) return;
    const run = ++operation.current;
    pending.current = true; setBusy(true); setActiveKind(kind === "id" ? null : kind); setStatus("숙박 정보를 불러오는 중…");
    cleanup.current?.(); host.current?.replaceChildren();
    const current = () => run === operation.current;
    const finish = (message: string) => {
      if (!current()) return;
      pending.current = false; setBusy(false); setStatus(message);
    };
    try {
      await loadGooglePlacesUiKit(key);
      if (!current() || !host.current) return;
      const widget = document.createElement(kind === "id" ? "gmp-place-details" : "gmp-place-search") as Widget;
      const content = document.createElement("gmp-place-content-config");
      const attribution = document.createElement("gmp-place-attribution");
      attribution.setAttribute("light-scheme-color", "gray");
      attribution.setAttribute("dark-scheme-color", "white");
      content.append(document.createElement("gmp-place-address"), document.createElement("gmp-place-type"), attribution);
      widget.append(content);
      const request = document.createElement(kind === "id" ? "gmp-place-details-place-request" : "gmp-place-nearby-search-request");
      if (kind === "id") request.setAttribute("place", id!);
      else {
        widget.setAttribute("selectable", "");
        widget.setAttribute("attribution-position", "bottom");
        request.setAttribute("included-primary-types", kind);
        request.setAttribute("max-result-count", String(LODGING_SEARCH_LIMIT));
        request.setAttribute("rank-preference", "DISTANCE");
        request.setAttribute("location-restriction", `${LODGING_RADIUS_METERS}@${stadium!.lat},${stadium!.lng}`);
      }
      widget.append(request);
      const select = (place?: UiKitPlace) => {
        if (!current()) return;
        const selected = projectUiKitPlace(place);
        if (!selected) { setStatus("좌표를 확인하지 못해 지도에 표시하지 않았어요."); return; }
        try { callback.current(googleLodgingStop(selected.id, selected.lat, selected.lng)); }
        catch { setStatus("장소 ID를 확인하지 못했어요."); return; }
        setStatus("선택한 숙소를 지도에 표시했어요. 지도에서 ‘코스에 담기’를 누르세요.");
      };
      const timeout = setTimeout(() => { cleanup.current?.(); finish("응답 시간이 초과됐어요. 자동 재시도하지 않아요."); }, 25000);
      const loaded = () => { clearTimeout(timeout); finish(kind === "id" ? "저장한 숙소를 조회했어요." : lodgingResultMessage(kind, widget.places?.length ?? 0)); if (kind === "id") select(widget.place); };
      const failed = () => { clearTimeout(timeout); cleanup.current?.(); finish("숙박 조회에 실패했어요. 잠시 후 다시 시도해 주세요."); };
      const chosen = (event: Event) => select((event as Event & { place?: UiKitPlace }).place);
      widget.addEventListener("gmp-load", loaded); widget.addEventListener("gmp-error", failed); widget.addEventListener("gmp-select", chosen);
      cleanup.current = () => {
        clearTimeout(timeout);
        widget.removeEventListener("gmp-load", loaded); widget.removeEventListener("gmp-error", failed); widget.removeEventListener("gmp-select", chosen); widget.remove();
      };
      pageQueries++; setCount(pageQueries);
      host.current.append(widget);
    } catch { finish("숙박 검색 연결을 확인해 주세요. 자동 재시도하지 않아요."); }
  }

  const references = stops.map((stop, index) => ({ id: googleLodgingId(stop), index })).filter(item => item.id);
  return <section className={styles.panel} aria-label="숙박 선택" aria-busy={busy}>
    <h3>숙박 찾기</h3>
    {stadium && <p>{stadium.name} 반경 {LODGING_RADIUS_METERS / 1000}km</p>}
    {!key && <p role="status">숙박 연결이 설정되지 않았어요. 다른 장소는 계속 이용할 수 있어요.</p>}
    <div className={styles.buttons} role="group" aria-label="숙박 분류">
      {stadium && LODGING_CATEGORIES.map(({ id, label }) => <button type="button" key={id} disabled={!key || busy} aria-pressed={activeKind === id} onClick={() => void query(id)}>{label}</button>)}
      {references.map(({ id, index }) => <button type="button" key={`${id}:${index}`} disabled={!key || busy} onClick={() => void query("id", id!)}>코스 {index + 1}번 숙소 확인</button>)}
    </div>
    <p role="status">{status}</p>
    {stadium && <p className={styles.note}>분류별 가까운 {LODGING_SEARCH_LIMIT}곳까지 검색하며, 반경 내 전체 목록은 아니에요.</p>}
    <div ref={host} className={styles.widget} />
    <details className={styles.note}><summary>검색·분류 안내</summary>
      <p>Google Maps에 등록된 분류로 검색해요. 여관은 ‘inn’ 기준이며 국내 신고 업종과 다를 수 있어요. 이름으로 업종을 추정하지 않아요.</p>
      <p>코스에는 장소 ID와 방문 순서만 저장해요. 다시 열 때 위 버튼으로 주소·분류·위치를 확인해 주세요.</p>
      <p>페이지 조회 {count}/{LIMIT}회 · 새로고침 시 초기화되며 결제 한도는 아니에요.</p>
    </details>
  </section>;
}
