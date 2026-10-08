import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const source = readFileSync(new URL("../components/chat-feedback.tsx", import.meta.url), "utf8");
const icons = { exports: {} };
new Function("require", "module", "exports", ts.transpileModule(readFileSync(new URL("../components/icons.tsx", import.meta.url), "utf8"), {
  compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS },
}).outputText)(require, icons, icons.exports);
const component = { exports: {} };
new Function("require", "module", "exports", ts.transpileModule(source, {
  compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS },
}).outputText)(name => {
  if (name === "./chat-provider") return { useChat: () => ({ onFeedback: async () => {} }) };
  if (name === "@/lib/chat/client") return { FEEDBACK_REASONS: {} };
  if (name === "@/styles/chat-feedback.css") return {};
  if (name === "./icons") return icons.exports;
  return require(name);
}, component, component.exports);
const render = (feedback, disabled = false) => renderToStaticMarkup(React.createElement(component.exports.ChatFeedback, {
  message: { id: 2, role: "assistant", status: "completed", content: "답변", feedback }, disabled,
}));

test("feedback actions are icon-only, named, toggle-aware and conditionally cancellable", () => {
  for (const rating of [undefined, "up", "down"]) {
    const html = render(rating ? { rating, reason: "", comment: "" } : undefined);
    const buttons = [...html.matchAll(/<button\b([^>]*)>(.*?)<\/button>/gs)];
    assert.equal(buttons.length, rating ? 4 : 3);
    for (const [index, label] of ["답변 좋아요", "답변 아쉬워요", "이 답변만 삭제", "평가 취소"].slice(0, buttons.length).entries()) {
      const [, attributes, content] = buttons[index];
      assert.ok(attributes.includes(`aria-label="${label}"`));
      assert.ok(attributes.includes(`title="${label === "이 답변만 삭제" ? "이 답변만 지우고 질문과 다른 대화는 남겨요" : label}"`));
      assert.match(content, /<svg[^>]*width="16"[^>]*height="16"[^>]*aria-hidden="true"/);
      assert.equal(content.replace(/<[^>]*>/g, ""), "");
      if (index < 2) assert.ok(attributes.includes(`aria-pressed="${rating === ["up", "down"][index]}"`));
    }
    assert.match(html, /aria-busy="false"/);
    assert.match(html, /role="status"/);
  }
  assert.equal([...render({ rating: "up" }, true).matchAll(/ disabled=""/g)].length, 4);
});

test("feedback never submits or mutates an enclosing course form, and works standalone", { skip: !process.env.PLAYWRIGHT_MODULE }, async () => {
  const { chromium } = require(process.env.PLAYWRIGHT_MODULE);
  const browser = await chromium.launch({ headless: true });
  try {
    // Bundle installed React and the real component in memory; no app server or new dependency.
    const modules = new Map();
    function bundle(name) {
      const path = require.resolve(name);
      if (modules.has(path)) return path;
      modules.set(path, "");
      const code = readFileSync(path, "utf8").replace(/require\(['"]([^'"]+)['"]\)/g, (_, dependency) => {
        const resolved = createRequire(path).resolve(dependency);
        return `require(${JSON.stringify(bundle(resolved))})`;
      });
      modules.set(path, code);
      return path;
    }
    const reactPath = bundle("react"), clientPath = bundle("react-dom/client");
    const compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.React, module: ts.ModuleKind.CommonJS } }).outputText;
    const page = await browser.newPage();
    for (const enclosingForm of [true, false]) {
      await page.setContent('<div id="root"></div>');
      await page.addScriptTag({ content: `
        var process = { env: { NODE_ENV: "production" } };
        var factories = {${[...modules].map(([path, code]) => `${JSON.stringify(path)}: function(require,module,exports){${code}\n}`).join(",")}};
        var cache = {};
        function require(name) { if (!cache[name]) { var m = cache[name] = {exports:{}}; factories[name](require,m,m.exports); } return cache[name].exports; }
        var React = require(${JSON.stringify(reactPath)});
        window.feedbackCalls = []; window.parentSubmits = 0; window.feedbackFailure = false;
        var feedbackModule = {exports:{}};
        (function(require,module,exports){${compiled}\n})(name => {
          if(name === "react") return React;
          if(name === "./chat-provider") return {useChat: () => ({onFeedback: async (id,value) => {
            window.feedbackCalls.push({id,value});
            await new Promise(resolve => window.finishFeedback = resolve);
            if(window.feedbackFailure) throw new Error("평가 저장 실패");
          }})};
          if(name === "@/lib/chat/client") return {FEEDBACK_REASONS: {inaccurate: "정확하지 않아요"}};
          if(name === "./icons") return {Icon: () => null};
          return {};
        }, feedbackModule, feedbackModule.exports);
        var fields = [React.createElement("input", {key:"title", id:"title", defaultValue:"보존할 코스 제목"}),
          React.createElement("textarea", {key:"body", id:"body", defaultValue:"보존할 이야기"}),
          React.createElement("input", {key:"stops", id:"stops", defaultValue:'["잠실","식당"]'}),
          React.createElement(feedbackModule.exports.ChatFeedback, {key:"feedback", message:{id:2,role:"assistant",status:"completed",content:"답변"}, disabled:false}),
          React.createElement("button", {key:"publish",type:"submit"}, "코스 공개")];
        require(${JSON.stringify(clientPath)}).createRoot(document.getElementById("root")).render(
          React.createElement(${JSON.stringify(enclosingForm ? "form" : "div")}, {onSubmit:e=>{e.preventDefault();window.parentSubmits++;}, onKeyDown:e=>{if(e.key==="Enter") {e.preventDefault();window.parentSubmits++;}}}, ...fields));
      ` });
      await page.getByRole("button", { name: "답변 아쉬워요", exact: true }).press("Enter");
      assert.equal(await page.locator("form").count(), enclosingForm ? 1 : 0);
      await page.getByLabel("사유 (선택)").selectOption("inaccurate");
      await page.getByLabel("사유 (선택)").press("Enter");
      const comment = page.getByLabel("의견 (선택, 1000자 이하)");
      assert.equal(await comment.getAttribute("maxlength"), "1000");
      assert.ok(await comment.getAttribute("aria-describedby"));
      await comment.fill("첫 줄");
      await comment.press("End");
      await comment.press("Enter");
      await comment.press("x");
      assert.equal(await comment.inputValue(), "첫 줄\nx");
      const save = page.getByRole("button", { name: "평가 저장", exact: true });
      await save.press("Enter");
      assert.equal(await save.isDisabled(), true);
      assert.equal(await page.getByRole("status").textContent(), "평가 저장 중…");
      await save.evaluate(button => button.click());
      assert.deepEqual(await page.evaluate(() => window.feedbackCalls), [{ id: 2, value: { rating: "down", reason: "inaccurate", comment: "첫 줄\nx" } }]);
      await page.evaluate(() => window.finishFeedback());
      await page.getByRole("status").filter({ hasText: "평가를 저장했어요." }).waitFor();
      await page.getByRole("button", { name: "답변 아쉬워요", exact: true }).click();
      await comment.fill("다시 시도");
      await save.click();
      await page.evaluate(() => { window.feedbackFailure = true; window.finishFeedback(); });
      await page.getByRole("alert").filter({ hasText: "평가 저장 실패" }).waitFor();
      assert.equal(await comment.inputValue(), "다시 시도");
      assert.equal(await save.isEnabled(), true);
      await page.getByRole("button", { name: "닫기", exact: true }).press("Enter");
      assert.equal(await page.evaluate(() => window.parentSubmits), 0);
      assert.equal(await page.locator("#title").inputValue(), "보존할 코스 제목");
      assert.equal(await page.locator("#body").inputValue(), "보존할 이야기");
      assert.equal(await page.locator("#stops").inputValue(), '["잠실","식당"]');
    }
  } finally { await browser.close(); }
});

test("compact sizing stays scoped to actions and keyboard focus remains visible", () => {
  const css = readFileSync(new URL("../styles/chat-feedback.css", import.meta.url), "utf8");
  assert.match(css, /\.answer-feedback-actions button \{[^}]*width: 30px; height: 30px; min-height: 30px; padding: 0;/);
  assert.match(css, /\.answer-feedback :focus-visible \{[^}]*outline: 2px/);
  assert.match(css, /\.answer-feedback-actions \{[^}]*flex-wrap: nowrap; align-items: center; gap: 4px;/);
  assert.match(css, /\.answer-feedback \{ margin-top: 4px;/);
  for (const surface of ["workspace", "chat-popup"]) {
    assert.ok(css.includes(`.${surface}-message:hover .answer-feedback-actions`));
    assert.ok(css.includes(`.${surface}-message:focus-within .answer-feedback-actions`));
  }
  assert.match(css, /@media \(hover: none\), \(pointer: coarse\)/);
  const workspace = readFileSync(new URL("../styles/chat-workspace.css", import.meta.url), "utf8");
  assert.match(workspace, /\.chat-workspace \.answer-feedback \{ margin-left: 34px; \}/);
  assert.match(workspace, /\.chat-workspace \.answer-feedback \{ margin-left: 0; \}/);
});
