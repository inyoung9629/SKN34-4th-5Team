"use client";

import { useState } from "react";
import Link from "next/link";
import type { KboHitterRanking, KboPitcherRanking, KboSnapshot, KboStanding } from "@/lib/kbo/types";
import {
  DetailTeamMark, KboDetailEmpty, KboDetailHeading, KboDetailLoading,
  KboDetailSource, KboDetailWarning, useKboResource,
} from "./kbo-detail-shared";

type RankingMode = "team" | "individual";
type AthleteType = "pitcher" | "hitter";
type SortKey = "rank" | "played" | "wins" | "draws" | "losses" | "winRate" | "gamesBehind" | "battingAverage" | "era";
type SortState = { key: SortKey; ascending: boolean };
type RankingColumn<T> = { key: keyof T; label: string };

const PAGE_SIZE = 20;

function nextSort(current: SortState, key: SortKey): SortState {
  return { key, ascending: current.key === key ? !current.ascending : ["rank", "gamesBehind", "era"].includes(key) };
}

const teamColumns: { key: keyof KboStanding; label: string; sort?: SortKey }[] = [
  { key: "rank", label: "순위", sort: "rank" }, { key: "team", label: "팀" },
  { key: "played", label: "경기", sort: "played" }, { key: "wins", label: "승", sort: "wins" },
  { key: "draws", label: "무", sort: "draws" }, { key: "losses", label: "패", sort: "losses" },
  { key: "winRate", label: "승률", sort: "winRate" }, { key: "gamesBehind", label: "게임차", sort: "gamesBehind" },
  { key: "streak", label: "연속" }, { key: "battingAverage", label: "타율", sort: "battingAverage" },
  { key: "era", label: "평균자책", sort: "era" }, { key: "lastTen", label: "최근 10경기" },
];

const pitcherColumns: RankingColumn<KboPitcherRanking>[] = [
  { key: "earnedRunAverage", label: "평균자책" }, { key: "fip", label: "FIP" },
  { key: "whip", label: "WHIP" }, { key: "war", label: "WAR" }, { key: "qualityStarts", label: "QS" },
  { key: "games", label: "경기" }, { key: "wins", label: "승" }, { key: "losses", label: "패" },
  { key: "saves", label: "세이브" }, { key: "holds", label: "홀드" }, { key: "innings", label: "이닝" },
  { key: "strikeouts", label: "탈삼진" }, { key: "hitsAllowed", label: "피안타" },
  { key: "homeRunsAllowed", label: "피홈런" }, { key: "walks", label: "볼넷" },
  { key: "hitByPitch", label: "사구" }, { key: "wildPitches", label: "폭투" },
  { key: "runsAllowed", label: "실점" }, { key: "winningPercentage", label: "승률" },
];

const hitterColumns: RankingColumn<KboHitterRanking>[] = [
  { key: "battingAverage", label: "타율" }, { key: "ops", label: "OPS" },
  { key: "wrcPlus", label: "wRC+" }, { key: "war", label: "WAR" }, { key: "games", label: "경기" },
  { key: "atBats", label: "타수" }, { key: "hits", label: "안타" }, { key: "doubles", label: "2루타" },
  { key: "triples", label: "3루타" }, { key: "homeRuns", label: "홈런" },
  { key: "runsBattedIn", label: "타점" }, { key: "runs", label: "득점" },
  { key: "stolenBases", label: "도루" }, { key: "walks", label: "볼넷" },
  { key: "strikeouts", label: "삼진" }, { key: "doublePlays", label: "병살" },
  { key: "onBasePercentage", label: "출루율" }, { key: "sluggingPercentage", label: "장타율" },
];

function TeamRanking({ standings, sort, setSort, season }: {
  standings: KboStanding[]; sort: SortState; setSort: (sort: SortState) => void; season: string;
}) {
  const rows = [...standings].sort((a, b) => {
    const difference = Number(a[sort.key]) - Number(b[sort.key]);
    return (sort.ascending ? difference : -difference) || a.rank - b.rank;
  });
  const changeSort = (key: SortKey) => setSort(nextSort(sort, key));

  return <div id="team-ranking-panel" role="tabpanel" aria-labelledby="team-ranking-tab">
    <div className="kbo-record-toolbar"><p>기록 이름을 누르면 해당 기록 순으로 정렬할 수 있어요.</p>
      <button type="button" disabled={sort.key === "rank" && sort.ascending} onClick={() => setSort({ key: "rank", ascending: true })}>순위순으로 보기</button>
    </div>
    <p className="kbo-record-scroll-note">옆으로 밀어 타율·평균자책·최근 10경기까지 확인하세요. <span aria-hidden="true">→</span></p>
    <div className="kbo-record-scroll" role="region" aria-label="KBO 전체 팀 순위와 기록, 좌우 스크롤 가능" tabIndex={0}>
      <table className="kbo-record-table"><caption className="sr-only">{season} KBO 정규리그 10개 팀 순위 및 기록. 정렬 버튼을 눌러 오름차순과 내림차순을 전환할 수 있습니다.</caption>
        <thead><tr>{teamColumns.map(column => <th scope="col" key={column.key} aria-sort={column.sort === sort.key ? (sort.ascending ? "ascending" : "descending") : undefined}>
          {column.sort ? <button type="button" onClick={() => changeSort(column.sort!)} aria-label={`${column.label} ${nextSort(sort, column.sort).ascending ? "오름차순" : "내림차순"} 정렬`}>
            {column.label}<span aria-hidden="true" className={column.sort === sort.key ? "is-sorted" : undefined}>{column.sort === sort.key ? sort.ascending ? "↑" : "↓" : "↕"}</span>
          </button> : column.label}
        </th>)}</tr></thead>
        <tbody>{rows.map(team => <tr key={team.teamCode}>
          <td><span className={`kbo-record-rank${team.rank === 1 ? " is-first" : ""}`}>{team.rank}</span></td>
          <th scope="row"><Link className="kbo-record-team" href={`/standings/teams/${team.teamCode}`}><DetailTeamMark code={team.teamCode} name={team.team} />{team.team}<span className="kbo-row-link-arrow" aria-hidden="true">›</span></Link></th>
          <td>{team.played}</td><td>{team.wins}</td><td>{team.draws}</td><td>{team.losses}</td>
          <td className="kbo-record-emphasis">{team.winRate}</td><td>{team.gamesBehind}</td>
          <td className={team.streak.includes("승") ? "kbo-record-positive" : undefined}>{team.streak}</td>
          <td>{team.battingAverage}</td><td>{team.era}</td><td>{team.lastTen}</td>
        </tr>)}</tbody>
      </table>
    </div>
    <p className="kbo-record-sort-status sr-only" role="status">{teamColumns.find(column => column.key === sort.key)?.label} {sort.ascending ? "오름차순" : "내림차순"} 정렬</p>
  </div>;
}

function RankingPagination({ page, pages, onChange }: { page: number; pages: number; onChange: (page: number) => void }) {
  if (pages <= 1) return null;
  return <nav className="kbo-ranking-pagination" aria-label="개인 순위 페이지">
    <button type="button" disabled={page === 1} onClick={() => onChange(page - 1)} aria-label="이전 페이지">‹</button>
    {Array.from({ length: pages }, (_, index) => index + 1).map(number => <button type="button" key={number}
      className={page === number ? "is-active" : undefined} aria-current={page === number ? "page" : undefined} onClick={() => onChange(number)}>{number}</button>)}
    <button type="button" disabled={page === pages} onClick={() => onChange(page + 1)} aria-label="다음 페이지">›</button>
  </nav>;
}

function rankingValue(row: KboPitcherRanking | KboHitterRanking, key: PropertyKey): string {
  const value = (row as unknown as Record<PropertyKey, unknown>)[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : "—";
}

function IndividualRanking({ data, athleteType, setAthleteType, page, setPage, season }: {
  data: KboSnapshot; athleteType: AthleteType; setAthleteType: (type: AthleteType) => void;
  page: number; setPage: (page: number) => void; season: string;
}) {
  const ranking = data.individualRankings;
  if (!ranking?.pitchers.length || !ranking.hitters.length) {
    return <KboDetailEmpty title="개인 순위를 준비하고 있어요." description="새 수집이 끝나면 투수와 타자 기록을 함께 보여드릴게요." />;
  }
  const isPitcher = athleteType === "pitcher";
  const allRows = isPitcher ? ranking.pitchers : ranking.hitters;
  const columns = isPitcher ? pitcherColumns : hitterColumns;
  const pages = Math.ceil(allRows.length / PAGE_SIZE);
  const safePage = Math.min(page, pages);
  const rows = allRows.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);
  const selectType = (type: AthleteType) => { setAthleteType(type); setPage(1); };

  return <div id="individual-ranking-panel" role="tabpanel" aria-labelledby="individual-ranking-tab">
    <div className="kbo-athlete-toolbar">
      <div className="kbo-athlete-type-tabs" role="tablist" aria-label="개인 순위 선수 구분">
        <button type="button" role="tab" aria-selected={isPitcher} onClick={() => selectType("pitcher")}>투수</button>
        <button type="button" role="tab" aria-selected={!isPitcher} onClick={() => selectType("hitter")}>타자</button>
      </div>
      <p><strong>{isPitcher ? "평균자책" : "타율"} 기준</strong> · 규정 {isPitcher ? "이닝" : "타석"} 충족 선수</p>
    </div>
    <p className="kbo-record-scroll-note">옆으로 밀어 선수의 전체 기록을 확인하세요. <span aria-hidden="true">→</span></p>
    <div className="kbo-record-scroll" role="region" aria-label={`${isPitcher ? "투수" : "타자"} 개인 순위와 전체 기록, 좌우 스크롤 가능`} tabIndex={0}>
      <table className="kbo-record-table kbo-athlete-table"><caption className="sr-only">{season} KBO 정규리그 {isPitcher ? "투수" : "타자"} 개인 순위</caption>
        <thead><tr><th scope="col">순위</th><th scope="col">선수</th>{columns.map(column => <th scope="col" key={String(column.key)}>{column.label}</th>)}</tr></thead>
        <tbody>{rows.map(row => <tr key={row.playerCode}>
          <td><span className={`kbo-record-rank${row.rank === 1 ? " is-first" : ""}`}>{row.rank}</span></td>
          <th scope="row"><Link className="kbo-athlete-player" href={`/standings/players/${row.playerCode}`}><DetailTeamMark code={row.teamCode} name={row.team} /><span><strong>{row.player}</strong><small>{row.team}</small></span><span className="kbo-row-link-arrow" aria-hidden="true">›</span></Link></th>
          {columns.map((column, index) => <td className={index === 0 ? "kbo-record-emphasis" : undefined} key={String(column.key)}>{rankingValue(row, column.key)}</td>)}
        </tr>)}</tbody>
      </table>
    </div>
    <div className="kbo-ranking-count"><span>전체 {allRows.length}명</span><span>{safePage} / {pages} 페이지</span></div>
    <RankingPagination page={safePage} pages={pages} onChange={setPage} />
  </div>;
}

export function KboStandingsPage() {
  const { data, error, loading, refreshing, refresh } = useKboResource<KboSnapshot>("/api/v1/tving/daily/", 60_000);
  const [mode, setMode] = useState<RankingMode>("team");
  const [athleteType, setAthleteType] = useState<AthleteType>("pitcher");
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<SortState>({ key: "rank", ascending: true });
  const season = data?.date.slice(0, 4) ?? "2026";

  return <main className="container kbo-detail-page">
    <KboDetailHeading active="standings" />
    <section className="kbo-detail-body" aria-labelledby="kbo-ranking-title">
      <div className="kbo-record-heading"><div><p className="eyebrow">{season} SEASON</p><h2 id="kbo-ranking-title">순위·기록</h2></div><span className="kbo-detail-season">{season} 정규리그</span></div>
      <div className="kbo-ranking-tabs" role="tablist" aria-label="순위 구분">
        <button id="team-ranking-tab" type="button" role="tab" aria-selected={mode === "team"} aria-controls="team-ranking-panel" onClick={() => setMode("team")}>팀 순위</button>
        <button id="individual-ranking-tab" type="button" role="tab" aria-selected={mode === "individual"} aria-controls="individual-ranking-panel" onClick={() => setMode("individual")}>개인 순위</button>
      </div>
      {data && (data.stale || data.warning || error) && <KboDetailWarning pending={refreshing} onRetry={refresh} text={data.stale || error ? undefined : data.warning ?? undefined} />}
      {loading ? <KboDetailLoading label="KBO 순위와 기록을 불러오고 있어요." rows={10} /> : !data ? <KboDetailEmpty title="순위와 기록을 불러오지 못했어요." description="잠시 후 다시 확인해 주세요." retry={refresh} pending={refreshing} />
        : mode === "team" ? (data.standings.length
          ? <TeamRanking standings={data.standings} sort={sort} setSort={setSort} season={season} />
          : <KboDetailEmpty title="아직 확인할 수 있는 팀 순위가 없어요." description="정규리그 순위가 제공되면 표시해 드릴게요." retry={refresh} pending={refreshing} />)
        : <IndividualRanking data={data} athleteType={athleteType} setAthleteType={setAthleteType} page={page} setPage={setPage} season={season} />}
      {data && <KboDetailSource source={{ ...data.source, url: "https://www.tving.com/sports/kbo/history" }} fetchedAt={data.fetchedAt} sourceUpdatedAt={data.sourceUpdatedAt} />}
      <div className="kbo-record-glossary"><h3>기록, 이렇게 읽어보세요</h3><dl>
        {mode === "team" ? <>
          <div><dt>승률</dt><dd>무승부를 제외한 경기 중 승리한 비율</dd></div>
          <div><dt>게임차</dt><dd>선두 팀과의 승패 차이를 경기 수로 표시</dd></div>
          <div><dt>평균자책</dt><dd>투수가 9이닝 동안 허용한 평균 자책점</dd></div>
        </> : <>
          <div><dt>WAR</dt><dd>대체 선수와 비교해 팀 승리에 보탠 가치를 나타내는 기록</dd></div>
          <div><dt>OPS</dt><dd>출루율과 장타율을 더해 타자의 공격력을 나타내는 기록</dd></div>
          <div><dt>WHIP</dt><dd>투수가 한 이닝에 허용한 볼넷과 안타의 평균</dd></div>
        </>}
      </dl></div>
      <p className="kbo-detail-footnote">정규리그 기준이며, 경기 결과와 순위·기록의 반영 시점은 다를 수 있어요.</p>
    </section>
  </main>;
}
