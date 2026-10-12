import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

function load(path, dependencies = {}) {
  const source = readFileSync(new URL(path, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } });
  const loadedModule = { exports: {} };
  new Function("module", "exports", "require", outputText)(loadedModule, loadedModule.exports, name => dependencies[name]);
  return loadedModule.exports;
}
const categories = load("../lib/community-post-category.ts");
const filter = load("../lib/community-category-filter.ts", { "./community-post-category": categories });

test("category links preserve team/search, leave detail and reset page", () => {
  const url = new URL(filter.communityCategoryHref("/community/teams?team=LT&q=검색&post=abc&page=3", "사진·영상"), "http://local");
  assert.equal(url.searchParams.get("team"), "LT");
  assert.equal(url.searchParams.get("q"), "검색");
  assert.equal(url.searchParams.get("category"), "사진·영상");
  assert.equal(url.searchParams.has("post"), false);
  assert.equal(url.searchParams.has("page"), false);
  assert.equal(filter.communityCategoryHref("/community?category=질문", ""), "/community");
});

test("category uses exact match including punctuation and legacy categories", () => {
  assert.equal(filter.matchesCommunityCategory({category: "사진·영상"}, "사진·영상"), true);
  assert.equal(filter.matchesCommunityCategory({category: "사진·영상"}, "사진"), false);
  assert.equal(filter.matchesCommunityCategory({category: "잡담"}, ""), true);
  assert.equal(filter.isCommunityCategory("소식·정보"), true);
  assert.equal(filter.isCommunityCategory("없는분류"), false);
});

function boardScreen(props) {
  let cursor = 0, dirty = false;
  const states = [];
  const jsx = (type, props) => ({ type, props });
  const posts = ["질문", "잡담"].flatMap(category => Array.from({ length: 65 }, (_, index) => ({
    id: `${category}-${index}`, postNumber: index + 1, board: props.section, teamCode: "LT",
    category, title: `${category} 글 ${index}`, content: "본문", author: "회원", createdAt: null,
  })));
  posts.push({ ...posts[0], id: "other-team", teamCode: "HH", title: "질문 글 2" });
  const { CommunityBoard } = load("../components/community-board.tsx", {
    "react/jsx-runtime": { jsx, jsxs: jsx },
    react: {
      useState(initial) {
        const index = cursor++;
        if (!(index in states)) states[index] = initial;
        return [states[index], value => { states[index] = value; dirty = true; }];
      },
      useRef: () => ({ current: "" }), useEffect: () => {},
    },
    "next/link": { default: "a" }, "next/navigation": { useRouter: () => ({}) },
    "@/lib/community-api": { useCommunityPosts: () => ({ posts, loading: false, error: "" }) },
    "@/lib/member-auth": { useMemberAuth: () => ({ user: null }) },
    "@/lib/team-community": load("../lib/team-community.ts"),
    "@/lib/community-category-filter": filter,
    "./community-board.module.css": { default: {} },
    ...Object.fromEntries([
      "CommunityNavigation", "CommunityPostBottom", "CommunityPostContent", "CommunityPostVote",
      "PostCategory", "PostCommentCount", "PostReportButton", "CommunityMemberLink",
    ].map(name => [`./${name.replace(/[A-Z]/g, (letter, index) => `${index ? "-" : ""}${letter.toLowerCase()}`)}`, { [name]: name }])),
  });
  function render(patch = {}) {
    props = { ...props, ...patch };
    let tree, attempts = 0;
    do {
      cursor = 0; dirty = false;
      tree = CommunityBoard(props);
      assert.ok(++attempts <= 10, "render-time state update must settle");
    } while (dirty);
    return tree;
  }
  return { render };
}

function nodes(tree) {
  if (!tree || typeof tree !== "object") return [];
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  return [tree, ...nodes(tree.props?.children)];
}
const currentPage = tree => nodes(tree).find(node => node.type === "button" && node.props["aria-current"] === "page")?.props.children;
const clickPage = (tree, label) => nodes(tree).find(node => node.type === "button" && node.props["aria-label"] === label).props.onClick();

for (const section of ["free", "teams"]) {
  for (const initialCategory of ["", "질문"]) {
    test(`${section} board resets stored pagination on ${initialCategory || "all"} category round trip`, () => {
      const screen = boardScreen({ section, teamCode: section === "teams" ? "LT" : "", category: initialCategory });
      const tree = screen.render();
      clickPage(tree, "3페이지");
      assert.equal(currentPage(screen.render()), 3);
      assert.equal(currentPage(screen.render({ category: "잡담" })), 1);
      assert.equal(currentPage(screen.render({ category: initialCategory })), 1, "returning to the original scope must not resurrect page 3");
    });
  }

  test(`${section} board keeps local search and team filters across category changes`, context => {
    const data = new FormData();
    for (const [name, value] of Object.entries({ team: "LT", field: "title", query: "글 2" })) data.set(name, value);
    context.mock.method(globalThis, "FormData", function () { return data; });
    const screen = boardScreen({ section, teamCode: section === "teams" ? "LT" : "" });
    const form = nodes(screen.render()).find(node => node.type === "form");
    form.props.onSubmit({ preventDefault() {}, currentTarget: { team: "LT", field: "title", query: "글 2" } });
    for (const category of ["질문", "잡담", ""]) {
      const links = nodes(screen.render({ category })).filter(node => node.type === "a" && node.props.href?.includes("post="));
      assert.equal(links.length, category ? 11 : 20);
      assert.ok(links.every(link => link.props.children[0].includes("글 2")));
    }
  });

  test(`${section} board preserves same-scope pagination and category detail links`, () => {
    const screen = boardScreen({ section, teamCode: section === "teams" ? "LT" : "", category: "질문" });
    let tree = screen.render();
    clickPage(tree, "다음 페이지");
    tree = screen.render();
    assert.equal(currentPage(tree), 2);
    const links = nodes(tree).filter(node => node.type === "a" && node.props.href?.includes("post="));
    assert.equal(links.length, 20);
    const url = new URL(links[0].props.href, "http://local");
    assert.equal(url.searchParams.get("category"), "질문");
    assert.equal(url.searchParams.get("post"), "질문-20");
    assert.equal(url.searchParams.get("team"), section === "teams" ? "LT" : null);
    assert.equal(currentPage(screen.render({ postId: "질문-20" })), 2);
    assert.equal(currentPage(screen.render({ postId: "" })), 2);
  });
}

test("list tags are separate links and category is part of pagination scope", () => {
  const source = readFileSync(new URL("../components/community-board.tsx", import.meta.url), "utf8");
  assert.ok(source.includes('styles.titleLinks}>{categoryLink(post.category)}<Link'));
  assert.ok(source.includes("activeSearch.field, query, category]"));
  assert.ok(source.includes('categoryHref("")'));
});
