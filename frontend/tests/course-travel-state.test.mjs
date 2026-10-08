import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const source = ts.transpileModule(readFileSync(new URL("../components/course-travel.tsx", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;

// Exercise the actual hook with GPS callbacks controlled by the test. Directions are disabled.
function harness() {
  let cursor = 0, dirty = false;
  const slots = [], pending = [], gps = [], changes = [];
  let control = { start: { lat: 37.5, lng: 127, name: "기존 출발지" }, mode: "walk", revision: 1, originSource: "custom" };
  function effect(fn, deps) {
    const i = cursor++, old = slots[i];
    if (!old || !deps || deps.some((v, n) => !Object.is(v, old.deps?.[n]))) {
      slots[i] = { deps, cleanup: old?.cleanup };
      pending.push(() => { old?.cleanup?.(); slots[i].cleanup = fn(); });
    }
  }
  const hooks = {
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === "function" ? initial() : initial;
      return [slots[i], value => { const next = typeof value === "function" ? value(slots[i]) : value; if (!Object.is(next, slots[i])) { slots[i] = next; dirty = true; } }]; },
    useRef(value) { const i = cursor++; return slots[i] ??= { current: value }; },
    useMemo(fn) { cursor++; return fn(); }, useCallback(fn) { cursor++; return fn; },
    useEffect: effect, useLayoutEffect: effect,
  };
  const sandbox = { exports: {}, AbortController, console, setTimeout, clearTimeout,
    require(id) {
      if (id === "react") return hooks;
      if (id === "react/jsx-runtime") return require(id);
      if (id.endsWith("/geolocation")) return { requestCurrentLocation(success, failure) { gps.push({ success, failure }); } };
      if (id.endsWith("/course-directions")) return { courseLegModes: () => [], activeLegModes: () => ({}), TRAVEL_MODES: [] };
      if (id.endsWith("/google-lodging")) return { locatedStop: () => true, isLodgingReference: () => false, canRequestDirections: () => true };
      return {};
    },
  };
  vm.runInNewContext(source, sandbox);
  function render() {
    let hook, count = 0;
    do {
      dirty = false; cursor = 0;
      hook = sandbox.exports.useCourseDirections([], false, undefined, undefined, "walk", mode => changes.push({ mode }), undefined, {}, undefined,
        { ...control, onStartChange(start, originSource) { changes.push({ start, originSource }); } });
      pending.splice(0).forEach(fn => fn());
      if (++count > 10) throw Error("hook did not settle");
    } while (dirty);
    return hook;
  }
  return { render, gps, changes, update(patch) { control = { ...control, ...patch }; }, get control() { return control; } };
}

test("writer origin and travel mode always come from the canonical course, including clearing the origin", () => {
  const h = harness();
  let travel = h.render();
  assert.equal(travel.location.name, "기존 출발지");
  travel.pickLocation({ lat: 37.6, lng: 127.1 });
  assert.deepEqual(h.changes[0], { start: { lat: 37.6, lng: 127.1 }, originSource: "custom" });
  h.update({ start: h.changes[0].start, revision: 2 });
  travel = h.render();
  assert.equal(travel.location.lat, 37.6);
  travel.setMode("transit");
  assert.deepEqual(h.changes.at(-1), { mode: "transit" });
  h.update({ mode: "transit", start: undefined, revision: 3 });
  travel = h.render();
  assert.equal(travel.location, null);
  assert.equal(travel.origin, "first");
  assert.equal(travel.mode, "transit");
  travel.syncStart({ lat: 37.7, lng: 127.2 });
  assert.equal(h.render().location, null, "a stale local origin cannot revive a deleted canonical origin");
});

test("late GPS success or failure cannot change a newer course or reopen its origin picker", () => {
  const h = harness();
  h.render().chooseCurrent();
  h.render();
  h.update({ start: undefined, revision: 2 });
  let travel = h.render();
  h.gps[0].success({ lat: 38, lng: 128 });
  h.gps[0].failure("위치 확인 실패");
  travel = h.render();
  assert.equal(h.changes.length, 0);
  assert.equal(travel.location, null);
  assert.equal(travel.picking, false);
  assert.equal(travel.locating, false);
  travel.chooseCurrent();
  h.gps[1].success({ lat: 37.6, lng: 127.1 });
  assert.deepEqual(h.changes, [{ start: { lat: 37.6, lng: 127.1 }, originSource: "current" }]);
});
