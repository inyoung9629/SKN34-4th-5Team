"use client";

import { useEffect, useRef, useState } from "react";
import { loadKakaoMaps, type KakaoMap, type KakaoMaps } from "@/lib/kakao-maps";
import { areValidCoordinates, type RouteStop } from "@/lib/routes";
import { coursePointLabel } from "@/lib/drawn-course";
import { useCourseDirections, useTravelOverlay } from "./course-travel";

export function RouteCardMap({ stops }: { stops: RouteStop[] }) {
  const host = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [sdk, setSdk] = useState<KakaoMaps | null>(null);
  const [map, setMap] = useState<KakaoMap | null>(null);
  const [error, setError] = useState(false);
  const travel = useCourseDirections(stops, visible);
  useTravelOverlay(map, sdk, travel);

  useEffect(() => {
    if (!host.current) return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect(); }
    }, { rootMargin: "150px" });
    observer.observe(host.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!visible || !host.current) return;
    let cancelled = false;
    const node = host.current;
    loadKakaoMaps().then(maps => {
      if (cancelled) return;
      const first = stops.find(stop => areValidCoordinates(stop.lat, stop.lng));
      if (!first) { setError(true); return; }
      const instance = new maps.Map(node, { center: new maps.LatLng(first.lat, first.lng), level: 4, draggable: false, scrollwheel: false, disableDoubleClickZoom: true });
      setSdk(maps); setMap(instance);
    }).catch(() => { if (!cancelled) setError(true); });
    return () => { cancelled = true; node.replaceChildren(); };
  }, [visible, stops]);

  useEffect(() => {
    if (!map || !sdk || !host.current) return;
    const points = stops.filter(stop => areValidCoordinates(stop.lat, stop.lng));
    const pins = points.map((stop) => {
      const label = document.createElement("span");
      label.className = "route-thumbnail-pin";
      label.textContent = coursePointLabel(stops, stops.indexOf(stop));
      return new sdk.CustomOverlay({ map, position: new sdk.LatLng(stop.lat, stop.lng), content: label, yAnchor: 0.5, zIndex: 5 });
    });
    const fit = () => {
      map.relayout();
      const bounds = new sdk.LatLngBounds();
      [...points, ...(travel.data?.legs.flatMap(leg => leg.paths.flat()) ?? [])].forEach(point => bounds.extend(new sdk.LatLng(point.lat, point.lng)));
      if (points.length > 1) map.setBounds(bounds, 42, 28, 48, 28);
    };
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(host.current);
    return () => { observer.disconnect(); pins.forEach(pin => pin.setMap(null)); };
  }, [map, sdk, stops, travel.data]);

  return <div className="route-thumbnail" role="img" aria-label="코스의 방문 지점과 이동 경로 지도">
    <div ref={host} className="route-thumbnail-map" aria-hidden="true" />
    {(error || travel.error) && <span className="route-thumbnail-status">{error ? "지도 미리보기를 불러오지 못했어요" : "이동 경로를 불러오지 못했어요"}</span>}
  </div>;
}
