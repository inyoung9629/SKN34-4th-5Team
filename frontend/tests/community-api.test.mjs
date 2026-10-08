import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

const frontend = new URL("../", import.meta.url);
const source = readFileSync(new URL("lib/community-api.ts", frontend), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } });
const categoryValues = ["잡담", "질문", "응원", "경기토론", "전력토론", "소식·정보", "이적·신인", "직관후기", "좌석·예매", "직관준비", "굿즈", "사진·영상"];
const teamCodes = ["LG", "HH", "SK", "SS", "NC", "KT", "LT", "HT", "OB", "WO"];
let memberHandler = async () => { throw new Error("unexpected authenticated request"); };
const apiRequest = async (path, init, fetcher = fetch) => {
  const response = await fetcher(path, init);
  if (response.status === 204) return null;
  const value = await response.json().catch(() => null);
  if (!response.ok) throw new Error(value?.detail ?? "요청 실패");
  return value;
};
const testModule = { exports: {} };
const require = name => name === "react"
  ? { useEffect() {}, useSyncExternalStore() {} }
  : name === "./community-post-category"
    ? { communityPostCategories: categoryValues }
    : name === "./api/client"
      ? { apiRequest }
    : name === "./member-auth-request"
      ? { memberFetch: (...args) => memberHandler(...args) }
      : { teamBoards: teamCodes.map(code => ({ code })) };
new Function("module", "exports", "require", outputText)(testModule, testModule.exports, require);
const api = testModule.exports;
const post = {
  id: "free/sample 1", sourceId: "free/sample 1", postNumber: "020001", board: "free", teamCode: "",
  authorId: 7, author: "작성자", title: "제목", content: "본문", category: "잡담", createdAt: null,
  views: 0, recommendations: 0, downvotes: 0, commentCount: 0, isSample: false,
};
const comment = { id: 3, postId: post.id, authorId: 7, author: "작성자", content: "댓글", createdAt: "2026-09-15T00:00:00Z", updatedAt: "2026-09-15T00:00:00Z" };
const vote = { vote: "up", recommendations: 1, downvotes: 0 };
const input = { board: "free", teamCode: "", category: "잡담", title: " 제목 ", content: " 본문 " };
const idempotencyKey = "550e8400-e29b-41d4-a716-446655440000";

test("community list is read from the public DB API", async () => {
  const result = await api.fetchCommunityPosts(async (url, init) => {
    assert.equal(url, "/api/v1/community/posts/");
    assert.equal(init.cache, "no-store");
    assert.ok(init.signal instanceof AbortSignal);
    return Response.json([post]);
  });
  assert.deepEqual(result, [post]);
});

test("public detail/comments use fetch while private reads use memberFetch with encoded IDs", async () => {
  const publicCalls = [];
  const originalFetch = global.fetch;
  global.fetch = async (url, init) => {
    publicCalls.push([url, init]);
    if (url.endsWith("comments/?order=newest")) return Response.json([comment]);
    return Response.json(post);
  };
  const memberCalls = [];
  memberHandler = async (url, init) => {
    memberCalls.push([url, init]);
    return url.includes("mine=1") ? Response.json([post]) : Response.json(vote);
  };
  try {
    assert.deepEqual(await api.getCommunityPost(post.id), post);
    assert.deepEqual(await api.fetchCommunityComments(post.id, "newest"), [comment]);
    assert.deepEqual(await api.fetchMyCommunityPosts(), [post]);
    assert.deepEqual(await api.fetchCommunityVote(post.id), vote);
  } finally { global.fetch = originalFetch; }

  assert.deepEqual(publicCalls.map(([url]) => url), [
    "/api/v1/community/posts/free%2Fsample%201/",
    "/api/v1/community/posts/free%2Fsample%201/comments/?order=newest",
  ]);
  assert.deepEqual(memberCalls.map(([url]) => url), ["/api/v1/community/posts/?mine=1", "/api/v1/community/posts/free%2Fsample%201/vote/"]);
  for (const [, init] of [...publicCalls, ...memberCalls]) {
    assert.equal(init.cache, "no-store");
    assert.ok(init.signal instanceof AbortSignal);
  }
});

test("authenticated mutations send exact bodies and refresh the public store", async () => {
  const calls = [];
  const responses = [post, post, null, comment, comment, null, vote, { id: 9, created: true }];
  memberHandler = async (url, init) => {
    calls.push([url, init]);
    const value = responses.shift();
    return value === null ? new Response(null, { status: 204 }) : Response.json(value, { status: init.method === "POST" ? 201 : 200 });
  };
  let refreshes = 0;
  const originalFetch = global.fetch;
  global.fetch = async url => {
    assert.equal(url, "/api/v1/community/posts/");
    refreshes += 1;
    return Response.json([post]);
  };
  try {
    assert.deepEqual(await api.createCommunityPost(input, idempotencyKey), post);
    assert.deepEqual(await api.updateCommunityPost(post.id, input), post);
    await api.deleteCommunityPost(post.id);
    assert.deepEqual(await api.createCommunityComment(post.id, " 댓글 "), comment);
    assert.deepEqual(await api.updateCommunityComment(3, " 수정 "), comment);
    await api.deleteCommunityComment(3);
    assert.deepEqual(await api.setCommunityVote(post.id, "up"), vote);
    assert.deepEqual(await api.reportCommunityPost(post.id, "spam", " 상세 "), { id: 9, created: true });
  } finally { global.fetch = originalFetch; }

  assert.equal(refreshes, 8);
  assert.deepEqual(calls.map(([url, init]) => [url, init.method]), [
    ["/api/v1/community/posts/", "POST"],
    ["/api/v1/community/posts/free%2Fsample%201/", "PATCH"],
    ["/api/v1/community/posts/free%2Fsample%201/", "DELETE"],
    ["/api/v1/community/posts/free%2Fsample%201/comments/", "POST"],
    ["/api/v1/community/comments/3/", "PATCH"],
    ["/api/v1/community/comments/3/", "DELETE"],
    ["/api/v1/community/posts/free%2Fsample%201/vote/", "POST"],
    ["/api/v1/community/posts/free%2Fsample%201/reports/", "POST"],
  ]);
  assert.equal(new Headers(calls[0][1].headers).get("Idempotency-Key"), idempotencyKey);
  assert.deepEqual(JSON.parse(calls[0][1].body), { ...input, title: "제목", content: "본문" });
  assert.deepEqual(JSON.parse(calls[3][1].body), { content: "댓글" });
  assert.deepEqual(JSON.parse(calls[6][1].body), { vote: "up" });
  assert.deepEqual(JSON.parse(calls[7][1].body), { reason: "spam", detail: "상세" });
});

test("validation and API failures are bounded Korean errors", async () => {
  let calls = 0;
  memberHandler = async () => { calls += 1; return Response.json({ detail: "권한이 없습니다." }, { status: 403 }); };
  await assert.rejects(api.createCommunityPost({ ...input, title: "x".repeat(201) }, "key"), /200자/);
  await assert.rejects(api.createCommunityPost({ ...input, content: "x".repeat(20001) }, "key"), /20000자/);
  await assert.rejects(api.createCommunityPost(input, "x".repeat(129)), /128자/);
  await assert.rejects(api.createCommunityComment(post.id, "x".repeat(2001)), /2000자/);
  await assert.rejects(api.reportCommunityPost(post.id, "spam", "x".repeat(51)), /50자/);
  await assert.rejects(api.updateCommunityPost("x".repeat(41), input), /식별자/);
  assert.equal(calls, 0);

  await assert.rejects(api.fetchMyCommunityPosts(), /권한이 없습니다/);
  memberHandler = async () => Response.json({ detail: "Internal server error" }, { status: 500 });
  await assert.rejects(api.fetchCommunityVote(post.id), /추천 정보를 불러오지 못했어요/);
  memberHandler = async () => { throw new DOMException("timed out", "TimeoutError"); };
  await assert.rejects(api.fetchCommunityVote(post.id), /요청 시간이 초과/);
  memberHandler = async () => { throw new TypeError("Failed to fetch internal.example"); };
  await assert.rejects(api.fetchCommunityVote(post.id), /^Error: 추천 정보를 불러오지 못했어요\.$/);
});

test("API and malformed payload failures stay failures without fixture fallback", async () => {
  await assert.rejects(api.fetchCommunityPosts(async () => Response.json([{ ...post, sourceId: "other" }])), /응답 형식/);
  await assert.rejects(api.fetchCommunityPosts(async () => Response.json([{ ...post, downvotes: -1 }])), /응답 형식/);
  await assert.rejects(api.fetchCommunityPosts(async () => Response.json({ detail: "서비스를 사용할 수 없습니다." }, { status: 503 })), /서비스를 사용할 수 없습니다/);

  for (const file of ["components/community-board.tsx", "components/home-team-boards.tsx"]) {
    const caller = readFileSync(new URL(file, frontend), "utf8");
    assert.doesNotMatch(caller, /getFreeBoardPosts|getTeamBoardPosts|free-community-examples|team-community-examples/);
    assert.match(caller, /useCommunityPosts/);
    assert.match(caller, /다시 (시도|확인)/);
  }
  const legacyWrapper = readFileSync(new URL("components/team-community-board.tsx", frontend), "utf8");
  assert.match(legacyWrapper, /import \{ CommunityBoard \} from "\.\/community-board"/);
  assert.match(legacyWrapper, /<CommunityBoard section="teams" teamCode=\{teamCode\} postId=\{postId\} \/>/);
  assert.doesNotMatch(legacyWrapper, /getFreeBoardPosts|getTeamBoardPosts|free-community-examples|team-community-examples/);
});

test("all contract functions remain named exports", () => {
  for (const name of [
    "fetchCommunityPosts", "useCommunityPosts", "retryCommunityPosts", "getCommunityPost", "createCommunityPost",
    "updateCommunityPost", "deleteCommunityPost", "fetchMyCommunityPosts", "fetchCommunityComments", "createCommunityComment",
    "updateCommunityComment", "deleteCommunityComment", "fetchCommunityVote", "setCommunityVote", "reportCommunityPost",
  ]) assert.equal(typeof api[name], "function", name);

});
