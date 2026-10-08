import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-api-client-test-"));
after(() => rmSync(scratch, { recursive: true, force: true }));
const source = readFileSync(join(frontend, "lib", "api", "client.ts"), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
});
writeFileSync(join(scratch, "client.cjs"), outputText);
const { ApiError, apiRequest, isAbortError, readApiResponse } = createRequire(import.meta.url)(join(scratch, "client.cjs"));

test("bodyless HTTP responses are null", async () => {
  for (const status of [204, 205, 304]) assert.equal(await readApiResponse(new Response(null, { status })), null);
});

test("DRF field errors keep status, fields, and original body", async () => {
  const body = { field_errors: { title: ["필수 항목입니다."], stops: { 0: { name: ["필수 항목입니다."] } } } };
  await assert.rejects(readApiResponse(new Response(JSON.stringify(body), { status: 400 })), error => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 400);
    assert.deepEqual(error.fields, { title: ["필수 항목입니다."], stops: ["필수 항목입니다."] });
    assert.deepEqual(error.body, body);
    return true;
  });
});

test("successful non-JSON responses fail safely", async () => {
  await assert.rejects(readApiResponse(new Response("<html>proxy error</html>"), "응답 형식 오류"), error =>
    error instanceof ApiError && error.status === 200 && error.message === "응답 형식 오류");
  assert.equal(await readApiResponse(new Response(JSON.stringify("valid JSON string"))), "valid JSON string");
});

test("injected fetchers preserve abort errors", async () => {
  const abort = new DOMException("stopped", "AbortError");
  await assert.rejects(apiRequest("/api/v1/example/", {}, async () => { throw abort; }), error => error === abort);
  assert.equal(isAbortError(abort), true);
});
