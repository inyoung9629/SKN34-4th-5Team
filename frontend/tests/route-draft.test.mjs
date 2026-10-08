import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createRequire } from "node:module";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-route-draft-test-"));
after(() => rmSync(scratch, { recursive: true }));
for (const name of ["client-id", "route-draft", "stadiums", "stadium-locations", "community-rich-content", "google-lodging", "course-directions", "drawn-course", "chat/course", "chat/current-course", "chat/writer-state"]) {
  const source = readFileSync(join(frontend, "lib", `${name}.ts`), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } });
  mkdirSync(dirname(join(scratch, `${name}.js`)), { recursive: true });
  writeFileSync(join(scratch, `${name}.js`), outputText);
}
const requireModule = createRequire(join(scratch, "entry.cjs"));
const { ROUTE_DRAFT_PREFIX, createDraftAutosave, latestNewDraftStadium, parseRouteDraft, readRouteDraft, recoverRouteDraft, removeRouteDraft, saveRouteDraft } = requireModule("./route-draft.js");
const data = { stadiumCode: "JAMSIL", title: "", content: "이야기", duration: "반나절", tags: ["첫 직관"], stops: [{ name: "잠실", category: "야구장", lat: 37.5, lng: 127, visitId: "v1", isMapPoint: true }], start: { lat: 37.4, lng: 127.1 }, tab: "chat", travelMode: "transit" };
const memory = () => {
  const values = new Map();
  return { values, getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
};

test("new writer resumes the latest storage slot including cleared story after a stadium change", () => {
  const storage = memory();
  const older = saveRouteDraft(storage, "new:GWANGJU", { ...data, stadiumCode: "GWANGJU", title: "옛 자동 제목", content: "옛 자동 설명" }, null);
  const latest = saveRouteDraft(storage, "new:CHANGWON", { ...data, stadiumCode: "DAEJEON", title: "", content: "" }, null);
  storage.setItem(ROUTE_DRAFT_PREFIX + "new:GWANGJU", JSON.stringify({ ...older.draft, updatedAt: "2026-10-07T01:00:00Z" }));
  storage.setItem(ROUTE_DRAFT_PREFIX + "new:CHANGWON", JSON.stringify({ ...latest.draft, updatedAt: "2026-10-08T01:00:00Z" }));
  const key = latestNewDraftStadium(storage, ["DAEJEON", "GWANGJU", "CHANGWON"]);
  assert.equal(key, "CHANGWON");
  const restored = readRouteDraft(storage, `new:${key}`).draft.data;
  assert.equal(restored.stadiumCode, "DAEJEON");
  assert.equal(restored.title, "");
  assert.equal(restored.content, "");
  assert.equal(latestNewDraftStadium(undefined, ["DAEJEON"]), undefined);
  assert.equal(latestNewDraftStadium(storage, ["JAMSIL"]), undefined);
});

test("conversation-linked draft restores title, named origin, completion and places into a blank writer", () => {
  const { restoreWriterCourse } = requireModule("./chat/writer-state.js");
  const storage = memory();
  const key = "chat:member:1:session-one:42";
  const original = { ...data, title: "내가 정한 잠실 코스", chatCourseKey: key, plannerCompleted: true,
    stops: [{ name: "카페", category: "카페·디저트", lat: 37.5, lng: 127.1, placeId: "cafe", visitId: "c" }],
    start: { lat: 37.4, lng: 127.05, name: "잠실새내역" } };
  assert.equal(saveRouteDraft(storage, key, original, null).status, "saved");
  const course = { stadiumCode: "JAMSIL", places: [], notes: [], title: "자동 제목" };
  const restored = restoreWriterCourse(course, "member:1", "session-one", 42, storage);
  assert.deepEqual(restored.writerDraft, original);
  assert.deepEqual(restored.writerState, { title: original.title, origin: original.start, completed: true });
  assert.equal(restored.places[0].name, "카페");
  assert.equal(restoreWriterCourse(course, "member:2", "session-one", 42, storage).writerDraft, undefined);
  assert.equal(restoreWriterCourse(course, "member:1", "other-session", 42, storage).writerDraft, undefined);
  assert.equal(restoreWriterCourse(course, "member:1", "session-one", 43, storage).writerDraft, undefined);
});

test("cleared title and origin stay cleared and editing mode survives a stored conversation draft", () => {
  const { restoreWriterCourse } = requireModule("./chat/writer-state.js");
  const storage = memory(), key = "chat:member:1:session-one:42";
  const draft = { ...data, title: "", start: undefined, plannerCompleted: false, chatCourseKey: key,
    stops: [{ name: "카페", category: "카페·디저트", lat: 37.5, lng: 127.1, placeId: "cafe" }] };
  saveRouteDraft(storage, key, draft, null);
  const restored = restoreWriterCourse({ stadiumCode: "JAMSIL", places: [], notes: [], origin: { lat: 37, lng: 127 } }, "member:1", "session-one", 42, storage);
  assert.equal(restored.origin, undefined);
  assert.deepEqual(restored.writerState, { title: "", origin: null, completed: false });
});

test("chat editing metadata survives draft recovery for another follow-up", () => {
  const storage = memory();
  const coursePlace = { name: "잠실", lat: 37.5, lng: 127, category: "STADIUM", phase: "GAME", time: "17:45", until: "22:00", completed: true, visitId: "v1" };
  const courseGame = { date: "2026-10-06", time: "18:30" };
  const courseProgress = { startMinute: 1320, gameEndMinute: 1320 };
  const draft = { ...data, stops: [{ ...data.stops[0], coursePlace, courseGame, courseProgress }] };
  const saved = saveRouteDraft(storage, "new:JAMSIL", draft, null);
  assert.equal(saved.status, "saved");
  assert.deepEqual(parseRouteDraft(saved.raw).data.stops[0].coursePlace, coursePlace);
  assert.deepEqual(parseRouteDraft(saved.raw).data.stops[0].courseGame, courseGame);
  assert.deepEqual(parseRouteDraft(saved.raw).data.stops[0].courseProgress, courseProgress);
  assert.equal(saveRouteDraft(storage, "bad", { ...draft, stops: [{ ...draft.stops[0], courseProgress: { startMinute: true } }] }, null).status, "error");
});

test("per-leg modes survive draft recovery while older drafts stay compatible", () => {
  const storage = memory();
  const key = "37.500000,127.000000>37.510000,127.010000";
  const saved = saveRouteDraft(storage, "new:JAMSIL", { ...data, legModes: { [key]: "car" } }, null);
  assert.equal(saved.status, "saved");
  assert.deepEqual(parseRouteDraft(saved.raw).data.legModes, { [key]: "car" });
  assert.equal(saveRouteDraft(storage, "bad", { ...data, legModes: { [key]: "plane" } }, null).status, "error");
});

test("versioned draft round-trips incomplete fields and route details", () => {
  const storage = memory();
  const saved = saveRouteDraft(storage, "copy:42", data, null);
  assert.equal(saved.status, "saved");
  assert.deepEqual(readRouteDraft(storage, "copy:42").draft?.data, data);
  assert.equal(readRouteDraft(storage, "edit:42").draft, undefined);
});

test("Google lodging draft persists references, not coordinates or content", () => {
  const storage = memory();
  const google = { name: "Google content", category: "호텔", address: "Google address", lat: 37.4, lng: 126.7, placeId: "google-ui-kit:fixture1", visitId: "v1" };
  const saved = saveRouteDraft(storage, "new:JAMSIL", { ...data, stops: [google] }, null);
  assert.equal(saved.status, "saved");
  const wire = JSON.parse(saved.raw).data.stops[0];
  assert.deepEqual(wire, { name: "선택한 숙소", category: "숙박", lat: null, lng: null, placeId: google.placeId, visitId: "v1" });
  const restored = parseRouteDraft(saved.raw).data.stops[0];
  assert.ok(Number.isNaN(restored.lat));
  assert.equal(saveRouteDraft(storage, "new:JAMSIL", { ...data, stops: [google] }, saved.raw).status, "unchanged");
});

test("route story styles and image references survive draft save on HTTP", () => {
  const storage = memory();
  const run = { text: "카페 방문", font: "serif", size: 20, color: "#246bf3", bold: true, italic: false, underline: false };
  const contentDoc = { version: 1, blocks: [
    { type: "paragraph", align: "center", runs: [run] },
    { type: "image", id: "d65e8543-1267-4530-a156-63be55546568" },
  ] };
  const saved = saveRouteDraft(storage, "new:JAMSIL", { ...data, content: "카페 방문\n[이미지]", contentDoc }, null);
  assert.equal(saved.status, "saved");
  assert.deepEqual(readRouteDraft(storage, "new:JAMSIL").draft.data.contentDoc, contentDoc);
  assert.ok(saved.draft.revision);
});

test("draft save still works when insecure HTTP has no crypto.randomUUID", () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", { value: {}, configurable: true });
  try {
    const saved = saveRouteDraft(memory(), "new:JAMSIL", data, null);
    assert.equal(saved.status, "saved");
    assert.match(saved.draft.revision, /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i);
  } finally {
    if (previous) Object.defineProperty(globalThis, "crypto", previous);
    else delete globalThis.crypto;
  }
});

test("invalid JSON, versions, types, and coordinates fail closed without deletion", () => {
  for (const raw of ["{", JSON.stringify({ version: 2, data }), JSON.stringify({ version: 1, revision: "r", updatedAt: new Date().toISOString(), data: { ...data, stadiumCode: "UNKNOWN_STADIUM" } }), JSON.stringify({ version: 1, revision: "r", updatedAt: new Date().toISOString(), data: { ...data, accessToken: "must-not-survive" } }), JSON.stringify({ version: 1, revision: "r", updatedAt: new Date().toISOString(), data: { ...data, stops: [{ ...data.stops[0], lat: 999 }] } })]) assert.equal(parseRouteDraft(raw), undefined);
  const storage = memory(); storage.values.set(ROUTE_DRAFT_PREFIX + "new:JAMSIL", "{");
  assert.equal(saveRouteDraft(storage, "new:JAMSIL", data, null).status, "conflict");
  assert.equal(storage.values.get(ROUTE_DRAFT_PREFIX + "new:JAMSIL"), "{");
  assert.equal(saveRouteDraft(memory(), "new:UNKNOWN", { ...data, stadiumCode: "UNKNOWN_STADIUM" }, null).status, "error");
});

test("clean reentry prefers latest disk while unsaved memory retains its original conflict base", () => {
  const storage = memory();
  const old = saveRouteDraft(storage, "new:JAMSIL", { ...data, title: "A old" }, null); assert.equal(old.status, "saved");
  const latest = saveRouteDraft(storage, "new:JAMSIL", { ...data, title: "B latest" }, old.raw); assert.equal(latest.status, "saved");
  assert.equal(recoverRouteDraft(readRouteDraft(storage, "new:JAMSIL")).data.title, "B latest");
  const recovered = recoverRouteDraft(readRouteDraft(storage, "new:JAMSIL"), { data: { ...data, title: "A unsaved" }, expectedRaw: old.raw });
  assert.equal(recovered.dirty, true); assert.equal(recovered.expectedRaw, old.raw);
  assert.equal(saveRouteDraft(storage, "new:JAMSIL", { ...recovered.data, travelMode: "car" }, recovered.expectedRaw).status, "conflict");
  assert.equal(readRouteDraft(storage, "new:JAMSIL").draft.data.title, "B latest");
});

test("unchanged writes are skipped and another tab revision is not overwritten", () => {
  const storage = memory();
  const first = saveRouteDraft(storage, "new:JAMSIL", data, null); assert.equal(first.status, "saved");
  assert.equal(saveRouteDraft(storage, "new:JAMSIL", data, first.raw).status, "unchanged");
  const other = saveRouteDraft(storage, "new:JAMSIL", { ...data, title: "다른 탭" }, first.raw); assert.equal(other.status, "saved");
  assert.equal(saveRouteDraft(storage, "new:JAMSIL", { ...data, title: "현재 탭" }, first.raw).status, "conflict");
  assert.equal(readRouteDraft(storage, "new:JAMSIL").draft.data.title, "다른 탭");
  assert.equal(removeRouteDraft(storage, "new:JAMSIL", first.raw), false);
  assert.equal(readRouteDraft(storage, "new:JAMSIL").draft.data.title, "다른 탭");
  assert.equal(removeRouteDraft(storage, "new:JAMSIL", other.raw), true);
  assert.equal(readRouteDraft(storage, "new:JAMSIL").draft, undefined);
});

test("autosave debounces at 1s, flushes continuous edits at 5s, and stops resurrection", () => {
  let now = 0, id = 0, calls = 0; const jobs = new Map();
  const timers = {
    setTimeout(fn, ms) { const key = ++id; jobs.set(key, { fn, at: now + ms, interval: 0 }); return key; },
    clearTimeout(key) { jobs.delete(key); },
    setInterval(fn, ms) { const key = ++id; jobs.set(key, { fn, at: now + ms, interval: ms }); return key; },
    clearInterval(key) { jobs.delete(key); },
  };
  const tick = ms => { const end = now + ms; while (true) { const next = [...jobs].sort((a, b) => a[1].at - b[1].at)[0]; if (!next || next[1].at > end) break; now = next[1].at; const [key, job] = next; if (job.interval) job.at += job.interval; else jobs.delete(key); job.fn(); } now = end; };
  const autosave = createDraftAutosave(() => calls++, timers);
  for (let index = 0; index < 5; index++) { autosave.changed(); tick(900); }
  autosave.changed(); assert.equal(calls, 0); tick(500); assert.equal(calls, 1); // periodic flush despite continuous debounce resets
  autosave.stop(); tick(10000); assert.equal(calls, 1);
});

test("storage failures report error and keep caller-owned snapshot usable", () => {
  const storage = { getItem: () => null, setItem: () => { throw new Error("quota"); }, removeItem: () => {} };
  assert.equal(saveRouteDraft(storage, "new:JAMSIL", data, null).status, "error");
  assert.equal(data.content, "이야기");
});
