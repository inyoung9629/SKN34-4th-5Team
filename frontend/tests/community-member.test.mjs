import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

function load(file, dependencies = {}) {
  const source = readFileSync(new URL("../" + file, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  const loadedModule = { exports: {} };
  new Function("module", "exports", "require", outputText)(loadedModule, loadedModule.exports, name => {
    if (!(name in dependencies)) throw new Error("Unexpected dependency: " + name);
    return dependencies[name];
  });
  return loadedModule.exports;
}
const client = load("lib/api/client.ts");
const routes = load("lib/team-community.ts");
const { safeMemberReturnPath, parseMemberActivityQuery, memberActivityLoginHref } = load("lib/member-return-path.ts");

test("member return paths reject external destinations and normalize pagination", () => {
  for (const path of [null, "https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)", "/admin", "/community/members/0", "/community/members/9007199254740992"]) {
    assert.equal(safeMemberReturnPath(path), null, String(path));
  }
  assert.equal(safeMemberReturnPath("/community/members/21?tab=comments&page=2"), "/community/members/21?tab=comments&page=2");
  assert.equal(safeMemberReturnPath("/fantasy/admin"), "/fantasy/admin");
  assert.equal(safeMemberReturnPath("/fantasy/admin?date=2026-10-09"), "/fantasy/admin?date=2026-10-09");
  assert.equal(safeMemberReturnPath("/fantasy/admin/other"), null);
  assert.equal(safeMemberReturnPath("/community/members/21?tab=invalid&page=-1"), "/community/members/21?tab=posts&page=1");
});

test("member activity links route to the original board and encode post IDs", () => {
  assert.equal(routes.getCommunityPostHref({ id: "free/a b", board: "free", teamCode: "" }), "/community?post=free%2Fa%20b");
  const url = new URL(routes.getCommunityPostHref({ id: "lg/a b", board: "teams", teamCode: "LG" }), "https://local.invalid");
  assert.equal(url.pathname, "/community/teams");
  assert.equal(url.searchParams.get("post"), "lg/a b");
  assert.equal(url.searchParams.get("team"), "LG");
});

function apiWith(handler) {
  return load("lib/community-member-api.ts", { "./api/client": client, "./member-auth-request": { memberFetch: handler }, "./team-community": routes });
}
const post = { id: "free-1", title: "제목", board: "free", teamCode: "", createdAt: "2026-09-28T01:00:00Z" };
const page = results => ({ count: results.length, next: null, previous: null, results });

test("member activity uses authenticated requests and preserves server order", async () => {
  const calls = [];
  const posts = [post, { ...post, id: "free-2", createdAt: "2026-09-27T01:00:00Z" }];
  const api = apiWith(async (url, init) => {
    calls.push(url);
    assert.equal(init.cache, "no-store");
    assert.ok(init.signal instanceof AbortSignal);
    return Response.json(url.endsWith("/21/public/") ? { id: 21, nickname: "회원", activityVisible: true } : page(posts));
  });
  assert.equal((await api.fetchCommunityMember(21)).id, 21);
  assert.deepEqual((await api.fetchCommunityMemberPosts(21, 2)).results, posts);
  assert.deepEqual(calls, ["/api/v1/auth/users/21/public/", "/api/v1/community/posts/?author_id=21&page=2&page_size=20"]);
});

test("member activity rejects mismatched identities and missing comment destinations", async () => {
  await assert.rejects(apiWith(async () => Response.json({ id: 22, nickname: "다른회원", activityVisible: true })).fetchCommunityMember(21), error => error.status === 502);
  await assert.rejects(apiWith(async () => Response.json(page([{ id: 1, content: "댓글", createdAt: post.createdAt, postId: "x" }]))).fetchCommunityMemberComments(21), error => error.status === 502);
  for (const status of [401, 403, 404]) {
    await assert.rejects(apiWith(async () => Response.json({ detail: "거부" }, { status })).fetchCommunityMember(21), error => error.status === status);
  }
});

test("invalid IDs and aborted requests never invoke the member transport", async () => {
  let calls = 0;
  const api = apiWith(async () => { calls++; return Response.json(page([])); });
  await assert.rejects(api.fetchCommunityMember(0), error => error.status === 400);
  assert.throws(() => api.fetchCommunityMemberPosts(21, -1), error => error.status === 400);
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(api.fetchCommunityMemberPosts(21, 1, controller.signal), error => error.name === "AbortError");
  assert.equal(calls, 0);
});

test("activity query parsing is shared and rejects duplicate or invalid values", () => {
  assert.deepEqual(parseMemberActivityQuery({ tab: "comments", page: "2" }), { tab: "comments", page: 2 });
  for (const value of ["0", "01", "-1", "1.5", "2147483648", ["2", "3"]]) {
    assert.deepEqual(parseMemberActivityQuery({ tab: ["comments", "posts"], page: value }), { tab: "posts", page: 1 });
  }
  assert.equal(safeMemberReturnPath("/community/members/21?tab=comments&tab=posts&page=2&page=3"), "/community/members/21?tab=posts&page=1");
  const login = new URL(memberActivityLoginHref(21, "comments", 3), "https://local.invalid");
  assert.equal(login.pathname, "/login");
  assert.equal(login.searchParams.get("next"), "/community/members/21?tab=comments&page=3");
});

test("public summaries require explicit boolean visibility", async () => {
  for (const activityVisible of [true, false]) {
    const member = { id: 21, nickname: "회원", activityVisible };
    assert.deepEqual(await apiWith(async () => Response.json(member)).fetchCommunityMember(21), member);
  }
  for (const activityVisible of [undefined, null, "true", 1]) {
    await assert.rejects(apiWith(async () => Response.json({ id: 21, nickname: "회원", activityVisible })).fetchCommunityMember(21), error => error.status === 502);
  }
});

test("comments use v1 author filtering and retain original-post destinations", async () => {
  const comment = { id: 3, content: "댓글", createdAt: post.createdAt, postId: post.id, postTitle: post.title, board: "free", teamCode: "" };
  const api = apiWith(async url => {
    assert.equal(url, "/api/v1/community/comments/?author_id=21&page=3&page_size=20");
    return Response.json(page([comment]));
  });
  assert.deepEqual((await api.fetchCommunityMemberComments(21, 3)).results, [comment]);
});

test("v1 posts permit null creation dates and all activity failures preserve status", async () => {
  const item = { ...post, createdAt: null };
  assert.deepEqual((await apiWith(async () => Response.json(page([item]))).fetchCommunityMemberPosts(21)).results, [item]);
  for (const status of [401, 403, 404]) {
    const api = apiWith(async () => Response.json({ detail: "거부" }, { status }));
    await assert.rejects(api.fetchCommunityMemberPosts(21), error => error.status === status);
    await assert.rejects(api.fetchCommunityMemberComments(21), error => error.status === status);
  }
});

function activityScreen({ viewerId = 22, status = "authenticated", visible = false, failure = null } = {}) {
  const effects = [], states = [], calls = [], redirects = [];
  const jsx = (type, props) => ({ type, props });
  const { CommunityMemberPage } = load("components/community-member-page.tsx", {
    "react/jsx-runtime": { jsx, jsxs: jsx },
    "next/link": { default: "a" },
    "next/navigation": { useRouter: () => ({ replace: url => redirects.push(url) }) },
    react: {
      useEffect: effect => effects.push(effect),
      useState: initial => {
        const index = states.length;
        states.push(initial);
        return [initial, value => { states[index] = typeof value === "function" ? value(states[index]) : value; }];
      },
    },
    "@/lib/api/client": client,
    "@/lib/community-member-api": {
      fetchCommunityMember: async () => ({ id: 21, nickname: "회원", activityVisible: visible }),
      fetchCommunityMemberPosts: async () => { calls.push("posts"); if (failure) throw new client.ApiError("거부", failure); return page([post]); },
      fetchCommunityMemberComments: async () => { calls.push("comments"); if (failure) throw new client.ApiError("거부", failure); return page([]); },
    },
    "@/lib/member-auth": { useMemberAuth: () => ({ status, user: status === "authenticated" ? { id: viewerId } : null, reload: () => {} }) },
    "@/lib/member-return-path": load("lib/member-return-path.ts"),
    "@/lib/team-community": routes,
    "./community-member-page.module.css": { default: {} },
  });
  return { CommunityMemberPage, effects, states, calls, redirects };
}

test("private screen skips other-member activity but allows the owner", async () => {
  for (const tab of ["posts", "comments"]) {
    for (const viewerId of [21, 22]) {
      const screen = activityScreen({ viewerId });
      const content = screen.CommunityMemberPage({ memberId: 21, tab, page: 1 });
      content.type(content.props);
      const cleanups = screen.effects.map(effect => effect());
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(screen.states[1].kind, viewerId === 21 ? "ready" : "private");
      assert.deepEqual(screen.calls, viewerId === 21 ? [tab] : []);
      cleanups.forEach(cleanup => cleanup?.());
    }
  }
});

test("activity 403 renders private state and 401 redirects to login with next", async () => {
  for (const failure of [403, 401]) {
    const screen = activityScreen({ visible: true, failure });
    const content = screen.CommunityMemberPage({ memberId: 21, tab: "comments", page: 2 });
    content.type(content.props);
    const cleanups = screen.effects.map(effect => effect());
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(screen.states[1].kind, failure === 403 ? "private" : "unauthorized");
    assert.deepEqual(screen.redirects, failure === 401 ? [memberActivityLoginHref(21, "comments", 2)] : []);
    cleanups.forEach(cleanup => cleanup?.());
  }
});

test("anonymous direct visits redirect to login without activity requests", () => {
  const screen = activityScreen({ status: "anonymous" });
  screen.CommunityMemberPage({ memberId: 21, tab: "posts", page: 3 });
  screen.effects.forEach(effect => effect());
  assert.deepEqual(screen.redirects, [memberActivityLoginHref(21, "posts", 3)]);
  assert.deepEqual(screen.calls, []);
});
