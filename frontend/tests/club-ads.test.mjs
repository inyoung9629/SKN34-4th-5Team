import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../lib/club-ads.ts", import.meta.url), "utf8");
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const exports = {};
new Function("exports", output)(exports);
const { clubAds, clubInstagramUrl, randomClubIndex, nextClubIndex, startClubRotation } = exports;

test("all ten teams have distinct local artwork and exact official Instagram destinations", () => {
  assert.equal(clubAds.length, 10);
  assert.equal(new Set(clubAds.map(club => club.code)).size, 10);
  const handles = ["lgtwinsbaseballclub", "hanwhaeagles_soori", "ssglanders.incheon", "samsunglions_baseballclub", "ncdinos2011", "ktwiz.pr", "busanlottegiants", "always_kia_tigers", "doosanbears.1982", "heroesbaseballclub"];
  clubAds.forEach((club, index) => {
    assert.equal(clubInstagramUrl(index), `https://www.instagram.com/${handles[index]}/`);
    for (const suffix of ["", "-mobile"]) {
      const svg = readFileSync(new URL(`../public/images/ads/clubs/${club.code.toLowerCase()}${suffix}.svg`, import.meta.url), "utf8");
      assert.ok(svg.includes(club.name));
      assert.ok(svg.includes("<path"));
      assert.doesNotMatch(svg, /<script|<foreignObject|(?:href|src)="https?:/i);
    }
  });
});

test("random starting selection covers all clubs without persisting state", () => {
  assert.equal(randomClubIndex(() => 0), 0);
  assert.equal(randomClubIndex(() => .999999), 9);
  for (let i = 0; i < 10; i++) assert.equal(randomClubIndex(() => (i + .5) / 10), i);
});

test("sequential navigation visits all ten teams and wraps in both directions", () => {
  const seen = new Set();
  let index = 6;
  for (let step = 0; step < 10; step++) { seen.add(index); index = nextClubIndex(index); }
  assert.equal(seen.size, 10);
  assert.equal(index, 6);
  assert.equal(nextClubIndex(0, -1), 9);
});

test("rotation advances at five seconds and cleanup cancels future ticks", context => {
  context.mock.timers.enable({ apis: ["setInterval"] });
  let count = 0;
  const stop = startClubRotation(() => count++);
  context.mock.timers.tick(4999);
  assert.equal(count, 0);
  context.mock.timers.tick(1);
  assert.equal(count, 1);
  context.mock.timers.tick(5000);
  assert.equal(count, 2);
  stop();
  context.mock.timers.tick(10000);
  assert.equal(count, 2);
});
