import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const modules = {};
function load(name) {
  const exports = {};
  const source = readFileSync(new URL(`../lib/chat/${name}.ts`, import.meta.url), "utf8");
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText,
    { exports, require: path => modules[path] });
  modules[`./${name}`] = exports;
  return exports;
}
load("types");
const directionExports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL("../lib/course-directions.ts", import.meta.url), "utf8"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText, { exports: directionExports });
modules["../course-directions"] = directionExports;
load("course");
const { parseChatRequest } = load("validation");
const { chatRoutePath, chatRouteSelectionKey } = load("route-path");
const a = { lat: 37.5, lng: 127 }, b = { lat: 37.51, lng: 127 }, c = { lat: 37.52, lng: 127 };
const leg = paths => ({ status: "ok", paths, instructions: [], seconds: 1, distance: 1 });
const plain = value => JSON.parse(JSON.stringify(value));

test("automatic geometry refresh does not invalidate a pending chat edit, while explicit segment changes do", () => {
  const ready = chatRoutePath([a, b, c], { legs: [leg([[a, b]]), leg([[b, c]])] }, null, false);
  assert.equal(chatRouteSelectionKey(ready), chatRouteSelectionKey(undefined));
  const selected = chatRoutePath([a, b, c], { legs: [leg([[a, b]]), leg([[b, c]])] }, 1, false);
  assert.notEqual(chatRouteSelectionKey(selected), chatRouteSelectionKey(ready));
});

test("a selected segment sends only that segment's actual road shape", () => {
  const bend = { lat: 37.515, lng: 127.004 };
  const result = chatRoutePath([a, b, c], { legs: [leg([[a, b]]), leg([[b, bend, c]])] }, 1, false);
  assert.deepEqual(plain(result.points), [b, bend, c]);
  assert.equal(result.source, "directions");
  assert.equal(result.label, "선택한 2번 구간");
});

test("drawn lines work before directions arrive, clearing stops clears the context", () => {
  assert.deepEqual(plain(chatRoutePath([a, b, c], undefined, null, true).points), [a, b, c]);
  assert.equal(chatRoutePath([a, b], undefined, null, false), undefined);
  assert.equal(chatRoutePath([a], undefined, null, true), undefined);
});

test("separate routing paths are not silently joined by an invented road", () => {
  const result = chatRoutePath([a, c], { legs: [leg([[a, b], [c, { lat: 37.53, lng: 127 }]])] }, null, false);
  assert.deepEqual(plain(result.breaks), [2]);
});

test("long road geometry is bounded without losing endpoints or a major bend", () => {
  const points = Array.from({ length: 2000 }, (_, i) => ({ lat: 37.5 + i / 100000, lng: 127 + Math.min(i, 1999 - i) / 100000 }));
  const result = chatRoutePath([points[0], points.at(-1)], { legs: [leg([points])] }, null, false);
  assert.ok(result.points.length <= 128);
  assert.deepEqual(plain(result.points[0]), points[0]);
  assert.deepEqual(plain(result.points.at(-1)), points.at(-1));
  assert.ok(result.points.some(p => p.lng > 127.009));
});

test("wire validation preserves only bounded coordinates and route metadata", () => {
  const result = parseChatRequest({ messages: [{ role: "user", content: "이 경로로 코스 짜줘" }], context: { routePath: {
    points: [{ ...a, private: "drop" }, b], source: "drawn", label: "선택한 경로", private: "drop",
  } } });
  assert.equal(JSON.stringify(result).includes("private"), false);
  assert.deepEqual(plain(result.context.routePath.points), [a, b]);
  for (const points of [[a], Array(129).fill(a), [{ lat: Infinity, lng: 127 }, b], [{ lat: true, lng: 127 }, b]]) {
    assert.throws(() => parseChatRequest({ messages: [{ role: "user", content: "코스" }], context: { routePath: { points, source: "drawn", label: "경로" } } }));
  }
});
