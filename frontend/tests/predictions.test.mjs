import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

const frontend = new URL("../", import.meta.url);
const source = readFileSync(new URL("lib/predictions-api.ts", frontend), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } });
let memberCalls = [];
const testModule = { exports: {} };
const memberError = (value, fallback) => typeof value?.detail === "string" ? value.detail : fallback;
const memberFetch = async (path, init) => { memberCalls.push([path, init]); return Response.json(game); };
const readApiResponse = async (response, fallback) => {
  const value = await response.json().catch(() => null);
  if (!response.ok) throw new Error(value?.detail ?? fallback);
  return value;
};
new Function("module", "exports", "require", outputText)(testModule, testModule.exports, name => {
  if (name === "./member-auth-request") return { memberError, memberFetch };
  if (name === "./api/client") return { readApiResponse };
  throw new Error(`Unexpected import: ${name}`);
});
const { fetchPredictionGame, fetchPredictionGames, setPredictionVote } = testModule.exports;
const game = {
  gameId: "TVING/game 1", date: "2026-09-15", startsAt: "2026-09-15T18:30:00+09:00", stadium: "잠실",
  away: { code: "LG", name: "LG", score: null }, home: { code: "OB", name: "두산", score: null },
  status: "scheduled", result: null, locked: false, voided: false, stale: false,
  sourceFetchedAt: "2026-09-15T09:00:00+09:00", votes: { home: 0, away: 0, total: 0, homePercent: 0, awayPercent: 0 }, myChoice: null,
};

test("public filters use the direct community API and reject malformed data", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (path, init) => {
    assert.equal(path, "/api/v1/community/predictions/games/?date=2026-09-15&team=LG");
    assert.equal(init.cache, "no-store");
    return Response.json([game]);
  };
  try { assert.deepEqual(await fetchPredictionGames({ date: "2026-09-15", team: "LG" }), [game]); }
  finally { globalThis.fetch = originalFetch; }

  globalThis.fetch = async () => Response.json([{ ...game, votes: { ...game.votes, total: 1 } }]);
  try { await assert.rejects(fetchPredictionGames(), /응답 형식/); }
  finally { globalThis.fetch = originalFetch; }
});

test("authenticated reads and final-state votes use memberFetch with encoded IDs", async () => {
  memberCalls = [];
  assert.deepEqual(await fetchPredictionGame(game.gameId, true), game);
  await setPredictionVote(game.gameId, "home");
  assert.equal(memberCalls[0][0], "/api/v1/community/predictions/games/TVING%2Fgame%201/");
  assert.equal(memberCalls[1][0], "/api/v1/community/predictions/games/TVING%2Fgame%201/vote/");
  assert.deepEqual(JSON.parse(memberCalls[1][1].body), { choice: "home" });
});

test("prediction UI stays a game card surface with direct auth and shared navigation", () => {
  const component = readFileSync(new URL("components/prediction-board.tsx", frontend), "utf8");
  const page = readFileSync(new URL("app/community/predictions/page.tsx", frontend), "utf8");
  assert.match(component, /CommunityNavigation active="predictions"/);
  assert.match(component, /useMemberAuth/);
  assert.match(component, /type="date"/);
  assert.match(component, /무승부로 종료/);
  assert.match(component, /무효 처리/);
  assert.doesNotMatch(component + page, /CommunityBoard|localStorage|points|ranking/i);
});
