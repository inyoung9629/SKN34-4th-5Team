import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const tick = () => new Promise(resolve => setTimeout(resolve, 5));
const nodes = tree => !tree || typeof tree !== "object" ? [] : [tree, ...[tree.props?.children].flat(Infinity).flatMap(nodes)];
const find = (tree, predicate) => nodes(tree).find(predicate);
const byId = (tree, id) => find(tree, node => node.props?.id === id);
const button = (tree, text) => find(tree, node => node.type === "button" && node.props.children === text);

function mount(path, transport, admin = true) {
  const states = [], effects = [];
  let cursor = 0, pending = [], tree;
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial;
      return [states[index], value => { states[index] = typeof value === "function" ? value(states[index]) : value; }];
    },
    useRef(initial) { const index = cursor++; return states[index] ??= { current: initial }; },
    useEffect(effect, dependencies) {
      const index = cursor++;
      if (!effects[index] || !dependencies.every((value, key) => Object.is(value, effects[index].dependencies[key]))) {
        const old = effects[index];
        effects[index] = { dependencies };
        pending.push(() => { old?.cleanup?.(); effects[index].cleanup = effect(); });
      }
    },
  };
  const source = readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
  const output = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
  const testModule = { exports: {} };
  const window = { setTimeout: () => 1, clearTimeout() {}, confirm: () => true, dispatchEvent() {} };
  new Function("require", "module", "exports", "window", "fetch", output)(id => {
    if (id === "react") return hooks;
    if (id === "react/jsx-runtime") return require(id);
    if (id === "next/link") return { __esModule: true, default: "a" };
    if (id === "@/lib/member-auth") return { useMemberAuth: () => ({ status: "authenticated", user: { is_staff: admin } }) };
    if (id === "@/lib/member-auth-request") return { memberFetch: transport };
    if (id === "@/lib/api/client") return { ApiError: Error, readApiResponse: async response => { const value = await response.json(); if (!response.ok) throw new Error(value.detail); return value; } };
    if (id.endsWith(".css")) return { __esModule: true, default: new Proxy({}, { get: (_, key) => key }) };
    throw new Error(id);
  }, testModule, testModule.exports, window, async () => Response.json([]));
  const Component = testModule.exports.default ?? testModule.exports.PredictionBoard;
  return {
    render() { cursor = 0; tree = Component(); const next = pending; pending = []; next.forEach(run => run()); return tree; },
    async settle() { for (let i = 0; i < 4; i++) { this.render(); await tick(); } return this.render(); },
    unmount() { effects.forEach(effect => effect?.cleanup?.()); },
  };
}
const stats = {
  batting: [{ external_code: "BAT-CODE", player_name: "타자", team_name: "구단", at_bats: 4, hits: 1, rbi: 0, runs: 1 }],
  pitching: [{ external_code: "PITCH-CODE", player_name: "투수", team_name: "구단", saves: 0, batters_faced: 20, strikeouts: 6, pitch_count: 88 }],
};
function adminTransport(write, reads = []) {
  return async (path, init = {}) => {
    if (init.method) return write(path, init);
    reads.push(path);
    if (path.includes("context/")) return Response.json({ week_start: "2026-10-05", week_end: "2026-10-11", today: "2026-10-09" });
    if (path.includes("games/")) return Response.json([1, 2].map(id => ({ id, game_time: "18:30", home_team: null, away_team: null })));
    return Response.json(stats);
  };
}
const change = (view, id, value) => { byId(view.render(), id).props.onChange({ target: { value } }); view.render(); };
const submit = view => find(view.render(), node => node.type === "form").props.onSubmit({ preventDefault() {} });

 test("admin save preserves newer per-field drafts and renders external codes", async () => {
  for (const changed of ["batting", "pitching"]) {
    const pending = deferred(); let sent;
    const view = mount("app/fantasy/admin/page.tsx", adminTransport((path, init) => { sent = JSON.parse(init.body); return pending.promise; }));
    await view.settle(); change(view, "fantasy-game", "1"); await view.settle();
    assert.deepEqual(nodes(view.render()).filter(node => node.type === "td" && String(node.props.children).endsWith("-CODE")).map(node => node.props.children), ["BAT-CODE", "PITCH-CODE"]);
    change(view, "fantasy-batting", "ORIGINAL BAT"); change(view, "fantasy-pitching", "ORIGINAL PITCH");
    const saving = submit(view);
    change(view, `fantasy-${changed}`, "NEW UNSAVED");
    pending.resolve(Response.json({ detail: "saved", batting_count: 1, pitching_count: 1 })); await saving;
    assert.equal(sent.batting, "ORIGINAL BAT");
    assert.equal(byId(view.render(), `fantasy-${changed}`).props.value, "NEW UNSAVED");
    assert.equal(byId(view.render(), `fantasy-${changed === "batting" ? "pitching" : "batting"}`).props.value, "");
    view.unmount();
  }
});

test("admin stale POST and DELETE successes and errors never update another game", async () => {
  for (const method of ["POST", "DELETE"]) for (const failure of [false, true]) {
    const pending = deferred(), reads = [];
    const view = mount("app/fantasy/admin/page.tsx", adminTransport(() => pending.promise, reads));
    await view.settle(); change(view, "fantasy-game", "1"); await view.settle();
    change(view, "fantasy-batting", "A BAT"); change(view, "fantasy-pitching", "A PITCH");
    let action;
    if (method === "POST") action = submit(view);
    else { button(view.render(), "경기 기록 삭제").props.onClick(); action = button(view.render(), "기록 삭제 확인").props.onClick(); }
    change(view, "fantasy-game", "2"); await view.settle();
    change(view, "fantasy-batting", "B BAT"); change(view, "fantasy-pitching", "B PITCH");
    const count = reads.length;
    pending.resolve(Response.json({ detail: "A RESPONSE", batting_count: 1, pitching_count: 1 }, { status: failure ? 400 : 200 }));
    await action; await view.settle();
    assert.equal(reads.length, count);
    assert.equal(byId(view.render(), "fantasy-batting").props.value, "B BAT");
    assert.equal(find(view.render(), node => node.props?.className === "successMessage" || node.props?.className === "errorMessage"), undefined);
    change(view, "fantasy-game", "1");
    assert.equal(byId(view.render(), "fantasy-batting").props.value, method === "POST" && !failure ? "" : "A BAT");
    view.unmount();
  }
});

test("admin POST and DELETE exclude immediate duplicate and overlapping mutations", async () => {
  for (const first of ["POST", "DELETE"]) {
    const pending = deferred(), calls = [];
    const view = mount("app/fantasy/admin/page.tsx", adminTransport((path, init) => { calls.push(init.method); return pending.promise; }));
    await view.settle(); change(view, "fantasy-game", "1"); await view.settle();
    change(view, "fantasy-batting", "BAT"); change(view, "fantasy-pitching", "PITCH");
    button(view.render(), "경기 기록 삭제").props.onClick();
    const post = () => submit(view), remove = button(view.render(), "기록 삭제 확인").props.onClick;
    const action = first === "POST" ? post() : remove();
    void post(); void remove();
    assert.deepEqual(calls, [first]);
    assert.equal(find(view.render(), node => node.props?.type === "submit").props.disabled, true);
    assert.equal(button(view.render(), first === "DELETE" ? "삭제 중…" : "기록 삭제 확인").props.disabled, true);
    pending.resolve(Response.json({ detail: "done", batting_count: 1, pitching_count: 1 })); await action; await view.settle(); view.unmount();
  }
});

const player = n => ({ external_code: String(n), name: `선수${n}`, team_name: "구단", fantasy_type: "BATTER", positions: [] });
const selection = (n, week) => ({ id: n, week, player: player(n), fantasy_type: "BATTER", weights: {} });
function boardTransport(extra = () => null) {
  return async (path, init = {}) => {
    const result = extra(path, init); if (result) return result;
    if (path.includes("week/")) return Response.json({ id: path.includes("current") ? 1 : 2, week_start: "2026-10-12", week_end: "2026-10-18", has_games: true });
    if (path.includes("score")) return Response.json({ score: "0" });
    if (path.includes("players")) return Response.json({ count: 9, results: path.includes("page=2") ? [player(9)] : Array.from({ length: 8 }, (_, i) => player(i + 1)) });
    return Response.json(path.includes("week=2") ? [selection(9, 2)] : []);
  };
}

test("filtered-empty last player page retains a working previous button", async () => {
  const view = mount("components/prediction-board.tsx", boardTransport(), false);
  await view.settle(); button(view.render(), "다음").props.onClick(); await view.settle();
  assert.ok(find(view.render(), node => node.props?.className === "emptyState"));
  assert.equal(button(view.render(), "이전").props.disabled, false);
  button(view.render(), "이전").props.onClick(); await view.settle();
  assert.equal(button(view.render(), "이전").props.disabled, true); view.unmount();
});

test("POST and DELETE refresh only the authoritative dashboard and ignore old-week reads", async () => {
  for (const method of ["POST", "DELETE"]) {
    const mutation = deferred(), oldRead = deferred(); let moved = false, oldReads = 0;
    const calls = [];
    const view = mount("components/prediction-board.tsx", boardTransport((path, init) => {
      calls.push([path, init.method]);
      if (init.method) return mutation.promise;
      if (moved && path.includes("week/")) return Response.json({ id: path.includes("current") ? 2 : 3, week_start: "2026-10-19", week_end: "2026-10-25", has_games: true });
      if (path.includes("selections/?week=2") && ++oldReads > 1) return oldRead.promise;
      if (path.includes("selections/?week=3")) return Response.json([selection(30, 3)]);
      return null;
    }), false);
    await view.settle();
    const tree = view.render();
    const action = method === "POST" ? button(tree, "교체").props.onClick() : button(tree, "선택 취소").props.onClick();
    moved = true;
    mutation.resolve(method === "POST" ? Response.json(selection(1, 2)) : new Response(null, { status: 204 }));
    await action; view.render(); await tick();
    oldRead.resolve(Response.json([selection(20, 2)])); await view.settle();
    assert.equal(calls.filter(([path, verb]) => !verb && path.includes("selections/?week=2")).length, 2);
    const upcoming = find(view.render(), node => node.props?.["aria-label"] === "다음 주 선택 선수");
    assert.match(JSON.stringify(upcoming), /선수30/); assert.doesNotMatch(JSON.stringify(upcoming), /선수20/);
    view.unmount();
  }
});

test("admin reload offers prior test payout cancellation without changing current-week payout target", async () => {
  const calls = []; let cancelled = false;
  const current = { week_id: 2, week_start: "2026-10-12", week_end: "2026-10-18", status: "OPEN", is_test_settlement: false };
  const view = mount("components/prediction-board.tsx", boardTransport((path, init) => {
    if (!path.includes("test-settlement")) return null;
    calls.push([path, init.method]);
    if (init.method === "DELETE") { cancelled = true; return Response.json({ ...current, week_id: 1, detail: "cancelled", user_count: 1, reversed_points: 10 }); }
    return Response.json({ ...current, cancellable_weeks: cancelled ? [] : [{ week_id: 1, week_start: "2026-10-05", week_end: "2026-10-11" }] });
  }));
  await view.settle();
  const selector = find(view.render(), node => node.type === "select" && nodes(node).some(child => child.type === "option" && child.props.value === 1));
  assert.ok(selector); assert.equal(button(view.render(), "지급 취소").props.disabled, true);
  selector.props.onChange({ target: { value: "1" } });
  await button(view.render(), "지급 취소").props.onClick(); await view.settle();
  assert.ok(calls.some(([path, method]) => method === "DELETE" && path.endsWith("?week_id=1")));
  assert.equal(button(view.render(), "현재 점수로 포인트 지급").props.disabled, false);
  assert.equal(button(view.render(), "지급 취소").props.disabled, true); view.unmount();
});
