"use client";

import { useMemo } from "react";
import { CourseShareButton } from "./course-share-button";
import { RouteCardMap } from "./route-card-map";
import Link from "next/link";
import { type TripRoute } from "@/lib/routes";
import { RouteLikeButton } from "./route-like-button";
import { useMemberAuth } from "@/lib/member-auth";
import { withCourseStart } from "@/lib/drawn-course";
import { useRouteNumber } from "@/lib/route-number";

export function formatRouteDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "날짜 미정" : new Intl.DateTimeFormat("ko-KR", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: "Asia/Seoul" }).format(date);
}

function stadiumTeam(stadium: string) {
  const teams: [string, string][] = [["잠실", "LG · 두산"], ["고척", "키움"], ["인천", "SSG"], ["수원", "KT"], ["대전", "한화"], ["대구", "삼성"], ["광주", "KIA"], ["사직", "롯데"], ["창원", "NC"]];
  return teams.find(([name]) => stadium.includes(name))?.[1] ?? "KBO";
}

export function RouteCard({ route }: { route: TripRoute }) {
  const { status: authStatus } = useMemberAuth();
  const authenticated = authStatus === "authenticated";
  const routeNumber = useRouteNumber(route.id, route.routeNumber);
  const stops = useMemo(() => withCourseStart(route.stops, route.start), [route.stops, route.start]);
  return (
    <article className="route-card">
      <div className="route-card-link">
        <div className="route-card-image">
          <RouteCardMap stops={stops} />
          <span className="route-card-sample">{route.isSample ? "샘플 코스" : route.legacy ? "로컬 코스" : route.owned && authenticated ? "내 코스" : "팬 코스"}</span>
          <div className="route-card-image-caption"><span>{route.duration}</span><span>{stops.length}개 장소</span></div>
        </div>
        <div className="route-card-content">
          <div className="route-card-badges"><span className="route-stadium-pill">{route.stadium}</span><span className="route-team-badge">{stadiumTeam(route.stadium)}</span></div>
          <h3><Link className="route-card-title-link" href={`/routes/${encodeURIComponent(route.id)}`}>{route.title}</Link></h3>
          {!route.isSample && <CourseShareButton route={route} />}
          <span className="route-public-number">루트 번호 {routeNumber}</span>
          <p className="route-card-stops">{stops.map(stop => stop.name).join(" → ")}</p>
          <div className="route-card-tags">{route.tags.slice(0, 3).map(tag => <span key={tag}>#{tag}</span>)}</div>
          <RouteLikeButton route={route}>{({ button, notice }) => <>
          <div className="route-card-footer">
            <span className="route-card-author"><span className="route-avatar" aria-hidden="true">{route.isSample ? "K" : route.owned && authenticated ? "나" : "팬"}</span><span>{route.author}<time dateTime={route.createdAt}>{formatRouteDate(route.createdAt)}</time></span></span>
            <div className="route-card-metrics">{button}<span className="route-card-views" aria-label={`조회 ${route.views ?? 0}회`}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M2 12s3.7-7 10-7 10 7 10 7-3.7 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>{route.views ?? 0}</span></div>
          </div>
          {notice}
          </>}</RouteLikeButton>
        </div>
      </div>
    </article>
  );
}
