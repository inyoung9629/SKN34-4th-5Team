import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
const read = path => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const source = read("components/chat-composer-tools.tsx");
const ast = ts.createSourceFile("tools.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const body = ast.statements.filter(node => !ts.isImportDeclaration(node)).map(node => node.getText(ast)).join("\n");
const groupIds = ["web_research", "schedule", "standings", "players", "baseball_stats", "rules", "stadium_info", "carry_in", "parking_transport", "community", "nearby_places", "tourism", "directions", "courses", "weather", "day_plan"];
let menuGroups = groupIds.map(id => ({ id, label: id }));
let menuError = "";
let stateIndex = 0;
const refs = [];
const chat = { draft: "", onDraftChange(value) { this.draft = value; }, onInlineToolSelect() {}, attachments: [], toolGroupIds: ["search"], onToolGroupsChange() {} };
const exports = {};
runInNewContext(ts.transpileModule(body, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText, {
  exports, require, createContext: value => ({ Provider: "provider", value }),
  useChat: () => chat, useMemberAuth: () => ({ status: "anonymous" }),
  useState: initial => [stateIndex++ === 0 ? menuGroups : stateIndex === 2 ? menuError : initial, () => {}], useEffect() {}, useId: () => "composer-menu",
  useRef: initial => { const ref = { current: initial }; refs.push(ref); return ref; },
  Icon: () => null, Image: () => null,
  window: { innerWidth: 390, innerHeight: 844 },
});
const children = { type: "textarea", props: {} };
const actions = { type: "button", props: { type: "button", "aria-label": "질문 보내기" } };
const tree = exports.ChatComposerTools({ disabled: false, available: true, children, hint: "야구가 궁금한 모든 순간", actions });
const flatten = tree => !tree || typeof tree !== "object" ? [] : [tree, ...[tree.props?.children].flat(Infinity).flatMap(flatten)];
const nodes = flatten(tree);
const find = className => nodes.find(node => node.props?.className === className);

test("shared composer keeps attachment cards and chips above text, then add and send in one toolbar", () => {
  const top = tree.props.children;
  assert.equal(top[0].type, exports.ChatAttachmentCards);
  assert.ok(!nodes.some(node => node.props?.className === "chat-selected-groups"));
  assert.equal(top[1].props.children.props.children, children);
  assert.equal(top[2].props.className, "chat-composer-toolbar");
  assert.equal(top[2].props.children[0].props.className, "chat-composer-add-row");
  assert.equal(top[2].props.children[1], actions);
  assert.equal(find("chat-composer-hint").props.children, "야구가 궁금한 모든 순간");
  assert.ok(!nodes.some(node => node.type === "form"));
  for (const button of nodes.filter(node => node.type === "button")) assert.equal(button.props.type, "button");
});

test("workspace and popup/embedded pass textarea, existing hints and send/stop to one shared controller", () => {
  for (const name of ["chat-workspace", "chat-popup"]) {
    const caller = ts.createSourceFile(`${name}.tsx`, read(`components/${name}.tsx`), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const tools = [];
    const visit = node => {
      if (ts.isJsxElement(node) && node.openingElement.tagName.getText(caller) === "ChatComposerTools") tools.push(node);
      ts.forEachChild(node, visit);
    };
    visit(caller);
    assert.equal(tools.length, 1);
    assert.ok(tools[0].children.some(node => ts.isJsxSelfClosingElement(node) && node.tagName.getText(caller) === "ChatInlineInput"));
    const attrs = tools[0].openingElement.attributes.properties;
    assert.ok(attrs.some(node => node.name?.getText(caller) === "hint"));
    const action = attrs.find(node => node.name?.getText(caller) === "actions").getText(caller);
    assert.match(action, /sendLabel/);
    assert.match(caller.text, /const sendLabel = .*질문 보내기/);
    assert.match(action, /답변 생성 중단/);
    assert.match(action, /chat\.onCancel/);
  }
});

test("shared native input handles IME paste and send boundaries", () => {
  const code = read("components/chat-inline-input.tsx");
  assert.match(code, /chat.onPasteText\(text\)/);
  assert.match(code, /getData\("text\/plain"\)/);
  assert.match(code, /chat.onCompositionChange\(true\)/);
  assert.match(code, /event.keyCode === 229/);
  assert.match(code, /chip.contentEditable = "false"/);
  assert.doesNotMatch(code, /dangerouslySetInnerHTML|chat-inline-mirror/);
});

test("sent attachments precede user bubbles in both surfaces and wrap in a right-aligned column", () => {
  for (const [name, prefix] of [["chat-workspace", "workspace"], ["chat-popup", "chat-popup"]]) {
    const code = read(`components/${name}.tsx`);
    assert.match(code, /<ChatAttachmentCards items=\{\(message.attachments \?\? \[\]\)\.filter/);
    assert.ok(code.includes(`<div className="${prefix}-user-bubble"><ChatUserContent attachments={message.attachments}`));
    assert.doesNotMatch(code, /\(chat\.pending \|\| chat\.failed\) && <article/);
    assert.match(code, /content=\{chat\.failed\}/);
  }
  const css = read("styles/chat-composer-tools.css");
  assert.match(css, /flex-direction: column; align-items: flex-end; flex-wrap: nowrap; min-width: 0; gap: 8px;/);
  assert.match(css, /justify-content: flex-end; width: 100%; min-width: 0; max-height: none; overflow: visible; padding: 0;/);
  const urlRules = [...css.matchAll(/\.chat-attachment-card\.is-url \{([^}]*)\}/g)];
  assert.equal(urlRules.length, 0); // URL references render inline, never as cards.
});

test("toolbar centers both ends and truncates hint instead of wrapping or shrinking controls", () => {
  const css = read("styles/chat-composer-tools.css");
  assert.match(css, /\.chat-composer-toolbar \{[^}]*display: flex;[^}]*align-items: center;[^}]*justify-content: space-between;/);
  assert.match(css, /\.chat-composer-toolbar > :last-child \{ flex-shrink: 0;/);
  assert.match(css, /\.chat-composer-add \{[^}]*flex-shrink: 0;/);
  const addStyle = css.match(/\.chat-composer-add \{([^}]*)\}/)[1];
  assert.match(addStyle, /border: none;/);
  assert.match(addStyle, /box-shadow: none;/);
  assert.doesNotMatch(addStyle, /outline:/);
  assert.match(css, /\.chat-composer-add:focus-visible[^}]*outline: 2px solid #246bf3; outline-offset: 3px;/);
  assert.match(css, /\.chat-composer-hint \{[^}]*text-overflow: ellipsis;[^}]*white-space: nowrap;/);
});

test("native popover anchors above the relocated add button and restores focus on Escape", () => {
  let focused = 0, hidden = 0, prevented = 0, stopped = 0;
  const style = {};
  refs[0].current = { style, querySelector: () => ({ focus: () => focused++ }), hidePopover: () => hidden++ };
  refs[1].current = { getBoundingClientRect: () => ({ left: 28, top: 760 }), focus: () => focused++ };
  const menu = find("chat-composer-menu");
  assert.equal(menu.props.popover, "auto");
  assert.equal(find("chat-composer-add").props.popoverTarget, menu.props.id);
  menu.props.onToggle({ newState: "open" });
  assert.equal(style.left, "28px");
  assert.equal(style.bottom, "92px");
  assert.equal(style.maxHeight, "480px");
  assert.equal(focused, 1);
  menu.props.onKeyDown({ key: "Escape", preventDefault: () => prevented++, stopPropagation: () => stopped++ });
  assert.equal(hidden, 1);
  assert.equal(focused, 2);
  assert.equal(prevented, 1);
  assert.equal(stopped, 1);
  refs[1].current.getBoundingClientRect = () => ({ left: 360, top: 220 });
  menu.props.onToggle({ newState: "open" });
  assert.equal(style.left, "54px");
  assert.equal(style.maxHeight, "196px");
});

const render = props => {
  stateIndex = 0;
  return flatten(exports.ChatComposerTools({ disabled: false, available: true, children, hint: "", actions, ...props }));
};

test("manual menu and chips show exactly fifteen visible server groups with category nouns and distinct capability icons", () => {
  const server = readFileSync(new URL("../../backend/llm/views/attachments.py", import.meta.url), "utf8");
  const serverIds = [...server.split("TOOL_GROUP_LABELS = {")[1].split("}")[0].matchAll(/"([a-z_]+)":/g)].map(match => match[1]);
  assert.deepEqual(groupIds, serverIds);
  chat.toolGroupIds = [...groupIds];
  const rendered = render();
  const rows = rendered.filter(node => node.props?.className === "chat-tool-row");
  const chips = flatten(rendered.find(node => node.props?.className === "chat-selected-groups")).filter(node => node.type === "button");
  const iconSource = read("components/icons.tsx");
  const visibleIds = groupIds.filter(id => id !== "weather");
  assert.deepEqual(Array.from(rows, row => row.key), visibleIds);
  assert.equal(chips.length, 0);
  assert.equal(rows.length, 15);
  assert.equal(chips.length, 0);
  assert.deepEqual(Array.from(rows, row => row.props.children[1].props.children[1].props.children), ["웹", "야구", "야구", "야구", "야구", "야구", "구장", "구장", "교통", "커뮤니티", "구장", "여행", "교통", "코스", "코스"]);
  assert.deepEqual(Array.from(rows, row => row.props.children[0].props.name), ["book", "calendar", "trophy", "userPlus", "chart", "book", "stadium", "stadium", "car", "chat", "pin", "map", "route", "heart", "clock"]);
  rows.forEach((row, index) => {
    const [icon, copy] = row.props.children;
    assert.notEqual(icon.props.name, "sparkles");
    assert.match(iconSource, new RegExp(`\\b${icon.props.name}:`));
    assert.equal(exports.presentationFor(visibleIds[index]).icon, icon.props.name);
    assert.equal(copy.props.children[0].props.children, visibleIds[index]);
    assert.equal(row.type, "button");
    assert.equal(row.props.type, "button");
    assert.equal(row.props["data-selected"], true);
    assert.equal(row.props["aria-labelledby"], copy.props.children[0].props.id);
    assert.equal(row.props["aria-describedby"], copy.props.children[1].props.id);
    assert.equal(row.props.onKeyDown, undefined); // Native Enter, Space and Tab remain intact.
    assert.equal(row.props.tabIndex, undefined);
    assert.ok(!flatten(row).some(node => node.type === "input"));
  });
  assert.ok(rendered.some(node => node.type === "legend" && node.props.children === "도구"));
  assert.ok(rendered.some(node => node.type === "h3" && node.props.children === "추가"));
});

test("historical weather stays compatible without a visible or orphan chip, including before groups load", () => {
  chat.toolGroupIds = ["weather"];
  assert.ok(!render().some(node => node.props?.className === "chat-selected-groups"));
  menuGroups = [];
  assert.ok(!render().some(node => node.props?.className === "chat-selected-groups"));
  menuGroups = groupIds.map(id => ({ id, label: id }));
  chat.toolGroupIds = ["weather", "rules"];
  const rendered = render();
  const chips = flatten(rendered.find(node => node.props?.className === "chat-selected-groups")).filter(node => node.type === "button");
  assert.equal(chips.length, 0);
  assert.deepEqual(chat.toolGroupIds, ["weather", "rules"]);
});

test("automatic weather capability and client compatibility remain available", () => {
  const registry = readFileSync(new URL("../../backend/llm/v2/middleware/dynamic_tools.py", import.meta.url), "utf8");
  assert.match(registry, /"weather": \("get_games", "get_stadium", "get_weather"\)/);
  assert.match(read("lib/chat/client.ts"), /export const TOOL_GROUP_IDS = \[[^\]]*"weather"/);
});

test("tool rows close the menu and dispatch inline insertion without toggling selection", () => {
  chat.toolGroupIds = ["schedule"];
  const rendered = render();
  const rows = rendered.filter(node => node.props?.className === "chat-tool-row");
  let selected, closed = 0;
  refs.at(-4).current = { hidePopover() { closed++; } };
  refs.at(-1).current = group => { selected = group; };
  rows[0].props.onClick();
  assert.equal(selected.id, "web_research");
  assert.equal(closed, 1);
  assert.deepEqual(chat.toolGroupIds, ["schedule"]);
  assert.equal(rows[0].props["aria-pressed"], undefined);
});

test("shared plus menu keeps file attachments but no manual URL item or dialog", () => {
  const rendered = render();
  const menu = rendered.find(node => node.props?.className === "chat-composer-menu");
  const buttons = menu.props.children.filter(node => node.type === "button");
  assert.equal(buttons.length, 1);
  assert.equal(buttons[0].props.children[1].props.children[0].props.children, "이미지 또는 문서 첨부");
  assert.ok(!rendered.some(node => node.type === "dialog" || node.props?.type === "url"));
  assert.doesNotMatch(source, /URL 첨부|공개 웹 주소|urlDialog|setUrl|showModal/);
  assert.doesNotMatch(read("styles/chat-composer-tools.css"), /chat-url-(dialog|heading|submit)/);
});

test("disabled loading and retry states remain reachable and attachment actions use matching icons", () => {
  const disabled = render({ disabled: true });
  assert.equal(disabled.find(node => node.type === "fieldset").props.disabled, true);
  assert.ok(disabled.filter(node => node.props?.className === "chat-tool-row").every(node => node.props.disabled));
  const menu = disabled.find(node => node.props?.className === "chat-composer-menu");
  const buttons = menu.props.children.filter(node => node.type === "button");
  assert.deepEqual(Array.from(buttons, node => node.props.children[0].props.name), ["paperclip"]);
  assert.ok(buttons.every(node => node.props.disabled));
  menuGroups = [];
  assert.ok(render().some(node => node.props?.role === "status"));
  menuError = "목록 요청 실패";
  const failure = render();
  assert.ok(failure.some(node => node.props?.role === "alert"));
  assert.ok(failure.some(node => node.type === "button" && node.props.children === "목록 다시 불러오기"));
  menuError = "";
  menuGroups = groupIds.map(id => ({ id, label: id }));
  const css = read("styles/chat-composer-tools.css");
  assert.match(css, /width: min\(320px, calc\(100vw - 32px\)\)/);
  assert.match(css, /overflow: auto/);
  assert.match(css, /\.chat-tool-row:focus-visible/);
  assert.match(css, /\.chat-tool-row\[data-selected="true"\]/);
});

test("shared inline input has no inner focus rectangle and retains outer and control keyboard cues", () => {
  const css = read("styles/chat-composer-tools.css");
  const editable = css.match(/\.chat-inline-editable \{([^}]*)\}/)[1];
  assert.match(editable, /border: 0;/);
  assert.match(editable, /background: transparent;/);
  assert.match(editable, /outline: none;/);
  assert.doesNotMatch(css, /(?:^|\n)\.chat-inline-editable:focus(?:-visible)?\s*\{/);
  for (const surface of ["workspace", "chat-popup"]) {
    assert.ok(css.includes(`.${surface}-composer:has(.chat-inline-editable:focus-visible)`));
    const outer = read(`styles/${surface === "workspace" ? "chat-workspace" : surface}.css`);
    assert.match(outer, new RegExp(`\\.${surface}-composer \\{[^}]*border: 1px solid #[a-f0-9]+;[^}]*border-radius: (?:24|18)px;`));
  }
  assert.match(css, /:has\(\.chat-inline-editable:focus-visible\)[^{]*\{ outline: 1px solid #526984; outline-offset: 2px; \}/);
  assert.match(css, /\.chat-inline-chip button:focus-visible[^}]*outline: 2px solid #246bf3;/);
  assert.match(css, /\.chat-composer-add:focus-visible[^}]*outline: 2px solid #246bf3;/);
});
