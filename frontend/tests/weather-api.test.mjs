import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../lib/weather-api.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } });
const testModule = { exports: {} };
new Function("module", "exports", outputText)(testModule, testModule.exports);
const { fetchStadiumWeather, parseStadiumWeather, weatherStadiumCode } = testModule.exports;

const weather = {
  label: "구름많음", temperature: 24, forecastAt: "2026-09-15T19:00+09:00",
  issuedAt: "2026-09-15T11:00+09:00", fetchedAt: "2026-09-15T12:00:00+09:00", source: "기상청 단기예보",
};

test("uses the direct same-origin Django route without a browser cache", async () => {
  let calls = 0;
  const fetcher = async (url, init) => {
    calls += 1;
    assert.equal(url, "/api/v1/weather/?stadium=JAMSIL&date=2026-09-15&time=18%3A30");
    assert.equal(init.cache, "no-store");
    assert.equal(init.redirect, "error");
    assert.ok(init.signal instanceof AbortSignal);
    return Response.json({ weather });
  };
  assert.deepEqual(await fetchStadiumWeather("JAMSIL", "2026-09-15", "18:30", fetcher), weather);
  assert.deepEqual(await fetchStadiumWeather("JAMSIL", "2026-09-15", "18:30", fetcher), weather);
  assert.equal(calls, 2);
  assert.doesNotMatch(source, /weather-api|600000|new Map/);
});

test("rejects malformed or invented weather at the browser trust boundary", () => {
  assert.equal(parseStadiumWeather({ ...weather, temperature: Number.NaN }), null);
  assert.equal(parseStadiumWeather({ ...weather, label: "태풍" }), null);
  assert.equal(parseStadiumWeather({ ...weather, issuedAt: "not-a-time" }), null);
  assert.equal(parseStadiumWeather({ ...weather, source: "실시간 관측" }), null);
});

test("maps schedule venue names to canonical stadium codes", () => {
  assert.equal(weatherStadiumCode("잠실야구장"), "JAMSIL");
  assert.equal(weatherStadiumCode("인천 SSG 랜더스필드"), "MUNHAK");
  assert.equal(weatherStadiumCode("알 수 없는 구장"), undefined);
});

test("retries briefly when the backend is busy, then gives up", async () => {
  let calls = 0;
  const busyOnce = async () => (calls += 1) === 1 ? Response.json({ weather: null, error: { code: "weather_busy" } }, { status: 429 }) : Response.json({ weather });
  assert.deepEqual(await fetchStadiumWeather("JAMSIL", "2026-09-15", "18:30", busyOnce), weather);
  assert.equal(calls, 2);

  calls = 0;
  const alwaysBusy = async () => { calls += 1; return Response.json({ weather: null }, { status: 429 }); };
  assert.equal(await fetchStadiumWeather("JAMSIL", "2026-09-15", "18:30", alwaysBusy), null);
  assert.equal(calls, 3);

  const controller = new AbortController();
  const pending = fetchStadiumWeather("JAMSIL", "2026-09-15", "18:30", alwaysBusy, controller.signal);
  controller.abort();
  assert.equal(await pending, null);
});

test("provider and malformed responses render as unavailable", async () => {
  assert.equal(await fetchStadiumWeather("JAMSIL", "2026-09-15", "19:00", async () => Response.json({ weather: null })), null);
  assert.equal(await fetchStadiumWeather("JAMSIL", "2026-09-15", "19:00", async () => Response.json({ error: { code: "provider_unavailable" } }, { status: 503 })), null);
});
