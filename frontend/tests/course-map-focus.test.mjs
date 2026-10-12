import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

const source = ts.transpileModule(readFileSync(new URL("../lib/course-map-focus.ts", import.meta.url), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;

function harness() {
  const listeners = new Map(), frames = new Map(), map = {};
  let nextFrame = 0, fits = 0;
  const sandbox = { exports: {},
    requestAnimationFrame(fn) { frames.set(++nextFrame, fn); return nextFrame; },
    cancelAnimationFrame(id) { frames.delete(id); },
  };
  vm.runInNewContext(source, sandbox);
  const events = {
    addListener(target, event, listener) { assert.equal(target, map); listeners.set(event, listener); },
    removeListener(target, event, listener) { assert.equal(target, map); assert.equal(listeners.get(event), listener); listeners.delete(event); },
  };
  const emit = event => listeners.get(event)?.();
  const focus = sandbox.exports.createCourseMapFocus(map, { event: events }, () => { fits++; });
  return { focus, emit, frames, listeners, get fits() { return fits; },
    frame() { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(fn => fn()); } };
}

test("a completed course is focused on the next frame and ordinary map navigation stays free", () => {
  const h = harness();
  h.focus.show(); h.frame();
  assert.equal(h.fits, 1);
  h.emit("zoom_start"); h.emit("idle"); h.frame();
  h.emit("dragstart"); h.emit("dragend"); h.emit("idle"); h.frame();
  assert.equal(h.fits, 1, "fitting and later gestures must not repeatedly snap the map back");
});

test("a course completed during dragging is shown immediately and refitted after the gesture settles", () => {
  const h = harness();
  h.emit("dragstart"); h.focus.show(); h.frame();
  assert.equal(h.fits, 1);
  h.emit("idle"); h.frame();
  assert.equal(h.fits, 1, "an idle notification while still dragging cannot consume the pending refit");
  h.emit("dragend"); h.emit("idle"); h.frame();
  assert.equal(h.fits, 2);
  h.emit("idle"); h.frame();
  assert.equal(h.fits, 2);
});

test("an in-flight zoom cannot leave the completed course outside the final viewport", () => {
  const h = harness();
  h.emit("zoom_start"); h.focus.show(); h.frame();
  assert.equal(h.fits, 1);
  h.emit("idle"); h.frame();
  assert.equal(h.fits, 2);
  h.emit("zoom_start"); h.emit("idle"); h.frame();
  assert.equal(h.fits, 2);
});

test("gestures starting between completion and the drawing frame still receive the final refit", () => {
  const h = harness();
  h.focus.show(); h.emit("dragstart"); h.frame();
  h.emit("zoom_start"); h.emit("dragend"); h.emit("idle"); h.frame();
  assert.equal(h.fits, 2);
});

test("new completions coalesce and leaving the map cancels callbacks and listeners", () => {
  const h = harness();
  h.focus.show(); h.focus.show();
  assert.equal(h.frames.size, 1);
  h.focus.dispose(); h.frame(); h.focus.show();
  assert.equal(h.frames.size, 0);
  assert.equal(h.listeners.size, 0);
  assert.equal(h.fits, 0);
});
