"use client";
import type { ReactNode } from "react";
import Link from "next/link";

import { useEffect, useRef, useState } from "react";
import type { KboApiResponse, KboGame, KboSnapshot, KboStanding } from "@/lib/kbo/types";
import { GameWeather } from "./game-weather";
import { Icon } from "./icons";
import { TeamLogo } from "./team-logo";
import { HomeTeamBoards } from "./home-team-boards";
import { KboHighlightSection } from "./kbo-highlight";

function formatDay(date: string, full = false) {
  const value = new Date(`${date}T12:00:00+09:00`);
  if (Number.isNaN(value.getTime())) return date;
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul", month: full ? "long" : "2-digit", day: "numeric", weekday: "short",
  }).format(value);
}

function formatCheckedAt(date: string) {
  const value = new Date(date);
  if (Number.isNaN(value.getTime())) return "확인 중";
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(value);
}

// Browsers read our cached endpoint. Crawling and its 5-minute/hourly schedule stay on the server.
function useKboSnapshot() {
  const [data, setData] = useState<KboSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [requestVersion, setRequestVersion] = useState(0);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let request: AbortController | null = null;
    const isPageVisible = () => document.visibilityState !== "hidden";

    const load = async () => {
      if (disposed || !isPageVisible() || request) return;
      const controller = new AbortController();
      request = controller;
      let timedOut = false;
      const timeout = setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, 65_000);

      try {
        const response = await fetch("/api/v1/tving/daily/?next_if_empty=true", { cache: "no-store", signal: controller.signal });
        const result: KboApiResponse = await response.json();
        if (!response.ok || !result.data || !Array.isArray(result.data.games) || !Array.isArray(result.data.standings)) {
          throw new Error("일정과 순위를 불러오지 못했어요.");
        }
        if (disposed || controller.signal.aborted) return;
        setData(result.data);
        setError(null);
      } catch {
        if (!disposed && (timedOut || !controller.signal.aborted)) {
          setError("일정과 순위를 불러오지 못했어요. 잠시 후 다시 확인해 주세요.");
        }
      } finally {
        clearTimeout(timeout);
        const isCurrentRequest = request === controller;
        if (isCurrentRequest) request = null;
        if (!disposed && isCurrentRequest) {
          setChecking(false);
          if (isPageVisible()) timer = setTimeout(load, 60_000);
        }
      }
    };

    const handleVisibility = () => {
      clearTimeout(timer);
      if (document.visibilityState === "hidden") request?.abort();
      else {
        // Returning before an aborted request settles must still refresh immediately.
        if (request?.signal.aborted) request = null;
        void load();
      }
    };

    void load();
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      disposed = true;
      clearTimeout(timer);
      request?.abort();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [requestVersion]);

  const retry = () => {
    setChecking(true);
    if (!data) setError(null);
    setRequestVersion(version => version + 1);
  };

  return { data, error, checking, retry };
}

function TeamBadge({ team, side }: { team: KboGame["home"]; side: "원정" | "홈" }) {
  return (
    <div className="game-team">
      <TeamLogo code={team.code} name={team.name} className="team-mark" />
      <strong>{team.name}</strong>
      <span className="game-starting-pitcher"><span>투수</span> {team.startingPitcher?.trim() || "미정"}</span>
      <small>{side}</small>
    </div>
  );
}

function GameCarousel({ games }: { games: KboGame[] }) {
  const trackRef = useRef<HTMLUListElement>(null);
  const [position, setPosition] = useState({ first: 1, last: 1, atStart: true, atEnd: true });
  const gameIds = games.map(game => game.id).join("|");

  useEffect(() => {
    const track = trackRef.current;
    if (!track) return;

    const updatePosition = () => {
      const card = track.firstElementChild;
      if (!card) return;
      const gap = Number.parseFloat(getComputedStyle(track).columnGap) || 0;
      const step = card.getBoundingClientRect().width + gap;
      if (!step) return;
      const first = Math.min(games.length, Math.round(track.scrollLeft / step) + 1);
      const count = Math.max(1, Math.round((track.clientWidth + gap) / step));
      const next = {
        first,
        last: Math.min(games.length, first + count - 1),
        atStart: track.scrollLeft <= 2,
        atEnd: track.scrollWidth - track.clientWidth - track.scrollLeft <= 2,
      };
      setPosition(previous => (
        previous.first === next.first && previous.last === next.last &&
        previous.atStart === next.atStart && previous.atEnd === next.atEnd ? previous : next
      ));
    };

    const observer = new ResizeObserver(updatePosition);
    observer.observe(track);
    track.addEventListener("scroll", updatePosition, { passive: true });
    const frame = requestAnimationFrame(updatePosition);
    return () => {
      observer.disconnect();
      track.removeEventListener("scroll", updatePosition);
      cancelAnimationFrame(frame);
    };
  }, [gameIds, games.length]);

  const move = (direction: -1 | 1) => {
    const track = trackRef.current;
    const card = track?.firstElementChild;
    if (!track || !card) return;
    const gap = Number.parseFloat(getComputedStyle(track).columnGap) || 0;
    track.scrollBy({
      left: direction * (card.getBoundingClientRect().width + gap),
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth",
    });
  };

  return (
    <>
      <div className="schedule-carousel">
        <button type="button" className="schedule-arrow schedule-arrow-prev" aria-label="이전 경기 일정"
          aria-controls="schedule-track" disabled={position.atStart} onClick={() => move(-1)}>
          <Icon name="chevron" size={19} />
        </button>
        <ul id="schedule-track" className="schedule-track" aria-label={`경기 일정 카드 ${games.length}개`}
          ref={trackRef} tabIndex={0} onKeyDown={event => {
            if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
              event.preventDefault();
              move(event.key === "ArrowLeft" ? -1 : 1);
            }
          }}>
          {games.map(game => {
            const hasScore = game.away.score !== null && game.home.score !== null;
            return (
              <li className="schedule-slide" key={game.id}>
                <article className={`game-card game-card-${game.status}`} aria-labelledby={`game-${game.id}-title`}>
                  <div className="game-card-top">
                    <time className="game-date" dateTime={game.startsAt ?? game.date}>
                      <span className="game-date-line">
                        <span>{formatDay(game.date)}</span>
                        <span className="game-venue"><Icon name="pin" size={16} />{game.stadium || "구장 확인 중"}</span>
                      </span>
                      <strong>{game.time || "시간 미정"}</strong>
                    </time>
                    <span className={`game-status game-status-${game.status}`}>{game.statusLabel}</span>
                  </div>
                  <h3 className="game-title sr-only" id={`game-${game.id}-title`}>{game.away.name} 대 {game.home.name}</h3>
                  <div className="game-matchup">
                    <TeamBadge team={game.away} side="원정" />
                    <div className="game-versus"><span aria-hidden="true">VS</span><GameWeather game={game} /></div>
                    <TeamBadge team={game.home} side="홈" />
                  </div>
                  {hasScore && (
                    <div className="game-score" aria-label={`${game.away.name} ${game.away.score}점, ${game.home.name} ${game.home.score}점`}>
                      <strong className={game.status === "final" && game.away.score! > game.home.score! ? "game-score-winner" : undefined}>{game.away.score}</strong>
                      <span aria-hidden="true">:</span>
                      <strong className={game.status === "final" && game.home.score! > game.away.score! ? "game-score-winner" : undefined}>{game.home.score}</strong>
                    </div>
                  )}
                  <div className="game-card-footer"><span>KBO 정규시즌</span></div>
                </article>
              </li>
            );
          })}
        </ul>
        <button type="button" className="schedule-arrow schedule-arrow-next" aria-label="다음 경기 일정"
          aria-controls="schedule-track" disabled={position.atEnd} onClick={() => move(1)}>
          <Icon name="chevron" size={19} />
        </button>
      </div>
      <div className="schedule-bottom">
        <span className="schedule-caption">화살표를 누르거나 옆으로 밀어 다른 경기를 확인하세요.</span>
        <p className="schedule-position" aria-live="polite" aria-atomic="true">
          <span className="sr-only">현재 표시 중인 경기 </span>
          {position.first === position.last ? position.first : `${position.first}–${position.last}`} / {games.length}
        </p>
      </div>
    </>
  );
}

function SourceMeta({ data }: { data: KboSnapshot }) {
  return (
    <div className="kbo-source-meta">
      <a href={data.source.url} target="_blank" rel="noreferrer">출처 {data.source.name}<Icon name="arrow" size={12} /></a>
      <span>마지막 확인 <time dateTime={data.fetchedAt}>{formatCheckedAt(data.fetchedAt)}</time> · 한국시간</span>
    </div>
  );
}

function StandingsTable({ standings }: { standings: KboStanding[] }) {
  return (
    <>
      <p className="standings-scroll-hint"><Icon name="arrow" size={13} />옆으로 밀어 전체 기록을 확인하세요.</p>
      <div className="standings-scroll" role="region" aria-label="KBO 팀 순위 상세 기록, 좌우 스크롤 가능" tabIndex={0}>
        <table className="standings-table">
          <caption className="sr-only">KBO 정규시즌 팀 순위. 경기 수, 승리, 무승부, 패배, 승률, 게임차, 연속 기록</caption>
          <thead><tr>
            <th scope="col">순위</th><th scope="col">팀</th><th scope="col">경기</th><th scope="col">승</th>
            <th scope="col">무</th><th scope="col">패</th><th scope="col">승률</th><th scope="col">게임차</th><th scope="col">연속</th>
          </tr></thead>
          <tbody>{standings.map(team => (
            <tr key={team.teamCode} className={team.rank === 1 ? "standings-leader" : undefined}>
              <td><span className="standings-rank">{team.rank}</span></td>
              <th scope="row"><span className="standings-team"><TeamLogo code={team.teamCode} name={team.team} className="standings-team-mark" />{team.team}</span></th>
              <td>{team.played}</td><td>{team.wins}</td><td>{team.draws}</td><td>{team.losses}</td>
              <td className="standings-win-rate">{team.winRate || "—"}</td><td>{team.gamesBehind || "—"}</td>
              <td><span className={team.streak.includes("승") ? "standings-streak-win" : undefined}>{team.streak || "—"}</span></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </>
  );
}

function ScheduleSkeleton() {
  return (
    <div className="schedule-loading" role="status">
      <span className="sr-only">경기 일정을 불러오고 있어요.</span>
      {[0, 1, 2].map(index => <div className="schedule-loading-card" aria-hidden="true" key={index}>
        <span /><span /><div><i /><i /></div><span />
      </div>)}
    </div>
  );
}

export function GameSchedule({ afterSchedule, beforeTeamBoards }: { afterSchedule?: ReactNode; beforeTeamBoards?: ReactNode } = {}) {
  const { data, error, checking, retry } = useKboSnapshot();
  const loading = !data && !error;
  const stale = Boolean(data && (data.stale || error));

  return (
    <>
      <section className="container home-schedule" aria-labelledby="schedule-heading">
        <div className="section-heading">
          <div><span className="eyebrow">GAME SCHEDULE</span><h2 id="schedule-heading">경기 일정</h2>
            <p>{data?.games.length ? `${formatDay(data.games[0].date, true)} · 총 ${data.games.length}경기` : "KBO 경기 일정을 확인해 보세요."}</p>
          </div>
          <Link href="/schedule" className="text-link">세부 일정 <Icon name="chevron" size={17} /></Link>
        </div>
        {data && (stale || data.warning) && (
          <div className="kbo-data-warning" role="status">
            <p>{stale ? "최신 정보를 확인하지 못해 마지막으로 수집한 자료를 보여드려요." : data.warning}</p>
            <button type="button" onClick={retry} disabled={checking}>{checking ? "확인 중…" : "다시 확인"}</button>
          </div>
        )}
        {loading ? <ScheduleSkeleton /> : !data ? (
          <div className="kbo-empty-state" role="status"><Icon name="stadium" size={32} /><strong>경기 정보를 불러오지 못했어요.</strong>
            <p>잠시 후 다시 확인해 주세요.</p><button type="button" className="button button-secondary" onClick={retry} disabled={checking}>다시 불러오기</button>
          </div>
        ) : data.games.length ? <GameCarousel games={data.games} /> : (
          <div className="kbo-empty-state"><Icon name="stadium" size={32} /><strong>아직 등록된 다음 경기가 없어요.</strong><p>다음 경기 일정이 확인되면 이곳에 표시해 드릴게요.</p></div>
        )}
        {data && <><SourceMeta data={data} /><p className="game-weather-source">날씨: <a href="https://www.data.go.kr/data/15084084/openapi.do" target="_blank" rel="noreferrer">기상청 단기예보</a> · 경기 시작에 가까운 정시 예보 · 고척은 구장 외부 기준</p></>}
      </section>

      {afterSchedule}
      <KboHighlightSection />

      <section className="container home-standings" aria-labelledby="standings-heading">
        <div className="section-heading">
          <div><span className="eyebrow">TEAM STANDINGS</span><h2 id="standings-heading">KBO 순위표</h2><div style={{ marginTop: 12 }}><span className="standings-season-label">{data?.date.slice(0, 4) ?? "KBO"} 정규시즌</span></div></div>
          <Link href="/standings" className="text-link">세부 순위표 <Icon name="chevron" size={17} /></Link>
        </div>
        {loading ? <div className="standings-loading" role="status"><span className="sr-only">팀 순위를 불러오고 있어요.</span>
          {Array.from({ length: 10 }, (_, index) => <div key={index} aria-hidden="true"><span /><span /><span /></div>)}
        </div> : data?.standings.length ? <StandingsTable standings={data.standings} /> : (
          <div className="kbo-empty-state"><Icon name="stadium" size={32} /><strong>아직 순위를 불러오지 못했어요.</strong><p>확인된 순위가 준비되면 이곳에 표시해 드릴게요.</p>
            {!checking && <button type="button" className="button button-secondary" onClick={retry}>순위 다시 확인</button>}
          </div>
        )}
        {data && <div className="standings-footnote"><p>{stale ? "마지막으로 수집한 순위입니다. " : ""}경기 결과와 순위가 반영되는 시점은 다를 수 있어요.</p>
          {data.sourceUpdatedAt && <p>출처에 표시된 갱신 시각: {data.sourceUpdatedAt}</p>}
          <SourceMeta data={data} />
        </div>}
      </section>
      {beforeTeamBoards}
      <HomeTeamBoards standings={data?.standings ?? null} loading={loading} retry={retry} />
    </>
  );
}
