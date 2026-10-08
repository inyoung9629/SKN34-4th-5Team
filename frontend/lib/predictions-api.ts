"use client";

import { readApiResponse } from "./api/client";
import { memberFetch } from "./member-auth-request";
import type { PredictionGameDto, PredictionTeamDto, PredictionVotesDto } from "./api/content";

export type PredictionChoice = "home" | "away";
export type PredictionGameStatus = "scheduled" | "live" | "final" | "cancelled" | "postponed" | "suspended" | "unknown";
export type PredictionTeam = PredictionTeamDto;
export type PredictionVotes = PredictionVotesDto;
export type PredictionGame = PredictionGameDto;

const statuses = new Set<PredictionGameStatus>(["scheduled", "live", "final", "cancelled", "postponed", "suspended", "unknown"]);
const teamCodes = new Set(["LG", "HH", "SK", "SS", "NC", "KT", "LT", "HT", "OB", "WO"]);

function isTeam(value: unknown): value is PredictionTeam {
  if (!value || typeof value !== "object") return false;
  const team = value as Record<string, unknown>;
  return typeof team.code === "string" && teamCodes.has(team.code) && typeof team.name === "string"
    && (team.score === null || Number.isSafeInteger(team.score) && Number(team.score) >= 0);
}

function isGame(value: unknown): value is PredictionGame {
  if (!value || typeof value !== "object") return false;
  const game = value as Record<string, unknown>, votes = game.votes as Record<string, unknown> | null;
  return typeof game.gameId === "string" && /^\d{4}-\d{2}-\d{2}$/.test(String(game.date))
    && (game.startsAt === null || typeof game.startsAt === "string" && Number.isFinite(Date.parse(game.startsAt)))
    && typeof game.stadium === "string" && isTeam(game.away) && isTeam(game.home)
    && statuses.has(game.status as PredictionGameStatus) && [null, "home", "away", "draw"].includes(game.result as string | null)
    && ["locked", "voided", "stale"].every(field => typeof game[field] === "boolean")
    && typeof game.sourceFetchedAt === "string" && Number.isFinite(Date.parse(game.sourceFetchedAt))
    && (game.myChoice === null || game.myChoice === "home" || game.myChoice === "away")
    && Boolean(votes) && ["home", "away", "total", "homePercent", "awayPercent"].every(field => Number.isSafeInteger(votes![field]) && Number(votes![field]) >= 0)
    && votes!.total === Number(votes!.home) + Number(votes!.away)
    && Number(votes!.homePercent) <= 100 && Number(votes!.awayPercent) <= 100;
}

async function responseData(response: Response, fallback: string) {
  return readApiResponse<unknown>(response, fallback);
}

function read(path: string, authenticated: boolean, signal?: AbortSignal) {
  const init = { cache: "no-store" as const, signal: signal ?? AbortSignal.timeout(15000) };
  return authenticated ? memberFetch(path, init) : fetch(path, init);
}

export async function fetchPredictionGames(filters: { date?: string; team?: string } = {}, authenticated = false, signal?: AbortSignal): Promise<PredictionGame[]> {
  const query = new URLSearchParams();
  if (filters.date) query.set("date", filters.date);
  if (filters.team) query.set("team", filters.team);
  const path = `/api/v1/community/predictions/games/${query.size ? `?${query}` : ""}`;
  const data = await responseData(await read(path, authenticated, signal), "경기 정보를 불러오지 못했어요.");
  if (!Array.isArray(data) || !data.every(isGame)) throw new Error("경기 응답 형식이 올바르지 않아요.");
  return data;
}

export async function fetchPredictionGame(gameId: string, authenticated = false, signal?: AbortSignal): Promise<PredictionGame> {
  const data = await responseData(await read(`/api/v1/community/predictions/games/${encodeURIComponent(gameId)}/`, authenticated, signal), "경기 정보를 불러오지 못했어요.");
  if (!isGame(data)) throw new Error("경기 응답 형식이 올바르지 않아요.");
  return data;
}

export async function setPredictionVote(gameId: string, choice: PredictionChoice | null): Promise<PredictionGame> {
  const response = await memberFetch(`/api/v1/community/predictions/games/${encodeURIComponent(gameId)}/vote/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    signal: AbortSignal.timeout(15000),
    body: JSON.stringify({ choice }),
  });
  const data = await responseData(response, "투표를 저장하지 못했어요.");
  if (!isGame(data)) throw new Error("경기 응답 형식이 올바르지 않아요.");
  return data;
}
