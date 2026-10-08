"use client";

import { COURSE_PHASE_LABEL } from "@/lib/chat/course";
import type { ChatCourse } from "@/lib/chat/types";
import { useChat } from "./chat-provider";
import { Icon } from "./icons";
import { ChatPlaceSource } from "./chat-answer";
import "@/styles/chat-course-card.css";

// 챗봇이 짠 코스를 답변 아래 카드로 보여준다.
// 루트 작성 화면에서는 답이 오자마자 옆 지도에 그려지고(chat-provider), 여기서는 되돌리기·다시 담기만 한다.
export function ChatCourseCard({ course }: { course: ChatCourse }) {
  const { courseTarget, openCourseInWriter, appliedCourses, applyChatCourse, undoChatCourse } = useChat();
  const applied = appliedCourses.get(course);
  const otherStadium = Boolean(courseTarget && course.stadiumCode && courseTarget.stadiumCode !== course.stadiumCode);
  const hasStops = Boolean(courseTarget && courseTarget.stopCount > 0);

  return (
    <section className="chat-course-card" aria-label="추천 코스">
      <header className="chat-course-head">
        <span className="chat-course-icon"><Icon name="route" size={15} /></span>
        <strong>추천 코스</strong>
        {course.travelLabel && <span className="chat-course-chip">{course.travelLabel} 기준</span>}
      </header>
      {course.places.some(place => place.completed) && <p className="chat-course-progress" role="note">{course.places.filter(place => place.completed).length}곳 방문 완료 · 남은 일정 {course.places.filter(place => !place.completed).length}곳</p>}
      {course.origin && <p className="chat-course-summary">출발: {course.origin.name ?? "선택한 출발지"}{course.entryPoint ? " → 구장 반경 2.5km 진입 → 아래 코스" : " → 아래 코스"}</p>}
      <ol className="chat-course-stops">
        {course.places.map((place, index) => (
          <li key={`${place.placeId ?? place.name}:${index}`} className={place.completed ? "is-completed" : place.phase === "GAME" ? "is-game" : undefined}>
            <span className="chat-course-time">{place.time ?? ""}</span>
            <span className="chat-course-name" title={place.address}>{place.name}{place.stayOverride && <small> · {place.stayOverride}분</small>}</span>
            <span className="chat-course-end"><span className="chat-course-phase">{place.completed ? "✓ 방문 완료" : place.category === "STAY" ? "숙소" : COURSE_PHASE_LABEL[place.phase]}</span><ChatPlaceSource place={place} /></span>
          </li>
        ))}
      </ol>
      {course.summary && <p className="chat-course-summary">{course.summary}</p>}
      {course.approachNotice && <p className="chat-course-time-warning" role="note" aria-label="코스 경로 안내">{course.approachNotice}</p>}
      {course.timeWarning && <p className="chat-course-time-warning" role="note" aria-label="경기 전 방문 시간 안내">{course.timeWarning}</p>}
      <div className="chat-course-actions">
        {!courseTarget ? (
          <button type="button" className="is-primary" onClick={() => openCourseInWriter(course)}>루트 작성 지도에서 열기</button>
        ) : applied?.undo ? (
          <button type="button" onClick={() => undoChatCourse(course)}>담기 전으로 되돌리기</button>
        ) : hasStops && !otherStadium ? (
          <>
            <button type="button" className="is-primary" onClick={() => applyChatCourse(course, "replace")}>이 코스로 바꾸기</button>
            <button type="button" onClick={() => applyChatCourse(course, "append")}>내 코스 뒤에 담기</button>
          </>
        ) : (
          <button type="button" className="is-primary" onClick={() => applyChatCourse(course, "replace")}>{otherStadium ? "구장 바꿔서 지도에 담기" : "옆 지도에 코스 담기"}</button>
        )}
      </div>
      {applied?.message && <p className="chat-course-message" role="status">{applied.message}</p>}
    </section>
  );
}
