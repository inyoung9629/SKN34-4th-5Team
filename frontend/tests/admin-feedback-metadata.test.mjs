import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const module = { exports: {} };
const source = readFileSync(new URL("../components/admin-feedback-metadata.tsx", import.meta.url), "utf8");
new Function("require", "module", "exports", ts.transpileModule(source, {
  compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS },
}).outputText)(name => name.endsWith(".module.css") ? { default: {} } : require(name), module, module.exports);
const render = metadata => renderToStaticMarkup(React.createElement(module.exports.AdminFeedbackMetadata, {
  sessionId: "session-123", answerId: "answer-456", messageId: 0, metadata,
}));

test("identifiers have separate labels and raw JSON stays collapsed", () => {
  const html = render({ version: "v2" });
  for (const value of ["세션 ID", "실제 답변 ID", "공개 번호", "session-123", "answer-456", "version", "v2", "원본 JSON 보기"]) {
    assert.ok(html.includes(value));
  }
  assert.doesNotMatch(html, /<details[^>]*\bopen/);
});
test("metadata preserves nested values, empty values and escapes untrusted text", () => {
  const html = render({ nested: { score: 0, enabled: false, nothing: null, empty: "" }, list: ["<script>alert(1)</script>", 2], object: {}, array: [] });
  for (const value of ["score", ">0<", ">false<", ">null<", "빈 객체", "빈 배열", "<ol", "&lt;script&gt;"]) assert.ok(html.includes(value), value);
  assert.doesNotMatch(html, /<script>/);
});
test("empty metadata and deeply nested JSON remain available", () => {
  assert.ok(render({}).includes("저장된 메타데이터가 없어요."));
  let metadata = { leaf: "preserved-value" };
  for (let i = 0; i < 10; i++) metadata = { nested: metadata };
  assert.ok(render(metadata).includes("preserved-value"));
});
