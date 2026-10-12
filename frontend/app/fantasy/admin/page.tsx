"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, readApiResponse } from "@/lib/api/client";
import { memberFetch } from "@/lib/member-auth-request";
import { useMemberAuth } from "@/lib/member-auth";
import styles from "./page.module.css";

const inputSections = [
  {
    key: "batting",
    title: "타자 기록",
    description: "구단명과 열 제목을 포함해 붙여 넣으세요. 같은 이름의 선수는 선수코드를 함께 입력해야 합니다.",
  },
  {
    key: "pitching",
    title: "투수 기록",
    description: "구단명과 열 제목을 포함해 붙여 넣으세요. 같은 이름의 선수는 선수코드를 함께 입력해야 합니다.",
  },
] as const;

type InputKey = (typeof inputSections)[number]["key"];
type AdminContext = { week_start: string; week_end: string; today: string };
type AdminGame = {
  id: number;
  game_code: string;
  game_date: string;
  game_time: string;
  home_team: { code: string; name: string } | null;
  away_team: { code: string; name: string } | null;
};
type GameStats = {
  batting: {
    external_code: string;
    player_name: string;
    team_name: string;
    at_bats: number;
    hits: number;
    rbi: number;
    runs: number;
  }[];
  pitching: {
    external_code: string;
    player_name: string;
    team_name: string;
    saves: number;
    batters_faced: number;
    strikeouts: number;
    pitch_count: number;
  }[];
};
type LoadedGameStats = { gameId: string; data?: GameStats; error?: string };

async function adminRequest<T>(path: string, init: RequestInit = {}, fallback: string): Promise<T> {
  const response = await memberFetch(path, {
    ...init,
    cache: "no-store",
    signal: init.signal ?? AbortSignal.timeout(15000),
  });
  const data = await readApiResponse<T>(response, fallback);
  if (data === null) throw new Error(fallback);
  return data;
}

function errorText(error: unknown) {
  if (error instanceof ApiError && error.body && typeof error.body === "object") {
    const details = (error.body as { errors?: unknown }).errors;
    if (Array.isArray(details)) {
      return [error.message, ...details.filter((item): item is string => typeof item === "string")].join("\n");
    }
  }
  return error instanceof Error ? error.message : "요청을 처리하지 못했습니다.";
}

function gameLabel(game: AdminGame) {
  const away = game.away_team ? `${game.away_team.name} (${game.away_team.code})` : "원정 구단";
  const home = game.home_team ? `${game.home_team.name} (${game.home_team.code})` : "홈 구단";
  return `${game.game_time.slice(0, 5)} · ${away} vs ${home}`;
}

export default function FantasyAdminPage() {
  const { status, user } = useMemberAuth();
  const emptyValues: Record<InputKey, string> = { batting: "", pitching: "" };
  const [drafts, setDrafts] = useState<Record<string, Record<InputKey, string>>>({});
  const [period, setPeriod] = useState<AdminContext | null>(null);
  const [gameDate, setGameDate] = useState("");
  const [gamesForDate, setGamesForDate] = useState<{ date: string; items: AdminGame[] } | null>(null);
  const [gameId, setGameId] = useState("");
  const gameIdRef = useRef(gameId);
  const mutationInFlight = useRef(false);
  const values = gameId ? drafts[gameId] ?? emptyValues : emptyValues;
  const [loadedGameStats, setLoadedGameStats] = useState<LoadedGameStats | null>(null);
  const [statsRefresh, setStatsRefresh] = useState(0);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const isAdmin = Boolean(user?.is_staff || user?.is_superuser);
  const games = gamesForDate?.date === gameDate ? gamesForDate.items : [];
  const selectedGame = games.find(game => String(game.id) === gameId);
  const loadingPeriod = status === "authenticated" && isAdmin && !period && !error;
  const loadingGames = Boolean(gameDate && period && gamesForDate?.date !== gameDate);

  useEffect(() => {
    if (status !== "authenticated" || !isAdmin) return;
    const controller = new AbortController();
    void adminRequest<AdminContext>(
      "/api/v1/fantasy/admin/context/",
      { signal: controller.signal },
      "현재 환상게임 주차 정보를 불러오지 못했습니다.",
    ).then(context => {
      if (controller.signal.aborted) return;
      setPeriod(context);
      setGameDate(context.today >= context.week_start && context.today <= context.week_end ? context.today : context.week_start);
      setError("");
    }).catch(requestError => {
      if (!controller.signal.aborted) setError(errorText(requestError));
    });
    return () => controller.abort();
  }, [isAdmin, status]);

  useEffect(() => {
    if (!gameDate || !period || status !== "authenticated" || !isAdmin) return;
    const controller = new AbortController();
    void adminRequest<AdminGame[]>(
      `/api/v1/fantasy/admin/games/?date=${encodeURIComponent(gameDate)}`,
      { signal: controller.signal },
      "해당 날짜의 경기 일정을 불러오지 못했습니다.",
    ).then(items => {
      if (controller.signal.aborted) return;
      setGamesForDate({ date: gameDate, items });
      setError("");
    }).catch(requestError => {
      if (!controller.signal.aborted) {
        setGamesForDate({ date: gameDate, items: [] });
        setError(errorText(requestError));
      }
    });
    return () => controller.abort();
  }, [gameDate, isAdmin, period, status]);

  useEffect(() => {
    if (!gameId || status !== "authenticated" || !isAdmin) return;
    const controller = new AbortController();
    void adminRequest<GameStats>(
      `/api/v1/fantasy/admin/stats/?game_id=${encodeURIComponent(gameId)}`,
      { signal: controller.signal },
      "선택한 경기의 기존 기록을 불러오지 못했습니다.",
    ).then(data => {
      if (!controller.signal.aborted) setLoadedGameStats({ gameId, data });
    }).catch(requestError => {
      if (!controller.signal.aborted) {
        setLoadedGameStats({ gameId, error: errorText(requestError) });
      }
    });
    return () => controller.abort();
  }, [gameId, isAdmin, statsRefresh, status]);

  async function saveStats(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!gameId || mutationInFlight.current) return;
    mutationInFlight.current = true;
    const targetGameId = gameId;
    const submitted = { ...values };
    setError("");
    setMessage("");
    setSaving(true);
    try {
      const result = await adminRequest<{
        detail: string;
        batting_count: number;
        pitching_count: number;
        stats_finalized: boolean;
      }>("/api/v1/fantasy/admin/stats/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          game_id: targetGameId,
          source_game_id: targetGameId,
          batting: submitted.batting,
          pitching: submitted.pitching,
        }),
      }, "선수 기록을 저장하지 못했습니다.");
      setDrafts(current => {
        const draft = current[targetGameId] ?? submitted;
        return { ...current, [targetGameId]: {
          batting: draft.batting === submitted.batting ? "" : draft.batting,
          pitching: draft.pitching === submitted.pitching ? "" : draft.pitching,
        } };
      });
      if (gameIdRef.current === targetGameId) {
        setMessage(`${result.detail} 타자 ${result.batting_count}명, 투수 ${result.pitching_count}명 기록이 반영되었습니다. 기록을 수정했으므로 주차 기록 완료 확인을 다시 해야 합니다.`);
        setStatsRefresh(current => current + 1);
      }
    } catch (requestError) {
      if (gameIdRef.current === targetGameId) setError(errorText(requestError));
    } finally {
      mutationInFlight.current = false;
      setSaving(false);
    }
  }

  async function deleteStats() {
    if (!gameId || mutationInFlight.current) return;
    mutationInFlight.current = true;
    const targetGameId = gameId;
    setError("");
    setMessage("");
    setDeleting(true);
    try {
      const result = await adminRequest<{
        detail: string;
        batting_count: number;
        pitching_count: number;
      }>(`/api/v1/fantasy/admin/stats/?game_id=${encodeURIComponent(targetGameId)}`, {
        method: "DELETE",
      }, "선택한 경기 기록을 삭제하지 못했습니다.");
      if (gameIdRef.current === targetGameId) {
        setLoadedGameStats({ gameId: targetGameId, data: { batting: [], pitching: [] } });
        setConfirmDelete(false);
        setMessage(`${result.detail} 타자 ${result.batting_count}명, 투수 ${result.pitching_count}명 기록을 삭제했습니다.`);
      }
    } catch (requestError) {
      if (gameIdRef.current === targetGameId) setError(errorText(requestError));
    } finally {
      mutationInFlight.current = false;
      setDeleting(false);
    }
  }

  if (status === "loading") {
    return <main className={`container ${styles.page}`}><p role="status">관리자 권한을 확인하고 있어요.</p></main>;
  }

  if (!isAdmin) {
    return (
      <main className={`container ${styles.page}`}>
        <section className={styles.accessNotice}>
          <h1>관리자 전용 페이지</h1>
          <p>{status === "authenticated" ? "관리자 계정으로 로그인해 주세요." : "로그인 후 관리자 권한을 확인할 수 있어요."}</p>
          {status === "anonymous" && <Link className="button button-primary" href="/login?next=%2Ffantasy%2Fadmin">로그인</Link>}
          <Link className="button button-secondary" href="/fantasy">환상게임으로 돌아가기</Link>
        </section>
      </main>
    );
  }

  return (
    <main className={`container ${styles.page}`}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>TEMPORARY ADMIN</p>
          <h1>선수 기록 입력</h1>
          <p>경기 날짜와 일정을 선택한 뒤, 문서의 타자·투수 기록 표를 각각 붙여 넣어 저장합니다. 지난 주차 기록도 결산 전이면 입력할 수 있습니다.</p>
        </div>
        <Link className={styles.backLink} href="/fantasy">환상게임으로</Link>
      </header>

      <p className={styles.temporaryNotice}>
        저장하면 선택한 경기의 기존 환상게임 타자·투수 기록을 새 입력 내용으로 교체합니다. 구단명과 표 머리글은 지우지 마세요. 이미 결산된 주차의 기록은 수정할 수 없습니다.
      </p>

      <form className={styles.form} onSubmit={saveStats}>
        <section className={styles.gameSection}>
          <label className={styles.selectLabel} htmlFor="fantasy-game-date">
            경기 날짜
            <input
              id="fantasy-game-date"
              type="date"
              max={period?.today}
              value={gameDate}
              disabled={loadingPeriod || !period}
              onChange={event => {
                setGameDate(event.target.value);
                gameIdRef.current = "";
                setGameId("");
                setLoadedGameStats(null);
                setConfirmDelete(false);
                setError("");
                setMessage("");
              }}
            />
          </label>
          <label className={styles.selectLabel} htmlFor="fantasy-game">
            경기 일정
            <select
              id="fantasy-game"
              value={gameId}
              disabled={loadingGames || games.length === 0}
              onChange={event => {
                const selectedGameId = event.target.value;
                gameIdRef.current = selectedGameId;
                setGameId(selectedGameId);
                setLoadedGameStats(null);
                setConfirmDelete(false);
                setError("");
                setMessage("");
              }}
            >
              <option value="">{loadingGames ? "경기 목록을 불러오는 중..." : "경기를 선택해 주세요"}</option>
              {games.map(game => <option key={game.id} value={game.id}>{gameLabel(game)}</option>)}
            </select>
          </label>
          {period && <p className={styles.columnHint}>오늘 이전의 일정 중 아직 결산되지 않은 주차를 선택할 수 있습니다.</p>}
          {!loadingGames && gameDate && games.length === 0 && (
            <p className={styles.columnHint}>선택한 날짜에 입력 가능한 경기 일정이 없습니다.</p>
          )}
        </section>

        <div className={styles.sections}>
          {inputSections.map(section => (
            <section className={styles.inputSection} key={section.key}>
              <div className={styles.sectionHeader}>
                <div>
                  <h2>{section.title}</h2>
                  <p>{section.description}</p>
                </div>
                <button
                  className={styles.clearButton}
                  type="button"
                  disabled={!values[section.key]}
                  onClick={() => setDrafts(current => ({
                    ...current,
                    [gameId]: { ...(current[gameId] ?? emptyValues), [section.key]: "" },
                  }))}
                >
                  비우기
                </button>
              </div>
              <label className={styles.inputLabel} htmlFor={`fantasy-${section.key}`}>
                입력 내용
                <textarea
                  id={`fantasy-${section.key}`}
                  value={values[section.key]}
                  onChange={event => setDrafts(current => ({
                    ...current,
                    [gameId]: { ...(current[gameId] ?? emptyValues), [section.key]: event.target.value },
                  }))}
                  placeholder="구단 | 선수코드 | 선수명 | 타수 | 안타 | 타점 | 득점"
                  spellCheck={false}
                />
              </label>
            </section>
          ))}
        </div>

        {error && <p className={styles.errorMessage} role="alert">{error}</p>}
        {message && <p className={styles.successMessage} role="status">{message}</p>}
        <button className={styles.saveButton} type="submit" disabled={saving || deleting || !gameId || !values.batting.trim() || !values.pitching.trim()}>
          {saving ? "저장 중..." : "선수 기록 저장"}
        </button>
      </form>

      {gameId && (
        <section className={styles.recordsSection} aria-labelledby="saved-game-records">
          <div className={styles.recordsHeader}>
            <div>
              <p className={styles.eyebrow}>SAVED GAME DATA</p>
              <h2 id="saved-game-records">선택 경기의 입력된 기록</h2>
              <p>{selectedGame ? gameLabel(selectedGame) : "선택한 경기"}</p>
            </div>
            <div className={styles.recordsActions}>
              {loadedGameStats?.gameId === gameId && loadedGameStats.data && (
                <span className={styles.recordCount}>
                  총 {loadedGameStats.data.batting.length + loadedGameStats.data.pitching.length}명
                </span>
              )}
              {loadedGameStats?.gameId === gameId
                && loadedGameStats.data
                && loadedGameStats.data.batting.length + loadedGameStats.data.pitching.length > 0
                && (
                  <button
                    className={styles.deleteRecordsButton}
                    type="button"
                    disabled={deleting || saving}
                    onClick={() => setConfirmDelete(true)}
                  >
                    경기 기록 삭제
                  </button>
                )}
            </div>
          </div>
          {confirmDelete && (
            <div className={styles.deleteConfirmation} role="alert">
              <p>선택 경기의 타자·투수 기록이 모두 삭제됩니다. 원본 경기 일정은 삭제되지 않습니다.</p>
              <div>
                <button type="button" disabled={deleting || saving} onClick={() => setConfirmDelete(false)}>취소</button>
                <button type="button" disabled={deleting || saving} onClick={() => void deleteStats()}>
                  {deleting ? "삭제 중…" : "기록 삭제 확인"}
                </button>
              </div>
            </div>
          )}
          {!loadedGameStats || loadedGameStats.gameId !== gameId ? (
            <p className={styles.recordsStatus} role="status">저장된 기록을 확인하고 있어요.</p>
          ) : loadedGameStats.error ? (
            <p className={styles.recordsError} role="alert">{loadedGameStats.error}</p>
          ) : loadedGameStats.data ? (
            loadedGameStats.data.batting.length || loadedGameStats.data.pitching.length ? (
              <div className={styles.recordTables}>
                {loadedGameStats.data.batting.length > 0 && (
                  <section className={styles.recordGroup}>
                    <h3>타자 기록 <span>{loadedGameStats.data.batting.length}명</span></h3>
                    <div className={styles.tableScroll}>
                      <table>
                        <thead><tr><th>선수</th><th>선수코드</th><th>구단</th><th>타수</th><th>안타</th><th>타점</th><th>득점</th></tr></thead>
                        <tbody>{loadedGameStats.data.batting.map((row, index) => (
                          <tr key={`${row.team_name}-${row.player_name}-${index}`}>
                            <th scope="row">{row.player_name}</th><td>{row.external_code}</td><td>{row.team_name}</td>
                            <td>{row.at_bats}</td><td>{row.hits}</td><td>{row.rbi}</td><td>{row.runs}</td>
                          </tr>
                        ))}</tbody>
                      </table>
                    </div>
                  </section>
                )}
                {loadedGameStats.data.pitching.length > 0 && (
                  <section className={styles.recordGroup}>
                    <h3>투수 기록 <span>{loadedGameStats.data.pitching.length}명</span></h3>
                    <div className={styles.tableScroll}>
                      <table>
                        <thead><tr><th>선수</th><th>선수코드</th><th>구단</th><th>세이브</th><th>타자</th><th>삼진</th><th>투구 수</th></tr></thead>
                        <tbody>{loadedGameStats.data.pitching.map((row, index) => (
                          <tr key={`${row.team_name}-${row.player_name}-${index}`}>
                            <th scope="row">{row.player_name}</th><td>{row.external_code}</td><td>{row.team_name}</td>
                            <td>{row.saves}</td><td>{row.batters_faced}</td>
                            <td>{row.strikeouts}</td><td>{row.pitch_count}</td>
                          </tr>
                        ))}</tbody>
                      </table>
                    </div>
                  </section>
                )}
              </div>
            ) : (
              <p className={styles.recordsStatus}>아직 저장된 선수 기록이 없습니다.</p>
            )
          ) : null}
        </section>
      )}
    </main>
  );
}
