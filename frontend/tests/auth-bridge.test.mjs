import assert from "node:assert/strict";
import { after, beforeEach, test } from "node:test";
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-auth-bridge-"));
after(() => rmSync(scratch, { recursive: true, force: true }));
const source = readFileSync(join(root, "lib/member-auth-request.ts"), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } });
writeFileSync(join(scratch, "member-auth-request.js"), outputText);
const require = createRequire(join(scratch, "test.cjs"));
const sessionValues = new Map();
global.sessionStorage = { getItem: name => sessionValues.get(name) ?? null, setItem: (name, value) => sessionValues.set(name, value), removeItem: name => sessionValues.delete(name) };
const { clearMemberTokens, createMemberRequestGate, isCurrentMember, loadLatestMember, logoutMember, memberFetch, normalizeMemberEmail, saveMemberTokens } = require("./member-auth-request.js");
beforeEach(() => clearMemberTokens());

test("auth callers use the backend-shaped public paths", () => {
  const sources = ["app/login/page.tsx", "app/signup/page.tsx", "app/mypage/page.tsx", "components/member-account-settings.tsx", "components/member-header-actions.tsx", "lib/member-auth-request.ts", "lib/api/auth.ts"].map(name => readFileSync(join(root, name), "utf8")).join("\n");
  for (const path of ["/api/v1/auth/signin", "/api/v1/auth/signup/", "/api/v1/auth/user", "/api/v1/auth/logout", "/api/v1/auth/username/request", "/api/v1/auth/password/request", "/api/v1/auth/password", "/api/v1/auth/email/request", "/api/v1/auth/email/verify"]) assert.match(sources, new RegExp(path.replaceAll("/", "\\/")));
  assert.doesNotMatch(sources, /\/team-auth\//);
  assert.match(sources, /\/api\/v1\/auth\/token\/refresh\//);
  assert.match(readFileSync(join(root, "app/signup/page.tsx"), "utf8"), /re_password: values\.passwordConfirm/);
  assert.doesNotMatch(readFileSync(join(root, "lib/member-auth-request.ts"), "utf8"), /localStorage/);
});

test("member admin uses direct Bearer APIs and generated DTOs", () => {
  const page = readFileSync(join(root, "app/admin/page.tsx"), "utf8");
  // 회원 관리 화면은 관리자 페이지와 마스터 관리자 마이페이지가 함께 쓰는 패널로 옮겨졌다
  const panel = readFileSync(join(root, "components/admin-panels.tsx"), "utf8");
  const api = readFileSync(join(root, "lib/api/auth.ts"), "utf8");
  assert.match(page, /<AdminMembersPanel \/>/);
  assert.match(panel, /Promise\.all\(\[getMemberUser\([^)]*\), listAdminMembers\(/);
  assert.match(api, /components\["schemas"\]\["MemberUser"\]/);
  assert.match(api, /components\["schemas"\]\["AdminMember"\]/);
  assert.match(api, /\/api\/v1\/auth\/admin\/members\//);
  assert.match(api, /memberFetch/);
  assert.doesNotMatch(page, /\/admin-api/);
  assert.doesNotMatch(panel, /\/admin-api/);
});

test("current roles and staff navigation come from authoritative identity", () => {
  assert.match(readFileSync(join(root, "app/mypage/page.tsx"), "utf8"), /memberRoleLabel\(user\)/);
  const menu = readFileSync(join(root, "components/member-header-actions.tsx"), "utf8");
  const categoryMenu = readFileSync(join(root, "components/header-menu.tsx"), "utf8");
  assert.match(categoryMenu, /status === "authenticated" && user\?\.is_active && user\.is_staff && \(/);
  assert.match(categoryMenu, /href="\/admin"/);
  assert.match(menu, /href="\/mypage\?tab=profile"/);
  assert.doesNotMatch(menu, /href="\/admin"/);
  assert.doesNotMatch(menu, /href="\/mypage\?tab=(members|manage-posts|reports)"/);
});

test("nginx sends every API path directly to Django", () => {
  const nginx = readFileSync(join(root, "..", "nginx", "nginx.conf"), "utf8");
  assert.match(nginx, /location \/api\/\s*\{[^}]*?proxy_pass http:\/\/backend:8000\s*;/);
  assert.doesNotMatch(nginx, /location \^~ \/api\/auth/);
});

test("an expired access token performs one direct refresh and one retry", async () => {
  saveMemberTokens("expired-access", "refresh-token");
  const calls = [];
  global.fetch = async (url, init) => {
    calls.push({ url: String(url), authorization: new Headers(init?.headers).get("Authorization"), body: init?.body });
    if (String(url) === "/api/v1/auth/token/refresh/") return Response.json({ access: "new-access" });
    if (calls.filter(call => call.url === "/api/v1/auth/user").length === 1) return Response.json({ detail: "만료" }, { status: 401 });
    return Response.json({ id: 1 });
  };
  assert.equal((await memberFetch("/api/v1/auth/user")).status, 200);
  assert.equal(calls.filter(call => call.url === "/api/v1/auth/token/refresh/").length, 1);
  assert.equal(calls.at(-1).authorization, "Bearer new-access");
  assert.deepEqual(JSON.parse(calls[1].body), { refresh: "refresh-token" });
});

test("concurrent 401 retries share refresh without sending Bearer null", async () => {
  saveMemberTokens("expired-access", "refresh-token");
  const authorizations = [];
  let userCalls = 0;
  global.fetch = async (url, init) => {
    if (String(url) === "/api/v1/auth/token/refresh/") return Response.json({ access: "new-access" });
    authorizations.push(new Headers(init?.headers).get("Authorization"));
    userCalls += 1;
    return userCalls <= 2 ? Response.json({ detail: "만료" }, { status: 401 }) : Response.json({ id: 1 });
  };
  assert.deepEqual(await Promise.all([memberFetch("/api/v1/auth/user"), memberFetch("/api/v1/auth/user")]).then(responses => responses.map(response => response.status)), [200, 200]);
  assert.equal(authorizations.includes("Bearer null"), false);
  assert.deepEqual(authorizations.slice(-2), ["Bearer new-access", "Bearer new-access"]);
});

test("a delayed member refresh cannot restore tokens after logout", async () => {
  saveMemberTokens("expired-access", "refresh-token");
  let finishRefresh;
  const refreshResponse = new Promise(resolve => { finishRefresh = resolve; });
  const calls = [];
  global.fetch = async (url, init) => {
    calls.push({ url: String(url), authorization: new Headers(init?.headers).get("Authorization") });
    if (String(url) === "/api/v1/auth/token/refresh/") return refreshResponse;
    if (String(url) === "/api/v1/auth/logout") return new Response(null, { status: 200 });
    return Response.json({ detail: "만료" }, { status: 401 });
  };
  const pending = memberFetch("/api/v1/auth/user");
  await new Promise(resolve => setTimeout(resolve));
  assert.equal((await logoutMember()).status, 200);
  finishRefresh(Response.json({ access: "late-access", refresh: "late-refresh" }));
  assert.equal((await pending).status, 401);
  assert.equal(sessionStorage.getItem("kbo_refresh"), null);
  assert.equal(calls.some(call => call.authorization === "Bearer late-access"), false);
});

test("a delayed member refresh cannot overwrite a newer login", async () => {
  saveMemberTokens("expired-access", "old-refresh");
  let finishRefresh;
  const refreshResponse = new Promise(resolve => { finishRefresh = resolve; });
  const calls = [];
  global.fetch = async (url, init) => {
    calls.push({ url: String(url), authorization: new Headers(init?.headers).get("Authorization") });
    if (String(url) === "/api/v1/auth/token/refresh/") return refreshResponse;
    if (calls.filter(call => call.url === "/api/v1/auth/user").length === 1) return Response.json({ detail: "만료" }, { status: 401 });
    return Response.json({ id: 1 });
  };
  const pending = memberFetch("/api/v1/auth/user");
  await new Promise(resolve => setTimeout(resolve));
  saveMemberTokens("new-login-access", "new-login-refresh");
  finishRefresh(Response.json({ access: "late-access", refresh: "late-refresh" }));
  assert.equal((await pending).status, 200);
  assert.equal(sessionStorage.getItem("kbo_refresh"), "new-login-refresh");
  assert.equal(calls.at(-1).authorization, "Bearer new-login-access");
});

test("logout sends the refresh token to Django then clears tab storage", async () => {
  saveMemberTokens("access-token", "refresh-token");
  let request;
  global.fetch = async (url, init) => { request = { url: String(url), body: JSON.parse(init.body) }; return new Response(null, { status: 200 }); };
  assert.equal((await logoutMember()).status, 200);
  assert.deepEqual(request, { url: "/api/v1/auth/logout", body: { refresh: "refresh-token" } });
  assert.equal(sessionStorage.getItem("kbo_refresh"), null);
});

test("frontend runtime has no authentication cookie path", () => {
  const files = [];
  const visit = directory => readdirSync(directory).forEach(name => {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) visit(path);
    else if (/\.(?:ts|tsx|js|mjs)$/.test(name)) files.push(path);
  });
  for (const directory of [join(root, "app"), join(root, "components"), join(root, "lib")]) visit(directory);
  const runtime = files.map(path => readFileSync(path, "utf8")).join("\n");
  assert.doesNotMatch(runtime, /document\.cookie|cookies\(\)|kbo_access|kbo_logged_out|AUTH_COOKIE_SECURE|credentials:\s*["'](?:include|same-origin)["']/);
});

test("a delayed identity response cannot overwrite an explicit anonymous state", async () => {
  const gate = createMemberRequestGate();
  let finish;
  const response = new Promise(resolve => { finish = resolve; });
  let state = { status: "loading", user: null };
  const pending = loadLatestMember(gate, () => response).then(next => { if (next) state = next; });
  gate.invalidate();
  state = { status: "anonymous", user: null };
  finish(Response.json({ id: 1, username: "late-user" }));
  await pending;
  assert.deepEqual(state, { status: "anonymous", user: null });
});

test("identity timeout becomes server unavailable", async () => {
  const gate = createMemberRequestGate(5);
  const result = await loadLatestMember(gate, signal => new Promise((_, reject) => signal.addEventListener("abort", () => reject(new DOMException("timed out", "AbortError")))));
  assert.deepEqual(result, { status: "unavailable", user: null });
});

test("a delayed profile write cannot recreate a logged-out member", async () => {
  let current = { id: 1, username: "member" };
  let finish;
  const response = new Promise(resolve => { finish = resolve; });
  const pending = response.then(user => { if (isCurrentMember(current, 1)) current = user; });
  current = null;
  finish({ id: 1, username: "late-member" });
  await pending;
  assert.equal(current, null);
});

test("email comparison normalizes uppercase input", () => {
  assert.equal(normalizeMemberEmail("  USER@Example.COM  "), "user@example.com");
});
