import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

const frontend = new URL("../", import.meta.url);
const vote = readFileSync(new URL("components/community-post-vote.tsx", frontend), "utf8");

test("vote artwork preserves labels, pressed state and read-only guest summaries", () => {
  assert.match(vote, /community-vote-\$\{direction\}\.png/);
  assert.match(vote, /alt="" width=\{72\} height=\{72\}/);
  assert.match(vote, /direction === "up" \? "추천" : "비추천"/);
  assert.match(vote, /aria-pressed=\{state.vote === "up"\}/);
  assert.match(vote, /aria-pressed=\{state.vote === "down"\}/);
  const guest = vote.slice(vote.indexOf("if (!actorId) return"), vote.indexOf("return <CommunityPostVoteContent"));
  assert.match(guest, /direction="up"/);
  assert.match(guest, /direction="down"/);
  assert.doesNotMatch(guest, /<button|onClick/);
  for (const direction of ["up", "down"]) {
    const image = readFileSync(new URL(`public/images/community-vote-${direction}.png`, frontend));
    assert.equal(image.subarray(1, 4).toString(), "PNG");
  }
});

test("write links preserve the board and selected team", () => {
  const source = readFileSync(new URL("lib/team-community.ts", frontend), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
  const testModule = { exports: {} };
  new Function("module", "exports", outputText)(testModule, testModule.exports);
  const href = testModule.exports.getCommunityWriteHref;
  assert.equal(href("free", "LG"), "/community/write?board=free");
  assert.equal(href("teams", "LG"), "/community/write?board=teams&team=LG");
  assert.equal(href("teams", "invalid"), "/community/write?board=teams");
});

test("post submission keys remain unique without crypto on HTTP", () => {
  const source = readFileSync(new URL("lib/community-post-key.ts", frontend), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
  const testModule = { exports: {} };
  new Function("module", "exports", "crypto", outputText)(testModule, testModule.exports, undefined);
  const keys = Array.from({ length: 1000 }, () => testModule.exports.createCommunitySubmissionKey());
  assert.equal(new Set(keys).size, keys.length);
  assert.ok(keys.every(key => key.length > 0 && key.length <= 128));
});

test("authenticated vote waits for server state and always releases the matching request", () => {
  assert.match(vote, /useState\(Boolean\(actorId\)\)/);
  assert.match(vote, /disabled=\{pending\}/);
  assert.match(vote, /\.finally\(\(\) => \{ if \(requestRef\.current === requestId\) setPending\(false\); \}\)/);
  assert.match(vote, /key=\{`\$\{post\.id\}:\$\{actorId \?\? "anonymous"\}:\$\{post\.recommendations\}:\$\{downvotes\}`\}/);
});

test("server vote control sends up, down, and cancel as final desired states", () => {
  const source = readFileSync(new URL("lib/community-votes.ts", frontend), "utf8");
  assert.doesNotMatch(source, /useCommunityVotes|localStorage/);
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
  const testModule = { exports: {} };
  new Function("module", "exports", outputText)(testModule, testModule.exports);
  const next = testModule.exports.nextCommunityVote;
  assert.equal(next(null, "up"), "up");
  assert.equal(next("up", "up"), null);
  assert.equal(next("up", "down"), "down");
});
