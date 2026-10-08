import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createRequire } from "node:module";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-course-state-"));
after(() => rmSync(scratch, { recursive: true, force: true }));
for (const name of ["course-state", "route-content", "community-rich-content", "client-id", "nearby-places", "course-directions", "drawn-course", "chat/course", "chat/current-course", "chat/writer-state", "route-draft", "stadiums", "stadium-locations", "google-lodging"]) {
  mkdirSync(dirname(join(scratch, `${name}.js`)), { recursive: true });
  writeFileSync(join(scratch, `${name}.js`), ts.transpileModule(readFileSync(join(frontend, "lib", `${name}.ts`), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText);
}
const require = createRequire(join(scratch, "entry.cjs"));
const { createCourseState, reduceCourse, courseContext, courseGeometryKey } = require("./course-state.js");
const { legKey } = require("./course-directions.js");
const { undoDrawnPoint, withUntrackedPoints } = require("./drawn-course.js");
const { restoreWriterCourse } = require("./chat/writer-state.js");
const { saveRouteDraft, readRouteDraft } = require("./route-draft.js");
const base = { stadiumCode: "JAMSIL", title: "", content: "", duration: "반나절", tags: [], stops: [], tab: "chat", travelMode: "walk" };
const origin = { lat: 37.51, lng: 127.08, name: "출발역" };
const place = (id, category, offset) => ({ visitId: id, placeId: id, name: id, category, phase: category === "STADIUM" ? "GAME" : "BEFORE", lat: 37.51 + offset, lng: 127.08 + offset });
const places = [place("100", "FOOD", .001), place("200", "CAFE", .002), place("300", "STADIUM", .003)];
const course = { places, stadiumCode: "JAMSIL", notes: [], origin, title: "추천 코스", content: "코스 안내", travelMode: "walk" };
const apply = (state, value = course) => reduceCourse(state, { type: "apply", course: value, stadiumCode: value.stadiumCode, how: "replace" });

test("collected stadium food retains source identity and category through manual selection and chat edits", () => {
  for (const [name, category] of [["치킨", "FOOD"], ["달콤커피", "CAFE"], ["세븐일레븐", "CONVENIENCE"]]) {
    const stop = { name, category: "구장 내부", lat: 37.512, lng: 127.072,
      placeId: "stadium-facility:SC_FOOD_JAMSIL_027:JAMSIL-027-5", visitId: "collected-visit" };
    const state = createCourseState({ ...base, stops: [stop] });
    const current = courseContext(state, undefined, stop).currentCourse;
    assert.equal(current.places[0].category, category);
    assert.equal(current.selectedPlace.category, category);
    const rebuilt = apply(createCourseState(base), { ...course, edit: true, places: current.places });
    assert.equal(rebuilt.data.stops[0].placeId, stop.placeId);
    assert.equal(courseContext(rebuilt).currentCourse.places[0].category, category);
  }
});

test("manual selection works for every category without inserting a preview into the course", () => {
  const categories = { "먹거리": "FOOD", "카페·디저트": "CAFE", "산책": "WALK", "관광 명소": "SPOT", "실내 놀거리": "INDOOR", "편의점": "CONVENIENCE", "숙박": "STAY", "야구장": "STADIUM" };
  for (const [category, expected] of Object.entries(categories)) {
    const selected = { name: category, category, placeId: `test:${expected}`, lat: 37.51, lng: 127.08 };
    const state = createCourseState(base);
    const preview = courseContext(state, undefined, selected).currentCourse;
    assert.equal(preview.selectedPlace.category, expected);
    assert.deepEqual(preview.places, []);
    assert.deepEqual(state.data.stops, []);
    const stop = { ...selected, visitId: `manual:${expected}` };
    const added = reduceCourse(state, { type: "patch", patch: { stops: [stop] } });
    const current = courseContext(added, undefined, stop).currentCourse;
    assert.deepEqual(current.selectedPlace, current.places[0]);
    assert.equal(current.selectedPlace.visitId, stop.visitId);
    assert.equal(courseContext(state, undefined, stop).currentCourse.selectedPlace, undefined);
  }
});

test("selection identifies the visit, never guesses among repeated visits or an origin", () => {
  const cafe = { name: "같은 카페", category: "카페·디저트", placeId: "repeat", lat: 37.51, lng: 127.08 };
  const stops = [{ ...cafe, visitId: "before" }, { ...cafe, visitId: "after" }];
  const state = createCourseState({ ...base, stops });
  assert.equal(courseContext(state, undefined, stops[1]).currentCourse.selectedPlace.visitId, "after");
  assert.equal(courseContext(state, undefined, cafe).currentCourse.selectedPlace, undefined);
  assert.equal(courseContext(state, undefined, { ...cafe, placeId: "route:origin" }).currentCourse.selectedPlace, undefined);
  assert.equal(courseContext(state, undefined, { ...cafe, lat: NaN }).currentCourse.selectedPlace, undefined);
  assert.equal(courseContext(state, undefined, null).currentCourse.selectedPlace, undefined);
});

test("creation -> manual order/mode -> chat cafe edit -> full deletion -> explicit restore share the same course", () => {
  let state = apply(createCourseState(base));
  const original = state;
  state = reduceCourse(state, { type: "patch", patch: { plannerCompleted: false, stops: [state.data.stops[1], state.data.stops[0], state.data.stops[2]] } });
  const [cafe, meal, stadium] = state.data.stops;
  const modes = { [legKey(origin, cafe)]: "car", [legKey(meal, stadium)]: "transit" };
  state = reduceCourse(state, { type: "patch", patch: { legModes: modes } });
  const sent = courseContext(state);
  assert.deepEqual(sent.currentCourse.places.map(p => p.visitId), ["200", "100", "300"]);
  assert.deepEqual(sent.currentCourse.legModes, modes);
  assert.deepEqual(sent.origin, origin);
  const replacement = { ...place("201", "CAFE", .004), visitId: "200" };
  state = apply(state, { ...course, edit: true, places: [replacement, places[0], places[2]], writerState: sent.currentCourse.writerState, legModes: modes });
  assert.deepEqual(state.data.stops.slice(1), [meal, stadium]);
  assert.equal(state.data.stops[0].name, "201");
  assert.deepEqual(state.data.legModes, { [legKey(meal, stadium)]: "transit" });
  assert.deepEqual(courseContext(state).currentCourse.places.map(p => p.name), ["201", "100", "300"]);
  while (state.data.stops.length || state.data.start) {
    const removed = undoDrawnPoint(state.data.stops, withUntrackedPoints(state.data.stops, []), state.data.start);
    state = reduceCourse(state, { type: "patch", patch: { stops: removed.stops, ...(removed.clearStart ? { start: undefined } : {}) } });
  }
  assert.deepEqual(courseContext(state).currentCourse.places, []);
  assert.equal(courseContext(state).origin, undefined);
  assert.equal(courseContext(state).currentCourse.writerState.origin, null);
  assert.deepEqual(state.data.legModes, {});
  state = reduceCourse(state, { type: "restore", data: original.data });
  assert.deepEqual(state.data, original.data);
});

test("legacy map origins migrate once and subsequent map points stay editable visits in chat", () => {
  const pin = { ...origin, category: "동선 지점", placeId: "map:a", isMapPoint: true };
  let state = createCourseState({ ...base, stops: [pin] });
  assert.equal(state.data.plannerMode, "draw");
  assert.equal(state.data.stops.length, 0);
  state = reduceCourse(state, { type: "patch", patch: { stops: [{ ...pin, lat: 37.512, placeId: "map:b" }] } });
  assert.deepEqual(state.data.start, origin);
  assert.equal(state.data.stops[0].name, "경유지 1");
  assert.equal(courseContext(state).currentCourse.places.length, 1);
  assert.equal(courseContext(state).currentCourse.places[0].label, "1");
  assert.deepEqual(createCourseState(state.data).data, state.data);
});

test("fresh recommendation never restores a deleted draft; reopening the saved edit is explicit", () => {
  const values = new Map();
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  const writerKey = "chat:member:1:s:9";
  const draft = { ...base, chatCourseKey: writerKey, title: "삭제한 편집본", start: undefined };
  assert.equal(saveRouteDraft(storage, writerKey, draft, null).status, "saved");
  const fresh = restoreWriterCourse(course, "member:1", "s", 9, storage, false);
  assert.equal(fresh.writerDraft, undefined);
  assert.equal(apply(createCourseState(base), fresh).data.stops.length, 3);
  const saved = restoreWriterCourse(course, "member:1", "s", 9, storage);
  const restored = apply(apply(createCourseState(base)), saved);
  assert.deepEqual(restored.data.stops, []);
  assert.equal(restored.data.title, "");
  assert.deepEqual(readRouteDraft(storage, writerKey).draft.data, JSON.parse(JSON.stringify(draft)));
});

test("late undo cannot overwrite later manual edits even when geometry returns to the same values", () => {
  const before = createCourseState(base), applied = apply(before);
  let current = reduceCourse(applied, { type: "patch", patch: { title: "내 제목" } });
  current = reduceCourse(current, { type: "patch", patch: { title: applied.data.title } });
  assert.equal(reduceCourse(current, { type: "undo", before, expectedRevision: applied.revision }), current);
  assert.deepEqual(reduceCourse(applied, { type: "undo", before, expectedRevision: applied.revision }).data, before.data);
});

test("stadium change and clear remove every old course reference atomically", () => {
  const current = apply(createCourseState(base));
  for (const action of [{ type: "stadium", code: "SAJIK" }, { type: "clear" }]) {
    const next = reduceCourse(current, action), context = courseContext(next);
    assert.equal(next.revision, current.revision + 1);
    assert.deepEqual(next.data.stops, []);
    assert.equal(next.data.start, undefined);
    assert.equal(next.data.travelMode, "walk");
    assert.equal(next.data.plannerCompleted, false);
    assert.equal(context.currentCourse.writerState.origin, null);
    assert.notEqual(courseGeometryKey(next.data), courseGeometryKey(current.data));
  }
});

test("recommendations never autofill empty story fields or replace an image-only story", () => {
  const imageDoc = { version: 1, blocks: [{ type: "image", id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" }] };
  for (const story of [{}, { content: "", contentDoc: imageDoc }, { content: "<p></p>", contentFormat: "html" }]) {
    for (const how of ["replace", "append"]) {
      const before = createCourseState({ ...base, ...story });
      const next = reduceCourse(before, { type: "apply", course, stadiumCode: "JAMSIL", how });
      for (const field of ["title", "content", "contentDoc", "contentFormat", "tab"]) {
        assert.strictEqual(next.data[field], before.data[field], `${how}: ${field}`);
      }
      assert.equal(next.data.stops.length, 3);
      assert.equal(next.data.plannerCompleted, true);
    }
  }
});

test("reopening a chat draft restores only its itinerary and preserves the active story", () => {
  const before = createCourseState({ ...base, title: "작성 중인 제목", content: "작성 중인 후기", duration: "하루", tags: ["친구"] });
  const draft = { ...apply(createCourseState(base)).data, title: "옛 제목", content: "옛 후기", tab: "write", travelMode: "transit", start: undefined, plannerCompleted: false };
  const next = apply(before, { ...course, writerDraft: draft });
  for (const field of ["title", "content", "contentDoc", "contentFormat", "tab", "tags", "duration"]) {
    assert.strictEqual(next.data[field], before.data[field], field);
  }
  assert.deepEqual(next.data.stops, draft.stops);
  assert.equal(next.data.start, undefined);
  assert.equal(next.data.travelMode, "transit");
  assert.equal(next.data.plannerCompleted, false);
});

test("course edits preserve user stories; tab navigation does not invalidate a request", () => {
  let state = apply(createCourseState(base));
  state = reduceCourse(state, { type: "patch", patch: { title: "직접 쓴 제목", content: "직접 쓴 후기" } });
  const next = apply(state, { ...course, title: "새 자동 제목", content: "새 안내" });
  assert.equal(next.data.title, "직접 쓴 제목");
  assert.equal(next.data.content, "직접 쓴 후기");
  assert.equal(reduceCourse(next, { type: "patch", patch: { tab: "write" } }).revision, next.revision);
});
