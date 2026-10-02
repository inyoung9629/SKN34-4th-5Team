import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, rmdirSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-collected-"));
const names = ["nearby-places", "collected-places"];
for (const name of names) {
  writeFileSync(join(scratch, `${name}.js`), ts.transpileModule(readFileSync(join(root, "lib", `${name}.ts`), "utf8"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText);
}
after(() => { for (const name of names) unlinkSync(join(scratch, `${name}.js`)); rmdirSync(scratch); });
const { fetchCollectedPlaces, selectedPlacePins, collectedSource } = createRequire(join(scratch, "entry.cjs"))("./collected-places.js");
const place = { placeId: "collected:SBIZ:1", name: "수집카페", kind: "cafe", category: "카페·디저트", lat: 37.5, lng: 127, address: "서울", phone: "", detail: "카페", cuisine: "기타", distance: 10 };
const payload = stadium => ({ status: "ok", snapshotId: "fixture", stadium, radiusM: 2500, count: 1, places: [place], warning: "미확인", lodging: { status: "details_unavailable", message: "미연결" } });
const controller = () => new AbortController();

test("nearby pins are absent until one collected place is explicitly selected", () => {
  const places = Array.from({ length: 7000 }, (_, i) => ({ ...place, placeId: `collected:SBIZ:${i}` }));
  assert.deepEqual(selectedPlacePins(places, null, []), []);
  assert.equal(selectedPlacePins(places, places[99], []).length, 1);
  assert.deepEqual(selectedPlacePins(places, places[99], [places[99]]), []);
  assert.deepEqual(selectedPlacePins(places, { ...place, placeId: "uncollected" }, []), []);
  assert.equal(collectedSource(place), "소상공인 상가정보");
});

test("one same-origin request is cached across screen remounts", async () => {
  const calls = [];
  const fetcher = async url => { calls.push(url); return { ok: true, json: async () => payload("JAMSIL") }; };
  const first = await fetchCollectedPlaces("JAMSIL", controller().signal, fetcher);
  assert.equal(await fetchCollectedPlaces("JAMSIL", controller().signal, fetcher), first);
  assert.deepEqual(calls, ["/api/v1/places/collected/?stadium=JAMSIL"]);
});

test("simultaneous mounts share a request, abort cannot update an old stadium", async () => {
  const old = controller(); let resolveResponse; let calls = 0;
  const fetcher = () => { calls++; return new Promise(resolve => { resolveResponse = resolve; }); };
  const stale = fetchCollectedPlaces("GOCHEOK", old.signal, fetcher);
  const current = fetchCollectedPlaces("GOCHEOK", controller().signal, fetcher);
  old.abort();
  resolveResponse({ ok: true, json: async () => payload("GOCHEOK") });
  await assert.rejects(stale, { name: "AbortError" });
  assert.equal((await current).stadium, "GOCHEOK");
  assert.equal(calls, 1);
});

test("failure is not cached and never falls back to a provider", async () => {
  let calls = 0;
  await assert.rejects(fetchCollectedPlaces("SUWON", controller().signal, async url => {
    calls++; assert.match(url, /places\/collected/); return { ok: false };
  }));
  assert.equal((await fetchCollectedPlaces("SUWON", controller().signal, async () => ({ ok: true, json: async () => payload("SUWON") }))).count, 1);
  assert.equal(calls, 1);
});

test("mixed providers, wrong stadium and duplicate identities are rejected", async () => {
  for (const data of [
    { ...payload("DAEGU"), stadium: "JAMSIL" },
    { ...payload("DAEGU"), places: [{ ...place, placeId: "1234" }] },
    { ...payload("DAEGU"), places: [{ ...place, lat: NaN }] },
    { ...payload("DAEGU"), count: 2, places: [place, place] },
  ]) await assert.rejects(fetchCollectedPlaces("DAEGU", controller().signal, async () => ({ ok: true, json: async () => data })));
});

test("planner loads live Kakao places without public catalogue fallback or background pins", () => {
  const planner = readFileSync(join(root, "components/nearby-route-planner.tsx"), "utf8");
  assert.doesNotMatch(planner, /fetchCollectedPlaces|collectNearbyPlaces|resolveStadium|fetchTourPlaces|crowded/);
  assert.match(planner, /collectLiveNearbyPlaces\(maps, stadium, controller.signal/);
  assert.match(planner, /key=\{ready.stadium.code\}/);
  assert.match(planner, /selectedPlacePins\(pinPlaces, selected, stops\)/);
  assert.match(planner, /for \(const \[index, stop\] of stops.entries\(\)\)/);
  assert.match(planner, /planner-drawn-pin/);
  assert.match(planner, /planner-number-pin/);
  assert.match(planner, /coursePointLabel\(stops, index, separateStart\)/);
  assert.match(planner, /if \(drawOnly\) return;/);
  assert.doesNotMatch(readFileSync(join(root, "lib/kakao-maps.ts"), "utf8"), /libraries=services/);
});

test("selected places use coordinate dots; category icons only decorate course pins", () => {
  const planner = readFileSync(join(root, "components/nearby-route-planner.tsx"), "utf8");
  const selectedPin = planner.slice(planner.indexOf("const makePin ="), planner.indexOf("unselected.forEach(makePin)"));
  assert.match(selectedPin, /planner-pin planner-pin-dot/);
  assert.doesNotMatch(selectedPin, /createElementNS|appendChild\(svg\)|planner-course-category/);
  assert.match(selectedPin, /overlay\(button, place.lat, place.lng/);
  const coursePin = planner.slice(planner.indexOf("for (const [index, stop] of stops.entries())"), planner.indexOf("return () => overlays.forEach"));
  assert.match(coursePin, /planner-course-category/);
  assert.match(coursePin, /coursePointLabel\(stops, index, separateStart\)/);
  assert.match(coursePin, /overlay\(button, stop.lat, stop.lng/);
  const css = readFileSync(join(root, "styles/nearby-planner.css"), "utf8");
  assert.match(css, /\.planner-pin-dot::before[^}]*width: 14px[^}]*border-radius: 50%/);
  assert.match(css, /\.planner-number-pin > \.planner-course-category[^}]*position: absolute; bottom: calc\(100% \+ 7px\)/);
  assert.doesNotMatch(planner, /coordsFromContainerPoint\(\{/);
  assert.equal((planner.match(/coordsFromContainerPoint\(new maps.Point\(/g) ?? []).length, 2);
});
