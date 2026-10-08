import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../lib/drawn-course.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } });
const { coursePointLabel, renumberMapPoints, undoDrawnPoint, withCourseStart, withUntrackedPoints } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const point = (id) => ({ placeId: id, name: "", category: "직접 지정", lat: 37.51, lng: 127.07, isMapPoint: true });
const cafe = { placeId: "cafe", name: "동네 카페", category: "카페", lat: 37.515, lng: 127.073 };

test("chatbot visits are removed before their separate origin and the final origin can be removed", () => {
  let stops = [{ ...cafe, isDrawnPoint: true, visitId: "chat-cafe" }, { ...point("stadium"), name: "구장" }];
  let history = [], start = { lat: 37.52, lng: 127.08, name: "내 출발지" };
  for (const expected of ["구장", cafe.name, start.name]) {
    const result = undoDrawnPoint(stops, withUntrackedPoints(stops, history), start);
    assert.equal(result.removed.name, expected);
    assert.equal(result.clearStart, expected === "내 출발지");
    stops = result.stops; history = result.history;
    if (result.clearStart) start = undefined;
  }
  assert.deepEqual(stops, []);
  assert.equal(start, undefined);
  assert.equal(undoDrawnPoint(stops, history, start).removed, undefined);
});

test("right click does not clear a separate origin while protected preexisting visits remain", () => {
  const result = undoDrawnPoint([cafe], [], { lat: 37.52, lng: 127.08 });
  assert.equal(result.clearStart, false);
  assert.deepEqual(result.stops, [cafe]);
});

test("an origin without a name or visits can be cleared, including stale draw history", () => {
  const result = undoDrawnPoint([], ["already-deleted"], { lat: 37.52, lng: 127.08 });
  assert.equal(result.clearStart, true);
  assert.equal(result.removed.name, "출발지");
  assert.deepEqual(result.history, []);
});

test("saved separate origin is restored before visits without mutating their stored order", () => {
  const stops = renumberMapPoints([point("a"), cafe]);
  const before = JSON.stringify(stops);
  const start = { lat: 37.518, lng: 127.079 };
  const restored = withCourseStart(stops, start);
  assert.deepEqual(restored.map((stop) => [stop.lat, stop.lng]), [[start.lat, start.lng], [stops[0].lat, stops[0].lng], [cafe.lat, cafe.lng]]);
  assert.deepEqual(restored.map((_, index) => coursePointLabel(restored, index)), ["출발", "1", "2"]);
  assert.equal(restored[1].name, "경유지 1");
  assert.equal(restored[2].name, cafe.name);
  assert.equal(JSON.stringify(stops), before);
});

test("legacy routes keep their first stop and a full course retains all twelve visits", () => {
  const stops = renumberMapPoints(Array.from({ length: 12 }, (_, index) => point(String(index))));
  assert.equal(withCourseStart(stops), stops);
  assert.equal(withCourseStart(stops, { lat: 37.5, lng: 127.07 }).length, 13);
});

test("drawn start and subsequent waypoints keep labels and coordinates after serialization", () => {
  const stops = renumberMapPoints([point("a"), point("b"), point("c")]);
  assert.deepEqual(stops.map((_, i) => coursePointLabel(stops, i)), ["출발", "1", "2"]);
  const restored = JSON.parse(JSON.stringify(stops));
  assert.deepEqual(restored.map((stop) => stop.name), ["출발지", "경유지 1", "경유지 2"]);
  assert.deepEqual(restored, stops);
});

test("right-click undo removes one newest point at a time, including the lone start", () => {
  let state = { stops: renumberMapPoints([point("a"), point("b"), point("c")]), history: ["a", "b", "c"] };
  for (const expected of [["a", "b"], ["a"], []]) {
    state = undoDrawnPoint(state.stops, state.history);
    assert.deepEqual(state.stops.map((stop) => stop.placeId), expected);
    assert.deepEqual(state.history, expected);
  }
  assert.equal(undoDrawnPoint(state.stops, state.history).removed, undefined);
});

test("points added outside the planner are undone newest-first after the recorded ones", () => {
  const drawn = (id) => ({ visitId: id, name: id, category: "먹거리", lat: 37.51, lng: 127.07, isDrawnPoint: true });
  const stops = [drawn("chat-1"), drawn("chat-2"), cafe, drawn("chat-3")];
  let history = withUntrackedPoints(stops, []);
  assert.deepEqual(history, ["chat-1", "chat-2", "chat-3"]);
  let state = undoDrawnPoint(stops, history);
  assert.equal(state.removed.visitId, "chat-3");
  state = undoDrawnPoint(state.stops, withUntrackedPoints(state.stops, state.history));
  assert.equal(state.removed.visitId, "chat-2");
  assert.deepEqual(withUntrackedPoints(state.stops, ["chat-1"]), ["chat-1"]);
  assert.deepEqual(withUntrackedPoints([cafe], ["gone"]), ["gone"]);
});

test("undo follows creation order after reordering and skips already deleted points", () => {
  const stops = renumberMapPoints([point("a"), point("c"), cafe, point("b")]);
  const result = undoDrawnPoint(stops, ["a", "b", "c", "deleted"]);
  assert.equal(result.removed.placeId, "c");
  assert.deepEqual(result.stops.map((stop) => stop.placeId), ["a", "cafe", "b"]);
  assert.equal(result.stops[2].name, "경유지 2");
  assert.deepEqual(stops.map((stop) => stop.placeId), ["a", "c", "cafe", "b"]);
});

test("extending a places course preserves existing places through every undo", () => {
  const stops = renumberMapPoints([cafe, point("a")]);
  assert.deepEqual(stops.map((_, i) => coursePointLabel(stops, i)), ["1", "2"]);
  assert.deepEqual(undoDrawnPoint(stops, ["a"]).stops, [cafe]);
  assert.deepEqual(undoDrawnPoint([cafe], ["cafe"]).stops, [cafe]);
});

test("deleting a start promotes the next manual point and renumbers remaining waypoints", () => {
  const original = renumberMapPoints([point("a"), point("b"), point("c")]);
  assert.deepEqual(renumberMapPoints(original.slice(1)).map((stop) => stop.name), ["출발지", "경유지 1"]);
});

test("a shop can be a drawn origin without losing its name or provider identity", () => {
  const shop = { ...cafe, isDrawnPoint: true };
  const stops = renumberMapPoints([shop, point("a")]);
  assert.equal(coursePointLabel(stops, 0), "출발");
  assert.equal(coursePointLabel(stops, 1), "1");
  assert.equal(stops[0].name, cafe.name);
  assert.equal(stops[0].placeId, cafe.placeId);
  assert.equal(stops[1].name, "경유지 1");
});

test("mixed shop and free point undo follows creation order and protects preexisting places", () => {
  const shop = { ...cafe, placeId: "new-shop", isDrawnPoint: true };
  let state = { stops: renumberMapPoints([cafe, point("a"), shop, point("b")]), history: ["a", "new-shop", "b"] };
  for (const expected of [["cafe", "a", "new-shop"], ["cafe", "a"], ["cafe"]]) {
    state = undoDrawnPoint(state.stops, state.history);
    assert.deepEqual(state.stops.map((stop) => stop.placeId), expected);
  }
});


test("undo removes only the latest visit when a place is revisited", () => {
  const first = { ...cafe, isDrawnPoint: true, visitId: "visit-a" };
  const second = { ...cafe, isDrawnPoint: true, visitId: "visit-b" };
  const result = undoDrawnPoint([first, point("middle"), second], ["visit-a", "middle", "visit-b"]);
  assert.equal(result.removed.visitId, "visit-b");
  assert.equal(result.stops.length, 2);
  assert.equal(result.stops[0].visitId, "visit-a");
});
