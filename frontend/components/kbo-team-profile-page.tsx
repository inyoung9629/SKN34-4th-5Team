"use client";

import Image from "next/image";
import Link from "next/link";
import { useState } from "react";
import type {
  KboDetailSnapshot, KboRosterPosition, KboTeamAthleteType, KboTeamCode, KboTeamDetail,
} from "@/lib/kbo/details-types";
import { DetailTeamMark, KboDetailEmpty, KboDetailLoading, KboDetailSource, useKboResource } from "./kbo-detail-shared";

const rankingLabels: Record<KboTeamAthleteType, string> = { pitcher: "투수", hitter: "타자" };
const positionLabels: Record<KboRosterPosition, string> = { pitcher: "투수", infielder: "내야수", outfielder: "외야수", catcher: "포수" };

function gameStatus(status: string) {
  return ({ PREV: "경기 예정", READY: "경기 준비", NOW: "경기 중", END: "경기 종료", CANCEL: "경기 취소", SUSPENDED: "경기 중단" } as Record<string, string>)[status] ?? status;
}

export function KboTeamProfilePage({ code }: { code: KboTeamCode }) {
  const url = `/api/v1/tving/details/teams/${code}/`;
  const { data, loading, refreshing, refresh } = useKboResource<KboDetailSnapshot<KboTeamDetail>>(url, current => current?.collecting ? 15_000 : 0);
  const [athleteType, setAthleteType] = useState<KboTeamAthleteType>("pitcher");
  const [position, setPosition] = useState<KboRosterPosition>("pitcher");

  if (loading) return <main className="container kbo-profile-page"><div className="kbo-profile-back"><Link href="/standings">← 순위·기록으로</Link></div><KboDetailLoading label="구단 상세 정보를 불러오고 있어요." rows={8} /></main>;
  if (!data) return <main className="container kbo-profile-page"><div className="kbo-profile-back"><Link href="/standings">← 순위·기록으로</Link></div><KboDetailEmpty title="구단 정보를 불러오지 못했어요." description="수집 서버를 확인한 뒤 다시 시도해 주세요." retry={refresh} pending={refreshing} /></main>;

  return <main className="container kbo-profile-page">
    <div className="kbo-profile-back"><Link href="/standings">← 순위·기록으로</Link></div>
    <section className="kbo-team-hero" style={data.backgroundImage ? { backgroundImage: `linear-gradient(90deg, rgb(13 38 75 / 93%), rgb(26 83 163 / 70%)), url(${data.backgroundImage})` } : undefined}>
      <DetailTeamMark code={data.code} name={data.shortName} />
      <div><p>2026 KBO LEAGUE</p><h1>{data.teamName}</h1><strong>{data.seasonTitle}</strong></div>
      <dl>{data.mainRecords.map(record => <div key={record.title}><dt>{record.title}</dt><dd>{record.value}</dd></div>)}</dl>
    </section>

    <section className="kbo-profile-section" aria-labelledby="team-season-records">
      <div className="kbo-profile-section-heading"><div><p className="eyebrow">SEASON RECORD</p><h2 id="team-season-records">시즌 기록</h2></div></div>
      <dl className="kbo-team-stat-grid">{data.boxRecords.map(record => <div key={record.title}><dt>{record.title}</dt><dd>{record.value}</dd></div>)}</dl>
    </section>

    <section className="kbo-profile-section" aria-labelledby="team-next-games">
      <div className="kbo-profile-section-heading"><div><p className="eyebrow">UPCOMING GAMES</p><h2 id="team-next-games">경기 일정</h2></div><Link href="/schedule">전체 일정 보기 →</Link></div>
      <ul className="kbo-team-games">{data.schedule.map(game => <li key={game.id}>
        <div><time dateTime={game.startsAt}>{new Intl.DateTimeFormat("ko-KR", { month: "long", day: "numeric", weekday: "short" }).format(new Date(game.startsAt))}</time><strong>{game.time}</strong><span>{game.stadium}</span></div>
        <div className="kbo-team-game-match"><span><DetailTeamMark code={game.away.code} name={game.away.name} />{game.away.name}</span><b>{game.away.score === null ? "VS" : `${game.away.score} : ${game.home.score}`}</b><span>{game.home.name}<DetailTeamMark code={game.home.code} name={game.home.name} /></span></div>
        <em>{gameStatus(game.status)}</em>
      </li>)}</ul>
    </section>

    <section className="kbo-profile-section" aria-labelledby="team-internal-ranking">
      <div className="kbo-profile-section-heading"><div><p className="eyebrow">TEAM TOP 3</p><h2 id="team-internal-ranking">팀 내 순위</h2></div></div>
      <div className="kbo-profile-tabs" role="tablist" aria-label="팀 내 순위 구분">{(Object.keys(rankingLabels) as KboTeamAthleteType[]).map(type => <button key={type} type="button" role="tab" aria-selected={athleteType === type} onClick={() => setAthleteType(type)}>{rankingLabels[type]}</button>)}</div>
      <div className="kbo-team-ranking-groups" role="tabpanel">{data.rankings[athleteType].map(group => <article key={group.title}><h3>{group.title}</h3><ol>{group.athletes.map(athlete => <li key={athlete.code}>
        <span>{athlete.rank}</span><Link href={`/standings/players/${athlete.code}`}>{athlete.imageUrl && <Image src={athlete.imageUrl} alt="" width={44} height={44} />}<strong>{athlete.name}</strong></Link><em>{athlete.value}</em>
      </li>)}</ol></article>)}</div>
    </section>

    <section className="kbo-profile-section" aria-labelledby="team-roster">
      <div className="kbo-profile-section-heading"><div><p className="eyebrow">TEAM ROSTER</p><h2 id="team-roster">선수단</h2></div><span>{data.rosters[position].length}명</span></div>
      <div className="kbo-profile-tabs" role="tablist" aria-label="선수단 포지션">{(Object.keys(positionLabels) as KboRosterPosition[]).map(type => <button key={type} type="button" role="tab" aria-selected={position === type} onClick={() => setPosition(type)}>{positionLabels[type]}</button>)}</div>
      <div className="kbo-roster-grid" role="tabpanel">{data.rosters[position].map(athlete => <Link href={`/standings/players/${athlete.code}`} key={athlete.code}>
        <div className="kbo-roster-photo">{athlete.imageUrl ? <Image src={athlete.imageUrl} alt="" width={150} height={170} /> : <span>{athlete.name.slice(0, 1)}</span>}</div>
        <small>{athlete.backNumber}</small><strong>{athlete.name}</strong>
      </Link>)}</div>
    </section>

    <section className="kbo-profile-section kbo-other-teams" aria-labelledby="other-teams"><div className="kbo-profile-section-heading"><div><p className="eyebrow">OTHER CLUBS</p><h2 id="other-teams">타 구단 바로가기</h2></div></div>
      <div>{data.shortcuts.map(team => <Link href={`/standings/teams/${team.code}`} key={team.code}><DetailTeamMark code={team.code} name={team.name} /><span>{team.name}</span></Link>)}</div>
    </section>
    <KboDetailSource source={data.source} fetchedAt={data.fetchedAt} />
  </main>;
}
