import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

function setup(api) {
  const source = readFileSync(new URL("../lib/routes.ts", import.meta.url), "utf8");
  const code = ts.transpileModule(source + '\nexport function seedTestRoute() { serverRoutes = [{ id: "test", likes: 0 } as TripRoute]; }', {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const testModule = { exports: {} };
  new Function("require", "module", "exports", "window", code)(
    name => name === "./course-api" ? api : name === "react" ? { useSyncExternalStore() {} } : {}, testModule, testModule.exports,
    { dispatchEvent() {}, localStorage: { getItem() { return null; } } },
  );
  testModule.exports.seedTestRoute();
  testModule.exports.resetRouteLikes(1);
  return testModule.exports;
}

test("initial reaction requests are deduplicated", async () => {
  let count = 0;
  const store = setup({ fetchCourseReaction: async () => { count++; return { liked: true, likes: 5 }; } });
  await Promise.all([store.loadRouteLike("test"), store.loadRouteLike("test")]);
  assert.equal(count, 1);
  assert.equal(store.getRoutes()[0].likes, 5);
});

test("toggles read server state and serialize updates", async () => {
  let liked = false;
  const writes = [];
  const store = setup({
    fetchCourseReaction: async () => ({ liked, likes: Number(liked) }),
    setCourseReaction: async (_id, desired) => {
      writes.push(desired); liked = desired;
      return { liked, likes: Number(liked) };
    },
  });
  assert.deepEqual(await Promise.all([store.toggleRouteLike("test"), store.toggleRouteLike("test")]), [true, false]);
  assert.deepEqual(writes, [true, false]);
  assert.equal(store.getRoutes()[0].likes, 0);
});

test("removing a cached like stays unliked after another tab already removed it", async () => {
  let liked = true;
  const writes = [];
  const store = setup({
    fetchCourseReaction: async () => ({ liked, likes: Number(liked) }),
    setCourseReaction: async (_id, desired) => {
      writes.push(desired); liked = desired;
      return { liked, likes: Number(liked) };
    },
  });
  await store.loadRouteLike("test");
  assert.deepEqual(store.useLikedRoutes(), ["test"]);
  assert.equal(store.getRoutes()[0].likes, 1);
  liked = false;
  assert.equal(await store.setRouteLike("test", false), false);
  assert.deepEqual(writes, [false]);
  assert.equal(liked, false);
  assert.equal(store.getRoutes()[0].likes, 0);
  assert.deepEqual(store.useLikedRoutes(), []);
});

test("account changes discard stale reads and prevent subsequent writes", async () => {
  let release;
  let writes = 0;
  const store = setup({
    fetchCourseReaction: () => new Promise(resolve => { release = resolve; }),
    setCourseReaction: async () => { writes++; return { liked: true, likes: 1 }; },
  });
  const request = store.toggleRouteLike("test");
  await new Promise(resolve => setImmediate(resolve));
  store.resetRouteLikes(2);
  release({ liked: false, likes: 0 });
  await assert.rejects(request, /로그인 상태/);
  assert.equal(writes, 0);
});

test("failed updates preserve the previous count and allow retry", async () => {
  let fail = true;
  const store = setup({
    fetchCourseReaction: async () => ({ liked: false, likes: 4 }),
    setCourseReaction: async () => {
      if (fail) throw new Error("network");
      return { liked: true, likes: 5 };
    },
  });
  await store.loadRouteLike("test");
  await assert.rejects(store.toggleRouteLike("test"), /network/);
  assert.equal(store.getRoutes()[0].likes, 4);
  fail = false;
  assert.equal(await store.toggleRouteLike("test"), true);
  assert.equal(store.getRoutes()[0].likes, 5);
});
