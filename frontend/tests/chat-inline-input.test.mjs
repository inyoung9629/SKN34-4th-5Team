import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import ts from "typescript";

const require = createRequire(import.meta.url);
const code = readFileSync(new URL("../components/chat-inline-input.tsx", import.meta.url), "utf8");
const ast = ts.createSourceFile("input.tsx", code, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const body = ast.statements.filter(node => !ts.isImportDeclaration(node)).map(node => node.getText(ast)).join("\n");
const groups = [{ id: "rules", label: "야구 규칙" }, { id: "schedule", label: "경기 일정" }];
const values = [], refs = [];
let stateIndex = 0, refIndex = 0, sends = 0;
const chat = { draft: "@", attachments: [], toolGroupIds: [], onDraftChange(value) { this.draft = value; input.childNodes = [textNode(value)]; }, onInlineToolSelect(id) { this.toolGroupIds = [...new Set([...this.toolGroupIds, id])]; }, onCommitUrls() {}, onCompositionChange() {}, onPasteText() { return null; } };
const textNode = value => ({ nodeType: 3, nodeName: "#text", textContent: value, childNodes: [] });
const input = { childNodes: [textNode("@")], focus() {} };
const exports = {};
const selectionContext = {}, selectionRef = { current: null };
class Element { constructor(marker) { this.dataset = { marker }; } }
runInNewContext(ts.transpileModule(body, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText, {
  exports, require, HTMLElement: Element, Node: { TEXT_NODE: 3 }, ChatToolGroupsContext: {}, ChatToolSelectionContext: selectionContext, useContext: context => context === selectionContext ? selectionRef : groups, useChat: () => chat, useMemberAuth: () => ({ status: "anonymous" }),
  useLayoutEffect(effect) { if (effect.toString().includes("toolSelectionRef.current")) effect(); }, document: { createRange: () => ({ setStart() {}, setEnd() {} }) }, useRef: value => refs[refIndex++] ??= { current: value }, useId: () => "tools", Icon: () => null, presentationFor: () => ({ icon: "book" }),
  useState(initial) { const index = stateIndex++; return [index in values ? values[index] : initial, value => { values[index] = typeof value === "function" ? value(values[index] ?? initial) : value; }]; },
  chatUrlTokens: () => [], normalizeChatUrl: value => value, window: { getSelection: () => null, setTimeout: fn => fn() },
});
const flatten = tree => !tree || typeof tree !== "object" ? [] : [tree, ...[tree.props?.children].flat(Infinity).flatMap(flatten)];
const render = () => { stateIndex = refIndex = 0; return flatten(exports.ChatInlineInput({ id: "question", inputRef: { current: input }, disabled: false, available: true, onSend: () => sends++, onCompositionChange() {} })); };
const editor = () => render().find(node => node.props?.role === "textbox").props;
const keyboard = key => ({ key, keyCode: 0, nativeEvent: {}, preventDefault() {}, stopPropagation() {} });

test("plus tool selection inserts once, preserves draft and focuses without sending", () => {
  let focused = 0;
  input.focus = () => focused++;
  chat.onDraftChange("prior suffix"); editor().onBlur();
  refs[0].current = { start: 6, end: 6 };
  selectionRef.current(groups[0]);
  assert.equal(chat.draft, "prior @야구 규칙 suffix");
  assert.deepEqual([...chat.toolGroupIds], ["rules"]);
  render(); selectionRef.current(groups[0]);
  assert.equal(chat.draft, "prior @야구 규칙 suffix");
  assert.ok(focused >= 2); assert.equal(sends, 0);
  chat.toolGroupIds = [];
});

test("real atomic DOM serializer emits marker only, not chip labels or controls", () => {
  assert.equal(exports.inlineEditorText(new Element("https://example.com/full/path")), "https://example.com/full/path");
  assert.equal(exports.inlineEditorText({ childNodes: [textNode("prior "), new Element("[[ Text 1 ]]"), textNode(" suffix")] }), "prior [[ Text 1 ]] suffix");
  assert.match(code, /chip.contentEditable = "false"/);
  assert.doesNotMatch(code, /<textarea|chat-inline-mirror|참고 해제|chat-inline-status/);
});

test("bare @ registry arrows select inline tool without sending; Escape stays closed on keyup", () => {
  chat.onDraftChange("@"); editor().onInput();
  assert.equal(render().filter(node => node.props?.role === "option").length, 2);
  editor().onKeyDown(keyboard("ArrowDown")); editor().onKeyUp(keyboard("ArrowDown"));
  editor().onKeyDown(keyboard("Enter"));
  assert.equal(chat.draft, "@경기 일정 "); assert.deepEqual([...chat.toolGroupIds], ["schedule"]); assert.equal(sends, 0);
  chat.onDraftChange("@"); editor().onInput(); editor().onKeyDown(keyboard("Escape")); editor().onKeyUp(keyboard("Escape"));
  assert.equal(render().filter(node => node.props?.role === "option").length, 0);
});

test("mention ownership uses actual atom ranges, including nested references and repeated raw labels", () => {
  const atom = new Element("@reference");
  const root = { childNodes: [textNode("prefix "), { nodeName: "DIV", childNodes: [atom, textNode(" @reference")] }] };
  const text = exports.inlineEditorText(root);
  assert.equal(exports.inlineEditorMention(root, text, 10), null);
  assert.equal(exports.inlineEditorMention(root, text, 17), null);
  assert.deepEqual({ ...exports.inlineEditorMention(root, text, 28) }, { start: 18, end: 28, query: "reference" });
  assert.equal(exports.inlineEditorMention(root, text, 29), null);
  assert.deepEqual({ ...exports.inlineEditorMention({ childNodes: [textNode("@")] }, "@", 1) }, { start: 0, end: 1, query: "" });
});

test("confirmed tool and reference atoms never reopen mentions at their caret boundaries", () => {
  const original = groups[0].label;
  groups[0].label = "규정";
  chat.draft = "@규정"; chat.toolGroupIds = ["rules"];
  input.childNodes = [new Element("@규정"), textNode(" ")]; chat.draft += " ";
  let prevented = false;
  editor().onKeyDown({ ...keyboard("Backspace"), preventDefault() { prevented = true; } });
  assert.equal(prevented, true); assert.equal(chat.draft, "@규정");
  input.childNodes = [new Element("@규정"), textNode("")];
  editor().onKeyUp(keyboard("Backspace"));
  assert.equal(render().filter(node => node.props?.role === "option").length, 0);
  editor().onClick();
  assert.equal(render().filter(node => node.props?.role === "option").length, 0);
  chat.draft = "@규정 @";
  input.childNodes = [new Element("@규정"), textNode(" @")];
  editor().onKeyUp(keyboard("@"));
  assert.equal(render().filter(node => node.props?.role === "option").length, 2);
  chat.draft = "@규"; input.childNodes = [textNode("@규")];
  editor().onKeyUp(keyboard("규"));
  assert.equal(render().filter(node => node.props?.role === "option").length, 1);
  groups[0].label = original; chat.toolGroupIds = [];
});

test("plain paste undo redo retain exact original text and composition never sends", () => {
  chat.onDraftChange("prior "); editor().onInput(); let requested;
  editor().onPaste({ preventDefault() {}, clipboardData: { files: [], getData(type) { requested = type; return "<b>plain</b>"; } } });
  assert.equal(requested, "text/plain"); assert.equal(chat.draft, "prior <b>plain</b>");
  editor().onKeyDown({ ...keyboard("z"), metaKey: true }); assert.equal(chat.draft, "prior ");
  editor().onKeyDown({ ...keyboard("z"), metaKey: true, shiftKey: true }); assert.equal(chat.draft, "prior <b>plain</b>");
  editor().onCompositionStart(); editor().onKeyDown(keyboard("Enter")); assert.equal(sends, 0); editor().onCompositionEnd();
});
