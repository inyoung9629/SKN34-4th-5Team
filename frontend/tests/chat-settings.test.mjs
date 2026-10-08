import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-chat-settings-"));
after(() => rmSync(scratch, { recursive: true, force: true }));
symlinkSync(join(frontend, "node_modules"), join(scratch, "node_modules"), "dir");
const source = readFileSync(join(frontend, "components/chat-usage.tsx"), "utf8")
  .replace('from "react"', 'from "./hooks"')
  .replace('from "@/lib/chat/client"', 'from "./client"');
writeFileSync(join(scratch, "icons.js"), ts.transpileModule(readFileSync(join(frontend, "components/icons.tsx"), "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText);
writeFileSync(join(scratch, "usage.js"), ts.transpileModule(source, {
  fileName: "usage.tsx", compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText);
writeFileSync(join(scratch, "hooks.js"), `exports.useState = (...args) => global.__hooks.useState(...args); exports.useEffect = (...args) => global.__hooks.useEffect(...args);`);
writeFileSync(join(scratch, "client.js"), `exports.fetchChatUsage = (...args) => global.__usageFetch(...args);`);
const require = createRequire(join(scratch, "entry.cjs"));
const { ChatUsage } = require("./usage.js");
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

const workspaceSource = readFileSync(join(frontend, "components/chat-workspace.tsx"), "utf8");
const workspaceAst = ts.createSourceFile("workspace.tsx", workspaceSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const workspaceBody = workspaceAst.statements.filter(node => !ts.isImportDeclaration(node)).map(node => node.getText(workspaceAst)).join("\n");
const workspaceCode = ts.transpileModule(`const { Link, Image, useEffect, useRef, useState, useMemberAuth, useChat, Icon, ChatUsage, ChatQueue, ChatCoursePreferences, ChatSubAgentStatus, ChatQuestions, ChatWriterOffer, ChatComposerTools, ChatAttachmentCards, ChatInlineInput, chatComposerContent, MAX_MESSAGE_LENGTH } = global.__workspaceDependencies;\n${workspaceBody}`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
writeFileSync(join(scratch, "workspace.js"), workspaceCode);
let identity;
const Link = () => null;
global.__workspaceDependencies = {
  chatComposerContent: text => text.trim(),
  ChatComposerTools: () => null, ChatAttachmentCards: () => null, ChatInlineInput: () => null,
  Link, Image: () => null, Icon: () => null, ChatUsage, ChatQueue: () => null, ChatCoursePreferences: () => null, ChatSubAgentStatus: () => null, ChatQuestions: () => null, ChatWriterOffer: () => null, MAX_MESSAGE_LENGTH: 2000,
  useState: (...args) => global.__hooks.useState(...args),
  useEffect: (...args) => global.__hooks.useEffect(...args),
  useRef: (...args) => global.__hooks.useRef(...args),
  useMemberAuth: () => identity,
  useChat: () => ({ queued: [], editingQueuedId: null, attachments: [], toolGroupIds: [], conversations: [], messages: [], draft: "", timeline: [], editingMessageId: null, status: { ready: true } }),
};
const { ChatWorkspace } = require("./workspace.js");

function runner(Component = ChatUsage) {
  const states = [], effects = [];
  let cursor = 0, pending = [];
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!(index in states)) states[index] = initial;
      return [states[index], value => { states[index] = typeof value === "function" ? value(states[index]) : value; }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in states)) states[index] = { current: initial };
      return states[index];
    },
    useEffect(effect, dependencies) {
      const index = cursor++;
      if (!effects[index] || !dependencies.every((value, key) => Object.is(value, effects[index].dependencies[key]))) {
        const old = effects[index];
        effects[index] = { dependencies };
        pending.push(() => { old?.cleanup?.(); effects[index].cleanup = effect(); });
      }
    },
  };
  return {
    states,
    render(props) { global.__hooks = hooks; cursor = 0; return Component(props); },
    flush() { const next = pending; pending = []; next.forEach(run => run()); },
    unmount() { effects.forEach(effect => effect?.cleanup?.()); },
  };
}

function find(tree, predicate) {
  if (!tree || typeof tree !== "object") return;
  if (predicate(tree)) return tree;
  for (const child of [tree.props?.children].flat(Infinity)) {
    const match = find(child, predicate);
    if (match) return match;
  }
}

const allowance = { plan: "member", remaining_credits: "4", limit_tokens: 10000, remaining_tokens: 4000, tokens_per_credit: 1000, used_tokens: 5000, reserved_tokens: 1000, resets_at: null, can_send: true, active_turn: false, unknown_calls: 0, accounting_state: "known" };

test("remaining allowance meter renders full, partial, reserved, zero and bounded balances", async () => {
  for (const [remaining, limit, expected] of [[10000, 10000, 100], [4000, 10000, 40], [2900, 10000, 29], [2901, 10000, 29.01], [0, 10000, 0], [200, 0, 0], [12000, 10000, 100], [-100, 10000, 0]]) {
    global.__usageFetch = async () => ({ ...allowance, remaining_tokens: remaining, limit_tokens: limit });
    const view = runner();
    view.render({ mode: "member", refreshKey: 0 }); view.flush(); await tick();
    const tree = view.render({ mode: "member", refreshKey: 0 });
    const meter = find(tree, node => node.type === "meter");
    assert.ok(meter, "remaining allowance must have native meter semantics");
    assert.equal(meter.props.value, expected);
    assert.equal(find(tree, node => node.type === "strong").props.children[0], Math.floor(expected));
    assert.equal(meter.props.min, 0);
    assert.equal(meter.props.max, 100);
    assert.match(meter.props["aria-label"], /남은/);
    assert.match(meter.props["aria-valuetext"], /실제 사용량 정산.*1,000/);
    assert.equal(find(tree, node => node.props?.href), undefined);
    view.unmount();
  }
});

test("usage shows only the actual next refill date in the policy timezone without details", async () => {
  const oldTimezone = process.env.TZ;
  process.env.TZ = "America/Los_Angeles";
  try {
    for (const [timezone, expected] of [["Asia/Seoul", "2026. 11. 1."], [null, "2026. 11. 1."], ["UTC", "2026. 10. 31."]]) {
      global.__usageFetch = async () => ({ ...allowance, timezone, resets_at: "2026-11-01T00:00:00+09:00" });
      const view = runner();
      view.render({ mode: "member", refreshKey: 0 }); view.flush(); await tick();
      const tree = view.render({ mode: "member", refreshKey: 0 });
      assert.equal(find(tree, node => node.props?.className === "workspace-usage-policy").props.children, `다음 충전: ${expected}`);
      assert.equal(find(tree, node => ["details", "summary"].includes(node.type)), undefined);
      assert.doesNotMatch(JSON.stringify(tree), /제공량 상세|매월 1일|다시 채워져요/);
      assert.equal(find(tree, node => node.type === "meter").props.title, undefined);
      view.unmount();
    }
  } finally {
    if (oldTimezone === undefined) delete process.env.TZ;
    else process.env.TZ = oldTimezone;
  }
  assert.doesNotMatch(readFileSync(join(frontend, "styles/chat-workspace.css"), "utf8"), /workspace-usage-detail/);
});

test("usage icon refresh is disabled during loading and retries errors without a fake balance", async () => {
  let calls = 0;
  global.__usageFetch = async () => { if (++calls === 1) throw new Error("연결 실패"); return allowance; };
  const view = runner();
  let tree = view.render({ mode: "member", refreshKey: 0 });
  const action = find(tree, node => node.type === "button");
  assert.equal(action.props.disabled, true);
  assert.match(action.props["aria-label"], /확인 중/);
  assert.equal(find(tree, node => node.type === "meter"), undefined);
  view.flush(); await tick();
  tree = view.render({ mode: "member", refreshKey: 0 });
  assert.ok(find(tree, node => node.props?.role === "alert"));
  const retry = find(tree, node => node.type === "button");
  assert.equal(retry.props.disabled, false);
  assert.match(retry.props.title, /다시/);
  assert.equal(find(tree, node => node.type === "meter"), undefined);
  retry.props.onClick();
  view.render({ mode: "member", refreshKey: 0 }); view.flush(); await tick();
  assert.ok(find(view.render({ mode: "member", refreshKey: 0 }), node => node.type === "meter"));
  assert.equal(calls, 2);
  view.unmount();
});

test("usage ignores aborted success/finally and refresh replaces the balance", async () => {
  const old = deferred(), fresh = deferred(), calls = [];
  global.__usageFetch = (mode, signal) => { calls.push({ mode, signal }); return calls.length === 1 ? old.promise : fresh.promise; };
  const view = runner();
  view.render({ mode: "member", refreshKey: 0 }); view.flush(); await tick();
  view.render({ mode: "member", refreshKey: 1 }); view.flush(); await tick();
  assert.equal(calls[0].signal.aborted, true);
  old.resolve({ remaining_credits: "old private balance" }); await tick();
  assert.equal(view.states[0], null);
  assert.equal(view.states[2], true);
  fresh.resolve({ remaining_credits: "new balance" }); await tick();
  assert.equal(view.states[0].remaining_credits, "new balance");
  assert.equal(view.states[2], false);
  view.unmount();
});

test("identity unmount aborts manual refresh and a new guest never inherits member usage", async () => {
  const requests = [];
  global.__usageFetch = (mode, signal) => { const request = deferred(); requests.push({ ...request, signal, mode }); return request.promise; };
  const member = runner();
  let tree = member.render({ mode: "member", refreshKey: 0 }); member.flush(); await tick();
  requests[0].resolve({ plan: "member", remaining_credits: "42", remaining_tokens: 42000, limit_tokens: 50000, tokens_per_credit: 1000, used_tokens: 8000, reserved_tokens: 0, resets_at: null, can_send: true }); await tick();
  tree = member.render({ mode: "member", refreshKey: 0 });
  find(tree, node => node.type === "button" && /새로고침/.test(node.props["aria-label"] ?? node.props.children)).props.onClick();
  member.render({ mode: "member", refreshKey: 0 }); member.flush(); await tick();
  member.unmount();
  assert.equal(requests[1].signal.aborted, true);
  identity = { status: "anonymous", user: null };
  const guest = runner(ChatWorkspace);
  const guestTree = guest.render(); guest.flush(); await tick();
  requests[1].resolve({ remaining_credits: "private late balance" }); await tick();
  assert.equal(find(guestTree, node => node.type === ChatUsage), undefined);
  assert.equal(requests.length, 2, "guest workspace must not mount usage or make a request");
  assert.doesNotMatch(JSON.stringify(guestTree), /private late balance|42,000/);
  guest.unmount();
});

test("shared desktop/mobile sidebar renders truthful identity and gates member usage on transitions", () => {
  for (const status of ["anonymous", "authenticated", "loading", "error"]) {
    identity = { status, user: { id: 7, nickname: "회원닉", avatar: "/member.png" } };
    const view = runner(ChatWorkspace);
    let tree = view.render();
    const desktop = find(tree, node => node.type === "aside");
    const mobile = find(tree, node => node.props?.className === "workspace-drawer-content");
    for (const sidebar of [desktop, mobile]) {
      const login = find(sidebar, node => node.type === Link && node.props.href === "/login");
      const profile = find(sidebar, node => node.props?.className === "workspace-profile");
      if (status === "anonymous") {
        assert.ok(login);
        assert.equal(find(login, node => node.type === "span").props.children, "로그인");
        assert.equal(find(login, node => node.props?.name === "login").props.size, 20);
        assert.equal(profile, undefined);
        assert.equal(find(sidebar, node => node.props?.href === "/signup"), undefined);
      } else if (status === "authenticated") {
        assert.equal(login, undefined);
        assert.equal(profile.props["aria-haspopup"], "dialog");
        assert.equal(find(profile, node => node.type === "strong").props.children, "회원닉");
        profile.props.onClick({ currentTarget: { focus() {} } });
      } else {
        assert.equal(login, undefined);
        assert.equal(profile.props.disabled, true);
        assert.equal(profile.props.onClick, undefined);
        assert.equal(find(profile, node => node.props?.role === "status").props.children, status === "loading" ? "계정 확인 중" : "계정 확인 불가");
      }
    }
    tree = view.render();
    const usage = find(tree, node => node.type === ChatUsage);
    assert.equal(Boolean(usage), status === "authenticated");
    if (usage) {
      assert.equal(usage.props.mode, "member");
      assert.equal(usage.key, "member:7");
      view.flush();
      let closed = 0;
      const dialog = find(tree, node => node.props?.className === "workspace-settings");
      dialog.props.ref.current = { close() { closed++; }, open: true };
      for (const nextStatus of ["loading", "anonymous"]) {
        identity = { status: nextStatus, user: identity.user };
        tree = view.render();
        assert.equal(find(tree, node => node.type === ChatUsage), undefined);
        assert.equal(find(tree, node => node.props?.href === "/mypage?tab=profile"), undefined);
        view.flush();
      }
      assert.equal(closed, 1);
    }
    view.unmount();
  }
  const css = readFileSync(join(frontend, "styles/chat-workspace.css"), "utf8");
  assert.match(css, /\.workspace-login \{[^}]*min-height: 46px/);
  assert.match(css, /\.workspace-login:focus-visible \{[^}]*outline:/);
});

test("sidebar and modal share trimmed MemberAuth name fallbacks without transitional identity leaks", () => {
  for (const [user, expected] of [
    [{ nickname: " 닉네임 ", first_name: " 이름 ", username: " 계정 " }, "닉네임"],
    [{ nickname: " \t", first_name: " 이름 ", username: " 계정 " }, "이름"],
    [{ nickname: "", first_name: " \t", username: " 계정 " }, "계정"],
    [{ nickname: " ", first_name: "", username: " " }, "회원"],
  ]) {
    identity = { status: "authenticated", user: { id: 7, ...user } };
    const view = runner(ChatWorkspace);
    const tree = view.render();
    for (const sidebar of [find(tree, node => node.type === "aside"), find(tree, node => node.props?.className === "workspace-drawer-content")]) {
      assert.equal(find(sidebar, node => node.type === "strong").props.children, expected);
    }
    assert.equal(find(tree, node => node.props?.className === "workspace-account-name").props.children, expected);
    view.unmount();
    for (const status of ["loading", "error", "anonymous"]) {
      identity = { status, user: { id: 7, nickname: "private-nickname", first_name: "private-name", username: "private-username" } };
      const transition = runner(ChatWorkspace);
      const hidden = transition.render();
      assert.doesNotMatch(JSON.stringify(hidden), /private-nickname|private-name|private-username/);
      assert.equal(find(hidden, node => node.type === ChatUsage), undefined);
      transition.unmount();
    }
  }
});

test("shared desktop/mobile profile shows two authenticated roles without stale identity leaks", () => {
  for (const [is_staff, is_superuser, expected] of [[false, false, "일반 유저"], [true, false, "관리자"], [false, true, "관리자"], [true, true, "관리자"]]) {
    identity = { status: "authenticated", user: { id: 7, nickname: "private-name", avatar: "/private-avatar.png", is_staff, is_superuser } };
    const view = runner(ChatWorkspace);
    let tree = view.render();
    const settings = find(tree, node => node.props?.className === "workspace-settings");
    const drawer = find(tree, node => node.props?.className === "workspace-drawer");
    let opened = 0, closed = 0;
    settings.props.ref.current = { showModal() { opened++; } };
    drawer.props.ref.current = { close() { closed++; } };
    for (const [index, sidebar] of [find(tree, node => node.type === "aside"), find(tree, node => node.props?.className === "workspace-drawer-content")].entries()) {
      const profile = find(sidebar, node => node.props?.className === "workspace-profile");
      assert.equal(find(profile, node => node.type === "small").props.children, expected);
      assert.equal(find(profile, node => node.type === "strong").props.children, "private-name");
      assert.equal(find(profile, node => node.props?.src).props.src, "/private-avatar.png");
      assert.equal(profile.type, "button");
      assert.equal(profile.props.type, "button");
      assert.equal(profile.props["aria-label"], "private-name 설정 열기");
      assert.equal(profile.props["aria-haspopup"], "dialog");
      assert.ok(profile.props.ref);
      profile.props.onClick({ currentTarget: { focus() {} } });
      assert.equal(opened, 1);
      assert.equal(closed, index);
    }
    for (const status of ["loading", "error", "anonymous", "authenticated"]) {
      identity = { status, user: status === "authenticated" ? null : { ...identity.user, is_staff: true, is_superuser: true } };
      tree = view.render();
      assert.doesNotMatch(JSON.stringify(tree), /관리자|일반 유저|private-name|private-avatar/);
      assert.equal(find(tree, node => node.type === "small"), undefined);
      assert.equal(find(tree, node => node.type === ChatUsage), undefined);
      assert.equal(find(tree, node => node.props?.["aria-haspopup"] === "dialog" && node.props?.className === "workspace-profile"), undefined);
    }
    view.unmount();
  }
});

test("native close handlers ignore reopened dialogs and restore genuinely closed openers", () => {
  const workspace = readFileSync(join(frontend, "components/chat-workspace.tsx"), "utf8");
  const ast = ts.createSourceFile("workspace.tsx", workspace, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const component = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name.text === "ChatWorkspace");
  const handlers = component.body.statements.filter(node => ts.isFunctionDeclaration(node) && ["openSettings", "onSettingsClose", "onDrawerClose"].includes(node.name.text));
  const context = {
    usageMode: "member",
    settingsRef: { current: { open: false, showModal() { this.open = true; } } },
    drawerRef: { current: { open: false, close() { this.open = false; }, showModal() { this.open = true; } } },
    settingsOpenerRef: { current: null }, settingsFromDrawerRef: { current: false },
    drawerFocusRef: { current: "menu" },
  };
  const focused = [];
  for (const name of ["profileRef", "mobileProfileRef", "menuRef", "inputRef"]) context[name] = { current: { focus() { focused.push(name); } } };
  let settingsOpen = false, mobile = false;
  context.setSettingsOpen = value => { settingsOpen = value; };
  context.window = { matchMedia: () => ({ matches: mobile }) };
  const code = ts.transpileModule(handlers.map(node => node.getText(ast)).join("\n"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const { openSettings, onSettingsClose, onDrawerClose } = new Function(...Object.keys(context), `${code}; return { openSettings, onSettingsClose, onDrawerClose };`)(...Object.values(context));

  openSettings(context.profileRef.current, false);
  context.settingsRef.current.open = false;
  openSettings(context.profileRef.current, false);
  onSettingsClose(); // The old queued close arrives after the native reopen.
  assert.equal(settingsOpen, true);
  assert.deepEqual(focused, []);
  context.settingsRef.current.open = false;
  onSettingsClose();
  assert.equal(settingsOpen, false);
  assert.equal(focused.pop(), "profileRef");

  mobile = true;
  context.drawerRef.current.open = true;
  openSettings(context.mobileProfileRef.current, true);
  onDrawerClose();
  assert.equal(context.settingsRef.current.open, true);
  assert.equal(context.drawerRef.current.open, false);
  onDrawerClose(); // Another queued drawer close must not steal modal focus.
  assert.deepEqual(focused, []);
  onSettingsClose();
  assert.equal(settingsOpen, true);
  assert.equal(context.settingsFromDrawerRef.current, true);
  context.settingsRef.current.open = false;
  onSettingsClose();
  assert.equal(settingsOpen, false);
  assert.equal(context.drawerRef.current.open, true);
  assert.equal(focused.pop(), "mobileProfileRef");
  onDrawerClose(); // A stale drawer close after focus restoration is harmless.
  assert.deepEqual(focused, []);
  context.drawerRef.current.open = false;
  onDrawerClose();
  assert.equal(focused.pop(), "menuRef");
});

test("workspace settings use existing identity, native dialog handoff and preserve chat controls", () => {
  const workspace = readFileSync(join(frontend, "components/chat-workspace.tsx"), "utf8");
  assert.match(workspace, /status: authStatus, user.*useMemberAuth/);
  assert.match(workspace, /user\?\.nickname/);
  assert.match(workspace, /user\.avatar/);
  assert.match(workspace, /\/images\/default-avatar\.svg/);
  assert.match(workspace, /계정 확인 중/);
  assert.match(workspace, /계정 확인 불가/);
  assert.match(workspace, /ref=\{mobile \? mobileProfileRef : profileRef\}/);
  assert.match(workspace, /if \(mobile\) drawerRef\.current\?\.close\(\)/);
  assert.match(workspace, /onClose=\{onDrawerClose\}/);
  assert.match(workspace, /onClose=\{onSettingsClose\}/);
  assert.match(workspace, /mobileProfileRef\.current\?\.focus\(\)/);
  assert.match(workspace, /settingsOpenerRef\.current/);
  assert.match(workspace, /settingsOpen && <ChatUsage key=\{usageIdentity\}/);
  assert.match(workspace, /`member:\$\{user!\.id\}`/);
  assert.doesNotMatch(workspace.split('<header className="workspace-topbar">')[1].split('</header>')[0], /ChatUsage/);
  assert.doesNotMatch(workspace, /직관 준비 이어가기|workspace-quick-links/);
  assert.match(workspace, /href="\/mypage\?tab=profile"/);
  assert.match(workspace, /구독 요금제는 준비 중/);
  const settings = workspace.split('<dialog ref={settingsRef}')[1].split('</dialog>')[0];
  const ast = ts.createSourceFile("settings.tsx", `<div ${settings}</div>`, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const links = [];
  function visit(node) {
    if (ts.isJsxElement(node) && node.openingElement.tagName.getText(ast) === "Link") links.push(node);
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.doesNotMatch(settings, /href="\/(login|signup)"/);
  for (const [href, label, icon] of [["/mypage?tab=profile", "회원 정보 관리", "book"]]) {
    const link = links.find(node => node.openingElement.attributes.properties.some(prop => prop.name?.getText(ast) === "href" && prop.initializer?.text === href));
    assert.ok(link, `${href} must retain native Link navigation`);
    const props = Object.fromEntries(link.openingElement.attributes.properties.map(prop => [prop.name.getText(ast), prop.initializer?.text]));
    assert.equal(props["aria-label"], label);
    assert.equal(props.title, label);
    assert.equal(props.className, "workspace-icon-button");
    assert.match(link.getText(ast), new RegExp(`Icon name="${icon}"`));
  }
  for (const retained of ['<ChatInlineInput id="workspace-question"', "chat.onSend()", "chat.onCancel", "chat.onSelectConversation", "chat.error", "chat.streaming"]) assert.ok(workspace.includes(retained));
});


test("positive fractional credit is sendable; busy and unknown are not exhausted", async () => {
  for (const active of [false, true]) {
    global.__usageFetch = async () => ({ ...allowance, remaining_tokens: 1, remaining_credits: "0.001",
      active_turn: active, can_send: !active, unknown_calls: 1, accounting_state: "unknown" });
    const view = runner();
    view.render({ mode: "guest", refreshKey: 0 }); view.flush(); await tick();
    const tree = view.render({ mode: "guest", refreshKey: 0 });
    assert.match(JSON.stringify(tree), /확인된 토큰만/);
    assert.doesNotMatch(JSON.stringify(tree), /사용 가능한 제공량이 없어요|예약된 양/);
    assert.equal(/다음 질문을 예약할 수 있어요/.test(JSON.stringify(tree)), active);
    view.unmount();
  }
});
