import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const React = require("react");

function load(path, mocks) {
  const source = readFileSync(new URL(path, import.meta.url), "utf8");
  const context = { exports: {}, require(id) {
    if (id in mocks) return mocks[id];
    if (id === "react" || id === "react/jsx-runtime") return require(id);
    if (id.endsWith(".css")) return {};
    throw new Error(`Unexpected dependency: ${id}`);
  } };
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, esModuleInterop: true,
  } }).outputText, context);
  return context.exports;
}
function elements(node) {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!React.isValidElement(node)) return [];
  return [node, ...elements(node.props.children)];
}

test("workspace transcript contains absolute accessibility content and keeps inner scrolling", () => {
  const css = readFileSync(new URL("../styles/chat-workspace.css", import.meta.url), "utf8");
  const transcript = css.match(/\.workspace-transcript\s*\{([^}]+)\}/)?.[1];
  assert.ok(transcript);
  assert.match(transcript, /position:\s*relative\s*;/);
  assert.match(transcript, /overflow-y:\s*auto\s*;/);
  assert.match(transcript, /min-height:\s*0\s*;/);
});

test("home question form hands guest and member questions to chat only on submit", () => {
  let status = "anonymous";
  const calls = [];
  const auth = { useMemberAuth: () => ({ status, user: null }) };
  const chat = { messages: [], conversations: [], draft: "", pending: "", failed: "",
    queued: [], editingQueuedId: null,
    activeConversationId: "initial-chat", status: null, statusLoading: false,
    editingMessageId: null, timeline: [], openChat: (...args) => calls.push(args) };
  let question = "";
  const composingRef = { current: false };
  const hooks = { ...React, useState: () => [question, value => { question = value; }],
    useRef: () => composingRef, useEffect() {} };
  const common = { react: hooks, "next/link": { __esModule: true, default: "a" },
    "next/image": { __esModule: true, default: "img" },
    "@/lib/member-auth": auth, "./chat-provider": { useChat: () => chat },
    "@/lib/chat/types": { MAX_MESSAGE_LENGTH: 2000 },
    "@/lib/chat/inline-urls": { chatComposerContent: text => text.trim() },
    "./icons": { Icon: "icon", Baseball: "baseball", CapBot: "cap-bot" } };
  const home = load("../components/home-page.tsx", { ...common,
    "@/lib/routes": { useRoutes: () => [], useRoutesReady: () => true },
    "./game-schedule": { GameSchedule: "schedule" }, "./ad-slot": { AdSlot: "ad" },
    "./route-card": { RouteCard: "route" }, "./route-skeleton": { RouteCardsSkeleton: "skeleton" } });
  const renderHero = () => elements(home.HomePage()).find(item => item.props.className === "hero-search");
  const findInput = hero => elements(hero).find(item => item.type === "input");
  const submit = hero => {
    let prevented = false;
    hero.props.onSubmit({ preventDefault() { prevented = true; } });
    assert.equal(prevented, true);
  };
  // Model the browser's implicit form submission only when Enter isn't cancelled.
  const enter = (hero, options = {}) => {
    let prevented = false;
    findInput(hero).props.onKeyDown({ key: "Enter", nativeEvent: { isComposing: false },
      keyCode: 13, ...options, preventDefault() { prevented = true; } });
    if (!prevented) submit(hero);
    return prevented;
  };
  const hero = renderHero();
  const input = findInput(hero);
  assert.equal(hero.type, "form");
  assert.equal(hero.props.href, undefined);
  assert.equal(hero.props.onClick, undefined);
  assert.equal(input.props.onClick, undefined);
  assert.equal(input.props.onFocus, undefined);
  assert.equal(input.props.maxLength, 2000);
  assert.equal(input.props.tabIndex, undefined);
  assert.ok(elements(hero).some(item => item.type === "label" && item.props.htmlFor === input.props.id));
  input.props.onChange({ target: { value: "잠실 경기 전 맛집 추천" } });
  assert.deepEqual(calls, []);
  assert.equal(findInput(renderHero()).props.value, "잠실 경기 전 맛집 추천");

  const workspace = load("../components/chat-workspace.tsx", { ...common,
    "./chat-answer": { ChatAnswer: "answer" }, "./chat-planning": { ChatQuestions: "questions", ChatUserContent: "content", ChatWriterOffer: "offer" },
    "./chat-feedback": { ChatFeedback: "feedback" }, "./chat-course-card": { ChatCourseCard: "course" },
    "./chat-pending": { ChatPending: "pending" }, "./chat-progress": { ChatProgress: "progress", ChatSubAgentStatus: "subagents" },
    "./chat-inline-input": { ChatInlineInput: "inline-input" },
    "./chat-composer-tools": { ChatComposerTools: "tools", ChatAttachmentCards: "attachments" },
    "./chat-course-preferences": { ChatCoursePreferences: "preferences" }, "./chat-queue": { ChatQueue: "queue" }, "./chat-usage": { ChatUsage: "usage" } });
  const destination = load("../app/chat/page.tsx", { "@/components/chat-workspace": workspace });
  assert.equal(destination.default().type, workspace.ChatWorkspace);
  const rendered = workspace.ChatWorkspace();
  assert.equal(rendered.props.className, "chat-workspace");
  assert.ok(elements(rendered).some(item => item.type === "h1" && item.props.children === "직관 도우미"));
  assert.deepEqual(calls, []);

  for (status of ["anonymous", "authenticated"]) {
    const current = renderHero();
    const button = elements(current).find(item => item.type === "button");
    assert.equal(button.props.type, "submit");
    assert.equal(button.props.disabled, false);
    assert.equal(button.props["aria-label"], "직관 도우미에게 질문하기");
    calls.length = 0;
    submit(current);
    assert.deepEqual(calls, [[question]]);
    calls.length = 0;
    assert.equal(enter(current), false);
    assert.deepEqual(calls, [[question]]);

    calls.length = 0;
    const currentInput = findInput(current);
    currentInput.props.onCompositionStart();
    assert.equal(enter(current), true);
    submit(current);
    assert.deepEqual(calls, []);
    currentInput.props.onCompositionEnd();
    assert.equal(enter(current, { nativeEvent: { isComposing: true } }), true);
    assert.equal(enter(current, { keyCode: 229 }), true);
    assert.deepEqual(calls, []);
    assert.equal(enter(current), false);
    assert.deepEqual(calls, [[question]]);
  }

  for (status of ["loading", "unavailable"]) {
    calls.length = 0;
    const blocked = renderHero();
    assert.equal(elements(blocked).find(item => item.type === "button").props.disabled, true);
    submit(blocked);
    enter(blocked);
    assert.deepEqual(calls, []);
  }

  for (status of ["anonymous", "authenticated"]) {
    for (question of ["", "   "]) {
      calls.length = 0;
      submit(renderHero());
      assert.deepEqual(calls, [[question]]);
    }
  }
});
