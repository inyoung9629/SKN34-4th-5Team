import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createRequire } from "node:module";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-auth-origin-"));
const previous = process.env.APP_ORIGIN;
after(() => {
  if (previous === undefined) delete process.env.APP_ORIGIN;
  else process.env.APP_ORIGIN = previous;
  rmSync(scratch, { recursive: true, force: true });
});
for (const name of ["server-only", "next"]) mkdirSync(join(scratch, "node_modules", name), { recursive: true });
writeFileSync(join(scratch, "node_modules/server-only/index.js"), "module.exports = {};\n");
writeFileSync(join(scratch, "node_modules/next/headers.js"), "exports.cookies = () => { throw Error('Origin checks must not access cookies'); };\n");
mkdirSync(join(scratch, "chat"));
for (const name of ["team-backend", "chat/validation", "chat/types", "chat/course", "course-directions"]) {
  const source = readFileSync(join(root, "lib", `${name}.ts`), "utf8");
  const { outputText } = ts.transpileModule(source, {
    fileName: `${name}.ts`,
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  });
  writeFileSync(join(scratch, `${name}.js`), outputText);
}
const require = createRequire(join(scratch, "test.cjs"));
const { checkSameOrigin } = require("./team-backend.js");
const request = (origin, extra = {}, url = "http://0.0.0.0:3000/member-preview-password") => new Request(url, {
  method: "POST", headers: { ...(origin === undefined ? {} : { origin }), ...extra },
});
const status = (value) => (error) => error.status === value;

test("configured public origin accepts Docker port mapping", () => {
  process.env.APP_ORIGIN = "http://127.0.0.1:3100";
  assert.doesNotThrow(() => checkSameOrigin(request(process.env.APP_ORIGIN, { "sec-fetch-site": "same-origin" })));
});
for (const origin of ["http://127.0.0.1:3000", "http://localhost:3100", "http://evil.example", "http://127.0.0.1:3100.evil.example", "null", "http://0.0.0.0:3000"]) {
  test(`configured public origin rejects ${origin}`, () => {
    process.env.APP_ORIGIN = "http://127.0.0.1:3100";
    assert.throws(() => checkSameOrigin(request(origin)), status(403));
  });
}
test("cross-site metadata is rejected even with matching origin", () => {
  process.env.APP_ORIGIN = "http://127.0.0.1:3100";
  assert.throws(() => checkSameOrigin(request(process.env.APP_ORIGIN, { "sec-fetch-site": "cross-site" })), status(403));
});
test("spoofed forwarding and host headers do not change trusted origin", () => {
  process.env.APP_ORIGIN = "http://127.0.0.1:3100";
  assert.throws(() => checkSameOrigin(request("https://evil.example", {
    host: "evil.example", "x-forwarded-host": "evil.example", "x-forwarded-proto": "https",
  })), status(403));
});
for (const configured of ["*", "not a URL", "https://example.test/path", "https://name:pass@example.test", "https://example.test?x=1", "https://example.test#fragment", "file:///tmp/test"]) {
  test(`invalid APP_ORIGIN fails closed: ${configured}`, () => {
    process.env.APP_ORIGIN = configured;
    assert.throws(() => checkSameOrigin(request("http://0.0.0.0:3000")), status(503));
  });
}
test("without configuration the existing request URL check is preserved", () => {
  delete process.env.APP_ORIGIN;
  assert.doesNotThrow(() => checkSameOrigin(request("http://0.0.0.0:3000")));
  assert.throws(() => checkSameOrigin(request("https://evil.example")), status(403));
});
test("non-browser requests without Origin retain existing behavior", () => {
  process.env.APP_ORIGIN = "http://127.0.0.1:3100";
  assert.doesNotThrow(() => checkSameOrigin(request(undefined)));
  assert.throws(() => checkSameOrigin(request(undefined, { "sec-fetch-site": "cross-site" })), status(403));
});
