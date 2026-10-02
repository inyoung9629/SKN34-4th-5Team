"use client";

export type MapCoordinate = { getLat(): number; getLng(): number };
type Bounds = { extend(point: MapCoordinate): void };
export type KakaoMap = {
  getCenter(): MapCoordinate;
  getLevel(): number;
  getBounds(): { getSouthWest(): MapCoordinate; getNorthEast(): MapCoordinate };
  setLevel(level: number): void;
  setMapTypeId(type: number): void;
  setCursor(cursor: string): void;
  getProjection(): { containerPointFromCoords(point: MapCoordinate): { x: number; y: number }; coordsFromContainerPoint(point: { x: number; y: number }): MapCoordinate };
  setCenter(point: MapCoordinate): void;
  setBounds(bounds: Bounds, top?: number, right?: number, bottom?: number, left?: number): void;
  relayout(): void;
};
export type KakaoPlace = { id: string; place_name: string; road_address_name: string; address_name: string; category_group_name: string; category_group_code?: string; category_name?: string; phone?: string; x: string; y: string };
export type PlaceSearchOptions = { location: MapCoordinate; size: number; radius?: number; page?: number; sort?: string; category_group_code?: string };
export type PlaceSearchCallback = (places: KakaoPlace[], status: string, pagination: { hasNextPage: boolean }) => void;
export type KakaoOverlay = { setMap(map: KakaoMap | null): void };
export type MapClickEvent = { latLng: MapCoordinate };
export type KakaoMaps = {
  MapTypeId: { ROADMAP: number; HYBRID: number };
  load(callback: () => void): void;
  Map: new (element: HTMLElement, options: { center: MapCoordinate; level: number; draggable?: boolean; scrollwheel?: boolean; disableDoubleClickZoom?: boolean }) => KakaoMap;
  LatLng: new (lat: number, lng: number) => MapCoordinate;
  Point: new (x: number, y: number) => { x: number; y: number };
  LatLngBounds: new () => Bounds;
  CustomOverlay: new (options: { map: KakaoMap; position: MapCoordinate; content: HTMLElement; xAnchor?: number; yAnchor?: number; zIndex?: number; clickable?: boolean }) => KakaoOverlay;
  Polyline: new (options: { map: KakaoMap; path: MapCoordinate[]; strokeWeight: number; strokeColor: string; strokeOpacity: number; strokeStyle: string; endArrow?: boolean }) => KakaoOverlay;
  Circle: new (options: { map: KakaoMap; center: MapCoordinate; radius: number; strokeWeight: number; strokeColor: string; strokeOpacity: number; strokeStyle: string; fillColor: string; fillOpacity: number }) => KakaoOverlay;
  event: { preventMap(): void; addListener(target: KakaoMap, event: string, callback: (event: MapClickEvent) => void): void; removeListener(target: KakaoMap, event: string, callback: (event: MapClickEvent) => void): void };
  services: { Places: new () => { keywordSearch(query: string, callback: PlaceSearchCallback, options: PlaceSearchOptions): void; categorySearch(code: string, callback: PlaceSearchCallback, options: PlaceSearchOptions): void }; Status: { OK: string; ZERO_RESULT: string }; SortBy: { DISTANCE: string; ACCURACY: string } };
};

declare global { interface Window { kakao?: { maps: KakaoMaps } } }
let loading: Promise<KakaoMaps> | undefined;

export function loadKakaoMaps(options?: { referrerPolicy?: ReferrerPolicy }): Promise<KakaoMaps> {
  if (window.kakao?.maps?.Map) return Promise.resolve(window.kakao.maps);
  if (loading) return loading;
  const key = process.env.NEXT_PUBLIC_KAKAO_MAP_KEY?.trim();
  if (!key) return Promise.reject(new Error("지도를 연결할 수 없어요. 지도 설정을 확인한 뒤 다시 시도해 주세요."));
  loading = new Promise<KakaoMaps>((resolve, reject) => {
    const script = document.createElement("script");
    let settled = false;
    const fail = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      script.remove();
      loading = undefined;
      reject(new Error("지도를 불러오지 못했어요. 잠시 후 다시 시도하거나 카카오맵에서 확인해 주세요."));
    };
    const timeout = setTimeout(fail, 12000);
    script.async = true;
    script.referrerPolicy = options?.referrerPolicy ?? "no-referrer";
    script.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${encodeURIComponent(key)}&autoload=false`;
    script.onerror = fail;
    script.onload = () => {
      if (!window.kakao?.maps?.load) { fail(); return; }
      window.kakao.maps.load(() => {
        if (settled) return;
        if (!window.kakao?.maps?.Map) { fail(); return; }
        settled = true;
        clearTimeout(timeout);
        resolve(window.kakao.maps);
      });
    };
    document.head.appendChild(script);
  });
  return loading;
}
