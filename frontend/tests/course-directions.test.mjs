import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-directions-test-"));
writeFileSync(join(scratch, "course-directions.js"), ts.transpileModule(readFileSync(join(frontend, "lib", "course-directions.ts"), "utf8"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText);
after(() => rmSync(scratch, { recursive: true }));
const require = createRequire(join(scratch, "entry.cjs"));
const { travelDistance, travelTime, validTravelPoint, legKey, courseLegModes, activeLegModes, fetchCourseDirections } = require("./course-directions.js");

test("formats provider totals and validates finite coordinate bounds", () => {
  assert.equal(travelTime(70), "2분");
  assert.equal(travelTime(3601), "1시간 1분");
  assert.equal(travelDistance(850), "850m");
  assert.equal(travelDistance(1250), "1.3km");
  assert.equal(validTravelPoint({ lat: 37.5, lng: 127.1 }), true);
  for (const value of [null, { lat: "37.5", lng: 127 }, { lat: NaN, lng: 127 }, { lat: 91, lng: 127 }, { lat: 37, lng: Infinity }]) assert.equal(validTravelPoint(value), false);
});

test("course directions use the generic Django API and no browser provider endpoint", () => {
  const source = readFileSync(join(frontend, "lib", "course-directions.ts"), "utf8");
  assert.match(source, /fetcher\("\/api\/v1\/travel\/directions\/"/);
  assert.doesNotMatch(source, /apis-navi\.kakaomobility|dapi\.kakao\.com|KAKAO_REST_API_KEY/);
});

const points = [{ lat: 37.5, lng: 127 }, { lat: 37.51, lng: 127.01 }, { lat: 37.52, lng: 127.02 }];
test("per-leg modes default to walking and follow the endpoints, not the list index", () => {
  const modes = { [legKey(points[0], points[1])]: "car" };
  assert.deepEqual(courseLegModes(points, "walk"), ["walk", "walk"]);
  assert.deepEqual(courseLegModes(points, "walk", modes), ["car", "walk"]);
  assert.deepEqual(courseLegModes([points[1], points[0], points[2]], "walk", modes), ["walk", "walk"]);
  assert.deepEqual(activeLegModes([points[1], points[0], points[2]], modes), {});
});
test("changing a leg fetches only that leg and recalculates the total with the other leg intact", async () => {
  const calls = [], cache = new Map(), signal = new AbortController().signal;
  const fetcher = async (_url, init) => {
    const body = JSON.parse(init.body); calls.push(body);
    return Response.json({ legs: [{ status: "ok", seconds: { walk: 600, car: 180, transit: 300 }[body.mode], distance: 1000, paths: [body.points], instructions: [] }] });
  };
  const original = await fetchCourseDirections(points, ["walk", "walk"], "walk", cache, signal, fetcher);
  const changed = await fetchCourseDirections(points, ["transit", "walk"], "walk", cache, signal, fetcher);
  assert.equal(calls.length, 3);
  assert.deepEqual(calls[2].points, points.slice(0, 2));
  assert.equal(changed.legs[1], original.legs[1]);
  assert.equal(changed.seconds, 900);
  const restored = await fetchCourseDirections(points, ["walk", "walk"], "walk", cache, signal, fetcher);
  assert.equal(restored.seconds, 1200);
  assert.equal(calls.length, 3);
});
test("failed transit leg is not disguised as walking or counted in a complete total", async () => {
  const result = await fetchCourseDirections(points, ["car", "transit"], "walk", new Map(), new AbortController().signal, async (_url, init) => {
    const { mode, points } = JSON.parse(init.body);
    return mode === "transit" ? Response.json({ error: "대중교통 경로 없음" }, { status: 503 })
      : Response.json({ legs: [{ status: "ok", seconds: 180, distance: 1000, paths: [points], instructions: [] }] });
  });
  assert.equal(result.legs[0].seconds, 180);
  assert.equal(result.legs[1].mode, "transit");
  assert.equal(result.legs[1].status, "error");
  assert.equal(result.seconds, null);
});
test("a canceled old response cannot populate the cache", async () => {
  const controller = new AbortController(), cache = new Map();
  await fetchCourseDirections(points.slice(0, 2), ["car"], "walk", cache, controller.signal, async () => {
    controller.abort();
    return Response.json({ legs: [{ status: "ok", seconds: 1, distance: 1, paths: [points], instructions: [] }] });
  });
  assert.equal(cache.size, 0);
});
