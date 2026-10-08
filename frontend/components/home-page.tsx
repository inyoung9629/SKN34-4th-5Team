"use client";

import Image from "next/image";
import Link from "next/link";
import { useRef, useState } from "react";
import { useChat } from "./chat-provider";
import { MAX_MESSAGE_LENGTH } from "@/lib/chat/types";
import { Baseball, CapBot, Icon } from "./icons";
import { GameSchedule } from "./game-schedule";
import { AdSlot } from "./ad-slot";
import { RouteCard } from "./route-card";
import { RouteCardsSkeleton } from "./route-skeleton";
import { useRoutes, useRoutesReady } from "@/lib/routes";
import { useMemberAuth } from "@/lib/member-auth";

const stadiumTeams = [
  { code: "lg", name: "LG 트윈스", stadium: "JAMSIL" },
  { code: "hh", name: "한화 이글스", stadium: "DAEJEON" },
  { code: "sk", name: "SSG 랜더스", stadium: "MUNHAK" },
  { code: "ss", name: "삼성 라이온즈", stadium: "DAEGU" },
  { code: "nc", name: "NC 다이노스", stadium: "CHANGWON" },
  { code: "kt", name: "KT 위즈", stadium: "SUWON" },
  { code: "lt", name: "롯데 자이언츠", stadium: "SAJIK" },
  { code: "ht", name: "KIA 타이거즈", stadium: "GWANGJU" },
  { code: "ob", name: "두산 베어스", stadium: "JAMSIL" },
  { code: "wo", name: "키움 히어로즈", stadium: "GOCHEOK" },
] as const;

const shortcuts = [
  { icon: "sparkles", image: "/images/icons/route-create.png", title: "루트 만들기", caption: "지도를 보며 직접 만드는 하루", href: "/routes/new" },
  { icon: "route", image: "/images/icons/route-browse.png", title: "루트 둘러보기", caption: "다른 팬들의 하루", href: "/routes" },
  { icon: "stadium", title: "구장 정보", caption: "가기 전에 알아두기", href: "/stadiums" },
  { icon: "book", title: "야구 가이드", caption: "첫 직관도 자신 있게", href: "/guide" },
] as const;

export function HomePage() {
  const { openChat } = useChat();
  const { status: authStatus } = useMemberAuth();
  const [question, setQuestion] = useState("");
  const composingRef = useRef(false);
  const canOpenChat = authStatus === "anonymous" || authStatus === "authenticated";
  const routes = useRoutes();
  const ready = useRoutesReady();
  const popular = routes.filter(route => route.isSample).sort((a, b) => b.likes - a.likes || b.createdAt.localeCompare(a.createdAt)).slice(0, 3);
  return (
    <main>
      <section className="home-hero" aria-labelledby="hero-heading">
        <Baseball className="hero-baseball" />
        <div className="container hero-content">
          <div className="hero-eyebrow"><span /> 경기 전부터, 경기 후까지</div>
          <h1 id="hero-heading">직관의 하루를, <span>나답게.</span></h1>
          <p className="hero-description">경기 전 맛집부터 경기 후 산책까지.<br />나만의 직관 루트를 만들고, 야구팬들과 함께 나눠보세요.</p>
          <div className="hero-search-row">
            <form className="hero-search" onSubmit={event => { event.preventDefault(); if (canOpenChat && !composingRef.current) openChat(question); }}>
              <Icon name="search" size={24} />
              <label className="sr-only" htmlFor="hero-query">직관 도우미에게 질문하기</label>
              <input id="hero-query" name="q" value={question} onChange={event => setQuestion(event.target.value)} maxLength={MAX_MESSAGE_LENGTH} placeholder="어느 구장으로 떠나볼까요?" onCompositionStart={() => { composingRef.current = true; }} onCompositionEnd={() => { composingRef.current = false; }} onKeyDown={event => {
                if (event.key === "Enter" && (event.nativeEvent.isComposing || composingRef.current || event.keyCode === 229)) event.preventDefault();
              }} />
              <button type="submit" aria-label="직관 도우미에게 질문하기" disabled={!canOpenChat} className="hero-chat-bot"><CapBot /><span className="hero-chat-ai">AI</span></button>
            </form>
          </div>
          <div className="hero-shortcuts">{shortcuts.map(shortcut => <Link href={shortcut.href} className="shortcut" key={shortcut.title}>{"image" in shortcut ? <span className="shortcut-icon has-image"><Image src={shortcut.image} alt="" width={96} height={96} /></span> : <span className="shortcut-icon"><Icon name={shortcut.icon} size={30} /></span>}<strong>{shortcut.title}</strong><small>{shortcut.caption}</small></Link>)}</div>
        </div>
      </section>
      <GameSchedule afterSchedule={<AdSlot placement="home-club-banner" />} beforeTeamBoards={<>
        <section className="home-popular-section" aria-labelledby="popular-heading"><div className="container">
        <div className="section-heading"><div><span className="eyebrow">FAN FAVORITES</span><h2 id="popular-heading">루트 추천</h2><p>경기 전후의 즐거움까지, 마음에 드는 하루를 골라보세요.</p></div><Link className="text-link" href="/routes">더보기 <Icon name="chevron" size={17} /></Link></div>
        <div className="home-popular-meta"><span>인기 루트</span><p>예시 코스 중 좋아요 순으로 3개를 보여드려요.</p></div>
        {!ready ? <RouteCardsSkeleton count={3} className="home-popular-grid" /> : <div className="home-popular-grid">{popular.map(route => <RouteCard key={route.id} route={route} />)}</div>}
      </div></section>
        <AdSlot placement="home-route-partner-banner" routeIds={ready ? popular.map(route => route.apiId ?? route.id) : []} />
      </>} />
      <section className="home-stadium-section" aria-labelledby="stadium-heading"><div className="container">
        <div className="section-heading"><div><span className="eyebrow">CHOOSE YOUR BALLPARK</span><h2 id="stadium-heading">어느 구장으로 떠날까요?</h2><p>응원하는 팀의 마크를 눌러 홈구장을 만나보세요.</p></div><Link href="/stadiums" className="text-link">구장 정보 <Icon name="chevron" size={17} /></Link></div>
        <nav className="home-team-strip" aria-label="팀별 홈구장">
          {stadiumTeams.map(team => (
            <Link href={`/stadiums/${team.stadium}`} className="home-team-link" key={team.code} aria-label={`${team.name} 홈구장 정보`} title={`${team.name} 홈구장 정보`}>
              <Image src={`/images/teams/${team.code}.svg`} alt={`${team.name} 마크`} width={88} height={88} className="home-team-logo" />
            </Link>
          ))}
        </nav>
      </div></section>
    </main>
  );
}
