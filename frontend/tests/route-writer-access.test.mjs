import assert from "node:assert/strict";
import { after, test } from "node:test";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { createRequire } from "node:module";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const React = require("react");
const writer = readFileSync(new URL("../components/route-writer.tsx", import.meta.url), "utf8");
const planner = readFileSync(new URL("../components/nearby-route-planner.tsx", import.meta.url), "utf8");
const guide = readFileSync(new URL("../components/route-guide.tsx", import.meta.url), "utf8");
const stadium = { code: "JAMSIL", name: "잠실", lat: 37.5, lng: 127 };
const stop = name => ({ name, category: "직접 지정", lat: 37.5, lng: 127, isMapPoint: true });
const scratch = mkdtempSync(join(tmpdir(), "kbo-writer-access-test-"));
after(() => rmSync(scratch, { recursive: true }));
for (const name of ["client-id", "route-draft", "stadiums", "stadium-locations", "google-lodging", "community-rich-content", "course-directions", "drawn-course", "chat/course", "chat/current-course", "chat/writer-state", "chat/route-path", "course-state", "nearby-places", "route-content"]) {
  const source = readFileSync(new URL(`../lib/${name}.ts`, import.meta.url), "utf8");
  mkdirSync(dirname(join(scratch, `${name}.js`)), { recursive: true });
  writeFileSync(join(scratch, `${name}.js`), ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText);
}
const draftService = createRequire(join(scratch, "entry.cjs"))("./route-draft.js");
const originalData = { stadiumCode: "JAMSIL", title: "원래 초안", content: "복원할 이야기", duration: "반나절", tags: ["첫 직관"], stops: [stop("원래 장소")], start: { lat: 37.4, lng: 127.1 }, tab: "write", travelMode: "transit" };
const originalRaw = JSON.stringify({ version: 1, revision: "original", updatedAt: "2026-09-01T00:00:00.000Z", data: originalData });

// Execute the actual component and its handlers, with hook state and boundary services stubbed.
// No DOM/test dependency is needed; Kakao rendering is covered by the existing planner tests.
function harness(status = "anonymous", sample = false, userId = 1, draftContext = "new:JAMSIL", availableStadiums = [stadium]) {
  let cursor = 0;
  let auth = { status, user: status === "authenticated" ? { id: userId } : null };
  const state = [], effects = [], listeners = new Map(), calls = [];
  const storage = new Map([[draftService.ROUTE_DRAFT_PREFIX + draftContext, originalRaw]]);
  const browserStorage = {
    getItem(key) { calls.push(["getItem", key]); return storage.get(key) ?? null; },
    setItem(key, value) { calls.push(["setItem", key]); storage.set(key, value); },
    removeItem(key) { calls.push(["removeItem", key]); storage.delete(key); },
  };
  const hooks = {
    useState(initial) { const i = cursor++; if (!(i in state)) state[i] = typeof initial === "function" ? initial() : initial; return [state[i], value => { state[i] = typeof value === "function" ? value(state[i]) : value; }]; },
    useRef(value) { const i = cursor++; return state[i] ??= { current: value }; },
    useCallback(fn) { cursor++; return fn; },
    useSyncExternalStore() { cursor++; return true; },
    useEffect(fn) { cursor++; effects.push(fn); },
    useLayoutEffect(fn) { cursor++; effects.push(fn); },
  };
  const chat = { onContextChange() {}, context: {}, onReset() {}, pending: "", registerCourseTarget(target) { chat.target = target; }, takePendingCourse() {} };
  const drafts = {
    browserDraftStorage() { calls.push("storage"); return browserStorage; },
    readRouteDraft(storage, key) { calls.push(["read", key]); return draftService.readRouteDraft(storage, key); },
    latestNewDraftStadium: draftService.latestNewDraftStadium,
    recoverRouteDraft: draftService.recoverRouteDraft,
    saveRouteDraft(storage, key, data, expectedRaw) { calls.push(["write", key]); return draftService.saveRouteDraft(storage, key, data, expectedRaw); },
    removeRouteDraft(storage, key, expectedRaw) { calls.push(["delete", key]); return draftService.removeRouteDraft(storage, key, expectedRaw); },
    createDraftAutosave(flush) { calls.push("autosave"); return { changed() {}, flush, stop(first) { if (first) flush(); } }; },
  };
  const win = {
    location: { href: "http://localhost/routes/new", origin: "http://localhost", pathname: "/routes/new", search: "" },
    addEventListener(name, fn) { listeners.set(name, fn); }, removeEventListener() {},
    setTimeout() { return 1; },
    history: { state: null, replaceState(_state, _unused, url) { win.location.href = String(url); } },
  };
  const doc = { addEventListener(name, fn) { listeners.set(name, fn); }, removeEventListener() {}, activeElement: null };
  const routeService = { useRoutes: () => [], useRoutesReady: () => true, useRoutesError: () => "", saveRoute: async () => { calls.push("server-save"); return { id: "saved" }; } };
  const sandbox = { exports: {}, window: win, document: doc, URL, clearTimeout() {}, requestAnimationFrame(fn) { fn(); }, console,
    require(id) {
      if (id === "react") return hooks;
      if (id === "react/jsx-runtime") return require(id);
      if (id === "./use-course-state") {
        const hook = { ...sandbox, exports: {} };
        vm.runInNewContext(ts.transpileModule(readFileSync(new URL("../components/use-course-state.ts", import.meta.url), "utf8"),
          { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText, hook);
        return hook.exports;
      }
      if (id.endsWith("/course-state")) return createRequire(join(scratch, "entry.cjs"))("./course-state.js");
      if (id === "next/navigation") return { useRouter: () => ({ push(url) { calls.push(["navigate", url]); } }) };
      if (id === "next/link") return { default: "a", __esModule: true };
      if (id.endsWith("/member-auth")) return { useMemberAuth: () => auth };
      if (id.endsWith("/route-draft")) return drafts;
      if (id.endsWith("/chat/writer-state")) return createRequire(join(scratch, "entry.cjs"))("./chat/writer-state.js");
      if (id.startsWith("@/lib/chat/")) return createRequire(join(scratch, "entry.cjs"))(`./chat/${id.split("/").at(-1)}.js`);
      if (id.endsWith("/routes")) return routeService;
      if (id.endsWith("/chat-provider")) return { useChat: () => chat, ChatSampleProvider: "sample-provider" };
      if (id.endsWith("/community-rich-content")) return createRequire(join(scratch, "entry.cjs"))("./community-rich-content.js");
      if (id.endsWith("/route-content")) return { routeContentToText: value => value };
      if (id.endsWith("/team-community")) return { teamBoards: [] };
      if (id.endsWith("/drawn-course")) return { withCourseStart: value => value };
      if (id.endsWith("/nearby-places")) return { MAX_ROUTE_STOPS: 12 };
      if (id.endsWith("/baseball/client")) return { fetchBaseballStadiums: async () => { calls.push("stadiums"); return { results: availableStadiums }; } };
      if (id.endsWith("/baseball/adapters")) return { adaptStadium: value => value };
      return new Proxy({}, { get: (_, name) => name });
    }, AbortController,
  };
  vm.runInNewContext(ts.transpileModule(writer + "\nexports.WriterForm = WriterForm;", { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, esModuleInterop: true } }).outputText, sandbox);
  const render = (component = sandbox.exports.WriterForm, props = { stadiums: [stadium], initial: stadium, sample }) => { cursor = 0; effects.length = 0; return component(props); };
  return { render, chat, wrapper: sandbox.exports.default, calls, storage, listeners, setAuth(value) { auth = value; }, mountEffects() { return effects.map(fn => fn()).filter(fn => typeof fn === "function"); } };
}
function elements(node) {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!React.isValidElement(node)) return [];
  return [node, ...elements(node.props.children)];
}
const find = (tree, predicate) => elements(tree).find(predicate);
const plannerProps = tree => find(tree, item => item.type === "NearbyRoutePlanner").props;

test("a linked conversation reopens the itinerary without copying its story, and refresh preserves the draft", () => {
  const h = harness("authenticated");
  h.storage.clear();
  const writerKey = "chat:member:1:session:42";
  const writerDraft = { ...originalData, title: "직접 지은 제목", chatCourseKey: writerKey, plannerCompleted: true,
    start: { ...originalData.start, name: "잠실새내역" },
    stops: [{ name: "식당", category: "먹거리", lat: 37.5, lng: 127.1, placeId: "food" }] };
  const course = { stadiumCode: "JAMSIL", places: [{ name: "식당", lat: 37.5, lng: 127.1, category: "FOOD", phase: "BEFORE" }], notes: [], writerKey, writerDraft,
    writerState: { title: writerDraft.title, origin: writerDraft.start, completed: true } };
  let pending = course;
  h.chat.takePendingCourse = () => { const value = pending; pending = null; return value; };
  h.render(); h.mountEffects();
  let tree = h.render(); h.mountEffects();
  assert.equal(find(tree, item => item.props.id === "route-title").props.value, "");
  assert.doesNotMatch(JSON.stringify(find(tree, item => item.type === "CommunityRichEditor").props.initial), /복원할 이야기/);
  assert.deepEqual(plannerProps(tree).initialStart, writerDraft.start);
  assert.equal(plannerProps(tree).initialCompleted, true);
  let prevented = false;
  h.listeners.get("beforeunload")({ preventDefault() { prevented = true; } });
  assert.equal(prevented, false, "a safely persisted member draft can reload without discarding its state");
  h.listeners.get("pagehide")();
  const saved = draftService.parseRouteDraft(h.storage.get(draftService.ROUTE_DRAFT_PREFIX + "new:JAMSIL"));
  assert.equal(saved.data.title, "");
  assert.equal(saved.data.content, "");
  assert.equal(saved.data.plannerCompleted, true);
  assert.deepEqual(saved.data.start, writerDraft.start);
  plannerProps(tree).onCompletionChange(false);
  tree = h.render(); h.mountEffects();
  h.listeners.get("pagehide")();
  assert.equal(draftService.parseRouteDraft(h.storage.get(draftService.ROUTE_DRAFT_PREFIX + writerKey)).data.plannerCompleted, false);
});

test("restoring a server course leaves the story editor empty and mounted", () => {
  const h = harness("authenticated");
  h.storage.clear();
  let pending = { stadiumCode: "JAMSIL", places: [stop("식당")], notes: [], title: "원래 코스",
    content: "식사 후 구장으로 이동합니다.", writerKey: "chat:member:1:session:88" };
  h.chat.takePendingCourse = () => { const value = pending; pending = null; return value; };
  const initial = find(h.render(), item => item.type === "CommunityRichEditor"); h.mountEffects();
  const tree = h.render(); h.mountEffects();
  const editor = find(tree, item => item.type === "CommunityRichEditor");
  assert.strictEqual(editor.props.initial, initial.props.initial);
  assert.equal(editor.key, initial.key);
  assert.doesNotMatch(JSON.stringify(editor.props.initial), /식사 후 구장으로 이동합니다/);
  assert.equal(find(tree, item => item.props.id === "route-title").props.value, "");
  assert.equal(plannerProps(tree).stops[0].name, "식당");
});

test("chat generation, edits, appends and undo preserve the typed story, images and editor instance", () => {
  const h = harness("authenticated");
  h.storage.clear();
  let tree = h.render(); h.mountEffects();
  const editor = find(tree, item => item.type === "CommunityRichEditor");
  const { plainRichDoc } = createRequire(join(scratch, "entry.cjs"))("./community-rich-content.js");
  const doc = plainRichDoc("직접 쓴 후기");
  doc.blocks.push({ type: "image", id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" });
  editor.props.onChange(doc, "직접 쓴 후기\n[이미지]");
  find(tree, item => item.props.id === "route-title").props.onChange({ target: { value: "내 제목" } });
  tree = h.render(); h.mountEffects();
  for (const [how, edit] of [["replace", false], ["replace", true], ["append", false]]) {
    const before = plannerProps(tree).stops;
    const undo = h.chat.target.apply({ stadiumCode: "JAMSIL", notes: [], edit, title: "자동 제목", content: "자동 설명",
      places: [{ name: "식당", lat: 37.5, lng: 127.1, category: "FOOD", phase: "BEFORE" }] }, how);
    tree = h.render(); h.mountEffects();
    assert.equal(plannerProps(tree).stops[0].name, "식당");
    for (const undone of [false, true]) {
      if (undone) { assert.equal(undo(), true); tree = h.render(); h.mountEffects(); }
      const currentEditor = find(tree, item => item.type === "CommunityRichEditor");
      assert.strictEqual(currentEditor.props.initial, editor.props.initial);
      assert.equal(currentEditor.key, editor.key);
      assert.equal(find(tree, item => item.props.id === "route-title").props.value, "내 제목");
      h.listeners.get("pagehide")();
      const saved = draftService.parseRouteDraft(h.storage.get(draftService.ROUTE_DRAFT_PREFIX + "new:JAMSIL"));
      assert.equal(saved.data.content, "직접 쓴 후기\n[이미지]");
      assert.deepEqual(saved.data.contentDoc, doc);
    }
    assert.deepEqual(plannerProps(tree).stops, before);
  }
});

test("opening an older conversation never silently replaces a populated writer", () => {
  const h = harness("authenticated");
  let pending = { stadiumCode: "JAMSIL", writerKey: "chat:member:1:another:99", places: [], notes: [] };
  h.chat.takePendingCourse = () => { const value = pending; pending = null; return value; };
  h.render(); h.mountEffects();
  const tree = h.render();
  assert.equal(find(tree, item => item.props.id === "route-title").props.value, originalData.title);
  assert.ok(find(tree, item => item.type === "button" && item.props.children === "코스 불러오기"));
});

test("anonymous new writer loads public stadiums, but editing remains login-only", async () => {
  const h = harness();
  h.render(h.wrapper, {}); h.mountEffects();
  await Promise.resolve();
  const fresh = h.render(h.wrapper, {});
  assert.ok(h.calls.includes("stadiums"));
  assert.equal(typeof fresh.type, "function");
  assert.ok(!h.calls.includes("storage"), "guest navigation never reads member drafts");
  const edit = h.render(h.wrapper, { editId: "private" });
  assert.equal(edit.type, "main");
  assert.ok(find(edit, item => item.props.href?.startsWith("/login?next=")));
});

test("default navigation resumes the latest draft slot, stays stable and respects an explicit stadium", async () => {
  const changwon = { ...stadium, code: "CHANGWON", name: "창원" };
  const daejeon = { ...stadium, code: "DAEJEON", name: "대전" };
  const h = harness("authenticated", false, 1, "new:JAMSIL", [stadium, changwon, daejeon]);
  h.storage.set(draftService.ROUTE_DRAFT_PREFIX + "new:CHANGWON", JSON.stringify({ version: 1, revision: "cleared", updatedAt: "2026-10-08T00:00:00Z",
    data: { ...originalData, stadiumCode: "DAEJEON", title: "", content: "" } }));
  h.render(h.wrapper, {}); h.mountEffects();
  await Promise.resolve();
  const resumed = h.render(h.wrapper, {});
  assert.equal(resumed.props.initial.code, "CHANGWON", "load the storage slot, not its changed stadium");
  h.storage.set(draftService.ROUTE_DRAFT_PREFIX + "new:JAMSIL", originalRaw.replace("2026-09-01", "2026-10-09"));
  assert.equal(h.render(h.wrapper, {}).props.initial.code, "CHANGWON", "a draft saved in another tab cannot remount the active writer");
  assert.equal(h.render(h.wrapper, { initialStadium: "JAMSIL" }).props.initial.code, "JAMSIL");
});

test("guest course mutations stay in memory, cannot save, and protect unsaved navigation", async () => {
  const h = harness();
  let tree = h.render(); h.mountEffects();
  assert.equal(find(tree, item => item.props.id === "route-title"), undefined);
  assert.equal(find(tree, item => item.type === "CommunityRichEditor"), undefined);
  assert.equal(find(tree, item => item.props.className === "writer-save-area"), undefined);
  assert.equal(plannerProps(tree).allowSave, false);
  assert.equal(h.calls.length, 0);
  plannerProps(tree).onChange([{ ...stop("A"), isMapPoint: false }, { ...stop("B"), isMapPoint: false }]);
  tree = h.render();
  assert.deepEqual(Array.from(plannerProps(tree).stops, item => item.name), ["A", "B"]);
  plannerProps(tree).onChange([{ ...stop("B"), isMapPoint: false }, { ...stop("A"), isMapPoint: false }]);
  tree = h.render();
  assert.deepEqual(Array.from(plannerProps(tree).stops, item => item.name), ["B", "A"]);
  await plannerProps(tree).onSaveCourse();
  find(tree, item => item.type === "form").props.onSubmit({ preventDefault() {} });
  assert.equal(h.calls.length, 0);
  let prevented = false;
  h.listeners.get("beforeunload")({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  h.listeners.get("click")({ button: 0, target: { closest: () => ({ href: "http://localhost/routes", hasAttribute: () => false }) }, preventDefault() {}, stopPropagation() {} });
  tree = h.render();
  find(tree, item => item.type === "button" && item.props.className === "button button-primary" && item.props.children === "떠나기").props.onClick();
  assert.deepEqual(h.calls, [["navigate", "/routes"]]);
  assert.deepEqual([...h.storage], [[draftService.ROUTE_DRAFT_PREFIX + "new:JAMSIL", originalRaw]]);
});

test("a completed course can select another stadium and clear the old stops", () => {
  const h = harness();
  const other = { code: "SAJIK", name: "사직", lat: 35.194, lng: 129.061 };
  const props = { stadiums: [stadium, other], initial: stadium };
  let tree = h.render(undefined, props);
  plannerProps(tree).onChange([stop("기존 코스")]);
  plannerProps(tree).onCompletionChange(true);
  tree = h.render(undefined, props);
  const select = find(tree, item => item.props.id === "route-stadium");
  assert.ok(!select.props.disabled);
  select.props.onChange({ target: { value: "SAJIK" } });
  tree = h.render(undefined, props);
  find(tree, item => item.type === "button" && item.props.children === "구장 바꾸기").props.onClick();
  tree = h.render(undefined, props);
  assert.equal(plannerProps(tree).stadium.code, "SAJIK");
  assert.equal(plannerProps(tree).stops.length, 0);
  assert.equal(plannerProps(tree).initialStart, undefined);
  assert.equal(plannerProps(tree).courseApplied, null);
});

test("origin-only map context and generated course keep the same origin, with undo restoring the pin", () => {
  const h = harness();
  const origin = { name: "직접 찍은 출발지", category: "직접 지정", lat: 37.504, lng: 127.002, isMapPoint: true };
  let tree = h.render(); h.mountEffects();
  plannerProps(tree).onChange([origin]);
  h.chat.onContextChange = context => { h.chat.context = context; };
  tree = h.render(); h.mountEffects();
  assert.equal(h.chat.context.currentCourse.places.length, 0);
  assert.equal(h.chat.context.currentCourse.writerState.origin.lat, origin.lat);
  const point = { lat: origin.lat, lng: origin.lng, name: origin.name };
  const undo = h.chat.target.apply({ stadiumCode: "JAMSIL", notes: [], origin: point,
    writerState: { title: "", origin: point, completed: false }, places: [
      { name: "초밥집", lat: 37.503, lng: 127.002, category: "FOOD", phase: "BEFORE" },
      { name: "공원", lat: 37.502, lng: 127.001, category: "WALK", phase: "BEFORE" },
      { name: "잠실", lat: 37.5, lng: 127, category: "STADIUM", phase: "GAME" },
    ] }, "replace");
  tree = h.render(); h.mountEffects();
  assert.deepEqual(plannerProps(tree).initialStart, point);
  assert.deepEqual(Array.from(plannerProps(tree).stops, p => p.name), ["초밥집", "공원", "잠실"]);
  undo(); tree = h.render();
  assert.deepEqual(plannerProps(tree).initialStart, point);
  assert.deepEqual(Array.from(plannerProps(tree).stops), []);
});

test("members restore valid original new/edit/copy drafts and retain canonical autosave keys", async () => {
  assert.ok(draftService.parseRouteDraft(originalRaw));
  for (const context of ["new:JAMSIL", "edit:42", "copy:42"]) {
    const h = harness("authenticated", false, 1, context);
    const props = { stadiums: [stadium], initial: stadium, ...(context.startsWith("new:") ? {} : { existing: { id: "42", title: "저장된 코스", content: "", stadium: stadium.name, stops: [], owned: true }, copying: context.startsWith("copy:") }) };
    const tree = h.render(undefined, props); h.mountEffects();
    assert.equal(plannerProps(tree).allowSave, true);
    assert.ok(find(tree, item => item.type === "CommunityRichEditor"));
    assert.equal(find(tree, item => item.props.id === "route-title").props.value, originalData.title);
    assert.deepEqual(plannerProps(tree).stops, createRequire(join(scratch, "entry.cjs"))("./course-state.js").createCourseState(originalData).data.stops);
    assert.deepEqual(plannerProps(tree).initialStart, originalData.start);
    assert.equal(plannerProps(tree).travelMode, originalData.travelMode);
    assert.ok(h.calls.includes("autosave"));
    assert.ok(h.calls.some(call => call[0] === "read" && call[1] === context));
    plannerProps(tree).onChange([{ ...stop("new"), isMapPoint: false }]);
    const changed = h.render(undefined, props); h.mountEffects();
    find(changed, item => item.type === "button" && item.props.children === "임시저장").props.onClick();
    assert.equal(draftService.parseRouteDraft(h.storage.get(draftService.ROUTE_DRAFT_PREFIX + context)).data.stops[0].name, "new");
    await plannerProps(changed).onSaveCourse();
    assert.equal(h.storage.has(draftService.ROUTE_DRAFT_PREFIX + context), false);
    const saved = h.render(undefined, props);
    plannerProps(saved).onChange([{ ...stop("after save"), isMapPoint: false }]);
    const updated = h.render(undefined, props); h.mountEffects();
    find(updated, item => item.type === "button" && item.props.children === "임시저장").props.onClick();
    assert.equal(draftService.parseRouteDraft(h.storage.get(draftService.ROUTE_DRAFT_PREFIX + "edit:saved")).data.stops[0].name, "after save");
  }
});

test("account changes and logout remount the writer without changing baseline shared draft keys", async () => {
  const h = harness("authenticated");
  h.render(h.wrapper, {}); h.mountEffects();
  await Promise.resolve();
  const member = h.render(h.wrapper, {});
  h.setAuth({ status: "authenticated", user: { id: 2 } });
  const otherMember = h.render(h.wrapper, {});
  h.setAuth({ status: "anonymous", user: null });
  const guest = h.render(h.wrapper, {});
  assert.notEqual(member.key, otherMember.key);
  assert.notEqual(otherMember.key, guest.key);
});

test("authenticated guide samples never restore drafts, upload images, or server-save", async () => {
  const h = harness("authenticated", true);
  const tree = h.render(); h.mountEffects();
  assert.equal(find(tree, item => item.type === "CommunityRichEditor").props.imageUploadDisabled, true);
  plannerProps(tree).onChange([stop("sample")]);
  await plannerProps(tree).onSaveCourse();
  find(tree, item => item.type === "form").props.onSubmit({ preventDefault() {} });
  assert.equal(h.calls.length, 0);
  assert.equal(h.listeners.has("beforeunload"), false);
});

test("identity changes remount the form, and guest completion/guide skip unavailable saves", () => {
  assert.match(writer, /key=\{`\$\{authStatus\}:\$\{user\?\.id \?\? "guest"\}/);
  assert.match(writer, /if \(!activeForm.current\) return;\s*const persisted = await saveRoute\(route\);\s*if \(!activeForm.current\) return;/);
  assert.match(planner, /const canSaveCourse = allowSave && canComplete/);
  assert.match(planner, /originReplacement=\{courseCompleted \? allowSave \?/);
  const selectSteps = guide.slice(guide.indexOf("const selectSteps ="), guide.indexOf("const tour = driver"));
  const js = ts.transpileModule(selectSteps + "\nexports.selectSteps = selectSteps;", {}).outputText;
  for (const includeMemberSteps of [false, true]) {
    const context = { exports: {}, includeMemberSteps };
    vm.runInNewContext(js, context);
    const steps = ["내 코스", "코스 저장", "코스 저장 완료", "가이드를 마쳤어요"].map(title => ({ popover: { title } }));
    assert.equal(context.exports.selectSteps(steps).length, includeMemberSteps ? 4 : 2);
  }
});
