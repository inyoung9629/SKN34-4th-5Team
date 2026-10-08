import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import vm from "node:vm";
const require = createRequire(import.meta.url);
const ts = require("typescript"), React = require("react");
function load(path, mocks) {
  const context = { exports: {}, Error, require(id) {
    if (id in mocks) return mocks[id];
    if (id === "react" || id === "react/jsx-runtime") return require(id);
    if (id.endsWith(".css")) return {};
    throw new Error(id);
  } };
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(path, import.meta.url), "utf8"), {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, esModuleInterop: true },
  }).outputText, context);
  return context.exports;
}
function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!React.isValidElement(tree)) return [];
  return [tree, ...nodes(tree.props.children)];
}
const member = { id: 18, is_active: true, is_staff: true, is_superuser: false };
function reportPanel(status, { busy = false, hidden = false, fail = false } = {}) {
  const calls = [];
  const report = { id: 1, status, created_at: "2026-10-06T00:00:00Z", reason: "spam", reporter: "회원", post: { post_number: "1", source_id: "free-1", board: "free", title: "글", author: "작성자", is_hidden: hidden } };
  const values = ["", "", busy, null, { count: 1, results: [report] }, "", false, "", 1, 0];
  let index = 0;
  const panel = load("../components/admin-panels.tsx", {
    react: { ...React, useState() { const i = index++; return [values[i], value => { values[i] = value; }]; }, useEffect() {}, useRef: () => ({ current: null }) },
    "next/link": { __esModule: true, default: "a" },
    "@/lib/api/auth": {}, "@/lib/member-policy": {}, "@/lib/team-community": {},
    "@/lib/api/admin-community": { reportReasonLabel: {}, reportStatusLabel: {}, sanctionLabel: {}, async actOnAdminReport(...args) { calls.push(args); if (fail) throw new Error("처리 실패"); } },
  }).AdminReportsPanel();
  return { values, calls, button: nodes(panel).find(n => n.type === "button" && ["보류", "보류 취소"].includes(n.props.children)) };
}
test("held reports cancel hold; pending reports can be held", async () => {
  for (const [status, action, label] of [["pending", "hold", "보류"], ["held", "unhold", "보류 취소"]]) {
    const view = reportPanel(status);
    assert.equal(view.button.props.children, label);
    assert.equal(view.button.props.disabled, false);
    view.button.props.onClick();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(view.calls[0][1], action);
    assert.ok(view.values[0].includes(action === "unhold" ? "처리 대기" : "보류"));
  }
});
test("hold changes are disabled for hidden reports and pending requests", () => {
  for (const [status, options] of [["hidden", {}], ["held", { hidden: true }], ["held", { busy: true }]]) {
    assert.equal(reportPanel(status, options).button.props.disabled, true);
  }
});
test("failed hold cancellation shows an error, not a success notice", async () => {
  const view = reportPanel("held", { fail: true });
  view.button.props.onClick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(view.values[0], "");
  assert.equal(view.values[1], "처리 실패");
});
const panels = { AdminMembersPanel: "members-panel", AdminPostsPanel: "posts-panel", AdminReportsPanel: "reports-panel" };
function dashboard(identity, tab = "members", reload = () => {}) {
  return load("../app/admin/page.tsx", {
    "next/link": { __esModule: true, default: "a" },
    "next/navigation": { useSearchParams: () => new URLSearchParams({ tab }) },
    "@/lib/member-auth": { useMemberAuth: () => ({ ...identity, reload }) },
    "@/components/admin-panels": panels,
  }).AdminDashboard();
}
test("dashboard blocks panels while unauthenticated, unavailable, loading or unauthorized", () => {
  for (const identity of [
    { status: "anonymous", user: null }, { status: "loading", user: member },
    { status: "unavailable", user: member },
    { status: "authenticated", user: { ...member, is_staff: false } },
    { status: "authenticated", user: { ...member, is_active: false } },
  ]) assert.equal(nodes(dashboard(identity)).some(n => Object.values(panels).includes(n.type)), false);
});
test("each menu mounts only its panel; invalid tabs default to members", () => {
  for (const [tab, panel] of [["members", "members-panel"], ["posts", "posts-panel"], ["reports", "reports-panel"], ["invalid", "members-panel"]]) {
    const tree = nodes(dashboard({ status: "authenticated", user: member }, tab));
    assert.deepEqual(tree.filter(n => Object.values(panels).includes(n.type)).map(n => n.type), [panel]);
    assert.equal(tree.filter(n => n.props["aria-current"] === "page").length, 1);
    assert.equal(tree.some(n => n.props.href === "/admin/feedback"), false);
  }
  assert.ok(nodes(dashboard({ status: "authenticated", user: { ...member, is_superuser: true } })).some(n => n.props.href === "/admin/feedback"));
});
test("auth failure has a working retry and guests have login entry", () => {
  let calls = 0;
  nodes(dashboard({ status: "unavailable" }, "members", () => { calls++; })).find(n => n.type === "button").props.onClick();
  assert.equal(calls, 1);
  assert.ok(nodes(dashboard({ status: "anonymous" })).some(n => n.props.href === "/login?next=admin"));
});

test("feedback entry shares the primary menu styling and is exclusive to superusers", () => {
  for (const is_superuser of [false, true]) {
    const tree = nodes(dashboard({ status: "authenticated", user: { ...member, is_superuser } }));
    const menu = tree.find(n => n.type === "nav" && n.props["aria-label"] === "관리 메뉴");
    const links = nodes(menu).filter(n => n.props.href);
    assert.equal(links.length, is_superuser ? 4 : 3);
    const feedback = tree.filter(n => n.props.href === "/admin/feedback");
    assert.equal(feedback.length, is_superuser ? 1 : 0);
    if (is_superuser) {
      assert.equal(links.at(-1), feedback[0]);
      assert.equal(feedback[0].props.className, links[0].props.className);
    }
  }
});
test("category menu ends with Admin only for authenticated active staff; header has no duplicate", () => {
  for (const [status, user, allowed] of [["authenticated", member, true], ["anonymous", null, false], ["loading", member, false], ["unavailable", member, false], ["authenticated", { ...member, is_staff: false }, false], ["authenticated", { ...member, is_active: false }, false]]) {
    const header = load("../components/member-header-actions.tsx", {
      react: { ...React, useState: value => [value, () => {}], useRef: () => ({ current: null }), useEffect() {} },
      "next/link": { __esModule: true, default: "a" }, "next/navigation": { useRouter: () => ({}) },
      "@/lib/member-auth": { useMemberAuth: () => ({ status, user }) }, "@/lib/member-auth-request": {},
    });
    assert.equal(nodes(header.MemberHeaderActions()).some(n => n.props.href === "/admin"), false);
    let closed = false;
    const menu = load("../components/header-menu.tsx", {
      react: { ...React, useState: () => [true, value => { closed = value === false; }], useRef: () => ({ current: null }), useEffect() {}, useId: () => "test-menu" },
      "next/link": { __esModule: true, default: "a" },
      "next/image": { __esModule: true, default: "img" },
      "next/navigation": { usePathname: () => "/admin" },
      "@/lib/member-auth": { useMemberAuth: () => ({ status, user }) },
      "./chat-provider": { useChat: () => ({ onExpand() {} }) },
      "./icons": { Icon: "icon", CapBot: "cap-bot" },
    }).HeaderMenu();
    const tree = nodes(menu.type(menu.props));
    const admin = tree.find(n => n.props.href === "/admin");
    assert.equal(Boolean(admin), allowed);
    if (allowed) {
      const actions = tree.filter(n => n.type === "a" || n.type === "button");
      assert.equal(actions.at(-1), admin);
      assert.equal(admin.props["aria-current"], "page");
      admin.props.onClick();
      assert.equal(closed, true);
    }
  }
});
