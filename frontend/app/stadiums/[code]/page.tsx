"use client";

import Image from "next/image";
import Link from "next/link";
import { use, useEffect, useState } from "react";
import { Icon } from "@/components/icons";
import { StadiumParkingMapDialog } from "@/components/stadium-parking-map-dialog";
import { adaptStadium } from "@/lib/baseball/adapters";
import { BaseballApiError, fetchBaseballStadium } from "@/lib/baseball/client";
import type { BaseballStadium } from "@/lib/baseball/types";
import { getStadiumMapUrl, type Stadium } from "@/lib/stadiums";

export default function StadiumPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = use(params);
  const [source, setSource] = useState<BaseballStadium | null>(null);
  const [stadium, setStadium] = useState<Stadium | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    fetchBaseballStadium(code, controller.signal).then(item => {
      const adapted = adaptStadium(item);
      if (!adapted) throw new BaseballApiError("구장 좌표가 올바르지 않아 지도와 코스를 표시할 수 없어요.");
      const contexts = [...item.home_teams].sort((a, b) => b.season - a.season || a.name.localeCompare(b.name, "ko"));
      setSource({ ...item, home_teams: contexts }); setStadium(adapted);
    }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof BaseballApiError ? cause.message : "구장 정보를 불러오지 못했어요."); });
    return () => controller.abort();
  }, [attempt, code]);
  if (error) return <main className="container writer-empty" role="alert"><h1>구장 정보를 표시할 수 없어요</h1><p>{error}</p><button type="button" onClick={() => { setError(""); setSource(null); setStadium(null); setAttempt(value => value + 1); }}>다시 시도</button> <Link href="/stadiums">전체 구장 보기</Link></main>;
  if (!stadium || !source) return <main className="container writer-empty"><p role="status">DB에서 구장 상세 정보를 불러오고 있어요.</p></main>;

  return <main className="info-page stadium-detail-page">
    <section className="info-hero"><div className="container page-intro stadium-detail-intro"><Link className="stadium-detail-back" href="/stadiums">← 전체 구장 보기</Link><p className="eyebrow">YOUR NEXT BALLPARK</p><h1>{stadium.name}</h1><p>{stadium.teams.length ? `${stadium.teams.join(" · ")}의 홈구장` : "홈팀 정보 미적재"} · 수집 {source.collected_at.slice(0, 10)}</p></div></section>
    <section className="container stadium-detail-content" aria-labelledby="stadium-info-heading">
      {stadium.seatingMap ? <a className={`stadium-detail-art stadium-seat-art stadium-seat-art-${stadium.code.toLowerCase()}`} href={stadium.seatingMap.src} target="_blank" rel="noopener noreferrer" aria-label={`${stadium.name} 전체 좌석 안내도 원본 크게 보기 (새 창)`}><Image className={`stadium-detail-seat-map stadium-detail-seat-map-${stadium.code.toLowerCase()}`} src={stadium.seatingMap.src} alt={`${stadium.name} 전체 좌석 안내도`} fill sizes="(max-width: 760px) 100vw, 46vw" priority /><span className="stadium-detail-art-label">{stadium.seatingMap.season} · {stadium.seatingMap.teamCode} SEATING MAP</span><span className="stadium-seat-zoom">원본 크게 보기 ↗</span></a> : <p>좌석 안내도 미적재</p>}
      <div className="stadium-detail-info"><p className="eyebrow">BALLPARK INFORMATION</p><h2 id="stadium-info-heading">구장 정보</h2><dl className="stadium-detail-facts"><div><dt>홈팀</dt><dd>{stadium.teams.join(" · ") || "미적재"}</dd></div><div><dt>주소</dt><dd>{stadium.address}</dd></div><div><dt>운영</dt><dd>{source.game_operator || source.facility_manager || "미적재"}</dd></div><div><dt>연락처</dt><dd>{source.phone_general || source.phone_ticket || "미적재"}</dd></div><div><dt>주차</dt><dd><StadiumParkingMapDialog parking={stadium.parkingMap} className="stadium-detail-parking-trigger" /></dd></div><div><dt>좌석도</dt><dd>{stadium.seatingMap?.sourceUrl ? <a className="stadium-official-seat-link" href={stadium.seatingMap.sourceUrl} target="_blank" rel="noopener noreferrer">구단 공식 안내에서 확인 ↗</a> : "공식 링크 미적재"}</dd></div><div><dt>구장 사진</dt><dd>{stadium.cardImage?.creditUrl ? <a href={stadium.cardImage.creditUrl} target="_blank" rel="noopener noreferrer">{stadium.cardImage.credit} ↗</a> : "사진 출처 미적재"}</dd></div></dl><div className="stadium-detail-actions"><Link href={`/routes/new?stadium=${encodeURIComponent(stadium.name)}`} className="button button-primary">이 구장으로 코스 만들기 <Icon name="arrow" size={17} /></Link><a href={getStadiumMapUrl(stadium)} target="_blank" rel="noopener noreferrer" className="button button-secondary"><Icon name="pin" size={17} /> 카카오맵에서 보기 ↗</a></div></div>
    </section>
  </main>;
}
