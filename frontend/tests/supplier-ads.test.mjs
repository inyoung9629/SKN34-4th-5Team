import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";
const api = {};
new Function("exports", ts.transpileModule(readFileSync(new URL("../lib/supplier-ads.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText)(api);
const { supplierAds, uniqueSupplierAds, nextAdIndex, randomAdIndex } = api;

test("all ten clubs are covered by unique supplier-brand slides", () => {
  assert.equal(supplierAds.length, 10);
  assert.equal(new Set(supplierAds.map(ad => ad.id)).size, 10);
  assert.deepEqual(supplierAds.flatMap(ad => ad.teamCodes).sort(), ["LG","HH","SK","SS","NC","KT","LT","HT","OB","WO"].sort());
  supplierAds.forEach(ad => {
    assert.equal(new URL(ad.href).protocol, "https:");
    assert.equal(new URL(ad.sourceImage).protocol, "https:");
  });
});
test("same supplier appears once, merges teams, and does not mutate the input", () => {
  const original = supplierAds[0];
  const result = uniqueSupplierAds([original, {...original, id: " PROSPECS ", teams:["추가 팀"], teamCodes:["EXTRA"]}, original]);
  assert.equal(result.length, 1);
  assert.deepEqual(result[0].teamCodes, ["LG","EXTRA"]);
  assert.deepEqual(original.teamCodes, ["LG"]);
});
test("variable-length supplier carousel wraps and supports random starts", () => {
  for (const count of [1,3,10]) {
    assert.equal(nextAdIndex(count - 1, count), 0);
    assert.equal(nextAdIndex(0, count, -1), count - 1);
    assert.equal(randomAdIndex(count, () => 0), 0);
    assert.equal(randomAdIndex(count, () => .99999), count - 1);
  }
});
test("each banner contains the exact downloaded photo, with no remote image requests", () => {
  for (const ad of supplierAds) {
    const photo = readFileSync(new URL(`../public/images/ads/suppliers/source/${ad.id}.img`, import.meta.url));
    assert.ok(photo.length > 1000);
    for (const suffix of ["", "-mobile"]) {
      const svg = readFileSync(new URL(`../public/images/ads/suppliers/${ad.id}${suffix}.svg`, import.meta.url), "utf8");
      assert.ok(svg.includes(photo.toString("base64")));
      assert.ok(svg.includes(ad.name));
      assert.ok(svg.includes("실제 제휴 광고 아님"));
      assert.ok(svg.includes('preserveAspectRatio="xMidYMid slice"'));
      assert.ok(svg.includes('fill="url(#text-shade)"'));
      assert.ok(svg.indexOf("<image ") < svg.indexOf("<text "));
      assert.ok(!svg.includes('clip-path="url(#photo)"'));
      assert.doesNotMatch(svg, /<script|<foreignObject|(?:href|src)="https?:/i);
    }
  }
});

