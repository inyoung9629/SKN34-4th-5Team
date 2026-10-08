"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";
import { adaptStadium } from "@/lib/baseball/adapters";
import { fetchBaseballStadiums } from "@/lib/baseball/client";
import { getStadiumMapUrl, type Stadium } from "@/lib/stadiums";
const regions = ["전체", "서울", "인천·경기", "대전·광주", "대구·부산·창원", "미분류"];

function getCityLabel(code: string, region: string) {
  if (region === "인천·경기") return code === "MUNHAK" ? "인천" : "수원";
  if (region === "대전·광주") return code === "DAEJEON" ? "대전" : "광주";
  if (region === "대구·부산·창원") return code === "DAEGU" ? "대구" : code === "SAJIK" ? "부산" : "창원";
  return region === "서울" ? "서울" : "미분류";
}

export default function StadiumsPage() {
  const [stadiums, setStadiums] = useState<Stadium[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [invalidCount, setInvalidCount] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [region, setRegion] = useState("전체");
  const [query, setQuery] = useState("");
  const filtered = stadiums.filter((stadium) => (region === "전체" || stadium.region === region) && `${stadium.name} ${stadium.address} ${stadium.teams.join(" ")}`.toLowerCase().includes(query.trim().toLowerCase()));
  useEffect(() => {
    const controller = new AbortController();
    fetchBaseballStadiums(controller.signal).then(page => {
      const available = page.results.map(adaptStadium).filter((item): item is Stadium => item !== null);
      setStadiums(available); setInvalidCount(page.results.length - available.length);
    }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "구장 정보를 불러오지 못했어요."); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [attempt]);

  return (
    <main className="info-page">
      <section className="info-hero">
        <div className="container page-intro"><p className="eyebrow">FIND YOUR BALLPARK</p><h1>어느 구장으로 떠나볼까요?</h1><p>처음 가는 구장도, 늘 가던 구장도.<br className="info-mobile-break" /> 나만의 직관 코스를 시작해 보세요.</p></div>
      </section>
      <section className="container info-section" aria-label="구장 찾기">
        <div className="info-filter-bar">
          <div className="info-region-tabs" aria-label="지역별 구장 필터">{regions.map((item) => <button key={item} type="button" className={region === item ? "info-chip is-active" : "info-chip"} aria-pressed={region === item} onClick={() => setRegion(item)}>{item}</button>)}</div>
          <div className="info-search"><svg viewBox="0 0 24 24" width="21" height="21" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.8" /><path d="m16 16 5 5" stroke="currentColor" strokeWidth="1.8" /></svg><label htmlFor="stadium-search" className="sr-only">구장명 또는 지역 검색</label><input id="stadium-search" placeholder="구장명, 지역으로 검색" value={query} onChange={(event) => setQuery(event.target.value)} type="search" /></div>
        </div>
        {loading && <p role="status">DB에서 구장 정보를 불러오고 있어요.</p>}
        {error && <div className="info-empty" role="alert"><h2>구장 정보를 불러오지 못했어요</h2><p>{error}</p><button type="button" onClick={() => { setLoading(true); setError(""); setAttempt(value => value + 1); }}>다시 시도</button></div>}
        {!loading && !error && invalidCount > 0 && <p role="alert">좌표가 올바르지 않은 구장 {invalidCount}개는 지도와 코스에서 제외했어요.</p>}
        {!loading && !error && <p className="info-count" role="status">총 <strong>{filtered.length}</strong>개의 구장</p>}
        <div className="info-stadium-grid">{filtered.map((stadium) => <article key={stadium.code} className="info-stadium-card">
          <div className="info-stadium-art">
            <Link href={`/stadiums/${stadium.code}`} className="info-stadium-photo-link" aria-label={`${stadium.name} 구장 정보 보기`}>
              {stadium.cardImage && <Image
                src={stadium.cardImage.src}
                alt={`${stadium.name} 구장 전경`}
                fill
                sizes="(max-width: 450px) calc(100vw - 40px), (max-width: 760px) 50vw, (max-width: 1000px) 33vw, 380px"
                className="info-stadium-photo"
                style={{ objectPosition: stadium.cardImage.objectPosition }}
              />}
              <span className="info-stadium-photo-shade" aria-hidden="true" />
              <span className="info-region-badge">{getCityLabel(stadium.code, stadium.region)}</span>
            </Link>
            {stadium.cardImage?.creditUrl && <a href={stadium.cardImage.creditUrl} target="_blank" rel="noopener noreferrer" className="info-stadium-photo-credit" aria-label={`${stadium.cardImage.credit} 사진 출처 또는 이용 조건 (새 창)`} title={stadium.cardImage.credit}>{stadium.cardImage.credit}</a>}
          </div>
          <div className="info-stadium-body"><p className="info-stadium-code">{stadium.code} BALLPARK</p><h2><Link href={`/stadiums/${stadium.code}`} className="info-stadium-detail-link" aria-label={`${stadium.name} 구장 정보 보기`}>{stadium.name} <span aria-hidden="true">↗</span></Link></h2><p className="info-address">{stadium.address}</p><div className="info-stadium-actions"><Link href={`/routes/new?stadium=${encodeURIComponent(stadium.name)}`} className="info-create-link">이 구장으로 코스 만들기 <span aria-hidden="true">→</span></Link><a href={getStadiumMapUrl(stadium)} target="_blank" rel="noopener noreferrer" className="info-map-link" aria-label={`${stadium.name} 카카오맵에서 보기 (새 창)`}>지도 ↗</a></div></div>
        </article>)}</div>
        {!loading && !error && filtered.length === 0 && <div className="info-empty"><h2>{stadiums.length ? "찾으시는 구장이 없어요" : "아직 적재된 구장이 없어요"}</h2><p>{stadiums.length ? "다른 구장 이름이나 지역으로 검색해 보세요." : "관리자가 수집 데이터를 적재한 뒤 다시 확인해 주세요."}</p>{stadiums.length > 0 && <button className="button button-secondary" type="button" onClick={() => { setQuery(""); setRegion("전체"); }}>전체 구장 보기</button>}</div>}
        <p className="info-data-note">DB에 적재된 수집 주소·좌표와 수집시각을 기준으로 표시해요. 실시간 정보는 아니므로 방문 전 구단 공식 공지를 확인해 주세요.</p>
      </section>
      <section className="container info-help-banner"><div><p className="eyebrow">FIRST TIME?</p><h2>첫 직관, 무엇부터 준비할까요?</h2><p>야구의 기본부터 구장 방문 체크리스트까지.</p></div><Link href="/guide" className="button button-secondary">직관 가이드 보기 <span aria-hidden="true">→</span></Link></section>
    </main>
  );
}
