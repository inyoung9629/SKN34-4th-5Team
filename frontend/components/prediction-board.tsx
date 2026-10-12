"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { memberFetch } from "@/lib/member-auth-request";
import { useMemberAuth } from "@/lib/member-auth";
import styles from "./prediction-board.module.css";

type Player = {
  external_code: string;
  name: string;
  team: string;
  team_name: string;
  fantasy_type: "BATTER" | "PITCHER";
  positions: string[];
};
type Team = { team_code: string; team_name_ko: string };
type Selection = {
  id: number;
  week: number;
  fantasy_type: "BATTER" | "PITCHER";
  player: Player;
  is_confirmed: boolean;
  weights: Record<string, number | null>;
};
type Week = { id: number; week_start: string; week_end: string; has_games: boolean };
type PlayerPage = { count: number; next: number | null; previous: number | null; results: Player[] };
type CancellableWeek = { week_id: number; week_start: string; week_end: string };
type AdminTestSettlement = {
  week_id: number;
  week_start: string;
  week_end: string;
  status: "OPEN" | "SETTLED";
  is_test_settlement: boolean;
  cancellable_weeks?: CancellableWeek[];
};
type AdminTestSettlementAction = AdminTestSettlement & {
  detail: string;
  user_count: number;
  total_points?: number;
  reversed_points?: number;
};
const PLAYERS_PER_PAGE = 8;
const PLAYER_TYPE_LABELS: Record<Player["fantasy_type"], string> = {
  BATTER: "타자",
  PITCHER: "투수",
};
const POSITION_LABELS: Record<string, string> = {
  catcher: "포수",
  infielder: "내야수",
  outfielder: "외야수",
  pitcher: "투수",
};
const STAT_LABELS: Record<string, string> = {
  at_bats: "타수",
  hits: "안타",
  rbi: "타점",
  runs: "득점",
  saves: "세이브",
  batters_faced: "타자",
  strikeouts: "삼진",
  pitch_count: "투구 수",
};

function positionLabel(position: string) {
  return POSITION_LABELS[position.toLowerCase()] ?? position;
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await memberFetch(path, {
    ...init,
    cache: "no-store",
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw new Error("환상게임 정보를 불러오지 못했어요.");
  return response.json() as Promise<T>;
}

async function publicJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { cache: "no-store", signal: AbortSignal.timeout(15000) });
  if (!response.ok) throw new Error("구단 목록을 불러오지 못했어요.");
  return response.json() as Promise<T>;
}

export function PredictionBoard() {
  const { status, user } = useMemberAuth();
  const [currentWeek, setCurrentWeek] = useState<Week | null>(null);
  const [nextWeek, setNextWeek] = useState<Week | null>(null);
  const [players, setPlayers] = useState<Player[]>([]);
  const [playerCount, setPlayerCount] = useState(0);
  const [teams, setTeams] = useState<Team[]>([]);
  const [currentSelections, setCurrentSelections] = useState<Selection[]>([]);
  const [nextSelections, setNextSelections] = useState<Selection[]>([]);
  const [score, setScore] = useState("0");
  const [fantasyType, setFantasyType] = useState<"BATTER" | "PITCHER">("BATTER");
  const [team, setTeam] = useState("");
  const [search, setSearch] = useState("");
  const [playerPage, setPlayerPage] = useState(1);
  const [loadedPlayersQuery, setLoadedPlayersQuery] = useState("");
  const [pendingPlayer, setPendingPlayer] = useState("");
  const [pendingRemoval, setPendingRemoval] = useState<number | null>(null);
  const [selectionBusy, setSelectionBusy] = useState(false);
  const selectionMutationInFlight = useRef(false);
  const [dashboardRevision, setDashboardRevision] = useState(0);
  const [adminSettlement, setAdminSettlement] = useState<AdminTestSettlement | null>(null);
  const [cancellationWeekId, setCancellationWeekId] = useState("");
  const [adminSettlementRevision, setAdminSettlementRevision] = useState(0);
  const [adminSettlementBusy, setAdminSettlementBusy] = useState(false);
  const [adminSettlementMessage, setAdminSettlementMessage] = useState("");
  const [error, setError] = useState("");
  const authenticated = status === "authenticated";
  const isAdmin = authenticated && Boolean(user?.is_staff || user?.is_superuser);
  const searchTerm = search.trim();
  const playerQuery = new URLSearchParams({ fantasy_type: fantasyType.toLowerCase() });
  if (team) playerQuery.set("team", team);
  if (searchTerm) playerQuery.set("q", searchTerm);
  playerQuery.set("page", String(playerPage));
  const playerQueryString = playerQuery.toString();
  const playersLoading = authenticated && loadedPlayersQuery !== playerQueryString;

  useEffect(() => {
    let cancelled = false;
    publicJson<Team[]>("/api/v1/baseball/teams/")
      .then(teamList => {
        if (!cancelled) setTeams(teamList);
      })
      .catch(value => {
        if (!cancelled) setError(value instanceof Error ? value.message : "구단 목록을 불러오지 못했어요.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!authenticated) return;
    let cancelled = false;
    async function loadFantasyDashboard() {
      try {
        const [current, upcoming] = await Promise.all([
          json<Week>("/api/v1/fantasy/week/current/"),
          json<Week>("/api/v1/fantasy/week/next/"),
        ]);
        const [currentChosen, upcomingChosen, result] = await Promise.all([
          json<Selection[]>(`/api/v1/fantasy/selections/?week=${current.id}`),
          json<Selection[]>(`/api/v1/fantasy/selections/?week=${upcoming.id}`),
          json<{ score: string }>("/api/v1/fantasy/score/current/"),
        ]);
        if (cancelled) return;
        setCurrentWeek(current);
        setNextWeek(upcoming);
        setCurrentSelections(currentChosen);
        setNextSelections(upcomingChosen);
        setScore(result.score);
      } catch (value) {
        if (!cancelled) setError(value instanceof Error ? value.message : "환상게임 정보를 불러오지 못했어요.");
      }
    }
    void loadFantasyDashboard();
    return () => {
      cancelled = true;
    };
  }, [authenticated, dashboardRevision]);

  useEffect(() => {
    if (!authenticated) return;
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: "Asia/Seoul",
      year: "numeric",
      month: "numeric",
      day: "numeric",
    }).formatToParts(new Date());
    const part = (type: string) => Number(parts.find(value => value.type === type)?.value);
    const midnightUtc = Date.UTC(part("year"), part("month") - 1, part("day") + 1) - 9 * 60 * 60 * 1000;
    const timer = window.setTimeout(
      () => setDashboardRevision(revision => revision + 1),
      Math.max(1000, midnightUtc - Date.now() + 1000),
    );
    return () => window.clearTimeout(timer);
  }, [authenticated, dashboardRevision]);

  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    json<AdminTestSettlement>("/api/v1/fantasy/admin/test-settlement/")
      .then(result => {
        if (!cancelled) {
          setAdminSettlement(result);
          setCancellationWeekId(current => {
            const weeks = result.cancellable_weeks ?? (result.is_test_settlement ? [result] : []);
            if (weeks.some(week => String(week.week_id) === current)) return current;
            return result.is_test_settlement ? String(result.week_id) : "";
          });
        }
      })
      .catch(value => {
        if (!cancelled) setError(value instanceof Error ? value.message : "테스트 결산 상태를 불러오지 못했어요.");
      });
    return () => {
      cancelled = true;
    };
  }, [isAdmin, dashboardRevision, adminSettlementRevision]);

  useEffect(() => {
    if (!authenticated) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      json<PlayerPage>(`/api/v1/fantasy/players/?${playerQueryString}`)
        .then(page => {
          if (cancelled) return;
          setPlayers(page.results);
          setPlayerCount(page.count);
          setLoadedPlayersQuery(playerQueryString);
        })
        .catch(value => {
          if (cancelled) return;
          setPlayers([]);
          setPlayerCount(0);
          setLoadedPlayersQuery(playerQueryString);
          setError(value instanceof Error ? value.message : "선수 목록을 불러오지 못했어요.");
        });
    }, searchTerm ? 250 : 0);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [authenticated, playerQueryString, searchTerm]);

  async function select(player: Player) {
    if (selectionMutationInFlight.current) return;
    selectionMutationInFlight.current = true;
    setSelectionBusy(true);
    setError("");
    setPendingPlayer(player.external_code);
    try {
      await json<Selection>("/api/v1/fantasy/selections/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ player: player.external_code }),
      });
      setDashboardRevision(revision => revision + 1);
    } catch (value) {
      setError(value instanceof Error ? value.message : "선택하지 못했어요.");
    } finally {
      setPendingPlayer("");
      selectionMutationInFlight.current = false;
      setSelectionBusy(false);
    }
  }

  async function remove(id: number) {
    if (selectionMutationInFlight.current) return;
    selectionMutationInFlight.current = true;
    setSelectionBusy(true);
    setError("");
    setPendingRemoval(id);
    try {
      const response = await memberFetch(`/api/v1/fantasy/selections/${id}/`, {
        method: "DELETE",
        cache: "no-store",
        signal: AbortSignal.timeout(15000),
      });
      if (!response.ok) throw new Error("선택을 취소하지 못했어요.");
      setDashboardRevision(revision => revision + 1);
    } catch (value) {
      setError(value instanceof Error ? value.message : "선택을 취소하지 못했어요.");
    } finally {
      setPendingRemoval(null);
      selectionMutationInFlight.current = false;
      setSelectionBusy(false);
    }
  }

  async function runAdminSettlementAction(method: "POST" | "DELETE") {
    const isPayout = method === "POST";
    if (adminSettlementBusy || (!isPayout && !cancellationWeekId)) return;
    const prompt = isPayout
      ? "현재 점수를 기준으로 모든 참여자에게 테스트 포인트를 지급할까요?"
      : "선택한 주차의 테스트 지급 포인트를 회수하고 미결산으로 되돌릴까요?";
    if (!window.confirm(prompt)) return;

    setError("");
    setAdminSettlementMessage("");
    setAdminSettlementBusy(true);
    try {
      const settlementPath = method === "DELETE"
        ? `/api/v1/fantasy/admin/test-settlement/?week_id=${cancellationWeekId}`
        : "/api/v1/fantasy/admin/test-settlement/";
      const response = await memberFetch(settlementPath, {
        method,
        cache: "no-store",
        signal: AbortSignal.timeout(15000),
      });
      const result = await response.json() as AdminTestSettlementAction;
      if (!response.ok) {
        throw new Error(result.detail || "테스트 결산을 처리하지 못했어요.");
      }
      if (isPayout) {
        setAdminSettlement(result);
        setCancellationWeekId(String(result.week_id));
      } else {
        setCancellationWeekId("");
      }
      setAdminSettlementRevision(revision => revision + 1);
      window.dispatchEvent(new Event("fantasy-points-updated"));
      setAdminSettlementMessage(
        isPayout
          ? `${result.user_count}명에게 총 ${result.total_points ?? 0}포인트를 지급했어요.`
          : `${result.user_count}명의 ${result.reversed_points ?? 0}포인트를 회수했어요.`,
      );
    } catch (value) {
      setError(value instanceof Error ? value.message : "테스트 결산을 처리하지 못했어요.");
    } finally {
      setAdminSettlementBusy(false);
    }
  }

  const selectedPlayerCodes = new Set(nextSelections.map(item => item.player.external_code));
  const filteredPlayers = players.filter(player => !selectedPlayerCodes.has(player.external_code));
  const pageCount = Math.ceil(playerCount / PLAYERS_PER_PAGE);
  const currentPlayerPage = playerPage;
  const visiblePlayers = filteredPlayers;

  return (
    <main className={`container ${styles.page} header-aligned-content`}>
      <header className={styles.hero}>
        <div>
          <p className={styles.eyebrow}>FANTASY GAME</p>
          <h1>환상게임</h1>
          <p className={styles.description}>
            매주 응원할 선수를 고르고, 실제 경기 기록으로 나만의 점수를 만들어 보세요.
          </p>
        </div>
        <div className={styles.weekBadge}>
          <span>이번 주 경기</span>
          <strong>{currentWeek ? `${currentWeek.week_start} – ${currentWeek.week_end}` : "월요일 – 일요일"}</strong>
        </div>
      </header>

      {error && <p className={styles.error} role="alert">{error}</p>}
      {status === "unavailable" && (
        <p className={styles.notice} role="alert">회원 정보를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.</p>
      )}
      {status === "anonymous" && (
        <div className={styles.loginNotice}>
          <div><strong>로그인하고 환상게임을 시작해 보세요</strong><span>선수 선택과 개인 점수 확인은 로그인 후 이용할 수 있어요.</span></div>
          <Link className="button button-primary" href="/login">로그인</Link>
        </div>
      )}

      <div className={styles.dashboard}>
        <section className={styles.panel}>
          <div className={styles.sectionHeading}>
            <div><p className={styles.sectionKicker}>MY LINEUP</p><h2>이번 주 선수 선택</h2></div>
            {authenticated && <span className={styles.count}>{currentSelections.length}명</span>}
          </div>
          {authenticated ? (
            currentSelections.length ? (
              <div className={styles.selectionList}>
                {currentSelections.map(item => (
                  <article className={styles.selectionCard} key={item.id}>
                    <div className={styles.playerIdentity}>
                      <span className={styles.playerMark}>{PLAYER_TYPE_LABELS[item.fantasy_type]}</span>
                      <div><strong>{item.player.name}</strong><span>{item.player.team_name} · {item.player.positions.map(positionLabel).join(", ") || "포지션 미상"}</span></div>
                    </div>
                    <div className={styles.weights}>
                      {Object.entries(item.weights).map(([key, value]) => (
                        <span key={key}>{STAT_LABELS[key] ?? key} <b>{value ?? "?"}</b></span>
                      ))}
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <div className={styles.emptyState}><strong>지난주에 선택한 선수가 없어요</strong><span>지난주 선택이 없으면 이번 주 라인업도 비어 있어요.</span></div>
            )
          ) : (
            <div className={styles.lockedState}><span className={styles.lockIcon} aria-hidden="true">★</span><strong>나만의 선수 라인업을 만들어 보세요</strong><span>로그인하면 지난주에 선택한 이번 주 선수를 확인할 수 있어요.</span></div>
          )}
          <div className={styles.scoreSummary}>
            <span>이번 주 획득 점수</span><strong>{authenticated ? `${score}점` : "로그인 후 확인"}</strong>
          </div>
          {isAdmin && (
            <section className={styles.adminSettlement} aria-label="관리자 테스트 포인트 결산">
              <div>
                <strong>관리자 테스트 결산</strong>
                <span>현재 기록과 점수 기준으로 포인트를 지급합니다. 실제 결산과 별도인 테스트 기능입니다.</span>
              </div>
              <label>취소할 테스트 결산 주차
                <select value={cancellationWeekId} disabled={adminSettlementBusy} onChange={event => setCancellationWeekId(event.target.value)}>
                  <option value="">주차를 선택해 주세요</option>
                  {(adminSettlement?.cancellable_weeks ?? (adminSettlement?.is_test_settlement ? [adminSettlement] : [])).map(week => (
                    <option key={week.week_id} value={week.week_id}>{week.week_start} – {week.week_end}</option>
                  ))}
                </select>
              </label>
              <div className={styles.adminSettlementActions}>
                <button
                  type="button"
                  disabled={
                    adminSettlementBusy
                    || adminSettlement === null
                    || adminSettlement.status === "SETTLED"
                  }
                  onClick={() => void runAdminSettlementAction("POST")}
                >
                  {adminSettlementBusy ? "처리 중…" : "현재 점수로 포인트 지급"}
                </button>
                <button
                  type="button"
                  className={styles.cancelTestSettlement}
                  disabled={adminSettlementBusy || !cancellationWeekId}
                  onClick={() => void runAdminSettlementAction("DELETE")}
                >
                  지급 취소
                </button>
              </div>
              {adminSettlementMessage && <span className={styles.adminSettlementMessage} role="status">{adminSettlementMessage}</span>}
            </section>
          )}
        </section>

        <section className={styles.panel}>
          <div className={styles.sectionHeading}>
            <div>
              <p className={styles.sectionKicker}>PICK YOUR PLAYERS</p>
              <h2>다음 주 선수 선택</h2>
              {nextWeek && <span className={styles.weekCaption}>{nextWeek.week_start} – {nextWeek.week_end}</span>}
            </div>
            {authenticated && <span className={styles.count}>{nextSelections.length}/2명</span>}
          </div>
          {nextWeek && !nextWeek.has_games && (
            <p className={styles.notice} role="status">다음 주 예정된 경기가 없어 선수 선택을 할 수 없어요.</p>
          )}

          {authenticated && (
            <section className={styles.upcomingSelections} aria-label="다음 주 선택 선수">
              <div className={styles.subsectionHeading}>
                <strong>타자·투수 각 1명</strong><span>같은 종류를 다시 선택하면 기존 선수가 교체돼요</span>
              </div>
              <div className={styles.slotSummary}>
                {(["BATTER", "PITCHER"] as const).map(type => {
                  const selection = nextSelections.find(item => item.fantasy_type === type);
                  return (
                    <div className={styles.slotCard} key={type}>
                      <span>{type === "BATTER" ? "타자" : "투수"}</span>
                      <strong>{selection ? selection.player.name : "미선택"}</strong>
                    </div>
                  );
                })}
              </div>
              {nextSelections.length ? (
                <ul className={styles.upcomingSelectionList}>
                  {nextSelections.map(item => (
                    <li className={styles.upcomingSelectionCard} key={item.id}>
                      <div className={styles.playerIdentity}>
                        <span className={styles.playerMark}>{PLAYER_TYPE_LABELS[item.fantasy_type]}</span>
                        <div>
                          <strong>{item.player.name}</strong>
                          <span>
                            {item.player.team_name} · {item.player.positions.map(positionLabel).join(", ") || "포지션 미상"}
                          </span>
                        </div>
                      </div>
                      <button
                        className={styles.removeButton}
                        type="button"
                        disabled={selectionBusy || pendingRemoval === item.id}
                        onClick={() => void remove(item.id)}
                      >
                        {pendingRemoval === item.id ? "취소 중…" : "선택 취소"}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className={styles.noUpcomingSelections}>아직 선택한 선수가 없어요. 아래에서 타자와 투수를 한 명씩 골라주세요.</p>
              )}
            </section>
          )}

          <div className={styles.filters}>
            <div className={styles.playerTypeTabs} role="group" aria-label="선수 종류">
              <button
                type="button"
                aria-pressed={fantasyType === "BATTER"}
                className={fantasyType === "BATTER" ? styles.activePlayerType : ""}
                disabled={!nextWeek?.has_games}
                onClick={() => {
                  setFantasyType("BATTER");
                  setPlayerPage(1);
                }}
              >
                타자 선택
              </button>
              <button
                type="button"
                aria-pressed={fantasyType === "PITCHER"}
                className={fantasyType === "PITCHER" ? styles.activePlayerType : ""}
                disabled={!nextWeek?.has_games}
                onClick={() => {
                  setFantasyType("PITCHER");
                  setPlayerPage(1);
                }}
              >
                투수 선택
              </button>
            </div>
            <label>팀
              <select value={team} onChange={event => {
                setTeam(event.target.value);
                setPlayerPage(1);
              }} disabled={!nextWeek?.has_games}>
                <option value="">전체 팀</option>
                {teams.map(item => <option value={item.team_code} key={item.team_code}>{item.team_name_ko}</option>)}
              </select>
            </label>
          </div>

          {authenticated ? (
            <>
              <label className={styles.searchField}>
                선수 검색
                <input
                  type="search"
                  value={search}
                  placeholder="선수 이름을 입력하세요"
                  disabled={!nextWeek?.has_games}
                  onChange={event => {
                    setSearch(event.target.value);
                    setPlayerPage(1);
                  }}
                />
              </label>
              <div className={styles.resultsHeading}>
                <span>{playersLoading ? "선수 목록을 불러오는 중…" : `선수 ${playerCount}명`}</span>
              </div>
              {playersLoading ? (
                <div className={styles.emptyState} role="status"><strong>선수 목록을 불러오는 중이에요</strong></div>
              ) : visiblePlayers.length ? (
                <>
                  <div className={styles.playerGrid}>
                    {visiblePlayers.map(player => (
                      <article className={styles.playerCard} key={player.external_code}>
                        <div className={styles.playerIdentity}>
                          <span className={styles.playerMark}>{PLAYER_TYPE_LABELS[player.fantasy_type]}</span>
                          <div>
                            <strong>{player.name}</strong>
                            <span>
                              {player.team_name} · {player.positions.map(positionLabel).join(", ") || "포지션 미상"}
                            </span>
                          </div>
                        </div>
                        <button
                          type="button"
                          disabled={!nextWeek?.has_games || selectionBusy}
                          onClick={() => void select(player)}
                        >
                          {pendingPlayer === player.external_code
                            ? "저장 중…"
                            : nextSelections.some(item => item.fantasy_type === player.fantasy_type)
                              ? "교체"
                              : "선택"}
                        </button>
                      </article>
                    ))}
                  </div>
                </>
              ) : (
                <div className={styles.emptyState}><strong>조건에 맞는 선수가 없어요</strong><span>검색어를 바꾸거나 다른 팀을 선택해 보세요.</span></div>
              )}
              <nav className={styles.pagination} aria-label="선수 목록 페이지">
                <button type="button" disabled={currentPlayerPage <= 1} onClick={() => setPlayerPage(page => Math.max(1, page - 1))}>이전</button>
                <span>{currentPlayerPage} / {Math.max(pageCount, 1)}</span>
                <button type="button" disabled={playersLoading || currentPlayerPage >= pageCount} onClick={() => setPlayerPage(page => Math.min(pageCount, page + 1))}>다음</button>
              </nav>
            </>
          ) : (
            <div className={styles.lockedState}><span className={styles.lockIcon} aria-hidden="true">＋</span><strong>{fantasyType === "PITCHER" ? "투수 선수 목록은 로그인 후 이용할 수 있어요" : "타자 선수 목록은 로그인 후 이용할 수 있어요"}</strong><span>선택한 팀의 {fantasyType === "PITCHER" ? "투수" : "타자"}를 보려면 로그인해 주세요.</span></div>
          )}
        </section>
      </div>
    </main>
  );
}
