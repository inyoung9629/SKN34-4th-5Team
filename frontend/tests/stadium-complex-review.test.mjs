import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

const scratch = mkdtempSync(join(tmpdir(), "kbo-complex-review-test-"));
after(() => rmSync(scratch, { recursive: true }));
for (const name of ["daejeon-complex-review", "stadium-complex-reviews", "stadium-complex-data", "stadium-search-scope", "stadium-locations", "stadium-boundaries", "stadium-boundary-frame"]) {
  const source = readFileSync(new URL(`../lib/${name}.ts`, import.meta.url), "utf8");
  writeFileSync(join(scratch, `${name}.js`), ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText);
}
const require = createRequire(join(scratch, "entry.cjs"));
const { daejeonComplexReview: review, complexReviewRings } = require("./daejeon-complex-review.js");
const { stadiumLocationAudit: audit } = require("./stadium-locations.js");
const { stadiumBoundaries: boundaries } = require("./stadium-boundaries.js");
const { stadiumBoundaryFrame } = require("./stadium-boundary-frame.js");
const main = stadiumBoundaryFrame(audit.stadiums.DAEJEON, boundaries.stadiums.DAEJEON.rings, audit.boundaryPaddingM).path;
const { stadiumComplexReviews: reviews, stadiumComplexReviewRings } = require("./stadium-complex-reviews.js");
const { classifyStadiumPoint, containsStadiumPoint } = require("./stadium-search-scope.js");
const contains = (path, [lat, lng]) => {
  let inside = false;
  for (let i = 0, j = path.length - 1; i < path.length; j = i++) {
    const [ay, ax] = path[i], [by, bx] = path[j];
    if ((ay > lat) !== (by > lat) && lng < (bx - ax) * (lat - ay) / (by - ay) + ax) inside = !inside;
  }
  return inside;
};

test("Daejeon outline is closed and remains a review-only draft", () => {
  assert.equal(review.applyToSearch, false);
  assert.equal(review.status, "draft_visual_review");
  assert.deepEqual(review.outer[0], review.outer.at(-1));
  assert.ok(review.outer.length > 10, "trace follows the complex rather than a broad four-corner box");
});

test("only Daejeon has a complex overlay; main stadium frame is reused unchanged", () => {
  const copy = structuredClone(main);
  const rings = complexReviewRings("DAEJEON", main);
  assert.equal(rings.hole, main);
  assert.deepEqual(main, copy);
  for (const code of Object.keys(audit.stadiums).filter(code => code !== "DAEJEON")) {
    assert.equal(complexReviewRings(code, main), null);
  }
});

test("old ballpark is in the complex but outside the main stadium; outside blocks stay out", () => {
  const old = audit.stadiums.DAEJEON.excluded[0];
  assert.ok(contains(review.outer, [old.lat, old.lng]));
  assert.ok(!contains(main, [old.lat, old.lng]));
  assert.ok(!contains(review.outer, [36.3195, 127.4300]), "north-side shops across the road");
  assert.ok(!contains(review.outer, [36.3145, 127.4325]), "school south of the stadium road");
  assert.ok(contains(review.outer, [audit.stadiums.DAEJEON.lat, audit.stadiums.DAEJEON.lng]));
});

test("all nine complex traces are closed, local, finite, and active for search", () => {
  assert.deepEqual(Object.keys(reviews).sort(), Object.keys(audit.stadiums).sort());
  assert.equal(stadiumComplexReviewRings("UNKNOWN", main), null);
  assert.equal(stadiumComplexReviewRings("constructor", main), null);
  for (const [code, review] of Object.entries(reviews)) {
    const p = audit.stadiums[code];
    assert.equal(review.applyToSearch, true);
    assert.equal(review.status, "active_manual_trace");
    for (const ring of [review.outer, ...(review.additionalOuters ?? [])]) {
      assert.deepEqual(ring[0], ring.at(-1), `${code}: closed ring`);
      assert.ok(ring.length >= 5);
      for (const [lat,lng] of ring) {
        assert.ok(Number.isFinite(lat) && Number.isFinite(lng));
        assert.ok(Math.abs(lat-p.lat) < .02 && Math.abs(lng-p.lng) < .025, `${code}: no offscreen coordinate jump`);
      }
    }
    assert.ok(contains(review.outer, [p.lat,p.lng]), `${code}: main stadium center in complex`);
    const frame = stadiumBoundaryFrame(p, boundaries.stadiums[code].rings, audit.boundaryPaddingM).path;
    const copy = structuredClone(frame);
    assert.equal(stadiumComplexReviewRings(code, frame).hole, frame);
    assert.deepEqual(frame, copy, `${code}: original green frame is unchanged`);
  }
  assert.deepEqual(reviews.DAEJEON.outer, review.outer, "approved Daejeon trace unchanged");
});

test("neighboring sports venues included, unrelated water/blocks not bridged", () => {
  for (const code of ["MUNHAK", "SUWON", "GWANGJU", "CHANGWON"]) {
    const adjacent = audit.stadiums[code].excluded[0];
    assert.ok(contains(reviews[code].outer, [adjacent.lat,adjacent.lng]), `${code}: neighboring stadium`);
  }
  assert.ok(!contains(reviews.JAMSIL.outer, [37.511,127.076]), "Asia park across the road");
  assert.ok(!contains(reviews.DAEGU.outer, [35.8399,128.6783]), "Yeonho reservoir");
  assert.ok(!contains(reviews.GOCHEOK.outer, [37.498,126.869]), "Anyang stream");
  assert.equal(reviews.SAJIK.additionalOuters.length, 1, "separate block stays a separate polygon");
});

test("traces have no crossing edges", () => {
  const side = (a,b,p) => (b[1]-a[1])*(p[0]-a[0])-(b[0]-a[0])*(p[1]-a[1]);
  for (const [code, review] of Object.entries(reviews)) {
    for (const path of [review.outer, ...(review.additionalOuters ?? [])]) {
      for (let i=0;i<path.length-1;i++) for(let j=i+2;j<path.length-1;j++) {
        if(i===0&&j===path.length-2) continue;
        const a=path[i],b=path[i+1],c=path[j],d=path[j+1];
        const crossing=side(a,b,c)*side(a,b,d)<-1e-20 && side(c,d,a)*side(c,d,b)<-1e-20;
        assert.ok(!crossing, `${code}: edges ${i}/${j} must not cross`);
      }
    }
  }
});

test("shared source and frontend copy agree; main wins over complex for all nine venues", () => {
  const source = JSON.parse(readFileSync(new URL("../../data/preprocessed/stadium_complexes.json", import.meta.url), "utf8"));
  assert.deepEqual(reviews, source.stadiums);
  for (const [code, point] of Object.entries(audit.stadiums)) {
    assert.deepEqual(classifyStadiumPoint(point), { scope: "internal", stadium: code });
    const frame = stadiumBoundaryFrame(point, boundaries.stadiums[code].rings, audit.boundaryPaddingM).path;
    for (const [lat, lng] of frame) assert.equal(classifyStadiumPoint({lat, lng}).scope, "internal");
  }
  for (const code of ["DAEJEON", "MUNHAK", "SUWON", "GWANGJU", "CHANGWON"]) {
    assert.deepEqual(classifyStadiumPoint(audit.stadiums[code].excluded[0]), { scope: "excluded_complex", stadium: code });
  }
  assert.equal(classifyStadiumPoint({lat:36.3195,lng:127.4300}).scope,"external");
  assert.equal(classifyStadiumPoint({lat:35.1900,lng:129.0583}).scope,"excluded_complex");
  assert.equal(classifyStadiumPoint({lat:NaN,lng:129}).scope,"unknown");
  assert.equal(containsStadiumPoint([[0,0],[0,3],[3,3],[3,0]], {lat:0,lng:2}),true);
});
