import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../lib/course-api.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } });
const lodgingCode = ts.transpileModule(readFileSync(new URL("../lib/google-lodging.ts", import.meta.url), "utf8"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const lodgingModule = { exports: {} };
new Function("module", "exports", lodgingCode)(lodgingModule, lodgingModule.exports);
const directionsModule = { exports: {} };
new Function("module", "exports", ts.transpileModule(readFileSync(new URL("../lib/course-directions.ts", import.meta.url), "utf8"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText)(directionsModule, directionsModule.exports);

function harness(memberFetch = async () => { throw new Error("unexpected authenticated request"); }) {
  const storage = new Map();
  let blocked = false;
  const window = { localStorage: {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => { if (blocked) throw new Error("quota"); storage.set(key, value); },
    removeItem: key => storage.delete(key),
  } };
  let memberHandler = memberFetch;
  class ApiError extends Error {}
  const apiRequest = async (path, init, fetcher = fetch) => {
    const response = await fetcher(path, init);
    if (response.status === 204) return null;
    const value = await response.json().catch(() => null);
    if (!response.ok) throw new ApiError(value?.detail ?? "요청 실패");
    return value;
  };
  const requireDependency = name => name === "./api/client"
    ? { ApiError, apiRequest }
    : name === "./member-auth-request"
      ? { memberFetch: (...args) => memberHandler(...args) }
      : name === "./google-lodging" ? lodgingModule.exports : name === "./course-directions" ? directionsModule.exports : (() => { throw new Error(`unexpected import: ${name}`); })();
  const testModule = { exports: {} };
  new Function("module", "exports", "window", "require", outputText)(testModule, testModule.exports, window, requireDependency);
  return { ...testModule.exports, storage, block: () => { blocked = true; }, member: handler => { memberHandler = handler; } };
}

test("default course writes use the member JWT request path while reads stay public", async () => {
  const requests = [];
  const api = harness(async (url, init) => {
    requests.push([url, init.method]);
    return Response.json(apiCourse({ editToken: "edit-secret" }), { status: 201 });
  });
  await api.fetchCourses(async () => Response.json([]));
  await api.persistCourse(route());
  assert.deepEqual(requests, [["/api/v1/courses/", "POST"]]);
});

const apiCourse = changes => ({
  id: "123e4567-e89b-12d3-a456-426614174000", title: "잠실 직관 코스", stadium: "잠실야구장",
  content: "", duration: "반나절", tags: [], author: "익명", createdAt: "2026-09-12T12:00:00Z",
  updatedAt: "2026-09-12T12:00:00Z", routeNumber: "000020", stops: [{ position: 0, name: "카페", category: "카페", lat: 37.51, lng: 127.07 }],
  ...changes,
});
const route = changes => ({
  id: "", title: "잠실 직관 코스", stadium: "잠실야구장", description: "카페", content: "", tags: [], duration: "반나절",
  cover: "/images/stadium-night.jpg", author: "익명", likes: 0, isSample: false, owned: true,
  createdAt: "2026-09-12T12:00:00Z", start: { lat: 37.5, lng: 127.1 },
  stops: [{ name: "카페", category: "카페", placeId: "p1", lat: 37.51, lng: 127.07 }], ...changes,
});

test("create sends ordered stops and stores only the returned edit token", async () => {
  const api = harness();
  const saved = await api.persistCourse(route(), async (url, init) => {
    assert.equal(url, "/api/v1/courses/");
    assert.equal(init.method, "POST");
    assert.equal(init.redirect, "error");
    assert.ok(init.signal instanceof AbortSignal);
    assert.deepEqual(JSON.parse(init.body), {
      title: "잠실 직관 코스", stadium: "잠실야구장", content: "", contentDoc: null, contentFormat: "", duration: "반나절", tags: [],
      startLat: 37.5, startLng: 127.1, stops: [{ name: "카페", category: "카페", placeId: "p1", lat: 37.51, lng: 127.07, position: 0 }],
      travelMode: "walk", legModes: {},
    });
    return Response.json(apiCourse({ editToken: "edit-secret" }), { status: 201 });
  });
  assert.equal(saved.id, apiCourse({}).id);
  assert.equal(saved.owned, true);
  assert.deepEqual([...api.storage], [[`kbo-course-edit-token:${saved.id}`, "edit-secret"]]);
});

test("course persistence sends edited visits but leaves transient chat metadata in the draft", async () => {
  const api = harness();
  const original = route();
  const stop = { ...original.stops[0], coursePlace: { name: "카페", phase: "BEFORE", time: "12:13", completed: true }, courseGame: { date: "2026-10-05", time: "14:00" }, courseProgress: { startMinute: 800 } };
  await api.persistCourse({ ...original, stops: [stop] }, async (_url, init) => {
    assert.deepEqual(JSON.parse(init.body).stops, [{ ...original.stops[0], position: 0 }]);
    return Response.json(apiCourse({ editToken: "test-edit-token" }), { status: 201 });
  });
  assert.equal(stop.coursePlace.time, "12:13");
});

test("Google lodging writes ID-only references and reads unresolved coordinates", async () => {
  const api = harness();
  const stop = { name: "Do not store Google content", address: "Do not store", category: "호텔", lat: 37.4, lng: 126.7, placeId: "google-ui-kit:fixture_1", visitId: "v1", tourContentId: "bad", isMapPoint: true };
  const result = await api.persistCourse(route({ stops: [stop] }), async (_url, init) => {
    const saved = JSON.parse(init.body).stops[0];
    assert.deepEqual(saved, { name: "선택한 숙소", category: "숙박", lat: null, lng: null, placeId: stop.placeId, visitId: "v1", position: 0 });
    return Response.json(apiCourse({ editToken: "test-token", stops: [saved] }), { status: 201 });
  });
  assert.ok(Number.isNaN(result.stops[0].lat));
  assert.equal(result.stops[0].address, undefined);
});

test("list ownership, update, and delete all use the per-course token", async () => {
  const api = harness();
  const id = apiCourse({}).id;
  api.storage.set(`kbo-course-edit-token:${id}`, "edit-secret");
  const listed = await api.fetchCourses(async () => Response.json([apiCourse({})]));
  assert.equal(listed[0].owned, true);
  await api.persistCourse(route({ id }), async (url, init) => {
    assert.equal(url, `/api/v1/courses/${id}/`);
    assert.equal(init.method, "PATCH");
    assert.equal(init.headers["X-Course-Edit-Token"], "edit-secret");
    return Response.json(apiCourse({}));
  });
  await api.removeCourse(id, async (url, init) => {
    assert.equal(url, `/api/v1/courses/${id}/`);
    assert.equal(init.headers["X-Course-Edit-Token"], "edit-secret");
    return new Response(null, { status: 204 });
  });
  assert.equal(api.storage.size, 0);
});

test("a missing edit token fails closed", async () => {
  const api = harness();
  await assert.rejects(api.persistCourse(route({ id: apiCourse({}).id }), async () => { throw new Error("must not fetch"); }), /편집 토큰/);
});

test("token storage failure keeps session edit access and never retries POST", async () => {
  const api = harness();
  api.block();
  const created = await api.persistCourse(route(), async (_url, init) => {
    assert.equal(init.method, "POST");
    return Response.json(apiCourse({ editToken: "edit-secret" }), { status: 201 });
  });
  assert.match(created.saveWarning, /새로고침하면 읽기 전용/);
  assert.equal(api.storage.size, 0);
  await api.persistCourse({ ...created, title: "변경" }, async (url, init) => {
    assert.equal(url, `/api/v1/courses/${created.id}/`);
    assert.equal(init.method, "PATCH");
    assert.equal(init.headers["X-Course-Edit-Token"], "edit-secret");
    return Response.json(apiCourse({ title: "변경" }));
  });
});

test("clearing an existing start sends explicit null coordinates", async () => {
  const api = harness();
  const id = apiCourse({}).id;
  api.storage.set(`kbo-course-edit-token:${id}`, "edit-secret");
  await api.persistCourse(route({ id, start: undefined }), async (_url, init) => {
    const body = JSON.parse(init.body);
    assert.equal(body.startLat, null);
    assert.equal(body.startLng, null);
    return Response.json(apiCourse({}));
  });
});

test("course requests have a bounded timeout and a useful network error", async () => {
  const api = harness();
  await assert.rejects(api.fetchCourses(async (_url, init) => {
    assert.ok(init.signal instanceof AbortSignal);
    throw new TypeError("network failed");
  }), /코스 서버에 연결하지 못했어요/);
  assert.match(source, /AbortSignal\.timeout\(40000\)/);
  assert.doesNotMatch(source, /COURSE_BACKEND_URL|["'`]\/course-api/);
});

test("database sample metadata keeps legacy links and original display fields", async () => {
  const api = harness();
  const [sample] = await api.fetchCourses(async () => Response.json([apiCourse({
    sampleId: "fan-sajik-date", description: "원본 설명", cover: "/images/stadium-day.jpg",
    likes: 7, views: 9, isSample: true,
    stops: [
      { position: 0, name: "사직야구장", category: "경기 관람", lat: 35.194, lng: 129.059, isDrawnPoint: true },
      { position: 1, name: "산책 후보 지점", category: "직접 지정", lat: 35.197, lng: 129.057 },
      { position: 2, name: "마무리 지점", category: "직접 지정", lat: 35.199, lng: 129.059 },
    ],
  })]));
  assert.equal(sample.id, "fan-sajik-date");
  assert.equal(sample.description, "원본 설명");
  assert.equal(sample.cover, "/images/stadium-day.jpg");
  assert.equal(sample.isSample, true);
  assert.equal(sample.owned, false);
  assert.equal(sample.likes, 7);
  assert.equal(sample.views, 9);
  assert.equal(sample.stops.length, 3);
  assert.equal(sample.stops[0].isDrawnPoint, true);
  assert.equal(sample.apiId, apiCourse({}).id);
  assert.equal(sample.routeNumber, "000020");
});

test("member reactions use JWT fetch and anonymous views use an explicit token header", async () => {
  const api = harness();
  const id = apiCourse({}).id;
  const memberCalls = [];
  api.member(async (url, init) => { memberCalls.push([url, init]); return Response.json({ liked: true, likes: 8 }); });
  assert.deepEqual(await api.fetchCourseReaction(id), { liked: true, likes: 8 });
  assert.deepEqual(await api.setCourseReaction(id, true), { liked: true, likes: 8 });
  const view = await api.recordCourseView(id, "11111111-1111-4111-8111-111111111111", async (url, init) => {
    assert.equal(url, `/api/v1/courses/${id}/view/`);
    assert.equal(new Headers(init.headers).get("X-Course-View-Token"), "11111111-1111-4111-8111-111111111111");
    assert.equal(new Headers(init.headers).has("Cookie"), false);
    return Response.json({ views: 10 });
  });
  assert.deepEqual(view, { views: 10 });
  assert.deepEqual(memberCalls.map(([url, init]) => [url, init.method ?? "GET"]), [
    [`/api/v1/courses/${id}/reaction/`, "GET"], [`/api/v1/courses/${id}/reaction/`, "POST"],
  ]);
});
