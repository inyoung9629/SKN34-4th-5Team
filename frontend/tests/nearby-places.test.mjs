import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, rmdirSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-nearby-test-"));
for (const name of ["nearby-places", "nearby-search"]) {
  const { outputText } = ts.transpileModule(readFileSync(join(frontend, "lib", `${name}.ts`), "utf8"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } });
  writeFileSync(join(scratch, `${name}.js`), outputText);
}
after(() => { for (const name of ["nearby-places", "nearby-search"]) unlinkSync(join(scratch, `${name}.js`)); rmdirSync(scratch); });
const requireModule = createRequire(join(scratch, "entry.cjs"));
const { normalizePlace, classifyPlace, distanceMeters, isStadiumFacility, mergePlaces, visiblePlaces, sameStop, moveStop, clusterPlaces } = requireModule("./nearby-places.js");
const { searchPage, resolveStadium, collectNearbyPlaces } = requireModule("./nearby-search.js");
const stadium = { code: "TEST", name: "잠실야구장", lat: 37.512, lng: 127.072, address: "서울 송파구 올림픽로 25" };
const raw = (overrides = {}) => ({ id: "101", place_name: "동네 중국집", category_name: "음식점 > 중식 > 중국요리", category_group_code: "FD6", category_group_name: "음식점", x: "127.080", y: "37.510", road_address_name: "서울 송파구 백제고분로 100", address_name: "서울 송파구 잠실동", ...overrides });

test("multiple categories exclude unchecked kinds and keep cuisine filtering local to food", () => {
  const places = [
    { placeId: "food1", kind: "food", cuisine: "한식" },
    { placeId: "food2", kind: "food", cuisine: "중식" },
    { placeId: "cafe", kind: "cafe", cuisine: "기타" },
    { placeId: "stay", kind: "stay", cuisine: "기타" },
  ];
  assert.deepEqual(visiblePlaces(places, []), []);
  assert.deepEqual(visiblePlaces(places, ["food", "cafe"], "한식").map((p) => p.placeId), ["food1", "cafe"]);
  assert.deepEqual(visiblePlaces(places, ["stay"]).map((p) => p.placeId), ["stay"]);
});

test("radius uses coordinates, accepts just inside 2.5km and rejects just outside", () => {
  const latitude = (distance) => String(stadium.lat + distance / 6371000 * 180 / Math.PI);
  assert.ok(normalizePlace(raw({ y: latitude(2499), x: String(stadium.lng) }), stadium));
  assert.equal(normalizePlace(raw({ y: latitude(2501), x: String(stadium.lng) }), stadium), null);
  for (const x of ["", "NaN", "999", "Infinity"]) assert.equal(normalizePlace(raw({ x }), stadium), null);
});
test("exclude stadium tenants by address regardless of geographic label placement", () => {
  assert.equal(isStadiumFacility(raw({ road_address_name: "서울특별시 송파구 올림픽로 25 3층", place_name: "익명 매점" }), stadium), true);
  assert.equal(isStadiumFacility(raw({ road_address_name: "서울 송파구 올림픽로 250" }), stadium), false);
  assert.equal(isStadiumFacility(raw({ road_address_name: "", place_name: "BBQ 잠실야구장3루점" }), stadium), true);
  assert.equal(isStadiumFacility(raw({ place_name: "카페 잠실야구장앞점" }), stadium), false);
});
test("food subcategories and activity classifications do not treat every cafe as indoor or every shop as convenience", () => {
  assert.equal(normalizePlace(raw(), stadium).cuisine, "중식");
  assert.equal(classifyPlace(raw({ category_name: "음식점 > 카페 > 보드카페", place_name: "레드버튼 보드게임카페", category_group_code: "CE7" })), "indoor");
  assert.equal(classifyPlace(raw({ category_name: "음식점 > 카페", category_group_code: "CE7" })), "cafe");
  assert.equal(classifyPlace(raw({ category_name: "여행 > 관광,명소 > 도보여행 > 둘레길", category_group_code: "AT4" })), "walk");
  assert.equal(classifyPlace(raw({ category_name: "여행 > 공원", category_group_code: "" })), "walk");
  assert.equal(classifyPlace(raw({ category_group_code: "CS2" })), "store");
  assert.equal(classifyPlace(raw({ category_name: "가정,생활 > 슈퍼마켓", category_group_code: "MT1" })), null);
});
test("tourism retains exactly the selected category families and their descendants", () => {
  for (const category of ["도보여행 > 둘레길 > 인천둘레길", "동물원 > 실내동물원", "문화유적 > 고궁,궁", "수목원", "식물원", "수목원,식물원", "저수지", "전망대", "천문대", "테마거리 > 먹자골목", "테마거리 > 카페거리", "테마파크 > 워터테마파크", "호수"]) {
    const place = normalizePlace(raw({ place_name: "조회된 장소", category_name: `여행 > 관광,명소 > ${category}`, category_group_code: "AT4" }), stadium);
    assert.ok(place, category);
    assert.equal(place.subcategory, category.split(" > ")[0]);
  }
  const sport = normalizePlace(raw({ place_name: "눈썰매장", category_name: "스포츠,레저 > 스포츠시설 > 눈썰매장", category_group_code: "AT4" }), stadium);
  assert.equal(sport.subcategory, "스포츠,레저");
});
test("unselected tourism families cannot pass through keyword searches or name-based indoor classification", () => {
  for (const leaf of ["계곡", "산", "숲", "온천", "자연휴양림", "자전거여행", "도자기,도예촌", "공원"]) {
    for (const category_group_code of ["AT4", ""]) assert.equal(classifyPlace(raw({ place_name: "박물관 주변", category_name: `여행 > 관광,명소 > ${leaf}`, category_group_code })), null, leaf);
  }
  assert.equal(classifyPlace(raw({ category_name: "", category_group_code: "AT4" })), null);
});
test("category selection does not add the previously reverted name exclusions or turn cafe streets into cafes", () => {
  for (const [place_name, category_name, expected] of [
    ["갈맷길 시작인증대", "여행 > 관광,명소 > 도보여행 > 갈맷길", "도보여행"],
    ["자마장시장 골목형상점가", "여행 > 관광,명소 > 테마거리", "테마거리"],
    ["수향길", "여행 > 관광,명소 > 테마거리 > 카페거리", "테마거리"],
    ["인천둘레길 8코스", "여행 > 관광,명소 > 도보여행 > 둘레길 > 인천둘레길", "도보여행"],
  ]) {
    const place = normalizePlace(raw({ place_name, category_name, category_group_code: "AT4" }), stadium);
    assert.equal(place.subcategory, expected);
    assert.notEqual(place.kind, "cafe");
  }
  assert.equal(normalizePlace(raw(), stadium).subcategory, undefined);
});
test("all and multiple categories display every fetched match without per-category caps", () => {
  const food = normalizePlace(raw(), stadium);
  const items = [...Array.from({ length: 100 }, (_, i) => ({ ...food, placeId: String(i) })), ...Array.from({ length: 20 }, (_, i) => ({ ...food, kind: "stay", placeId: `hotel${i}` })), { ...food, kind: "walk", placeId: "park" }];
  const visible = visiblePlaces(items, "all");
  assert.equal(visible.filter((p) => p.kind === "food").length, 100);
  assert.equal(visible.filter((p) => p.kind === "stay").length, 20);
  assert.ok(visible.some((p) => p.placeId === "park"));

  assert.equal(visiblePlaces(items, ["food", "stay", "walk"]).length, items.length);
  assert.equal(visiblePlaces(items, "stay").length, 20);
  assert.equal(visiblePlaces(items, "food", "일식").length, 0);
});

test("deduplicate by Kakao ID, reorder without mutation, and cluster dense pins", () => {
  const place = normalizePlace(raw(), stadium);
  const another = { ...place, placeId: "102", lat: place.lat + .001 };
  assert.equal(mergePlaces([place], [{ ...place, name: "수정된 상호" }, another]).length, 2);
  assert.equal(sameStop(place, { ...place, name: "다른 표기" }), true);
  const stops = [place, another];
  assert.deepEqual(moveStop(stops, 0, 1), [another, place]);
  assert.equal(stops[0], place);
  assert.equal(moveStop(stops, 0, -1), stops);
  assert.equal(clusterPlaces(stops, () => ({ x: 12, y: 12 }), 50).length, 1);
  assert.equal(clusterPlaces(stops, () => ({ x: 12, y: 12 }), 0).length, 2);
  assert.ok(distanceMeters(place, another) > 100);
});

function fakeMaps(handler) {
  return {
    LatLng: class { constructor(lat, lng) { this.lat = lat; this.lng = lng; } },
    services: { Status: { OK: "OK", ZERO_RESULT: "ZERO_RESULT" }, SortBy: { DISTANCE: "distance", ACCURACY: "accuracy" }, Places: class {
      keywordSearch(query, callback, options) { handler(query, callback, options, "keyword"); }
      categorySearch(query, callback, options) { handler(query, callback, options, "category"); }
    } },
  };
}
test("place search posts directly to the Django API without a browser or Next relay", async () => {
  const calls = [];
  const maps = fakeMaps(() => { throw new Error("browser Places SDK must not run"); });
  const result = await searchPage(maps, stadium, { kind: "food", method: "category", query: "FD6" }, 1, new AbortController().signal, async (url, options) => {
    calls.push({ url, options, body: JSON.parse(options.body) });
    return Response.json({ places: [raw()], hasNextPage: false });
  });
  assert.equal(result.places.length, 1);
  assert.deepEqual(calls.map(({ url, options, body }) => ({ url, httpMethod: options.method, body })), [
    { url: "/api/v1/places/search/", httpMethod: "POST", body: { method: "category", category: "FD6", lat: stadium.lat, lng: stadium.lng, radius: 2500, size: 15, page: 1, sort: "distance" } },
  ]);
});
test("place searches are not cached in the browser", async () => {
  let calls = 0;
  const fetcher = async () => { calls++; return Response.json({ places: [raw()], hasNextPage: false, syncedAt: "2026-09-15T00:00:00Z" }); };
  const query = { kind: "food", method: "category", query: "FD6" };
  await searchPage(fakeMaps(() => {}), stadium, query, 1, new AbortController().signal, fetcher);
  const result = await searchPage(fakeMaps(() => {}), stadium, query, 1, new AbortController().signal, fetcher);
  assert.equal(calls, 2);
  assert.equal(result.syncedAt, "2026-09-15T00:00:00Z");
});
test("search options always use the stadium, radius, distance ordering and bounded pages", async () => {
  const calls = [];
  const maps = fakeMaps(() => { throw new Error("browser Places SDK must not run"); });
  const fetcher = async (_, options) => { const body = JSON.parse(options.body); calls.push(body); return Response.json({ places: [raw()], hasNextPage: true }); };
  let finalPlaces = [];
  const result = await collectNearbyPlaces(maps, { ...stadium, code: "pagination" }, new AbortController().signal, () => "all", (items) => { finalPlaces = mergePlaces(finalPlaces, items); }, fetcher);
  assert.equal(result.failures, 0);
  assert.equal(finalPlaces.length, 1);
  assert.ok(calls.every((call) => call.radius === 2500 && call.size === 15 && call.page <= 3 && call.sort === "distance" && call.lat === stadium.lat));
  assert.ok(calls.findIndex((c) => c.category === "AD5") > calls.findLastIndex((c) => c.category === "FD6"));
});
test("explicit lodging filter moves lodging to front of the queue", async () => {
  const calls = [];
  const maps = fakeMaps(() => { throw new Error("browser Places SDK must not run"); });
  await collectNearbyPlaces(maps, { ...stadium, code: "preferred" }, new AbortController().signal, () => "stay", () => {}, async (_, options) => { const body = JSON.parse(options.body); calls.push(body.category ?? body.keyword); return Response.json({ places: [], hasNextPage: false }); });
  assert.equal(calls[0], "AD5");
});
test("partial API errors retain successful results and are reported", async () => {
  let received = 0;
  const maps = fakeMaps(() => { throw new Error("browser Places SDK must not run"); });
  const result = await collectNearbyPlaces(maps, { ...stadium, code: "partial" }, new AbortController().signal, () => "all", (items) => { received += items.length; }, async (_, options) => {
    const body = JSON.parse(options.body), failed = body.method === "category" && body.category === "FD6";
    return Response.json(failed ? { error: "failed" } : { places: [raw()], hasNextPage: false }, { status: failed ? 502 : 200 });
  });
  assert.equal(result.failures, 1);
  assert.ok(received > 0);
});
test("aborting an in-flight request discards its eventual response", async () => {
  let finish;
  const maps = fakeMaps(() => { throw new Error("browser Places SDK must not run"); });
  const controller = new AbortController();
  const request = searchPage(maps, { ...stadium, code: "aborted" }, { kind: "food", method: "category", query: "FD6" }, 1, controller.signal, (_, options) => new Promise((resolve, reject) => {
    finish = resolve;
    options.signal.addEventListener("abort", () => reject(new DOMException("cancelled", "AbortError")), { once: true });
  }));
  controller.abort();
  await assert.rejects(request, { name: "AbortError" });
  finish(Response.json({ places: [raw()], hasNextPage: false }));
});
test("stadium resolution uses the actual baseball venue instead of a tenant or complex address", async () => {
  const maps = fakeMaps(() => { throw new Error("browser Places SDK must not run"); });
  const result = await resolveStadium(maps, { ...stadium, code: "resolve" }, new AbortController().signal, async (_, options) => { assert.equal(JSON.parse(options.body).sort, "accuracy"); return Response.json({ places: [raw({ place_name: "BBQ 잠실야구장점" }), raw({ place_name: "잠실종합운동장 잠실야구장", category_name: "스포츠 > 스포츠시설 > 야구장", x: "127.071", y: "37.513" })], hasNextPage: false }); });
  assert.equal(result.lat, 37.513);
  assert.equal(result.lng, 127.071);
});
test("stadium resolution accepts the provider's Korean KIA spelling", async () => {
  const maps = fakeMaps(() => { throw new Error("browser Places SDK must not run"); });
  const result = await resolveStadium(maps, { ...stadium, code: "GWANGJU", name: "광주-KIA 챔피언스 필드" }, new AbortController().signal, async () => Response.json({ places: [raw({ place_name: "광주기아챔피언스필드", category_name: "스포츠,레저 > 야구 > 야구장" })], hasNextPage: false }));
  assert.equal(result.code, "GWANGJU");
});

test("hospital and pharmacy search results are excluded even if names match an activity", () => {
  for (const category_group_code of ["HP8", "PM9"]) assert.equal(normalizePlace(raw({ category_group_code, place_name: "박물관 옆 의료시설" }), stadium), null);
});
