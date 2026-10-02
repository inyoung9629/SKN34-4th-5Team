import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, rmdirSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-live-nearby-"));
const modules = ["nearby-places", "nearby-search", "live-nearby-places", "kakao-lodging", "google-lodging", "collected-places", "stadium-boundaries", "stadium-locations", "stadium-boundary-frame", "stadium-search-scope", "stadium-complex-reviews", "stadium-complex-data"];
for (const name of modules) {
  const { outputText } = ts.transpileModule(readFileSync(join(root, "lib", `${name}.ts`), "utf8"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } });
  writeFileSync(join(scratch, `${name}.js`), outputText);
}
after(() => { for (const name of modules) unlinkSync(join(scratch, `${name}.js`)); rmdirSync(scratch); });
const requireModule = createRequire(join(scratch, "entry.cjs"));
const { collectLiveNearbyPlaces, outsideReviewedStadium } = requireModule("./live-nearby-places.js");
const { mergePlaces } = requireModule("./nearby-places.js");
const { selectedPlacePins } = requireModule("./collected-places.js");
const stadium = { code: "JAMSIL", name: "잠실야구장", lat: 37.5121854, lng: 127.0718513, address: "서울 송파구 올림픽로 25" };
const raw = { id: "123", place_name: "가상 식당", x: "127.080", y: "37.510", category_group_code: "FD6", category_name: "음식점 > 일식", road_address_name: "서울 송파구 테스트로 1", address_name: "서울 송파구" };

test("live search includes lodging once in the shared bounded queue without automatic pins", async () => {
  const requests = [];
  let active = 0, peak = 0, places = [];
  const result = await collectLiveNearbyPlaces({}, stadium, new AbortController().signal, () => "all", incoming => { places = mergePlaces(places, incoming); }, async (url, init) => {
    assert.equal(init.cache, "no-store");
    const body = JSON.parse(init.body); requests.push(body);
    assert.equal(url, body.category === "AD5" ? "/api/v1/places/lodging/" : "/api/v1/places/live/");
    active++; peak = Math.max(peak, active);
    await new Promise(resolve => setTimeout(resolve, 1)); active--;
    const document = body.category === "AD5" ? { ...raw, id: "456", place_name: "가상 숙소", category_group_code: "AD5", category_name: "숙박 > 호텔" } : raw;
    return { ok: true, json: async () => ({ places: [document], hasNextPage: true }) };
  });
  assert.equal(requests.length, 54);
  assert.equal(peak, 2);
  assert.ok(requests.every(q => q.page <= 3 && q.size === 15 && q.radius === 2500 && q.lat === stadium.lat));
  assert.deepEqual(requests.filter(q => q.category === "AD5").map(q => q.page), [1, 2, 3]);
  assert.deepEqual(result, { completed: 18, failures: 0 });
  assert.equal(places.length, 2);
  assert.equal(places[0].source, "KAKAO");
  assert.deepEqual(selectedPlacePins(places, null, []), []);
  assert.deepEqual(selectedPlacePins(places, places[0], []), [places[0]]);
  assert.deepEqual(selectedPlacePins(places, places[0], places), []);
  const lodging = places.find(place => place.kind === "stay");
  assert.equal(lodging.placeId, "kakao-lodging:all:456");
  assert.equal(lodging.subcategory, "호텔");
  assert.deepEqual(selectedPlacePins(places, lodging, []), [lodging]);
  const { visiblePlaces } = requireModule("./nearby-places.js");
  assert.deepEqual(visiblePlaces(places, ["stay"]), [lodging]);
});

test("partial failures preserve results and never fall back to public catalogue", async () => {
  let places = [], calls = 0;
  const result = await collectLiveNearbyPlaces({}, stadium, new AbortController().signal, () => "food", incoming => { places = mergePlaces(places, incoming); }, async (url, init) => {
    assert.equal(url, JSON.parse(init.body).category === "AD5" ? "/api/v1/places/lodging/" : "/api/v1/places/live/");
    if (++calls === 1) throw new Error("offline");
    return { ok: true, json: async () => ({ places: [raw], hasNextPage: false }) };
  });
  assert.equal(result.failures, 1);
  assert.equal(places.length, 1);
});

test("lodging failure preserves other categories and reports partial failure", async () => {
  let places = [];
  const result = await collectLiveNearbyPlaces({}, stadium, new AbortController().signal, () => "stay", incoming => { places = mergePlaces(places, incoming); }, async url => {
    if (url.endsWith("/lodging/")) throw new Error("lodging unavailable");
    return { ok: true, json: async () => ({ places: [raw], hasNextPage: false }) };
  });
  assert.equal(result.failures, 1);
  assert.deepEqual(places.map(place => place.kind), ["food"]);
});

test("course editor uses common category counts, subtype controls and list for lodging", () => {
  const source = readFileSync(join(root, "components/nearby-route-planner.tsx"), "utf8");
  assert.doesNotMatch(source, /KakaoLodgingPanel|showLodging|lodgingSnapshot/);
  assert.match(source, /category.label\}<span>\{categoryPlaces.length\}/);
  assert.match(source, /options.map\(\(label\)/);
  assert.match(source, /selectedPlacePins\(pinPlaces, selected, stops\)/);
});

test("stadium change aborts in-flight results and stops queued work", async () => {
  const controller = new AbortController(); let updates = 0, calls = 0;
  await assert.rejects(collectLiveNearbyPlaces({}, stadium, controller.signal, () => "all", () => updates++, async () => {
    calls++; controller.abort();
    return { ok: true, json: async () => ({ places: [raw], hasNextPage: true }) };
  }), { name: "AbortError" });
  assert.equal(updates, 0);
  assert.ok(calls <= 2);
});

test("reviewed building footprint separates unnamed internal tenants, not the whole complex", () => {
  assert.equal(outsideReviewedStadium({ lat: 37.5128, lng: 127.0713 }, "JAMSIL"), false);
  assert.equal(outsideReviewedStadium({ lat: 37.510, lng: 127.080 }, "JAMSIL"), true);
  assert.equal(outsideReviewedStadium({ lat: 37.4999632, lng: 126.87119005 }, "GOCHEOK"), true);
});

test("red-only complex places never reach external search results", async () => {
  const { stadiumLocationAudit } = requireModule("./stadium-locations.js");
  const main = stadiumLocationAudit.stadiums.CHANGWON;
  const old = main.excluded[0];
  const documents = [main, old, {lat:35.219,lng:128.585}].map((p,index)=>({...raw,id:String(index+1),x:String(p.lng),y:String(p.lat)}));
  let found=[];
  await collectLiveNearbyPlaces({}, {...stadium,...main,code:"CHANGWON"}, new AbortController().signal,()=>"food",p=>{found=mergePlaces(found,p);},async()=>({ok:true,json:async()=>({places:documents,hasNextPage:false})}));
  assert.deepEqual(found.map(p=>p.placeId),["3"]);
});
