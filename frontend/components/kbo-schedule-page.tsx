"use client";

import { useEffect, useRef, useState } from "react";
import type { KboGame, KboScheduleMonth } from "@/lib/kbo/types";
import { Icon } from "./icons";
import {
  DetailTeamMark, KboDetailEmpty, KboDetailHeading, KboDetailLoading,
  KboDetailSource, KboDetailWarning, detailDate, kboTeams, koreaToday, useKboResource,
} from "./kbo-detail-shared";

const MIN_DATE = "2026-01-01";
const MAX_DATE = "2026-12-31";
const MONTHS = Array.from({ length: 12 }, (_, index) => `2026-${String(index + 1).padStart(2, "0")}`);
const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];
const monthPoll = (data: KboScheduleMonth | null) => data?.loading ? 3_000 : 60_000;

function boundedDate(date: string) {
  return date < MIN_DATE ? MIN_DATE : date > MAX_DATE ? MAX_DATE : date;
}

function dateWithOffset(date: string, offset: number) {
  const value = new Date(`${date}T12:00:00Z`);
  value.setUTCDate(value.getUTCDate() + offset);
  return boundedDate(value.toISOString().slice(0, 10));
}

function GameRow({ game }: { game: KboGame }) {
  const hasScore = game.away.score !== null && game.home.score !== null;
  return <li className={`kbo-fixture-row kbo-fixture-${game.status}`}>
    <div className="kbo-fixture-when"><time dateTime={game.startsAt ?? game.date}>{game.time || "시간 미정"}</time><span className={`kbo-fixture-status is-${game.status}`}>{game.statusLabel}</span></div>
    <div className="kbo-fixture-match">
      <div className="kbo-fixture-team kbo-fixture-away"><DetailTeamMark code={game.away.code} name={game.away.name} />
        <div><strong>{game.away.name}<small>원정</small></strong><p>투수 <span>{game.away.startingPitcher || "미정"}</span></p></div>
      </div>
      <div className={`kbo-fixture-score${hasScore ? " has-score" : ""}`} aria-label={hasScore ? `${game.away.name} ${game.away.score}점, ${game.home.name} ${game.home.score}점` : "대"}>
        {hasScore ? <><strong className={game.status === "final" && game.away.score! > game.home.score! ? "is-winner" : undefined}>{game.away.score}</strong><span>:</span><strong className={game.status === "final" && game.home.score! > game.away.score! ? "is-winner" : undefined}>{game.home.score}</strong></> : <span>VS</span>}
      </div>
      <div className="kbo-fixture-team kbo-fixture-home"><DetailTeamMark code={game.home.code} name={game.home.name} />
        <div><strong>{game.home.name}<small>홈</small></strong><p>투수 <span>{game.home.startingPitcher || "미정"}</span></p></div>
      </div>
    </div>
    <div className="kbo-fixture-venue"><Icon name="pin" size={15} /><span>{game.stadium}</span></div>
  </li>;
}

export function KboSchedulePage() {
  // Resolve the current Korean date after hydration, without a server/client midnight mismatch.
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  useEffect(() => { const frame = requestAnimationFrame(() => setSelectedDate(boundedDate(koreaToday()))); return () => cancelAnimationFrame(frame); }, []);
  return <main className="container kbo-detail-page">
    <KboDetailHeading active="schedule" />
    {selectedDate ? <ScheduleBrowser selectedDate={selectedDate} onDateChange={setSelectedDate} /> : <div className="kbo-detail-body"><KboDetailLoading label="경기 일정을 준비하고 있어요." /></div>}
  </main>;
}

function ScheduleBrowser({ selectedDate, onDateChange }: { selectedDate: string; onDateChange: (date: string) => void }) {
  const month = selectedDate.slice(0, 7);
  const [team, setTeam] = useState("all");
  const { data, error, loading, refreshing, refresh } = useKboResource<KboScheduleMonth>(`/api/v1/tving/schedule/?month=${month}`, monthPoll);
  const today = data?.today ?? koreaToday();
  const stripRef = useRef<HTMLDivElement>(null);
  const daysCount = new Date(Date.UTC(2026, Number(month.slice(5)), 0)).getUTCDate();
  const dates = Array.from({ length: daysCount }, (_, index) => `${month}-${String(index + 1).padStart(2, "0")}`);
  const day = data?.days.find(item => item.date === selectedDate);
  const games = (data?.games ?? []).filter(game => game.date === selectedDate);
  const filtered = games.filter(game => team === "all" || game.away.code === team || game.home.code === team);
  const teamName = kboTeams.find(item => item.code === team)?.name;

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const strip = stripRef.current;
      const selected = strip?.querySelector<HTMLButtonElement>('[aria-pressed="true"]');
      if (!strip || !selected) return;
      const left = selected.getBoundingClientRect().left - strip.getBoundingClientRect().left + strip.scrollLeft;
      strip.scrollTo({ left: left - (strip.clientWidth - selected.offsetWidth) / 2, behavior: "instant" });
    });
    return () => cancelAnimationFrame(frame);
  }, [selectedDate]);

  const changeMonth = (value: string) => {
    if (!MONTHS.includes(value)) return;
    const dayNumber = Math.min(Number(selectedDate.slice(8)), new Date(Date.UTC(2026, Number(value.slice(5)), 0)).getUTCDate());
    onDateChange(`${value}-${String(dayNumber).padStart(2, "0")}`);
  };
  const shiftMonth = (direction: -1 | 1) => {
    const next = MONTHS[MONTHS.indexOf(month) + direction];
    if (next) changeMonth(next);
  };
  const scrollDates = (direction: -1 | 1) => stripRef.current?.scrollBy({
    left: direction * Math.max(240, stripRef.current.clientWidth * .8),
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth",
  });

  return <section className="kbo-detail-body" aria-labelledby="kbo-date-title">
    <div className="kbo-calendar-toolbar">
      <div className="kbo-month-controls"><button type="button" className="kbo-detail-icon-button" aria-label="이전 달" disabled={month === MONTHS[0]} onClick={() => shiftMonth(-1)}><Icon name="chevron" className="kbo-chevron-back" size={18} /></button>
        <label className="sr-only" htmlFor="kbo-month">조회할 월</label><select id="kbo-month" value={month} onChange={event => changeMonth(event.target.value)}>{MONTHS.map(value => <option key={value} value={value}>2026년 {Number(value.slice(5))}월</option>)}</select>
        <button type="button" className="kbo-detail-icon-button" aria-label="다음 달" disabled={month === MONTHS[11]} onClick={() => shiftMonth(1)}><Icon name="chevron" size={18} /></button>
      </div>
      <button type="button" className="kbo-today-button" onClick={() => onDateChange(boundedDate(today))} disabled={selectedDate === today || !today.startsWith("2026-")}>오늘</button>
    </div>
    <div className="kbo-date-picker">
      <button type="button" className="kbo-detail-icon-button kbo-date-scroll-button" aria-label="앞쪽 날짜 보기" onClick={() => scrollDates(-1)}><Icon name="chevron" className="kbo-chevron-back" size={16} /></button>
      <div className="kbo-date-strip" ref={stripRef} aria-label="경기 날짜 선택">{dates.map(date => {
        const weekday = new Date(`${date}T12:00:00Z`).getUTCDay();
        const info = data?.days.find(item => item.date === date);
        return <button type="button" key={date} aria-pressed={date === selectedDate}
          aria-label={`${detailDate(date)}${date === today ? ", 오늘" : ""}${info?.status === "ready" ? `, ${info.gameCount}경기` : ""}`}
          className={`kbo-date-button${date === today ? " is-today" : ""}${weekday === 0 ? " is-sunday" : ""}${weekday === 6 ? " is-saturday" : ""}`}
          onClick={() => onDateChange(date)}><span>{WEEKDAYS[weekday]}</span><strong>{Number(date.slice(8))}</strong><i className={info?.status === "ready" && info.gameCount > 0 ? "has-games" : undefined} aria-hidden="true" /></button>;
      })}</div>
      <button type="button" className="kbo-detail-icon-button kbo-date-scroll-button" aria-label="뒤쪽 날짜 보기" onClick={() => scrollDates(1)}><Icon name="chevron" size={16} /></button>
    </div>
    <div className="kbo-team-filters" role="group" aria-label="구단별 경기 필터"><button type="button" aria-pressed={team === "all"} onClick={() => setTeam("all")}>전체 구단</button>
      {kboTeams.map(item => <button type="button" key={item.code} aria-pressed={team === item.code} onClick={() => setTeam(item.code)}>{item.name}</button>)}
    </div>
    {data && (data.stale || data.warning || error) && <KboDetailWarning pending={refreshing} onRetry={refresh} text={data.stale || error ? undefined : data.warning ?? undefined} />}
    <div className="kbo-fixtures-heading"><div><h2 id="kbo-date-title">{detailDate(selectedDate)}{selectedDate === today && <span>오늘</span>}</h2>
      <p role="status">{day?.status === "ready" ? `${teamName ? `${teamName} · ` : ""}총 ${filtered.length}경기` : "2026 경기 일정"}</p></div>
      <div className="kbo-day-controls"><button type="button" className="kbo-detail-icon-button" aria-label="이전 날 경기" disabled={selectedDate === MIN_DATE} onClick={() => onDateChange(dateWithOffset(selectedDate, -1))}><Icon name="chevron" className="kbo-chevron-back" size={15} /></button><button type="button" className="kbo-detail-icon-button" aria-label="다음 날 경기" disabled={selectedDate === MAX_DATE} onClick={() => onDateChange(dateWithOffset(selectedDate, 1))}><Icon name="chevron" size={15} /></button></div>
    </div>
    {loading ? <KboDetailLoading label="선택한 달의 경기 일정을 불러오고 있어요." /> : !data ? <KboDetailEmpty title="경기 일정을 불러오지 못했어요." description="잠시 후 다시 확인해 주세요." retry={refresh} pending={refreshing} />
      : day?.status === "error" || (!day && !data.loading) ? <KboDetailEmpty title="이 날짜의 경기를 확인하지 못했어요." description="다른 날짜를 먼저 둘러보거나 잠시 후 다시 확인해 주세요." retry={refresh} pending={refreshing} />
        : day?.status === "pending" || (!day && data.loading) ? <KboDetailLoading label="이 날짜의 경기 정보를 확인하고 있어요." />
          : filtered.length ? <ul className="kbo-fixtures-list" aria-label={`${detailDate(selectedDate)} 경기 목록`}>{filtered.map(game => <GameRow key={game.id} game={game} />)}</ul>
            : day?.status === "ready" && team !== "all" && games.length > 0 ? <KboDetailEmpty title={`${teamName}의 경기가 없어요.`} description="다른 날짜나 전체 구단을 선택해 보세요." />
              : <KboDetailEmpty title="등록된 경기가 없어요." description="선택한 날짜에 제공되는 경기 일정이 없어요. 다른 날짜를 확인해 보세요." />}
    {data?.loading && !loading && <p className="kbo-month-loading-note" role="status"><span className="ui-spinner" aria-hidden="true" />이 달의 경기 정보를 확인하고 있어요.</p>}
    {data?.fetchedAt && <KboDetailSource source={data.source} fetchedAt={data.fetchedAt} />}
    <p className="kbo-detail-footnote">경기 시각은 한국시간 기준입니다. 선발 투수와 경기 일정은 변경될 수 있어요.</p>
  </section>;
}
