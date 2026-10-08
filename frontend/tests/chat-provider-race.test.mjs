import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createRequire } from "node:module";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { runInNewContext } from "node:vm";

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-chat-provider-race-"));
after(() => rmSync(scratch, { recursive: true, force: true }));
symlinkSync(join(frontend, "node_modules"), join(scratch, "node_modules"), "dir");

function compile(name) {
  const source = readFileSync(join(frontend, `${name}.ts`), "utf8");
  const { outputText } = ts.transpileModule(source, {
    fileName: `${name}.ts`, compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  });
  mkdirSync(dirname(join(scratch, `${name}.js`)), { recursive: true });
  writeFileSync(join(scratch, `${name}.js`), outputText);
}

for (const name of ["lib/chat/planning", "lib/chat/types", "lib/chat/validation", "lib/chat/history", "lib/chat/inline-urls", "lib/client-id", "lib/route-draft", "lib/stadiums", "lib/stadium-locations", "lib/community-rich-content", "lib/google-lodging", "lib/drawn-course", "lib/chat/current-course", "lib/chat/writer-state", "lib/course-directions", "lib/chat/course"]) compile(name);
mkdirSync(join(scratch, "components"), { recursive: true });

const providerSource = readFileSync(join(frontend, "components/chat-provider.tsx"), "utf8")
  .replace('from "react"', 'from "../test-react"')
  .replace('from "next/navigation"', 'from "../test-navigation"')
  .replaceAll('from "@/lib/chat/types"', 'from "../lib/chat/types"')
  .replace('from "@/lib/chat/client"', 'from "../test-chat-client"')
  .replace('from "@/lib/chat/history"', 'from "../lib/chat/history"')
  .replace('from "@/lib/chat/inline-urls"', 'from "../lib/chat/inline-urls"')
  .replace('from "@/lib/chat/course"', 'from "../lib/chat/course"')
  .replace('from "@/lib/chat/writer-state"', 'from "../lib/chat/writer-state"')
  .replace('from "@/lib/member-auth"', 'from "../test-member-auth"')
  .replace('from "@/lib/client-id"', 'from "../test-client-id"')
  .replace('from "./chat-popup"', 'from "../test-popup"');
writeFileSync(join(scratch, "components/chat-provider.js"), ts.transpileModule(providerSource, {
  fileName: "chat-provider.tsx",
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText);

writeFileSync(join(scratch, "test-react.js"), `
exports.createContext = (...args) => global.__hooks.createContext(...args);
exports.useCallback = (...args) => global.__hooks.useCallback(...args);
exports.useContext = (...args) => global.__hooks.useContext(...args);
exports.useEffect = (...args) => global.__hooks.useEffect(...args);
exports.useRef = (...args) => global.__hooks.useRef(...args);
exports.useState = (...args) => global.__hooks.useState(...args);
`);
writeFileSync(join(scratch, "test-navigation.js"), `
exports.usePathname = () => global.__path ?? "/";
exports.useRouter = () => ({ push(url) { global.__pushes?.push(url); } });
`);
writeFileSync(join(scratch, "test-member-auth.js"), `exports.useMemberAuth = () => global.__memberAuth;`);
writeFileSync(join(scratch, "test-client-id.js"), `let next = 0; exports.createClientId = () => "new-chat-" + ++next;`);
writeFileSync(join(scratch, "components/chat-planning.js"), `exports.ChatQuestions = () => null; exports.ChatWriterOffer = () => null;`);
writeFileSync(join(scratch, "test-popup.js"), `exports.ChatPopup = () => null;`);
writeFileSync(join(scratch, "test-chat-client.js"), `
class ChatClientError extends Error { constructor(message, status, uncertain = false, sessionId, code) { super(message); Object.assign(this, {status, uncertain, sessionId, code}); } }
class ChatStreamStoppedError extends ChatClientError {}
exports.ChatClientError = ChatClientError;
exports.ChatStreamStoppedError = ChatStreamStoppedError;
exports.USAGE_BUSY = "usage_busy";
exports.GUEST_STATUS = { provider: "guest", model: "guest", ready: true };
for (const name of ["deleteChatMessages", "deleteChatSession", "editChatMessage", "fetchChatHistory", "getChatStatus", "listChatSessions", "sendChatMessage", "saveAnswerFeedback", "createChatSession", "uploadChatAttachment", "deleteChatAttachment", "validateChatFile", "fetchChatUsage"])
  exports[name] = (...args) => global.__chatApi[name](...args);
`);

function hookRunner() {
  const slots = [];
  let cursor = 0;
  let pending = [];
  const same = (left, right) => left && right && left.length === right.length && left.every((value, index) => Object.is(value, right[index]));
  const hooks = {
    createContext: value => ({ value, Provider() {} }),
    useContext: context => context.value,
    useState(initial) {
      const index = cursor++;
      if (!slots[index]) slots[index] = { value: typeof initial === "function" ? initial() : initial };
      return [slots[index].value, value => { slots[index].value = typeof value === "function" ? value(slots[index].value) : value; }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!slots[index]) slots[index] = { value: { current: initial } };
      return slots[index].value;
    },
    useCallback(callback, dependencies) {
      const index = cursor++;
      if (!slots[index] || !same(slots[index].dependencies, dependencies)) slots[index] = { value: callback, dependencies };
      return slots[index].value;
    },
    useEffect(effect, dependencies) {
      const index = cursor++;
      if (!slots[index] || !same(slots[index].dependencies, dependencies)) {
        const previous = slots[index]?.cleanup;
        slots[index] = { ...slots[index], dependencies };
        pending.push(() => {
          previous?.();
          slots[index].cleanup = effect();
        });
      }
    },
  };
  global.__hooks = hooks;
  const require = createRequire(join(scratch, "entry.cjs"));
  const { ChatProvider } = require("./components/chat-provider.js");
  return {
    render() {
      global.__hooks = hooks;
      cursor = 0;
      pending = [];
      return ChatProvider({ children: null }).props.value;
    },
    unmount() { slots.forEach(slot => slot?.cleanup?.()); },
    flushEffects() {
      const effects = pending;
      pending = [];
      effects.forEach(run => run());
    },
  };
}

// Execute the real editor's DOM × handler and undo stack against the real provider.
function inlineEditor(runner) {
  class Element {
    constructor(name = "SPAN") { this.nodeName = name; this.dataset = {}; this.childNodes = []; }
    append(...children) { this.childNodes.push(...children); }
    replaceChildren(...children) { this.childNodes = children.flatMap(child => child.nodeName === "#fragment" ? child.childNodes : [child]); }
    setAttribute() {}
    focus() {}
  }
  const root = new Element("DIV"), refs = [], states = [], effects = [];
  const selectionContext = {}, selectionRef = { current: null };
  const textNode = value => ({ nodeName: "#text", nodeType: 3, textContent: value, childNodes: [] });
  let chat, refIndex, stateIndex, props;
  const source = readFileSync(join(frontend, "components/chat-inline-input.tsx"), "utf8");
  const ast = ts.createSourceFile("editor.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const body = ast.statements.filter(node => !ts.isImportDeclaration(node)).map(node => node.getText(ast)).join("\n");
  const exports = {}, require = createRequire(join(scratch, "entry.cjs"));
  runInNewContext(ts.transpileModule(body, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText, {
    exports, require, URL, HTMLElement: Element, Node: { TEXT_NODE: 3 },
    document: { createElement: name => new Element(name.toUpperCase()), createElementNS: (_, name) => new Element(name), createTextNode: textNode, createDocumentFragment: () => new Element("#fragment") },
    window: { getSelection: () => null }, useChat: () => chat, useMemberAuth: () => global.__memberAuth,
    ChatToolGroupsContext: {}, ChatToolSelectionContext: selectionContext, useContext: context => context === selectionContext ? selectionRef : [], useId: () => "tools", useLayoutEffect: effect => effects.push(effect),
    useRef: value => refs[refIndex++] ??= { current: value },
    useState: initial => { const index = stateIndex++; return [states[index] ?? initial, value => { states[index] = typeof value === "function" ? value(states[index] ?? initial) : value; }]; },
    ...require("./lib/chat/inline-urls.js"), Icon: () => null, presentationFor: () => ({ icon: "book" }),
  });
  const flatten = tree => !tree || typeof tree !== "object" ? [] : [tree, ...[tree.props?.children].flat(Infinity).flatMap(flatten)];
  function render() {
    chat = runner.render(); refIndex = stateIndex = 0;
    const tree = exports.ChatInlineInput({ id: "test", inputRef: { current: root }, disabled: false, available: true, onSend: chat.onSend, onCompositionChange() {} });
    props = flatten(tree).find(node => node.props?.role === "textbox").props;
    effects.splice(0).forEach(effect => effect());
    return chat;
  }
  return {
    render,
    remove() { root.childNodes.find(node => node.dataset?.marker !== undefined).childNodes.find(node => node.textContent === "×").onclick(); },
    undo(redo = false) { props.onKeyDown({ key: "z", metaKey: true, shiftKey: redo, nativeEvent: {}, target: root, currentTarget: root, preventDefault() {} }); },
  };
}

const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};
const tick = () => new Promise(resolve => setImmediate(resolve));
const FIRST = "3f2c1a4e-8b7d-4c21-9e0f-5a6b7c8d9e01", SECOND = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d";
const USER_MSG = 1, ASSISTANT_MSG = 2;
const OLD_MSG = 4;
const rooms = [{ id: FIRST, title: "첫 대화" }, { id: SECOND, title: "둘째 대화" }];
const row = (id, role, content, status = "completed", tools = []) => ({ id, sequence_no: id, role, content, status, tools, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" });
const unused = async () => { throw new Error("not used"); };
const baseApi = {
  deleteChatMessages: unused, deleteChatSession: unused, editChatMessage: unused, sendChatMessage: unused,
  fetchChatHistory: async () => [],
  fetchChatUsage: async () => ({ active_turn: false, can_send: true }),
  getChatStatus: async mode => ({ provider: mode === "member" ? "backend" : "guest", model: "server", ready: true }),
};

test("new course draws pins once for the current stadium and survives history reconciliation", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const course = { stadiumCode: "JAMSIL", notes: [], places: [{ name: "잠실 식당", phase: "BEFORE", category: "FOOD", lat: 37.51, lng: 127.07 }] };
  let stored = [], request, applied = 0;
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], fetchChatHistory: async () => stored,
    sendChatMessage: async (_, body) => {
      request = body;
      stored = [row(1, "user", body.content), { ...row(2, "assistant", "잠실 코스"), course }];
      return { reply: "잠실 코스", sessionId: FIRST, assistantMessageId: 2, provider: "guest", model: "test", ready: true, course };
    } };
  const runner = hookRunner(); let c = runner.render(); runner.flushEffects(); await tick(); c = runner.render();
  c.onContextChange({ stadium: "CHANGWON", intent: "route" });
  c.registerCourseTarget({ stadiumCode: "JAMSIL", stopCount: 0, apply: () => { applied++; return () => {}; } });
  c.onDraftChange("식사와 카페 코스"); c = runner.render(); c.onSend(); await tick(); c = runner.render();
  assert.equal(request.context.stadium, "JAMSIL");
  assert.equal(applied, 1);
  const answer = c.messages.find(message => message.role === "assistant");
  assert.equal(answer.course.stadiumCode, "JAMSIL");
  assert.ok(c.appliedCourses.get(answer.course)?.undo);
  runner.unmount();
});

test("a new stadium or team course moves the map and retains undo", async () => {
  for (const question of ["이번엔 롯데로 짜줘", "잠실 말고 사직으로 바꿔줘"]) {
    global.__memberAuth = { status: "anonymous", user: null };
    const course = { stadiumCode: "SAJIK", places: [], notes: [] };
    let applied, undone = false;
    global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: async () => ({ reply: "사직 코스", provider: "guest", model: "test", ready: true, course }) };
    const runner = hookRunner(); let c = runner.render(); runner.flushEffects(); await tick(); c = runner.render();
    c.registerCourseTarget({ stadiumCode: "JAMSIL", stopCount: 4, apply: (value, how) => { applied = { value, how }; return () => { undone = true; }; } });
    c.onDraftChange(question); c = runner.render(); c.onSend(); await tick(); c = runner.render(); runner.flushEffects();
    assert.equal(applied.value.stadiumCode, "SAJIK");
    assert.equal(applied.how, "replace");
    assert.match(c.appliedCourses.get(course).message, /구장을 바꾸고/);
    c.undoChatCourse(course);
    assert.equal(undone, true);
    runner.unmount();
  }
});

test("a late course preserves a newer map selection, including switching back or remounting", async () => {
  for (const change of ["other", "back", "remount", "segment"]) {
    global.__memberAuth = { status: "anonymous", user: null };
    const pending = deferred(); let applied = 0;
    global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: () => pending.promise };
    const runner = hookRunner(); let c = runner.render(); runner.flushEffects(); await tick(); c = runner.render();
    c.registerCourseTarget({ stadiumCode: "JAMSIL", stopCount: 0, apply: () => { applied++; return () => {}; } });
    c.onDraftChange("코스"); c = runner.render(); c.onSend();
    c.registerCourseTarget(change === "remount" ? null : { stadiumCode: change === "segment" ? "JAMSIL" : "GOCHEOK", selectionKey: change === "segment" ? "new segment" : undefined, stopCount: 0, apply: () => { applied++; return () => {}; } });
    if (change === "back" || change === "remount") c.registerCourseTarget({ stadiumCode: "JAMSIL", stopCount: 0, apply: () => { applied++; return () => {}; } });
    pending.resolve({ reply: "코스", provider: "guest", model: "test", ready: true,
      course: { stadiumCode: "SAJIK", places: [], notes: [] } });
    await tick(); c = runner.render();
    assert.equal(applied, 0);
    runner.unmount();
  }
});

test("send reads the authoritative course immediately and late replies check its revision without waiting for registration", async () => {
  for (const changed of [false, true]) {
    global.__memberAuth = { status: "anonymous", user: null };
    const pending = deferred(); let body, applied = 0, revision = 2;
    let latest = { stadium: "SAJIK", currentCourse: { places: ["manual cafe"], writerState: { origin: null } } };
    global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: (_, value) => { body = value; return pending.promise; } };
    const runner = hookRunner(); let chat = runner.render(); runner.flushEffects(); await tick(); chat = runner.render();
    chat.onContextChange({ stadium: "JAMSIL", currentCourse: { places: ["stale"] } });
    chat.registerCourseTarget({ stadiumCode: "JAMSIL", stopCount: 0, getContext: () => latest, getVersion: () => String(revision),
      apply: () => { applied++; return () => true; } });
    chat.onDraftChange("카페만 바꿔줘"); chat = runner.render(); chat.onSend();
    assert.deepEqual(body.context, latest);
    if (changed) { revision++; latest = { stadium: "SAJIK", currentCourse: { places: [], writerState: { origin: null } } }; }
    pending.resolve({ reply: "카페 변경", provider: "guest", model: "test", ready: true, course: { stadiumCode: "SAJIK", places: [], notes: [] } });
    await tick(); chat = runner.render();
    assert.equal(applied, changed ? 0 : 1);
    runner.unmount();
  }
});

test("a rejected stale undo is reported as preserving the newer course", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  global.__chatApi = { ...baseApi, listChatSessions: async () => [] };
  const runner = hookRunner(); let chat = runner.render(); runner.flushEffects(); await tick(); chat = runner.render();
  const course = { stadiumCode: "JAMSIL", places: [], notes: [] };
  chat.registerCourseTarget({ stadiumCode: "JAMSIL", stopCount: 1, apply: () => () => false });
  chat.applyChatCourse(course, "replace"); chat = runner.render(); runner.flushEffects();
  chat.undoChatCourse(course); chat = runner.render();
  assert.match(chat.appliedCourses.get(course).message, /최신 내용을 유지/);
  assert.equal(chat.appliedCourses.get(course).undo, null);
  runner.unmount();
});

global.window = {
  location: { pathname: "/", search: "", hash: "", origin: "http://localhost" }, scrollY: 0,
  setTimeout: (callback, delay) => { if (delay === 1200) global.__queueTimer = callback; return 1; }, clearTimeout() {}, setInterval: callback => { global.__timer = callback; return 1; }, clearInterval() { global.__timer = null; },
  addEventListener() {}, removeEventListener() {},
  scrollTo() {}, matchMedia: () => ({ matches: false }),
};
global.requestAnimationFrame = callback => { callback(); return 1; };
global.cancelAnimationFrame = () => {};
global.__memberAuth = { status: "authenticated", user: { id: 7 } };

test("attachment upload failures retry/cancel honestly; successful send clears attachments not chips and new chats scope drafts", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const uploaded = { id: FIRST, kind: "text", name: "notes.md", size: 5, contentType: "text/markdown", width: null, height: null, url: null, createdAt: "now" };
  let uploads = 0; const sends = [];
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], createChatSession: async () => rooms[0], validateChatFile: () => "text",
    uploadChatAttachment: async () => { if (++uploads === 1) throw new Error("storage unavailable"); return uploaded; },
    sendChatMessage: async (_, body) => { sends.push(body); return { reply: "answer", sessionId: FIRST, ready: true, model: "m", provider: "guest" }; } };
  const runner = hookRunner(); let c = runner.render(); runner.flushEffects(); await tick(); c = runner.render();
  c.onAttach([new File(["notes"], "notes.md")]); await tick(); c = runner.render();
  assert.equal(c.attachments[0].state, "failed"); assert.equal(c.attachments[0].error, "storage unavailable");
  c.onDraftChange("question"); c.onToolGroupsChange(["rules"]); c = runner.render(); c.onSend(); await tick(); assert.equal(sends.length, 0);
  c.onRetryAttachment(c.attachments[0].key); await tick(); c = runner.render(); assert.equal(c.attachments[0].state, "ready");
  c.onSend(); await tick(); c = runner.render(); assert.deepEqual(sends[0].attachmentIds, [FIRST]); assert.deepEqual(sends[0].toolGroupIds, ["rules"]);
  assert.deepEqual(c.attachments, []); assert.deepEqual(c.toolGroupIds, ["rules"]);
  c.onReset(); c = runner.render(); assert.equal(c.draft, ""); assert.deepEqual(c.toolGroupIds, []); assert.deepEqual(c.attachments, []); runner.unmount();
});

test("cancelled attachment ignores late upload success and restores scoped drafts after room switching", async () => {
  global.__memberAuth = { status: "anonymous", user: null }; const pendingUpload = deferred(); let signal;
  global.__chatApi = { ...baseApi, listChatSessions: async () => rooms, validateChatFile: () => "text", uploadChatAttachment: async (_mode, _session, _source, value) => { signal = value; return pendingUpload.promise; } };
  const runner = hookRunner(); let c = runner.render(); runner.flushEffects(); await tick(); c = runner.render();
  c.onAttach([new File(["notes"], "notes.txt")]); c = runner.render(); const key = c.attachments[0].key;
  c.onCancelAttachment(key); assert.equal(signal.aborted, true);
  pendingUpload.resolve({ id: FIRST, kind: "text", name: "notes.txt", size: 5 }); await tick(); c = runner.render();
  assert.equal(c.attachments[0].state, "cancelled"); assert.equal(c.attachments[0].attachment, undefined);
  c.onDraftChange("room one draft"); c.onToolGroupsChange(["weather"]); c = runner.render();
  c.onSelectConversation(`guest:${SECOND}`); await tick(); c = runner.render(); assert.deepEqual(c.attachments, []); assert.equal(c.draft, "");
  c.onSelectConversation(`guest:${FIRST}`); c = runner.render(); assert.equal(c.draft, "room one draft"); assert.deepEqual(c.toolGroupIds, ["weather"]); assert.equal(c.attachments[0].state, "cancelled"); runner.unmount();
});

test("provider ignores delayed list/history callbacks and reloads an interrupted room", async () => {
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
  const lateList = deferred();
  global.__chatApi = { ...baseApi, listChatSessions: () => lateList.promise };
  let runner = hookRunner();
  let controls = runner.render();
  runner.flushEffects();
  controls.onDraftChange("작성 중");
  lateList.resolve(rooms);
  await tick();
  controls = runner.render();
  assert.equal(controls.activeConversationId, "initial-chat");
  assert.equal(controls.draft, "작성 중");
  assert.deepEqual(controls.conversations, [{ id: "initial-chat", title: "새 대화" }]);

  const firstHistory = deferred(), secondHistory = deferred();
  const historyCalls = [];
  global.__chatApi = {
    ...baseApi,
    listChatSessions: async () => rooms,
    fetchChatHistory: (mode, sessionId) => {
      historyCalls.push([mode, sessionId]);
      if (sessionId === FIRST && historyCalls.filter(([, id]) => id === FIRST).length === 1) return firstHistory.promise;
      if (sessionId === SECOND) return secondHistory.promise;
      return Promise.resolve([]);
    },
  };
  runner = hookRunner();
  controls = runner.render();
  runner.flushEffects();
  await tick();
  controls = runner.render();
  assert.equal(controls.activeConversationId, `member:${FIRST}`);

  controls.onSelectConversation(`member:${SECOND}`);
  controls = runner.render();
  controls.onDraftChange("둘째 방 초안");
  firstHistory.resolve([row(OLD_MSG, "user", "늦은 첫 기록")]);
  await tick();
  controls = runner.render();
  assert.equal(controls.activeConversationId, `member:${SECOND}`);
  assert.equal(controls.draft, "둘째 방 초안");

  secondHistory.resolve([row(USER_MSG, "user", "둘째 질문"), row(ASSISTANT_MSG, "assistant", "둘째 답변")]);
  await tick();
  controls = runner.render();
  assert.deepEqual(controls.messages.map(message => [message.id, message.role, message.content]), [[USER_MSG, "user", "둘째 질문"], [ASSISTANT_MSG, "assistant", "둘째 답변"]]);
  assert.equal(controls.draft, "둘째 방 초안");

  controls.onSelectConversation(`member:${FIRST}`);
  assert.deepEqual(historyCalls, [["member", FIRST], ["member", SECOND], ["member", FIRST]]);
});

test("feedback updates shared history, rejects duplicate writes and ignores an old identity", async () => {
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
  let pendingVote = deferred();
  const calls = [];
  global.__chatApi = {
    ...baseApi,
    listChatSessions: async () => [rooms[0]],
    fetchChatHistory: async () => [row(USER_MSG, "user", "질문"), row(ASSISTANT_MSG, "assistant", "답변")],
    saveAnswerFeedback: (...args) => { calls.push(args); return pendingVote.promise; },
  };
  const runner = hookRunner();
  let controls = runner.render();
  runner.flushEffects();
  await tick();
  controls = runner.render();
  const up = { rating: "up", reason: "", comment: "" };
  const saving = controls.onFeedback(ASSISTANT_MSG, up);
  await assert.rejects(controls.onFeedback(ASSISTANT_MSG, up));
  pendingVote.resolve(up);
  await saving;
  controls = runner.render();
  assert.deepEqual(controls.messages.find(message => message.id === ASSISTANT_MSG).feedback, up);
  assert.deepEqual(calls, [["member", FIRST, ASSISTANT_MSG, up]]);
  pendingVote = deferred();
  const cancelling = controls.onFeedback(ASSISTANT_MSG, null);
  global.__memberAuth = { status: "anonymous", user: null };
  controls = runner.render();
  runner.flushEffects();
  pendingVote.resolve(null);
  await cancelling;
  await tick();
  controls = runner.render();
  assert.ok(controls.messages.every(message => !message.feedback));
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
});

test("colliding answer IDs vote independently across conversations and account changes", async () => {
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
  const votes = [];
  global.__chatApi = {
    ...baseApi, listChatSessions: async () => rooms,
    fetchChatHistory: async () => [row(1, "user", "question"), row(2, "assistant", "answer")],
    saveAnswerFeedback: (...args) => { const pending = deferred(); votes.push({ args, pending }); return pending.promise; },
  };
  const runner = hookRunner();
  let controls = runner.render(); runner.flushEffects(); await tick(); controls = runner.render();
  const up = { rating: "up", reason: "", comment: "" };
  const first = controls.onFeedback(2, up);
  controls.onSelectConversation(`member:${SECOND}`); await tick(); controls = runner.render();
  const second = controls.onFeedback(2, up);
  assert.equal(votes.length, 2);
  votes[0].pending.resolve(up); await first;
  await assert.rejects(controls.onFeedback(2, up));
  global.__memberAuth = { status: "authenticated", user: { id: 8 } };
  controls = runner.render(); runner.flushEffects(); await tick(); controls = runner.render();
  controls.onSelectConversation(`member:${SECOND}`); await tick(); controls = runner.render();
  const newOwner = controls.onFeedback(2, up);
  assert.equal(votes.length, 3);
  votes[1].pending.resolve(up); await second;
  controls = runner.render();
  assert.ok(controls.messages.every(message => !message.feedback));
  await assert.rejects(controls.onFeedback(2, up));
  votes[2].pending.resolve(up); await newOwner;
});

test("assistant deletion removes only its answer and reloads a truthful question tombstone", async () => {
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
  let rows = [row(1, "user", "question"), row(2, "assistant", "answer"), row(3, "user", "later"), row(4, "assistant", "later answer")];
  const confirmations = [];
  window.confirm = text => { confirmations.push(text); return true; };
  global.__chatApi = {
    ...baseApi, listChatSessions: async () => [rooms[0]], fetchChatHistory: async () => rows,
    deleteChatMessages: async (_mode, _session, id) => { rows = id === 2 ? [{ ...rows[0], answer_deleted: true }, ...rows.slice(2)] : rows.slice(0, rows.findIndex(item => item.id === id)); },
  };
  const runner = hookRunner();
  let controls = runner.render(); runner.flushEffects(); await tick(); controls = runner.render();
  controls.onDeleteMessage(2); await tick(); controls = runner.render();
  assert.deepEqual(controls.messages.map(message => message.id), [1, 3, 4]);
  assert.equal(controls.messages[0].answerDeleted, true);
  assert.match(confirmations[0], /이 답변만/);
  assert.match(confirmations[0], /질문과 다른 대화는 그대로/);
  controls.onDeleteMessage(3); await tick(); controls = runner.render();
  assert.deepEqual(controls.messages.map(message => message.id), [1]);
  assert.match(confirmations[1], /이 질문과 이후 대화를 모두/);
});

test("guest reload lists cookie-owned sessions and restores history in guest mode", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const calls = [];
  global.__chatApi = {
    ...baseApi,
    listChatSessions: async mode => { calls.push(["list", mode]); return [rooms[0]]; },
    fetchChatHistory: async (mode, sessionId) => { calls.push(["history", mode, sessionId]); return [row(USER_MSG, "user", "비회원 질문"), row(ASSISTANT_MSG, "assistant", "비회원 답")]; },
  };
  const runner = hookRunner();
  let controls = runner.render();
  runner.flushEffects();
  await tick();
  controls = runner.render();
  assert.equal(controls.activeConversationId, `guest:${FIRST}`);
  assert.deepEqual(controls.messages.map(message => message.content), ["비회원 질문", "비회원 답"]);
  assert.ok(calls.some(call => call[0] === "list" && call[1] === "guest"));
  assert.deepEqual(calls.filter(call => call[0] === "history"), [["history", "guest", FIRST]]);
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
});

test("history reload surfaces a stopped turn's status without dropping it", async () => {
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
  global.__chatApi = {
    ...baseApi,
    listChatSessions: async () => [rooms[0]],
    fetchChatHistory: async () => [row(USER_MSG, "user", "질문", "stopped"), row(ASSISTANT_MSG, "assistant", "", "stopped")],
  };
  const runner = hookRunner();
  let controls = runner.render();
  runner.flushEffects();
  await tick();
  controls = runner.render();
  assert.deepEqual(controls.messages.map(message => [message.id, message.status]), [[USER_MSG, "stopped"], [ASSISTANT_MSG, "stopped"]]);
});

test("live delta/tool events keep SSE order, update tools in place, and clear once the turn settles", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  let onTool, onDelta, release;
  const persisted = [row(USER_MSG, "user", "잠실 맛집 알려 주세요"), row(ASSISTANT_MSG, "assistant", "답변", "completed", [{ id: "call-1", tool_name: "search_places", status: "completed" }])];
  global.__chatApi = {
    ...baseApi,
    listChatSessions: async () => [],
    fetchChatHistory: async () => persisted,
    sendChatMessage: (mode, body, signal, callbacks) => { ({ onTool, onDelta } = callbacks); return new Promise(resolve => {
      onDelta("찾아"); onDelta("볼게요");
      onTool({ id: "call-1", tool_name: "search_places", status: "running" });
      onTool({ id: "call-1", tool_name: "search_places", status: "completed" });
      onDelta(" 부분");
      release = () => resolve({ reply: "답변", sessionId: FIRST, provider: "guest", model: "m", ready: true, assistantMessageId: ASSISTANT_MSG, tools: [{ id: "call-1", toolName: "search_places", status: "completed", kind: "tool", parentId: null }] });
    }); },
  };
  const runner = hookRunner();
  let controls = runner.render();
  runner.flushEffects();
  await tick();
  controls = runner.render();
  controls.onDraftChange("잠실 맛집 알려 주세요");
  controls = runner.render();
  controls.onSend();
  await tick();
  controls = runner.render();
  assert.deepEqual(controls.timeline, [
    { kind: "text", text: "찾아볼게요", parentId: null },
    { kind: "tools", tools: [{ id: "call-1", toolName: "search_places", status: "completed", kind: "tool", parentId: null }] },
    { kind: "text", text: " 부분", parentId: null },
  ]);
  release();
  await tick();
  controls = runner.render();
  assert.deepEqual(controls.timeline, []);
  assert.equal(controls.messages.at(-1).content, "답변");
  await tick();
  controls = runner.render();
  assert.deepEqual(controls.messages.at(-1).tools, [{ id: "call-1", toolName: "search_places", status: "completed", kind: "tool", parentId: null }]);
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
});

test("Stop aborts only the local stream, clears loading and shows the unsaved notice", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const sends = [];
  global.__chatApi = {
    ...baseApi,
    listChatSessions: async () => [],
    sendChatMessage: (mode, body, signal) => {
      sends.push([mode, body.content]);
      return new Promise((_, reject) => signal.addEventListener("abort", () => reject(new Error("요청이 중단됐어요.")), { once: true }));
    },
  };
  const runner = hookRunner();
  let controls = runner.render();
  runner.flushEffects();
  await tick();
  controls = runner.render();
  controls.onDraftChange("잠실 맛집 알려 주세요");
  controls = runner.render();
  controls.onSend();
  controls = runner.render();
  assert.equal(controls.pending, "잠실 맛집 알려 주세요");
  controls.onCancel();
  await tick();
  controls = runner.render();
  assert.equal(controls.pending, "");
  assert.equal(controls.failed, "");
  assert.equal(controls.error, "");
  assert.equal(controls.notice, "답변 받기를 중단했어요. 받던 답변은 저장되지 않아요.");
  assert.equal(controls.draft, "잠실 맛집 알려 주세요");
  assert.deepEqual(sends, [["guest", "잠실 맛집 알려 주세요"]]);
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
});

// Stop clears `pending` inside its own click, so React reuses that <button> node as the submit/send button.
// Without preventDefault the browser's default activation then re-submits the restored draft (seen in the real UI).
for (const [path, exportName] of [["components/chat-workspace", "ChatWorkspace"], ["components/chat-popup", "ChatPopup"]]) {
  test(`${exportName} stop button cancels without the default submit activation`, () => {
    const source = readFileSync(join(frontend, `${path}.tsx`), "utf8")
      .replace(/^import "@\/styles\/[^"]+";$/m, "")
      .replace('from "react"', 'from "../test-surface-react"')
      .replace('from "next/link"', 'from "../test-surface-stub"')
      .replace('from "next/image"', 'from "../test-surface-stub"')
      .replace('from "@/lib/chat/types"', 'from "../lib/chat/types"')
      .replace('from "@/lib/chat/inline-urls"', 'from "../lib/chat/inline-urls"')
      .replace('from "@/lib/member-auth"', 'from "../test-member-auth"')
      .replace(/from "\.\/(chat-provider|icons|chat-answer|chat-course-card|chat-pending|chat-progress|chat-usage|chat-feedback|chat-course-preferences|chat-queue|chat-composer-tools|chat-inline-input)"/g, 'from "../test-surface-stub"');
    writeFileSync(join(scratch, `${path}.js`), ts.transpileModule(source, {
      fileName: `${path}.tsx`, compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
    }).outputText);
    writeFileSync(join(scratch, "test-surface-react.js"), "exports.useEffect = () => {}; exports.useRef = current => ({ current }); exports.useState = value => [value, () => {}];");
    writeFileSync(join(scratch, "test-surface-stub.js"), "const Stub = () => null; module.exports = new Proxy({ __esModule: true, default: Stub, useChat: () => global.__chat }, { get: (target, key) => key in target ? target[key] : Stub });");
    global.__memberAuth = { status: "anonymous", user: null };
    let cancelled = 0;
    global.__chat = new Proxy({
      messages: [], conversations: [{ id: "initial-chat", title: "새 대화" }], activeConversationId: "initial-chat",
      attachments: [], toolGroupIds: [], draft: "잠실 맛집 알려 주세요", pending: "잠실 맛집 알려 주세요", streaming: "", timeline: [], failed: "", error: "", notice: "",
      editingMessageId: null, status: { provider: "guest", model: "m", ready: true }, statusLoading: false, statusError: "",
      queued: [], editingQueuedId: null,
      onCancel: () => { cancelled += 1; },
    }, { get: (target, key) => key in target ? target[key] : () => {} });
    const require = createRequire(join(scratch, "entry.cjs"));
    const Surface = require(`./${path}.js`)[exportName];
    const find = node => {
      if (!node || typeof node !== "object") return null;
      if (Array.isArray(node)) { for (const child of node) { const hit = find(child); if (hit) return hit; } return null; }
      if (node.props?.["aria-label"] === "답변 생성 중단") return node;
      return find(node.props?.children) ?? find(node.props?.actions);
    };
    const stop = find(Surface({}));
    assert.ok(stop, "stop button renders while a reply is pending");
    let prevented = false;
    stop.props.onClick({ preventDefault: () => { prevented = true; } });
    assert.equal(cancelled, 1);
    assert.equal(prevented, true);
    global.__chat.pending = "";
    global.__chat.messages = [{ id: 1, role: "user", content: "question", status: "completed" }, { id: 2, role: "assistant", content: "answer", status: "completed" }];
    const actions = [];
    const feedback = [];
    const collect = node => {
      if (!node || typeof node !== "object") return;
      if (Array.isArray(node)) { node.forEach(collect); return; }
      if (["이 질문 수정", "이 질문부터 삭제", "이 답변만 삭제"].includes(node.props?.["aria-label"])) actions.push(node);
      if (node.type === require("./test-surface-stub.js").ChatFeedback && node.props?.message?.role === "assistant") feedback.push(node);
      collect(node.props?.children);
    };
    collect(Surface({}));
    assert.deepEqual(actions.map(node => node.props["aria-label"]), ["이 질문 수정", "이 질문부터 삭제"]);
    assert.equal(feedback.length, 1, "assistant actions are delegated to one shared feedback toolbar");
    assert.equal(feedback[0].props.disabled, false);
    for (const action of actions) {
      assert.equal(action.props.type, "button");
      assert.equal(action.props.children.type, "svg");
      assert.equal(action.props.children.props["aria-hidden"], "true");
      assert.equal(action.props.children.props.width, "16");
    }
    global.__chat.draft = "";
    global.__chat.attachments = [{ key: "url", kind: "url", state: "ready", sourceUrl: "https://example.com/#part", attachment: { id: FIRST, url: "https://example.com/" } }];
    const readyActions = [];
    const findReady = node => {
      if (!node || typeof node !== "object") return;
      if (Array.isArray(node)) { node.forEach(findReady); return; }
      if (node.props?.["aria-label"] === "질문 보내기") readyActions.push(node);
      findReady(node.props?.children); findReady(node.props?.actions);
    };
    findReady(Surface({}));
    assert.equal(readyActions[0].props.disabled, false);
    global.__memberAuth = { status: "authenticated", user: { id: 7 } };
  });
}

test("uncertain failure whose turn history shows completed clears failure state and only the auto-restored draft", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  for (const newer of [null, "새 질문", "잠실 맛집"]) {
    const history = deferred();
    global.__chatApi = {
      ...baseApi,
      listChatSessions: async () => [],
      fetchChatHistory: () => history.promise,
      sendChatMessage: async () => { const { ChatClientError } = createRequire(join(scratch, "entry.cjs"))("./test-chat-client.js"); const error = new ChatClientError("연결이 끊겼어요."); error.sessionId = FIRST; throw error; },
    };
    const runner = hookRunner();
    let controls = runner.render();
    runner.flushEffects();
    await tick();
    controls = runner.render();
    controls.onDraftChange("잠실 맛집");
    controls = runner.render();
    controls.onSend();
    await tick();
    controls = runner.render();
    assert.equal(controls.failed, "잠실 맛집");
    assert.equal(controls.draft, "잠실 맛집");
    if (newer) { controls.onDraftChange(newer); controls = runner.render(); }
    history.resolve([row(USER_MSG, "user", "잠실 맛집"), row(ASSISTANT_MSG, "assistant", "답변")]);
    await tick();
    controls = runner.render();
    assert.deepEqual([controls.failed, controls.error, controls.notice, controls.draft], ["", "", "", newer ?? ""]);
    assert.equal(controls.messages.at(-1).content, "답변");
  }
  global.__memberAuth = { status: "authenticated", user: { id: 7 } };
});


test("fresh planning offer navigates once, preserves request/conversation and cancels on stay, typing, send, route and room changes", async () => {
  const payload = { offer_writer: true, questions: [{ question: "동행", choices: ["혼자", "친구"] }] };
  const realNow = Date.now;
  let now = 1000;
  Date.now = () => now;
  try {
    for (const action of ["auto", "immediate", "stay", "typing", "send", "route", "switch", "unmount", "close", "writer", "history"]) {
      global.__pushes = [];
      global.__path = action === "writer" ? "/routes/new" : "/chat";
      global.__memberAuth = { status: "anonymous", user: null };
      let calls = 0;
      global.__chatApi = { ...baseApi,
        listChatSessions: async () => action === "history" ? [rooms[0]] : [],
        fetchChatHistory: async () => action === "history" ? [{ ...row(2, "assistant", "조건을 알려주세요"), planning: payload }] : [],
        sendChatMessage: async (mode, body, signal, callbacks) => {
          calls++; callbacks.onPlanning(payload);
          return { reply: "조건을 알려주세요", assistantMessageId: 2, planning: payload, ready: true, model: "test", provider: "guest" };
        },
      };
      const runner = hookRunner();
      let chat = runner.render(); runner.flushEffects(); await tick(); chat = runner.render(); runner.flushEffects();
      if (action !== "history") { chat.onDraftChange("야구 여행 계획 짜줘"); chat = runner.render(); chat.onSend(); await tick(); chat = runner.render(); runner.flushEffects(); }
      assert.equal(chat.writerSeconds, ["writer", "history"].includes(action) ? null : 20);
      const originalId = chat.activeConversationId;
      const announcement = chat.writerAnnouncement;
      if (chat.writerSeconds !== null) {
        assert.match(announcement, /20초 후 루트 작성 화면/);
        assert.match(announcement, /자동 이동을 취소/);
        now += 1000; global.__timer?.(); chat = runner.render();
        assert.equal(chat.writerSeconds, 19);
        assert.equal(chat.writerAnnouncement, announcement);
        assert.deepEqual(global.__pushes, []);
      }
      if (action === "immediate") { chat.goToWriter(); chat.goToWriter(); }
      if (action === "stay") chat.stayHere(true);
      if (action === "typing") chat.onDraftChange("새 초안");
      if (action === "send") chat.onSend();
      if (action === "route") { global.__path = "/routes"; chat = runner.render(); runner.flushEffects(); }
      if (action === "switch") { chat.onReset(); chat = runner.render(); runner.flushEffects(); }
      if (action === "unmount") runner.unmount();
      if (action === "close") chat.onClosePopup();
      now += 19000; global.__timer?.(); chat = runner.render();
      assert.deepEqual(global.__pushes, ["auto", "immediate"].includes(action) ? ["/routes/new"] : []);
      if (["auto", "immediate"].includes(action)) {
        assert.equal(chat.activeConversationId, originalId);
        assert.equal(chat.messages[0].content, "야구 여행 계획 짜줘");
        assert.equal(calls, 1);
      }
      if (action === "typing") assert.equal(chat.draft, "새 초안");
      if (action === "stay") assert.equal(chat.writerAnnouncement, "자동 이동을 취소했어요. 여기서 대화를 이어가요.");
      else assert.doesNotMatch(chat.writerAnnouncement, /자동 이동을 취소했어요/);
      chat.stayHere();
    }
  } finally { Date.now = realNow; global.__path = "/"; }
});


test("question group uses existing user flow once and leaves subsequent history groups disabled", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const payload = { offer_writer: false, questions: [{ question: "동행", choices: ["혼자", "친구"] }] };
  const calls = [];
  global.__chatApi = { ...baseApi, listChatSessions: async () => [rooms[0]],
    fetchChatHistory: async () => [{ ...row(2, "assistant", "조건"), planning: payload }],
    sendChatMessage: async (_, body) => { calls.push(body.content); return { reply: "계획", assistantMessageId: 4, ready: true, model: "test", provider: "guest" }; },
  };
  const runner = hookRunner(); let chat = runner.render(); runner.flushEffects(); await tick(); chat = runner.render(); runner.flushEffects();
  chat.submitQuestions(2, "동행: 친구"); chat.submitQuestions(2, "동행: 혼자"); await tick(); chat = runner.render(); chat.submitQuestions(2, "동행: 혼자");
  assert.deepEqual(calls, ["동행: 친구"]);
});

test("new identity receives its first live planning offer", async () => {
  const payload = { offer_writer: true, questions: [] };
  global.__memberAuth = { status: "authenticated", user: { id: 9002 } };
  global.__path = "/chat"; global.__pushes = [];
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: async (_, body, signal, cb) => {cb.onPlanning(payload); return {reply:"plan",assistantMessageId:2,planning:payload,ready:true,model:"test",provider:"guest"};} };
  const runner = hookRunner(); let c=runner.render();runner.flushEffects();await tick();c=runner.render();runner.flushEffects();
  c.onDraftChange("member plan");c=runner.render();c.onSend();await tick();c=runner.render();runner.flushEffects();assert.equal(c.writerSeconds,20);c.stayHere();
  global.__memberAuth={status:"anonymous",user:null};
  c=runner.render();runner.flushEffects();await tick();c=runner.render();runner.flushEffects();await tick();c=runner.render();runner.flushEffects();
  assert.equal(c.activeConversationId,"initial-chat");
  c.onDraftChange("guest first plan");c=runner.render();c.onSend();await tick();c=runner.render();runner.flushEffects();
  try {assert.equal(c.writerSeconds,20);} finally {runner.unmount();}
});

test("stop and stream error cancel existing live offer", async () => {
  const payload = {offer_writer:true,questions:[]};
  for(const action of ["stop","error"]){
    global.__memberAuth={status:"anonymous",user:null};global.__path="/chat";global.__pushes=[];
    let rejectSend;
    global.__chatApi={...baseApi,listChatSessions:async()=>[],sendChatMessage:async(_,body,signal,cb)=>{cb.onPlanning(payload);return new Promise((resolve,reject)=>{rejectSend=reject;signal.addEventListener("abort",()=>reject(new Error("aborted")),{once:true});});}};
    const runner=hookRunner();let c=runner.render();runner.flushEffects();await tick();c=runner.render();runner.flushEffects();c.onDraftChange("plan");c=runner.render();c.onSend();await tick();c=runner.render();runner.flushEffects();assert.equal(c.writerSeconds,20);
    if(action==="stop")c.onCancel();else rejectSend(new Error("local failure"));
    await tick();c=runner.render();runner.flushEffects();assert.equal(c.writerSeconds,null);assert.deepEqual(global.__pushes,[]);runner.unmount();
  }
});

test("review repro: deleting follow-up leaves question group permanently rejected", async () => {
 global.__memberAuth={status:"anonymous",user:null};
 const payload={offer_writer:false,questions:[{question:"동행",choices:["혼자","친구"]}]};
 const original=[{...row(2,"assistant","조건"),planning:payload}];
 let stored=original, sends=0;
 global.window.confirm=()=>true;
 global.__chatApi={...baseApi,listChatSessions:async()=>[rooms[0]],fetchChatHistory:async()=>stored,
 sendChatMessage:async()=>{sends++;stored=[...original,row(3,"user","동행: 친구"),row(4,"assistant","계획")];return {reply:"계획",sessionId:FIRST,assistantMessageId:4,ready:true,model:"test",provider:"guest"};},
 deleteChatMessages:async()=>{stored=original;}};
 const runner=hookRunner();let c=runner.render();runner.flushEffects();await tick();c=runner.render();
 c.submitQuestions(2,"동행: 친구");await tick();c=runner.render();
 assert.equal(c.messages.at(-1).id,4);
 c.onDeleteMessage(3);await tick();c=runner.render();
 assert.equal(c.messages.at(-1).id,2);
 c.submitQuestions(2,"동행: 혼자");await tick();c=runner.render();
 assert.equal(sends,2);assert.equal(c.pending,"");
 runner.unmount();
});
test("review repro: failed request cannot change and resend question answers",async()=>{
 global.__memberAuth={status:"anonymous",user:null};
 const payload={offer_writer:false,questions:[{question:"동행",choices:["혼자","친구"]}]};
 let sends=0;
 global.__chatApi={...baseApi,listChatSessions:async()=>[rooms[0]],fetchChatHistory:async()=>[{...row(2,"assistant","조건"),planning:payload}],sendChatMessage:async()=>{sends++;throw new Error("offline");}};
 const runner=hookRunner();let c=runner.render();runner.flushEffects();await tick();c=runner.render();
 c.submitQuestions(2,"동행: 친구");await tick();c=runner.render();assert.equal(c.error,"offline");
 c.submitQuestions(2,"동행: 혼자");await tick();c=runner.render();assert.equal(sends,2);
 runner.unmount();
});

test("planning preserves a preexisting equal composer after completed history reconciliation", async () => {
 global.__memberAuth={status:"anonymous",user:null};
 const payload={offer_writer:false,questions:[{question:"동행",choices:["혼자","친구"]}]};
 const original=[{...row(2,"assistant","조건"),planning:payload}]; let calls=0;
 global.__chatApi={...baseApi,listChatSessions:async()=>[rooms[0]],fetchChatHistory:async()=>++calls===1?original:[...original,row(3,"user","동행: 친구"),row(4,"assistant","계획")],sendChatMessage:async()=>{ const {ChatClientError}=createRequire(join(scratch,"entry.cjs"))("./test-chat-client.js"); const e=new ChatClientError("offline");e.sessionId=FIRST;throw e; }};
 const runner=hookRunner();let c=runner.render();runner.flushEffects();await tick();c=runner.render();c.onDraftChange("동행: 친구");c=runner.render();
 assert.equal(await c.submitQuestions(2,"동행: 친구"),"failed");await tick();c=runner.render();assert.equal(c.draft,"동행: 친구");assert.equal(c.failed,"");runner.unmount();
});
test("planning rejects invalid, loading, identity and concurrent submissions truthfully", async()=>{
 global.__memberAuth={status:"anonymous",user:null}; const load=deferred(), send=deferred();let calls=0;
 const payload={offer_writer:false,questions:[{question:"동행",choices:["혼자","친구"]}]};
 global.__chatApi={...baseApi,listChatSessions:async()=>[rooms[0]],fetchChatHistory:()=>load.promise,sendChatMessage:()=>{calls++;return send.promise;}};
 const runner=hookRunner();let c=runner.render();runner.flushEffects();await tick();c=runner.render();assert.equal(await c.submitQuestions(2,"답"),"rejected");
 load.resolve([{...row(2,"assistant","조건"),planning:payload}]);await tick();c=runner.render();
 for(const [id,text] of [[99,"답"],[2," "],[2,"x".repeat(2001)]])assert.equal(await c.submitQuestions(id,text),"rejected");
 let acceptedCount=0;
 const accepted=c.submitQuestions(2,"동행: 친구",()=>acceptedCount++);assert.equal(acceptedCount,1);assert.equal(await c.submitQuestions(2,"동행: 혼자",()=>acceptedCount++),"rejected");assert.equal(acceptedCount,1);assert.equal(calls,1);
 send.resolve({reply:"계획",assistantMessageId:4,ready:true,model:"test",provider:"guest"});assert.equal(await accepted,"succeeded");
 global.__memberAuth={status:"authenticated",user:{id:88}};runner.render();runner.flushEffects();assert.equal(await c.submitQuestions(2,"동행: 혼자"),"rejected");runner.unmount();
});
test("saved failed planning answer retries via edit without duplicate user history",async()=>{
 global.__memberAuth={status:"anonymous",user:null};global.window.confirm=()=>true;
 const payload={offer_writer:false,questions:[{question:"동행",choices:["혼자","친구"]}]};const original=[{...row(2,"assistant","조건"),planning:payload}];let stored=original,sends=0,edits=0;
 global.__chatApi={...baseApi,listChatSessions:async()=>[rooms[0]],fetchChatHistory:async()=>stored,sendChatMessage:async()=>{sends++;stored=[...original,row(3,"user","동행: 친구","failed")];throw new Error("offline");},editChatMessage:async(_,body)=>{edits++;assert.equal(body.messageId,3);stored=[...original,row(3,"user",body.content),row(4,"assistant","계획")];return {reply:"계획",sessionId:FIRST,assistantMessageId:4,ready:true,model:"test",provider:"guest"};}};
 const runner=hookRunner();let c=runner.render();runner.flushEffects();await tick();c=runner.render();assert.equal(await c.submitQuestions(2,"동행: 친구"),"failed");await tick();c=runner.render();assert.equal(await c.submitQuestions(2,"동행: 혼자"),"rejected");c.onRetry();await tick();c=runner.render();assert.deepEqual([sends,edits],[1,1]);assert.equal(c.messages.filter(m=>m.role==="user").length,1);runner.unmount();
});

test("edit restores the composer bundle and cancels only replaced upload scopes", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  window.confirm = () => true;
  const uploads = [], sent = [];
  const uploaded = { id: FIRST, kind: "text", name: "notes.md", size: 5 };
  global.__chatApi = { ...baseApi, listChatSessions: async () => rooms,
    fetchChatHistory: async () => [row(1, "user", "old question"), row(2, "assistant", "answer"), row(3, "user", "other question")],
    validateChatFile: () => "text", uploadChatAttachment: (_mode, _session, _source, signal) => { const pending = deferred(); uploads.push({ pending, signal }); return pending.promise; },
    editChatMessage: async (_, body) => { sent.push(body); return { reply: "ok", ready: true, provider: "guest", model: "m" }; },
  };
  const runner = hookRunner(); let c = runner.render(); runner.flushEffects(); await tick(); c = runner.render();
  c.onDraftChange("unsent draft"); c.onToolGroupsChange(["weather"]); c.onContextChange({ intent: "course" });
  c.onAttach([new File(["notes"], "notes.md")]); await tick(); c = runner.render();
  const key = c.attachments[0].key;
  c.onEditMessage(1); c = runner.render(); assert.equal(uploads[0].signal.aborted, true);
  c.onAttach([new File(["edit"], "edit.md")]); await tick(); c = runner.render();
  c.onEditMessage(3); c = runner.render(); assert.equal(uploads[1].signal.aborted, true);
  c.onCancelEdit(); c = runner.render();
  assert.equal(c.draft, "unsent draft"); assert.deepEqual(c.toolGroupIds, ["weather"]); assert.deepEqual(c.context, { intent: "course" });
  assert.equal(c.attachments[0].key, key); assert.equal(c.attachments[0].state, "cancelled");
  uploads[0].pending.resolve(uploaded); uploads[1].pending.resolve(uploaded); await tick(); c = runner.render();
  assert.equal(c.attachments[0].state, "cancelled"); assert.equal(c.attachments[0].attachment, undefined);
  c.onRetryAttachment(key); await tick(); uploads[2].pending.resolve(uploaded); await tick(); c = runner.render();
  assert.equal(c.attachments[0].state, "ready");
  c.onEditMessage(1); c = runner.render(); c.onDraftChange("edited question"); c = runner.render(); c.onSend(); await tick(); c = runner.render();
  assert.equal(sent[0].content, "edited question"); assert.deepEqual(sent[0].attachmentIds, []);
  assert.equal(c.draft, "unsent draft"); assert.equal(c.attachments[0].attachment.id, FIRST); assert.deepEqual(c.toolGroupIds, ["weather"]);
  c.onEditMessage(1); c = runner.render(); c.onSelectConversation(`guest:${SECOND}`); await tick(); c = runner.render();
  c.onSelectConversation(`guest:${FIRST}`); c = runner.render();
  assert.equal(c.draft, "unsent draft"); assert.equal(c.editingMessageId, null); assert.equal(c.attachments[0].state, "ready");
  c.onCancelEdit(); c = runner.render(); assert.equal(c.draft, "unsent draft"); runner.unmount();
});

for (const edited of [false, true]) for (const result of ["success", "failure", "stop"]) {
  test(`retry preserves newer composer bundle (${edited ? "edit" : "send"}, ${result})`, async () => {
    global.__memberAuth = { status: "anonymous", user: null }; window.confirm = () => true;
    const uploaded = { id: SECOND, kind: "text", name: "new.md", size: 3 };
    let calls = 0, finish;
    const bodies = [];
    const request = async (_, body, signal) => {
      bodies.push(body);
      if (++calls === 1) throw new Error("offline");
      return new Promise((resolve, reject) => { finish = () => result === "failure" ? reject(new Error("still offline")) : resolve({ reply: "ok", ready: true, provider: "guest", model: "m" }); signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true }); });
    };
    global.__chatApi = { ...baseApi, listChatSessions: async () => [rooms[0]], fetchChatHistory: async () => [row(1, "user", "old question"), row(2, "assistant", "answer")],
      sendChatMessage: request, editChatMessage: request, validateChatFile: () => "text", uploadChatAttachment: async () => uploaded };
    const runner = hookRunner(); let c = runner.render(); runner.flushEffects(); await tick(); c = runner.render();
    if (edited) { c.onEditMessage(1); c = runner.render(); }
    c.onDraftChange("failed question"); c = runner.render(); c.onSend(); await tick(); c = runner.render();
    c.onDraftChange("new unsent draft"); c.onToolGroupsChange(["rules"]); c.onAttach([new File(["new"], "new.md")]); await tick(); c = runner.render();
    const bundle = c.attachments;
    c.onRetry(); await tick(); c = runner.render();
    assert.equal(c.draft, "new unsent draft");
    if (result === "stop") c.onCancel(); else finish();
    await tick(); c = runner.render();
    assert.equal(c.draft, "new unsent draft"); assert.deepEqual(c.attachments, bundle); assert.deepEqual(c.toolGroupIds, ["rules"]);
    assert.equal(bodies[1].content, "failed question"); assert.deepEqual(bodies[1].attachmentIds, []); assert.deepEqual(bodies[1].toolGroupIds, []);
    runner.unmount();
  });
}

for (const change of ["same text", "chips", "attachment", "empty"]) {
  test(`retry preserves user-owned composer even when text matches (${change})`, async () => {
    global.__memberAuth = { status: "anonymous", user: null };
    let calls = 0;
    global.__chatApi = { ...baseApi, listChatSessions: async () => [rooms[0]],
      sendChatMessage: async () => { if (++calls === 1) throw new Error("offline"); return { reply: "ok", ready: true, provider: "guest", model: "m" }; },
      validateChatFile: () => "text", uploadChatAttachment: async () => ({ id: SECOND, kind: "text", name: "notes.md", size: 5 }) };
    const runner = hookRunner(); let c = runner.render(); runner.flushEffects(); await tick(); c = runner.render();
    c.onDraftChange("question"); c = runner.render(); c.onSend(); await tick(); c = runner.render();
    if (change === "same text" || change === "empty") c.onDraftChange(change === "empty" ? "" : "question");
    if (change === "chips") c.onToolGroupsChange(["weather"]);
    if (change === "attachment") c.onAttach([new File(["notes"], "notes.md")]);
    await tick(); c = runner.render(); const bundle = [c.draft, c.attachments, c.toolGroupIds];
    c.onRetry(); await tick(); c = runner.render(); assert.deepEqual([c.draft, c.attachments, c.toolGroupIds], bundle); runner.unmount();
  });
}

test("retry consumes only an unchanged auto-restored draft and normal send consumes its submitted bundle", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  let calls = 0;
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: async () => { if (++calls === 1) throw new Error("offline"); return { reply: "ok", ready: true, provider: "guest", model: "m" }; } };
  const runner = hookRunner(); let c = runner.render(); runner.flushEffects(); await tick(); c = runner.render();
  c.onDraftChange("question"); c = runner.render(); c.onSend(); await tick(); c = runner.render(); assert.equal(c.draft, "question");
  c.onRetry(); await tick(); c = runner.render(); assert.equal(c.draft, "");
  c.onDraftChange("normal question"); c = runner.render(); c.onSend(); await tick(); c = runner.render(); assert.equal(c.draft, ""); runner.unmount();
});

test("edit and delete start cancel writer countdown including delete failure and declined confirmation",async()=>{
 for(const action of ["edit","delete","decline"]){
  global.__memberAuth={status:"anonymous",user:null};global.__path="/chat";global.__pushes=[];global.window.confirm=()=>action!=="decline";
  const payload={offer_writer:true,questions:[]};let stored=[];
  global.__chatApi={...baseApi,listChatSessions:async()=>[],fetchChatHistory:async()=>stored,sendChatMessage:async(_,body,signal,cb)=>{cb.onPlanning(payload);stored=[row(1,"user",body.content),row(2,"assistant","계획")];return {reply:"계획",sessionId:FIRST,assistantMessageId:2,ready:true,model:"test",provider:"guest"};},deleteChatMessages:async()=>{throw new Error("delete offline");}};
  const runner=hookRunner();let c=runner.render();runner.flushEffects();await tick();c=runner.render();runner.flushEffects();c.onDraftChange("계획");c=runner.render();c.onSend();await tick();c=runner.render();runner.flushEffects();assert.equal(c.writerSeconds,20);
  if(action==="edit")c.onEditMessage(1);else c.onDeleteMessage(1);await tick();c=runner.render();runner.flushEffects();assert.equal(c.writerSeconds,null);assert.deepEqual(global.__pushes,[]);if(action==="delete")assert.equal(c.error,"delete offline");runner.unmount();
 }
 global.__path="/";
});

test("composer reserves concurrent batches atomically and rejects overflow without uploads", async () => {
  const wait = deferred(), uploads = [], sends = [];
  const runner = await urlRunner({ validateChatFile: () => "image", uploadChatAttachment: async (_, __, source) => {
    const id = `image-${uploads.length}`; uploads.push(source); await wait.promise;
    return { id, kind: "image", name: source.name, size: source.size };
  }, sendChatMessage: async (_, body) => { sends.push(body); if (sends.length === 1) throw new Error("offline"); return urlReply; } });
  let c = runner.render();
  const files = count => Array.from({ length: count }, (_, i) => new File(["pixels"], `seat-${i}.png`, { type: "image/png" }));
  c.onAttach(files(6)); c.onAttach(files(5)); c = runner.render();
  assert.equal(c.attachments.length, 6); assert.match(c.notice, /10개/);
  c.onAttach(files(4)); c.onAttach(files(1)); await tick(); c = runner.render();
  assert.equal(c.attachments.length, 10); assert.equal(uploads.length, 10);
  wait.resolve(); await tick(); c = runner.render();
  c.onDraftChange("잠실 사진"); c = runner.render(); c.onSend(); await tick();
  assert.equal(sends[0].attachmentIds.length, 10);
  c = runner.render(); assert.equal(c.attachments.length, 10);
  c.onRetry(); await tick();
  assert.deepEqual(sends[1].attachmentIds, sends[0].attachmentIds); assert.equal(uploads.length, 10);
  runner.unmount();
});

test("mixed image and text ten accepted; URL and eleven batches reject atomically", async () => {
  const uploads = [], sends = [];
  const runner = await urlRunner({ validateChatFile: file => file.name.endsWith("png") ? "image" : "text", uploadChatAttachment: async (_, __, source) => {
    uploads.push(source); return { id: `ref-${uploads.length}`, kind: source.name.endsWith("png") ? "image" : "text", name: source.name, size: 4 };
  }, sendChatMessage: async (_, body) => { sends.push(body); return urlReply; } });
  let c = runner.render();
  c.onAttach([new File(["x"], "x.txt"), "not a URL"]); await tick(); c = runner.render();
  assert.equal(uploads.length, 0); assert.equal(c.attachments.length, 0); assert.match(c.notice, /HTTP/);
  c.onAttach(Array.from({ length: 11 }, () => new File(["x"], "x.txt"))); c = runner.render();
  assert.equal(c.attachments.length, 0); assert.match(c.notice, /10개/);
  c.onAttach(Array.from({ length: 10 }, (_, i) => new File(["x"], i < 5 ? "x.png" : "x.txt"))); await tick(); c = runner.render();
  assert.equal(c.attachments.length, 10); assert.equal(uploads.length, 10);
  c.onDraftChange("잠실 자료 https://example.com/"); c = runner.render(); c.onSend(); await tick();
  assert.equal(sends.length, 0); c = runner.render(); assert.match(c.notice, /10개/);
  c.onDraftChange("잠실 자료"); c = runner.render(); c.onSend(); await tick();
  assert.equal(sends[0].attachmentIds.length, 10); assert.equal(sends[0].content, "잠실 자료"); runner.unmount();
});

const urlAttachment = (url, id = FIRST) => ({ id, kind: "url", name: url, url, size: 0 });
const urlReply = { reply: "ok", sessionId: FIRST, ready: true, model: "m", provider: "guest" };
async function urlRunner(api = {}) {
  global.__memberAuth = { status: "anonymous", user: null };
  global.__chatApi = { ...baseApi, deleteChatAttachment: async () => {}, listChatSessions: async () => [], createChatSession: async () => rooms[0], validateChatFile: () => "text", uploadChatAttachment: async (_, __, source) => typeof source === "string" ? urlAttachment(source) : ({ id: FIRST, kind: "text", name: source.name, size: source.size }), sendChatMessage: async () => urlReply, ...api };
  const runner = hookRunner(); runner.render(); runner.flushEffects(); await tick();
  return runner;
}

test("automatic URL registration preserves inline literal on success and failed drafts", async () => {
  const uploads = [], sends = []; let fail = true;
  const runner = await urlRunner({ uploadChatAttachment: async (_, __, url) => { uploads.push(url); if (fail) throw new Error("offline"); return urlAttachment(url); }, sendChatMessage: async (_, body) => { sends.push(body); return urlReply; } });
  let c = runner.render(); const text = "요약 https://EXAMPLE.com:443/story#part";
  c.onDraftChange(text); c.onCommitUrls(text, true); await tick(); c = runner.render();
  assert.equal(c.draft, text); assert.equal(c.attachments[0].state, "failed");
  fail = false; c.onRetryAttachment(c.attachments[0].key); await tick(); c = runner.render();
  assert.equal(c.draft, text);
  c.onSend(); await tick(); assert.equal(sends[0].content, text); assert.deepEqual(sends[0].attachmentIds, [FIRST]);
  assert.equal(uploads.length, 2); runner.unmount();
});

test("URL edit and failed-turn retry keep attachment IDs without re-extracting or disturbing newer draft", async () => {
  let uploads = 0, fail = true; const edits = [];
  const text = "요약 https://example.com/";
  const runner = await urlRunner({ listChatSessions: async () => [rooms[0]], fetchChatHistory: async () => [{ ...row(USER_MSG, "user", text), attachments: [{ id: FIRST, kind: "url", name: "Example title", url: "https://example.com/", size: 0, content_type: "text/html", width: null, height: null, created_at: "now" }], tool_group_ids: [] }], uploadChatAttachment: async (_, __, url) => { uploads++; return urlAttachment(url); }, editChatMessage: async (_, body) => { edits.push(body); if (fail) throw new Error("turn offline"); return urlReply; } });
  global.window.confirm = () => true;
  let c = runner.render(); c.onDraftChange("newer draft"); c = runner.render(); c.onEditMessage(USER_MSG); c = runner.render();
  c.onDraftChange(`${text} 수정`); c = runner.render(); c.onSend(); await tick(); c = runner.render();
  assert.equal(uploads, 0); assert.equal(c.error, "turn offline"); assert.equal(c.draft, "newer draft"); assert.deepEqual(edits[0].attachmentIds, [FIRST]);
  fail = false; c.onRetry(); await tick(); c = runner.render(); assert.equal(uploads, 0); assert.deepEqual(edits[1].attachmentIds, [FIRST]); assert.equal(c.draft, "newer draft"); runner.unmount();
});

test("inline URL parser ignores incidental malformed and credential URLs, preserving punctuation in paths", () => {
  const require = createRequire(join(scratch, "entry.cjs"));
  const { inlineChatUrls } = require("./lib/chat/inline-urls.js");
  assert.deepEqual(inlineChatUrls("xhttps://example.com https:/bad https://user:pw@example.com https:// https://example. ftp://example.com"), []);
  assert.deepEqual(inlineChatUrls("요약 (https://Example.com/wiki/A_(B)), https://example.com/wiki/A_(B)#part"), ["https://example.com/wiki/A_(B)"]);
});


test("URL tokenizer preserves literals, query distinctions and composer bytes", () => {
  const require = createRequire(join(scratch, "entry.cjs"));
  const { chatUrlTokens, chatComposerContent } = require("./lib/chat/inline-urls.js");
  const text = "  https://example.com/a?q=1, 질문\nhttps://EXAMPLE.com:443/a?q=1#part! https://example.com/a?q=12 ";
  assert.deepEqual(chatUrlTokens(text).map(token => token.literal), ["https://example.com/a?q=1", "https://EXAMPLE.com:443/a?q=1#part", "https://example.com/a?q=12"]);
  assert.equal(chatComposerContent(text, []), text.trim());
  assert.equal(chatComposerContent("  ", [{ kind: "text", state: "ready", attachment: { id: FIRST } }]), "");
});


test("accepted attachment submit transfers to optimistic history before SSE and preserves metadata on done refresh", async () => {
  const upload = deferred(), reply = deferred(), sends = [];
  let callbacks, stored = [], uploads = 0;
  const dto = { id: FIRST, kind: "image", name: "photo.png", url: `/llm/guest/sessions/${FIRST}/attachments/${FIRST}/`, size: 12, content_type: "image/png", width: 96, height: 76, created_at: "now" };
  const runner = await urlRunner({ validateChatFile: () => "image", uploadChatAttachment: () => { uploads++; return upload.promise; }, fetchChatHistory: async () => stored, sendChatMessage: (_, body, signal, cb) => { sends.push(body); callbacks = cb; return reply.promise; } });
  let c = runner.render(); c.onDraftChange("사진 질문"); c.onAttach([new File(["photo"], "photo.png", { type: "image/png" })]); c = runner.render();
  const preview = c.attachments[0].preview;
  c.onSend(); await tick(); c = runner.render();
  assert.equal(sends.length, 0); assert.equal(c.attachments[0].state, "uploading"); assert.equal(c.draft, "사진 질문");
  upload.resolve({ id: FIRST, kind: "image", name: "photo.png", size: 12, url: dto.url }); await tick(); c = runner.render();
  assert.equal(c.pending, "사진 질문"); assert.equal(c.messages.length, 1); assert.equal(c.messages[0].content, "사진 질문"); assert.equal(c.messages[0].attachments[0].preview, preview);
  assert.deepEqual(c.attachments, []); assert.equal(c.draft, ""); assert.deepEqual(sends[0].attachmentIds, [FIRST]);
  callbacks.onDelta("분석", null); c = runner.render(); assert.equal(c.streaming, "분석"); assert.equal(c.messages.length, 1); assert.deepEqual(c.attachments, []);
  stored = [{ ...row(1, "user", "사진 질문"), attachments: [dto] }, row(2, "assistant", "ok")];
  reply.resolve(urlReply); await tick(); c = runner.render();
  assert.equal(c.messages[0].id, 1); assert.equal(c.messages[0].attachments[0].preview, preview); assert.equal(c.messages[0].attachments[0].contentType, "image/png"); assert.equal(c.draft, ""); assert.deepEqual(c.attachments, []);
  c.onDraftChange("후속 질문"); c = runner.render(); c.onSend(); await tick();
  assert.equal(uploads, 1); assert.deepEqual(sends[1].attachmentIds, []); assert.equal(sends[1].sessionId, FIRST);
  assert.equal((await fetch(preview)).ok, true);
  runner.unmount();
  await assert.rejects(fetch(preview));
});

for (const action of ["failure", "stop"]) for (const newer of [false, true]) {
  test(`attachment ${action} restores accepted bundle without overwriting newer draft (${newer})`, async () => {
    let reject;
    const runner = await urlRunner({ sendChatMessage: (_, body, signal) => new Promise((resolve, fail) => { reject = fail; signal.addEventListener("abort", () => fail(new Error("aborted")), { once: true }); }) });
    let c = runner.render(); c.onDraftChange("요약 https://example.com/a"); c.onCommitUrls("https://example.com/a", true); await tick(); c = runner.render();
    const selected = c.attachments;
    c.onSend(); await tick(); c = runner.render(); assert.deepEqual(c.attachments, []); assert.equal(c.messages.length, 1);
    if (newer) { c.onDraftChange("새 질문"); c = runner.render(); c.onDraftChange(""); c = runner.render(); }
    if (action === "stop") c.onCancel(); else reject(new Error("offline"));
    await tick(); c = runner.render();
    assert.equal(c.messages.length, 0); assert.equal(c.draft, newer ? "" : "요약 https://example.com/a"); assert.deepEqual(c.attachments, newer && action !== "stop" ? [] : selected);
    runner.unmount();
  });
}

test("successful URL-only submit never puts the literal back in the composer", async () => {
  const reply = deferred();
  const runner = await urlRunner({ sendChatMessage: () => reply.promise, fetchChatHistory: async () => [] });
  let c = runner.render(); c.onDraftChange("https://example.com/a#part"); c.onCommitUrls("https://example.com/a#part", true); await tick(); c = runner.render(); c.onSend(); await tick(); c = runner.render();
  assert.equal(c.draft, ""); assert.deepEqual(c.attachments, []); assert.equal(c.messages[0].attachments[0].id, FIRST); assert.equal(c.messages[0].content, "https://example.com/a#part");
  reply.resolve(urlReply); await tick(); c = runner.render(); assert.equal(c.draft, ""); assert.deepEqual(c.attachments, []); runner.unmount();
});

// Attachment ownership regressions (also exercised by the standalone reproduction).
for (const destination of ["same", "room", "identity"]) {
  test(`cancel preserves newer composer input and scopes retry (${destination})`, async () => {
    const sends = [];
    const runner = await urlRunner({ sendChatMessage: (_, body, signal) => {
      sends.push(body);
      return new Promise((_, reject) => signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true }));
    } });
    try {
      let c = runner.render(); c.onDraftChange("요약 https://example.com/a"); c.onAttach([new File(["notes"], "x.txt")]); await tick();
      c = runner.render(); c.onSend(); await tick(); c = runner.render(); c.onDraftChange("newer input"); c = runner.render(); c.onCancel(); await tick(); c = runner.render();
      assert.equal(c.draft, "newer input"); assert.equal(c.attachments[0].attachment.id, FIRST); assert.equal(c.failed, "");
      if (destination === "room") { c.onReset(); c = runner.render(); }
      if (destination === "identity") { global.__memberAuth = { status: "authenticated", user: { id: 99 } }; runner.render(); runner.flushEffects(); await tick(); c = runner.render(); }
      c.onRetry(); c = runner.render();
      if (destination === "same") {
        assert.equal(sends.length, 2); assert.equal(sends[1].sessionId, FIRST); assert.deepEqual(sends[1].attachmentIds, [FIRST]);
        assert.equal(c.draft, "newer input"); c.onCancel(); await tick();
      } else { assert.equal(sends.length, 1); assert.deepEqual(c.attachments, []); }
    } finally { runner.unmount(); }
  });
}

for (const text of ["https://example.com/a#part", "요약 https://example.com/a", "  요약 https://example.com/a  "]) {
  test(`unchanged converted retry consumes composer on acceptance and restores exact draft on repeat failure (${text})`, async () => {
    let calls = 0; const reply = deferred();
    const runner = await urlRunner({ fetchChatHistory: async () => { throw new Error("history unavailable"); }, sendChatMessage: async () => {
      if (++calls <= 2) throw new Error("offline");
      return reply.promise;
    } });
    try {
      let c = runner.render(); c.onDraftChange(text); c.onAttach([new File(["notes"], "x.txt")]); await tick(); c = runner.render();
      const converted = c.draft;
      c.onSend(); await tick(); c = runner.render(); assert.equal(c.draft, converted);
      c.onRetry(); await tick(); c = runner.render(); assert.equal(c.draft, converted); assert.equal(c.attachments.length, 2);
      c.onRetry(); c = runner.render(); assert.equal(c.draft, ""); assert.deepEqual(c.attachments, []); assert.equal(c.messages[0].attachments[0].id, FIRST);
      reply.resolve(urlReply); await tick(); c = runner.render(); assert.equal(c.draft, ""); assert.deepEqual(c.attachments, []);
    } finally { runner.unmount(); }
  });
}

for (const newer of [null, "요약 ", "newer input"]) {
  test(`completed history clears only owned converted draft (${newer})`, async () => {
    const history = deferred();
    const runner = await urlRunner({ fetchChatHistory: () => history.promise, sendChatMessage: async () => {
      const { ChatClientError } = createRequire(join(scratch, "entry.cjs"))("./test-chat-client.js");
      const error = new ChatClientError("offline"); error.sessionId = FIRST; throw error;
    } });
    try {
      let c = runner.render(); c.onDraftChange("요약 https://example.com/a"); c.onAttach([new File(["notes"], "x.txt")]); await tick(); c = runner.render();
      c.onSend(); await tick(); c = runner.render(); assert.equal(c.draft, "요약 https://example.com/a");
      if (newer !== null) { c.onDraftChange(newer); c = runner.render(); }
      const dto = { id: FIRST, kind: "url", name: "title", url: "https://example.com/a", size: 0, content_type: "text/html", width: null, height: null, created_at: "now" };
      history.resolve([{ ...row(1, "user", "요약 https://example.com/a"), attachments: [dto] }, row(2, "assistant", "ok")]); await tick(); c = runner.render();
      assert.equal(c.draft, newer ?? ""); assert.equal(c.failed, ""); assert.equal(c.error, ""); assert.equal(c.attachments.length, newer === null ? 0 : 1);
    } finally { runner.unmount(); }
  });
}
// End attachment ownership regressions.

test("deferred paste cannot attach into a new conversation", async () => {
  let uploads = 0;
  const runner = await urlRunner({ uploadChatAttachment: async (_, __, url) => { uploads++; return urlAttachment(url); } });
  let c = runner.render(); c.onDraftChange("old draft"); c = runner.render();
  const paste = c.onCommitUrls;
  c.onReset(); c = runner.render(); paste("https://example.com/", true); await tick(); c = runner.render();
  assert.equal(uploads, 0); assert.equal(c.draft, ""); assert.deepEqual(c.attachments, []); runner.unmount();
});

for (const only of [false, true]) test(`pending automatic URL send waits and sends exact question with one reference (${only})`, async () => {
  const upload = deferred(), sends = [], uploads = [];
  const runner = await urlRunner({ uploadChatAttachment: (_, __, url) => { uploads.push(url); return upload.promise; }, sendChatMessage: async (_, body) => { sends.push(body); return urlReply; } });
  let c = runner.render(); const literal = "https://EXAMPLE.com/a?q=1#part";
  c.onDraftChange(only ? literal : `질문 ${literal}`); c.onCommitUrls(literal, true); c = runner.render(); c.onSend(); await tick();
  assert.equal(sends.length, 0); upload.resolve(urlAttachment("https://example.com/a?q=1")); await tick(); c = runner.render();
  assert.equal(sends[0].content, only ? literal : `질문 ${literal}`); assert.deepEqual(sends[0].attachmentIds, [FIRST]);
  assert.equal(uploads.length, 1); assert.equal(c.draft, ""); runner.unmount();
});
test("IME, removal suppression and renewed paste keep a single URL registration", async () => {
  const uploads = [], upload = deferred();
  const runner = await urlRunner({ uploadChatAttachment: (_, __, url) => { uploads.push(url); return upload.promise; } });
  let c = runner.render(); const url = "https://example.com/a";
  c.onCompositionChange(true); c.onDraftChange(`질문 ${url}`); c.onCommitUrls(url); await tick(); assert.equal(uploads.length, 0);
  c.onCompositionChange(false); c = runner.render(); c.onCommitUrls(url); c = runner.render(); c.onCommitUrls(url); await tick();
  assert.equal(uploads.length, 1); c = runner.render(); c.onRemoveAttachment(c.attachments[0].key); c.onCommitUrls(url); await tick();
  assert.equal(uploads.length, 1); upload.resolve(urlAttachment(url)); await tick(); c = runner.render(); assert.equal(c.draft, `질문 ${url}`); assert.equal(c.attachments.length, 0);
  c.onCommitUrls(url, true); await tick(); c = runner.render(); assert.equal(uploads.length, 2); assert.equal(c.attachments.length, 1); assert.equal(c.draft, `질문 ${url}`); runner.unmount();
});
test("pending mixed slots and URL-specific cap reject entire batches", async () => {
  const wait = deferred(), uploads = [];
  const runner = await urlRunner({ uploadChatAttachment: (_, __, source) => { uploads.push(source); return wait.promise; } });
  let c = runner.render(); c.onAttach(["https://a.com", "https://b.com", "https://c.com", ...Array.from({length: 7}, () => new File(["x"], "x.txt"))]); await tick(); c = runner.render();
  assert.equal(c.attachments.length, 10); assert.equal(uploads.length, 10);
  c.onAttach([new File(["x"], "x.txt")]); c = runner.render(); assert.match(c.notice, /10개/); assert.equal(uploads.length, 10);
  c.onAttach(["https://d.com"]); c = runner.render(); assert.match(c.notice, /3개/); assert.equal(uploads.length, 10); runner.unmount(); wait.resolve(urlAttachment("https://a.com")); await tick();
});

test("late removed URL registration cleans its unused owned metadata", async () => {
  const upload = deferred(), deleted = [];
  const runner = await urlRunner({ uploadChatAttachment: () => upload.promise, deleteChatAttachment: async (...args) => deleted.push(args) });
  let c = runner.render(); c.onAttach(["https://example.com/a"]); await tick(); c = runner.render(); c.onRemoveAttachment(c.attachments[0].key);
  upload.resolve(urlAttachment("https://example.com/a")); await tick(); c = runner.render();
  assert.equal(c.attachments.length, 0); assert.deepEqual(deleted, [["guest", FIRST, FIRST]]); runner.unmount();
});

for (const size of [1999, 2000]) test(`one paste uses Unicode codepoints threshold (${size}) and full plain original`, async () => {
  const uploads = [], sends = [];
  const runner = await urlRunner({ uploadChatAttachment: async (_, __, file) => { uploads.push(file); return { id: FIRST, kind: "text", name: file.name, size: file.size }; }, sendChatMessage: async (_, body) => { sends.push(body); return urlReply; } });
  let c = runner.render(); const text = "😀".repeat(size - 50) + "<script>literal</script> https://example.com/".padEnd(50, "x");
  c.onDraftChange("before SELECT after"); c = runner.render(); const marker = c.onPasteText(text);
  if (size === 1999) { assert.equal(marker, null); assert.equal(uploads.length, 0); }
  else {
    assert.equal(marker, "[[ Text 1 ]]"); c.onDraftChange(`before ${marker} after`); await tick(); c = runner.render();
    assert.equal(await uploads[0].text(), text); assert.equal(uploads.length, 1); assert.equal(c.attachments[0].inlineText, marker);
    c.onSend(); await tick(); assert.deepEqual(sends[0].attachmentIds, [FIRST]); assert.doesNotMatch(sends[0].content, /\[\[ Text/); assert.equal(sends[0].content, "before 첨부 참고 자료: Pasted Text 1.txt after");
  }
  runner.unmount();
});
for (const newer of ["surrounding", "replacement", "empty", "room", "identity"]) test(`failed long paste restores original without taking newer input ownership (${newer})`, async () => {
  const wait = deferred();
  const runner = await urlRunner({ uploadChatAttachment: async () => { await wait.promise; throw new Error("upload offline"); } });
  let c = runner.render(); c.onDraftChange("a".repeat(2000)); c = runner.render(); assert.equal(c.attachments.length, 0);
  const text = "😀<script>literal</script>".repeat(200), marker = c.onPasteText(text);
  c.onDraftChange(`prefix ${marker} suffix`); await tick(); c = runner.render();
  if (newer === "surrounding") c.onDraftChange(`new prefix ${marker} newer suffix`);
  if (newer === "replacement") c.onDraftChange("newer input");
  if (newer === "empty") c.onDraftChange("");
  if (newer === "room") c.onReset();
  if (newer === "identity") { global.__memberAuth = { status: "authenticated", user: { id: 99 } }; runner.render(); runner.flushEffects(); }
  c = runner.render(); wait.resolve(); await tick(); c = runner.render();
  assert.equal(c.draft, newer === "surrounding" ? `new prefix ${text} newer suffix` : newer === "replacement" ? "newer input" : "");
  assert.deepEqual(c.attachments, []);
  if (newer === "surrounding") { c.onDraftChange(`undo ${marker}`); c = runner.render(); assert.equal(c.draft, `undo ${text}`); }
  runner.unmount();
});

test("native undo redo retains removed reference mapping, submission excludes deleted references and fences old markers", async () => {
  const sends = [], deletes = [];
  const runner = await urlRunner({ deleteChatAttachment: async (...args) => deletes.push(args), sendChatMessage: async (_, body) => { sends.push(body); return urlReply; } });
  let c = runner.render(); const text = "b".repeat(2000), marker = c.onPasteText(text);
  c.onDraftChange(`prefix ${marker} suffix`); await tick(); c = runner.render(); const key = c.attachments[0].key;
  c.onRemoveAttachment(key); c = runner.render(); assert.equal(c.draft, `prefix ${text} suffix`); assert.deepEqual(c.attachments, []);
  c.onDraftChange(`prefix ${marker} suffix`); c = runner.render(); assert.equal(c.attachments[0].attachment.id, FIRST);
  c.onDraftChange(`prefix ${text} suffix`); c = runner.render();
  c.onDraftChange(`prefix ${marker} suffix`); c = runner.render(); c.onSend(); await tick(); c = runner.render();
  assert.deepEqual(sends[0].attachmentIds, [FIRST]); assert.deepEqual(c.attachments, []);
  c.onDraftChange(marker); c = runner.render(); assert.equal(c.draft, text); assert.deepEqual(c.attachments, []);
  const next = c.onPasteText(text); assert.notEqual(next, marker); c.onDraftChange("deleted reference"); await tick(); c = runner.render(); c.onSend(); await tick();
  assert.deepEqual(sends[1].attachmentIds, []); assert.deepEqual(deletes, []); runner.unmount();
});

test("undo of removed in-flight paste completes its original mapping without another upload", async () => {
  const wait = deferred(); let uploads = 0;
  const runner = await urlRunner({ uploadChatAttachment: async (_, __, file) => { uploads++; await wait.promise; return { id: FIRST, kind: "text", name: file.name, size: file.size }; } });
  let c = runner.render(); const text = "한".repeat(2000), marker = c.onPasteText(text);
  c.onDraftChange(marker); await tick(); c = runner.render(); const key = c.attachments[0].key;
  c.onRemoveAttachment(key); c = runner.render(); assert.equal(c.draft, text); assert.deepEqual(c.attachments, []);
  c.onDraftChange(marker); c = runner.render(); assert.equal(c.attachments[0].state, "uploading");
  wait.resolve(); await tick(); c = runner.render(); assert.equal(c.attachments[0].state, "ready"); assert.equal(uploads, 1);
  c.onDraftChange(""); c = runner.render(); assert.deepEqual(c.attachments, []);
  c.onDraftChange(marker); c = runner.render(); assert.equal(c.attachments[0].attachment.id, FIRST); runner.unmount();
});

test("failed unchanged paste restores exact full original and manual selection added later survives tag deletion", async () => {
  const runner = await urlRunner({ uploadChatAttachment: async () => { throw new Error("offline"); } });
  let c = runner.render(); const text = "字".repeat(2000), marker = c.onPasteText(text);
  c.onDraftChange(`before ${marker} after`); await tick(); c = runner.render(); assert.equal(c.draft, `before ${text} after`); assert.deepEqual(c.attachments, []);
  c.onInlineToolSelect("rules", "야구 규칙"); c.onDraftChange("@야구 규칙"); c = runner.render();
  c.onToolGroupsChange([...c.toolGroupIds, "rules"]); c = runner.render(); c.onDraftChange(""); c = runner.render(); assert.deepEqual(c.toolGroupIds, ["rules"]); runner.unmount();
});

for (const explicit of [false, true]) test(`deleting tool tag deselects only inline-owned capability and undo redo synchronizes (${explicit})`, async () => {
  const runner = await urlRunner(); let c = runner.render();
  if (explicit) { c.onToolGroupsChange(["rules"]); c = runner.render(); }
  c.onInlineToolSelect("rules", "야구 규칙"); c.onDraftChange("@야구 규칙 question"); c = runner.render(); assert.deepEqual(c.toolGroupIds, ["rules"]);
  c.onDraftChange("question"); c = runner.render(); assert.deepEqual(c.toolGroupIds, explicit ? ["rules"] : []);
  c.onDraftChange("@야구 규칙 question"); c = runner.render(); assert.deepEqual(c.toolGroupIds, ["rules"]);
  c.onDraftChange("question"); c = runner.render(); assert.deepEqual(c.toolGroupIds, explicit ? ["rules"] : []); runner.unmount();
});
test("paste limit refusal retains original via caller and no pending placeholder", async () => {
  const runner = await urlRunner(); let c = runner.render();
  c.onAttach(Array.from({ length: 10 }, (_, index) => new File(["x"], `${index}.txt`))); await tick(); c = runner.render();
  assert.equal(c.onPasteText("a".repeat(2000)), null); c = runner.render(); assert.equal(c.attachments.length, 10); assert.match(c.notice, /10개/); runner.unmount();
});


test("undo restoration at ten slots expands auto TXT to original without dropping existing cards", async () => {
  let uploads = 0;
  const runner = await urlRunner({ uploadChatAttachment: async (_, __, file) => ({ id: `unique-${++uploads}`, kind: "text", name: file.name, size: file.size }) });
  try {
    let c = runner.render(); const original = "가".repeat(2000);
    const marker = c.onPasteText(original); c.onDraftChange(marker); await tick(); c = runner.render();
    c.onDraftChange(""); c = runner.render();
    c.onAttach(Array.from({ length: 10 }, (_, index) => new File(["notes"], `manual-${index}.txt`))); await tick(); c = runner.render();
    assert.equal(c.attachments.length, 10);
    c.onDraftChange(marker); c = runner.render();
    assert.equal(c.attachments.length, 10); assert.equal(c.draft, original); assert.match(c.notice, /10개/);
  } finally { runner.unmount(); }
});

for (const kind of ["url", "text"]) for (const completeRemoved of [false, true]) test(`actual pending ${kind} × undo retains original request and exact send ID (complete removed: ${completeRemoved})`, async () => {
  const wait = deferred(), sends = [], uploads = [];
  const runner = await urlRunner({ uploadChatAttachment: (_, __, source, signal) => { uploads.push({ source, signal }); return wait.promise; }, sendChatMessage: async (_, body) => { sends.push(body); return urlReply; } });
  try {
    let c = runner.render(); const literal = "https://pending.example.com/a", original = "가".repeat(2000);
    const marker = kind === "url" ? literal : c.onPasteText(original);
    const draft = `prior ${marker} suffix`;
    c.onDraftChange(draft); if (kind === "url") c.onCommitUrls(literal, true);
    await tick(); const editor = inlineEditor(runner); c = editor.render(); const key = c.attachments[0].key;
    editor.remove(); c = editor.render(); assert.equal(c.draft, "prior  suffix"); assert.deepEqual(c.attachments, []);
    assert.equal(uploads[0].signal.aborted, false);
    const attachment = kind === "url" ? urlAttachment(literal, SECOND) : { id: SECOND, kind: "text", name: uploads[0].source.name, size: uploads[0].source.size };
    if (completeRemoved) { wait.resolve(attachment); await tick(); c = editor.render(); assert.deepEqual(c.attachments, []); }
    editor.undo(); c = editor.render(); assert.equal(c.draft, draft); assert.equal(c.attachments[0].key, key);
    editor.undo(true); c = editor.render(); assert.deepEqual(c.attachments, []);
    editor.undo(); c = editor.render(); assert.equal(c.attachments[0].key, key);
    c.onSend(); await tick(); assert.equal(sends.length, completeRemoved ? 1 : 0);
    if (!completeRemoved) { wait.resolve(attachment); await tick(); c = editor.render(); }
    assert.equal(uploads.length, 1); assert.equal(sends.length, 1); assert.deepEqual(sends[0].attachmentIds, [SECOND]);
    assert.equal(sends[0].content, kind === "url" ? draft : "prior 첨부 참고 자료: Pasted Text 1.txt suffix");
  } finally { runner.unmount(); }
});

for (const kind of ["url", "text"]) test(`actual pending ${kind} × during submit fences old send and excludes hidden reference`, async () => {
  const wait = deferred(), sends = []; let signal;
  const runner = await urlRunner({ uploadChatAttachment: (_, __, source, value) => { signal = value; return wait.promise; }, sendChatMessage: async (_, body) => { sends.push(body); return urlReply; } });
  try {
    let c = runner.render(); const literal = "https://pending.example.com/a", original = "나".repeat(2000);
    const marker = kind === "url" ? literal : c.onPasteText(original);
    c.onDraftChange(`question ${marker}`); if (kind === "url") c.onCommitUrls(literal, true);
    await tick(); const editor = inlineEditor(runner); c = editor.render();
    c.onSend(); await tick(); editor.remove(); c = editor.render(); assert.deepEqual(c.attachments, []); assert.equal(signal.aborted, false);
    wait.resolve(kind === "url" ? urlAttachment(literal, SECOND) : { id: SECOND, kind: "text", name: "Pasted Text 1.txt", size: 6000 });
    await tick(); c = editor.render(); assert.equal(sends.length, 0); assert.deepEqual(c.attachments, []);
    c.onSend(); await tick(); c = editor.render(); assert.equal(sends.length, 1); assert.deepEqual(sends[0].attachmentIds, []); assert.equal(sends[0].content, "question");
    editor.undo(); c = editor.render(); assert.deepEqual(c.attachments, []); assert.doesNotMatch(c.draft, /\[\[ Text/);
  } finally { runner.unmount(); }
});

for (const kind of ["url", "text"]) test(`explicit ${kind} cancel still aborts and rejects late completion`, async () => {
  const wait = deferred(), sends = []; let signal;
  const runner = await urlRunner({ uploadChatAttachment: (_, __, source, value) => { signal = value; return wait.promise; }, sendChatMessage: async (_, body) => { sends.push(body); return urlReply; } });
  try {
    let c = runner.render(); const literal = "https://pending.example.com/a", original = "다".repeat(2000);
    const marker = kind === "url" ? literal : c.onPasteText(original);
    c.onDraftChange(marker); if (kind === "url") c.onCommitUrls(literal, true);
    await tick(); c = runner.render(); const key = c.attachments[0].key;
    c.onCancelAttachment(key); c = runner.render(); assert.equal(signal.aborted, true);
    wait.resolve(kind === "url" ? urlAttachment(literal, SECOND) : { id: SECOND, kind: "text", name: "Pasted Text 1.txt", size: 6000 });
    await tick(); c = runner.render();
    if (kind === "url") { assert.equal(c.attachments[0].state, "cancelled"); assert.equal(c.attachments[0].attachment, undefined); c.onSend(); await tick(); assert.equal(sends.length, 0); }
    else { assert.equal(c.draft, original); assert.deepEqual(c.attachments, []); c.onDraftChange(marker); c = runner.render(); assert.equal(c.draft, original); assert.deepEqual(c.attachments, []); }
  } finally { runner.unmount(); }
});

test("URL chip deletion and undo reconnect the same mapping without duplicate registration", async () => {
  let uploads = 0;
  const runner = await urlRunner({ uploadChatAttachment: async (_, __, url) => { uploads++; return urlAttachment(url); } });
  try {
    let c = runner.render(); const draft = "prior https://example.com/a suffix";
    c.onDraftChange(draft); c.onCommitUrls(draft, true); await tick(); c = runner.render();
    const id = c.attachments[0].attachment.id;
    c.onDraftChange("prior  suffix"); c = runner.render(); assert.equal(c.attachments.length, 0);
    c.onDraftChange(draft); c = runner.render(); assert.equal(c.attachments.length, 1); assert.equal(c.attachments[0].attachment.id, id); assert.equal(uploads, 1);
  } finally { runner.unmount(); }
});


test("removed pending URL failure restores failed mapping and retries once", async () => {
  const wait = deferred(); let uploads = 0;
  const runner = await urlRunner({ uploadChatAttachment: async (_, __, url) => { if (++uploads === 1) { await wait.promise; throw new Error("offline"); } return urlAttachment(url); } });
  try {
    let c = runner.render(); const url = "https://example.com/fail";
    c.onDraftChange(url); c.onCommitUrls(url, true); await tick();
    const editor = inlineEditor(runner); c = editor.render(); editor.remove(); editor.render();
    wait.resolve(); await tick(); editor.undo(); c = editor.render();
    assert.equal(c.attachments[0].state, "failed");
    c.onRetryAttachment(c.attachments[0].key); await tick(); c = editor.render();
    assert.equal(c.attachments[0].state, "ready"); assert.equal(uploads, 2);
  } finally { runner.unmount(); }
});

test("saved edit chip ownership removal and undo preserve only independent selections", async () => {
  const edits = [];
  const runner = await urlRunner({ listChatSessions: async () => [rooms[0]], fetchChatHistory: async () => [{ ...row(USER_MSG, "user", "@야구 규칙 질문"), tool_group_ids: ["rules", "schedule"] }], editChatMessage: async (_, body) => { edits.push(body); return urlReply; } });
  try {
    let c = runner.render(); c.onEditMessage(USER_MSG); c = runner.render();
    c.onInlineToolSelect("rules", "야구 규칙", true);
    c.onDraftChange("질문"); c = runner.render(); assert.deepEqual(c.toolGroupIds, ["schedule"]);
    c.onDraftChange("@야구 규칙 질문"); c = runner.render(); assert.deepEqual(c.toolGroupIds, ["schedule", "rules"]);
    c.onDraftChange("질문"); c = runner.render(); c.onSend(); await tick();
    assert.deepEqual(edits[0].toolGroupIds, ["schedule"]);
  } finally { runner.unmount(); }
});


test("deleted conversation purges draft/edit maps without discarding a surviving Undo", async () => {
  const runner = await urlRunner();
  try {
    let c = runner.render(); const deleted = c.activeConversationId;
    const oldMarker = c.onPasteText("old".repeat(1000)); c.onDraftChange(oldMarker); await tick(); c = runner.render();
    c.onDraftChange(""); c = runner.render(); c.onReset(); c = runner.render();
    const alive = c.activeConversationId; const marker = c.onPasteText("alive".repeat(500)); c.onDraftChange(marker); await tick(); c = runner.render();
    c.onDraftChange(""); c = runner.render(); c.onDeleteConversation(deleted); c = runner.render();
    c.onDraftChange(marker); c = runner.render(); assert.equal(c.activeConversationId, alive); assert.equal(c.attachments.length, 1);
    c.onSelectConversation(deleted); c = runner.render();
    assert.equal(c.activeConversationId, alive);
    c.onDraftChange(oldMarker); c = runner.render(); assert.equal(c.attachments.length, 0);
  } finally { runner.unmount(); }
});

test("deleted pending session creation cannot resurrect a backend session", async () => {
  const creation = deferred(); let uploads = 0;
  const runner = await urlRunner({ createChatSession: () => creation.promise, uploadChatAttachment: async () => { uploads++; return urlAttachment("https://example.com"); } });
  try {
    let c = runner.render(); const deleted = c.activeConversationId;
    const marker = c.onPasteText("gone".repeat(750)); c.onDraftChange(marker); c = runner.render();
    c.onDeleteConversation(deleted); c = runner.render(); creation.resolve(rooms[0]); await tick(); c = runner.render();
    assert.equal(uploads, 0); assert.equal(c.attachments.length, 0); assert.equal(c.draft, "");
    c.onSelectConversation(deleted); c = runner.render(); c.onEditMessage(USER_MSG); c = runner.render(); assert.equal(c.messages.length, 0);
  } finally { runner.unmount(); }
});

const queueReply = text => ({ reply: text, ready: true, model: "test", provider: "guest" });
async function renderEffects(runner) {
  global.__path = "/chat";
  let chat;
  for (let i = 0; i < 3; i++) { chat = runner.render(); runner.flushEffects(); await tick(); }
  return runner.render();
}
function typeAndSend(runner, text) {
  runner.render().onDraftChange(text);
  runner.render().onSend();
  return runner.render();
}

test("queues at most two follow-ups, keeps the active answer, and sends each exactly once in order", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const waiting = [deferred(), deferred(), deferred()], sent = [], signals = [];
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: (_, body, signal, cb) => {
    const index = sent.length; sent.push(body.content); signals.push(signal); cb.onDelta("답변 중", null); return waiting[index].promise;
  } };
  const runner = hookRunner(); await renderEffects(runner);
  typeAndSend(runner, "첫 질문"); typeAndSend(runner, "두 번째"); typeAndSend(runner, "세 번째");
  let chat = typeAndSend(runner, "한도 밖 질문");
  assert.deepEqual(chat.queued.map(q => q.content), ["두 번째", "세 번째"]);
  assert.equal(chat.draft, "한도 밖 질문"); assert.match(chat.notice, /최대 2개/); assert.equal(signals[0].aborted, false);
  waiting[0].resolve(queueReply("첫 답변 유지")); chat = await renderEffects(runner);
  assert.deepEqual(sent, ["첫 질문", "두 번째"]); assert.equal(chat.messages[1].content, "첫 답변 유지");
  assert.equal(chat.draft, "한도 밖 질문"); assert.equal(chat.queued.length, 1);
  waiting[1].resolve(queueReply("둘째 답변")); await renderEffects(runner);
  assert.deepEqual(sent, ["첫 질문", "두 번째", "세 번째"]);
  waiting[2].resolve(queueReply("셋째 답변")); chat = await renderEffects(runner);
  assert.equal(chat.queued.length, 0); assert.equal(chat.messages.length, 6); runner.unmount();
});

test("editing a queued question holds dispatch, preserves its position and restores unrelated composer text", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const first = deferred(), sent = [];
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: (_, body) => {
    sent.push(body.content); return sent.length === 1 ? first.promise : Promise.resolve(queueReply("수정 반영"));
  } };
  const runner = hookRunner(); await renderEffects(runner);
  typeAndSend(runner, "현재 질문"); typeAndSend(runner, "취소할 질문"); let chat = typeAndSend(runner, "고칠 질문");
  const [cancelled, edited] = chat.queued; chat.onDraftChange("작성 중인 초안"); chat = runner.render(); chat.onEditQueued(edited.id);
  first.resolve(queueReply("원래 답변")); chat = await renderEffects(runner);
  assert.deepEqual(sent, ["현재 질문"]); assert.equal(chat.draft, "고칠 질문");
  chat.onRemoveQueued(cancelled.id); chat.onDraftChange("고친 예약 질문"); runner.render().onSend();
  chat = await renderEffects(runner);
  assert.deepEqual(sent, ["현재 질문", "고친 예약 질문"]); assert.equal(chat.draft, "작성 중인 초안");
  assert.equal(chat.editingQueuedId, null); runner.unmount();
});

test("cancel queued edit leaves original text; cancelling the queued item never aborts the active answer", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const first = deferred(); let signal;
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: (_, body, value) => { signal = value; return first.promise; } };
  const runner = hookRunner(); await renderEffects(runner);
  typeAndSend(runner, "현재"); let chat = typeAndSend(runner, "원래 예약"); const id = chat.queued[0].id;
  chat.onEditQueued(id); runner.render().onDraftChange("저장 안 한 수정"); runner.render().onCancelQueuedEdit(); chat = runner.render();
  assert.equal(chat.queued[0].content, "원래 예약"); chat.onRemoveQueued(id); assert.equal(signal.aborted, false);
  first.resolve(queueReply("완료")); chat = await renderEffects(runner); assert.equal(chat.queued.length, 0); runner.unmount();
});

test("server busy keeps an unsent request queued and waits for usage release instead of displaying an error", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const { ChatClientError } = createRequire(join(scratch, "entry.cjs"))("./test-chat-client.js");
  let attempts = 0, active = true;
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], fetchChatUsage: async () => ({ active_turn: active, can_send: !active }),
    sendChatMessage: async () => { if (++attempts === 1) throw new ChatClientError("busy", 409, false, FIRST, "usage_busy"); return queueReply("자동 재전송 완료"); } };
  const runner = hookRunner(); await renderEffects(runner); typeAndSend(runner, "다른 답변 뒤에 보내줘"); let chat = await renderEffects(runner);
  assert.equal(chat.queued.length, 1); assert.equal(chat.error, ""); assert.equal(chat.failed, ""); assert.equal(attempts, 1);
  assert.equal(chat.messages.length, 0); assert.equal(chat.queueWaitingForServer, true);
  active = false; global.__queueTimer(); chat = await renderEffects(runner);
  assert.equal(chat.queueWaitingForServer, false);
  assert.equal(attempts, 2); assert.equal(chat.queued.length, 0); assert.equal(chat.messages.at(-1).content, "자동 재전송 완료"); runner.unmount();
});

test("retry of a stored failed question queues the same PUT and does not duplicate questions or confirmations", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const { ChatClientError } = createRequire(join(scratch, "entry.cjs"))("./test-chat-client.js");
  let stored = [], sends = 0, edits = 0, confirms = 0, active = true;
  global.window.confirm = () => { confirms++; return true; };
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], fetchChatHistory: async () => stored,
    fetchChatUsage: async () => ({ active_turn: active, can_send: !active }),
    sendChatMessage: async (_, body) => {
      sends++; stored = [row(1, "user", body.content, "failed")];
      throw new ChatClientError("offline", 503, false, FIRST);
    },
    editChatMessage: async (_, body) => {
      assert.equal(body.messageId, 1); assert.equal(body.sessionId, FIRST);
      if (++edits === 1) throw new ChatClientError("busy", 409, false, FIRST, "usage_busy");
      stored = [row(1, "user", body.content), row(2, "assistant", "코스 완료")];
      return { ...queueReply("코스 완료"), sessionId: FIRST, assistantMessageId: 2 };
    } };
  const runner = hookRunner(); await renderEffects(runner);
  typeAndSend(runner, "스테이크 카페 경기 호텔 코스"); let chat = await renderEffects(runner);
  assert.equal(chat.messages[0].status, "failed"); chat.onRetry(); chat = await renderEffects(runner);
  assert.equal(chat.error, ""); assert.equal(chat.failed, ""); assert.equal(chat.queued[0].edit.messageId, 1);
  assert.equal(chat.messages.length, 1); assert.equal(edits, 1);
  active = false; global.__queueTimer(); chat = await renderEffects(runner);
  assert.deepEqual([sends, edits, confirms], [1, 2, 1]); assert.equal(chat.queued.length, 0);
  assert.equal(chat.messages.filter(message => message.role === "user").length, 1);
  assert.equal(chat.messages.at(-1).content, "코스 완료"); runner.unmount();
});

test("busy queued edits preserve their target when changed and leave history intact when cancelled", async () => {
  for (const cancel of [false, true]) {
    global.__memberAuth = { status: "anonymous", user: null }; global.window.confirm = () => true;
    const { ChatClientError } = createRequire(join(scratch, "entry.cjs"))("./test-chat-client.js");
    const original = [row(1, "user", "기존 질문"), row(2, "assistant", "기존 답변")];
    let stored = original, active = true; const edits = [];
    global.__chatApi = { ...baseApi, listChatSessions: async () => [rooms[0]], fetchChatHistory: async () => stored,
      fetchChatUsage: async () => ({ active_turn: active, can_send: !active }),
      editChatMessage: async (_, body) => {
        edits.push(body);
        if (edits.length === 1) throw new ChatClientError("busy", 409, false, FIRST, "usage_busy");
        stored = [row(1, "user", body.content), row(2, "assistant", "수정 답변")];
        return { ...queueReply("수정 답변"), sessionId: FIRST };
      } };
    const runner = hookRunner(); let chat = await renderEffects(runner);
    chat.onEditMessage(1); typeAndSend(runner, "수정 질문"); chat = await renderEffects(runner);
    assert.deepEqual(chat.messages.map(message => message.content), ["기존 질문", "기존 답변"]);
    const id = chat.queued[0].id; chat.onEditQueued(id); chat = await renderEffects(runner);
    chat.onDraftChange("예약에서 고친 질문"); runner.render().onSend();
    chat = await renderEffects(runner); assert.equal(chat.queued[0].edit.messageId, 1);
    if (cancel) chat.onRemoveQueued(id);
    active = false; global.__queueTimer(); chat = await renderEffects(runner);
    assert.equal(edits.length, cancel ? 1 : 2); assert.equal(chat.queued.length, 0);
    if (cancel) assert.deepEqual(stored, original);
    else { assert.equal(edits[1].messageId, 1); assert.equal(edits[1].content, "예약에서 고친 질문"); }
    runner.unmount();
  }
});

test("queued edits pause for changed or deleted history and never replace newly arrived answers without consent", async () => {
  for (const removed of [false, true]) {
    global.__memberAuth = { status: "anonymous", user: null }; global.window.confirm = () => true;
    const { ChatClientError } = createRequire(join(scratch, "entry.cjs"))("./test-chat-client.js");
    let stored = [row(1, "user", "기존 질문", "failed")], active = true, edits = 0;
    global.__chatApi = { ...baseApi, listChatSessions: async () => [rooms[0]], fetchChatHistory: async () => stored,
      fetchChatUsage: async () => ({ active_turn: active, can_send: !active }),
      editChatMessage: async (_, body) => {
        if (++edits === 1) throw new ChatClientError("busy", 409, false, FIRST, "usage_busy");
        assert.equal(body.messageId, 1);
        stored = [row(1, "user", body.content), row(2, "assistant", "다시 받은 답변")];
        return { ...queueReply("다시 받은 답변"), sessionId: FIRST };
      } };
    const runner = hookRunner(); let chat = await renderEffects(runner);
    chat.onEditMessage(1); typeAndSend(runner, "수정 질문"); chat = await renderEffects(runner);
    stored = removed ? [] : [row(1, "user", "기존 질문"), row(2, "assistant", "다른 탭에서 받은 답변")];
    active = false; global.__queueTimer(); chat = await renderEffects(runner);
    assert.equal(chat.queuePaused, true); assert.equal(edits, 1); assert.equal(chat.queued.length, 1);
    global.window.confirm = () => false; chat.onResumeQueue(); chat = await renderEffects(runner);
    assert.equal(chat.queuePaused, true); assert.equal(edits, 1);
    if (removed) {
      assert.match(chat.notice, /질문이 없어졌어요/); chat.onRemoveQueued(chat.queued[0].id);
    } else {
      assert.equal(chat.messages.at(-1).content, "다른 탭에서 받은 답변");
      global.window.confirm = () => true; chat.onResumeQueue(); chat = await renderEffects(runner);
      assert.equal(edits, 2); assert.equal(chat.queued.length, 0);
    }
    runner.unmount();
  }
});

test("failed or stopped answers pause the remaining queue without silently submitting dependent follow-ups", async () => {
  for (const stop of [false, true]) {
    global.__memberAuth = { status: "anonymous", user: null };
    let reject, calls = 0;
    global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: (_, body, signal) => {
      if (++calls > 1) return Promise.resolve(queueReply("재개 완료"));
      return new Promise((resolve, fail) => { reject = fail; signal.addEventListener("abort", () => fail(new Error("stopped")), { once: true }); });
    } };
    const runner = hookRunner(); await renderEffects(runner); typeAndSend(runner, "첫 질문"); let chat = typeAndSend(runner, "대기 질문");
    if (stop) chat.onCancel(); else reject(new Error("offline")); chat = await renderEffects(runner);
    assert.equal(chat.queuePaused, true); assert.equal(chat.queued.length, 1); assert.equal(calls, 1);
    chat.onResumeQueue(); chat = await renderEffects(runner); assert.equal(calls, 2); assert.equal(chat.queued.length, 0); runner.unmount();
  }
});

test("queued follow-up uses the map updated by the prior answer, never the old course captured on enqueue", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const first = deferred(), requests = [];
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: (_, body) => {
    requests.push(body); return requests.length === 1 ? first.promise : Promise.resolve(queueReply("후속 답변"));
  } };
  const runner = hookRunner(); let chat = await renderEffects(runner);
  chat.onContextChange({ stadium: "JAMSIL", currentCourse: { places: ["old"] } });
  chat.registerCourseTarget({ stadiumCode: "JAMSIL", stopCount: 1, apply: () => null });
  typeAndSend(runner, "코스 생성"); typeAndSend(runner, "카페만 바꿔줘");
  runner.render().onContextChange({ stadium: "JAMSIL", currentCourse: { places: ["latest"] } });
  first.resolve(queueReply("새 코스")); await renderEffects(runner);
  assert.deepEqual(requests[1].context.currentCourse.places, ["latest"]); runner.unmount();
});

test("identity change drops queued requests and late completion cannot submit them for the next account", async () => {
  global.__memberAuth = { status: "authenticated", user: { id: 100 } };
  const first = deferred(); let calls = 0;
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: () => { calls++; return first.promise; } };
  const runner = hookRunner(); await renderEffects(runner); typeAndSend(runner, "첫 계정"); typeAndSend(runner, "예약");
  global.__memberAuth = { status: "authenticated", user: { id: 200 } }; let chat = await renderEffects(runner);
  assert.equal(chat.queued.length, 0); first.resolve(queueReply("늦은 답변")); chat = await renderEffects(runner);
  assert.equal(calls, 1); assert.equal(chat.messages.length, 0); runner.unmount();
});

test("cancelling the queue while the usage check is pending never sends the cancelled item", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const first = deferred(), usage = deferred(); let calls = 0;
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], fetchChatUsage: () => usage.promise,
    sendChatMessage: () => { calls++; return first.promise; } };
  const runner = hookRunner(); await renderEffects(runner); typeAndSend(runner, "현재"); typeAndSend(runner, "취소할 예약");
  first.resolve(queueReply("완료")); let chat = await renderEffects(runner); chat.onRemoveQueued(chat.queued[0].id);
  usage.resolve({ active_turn: false, can_send: true }); chat = await renderEffects(runner);
  assert.equal(chat.queued.length, 0); assert.equal(calls, 1); runner.unmount();
});

test("a paused queue stays with its conversation and cannot leak into another room", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  let reject, calls = 0;
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], sendChatMessage: () => { calls++; return new Promise((resolve, fail) => { reject = fail; }); } };
  const runner = hookRunner(); await renderEffects(runner); typeAndSend(runner, "기존 대화"); typeAndSend(runner, "기존 예약");
  reject(new Error("offline")); let chat = await renderEffects(runner); const room = chat.activeConversationId;
  chat.onReset(); chat = await renderEffects(runner); assert.equal(chat.queued.length, 0);
  chat.onSelectConversation(room); chat = await renderEffects(runner);
  assert.equal(chat.queued[0].content, "기존 예약"); assert.equal(chat.queuePaused, true); assert.equal(calls, 1); runner.unmount();
});

test("exhausted usage preserves queued text and pauses instead of repeatedly posting", async () => {
  global.__memberAuth = { status: "anonymous", user: null };
  const first = deferred(); let calls = 0;
  global.__chatApi = { ...baseApi, listChatSessions: async () => [], fetchChatUsage: async () => ({ active_turn: false, can_send: false }),
    sendChatMessage: () => { calls++; return first.promise; } };
  const runner = hookRunner(); await renderEffects(runner); typeAndSend(runner, "현재"); typeAndSend(runner, "남길 예약");
  first.resolve(queueReply("완료")); const chat = await renderEffects(runner);
  assert.equal(chat.queuePaused, true); assert.equal(chat.queued[0].content, "남길 예약"); assert.equal(calls, 1); runner.unmount();
});

test("queued input keeps its own groups and leaves the next composer alone", async () => {
  const first = deferred(), sent = [];
  const runner = await urlRunner({ fetchChatUsage: async () => ({ can_send: true, active_turn: null }), sendChatMessage: (_, body) => {
    sent.push(body); return sent.length === 1 ? first.promise : Promise.resolve(urlReply);
  } });
  try {
    let chat = runner.render(); chat.onToolGroupsChange(["rules"]);
    typeAndSend(runner, "first");
    chat = runner.render(); chat.onDraftChange("queued notes"); chat = runner.render(); chat.onSend();
    chat = runner.render(); assert.equal(chat.queued.length, 1);
    chat.onDraftChange("next composer");
    first.resolve(urlReply); await renderEffects(runner); await renderEffects(runner);
    assert.equal(sent.length, 2); assert.equal(sent[1].content, "queued notes");
    assert.deepEqual(sent[1].toolGroupIds, ["rules"]); assert.equal(runner.render().draft, "next composer");
  } finally { runner.unmount(); }
});
