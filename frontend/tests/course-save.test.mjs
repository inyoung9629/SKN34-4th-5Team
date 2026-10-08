import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../lib/routes.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } });
const richSource = readFileSync(new URL("../lib/community-rich-content.ts", import.meta.url), "utf8");
const { outputText: richOutput } = ts.transpileModule(richSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } });
const richModule = { exports: {} };
new Function("module", "exports", richOutput)(richModule, richModule.exports);
const lodgingModule = { exports: {} };
new Function("module", "exports", ts.transpileModule(readFileSync(new URL("../lib/google-lodging.ts", import.meta.url), "utf8"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText)(lodgingModule, lodgingModule.exports);

function apiHarness({ legacy = [], fetched = [], loadFailure = false } = {}) {
  let blocked = false;
  let nextId = 1;
  let fetchCount = 0;
  const storage = new Map(legacy.length ? [["kbo-trip-routes-v1", JSON.stringify(legacy)]] : []);
  const adapter = {
    fetchCourses: async () => { fetchCount += 1; if (loadFailure) throw new Error("offline"); return fetched; },
    persistCourse: async route => {
      if (blocked) throw new Error("저장 실패");
      return { ...route, id: !route.id || route.legacy ? `server-${nextId++}` : route.id, owned: true, legacy: false };
    },
    removeCourse: async () => {},
  };
  const react = { useEffect() {}, useMemo: callback => callback(), useSyncExternalStore: (_subscribe, snapshot) => snapshot() };
  const browser = { localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) }, addEventListener() {}, removeEventListener() {}, dispatchEvent() {} };
  const requireDependency = name => name === "./course-api" ? adapter : name === "./client-id" ? { createClientId: () => "11111111-1111-4111-8111-111111111111" } : name === "./community-rich-content" ? richModule.exports : name === "./google-lodging" ? lodgingModule.exports : name === "react" ? react : (() => { throw new Error(`unexpected import: ${name}`); })();
  const testModule = { exports: {} };
  new Function("require", "module", "exports", "window", outputText)(requireDependency, testModule, testModule.exports, browser);
  return { ...testModule.exports, storage, block: () => { blocked = true; }, allow: () => { blocked = false; }, recover: () => { loadFailure = false; }, fetchCount: () => fetchCount };
}

const course = (id = "") => ({
  id, title: "잠실 직관 코스", stadium: "잠실야구장", description: "출발지 → 카페", content: "",
  tags: [], duration: "반나절", cover: "/images/stadium-night.jpg", author: "익명", likes: 0,
  isSample: false, owned: true, createdAt: "2026-09-12T12:00:00.000Z",
  stops: [{ name: "카페", category: "카페", placeId: "123", lat: 37.51, lng: 127.07 }],
});

test("a named course without a story persists its visits and separate start coordinates", async () => {
  const api = apiHarness();
  const route = { ...course(), start: { lat: 37.516, lng: 127.075 } };
  const saved = await api.saveRoute(route);
  assert.deepEqual(api.getRoutes().find(item => item.id === saved.id), saved);
});

test("a course retains story formatting and image references after save", async () => {
  const contentDoc = { version: 1, blocks: [
    { type: "paragraph", align: "center", runs: [{ text: "경기 전 카페", font: "serif", size: 20, color: "#246bf3", bold: true, italic: false, underline: false }] },
    { type: "image", id: "d65e8543-1267-4530-a156-63be55546568" },
  ] };
  const api = apiHarness();
  const saved = await api.saveRoute({ ...course(), content: "경기 전 카페\n[이미지]", contentDoc });
  assert.deepEqual(api.getRoutes().find(item => item.id === saved.id).contentDoc, contentDoc);
});

test("editing updates one course and keeps other courses and legacy routes intact", async () => {
  const api = apiHarness();
  const first = await api.saveRoute(course()), second = await api.saveRoute(course());
  await api.saveRoute({ ...first, title: "새 코스 이름", start: { lat: 37.52, lng: 127.08 } });
  assert.equal(api.getRoutes().filter(item => item.id === first.id).length, 1);
  assert.equal(api.getRoutes().find(item => item.id === first.id).title, "새 코스 이름");
  assert.deepEqual(api.getRoutes().find(item => item.id === second.id), second);
});

test("invalid start coordinates cannot replace a previously saved course", async () => {
  const api = apiHarness();
  const original = await api.saveRoute(course());
  for (const start of [null, {}, { lat: NaN, lng: 127 }, { lat: 91, lng: 127 }, { lat: "37.5", lng: 127 }]) {
    await assert.rejects(api.saveRoute({ ...original, start }));
    assert.deepEqual(api.getRoutes().find(item => item.id === original.id), original);
  }
});

test("storage failure is reported and leaves the previous course available", async () => {
  const api = apiHarness();
  const original = await api.saveRoute(course());
  api.block();
  await assert.rejects(api.saveRoute({ ...original, title: "저장 실패" }), /저장/);
  assert.deepEqual(api.getRoutes().find(item => item.id === original.id), original);
});

test("legacy browser courses remain editable until successful migration", async () => {
  const legacy = { ...course("local-one") };
  delete legacy.owned;
  const futureItem = { id: "future-format", version: 2 };
  const api = apiHarness({ legacy: [legacy, futureItem] });
  await api.retryRoutes();
  const restored = api.getRoutes().find(item => item.id === legacy.id);
  assert.equal(restored.legacy, true);
  api.block();
  await assert.rejects(api.saveRoute(restored), /저장/);
  assert.equal(JSON.parse(api.storage.get("kbo-trip-routes-v1")).length, 2);
  api.allow();
  const migrated = await api.saveRoute(restored);
  assert.match(migrated.id, /^server-/);
  assert.equal(migrated.legacySourceId, legacy.id);
  assert.deepEqual(JSON.parse(api.storage.get("kbo-trip-routes-v1")), [futureItem]);
  const updated = await api.saveRoute({ ...migrated, title: "다시 저장" });
  assert.equal(updated.id, migrated.id);
  assert.equal(updated.legacySourceId, legacy.id);
});

test("course load errors stay honest and clear after retry", async () => {
  const api = apiHarness({ loadFailure: true, fetched: [course("server-listed")] });
  await api.retryRoutes();
  assert.match(api.useRoutesError(), /불러오지 못했어요/);
  assert.deepEqual(api.getRoutes(), []);
  api.recover();
  await api.retryRoutes();
  assert.equal(api.useRoutesError(), "");
  assert.ok(api.getRoutes().some(item => item.id === "server-listed"));
});

test("database samples are listed once and an empty database stays empty", async () => {
  const samples = Array.from({ length: 19 }, (_, index) => ({ ...course(index === 0 ? "fan-sajik-date" : `sample-${index}`), isSample: true }));
  const api = apiHarness({ fetched: samples });
  await api.retryRoutes();
  assert.equal(api.fetchCount(), 1);
  assert.equal(api.getRoutes().length, 19);
  assert.equal(new Set(api.getRoutes().map(route => route.id)).size, 19);
  assert.equal(api.getRoutes().find(route => route.id === "fan-sajik-date").stops.length, 1);

  const empty = apiHarness();
  await empty.retryRoutes();
  assert.deepEqual(empty.getRoutes(), []);
  assert.doesNotMatch(source, /sampleRoutes|additional-route-examples/);
});
