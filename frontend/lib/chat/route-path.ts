import type { ChatOrigin, ChatRoutePath } from "./types";
import type { CourseDirections } from "../course-directions";

const same = (a: ChatOrigin, b: ChatOrigin) => a.lat === b.lat && a.lng === b.lng;

/** Loading/refreshed whole-route geometry is not a user's new segment selection. */
export function chatRouteSelectionKey(path: ChatRoutePath | undefined): string | undefined {
  return path?.label.startsWith("선택한 ") ? path.label : undefined;
}

/** 경로의 굴곡을 남기는 Douglas–Peucker 축약. 요청 크기를 128개 좌표로 제한한다. */
function simplify(points: ChatOrigin[], tolerance: number): ChatOrigin[] {
  if (points.length <= 2) return points;
  const first = points[0], last = points.at(-1)!;
  const scale = Math.cos(first.lat * Math.PI / 180);
  const dx = (last.lng - first.lng) * scale, dy = last.lat - first.lat;
  const length = dx * dx + dy * dy;
  let farthest = 0, index = 0;
  for (let i = 1; i < points.length - 1; i++) {
    const x = (points[i].lng - first.lng) * scale, y = points[i].lat - first.lat;
    const t = length ? Math.max(0, Math.min(1, (x * dx + y * dy) / length)) : 0;
    const distance = Math.hypot(x - t * dx, y - t * dy) * 111195;
    if (distance > farthest) { farthest = distance; index = i; }
  }
  return farthest <= tolerance ? [first, last] : [...simplify(points.slice(0, index + 1), tolerance).slice(0, -1), ...simplify(points.slice(index), tolerance)];
}

export function chatRoutePath(points: ChatOrigin[], data: CourseDirections | undefined, selectedLeg: number | null, drawn: boolean): ChatRoutePath | undefined {
  if (points.length < 2) return undefined;
  const legs = selectedLeg === null ? data?.legs : data?.legs.slice(selectedLeg, selectedLeg + 1);
  const hasDirections = legs?.length && legs.every(leg => leg.status === "ok" && leg.paths.length > 0);
  if (!hasDirections && !drawn) return undefined;
  const raw = hasDirections ? legs.flatMap(leg => leg.paths) : [selectedLeg === null ? points : points.slice(selectedLeg, selectedLeg + 2)];
  const paths: ChatOrigin[][] = [];
  for (const path of raw) {
    const unique = path.map(({ lat, lng }) => ({ lat, lng })).filter((point, i, all) => !i || !same(point, all[i - 1]));
    if (!unique.length) continue;
    const previous = paths.at(-1);
    if (previous && same(previous.at(-1)!, unique[0])) previous.push(...unique.slice(1));
    else paths.push(unique);
  }
  if (paths.flat().length < 2) return undefined;
  let compact = paths, tolerance = 15;
  while (compact.flat().length > 128 && tolerance < 1e7) { compact = paths.map(path => simplify(path, tolerance)); tolerance *= 2; }
  if (compact.flat().length > 128) return undefined;
  let offset = 0;
  const breaks = compact.slice(0, -1).map(path => (offset += path.length));
  return { points: compact.flat(), breaks, source: hasDirections ? "directions" : "drawn", label: selectedLeg === null ? "지도에 만든 전체 경로" : `선택한 ${selectedLeg + 1}번 구간` };
}
