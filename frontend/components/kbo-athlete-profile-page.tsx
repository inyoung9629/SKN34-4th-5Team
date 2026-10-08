"use client";

import Image from "next/image";
import Link from "next/link";
import { useMemo, useState } from "react";
import type { KboAthleteDetail, KboDetailSnapshot, KboGraphRecord } from "@/lib/kbo/details-types";
import { DetailTeamMark, KboDetailEmpty, KboDetailLoading, KboDetailSource, useKboResource } from "./kbo-detail-shared";

function graphHeight(value: string, points: KboGraphRecord["points"]): number {
  const numeric = points.map(point => Number(point.y)).filter(Number.isFinite);
  if (!numeric.length) return value === "승" ? 88 : 30;
  const current = Number(value);
  const max = Math.max(...numeric, 1); const min = Math.min(...numeric, 0);
  return 20 + ((current - min) / Math.max(max - min, 1)) * 68;
}

export function KboAthleteProfilePage({ code }: { code: string }) {
  const url = `/api/v1/tving/details/athletes/${code}/`;
  const { data, loading, refreshing, refresh } = useKboResource<KboDetailSnapshot<KboAthleteDetail>>(url, current => current?.collecting ? 15_000 : 0);
  const [recordIndex, setRecordIndex] = useState(0);
  const selected = useMemo(() => data?.seasonRecords[Math.min(recordIndex, Math.max(data.seasonRecords.length - 1, 0))], [data, recordIndex]);

  if (loading) return <main className="container kbo-profile-page"><div className="kbo-profile-back"><Link href="/standings">← 순위·기록으로</Link></div><KboDetailLoading label="선수 상세 정보를 불러오고 있어요." rows={8} /></main>;
  if (!data) return <main className="container kbo-profile-page"><div className="kbo-profile-back"><Link href="/standings">← 순위·기록으로</Link></div><KboDetailEmpty title="선수 정보를 불러오지 못했어요." description="수집 서버를 확인한 뒤 다시 시도해 주세요." retry={refresh} pending={refreshing} /></main>;
  const { profile } = data;

  return <main className="container kbo-profile-page">
    <div className="kbo-profile-back"><Link href="/standings">← 순위·기록으로</Link></div>
    <section className="kbo-athlete-hero">
      <div className="kbo-athlete-photo">{profile.imageUrl ? <Image src={profile.imageUrl} alt={`${profile.name} 선수`} width={310} height={350} priority /> : <span>{profile.name.slice(0, 1)}</span>}</div>
      <div className="kbo-athlete-summary"><Link href={`/standings/teams/${profile.team.code}`}><DetailTeamMark code={profile.team.code} name={profile.team.name} />{profile.team.name}</Link><p>{profile.backNumber} {profile.positions.join(" · ")}</p><h1>{profile.name}</h1>
        <dl><div><dt>생년월일</dt><dd>{profile.birthDate || "—"}</dd></div><div><dt>신체</dt><dd>{profile.body.join(" · ") || "—"}</dd></div><div><dt>입단</dt><dd>{profile.joinDate || "—"}</dd></div><div><dt>지명</dt><dd>{profile.draftOrder || "—"}</dd></div><div className="is-wide"><dt>경력</dt><dd>{profile.education || "—"}</dd></div></dl>
      </div>
    </section>

    <section className="kbo-profile-section" aria-labelledby="athlete-season-records">
      <div className="kbo-profile-section-heading"><div><p className="eyebrow">SEASON RECORD</p><h2 id="athlete-season-records">{data.seasonTitle}</h2></div><p>기록 카드를 누르면 변화 추이를 볼 수 있어요.</p></div>
      {data.seasonRecords.length ? <div className="kbo-athlete-record-cards" role="tablist" aria-label="선수 시즌 기록">{data.seasonRecords.map((record, index) => <button type="button" role="tab" aria-selected={recordIndex === index} key={record.title} onClick={() => setRecordIndex(index)}><span>{record.title}</span><strong>{record.value}</strong><small className={record.isFirstRank ? "is-first" : undefined}>{record.rank ?? "순위 정보 없음"}</small></button>)}</div> : <div className="kbo-profile-inline-empty">아직 제공된 시즌 기록이 없어요.</div>}
      {selected && <div className="kbo-athlete-graphs" role="tabpanel"><div><h3>{selected.title} 변화</h3><strong>{selected.value}</strong>{selected.rank && <span>{selected.rank}</span>}</div>
        {selected.graphs.length ? selected.graphs.map((graph, graphIndex) => <article key={`${graph.type}-${graphIndex}`}><h4>{selected.graphs.length > 1 ? graphIndex === 0 ? "월별 흐름" : "최근 경기" : "시즌 흐름"}</h4><div className={`kbo-stat-graph is-${graph.type}`}>{graph.points.map((point, index) => <div key={`${point.x}-${index}`} title={point.description ?? `${point.x} ${point.y}`}><span>{point.description ?? point.y}</span><i style={{ height: `${graphHeight(point.y, graph.points)}%` }} /><small>{point.x}</small></div>)}</div></article>) : <p className="kbo-graph-empty">제공된 변화 추이 데이터가 없어요.</p>}
      </div>}
    </section>

    <section className="kbo-profile-section" aria-labelledby="athlete-career-records">
      <div className="kbo-profile-section-heading"><div><p className="eyebrow">CAREER RECORD</p><h2 id="athlete-career-records">{data.careerTitle}</h2></div><span>{Math.max(data.careerRows.length - 1, 0)}시즌</span></div>
      {data.careerRows.length ? <><p className="kbo-record-scroll-note">옆으로 밀어 전체 통산 기록을 확인하세요. →</p>
        <div className="kbo-career-scroll" tabIndex={0} role="region" aria-label={`${profile.name} 통산 기록, 좌우 스크롤 가능`}><table><thead><tr>{data.careerColumns.map(column => <th key={column.key} scope="col">{column.name}</th>)}</tr></thead><tbody>{data.careerRows.map((row, rowIndex) => <tr key={`${row.season}-${rowIndex}`}>{data.careerColumns.map(column => <td key={column.key}>{row[column.key] || "—"}</td>)}</tr>)}</tbody></table></div></> : <div className="kbo-profile-inline-empty">아직 제공된 통산 기록이 없어요.</div>}
    </section>
    <KboDetailSource source={data.source} fetchedAt={data.fetchedAt} />
  </main>;
}
