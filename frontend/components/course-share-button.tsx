"use client";
import { useState } from "react";
import type { TripRoute } from "@/lib/routes";
import { withCourseStart } from "@/lib/drawn-course";
import { shareOrCopy } from "@/lib/browser-share";
import { googleLodgingId } from "@/lib/google-lodging";

export function CourseShareButton({ route }: { route: TripRoute }) {
  const [message, setMessage] = useState("");
  const [fallback, setFallback] = useState("");
  async function share() {
    const stops = withCourseStart(route.stops, route.start);
    const text = `${route.title}\n${route.stadium}\n\n${stops.map((stop, index) => googleLodgingId(stop) ? `${index + 1}. Google 숙소 · 저장된 코스에서 확인\n${window.location.origin}/routes/${encodeURIComponent(route.id)}` : `${index + 1}. ${stop.name}\nhttps://map.kakao.com/link/map/${encodeURIComponent(stop.name)},${stop.lat},${stop.lng}`).join("\n\n")}`;
    setMessage(""); setFallback("");
    const outcome = await shareOrCopy({ title: route.title, text }, text);
    if (outcome === "cancelled" || outcome === "shared") return;
    if (outcome === "copied") {
      setMessage("코스 내용을 복사했어요. 원하는 곳에 붙여 넣어 주세요.");
      return;
    }
    setFallback(text); setMessage("아래 코스 내용을 복사해 공유해 주세요.");
  }
  return <div className="course-card-share">
    <button type="button" onClick={share}><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M12 15V3m-4 4 4-4 4 4M5 12v8h14v-8" /></svg>코스 공유하기</button>
    {message && <p role="status">{message}</p>}
    {fallback && <textarea aria-label="공유할 코스 내용" readOnly value={fallback} onFocus={event => event.currentTarget.select()} rows={4} />}
  </div>;
}
