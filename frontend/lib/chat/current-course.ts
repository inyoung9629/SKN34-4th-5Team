import type { RouteStop } from "../routes";
import { coursePointLabel } from "../drawn-course";
import type { LegModes, TravelMode } from "../course-directions";
import { activeLegModes } from "../course-directions";
import type { ChatCurrentCourse, ChatCoursePlace, ChatOrigin, CourseWriterState } from "./types";
import { courseVisitId } from "./course";

const categories: Record<string, ChatCoursePlace["category"]> = {
  "먹거리": "FOOD", "카페·디저트": "CAFE", "명소·산책": "SPOT", "관광 명소": "SPOT",
  "야구장": "STADIUM", "숙박": "STAY", "산책": "WALK", "실내 놀거리": "INDOOR",
  "편의점": "CONVENIENCE",
};

export function courseCategory(stop: RouteStop): ChatCoursePlace["category"] {
  if (stop.coursePlace?.category) return stop.coursePlace.category;
  if (categories[stop.category]) return categories[stop.category];
  if (stop.placeId?.startsWith("stadium-facility:SC_FOOD_")) {
    if (/편의점|GS25|세븐일레븐|이마트24|\bCU\b/i.test(stop.name)) return "CONVENIENCE";
    if (/카페|커피|coffee|cafe|스타벅스|이디야|투썸|공차|탐앤탐스|폴바셋|파스쿠찌/i.test(stop.name)) return "CAFE";
    return "FOOD";
  }
  return stop.coursePlace?.category ?? categories[stop.category] ?? "SPOT";
}

/** 대화 내용이 아닌 지금 지도에서 편집 중인 순서와 장소가 수정의 기준이다. */
export function currentCourse(stops: RouteStop[], stadiumCode: string, travelMode: TravelMode, legModes: LegModes,
  start?: ChatOrigin, writerState?: CourseWriterState): ChatCurrentCourse | undefined {
  const gameIndex = stops.findIndex(stop => stop.coursePlace?.category === "STADIUM" || stop.category === "야구장");
  const places = stops.flatMap((stop, index) => {
    if (index === 0 && (stop.placeId === "route:origin" || (!start && stop.isMapPoint))) return [];
    return [{ ...stop.coursePlace, visitId: courseVisitId(stop, index), label: coursePointLabel(stops, index, Boolean(start)),
      name: stop.name, lat: stop.lat, lng: stop.lng, placeId: stop.placeId, address: stop.address,
      category: courseCategory(stop),
      phase: (index === gameIndex ? "GAME" : gameIndex >= 0 && index > gameIndex ? "AFTER" : "BEFORE") as ChatCoursePlace["phase"] }];
  });
  // 출발 핀만 있거나 방문지를 모두 지운 화면도 이전 대화보다 최신인 편집 상태다.
  if (!places.length && !writerState) return undefined;
  return { places, stadiumCode, travelMode, legModes: activeLegModes(start ? [start, ...stops] : stops, legModes),
    ...(writerState ? { writerState } : {}),
    ...(gameIndex >= 0 && stops[gameIndex].courseProgress ? { progress: stops[gameIndex].courseProgress } : {}),
    ...(gameIndex >= 0 && stops[gameIndex].courseGame ? { game: stops[gameIndex].courseGame } : {}) };
}
