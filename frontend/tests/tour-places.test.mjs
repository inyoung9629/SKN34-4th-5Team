import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, rmdirSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-tour-test-"));
const modules = ["nearby-places", "tour-places", "tour-api"];
for (const name of modules) {
  const { outputText } = ts.transpileModule(readFileSync(join(frontend, "lib", `${name}.ts`), "utf8"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } });
  writeFileSync(join(scratch, `${name}.js`), outputText);
}
after(() => { for (const name of modules) unlinkSync(join(scratch, `${name}.js`)); rmdirSync(scratch); });
const requireModule = createRequire(join(scratch, "entry.cjs"));
const { normalizeTourPlace, tourCategory, parseTourPage } = requireModule("./tour-places.js");
const { mergePlaces, sameStop } = requireModule("./nearby-places.js");
const { fetchTourPlaces } = requireModule("./tour-api.js");
const stadium = { code: "JAMSIL", name: "잠실야구장", lat: 37.5122, lng: 127.0719, address: "서울특별시 송파구 올림픽로 25" };
const raw = (overrides = {}) => ({ contentid: "1603175", contenttypeid: "12", title: "아시아공원", mapx: "127.0767", mapy: "37.51008", addr1: "서울특별시 송파구 올림픽로 44", ...overrides });
const page = (items = [], total = items.length) => ({ response: { header: { resultCode: "0000" }, body: { items: items.length ? { item: items } : "", totalCount: total } } });
const tourPlace = normalizeTourPlace(raw(), stadium);
const kakaoPlace = { ...tourPlace, placeId: "12345", tourContentId: undefined, name: "아시아 공원", lat: tourPlace.lat + .0001 };

test("TourAPI pins use distinct provider IDs and retain the tourism identity through serialization", () => {
  assert.equal(tourPlace.placeId, "tour:1603175");
  assert.equal(JSON.parse(JSON.stringify(tourPlace)).tourContentId, "1603175");
  assert.equal(tourPlace.kind, "walk");
  assert.ok(tourPlace.distance < 500);
  assert.equal(mergePlaces([tourPlace], [{ ...kakaoPlace, placeId: "1603175", name: "완전히 다른 명소" }]).length, 2);
});
test("TourAPI results outside the exact circle, inside the stadium or with invalid coordinates are excluded", () => {
  const latitude = (meters) => String(stadium.lat + meters / 6371000 * 180 / Math.PI);
  assert.ok(normalizeTourPlace(raw({ mapy: latitude(2499), mapx: String(stadium.lng) }), stadium));
  assert.equal(normalizeTourPlace(raw({ mapy: latitude(2501), mapx: String(stadium.lng) }), stadium), null);
  assert.equal(normalizeTourPlace(raw({ addr1: "서울 송파구 올림픽로 25 2층" }), stadium), null);
  assert.equal(normalizeTourPlace(raw({ mapy: String(stadium.lat), mapx: String(stadium.lng) }), stadium), null);
  for (const mapx of ["", "NaN", "Infinity", "999"]) assert.equal(normalizeTourPlace(raw({ mapx }), stadium), null);
  assert.equal(normalizeTourPlace(raw({ contentid: "not-an-id" }), stadium), null);
});
test("classify parks, indoor culture and tourism without treating outdoor pools or old events as walks", () => {
  for (const title of ["코엑스 아쿠아리움", "별마당도서관", "전시관", "백암아트홀"]) assert.equal(tourCategory(raw({ title })), "indoor");
  assert.equal(tourCategory(raw({ title: "선릉과 정릉" })), "sight");
  assert.equal(tourCategory(raw({ title: "한강공원 수영장(실외)" })), "sight");
  assert.equal(tourCategory(raw({ title: "숲길", contenttypeid: "28" })), "walk");
  assert.equal(tourCategory(raw({ title: "야외공연장", contenttypeid: "14" })), "sight");
  for (const item of [raw({ title: "2025 서울 카페&베이커리페어" }), raw({ title: "잠실야구장" }), raw({ contenttypeid: "15" }), raw({ contenttypeid: "28", title: "축구장" }), raw({ contenttypeid: "32" })]) assert.equal(tourCategory(item), null);
});
test("matching Kakao and tourism pins merge in either arrival order and keep one canonical ID", () => {
  for (const [first, second] of [[tourPlace, kakaoPlace], [kakaoPlace, tourPlace]]) {
    const merged = mergePlaces([first], [second]);
    assert.equal(merged.length, 1);
    assert.equal(merged[0].placeId, kakaoPlace.placeId);
    assert.equal(merged[0].tourContentId, tourPlace.tourContentId);
    assert.equal(sameStop(merged[0], tourPlace), true);
    assert.equal(sameStop(merged[0], kakaoPlace), true);
    const refreshed = mergePlaces(mergePlaces(merged, [kakaoPlace]), [tourPlace]);
    assert.equal(refreshed.length, 1);
    assert.equal(refreshed[0].tourContentId, tourPlace.tourContentId);
  }
});

test("shared building coordinates alone cannot merge distinct attractions or distant branches", () => {
  assert.equal(mergePlaces([tourPlace], [{ ...kakaoPlace, name: "아시아공원 기념비" }]).length, 2);
  assert.equal(mergePlaces([tourPlace], [{ ...kakaoPlace, lat: tourPlace.lat + .004 }]).length, 2);
  assert.equal(mergePlaces([kakaoPlace], [{ ...kakaoPlace, placeId: "22222" }]).length, 2);
  assert.equal(mergePlaces([tourPlace], [{ ...kakaoPlace, lat: tourPlace.lat + .001, address: "서울 송파구 올림픽로 44 1층" }]).length, 1);
});
test("parse singleton, empty and string-count pages; reject business errors even on HTTP 200", () => {
  assert.deepEqual(parseTourPage(page()), { items: [], total: 0 });
  assert.deepEqual(parseTourPage({ response: { header: { resultCode: "00" }, body: { items: { item: raw() }, totalCount: "1" } } }), { items: [raw()], total: 1 });
  assert.throws(() => parseTourPage({ response: { header: { resultCode: "30" } } }));
  assert.throws(() => parseTourPage(page([], 2)));
  assert.throws(() => parseTourPage({ response: { header: { resultCode: "0000" }, body: {} } }));
});
test("tourism client calls Django only and validates its response", async () => {
  const expected = JSON.parse(JSON.stringify({ status: "ok", places: [tourPlace], truncated: false }));
  const result = await fetchTourPlaces(stadium, async (url, options) => {
    const target = new URL(url, "http://example.test");
    assert.equal(target.pathname, "/api/v1/tourism/");
    assert.equal(target.searchParams.get("stadium"), stadium.code);
    assert.equal(target.searchParams.get("lat"), String(stadium.lat));
    assert.equal(target.searchParams.get("lng"), String(stadium.lng));
    assert.equal(options.redirect, "error");
    return Response.json(expected);
  });
  assert.deepEqual(result, expected);
  await assert.rejects(() => fetchTourPlaces(stadium, async () => Response.json({ status: "ok", places: "invalid", truncated: false })));
  await assert.rejects(() => fetchTourPlaces(stadium, async () => new Response("secret upstream URL", { status: 502 })));
});
