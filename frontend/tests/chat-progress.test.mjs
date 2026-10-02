import assert from "node:assert/strict";
import { after, beforeEach, test } from "node:test";
import { createRequire } from "node:module";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-chat-progress-test-"));
after(() => rmSync(scratch, { recursive: true, force: true }));
symlinkSync(join(frontend, "node_modules"), join(scratch, "node_modules"), "dir");
for (const name of ["lib/member-auth-request", "lib/chat/types", "lib/chat/validation", "lib/chat/course", "lib/chat/wire", "lib/chat/history", "lib/chat/client"]) {
  const source = readFileSync(join(frontend, `${name}.ts`), "utf8");
  const { outputText } = ts.transpileModule(source, {
    fileName: `${name}.ts`, compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  });
  mkdirSync(dirname(join(scratch, `${name}.js`)), { recursive: true });
  writeFileSync(join(scratch, `${name}.js`), outputText);
}
{
  const source = readFileSync(join(frontend, "components/chat-pending.tsx"), "utf8");
  const { outputText } = ts.transpileModule(source, {
    fileName: "chat-pending.tsx", compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  });
  mkdirSync(join(scratch, "components"), { recursive: true });
  writeFileSync(join(scratch, "components/chat-pending.js"), outputText);
}
{
  const source = readFileSync(join(frontend, "components/chat-answer.tsx"), "utf8");
  const { outputText } = ts.transpileModule(source, {
    fileName: "chat-answer.tsx", compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  });
  writeFileSync(join(scratch, "components/chat-answer.js"), outputText);
}
{
  const source = readFileSync(join(frontend, "components/chat-progress.tsx"), "utf8").replace(/^import "@\/styles\/chat-progress\.css";$/m, "");
  const { outputText } = ts.transpileModule(source, {
    fileName: "chat-progress.tsx", compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  });
  writeFileSync(join(scratch, "components/chat-progress.js"), outputText);
}

global.window = { setTimeout, clearTimeout, location: { origin: "http://localhost" } };
const stored = new Map();
global.sessionStorage = {
  getItem: key => stored.get(key) ?? null,
  setItem: (key, value) => stored.set(key, value),
  removeItem: key => stored.delete(key),
};
const require = createRequire(join(scratch, "entry.cjs"));
const { clearMemberTokens, saveMemberTokens } = require("./lib/member-auth-request.js");
const { parseChatRequest } = require("./lib/chat/validation.js");
const { commitChatLoad, restoreChatMessages } = require("./lib/chat/history.js");
const { ChatClientError, fetchChatHistory, sendChatMessage } = require("./lib/chat/client.js");
const { ChatPending } = require("./components/chat-pending.js");
const { ChatProgress } = require("./components/chat-progress.js");
const { appendTimeline, buildTimeline } = require("./lib/chat/types.js");
const toolItems = tools => tools.length ? [{ kind: "tools", tools: tools.map(tool => ({ kind: "tool", parentId: null, ...tool })) }] : [];

const SESSION = "22222222-2222-4222-8222-222222222222";
const USER_MSG = 1;
const ASSISTANT_MSG = 3;
const frame = (name, data) => `event: ${name}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`;
const stream = (text, splitUtf8 = false) => new Response(new ReadableStream({
  start(controller) {
    const bytes = new TextEncoder().encode(text);
    if (!splitUtf8) controller.enqueue(bytes);
    else {
      const korean = bytes.findIndex(byte => byte >= 0xe0);
      for (const chunk of [bytes.slice(0, 7), bytes.slice(7, korean + 1), bytes.slice(korean + 1, korean + 2), bytes.slice(korean + 2)]) if (chunk.length) controller.enqueue(chunk);
    }
    controller.close();
  },
}), { headers: { "Content-Type": "text/event-stream; charset=utf-8" } });
const json = value => Response.json(value);
const row = (id, role, content, status = "completed", tools = []) => ({ id, sequence_no: id, role, content, status, tools, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" });

beforeEach(() => { stored.clear(); clearMemberTokens(); });

test("pre-answer busy indicator renders until the first streamed text", () => {
  for (const className of ["workspace-thinking", "chat-popup-thinking"]) {
    const pending = renderToStaticMarkup(React.createElement(ChatPending, { busy: true, streaming: "", className }));
    assert.match(pending, /응답 준비 중…/);
    assert.equal((pending.match(/<i><\/i>/g) ?? []).length, 3);
  }
  assert.equal(renderToStaticMarkup(React.createElement(ChatPending, { busy: true, streaming: "첫 토큰", className: "workspace-thinking" })), "");
  assert.equal(renderToStaticMarkup(React.createElement(ChatPending, { busy: false, streaming: "", className: "workspace-thinking" })), "");
});

test("tool log renders Korean labels for known and unknown tool names, no raw names or args", () => {
  const known = renderToStaticMarkup(React.createElement(ChatProgress, { items: toolItems([{ id: "call-1", toolName: "get_directions", status: "completed", kind: "tool", parentId: null }]) }));
  assert.match(known, /경로 검색/);
  assert.match(known, /정보 조회 완료/);
  assert.doesNotMatch(known, /get_directions/);

  const running = renderToStaticMarkup(React.createElement(ChatProgress, { items: toolItems([{ id: "call-2", toolName: "search_documents_tool", status: "running" }]) }));
  assert.match(running, /규칙·안내 문서 검색/);
  assert.match(running, /조회 중/);

  const unknown = renderToStaticMarkup(React.createElement(ChatProgress, { items: toolItems([{ id: "call-3", toolName: "brand_new_tool", status: "failed" }]) }));
  assert.match(unknown, /정보 조회/);
  assert.match(unknown, /실패/);
  assert.doesNotMatch(unknown, /brand_new_tool/);

  assert.equal(renderToStaticMarkup(React.createElement(ChatProgress, { items: toolItems([]) })), "");
  const specialists = renderToStaticMarkup(React.createElement(ChatProgress, { items: toolItems([
    { id: "s1", toolName: "ask_baseball", status: "running" },
    { id: "s2", toolName: "ask_travel_research", status: "completed" },
    { id: "s3", toolName: "ask_place_data", status: "running" },
  ]) }));
  assert.match(specialists, /야구 정보 확인 조회 중/);
  assert.match(specialists, /여행 정보 조사 정보 조회 완료/);
  assert.match(specialists, /장소 정보 확인 조회 중/);
  assert.doesNotMatch(specialists, /ask_|<span>정보 조회/);
});

test("display progress never enters the model message payload", () => {
  const parsed = parseChatRequest({ messages: [{ role: "assistant", content: "이전 답변", tools: [{ id: "x", tool_name: "y", status: "completed" }] }, { role: "user", content: "후속 질문" }] });
  assert.deepEqual(parsed.messages, [{ role: "assistant", content: "이전 답변" }, { role: "user", content: "후속 질문" }]);
});

test("an interrupted question restores without a placeholder and a follow-up sends only the new question", async () => {
  // Stop leaves the user row pending with no assistant row; history is server-built from completed rows.
  const restored = restoreChatMessages([row(USER_MSG, "user", "멈춘 질문", "pending")]);
  assert.deepEqual(restored, [{ id: USER_MSG, role: "user", content: "멈춘 질문", status: "pending" }]);
  let posted;
  global.fetch = async (_url, init) => {
    posted = JSON.parse(init.body);
    return stream(frame("delta", { text: "후속 답변" }) + frame("done", { message_id: String(ASSISTANT_MSG), assistant_message: "후속 답변", tools: [] }));
  };
  await sendChatMessage("guest", { sessionId: SESSION, content: "후속 질문" });
  assert.deepEqual(posted, { content: "후속 질문" });
  assert.throws(() => parseChatRequest({ messages: [{ role: "assistant", content: "" }, { role: "user", content: "질문" }] }), /비어 있거나/);
  assert.throws(() => parseChatRequest({ messages: [{ role: "user", content: "" }] }), /비어 있거나/);
});

test("late chat loads cannot commit after a local action invalidates them", async () => {
  const controller = new AbortController();
  const state = { messages: ["새 질문", "새 답변"], draft: "작성 중", tools: ["조회 완료"], sessions: ["새 대화"] };
  let resolve;
  const late = new Promise(done => { resolve = done; }).then(snapshot => {
    commitChatLoad(controller.signal, () => true, () => Object.assign(state, snapshot));
  });
  controller.abort();
  resolve({ messages: ["옛 기록"], draft: "", tools: [], sessions: ["옛 대화"] });
  await late;
  assert.deepEqual(state, { messages: ["새 질문", "새 답변"], draft: "작성 중", tools: ["조회 완료"], sessions: ["새 대화"] });

  const stillConnected = new AbortController();
  commitChatLoad(stillConnected.signal, () => false, () => Object.assign(state, { sessions: ["늦은 목록"] }));
  assert.deepEqual(state.sessions, ["새 대화"]);
});

test("v2 SSE handles CRLF frames split across UTF-8 boundaries", async () => {
  saveMemberTokens("access-token", "refresh-token");
  const seen = [];
  global.fetch = async () => stream(frame("delta", { text: "완" }) + frame("delta", { text: "료" }) + frame("done", { message_id: String(ASSISTANT_MSG), assistant_message: "완료", tools: [] }), true);
  const reply = await sendChatMessage("member", { sessionId: SESSION, content: "질문" }, undefined, { onDelta: value => seen.push(value) });
  assert.deepEqual([reply.reply, reply.assistantMessageId, seen], ["완료", ASSISTANT_MSG, ["완", "료"]]);
});

for (const [name, frames] of [
  ["the retired progress event name", frame("progress", { turn_id: SESSION })],
  ["retired checkpoint frames", frame("checkpoint", { turn_id: SESSION, receipt: "empty" })],
  ["oversize deltas", Array.from({ length: 9 }, () => frame("delta", { text: "x".repeat(7200), parent_id: "A" })).join("")],
  ["frames after done", frame("done", { message_id: String(ASSISTANT_MSG), assistant_message: "답", tools: [] }) + frame("delta", { text: "더" })],
  ["non-JSON data", "event: delta\r\ndata: {text\r\n\r\n"],
]) {
  test(`v2 SSE rejects ${name}`, async () => {
    global.fetch = async () => stream(frames);
    await assert.rejects(sendChatMessage("guest", { sessionId: SESSION, content: "질문" }), error => error instanceof ChatClientError && error.status === 502 && error.uncertain);
  });
}

test("v2 SSE accepts tool events and threads them to onTool", async () => {
  const seen = [];
  global.fetch = async () => stream(
    frame("tool", { id: "call-1", tool_name: "get_directions", status: "running" }) +
    frame("tool", { id: "call-1", tool_name: "get_directions", status: "completed" }) +
    frame("done", { message_id: String(ASSISTANT_MSG), assistant_message: "답", tools: [{ id: "call-1", tool_name: "get_directions", status: "completed" }] }),
  );
  const reply = await sendChatMessage("guest", { sessionId: SESSION, content: "질문" }, undefined, { onTool: value => seen.push(value) });
  assert.deepEqual(seen, [{ id: "call-1", tool_name: "get_directions", status: "running" }, { id: "call-1", tool_name: "get_directions", status: "completed" }]);
  assert.deepEqual(reply.tools, [{ id: "call-1", toolName: "get_directions", status: "completed", kind: "tool", parentId: null }]);
});

test("history is one plain array from the session messages endpoint", async () => {
  const calls = [];
  global.fetch = async url => { calls.push(String(url)); return json([row(ASSISTANT_MSG, "assistant", "답"), row(USER_MSG, "user", "질문")]); };
  const history = await fetchChatHistory("guest", SESSION);
  assert.deepEqual(calls, [`/api/v2/chat/sessions/${SESSION}/messages/`]);
  assert.deepEqual(restoreChatMessages(history).map(message => message.content), ["답", "질문"]);
  global.fetch = async () => json({ count: 1, next: null, results: [] });
  await assert.rejects(fetchChatHistory("guest", SESSION), error => error instanceof ChatClientError && error.status === 502);
});

test("history keeps failed and stopped turns visible with their status", () => {
  const restored = restoreChatMessages([row(USER_MSG, "user", "실패한 질문", "failed"), row(ASSISTANT_MSG, "assistant", "부분 답", "stopped")]);
  assert.deepEqual(restored.map(message => [message.role, message.content, message.status]), [["user", "실패한 질문", "failed"], ["assistant", "부분 답", "stopped"]]);
});

test("both chat surfaces share a tool-only call log with no debug details", () => {
  const component = readFileSync(join(frontend, "components/chat-progress.tsx"), "utf8");
  const styles = readFileSync(join(frontend, "styles/chat-progress.css"), "utf8");
  for (const path of ["components/chat-popup.tsx", "components/chat-workspace.tsx"]) {
    const surface = readFileSync(join(frontend, path), "utf8");
    assert.match(surface, /<ChatProgress[^>]*items=/);
    assert.match(surface, /<ChatPending busy=\{busy\} streaming=\{chat\.streaming\}/);
  }
  assert.match(component, /tool\.status/);
  assert.match(component, /tool\.toolName/);
  assert.match(component, /도구 호출 로그/);
  assert.doesNotMatch(component, /관리자 로그|진행 과정|단계/);
  assert.doesNotMatch(component, /dangerouslySetInnerHTML/);
  assert.match(styles, /prefers-reduced-motion: reduce/);
  assert.match(styles, /overflow-wrap: anywhere/);
});

const tl = (id, toolName, status, extra = {}) => ({ id, toolName, status, kind: "tool", parentId: null, ...extra });

test("appendTimeline groups inner events by parent_id and keeps two interleaved sub-agents apart", () => {
  const a = tl("A", "ask_baseball", "running", { kind: "sub_agent", title: "잠실 일정" });
  const b = tl("B", "ask_place_data", "running", { kind: "sub_agent" });
  let items = [];
  for (const [event, parent] of [["먼저 구장 정보를 확인해 볼게요.", null], [a], [b], ["A생각", "A"], ["B생각", "B"],
    [tl("a1", "get_stadium", "running", { parentId: "A" })], [tl("b1", "search_places", "running", { parentId: "B" })], ["A더", "A"],
    [{ ...a, status: "completed" }]]) items = appendTimeline(items, event, parent);
  const html = renderToStaticMarkup(React.createElement(ChatProgress, { items }));
  assert.ok(html.indexOf("먼저 구장 정보를") < html.indexOf("야구 정보 확인 (서브에이전트) 정보 조회 완료"));
  const [aBlock, bBlock] = html.split("장소 정보 확인 (서브에이전트)");
  assert.match(aBlock, /A생각.*구장 정보 조회.*A더/); assert.match(aBlock, /구장 정보 조회/); assert.doesNotMatch(aBlock, /B생각|장소 검색/);
  assert.match(bBlock, /B생각/); assert.match(bBlock, /장소 검색/); assert.doesNotMatch(bBlock, /A생각|구장 정보 조회/);
  assert.equal((html.match(/chat-progress-nested/g) ?? []).length, 2);
  assert.doesNotMatch(html, /ask_|get_stadium|search_places|상세/);
});

test("history steps rebuild the same timeline; missing kind/parent_id/steps default to tool/null/[]", () => {
  const base = { id: ASSISTANT_MSG, sequence_no: 2, role: "assistant", content: "답", status: "completed", created_at: "x", updated_at: "x" };
  const [restored] = restoreChatMessages([{ ...base,
    tools: [{ id: "A", tool_name: "ask_baseball", status: "completed", kind: "sub_agent", parent_id: null, title: "t" }, { id: "a1", tool_name: "get_stadium", status: "completed", kind: "tool", parent_id: "A" }],
    steps: [{ type: "text", text: "확인할게요", parent_id: null }, { type: "tool", id: "A" }, { type: "text", text: "속", parent_id: "A" }, { type: "tool", id: "a1" }] }]);
  const html = renderToStaticMarkup(React.createElement(ChatProgress, { items: restored.timeline }));
  assert.ok(html.indexOf("확인할게요") < html.indexOf("야구 정보 확인 (서브에이전트) 정보 조회 완료"));
  assert.match(html, /chat-progress-nested.*속.*구장 정보 조회/s);
  assert.doesNotMatch(html, /답/);
  const [old] = restoreChatMessages([{ ...base, tools: [{ id: "c", tool_name: "get_weather", status: "completed" }] }]);
  assert.deepEqual(old.tools, [tl("c", "get_weather", "completed")]);
  assert.deepEqual(old.timeline, [{ kind: "tools", tools: [tl("c", "get_weather", "completed")] }]);
  assert.deepEqual(buildTimeline([], []), []);
});

test("detail disclosure renders only when detail is present, as plain text", () => {
  const plain = renderToStaticMarkup(React.createElement(ChatProgress, { items: toolItems([{ id: "x", toolName: "get_weather", status: "completed" }]) }));
  assert.doesNotMatch(plain, /<details|상세|args/);
  const detailed = renderToStaticMarkup(React.createElement(ChatProgress, { items: toolItems([{ id: "x", toolName: "ask_baseball", kind: "sub_agent", status: "completed",
    detail: { args: { q: "<b>잠실</b>" }, result: "결과", messages: [{ role: "ai", content: "내부" }] } }]) }));
  assert.match(detailed, /<\/summary><details class="chat-progress-detail"><summary>상세<\/summary>/);
  assert.match(detailed, /&lt;b&gt;잠실/); assert.match(detailed, /결과/); assert.match(detailed, /\[ai\] 내부/);
});

test("malformed kind/parent_id/steps are rejected by the client", async () => {
  global.fetch = async () => stream("");
  const row = { id: ASSISTANT_MSG, sequence_no: 1, role: "assistant", content: "", status: "completed", created_at: "x", updated_at: "x" };
  for (const bad of [{ tools: [{ id: "a", tool_name: "x", status: "completed", kind: "agent" }] }, { tools: [{ id: "a", tool_name: "x", status: "completed", parent_id: 3 }] }, { tools: [], steps: [{ type: "tool" }] }]) {
    global.fetch = async () => new Response(JSON.stringify([{ ...row, ...bad }]), { status: 200, headers: { "Content-Type": "application/json" } });
    await assert.rejects(fetchChatHistory("guest", SESSION), ChatClientError);
  }
});

test("a long multi-step turn streams past MAX_REPLY_LENGTH when the final answer fits", async () => {
  const pre = frame("delta", { text: "x".repeat(6000) }) + frame("delta", { text: "y".repeat(6000), parent_id: "A" });
  global.fetch = async () => stream(pre + frame("delta", { text: "답" }) + frame("done", { message_id: String(ASSISTANT_MSG), assistant_message: "답", tools: [] }));
  assert.equal((await sendChatMessage("guest", { sessionId: SESSION, content: "질문" })).reply, "답");
});

test("a sub-agent's final text step renders nested under that sub-agent from done/history steps", () => {
  const tools = [tl("A", "ask_baseball", "completed", { kind: "sub_agent" })];
  const html = renderToStaticMarkup(React.createElement(ChatProgress, { items: buildTimeline([{ type: "tool", id: "A" }, { type: "text", text: "서브 최종 답", parent_id: "A" }], tools) }));
  assert.match(html, /<ul class="chat-progress-nested"><li class="chat-progress-text"><span aria-hidden="true"><\/span><span>서브 최종 답<\/span>/);
  assert.doesNotMatch(html, /chat-answer/);
});

test("a large done frame (steps > 32k) delivered in 4KB network chunks is accepted", async () => {
  const steps = Array.from({ length: 5 }, () => ({ type: "text", text: "가".repeat(7000), parent_id: null }));
  const body = new TextEncoder().encode(frame("done", { message_id: String(ASSISTANT_MSG), assistant_message: "답", tools: [], steps }));
  global.fetch = async () => new Response(new ReadableStream({
    start(controller) { for (let i = 0; i < body.length; i += 4096) controller.enqueue(body.slice(i, i + 4096)); controller.close(); },
  }), { status: 200, headers: { "Content-Type": "text/event-stream" } });
  const reply = await sendChatMessage("guest", { sessionId: SESSION, content: "질문" });
  assert.equal(reply.reply, "답");
  assert.equal(reply.timeline[0].text.length, 35000);
});

test("sub-agent line is a default-closed details/summary with concise label; ordinary payload shows no task", () => {
  const ordinary = renderToStaticMarkup(React.createElement(ChatProgress, { items: [
    { kind: "tools", tools: [tl("A", "ask_baseball", "completed", { kind: "sub_agent" })] },
    { kind: "tools", tools: [tl("a1", "get_stadium", "failed", { parentId: "A" })] }] }));
  assert.match(ordinary, /<details class="chat-progress-disclosure"><summary>.*야구 정보 확인 \(서브에이전트\) 정보 조회 완료.*<\/summary>/s);
  assert.doesNotMatch(ordinary, /<details[^>]* open/);
  assert.match(ordinary, /구장 정보 조회 조회 실패/);
  assert.doesNotMatch(ordinary, /chat-progress-title|args|상세/);
  const admin = renderToStaticMarkup(React.createElement(ChatProgress, { items: toolItems([{ id: "A", toolName: "ask_baseball", kind: "sub_agent", status: "running", title: "잠실 경기 일정 알려줘" }]) }));
  assert.match(admin, /<summary>.*야구 정보 확인 \(서브에이전트\) 조회 중<\/span><\/summary><p class="chat-progress-title">잠실 경기 일정 알려줘/s);
});

test("running main sub-agents render only above the composer; finished ones fold into the body", () => {
  const { ChatSubAgentStatus } = require("./components/chat-progress.js");
  const tools = (a, b) => [{ kind: "tools", tools: [
    { id: "s1", toolName: "ask_baseball", status: a, kind: "sub_agent", parentId: null, summary: "두산 다음 경기 확인" },
    { id: "s2", toolName: "ask_travel_research", status: b, kind: "sub_agent", parentId: null },
    { id: "t1", toolName: "get_weather", status: "running", kind: "tool", parentId: null },
  ] }];
  const bar = items => renderToStaticMarkup(React.createElement(ChatSubAgentStatus, { items }));
  const body = items => renderToStaticMarkup(React.createElement(ChatProgress, { items, live: true }));
  const both = tools("running", "running");
  assert.match(bar(both), /role="status"/);
  assert.match(bar(both), /야구 정보 확인 · 두산 다음 경기 확인 조회 중/);
  assert.match(bar(both), /여행 정보 조사 조회 중/);
  assert.doesNotMatch(body(both), /야구 정보 확인|여행 정보 조사/);
  assert.match(body(both), /날씨 조회/);
  const one = tools("completed", "running");
  assert.doesNotMatch(bar(one), /야구/);
  assert.match(body(one), /<details[^>]*>(?!.*open)/);
  assert.match(body(one), /두산 다음 경기 확인/);
  assert.equal(bar(tools("failed", "completed")), "");
  assert.equal(bar([]), "");  // not busy / stop / session change: callers pass []
  // saved stopped/failed history (no live): unresolved running sub-agent stays folded in the body
  assert.match(renderToStaticMarkup(React.createElement(ChatProgress, { items: both })), /야구 정보 확인 \(서브에이전트\) 조회 중.*여행 정보 조사 \(서브에이전트\) 조회 중/s);
  for (const f of ["components/chat-workspace.tsx", "components/chat-popup.tsx"]) {
    const src = readFileSync(join(frontend, f), "utf8");
    assert.match(src, /<ChatProgress items=\{chat\.timeline\} live \/>/);
    assert.match(src, /<ChatProgress items=\{message\.timeline \?\? \[\]\} \/>/);
  }
});
