import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
const read = path => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const tick = () => new Promise(resolve => setImmediate(resolve));
const noop = () => null;
function load(path, dependencies) {
  const ast = ts.createSourceFile(path, read(path), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const body = ast.statements.filter(node => !ts.isImportDeclaration(node)).map(node => node.getText(ast)).join("\n");
  const testModule = { exports: {} };
  new Function("require", "module", "exports", ...Object.keys(dependencies), ts.transpileModule(body, {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText)(require, testModule, testModule.exports, ...Object.values(dependencies));
  return testModule.exports;
}
function harness() {
  const states = [], effects = [];
  let cursor = 0, pending = [];
  return {
    useState(initial) {
      const index = cursor++;
      if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial;
      return [states[index], value => { states[index] = typeof value === "function" ? value(states[index]) : value; }];
    },
    useRef(initial) {
      const index = cursor++;
      return states[index] ??= { current: initial };
    },
    useEffect(effect, dependencies) {
      const index = cursor++;
      if (!effects[index] || !dependencies.every((value, key) => Object.is(value, effects[index].dependencies[key]))) {
        const old = effects[index];
        effects[index] = { dependencies };
        pending.push(() => { old?.cleanup?.(); effects[index].cleanup = effect(); });
      }
    },
    render(Component) { cursor = 0; return Component(); },
    flush() { const next = pending; pending = []; next.forEach(run => run()); },
    unmount() { effects.forEach(effect => effect?.cleanup?.()); },
  };
}
function nodes(tree) {
  if (!tree || typeof tree !== "object") return [];
  return [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)];
}
const find = (tree, predicate) => nodes(tree).find(predicate);
const text = tree => JSON.stringify(tree);
const styles = new Proxy({}, { get: (_, key) => key });
const roles = [
  { status: "anonymous", user: null },
  { status: "authenticated", user: { id: 1, is_staff: false, is_superuser: false } },
  { status: "authenticated", user: { id: 2, is_staff: true, is_superuser: false } },
  { status: "authenticated", user: { id: 3, is_staff: false, is_superuser: true } },
  { status: "loading", user: null },
];

test("account menu retains feedback for superusers but removes community administration", () => {
  for (const identity of roles) {
    const hooks = harness();
    const { MemberHeaderActions } = load("components/member-header-actions.tsx", {
      ...hooks, Link: noop, useMemberAuth: () => identity, useRouter: () => ({}), logoutMember: noop, memberError: noop,
    });
    let tree = hooks.render(MemberHeaderActions);
    find(tree, node => node.props?.["aria-controls"] === "member-menu-panel")?.props.onClick();
    tree = hooks.render(MemberHeaderActions);
    const links = nodes(tree).filter(node => node.props?.href).map(node => node.props.href);
    const allowed = identity.user?.is_superuser === true;
    assert.equal(links.includes("/mypage?tab=feedback"), allowed);
    if (allowed) {
      const index = links.indexOf("/mypage?tab=feedback");
      assert.equal(links.includes("/mypage?tab=reports"), false);
      assert.equal(links[index + 1], "/mypage?tab=profile");
      assert.equal(find(tree, node => node.props?.href === "/mypage?tab=feedback").props.children, "챗봇 답변 평가");
    }
  }
});

test("mypage validates feedback deep links and mounts the shared panel only for superusers", () => {
  for (const identity of roles) {
    const hooks = harness(), pushes = [];
    const dependencies = {
      ...hooks, Link: noop, Image: noop, Suspense: noop, useMemberAuth: () => identity,
      useRouter: () => ({ push: (...args) => pushes.push(args) }), useSearchParams: () => new URLSearchParams("tab=feedback"),
      useRoutesReady: () => true, useRoutes: () => [], useRoutesError: () => null, useLikedRoutes: () => [],
      teamBoards: [], memberRoleLabel: () => "회원", nextNicknameChangeAt: () => null, styles,
      AdminFeedbackPanel: noop, AdminMembersPanel: noop, AdminPostsPanel: noop, AdminReportsPanel: noop,
      MemberPosts: noop, MemberAccountSettings: noop, NicknameChangeButton: noop, PasswordChangeButton: noop,
      ProfilePhotoEditor: noop, RouteCard: noop, logoutMember: noop, memberError: noop,
    };
    const { default: MyPage } = load("app/mypage/page.tsx", dependencies);
    const Content = MyPage().props.children.type;
    const tree = hooks.render(Content);
    const allowed = identity.user?.is_superuser === true;
    assert.equal(Boolean(find(tree, node => node.key === String(identity.user?.id))), allowed);
    const tab = find(tree, node => node.type === "button" && node.props.children === "챗봇 답변 평가");
    assert.equal(Boolean(tab), allowed);
    if (allowed) {
      assert.equal(tab.props["aria-pressed"], true);
      tab.props.onClick();
      assert.deepEqual(pushes, [["/mypage?tab=feedback", { scroll: false }]]);
      const labels = nodes(tree).filter(node => node.type === "button").map(node => node.props.children);
      assert.equal(labels.includes("신고 관리"), false);
      assert.equal(labels[labels.indexOf("챗봇 답변 평가") + 1], "회원 정보");
    } else assert.doesNotMatch(text(tree), /평가 상세|질문 스냅샷/);
  }
});

function panel(identity, fetchList, fetchDetail = async () => ({}), mobile = false) {
  const hooks = harness();
  const { AdminFeedbackPanel } = load("components/admin-feedback-panel.tsx", {
    ...hooks, useMemberAuth: () => identity, FEEDBACK_REASONS: { inaccurate: "부정확해요" },
    fetchAdminFeedback: fetchList, fetchAdminFeedbackDetail: fetchDetail, styles, panelStyles: styles,
    AdminFeedbackMetadata: props => props,
    window: { matchMedia: query => { assert.equal(query, "(max-width: 900px)"); return { matches: mobile }; } },
  });
  return { ...hooks, render: () => hooks.render(AdminFeedbackPanel) };
}

test("shared panel blocks both privileged requests and records for logged-out, regular, staff and loading states", () => {
  for (const identity of roles.filter(role => !role.user?.is_superuser)) {
    let calls = 0;
    const view = panel(identity, () => { calls++; }, () => { calls++; });
    const tree = view.render(); view.flush();
    assert.equal(calls, 0);
    assert.equal(find(tree, node => node.type === "select"), undefined);
    assert.doesNotMatch(text(tree), /질문 스냅샷|평가 상세/);
    view.unmount();
  }
});

test("shared panel retains list, detail snapshots, filters, pagination, refresh and loading", async () => {
  const calls = [], details = [];
  const row = { id: 7, rating: "down", question: "질문 내용", answer: "답변 내용", updated_at: "2026-10-01", reason: "inaccurate", metadata: { actual: true } };
  const view = panel(roles[3], async (...args) => { calls.push(args); return { count: 21, results: [row] }; }, async (id, signal) => { details.push({ id, signal }); return row; });
  view.render(); view.flush(); await Promise.resolve();
  assert.match(text(view.render()), /평가 불러오는 중/);
  await tick();
  let tree = view.render();
  assert.equal(find(tree, node => node.props?.className === "summary").props.children, "조회된 평가 21건");
  assert.deepEqual(find(tree, node => node.props?.["aria-label"] === "평가 페이지").props.children[1].props.children, [1, " / ", 2]);
  find(tree, node => node.type === "button" && node.props["aria-pressed"] === false).props.onClick({ currentTarget: { focus: noop } });
  view.render(); view.flush(); await tick(); tree = view.render();
  assert.equal(details[0].id, 7);
  assert.match(text(tree), /질문 스냅샷.*답변 스냅샷/);
  assert.deepEqual(find(tree, node => node.props?.metadata).props.metadata, row.metadata);
  find(tree, node => node.props?.children === "상세 닫기").props.onClick();
  assert.equal(find(view.render(), node => node.props?.["aria-label"] === "평가 상세"), undefined);
  find(tree, node => node.props?.children === "다음").props.onClick();
  view.render(); view.flush(); await tick();
  assert.equal(calls.at(-1)[0], 2);
  tree = view.render();
  const filters = nodes(tree).filter(node => node.type === "select");
  filters[0].props.onChange({ target: { value: "down" } });
  filters[1].props.onChange({ target: { value: "inaccurate" } });
  view.render(); view.flush(); await tick();
  assert.deepEqual(calls.at(-1).slice(0, 3), [1, "down", "inaccurate"]);
  const count = calls.length;
  find(view.render(), node => node.props?.children === "새로고침").props.onClick();
  view.render(); view.flush(); await tick();
  assert.equal(calls.length, count + 1);
  view.unmount();
  assert.equal(calls.at(-1)[3].aborted, true);
});

test("shared panel retains error/retry and the legacy route reuses it without a nested main", async () => {
  let calls = 0;
  const view = panel(roles[3], async () => { if (++calls === 1) throw new Error("조회 실패"); return { count: 0, results: [] }; });
  view.render(); view.flush(); await tick();
  assert.equal(find(view.render(), node => node.props?.role === "alert").props.children, "조회 실패");
  find(view.render(), node => node.props?.children === "새로고침").props.onClick();
  view.render(); view.flush(); await tick();
  assert.match(text(view.render()), /조회된 평가 0건.*아직 등록된 평가가 없어요/);
  assert.deepEqual(find(view.render(), node => node.props?.["aria-label"] === "평가 페이지").props.children[1].props.children, [1, " / ", 1]);
  assert.equal(find(view.render(), node => node.props?.role === "alert"), undefined);
  assert.equal(view.render().type, "section");
  const { default: Legacy } = load("app/admin/feedback/page.tsx", { Link: noop, AdminFeedbackPanel: noop, styles });
  const tree = Legacy();
  assert.equal(tree.type, "main");
  assert.equal(find(tree, node => node.props?.href === "/admin").props.children, "← 관리자 대시보드");
  assert.equal(tree.props.children[1].type, "header");
  assert.equal(tree.props.children[2].type, noop);
  view.unmount();
});


const feedbackRow = id => ({ id, rating: "down", question: `질문 ${id}`, answer: `답변 ${id}`, updated_at: "2026-10-01", metadata: {} });
const selectFeedback = (view, id, button = { focus: noop }) => {
  find(view.render(), node => node.type === "button" && node.props?.["aria-pressed"] !== undefined && text(node).includes(`질문 ${id}`)).props.onClick({ currentTarget: button });
};

test("feedback detail focuses and scrolls on mobile only, and closing restores the selected button", async () => {
  for (const mobile of [true, false]) {
    const row = feedbackRow(7), focus = [], scroll = [], returned = [];
    const view = panel(roles[3], async () => ({ count: 1, results: [row] }), async () => row, mobile);
    view.render(); view.flush(); await tick();
    selectFeedback(view, 7, { focus: options => returned.push(options) });
    view.render(); view.flush(); await tick();
    const tree = view.render();
    const heading = find(tree, node => node.type === "h2" && node.props.tabIndex === -1);
    assert.ok(heading);
    heading.props.ref.current = { focus: options => focus.push(options), scrollIntoView: options => scroll.push(options) };
    view.flush();
    assert.deepEqual(focus, mobile ? [{ preventScroll: true }] : []);
    assert.deepEqual(scroll, mobile ? [{ block: "start" }] : []);
    find(tree, node => node.props?.children === "상세 닫기").props.onClick();
    assert.deepEqual(returned, [{ preventScroll: !mobile }]);
    view.render(); view.flush();
    assert.equal(find(view.render(), node => node.props?.["aria-label"] === "평가 상세"), undefined);
    view.unmount();
  }
});

test("feedback detail failure retries the same selection and ignores aborted detail responses", async () => {
  const rows = [feedbackRow(7), feedbackRow(8)], requests = [];
  const view = panel(roles[3], async () => ({ count: 2, results: rows }), (id, signal) => new Promise((resolve, reject) => requests.push({ id, signal, resolve, reject })));
  view.render(); view.flush(); await tick();
  selectFeedback(view, 7); view.render(); view.flush(); await tick();
  requests[0].reject(new Error("상세 조회 실패")); await tick();
  assert.equal(find(view.render(), node => node.props?.role === "alert").props.children, "상세 조회 실패");
  selectFeedback(view, 7); view.render(); view.flush(); await tick();
  assert.equal(requests.length, 2);
  assert.equal(requests[0].signal.aborted, true);
  assert.match(text(view.render()), /상세 불러오는 중/);
  assert.equal(find(view.render(), node => node.props?.role === "alert"), undefined);
  selectFeedback(view, 8); view.render(); view.flush(); await tick();
  assert.equal(requests[1].signal.aborted, true);
  requests[2].resolve(rows[1]); await tick();
  requests[1].resolve(rows[0]); await tick();
  let tree = view.render(); view.flush();
  assert.deepEqual(find(tree, node => node.type === "h2" && node.props.tabIndex === -1).props.children, ["평가 #", 8]);
  find(tree, node => node.props?.children === "상세 닫기").props.onClick();
  view.render(); view.flush();
  assert.equal(requests[2].signal.aborted, true);
  selectFeedback(view, 7); view.render(); view.flush(); await tick();
  view.unmount();
  assert.equal(requests[3].signal.aborted, true);
  requests[3].reject(new Error("늦은 오류")); await tick();
  assert.equal(find(view.render(), node => node.props?.role === "alert"), undefined);
});

test("feedback filters, pages and refresh cancel detail work and retain the filtered empty state", async () => {
  for (const change of ["filter", "page", "refresh"]) {
    const row = feedbackRow(7), requests = [], calls = [];
    const view = panel(roles[3], async (...args) => { calls.push(args); return calls.length === 1 ? { count: 21, results: [row] } : { count: 0, results: [] }; }, (id, signal) => new Promise(resolve => requests.push({ id, signal, resolve })));
    view.render(); view.flush(); await tick();
    selectFeedback(view, 7); view.render(); view.flush(); await tick();
    const tree = view.render();
    if (change === "filter") find(tree, node => node.type === "select").props.onChange({ target: { value: "down" } });
    else find(tree, node => node.props?.children === (change === "page" ? "다음" : "새로고침")).props.onClick();
    view.render(); view.flush(); await tick();
    view.render(); view.flush();
    assert.equal(requests[0].signal.aborted, true);
    requests[0].resolve(row); await tick();
    assert.equal(find(view.render(), node => node.props?.["aria-label"] === "평가 상세"), undefined);
    assert.match(text(view.render()), change === "filter" ? /조건에 맞는 평가가 없어요/ : /아직 등록된 평가가 없어요/);
    assert.equal(calls.at(-1)[0], change === "page" ? 2 : 1);
    view.unmount();
  }
});

test("admin tables wrap user text but retain single-line dates, numbers, status and actions", () => {
  const source = read("components/admin-panels.tsx");
  for (const field of ["member.username", "post.author", "report.post.author", "report.reporter"]) {
    assert.ok(source.includes(`<td className={styles.longText}>{${field}}`), field);
  }
  for (const field of ["member.id", "member.date_joined.slice(0, 10)", "dateText(post.created_at)", "post.views", "dateText(report.created_at)"]) {
    assert.ok(source.includes(`<td className={styles.nowrap}>{${field}}`), field);
  }
  const css = read("components/admin-panels.module.css");
  assert.doesNotMatch(css.match(/\.tableScroll td \{([^}]+)\}/)[1], /white-space:\s*nowrap/);
  assert.match(css, /\.tableScroll td\.nowrap \{ white-space: nowrap;/);
  assert.match(css, /td\.longText[^}]*overflow-wrap: anywhere/);
});
