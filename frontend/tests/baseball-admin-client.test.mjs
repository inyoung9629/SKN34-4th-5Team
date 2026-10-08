import assert from "node:assert/strict";
import { after, beforeEach, test } from "node:test";
import { createRequire } from "node:module";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-baseball-admin-test-"));
after(() => rmSync(scratch, { recursive: true, force: true }));
for (const name of ["lib/api/client", "lib/member-auth-request", "lib/baseball/client"]) {
  const source = readFileSync(join(frontend, `${name}.ts`), "utf8");
  const { outputText } = ts.transpileModule(source, {
    fileName: `${name}.ts`, compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  });
  mkdirSync(dirname(join(scratch, `${name}.js`)), { recursive: true });
  writeFileSync(join(scratch, `${name}.js`), outputText);
}

const stored = new Map();
global.sessionStorage = {
  getItem: key => stored.get(key) ?? null,
  setItem: (key, value) => stored.set(key, value),
  removeItem: key => stored.delete(key),
};
const require = createRequire(join(scratch, "entry.cjs"));
const { ApiError } = require("./lib/api/client.js");
const { clearMemberTokens, saveMemberTokens } = require("./lib/member-auth-request.js");
const { deleteAdminRow, fetchAdminDetail, fetchAdminPage, updateAdminRow } = require("./lib/baseball/client.js");

beforeEach(() => { stored.clear(); clearMemberTokens(); saveMemberTokens("access-token", "refresh-token"); });

test("admin uses direct Bearer CRUD with ETag and preserves bodyless 204", async () => {
  const calls = [];
  global.fetch = async (url, init = {}) => {
    const headers = new Headers(init.headers);
    calls.push({ url: String(url), method: init.method ?? "GET", authorization: headers.get("Authorization"), etag: headers.get("If-Match"), body: init.body });
    if (init.method === "DELETE") return new Response(null, { status: 204 });
    if (init.method === "PATCH") return Response.json({ id: 1, team_code: "LG", team_name_ko: "엘지", _etag: '"next"' });
    if (String(url).endsWith("/1/")) return Response.json({ id: 1, team_code: "LG", team_name_ko: "엘지", _etag: '"etag"' });
    return Response.json({ count: 1, next: null, previous: null, results: [{ id: 1, team_code: "LG", team_name_ko: "엘지" }] });
  };
  assert.equal((await fetchAdminPage("teams")).results[0].team_code, "LG");
  const detail = await fetchAdminDetail("teams", 1);
  await updateAdminRow("teams", 1, detail._etag, { team_name_ko: "엘지" });
  assert.equal(await deleteAdminRow("teams", 1, '"next"'), undefined);
  assert.ok(calls.every(call => call.url.startsWith("/api/v1/baseball/manage/") && call.authorization === "Bearer access-token"));
  assert.equal(calls[2].etag, '"etag"');
  assert.equal(calls[3].etag, '"next"');
  assert.equal(JSON.parse(calls[2].body)._etag, undefined);
});

test("stale admin update keeps 412 and field errors", async () => {
  global.fetch = async () => Response.json({ code: "stale_write", message: "다른 관리자가 먼저 변경했습니다.", field_errors: { if_match: ["stale"] } }, { status: 412 });
  await assert.rejects(
    updateAdminRow("teams", 1, '"old"', { team_name_ko: "변경" }),
    error => error instanceof ApiError && error.status === 412 && error.fields.if_match[0] === "stale",
  );
});
