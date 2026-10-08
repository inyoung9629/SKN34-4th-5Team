import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

function loadTypeScript(relativePath, globals = {}) {
  const source = readFileSync(new URL(relativePath, import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const exports = {};
  vm.runInNewContext(code, { exports, module: { exports }, require: globals.require ?? (() => ({})), URLSearchParams, ...globals });
  return exports;
}

const presentation = [
  ["JAMSIL", "서울특별시 송파구 올림픽로 25", "서울"],
  ["GOCHEOK", "서울특별시 구로구 경인로 430", "서울"],
  ["MUNHAK", "인천광역시 미추홀구 매소홀로 618", "인천·경기"],
  ["SUWON", "경기도 수원시 장안구 경수대로 893", "인천·경기"],
  ["DAEJEON", "대전광역시 중구 대종로 373", "대전·광주"],
  ["DAEGU", "대구광역시 수성구 야구전설로 1", "대구·부산·창원"],
  ["GWANGJU", "전남광주통합특별시 북구 서림로 10", "대전·광주"],
  ["SAJIK", "부산광역시 동래구 사직로 45", "대구·부산·창원"],
  ["CHANGWON", "경상남도 창원시 마산회원구 삼호로 63", "대구·부산·창원"],
];
const visuals = presentation.map(([code, , region]) => ({ code, region, color: "blue", seatingMap: { src: "/seat.png", sourceUrl: "https://example.test/seat" }, cardImage: { src: "/photo.jpg", sourceUrl: "https://example.test/photo", credit: "credit", creditUrl: "https://example.test/credit" } }));
const { adaptStadium } = loadTypeScript("../lib/baseball/adapters.ts", { require: name => name.includes("media-url") ? loadTypeScript("../lib/media-url.ts", { URL }) : ({ stadiums: visuals }) });
const row = (stadium_code, address, longitude = "127", latitude = "37") => ({ stadium_code, stadium_name_ko: stadium_code, address, longitude, latitude, home_teams: [] });

test("all nine source addresses use code presentation regions, including Gwangju and Changwon", () => {
  for (const [code, address, region] of presentation) assert.equal(adaptStadium(row(code, address)).region, region);
  assert.equal(adaptStadium(row("NEW", "서울특별시 어딘가")).region, "미분류");
});

test("invalid or out-of-range coordinates never reach maps and route planning", () => {
  assert.equal(adaptStadium(row("NEW", "주소", "NaN", "37")), null);
  assert.equal(adaptStadium(row("NEW", "주소", "181", "37")), null);
  assert.equal(adaptStadium(row("NEW", "주소", "127", "-91")), null);
});

test("stadium loading follows every API page instead of silently stopping at 100", async () => {
  const calls = [];
  const fetch = async url => {
    calls.push(url);
    const page = Number(new URL(url, "https://app.test").searchParams.get("page"));
    const size = page < 3 ? 100 : 5;
    return { ok: true, json: async () => ({ count: 205, next: page < 3 ? "next" : null, previous: null, results: Array.from({ length: size }, (_, index) => ({ id: (page - 1) * 100 + index })) }) };
  };
  const { fetchBaseballStadiums } = loadTypeScript("../lib/baseball/client.ts", { fetch });
  const result = await fetchBaseballStadiums();
  assert.equal(result.results.length, 205);
  assert.deepEqual(calls.map(url => new URL(url, "https://app.test").searchParams.get("page")), ["1", "2", "3"]);
});

test("an incomplete paginated stadium response fails instead of presenting a partial list", async () => {
  const { fetchBaseballStadiums } = loadTypeScript("../lib/baseball/client.ts", { fetch: async url => ({ ok: true, json: async () => ({ count: 101, next: null, previous: null, results: new URL(url, "https://app.test").searchParams.get("page") === "1" ? Array(100).fill({}) : [] }) }) });
  await assert.rejects(fetchBaseballStadiums(), /끝까지 불러오지 못했어요/);
});

test("section requests keep selected home context and use visible pagination", async () => {
  const calls = [];
  const { fetchStadiumSection, fetchTicketPolicies } = loadTypeScript("../lib/baseball/client.ts", { fetch: async url => { calls.push(url); return { ok: true, json: async () => ({ count: 0, next: null, previous: null, results: [] }) }; } });
  await fetchStadiumSection("JAMSIL", "ticket-prices", 2, 77);
  await fetchTicketPolicies(88, 3);
  const url = new URL(calls[0], "https://app.test");
  assert.equal(url.searchParams.get("page"), "2");
  assert.equal(url.searchParams.get("page_size"), "30");
  assert.equal(url.searchParams.get("home_context"), "77");
  const policies = new URL(calls[1], "https://app.test");
  assert.equal(policies.pathname, "/api/v1/baseball/ticket-policies/");
  assert.equal(policies.searchParams.get("team"), "88");
  assert.equal(policies.searchParams.get("page"), "3");
});

test("stadium detail keeps the collection date but no longer lists collected ticket rows", () => {
  const detail = readFileSync(new URL("../app/stadiums/[code]/page.tsx", import.meta.url), "utf8");
  // 좌석·가격·예매 정책 목록이 있던 "수집된 구장 안내"는 화면에서 제거했다
  assert.doesNotMatch(detail, /수집된 구장 안내|예매 정책 스냅샷|seat_zone_name/);
  assert.match(detail, /collected_at\.slice/);
});

 test("stadium images and credits come only from DB, with unsafe or absent photos omitted", () => {
  const base = row("JAMSIL", "주소");
  assert.equal(adaptStadium(base).cardImage, undefined);
  const data = { ...base, image_url: "/images/stadiums/exteriors/jamsil.jpg", image_source_url: "https://commons.wikimedia.org/photo", image_credit: "DB credit", image_credit_url: "https://creativecommons.org/licenses/by/4.0/", image_license_url: "https://creativecommons.org/licenses/by/4.0/" };
  assert.equal(adaptStadium(data).cardImage.src, data.image_url);
  assert.equal(adaptStadium(data).cardImage.credit, "DB credit");
  assert.equal(adaptStadium(data).cardImage.creditUrl, data.image_credit_url);
  assert.equal(adaptStadium({ ...data, image_url: "javascript:alert(1)" }).cardImage, undefined);
  assert.equal(adaptStadium({ ...data, image_credit_url: "javascript:alert(1)" }).cardImage.creditUrl, "");
});

 test("guide adapters use only safe DB metadata and omit missing guides", () => {
  const base = row("JAMSIL", "주소");
  assert.equal(adaptStadium(base).seatingMap, undefined);
  assert.equal(adaptStadium(base).parkingMap, undefined);
  const parkingMap = { imageUrl: "/images/stadiums/parking-maps/jamsil-parking.png", sourceUrl: "https://myseatcheck.com/guide", credit: "DB credit", title: "DB title", summary: "DB summary", kind: "preferred-area", visualNotes: ["DB note"], capturedAt: "2026-09-10", width: 839, height: 857 };
  const seatingMap = { imageUrl: "/images/stadiums/seating-maps/jamsil.png", sourceUrl: "https://www.lgtwins.com/ticket/general", season: 2026, team_code: "LG" };
  const adapted = adaptStadium({ ...base, parkingMap, seatingMap });
  assert.equal(adapted.parkingMap.src, parkingMap.imageUrl);
  assert.equal(adapted.parkingMap.credit, "DB credit");
  assert.equal(adapted.parkingMap.capturedAt, parkingMap.capturedAt);
  assert.equal(adapted.seatingMap.src, seatingMap.imageUrl);
  assert.equal(adapted.seatingMap.season, 2026);
  assert.equal(adapted.seatingMap.teamCode, "LG");
  const detail = readFileSync(new URL("../app/stadiums/[code]/page.tsx", import.meta.url), "utf8");
  assert.match(detail, /stadium\.seatingMap\.season/);
  assert.match(detail, /stadium\.seatingMap\.teamCode/);
  assert.equal(adaptStadium({ ...base, parkingMap: { ...parkingMap, imageUrl: "javascript:alert(1)" } }).parkingMap, undefined);
  assert.equal(adaptStadium({ ...base, seatingMap: { ...seatingMap, imageUrl: "https://evil.test/map.png" } }).seatingMap, undefined);
  assert.equal(adaptStadium({ ...base, parkingMap: { ...parkingMap, sourceUrl: "javascript:alert(1)" } }).parkingMap.sourcePageUrl, "");
});
