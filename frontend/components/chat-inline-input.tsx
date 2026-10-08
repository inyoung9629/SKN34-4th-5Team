"use client";

import { useContext, useLayoutEffect, useId, useRef, useState, type RefObject } from "react";
import { ChatToolGroupsContext, ChatToolSelectionContext, presentationFor } from "./chat-composer-tools";
import { chatUrlTokens, normalizeChatUrl } from "@/lib/chat/inline-urls";
import { type ChatAttachment, type ChatAttachmentDraft, type ChatToolGroup } from "@/lib/chat/types";
import { useChat } from "./chat-provider";
import { Icon } from "./icons";
import { useMemberAuth } from "@/lib/member-auth";

export function ChatInlineContent({ text, attachments = [], toolTags = [] }: { text: string; attachments?: (ChatAttachment | ChatAttachmentDraft)[]; toolTags?: string[] }) {
  const tokens = chatUrlTokens(text).filter(token => attachments.some(item => item.kind === "url" && normalizeChatUrl("key" in item ? item.sourceUrl ?? item.attachment?.url ?? item.name : item.url ?? "") === token.url));
  const references = [...attachments.flatMap(item => "key" in item && item.inlineText ? [item.inlineText] : []), ...toolTags];
  for (const marker of references) for (const match of text.matchAll(new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g"))) tokens.push({ start: match.index!, end: match.index! + marker.length, literal: marker, url: "" });
  tokens.sort((left, right) => left.start - right.start);
  const parts = []; let cursor = 0;
  for (const token of tokens) {
    if (token.start < cursor) continue;
    parts.push(text.slice(cursor, token.start), <span className="chat-inline-url" key={token.start}>{token.literal}</span>);
    cursor = token.end;
  }
  parts.push(text.slice(cursor));
  return <>{parts.map((part, index) => typeof part !== "string" ? part : part.split(/(\[\[ Text \d+ \]\]|첨부 참고 자료: Pasted Text \d+\.txt)/g).map((value, offset) => {
    const item = attachments.find(item => "key" in item && item.inlineText === value || value === `첨부 참고 자료: ${item.name}` && /^Pasted Text \d+\.txt$/.test(item.name));
    return item ? <span key={`${index}-${offset}`} className="chat-inline-url">{`[[ Text ${item.name.match(/\d+/)?.[0]} ]]`}</span> : value;
  }))}</>;
}

// The DOM contains real non-editable atoms; the provider keeps the plain-text wire format.
export function inlineEditorText(node: Node): string {
  if (node instanceof HTMLElement && node.dataset.marker !== undefined) return node.dataset.marker;
  if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? "";
  if (node.nodeName === "BR") return "\n";
  return Array.from(node.childNodes).map(child => inlineEditorText(child) + (child.nodeName === "DIV" || child.nodeName === "P" ? "\n" : "")).join("");
}

export function inlineEditorMention(root: Node, text: string, caret: number) {
  const match = text.slice(0, caret).match(/(?:^|\s)@([^\s@]*)$/);
  if (!match) return null;
  const start = caret - match[1].length - 1;
  let offset = 0, owned = false;
  function visit(node: Node) {
    const length = inlineEditorText(node).length;
    if (node instanceof HTMLElement && node.dataset.marker !== undefined) {
      if (start < offset + length && caret > offset) owned = true;
      offset += length;
    } else if (node.nodeType === Node.TEXT_NODE || node.nodeName === "BR") offset += length;
    else for (const child of Array.from(node.childNodes)) {
      visit(child);
      if (child.nodeName === "DIV" || child.nodeName === "P") offset++;
    }
  }
  visit(root);
  return owned ? null : { start, end: caret, query: match[1] };
}

type Position = { start: number; end: number };
export function ChatInlineInput({ id, inputRef, disabled, available, onSend, onCompositionChange }: {
  id: string; inputRef: RefObject<HTMLDivElement | null>; disabled: boolean; available: boolean;
  onSend: () => void; onCompositionChange: (value: boolean) => void;
}) {
  const chat = useChat();
  const groups = useContext(ChatToolGroupsContext);
  const toolSelectionRef = useContext(ChatToolSelectionContext);
  const savedPosition = useRef<Position | null>(null);
  const composing = useRef(false);
  const history = useRef<{ text: string; position: Position }[]>([]);
  const future = useRef<{ text: string; position: Position }[]>([]);
  const previous = useRef(chat.draft);
  const auth = useMemberAuth();
  const scope = `${auth.status}:${auth.user?.id ?? "guest"}:${chat.activeConversationId}:${chat.editingMessageId ?? "draft"}`;
  const historyScope = useRef(scope);
  const pendingPosition = useRef<Position | null>(null);
  const [mention, setMention] = useState<{ start: number; end: number; query: string } | null>(null);
  const [active, setActive] = useState(0);
  const listId = useId();
  const options = mention ? groups.filter(group => group.id !== "weather" && (group.label.includes(mention.query) || group.id.includes(mention.query))) : [];

  function position(): Position {
    const root = inputRef.current, selection = window.getSelection();
    if (!root || !selection?.rangeCount || !root.contains(selection.anchorNode)) return { start: chat.draft.length, end: chat.draft.length };
    const range = selection.getRangeAt(0), prefix = range.cloneRange();
    prefix.selectNodeContents(root); prefix.setEnd(range.startContainer, range.startOffset);
    const start = inlineEditorText(prefix.cloneContents());
    prefix.setEnd(range.endContainer, range.endOffset);
    return { start: start.length, end: inlineEditorText(prefix.cloneContents()).length };
  }

  function select(position: Position) {
    const root = inputRef.current;
    if (!root) return;
    function point(offset: number): [Node, number] {
      let remaining = offset;
      for (const child of Array.from(root!.childNodes)) {
        const length = inlineEditorText(child).length;
        if (child.nodeType === Node.TEXT_NODE && remaining <= length) return [child, remaining];
        if (child instanceof HTMLElement && remaining < length) return [root!, Array.from(root!.childNodes).indexOf(child)];
        remaining -= length;
      }
      return [root!, root!.childNodes.length];
    }
    const range = document.createRange();
    range.setStart(...point(position.start)); range.setEnd(...point(position.end));
    const selection = window.getSelection(); selection?.removeAllRanges(); selection?.addRange(range);
  }

  function updateMention(text: string, caret = position().end) {
    if (composing.current) return;
    setMention(inputRef.current ? inlineEditorMention(inputRef.current, text, caret) : null); setActive(0);
  }

  function change(text: string, next: Position, record = true) {
    if (record && text !== previous.current) { history.current.push({ text: previous.current, position: position() }); future.current = []; }
    previous.current = text; pendingPosition.current = next;
    chat.onDraftChange(text); updateMention(text, next.end);
  }

  function replace(value: string, at = position()) {
    const text = inlineEditorText(inputRef.current!);
    const caret = at.start + value.length;
    change(text.slice(0, at.start) + value + text.slice(at.end), { start: caret, end: caret });
    inputRef.current?.focus();
  }

  function undo(redo = false) {
    const source = redo ? future.current : history.current, destination = redo ? history.current : future.current;
    const entry = source.pop(); if (!entry) return;
    destination.push({ text: previous.current, position: position() });
    change(entry.text, entry.position, false);
  }

  function selectTool(group: ChatToolGroup) {
    if (!mention) return;
    chat.onInlineToolSelect(group.id, group.label);
    replace(`@${group.label} `, mention); setMention(null);
  }

  useLayoutEffect(() => {
    if (!toolSelectionRef) return;
    toolSelectionRef.current = group => {
      const root = inputRef.current;
      if (!root || disabled || !available || composing.current) return;
      root.focus();
      const at = savedPosition.current ?? { start: chat.draft.length, end: chat.draft.length };
      select({ start: Math.min(at.start, chat.draft.length), end: Math.min(at.end, chat.draft.length) });
      const marker = `@${group.label}`;
      chat.onInlineToolSelect(group.id, group.label);
      if (!chat.draft.includes(marker)) replace(`${at.start && !/\s/.test(chat.draft[at.start - 1]) ? " " : ""}${marker} `, at);
      setMention(null);
    };
    return () => { toolSelectionRef.current = null; };
  });

  useLayoutEffect(() => {
    const root = inputRef.current;
    if (!root || composing.current) return;
    if (historyScope.current !== scope || chat.pending) { history.current = []; future.current = []; historyScope.current = scope; }
    const changed = pendingPosition.current !== null;
    const caret = pendingPosition.current ?? position(); pendingPosition.current = null;
    const focused = document.activeElement === root;
    // External restoration/identity changes are not additional undo entries.
    previous.current = chat.draft;
    const atoms: { start: number; end: number; marker: string; label: string; title: string; key?: string; group?: ChatToolGroup; state?: string }[] = [];
    for (const item of chat.attachments) {
      if (item.inlineText) {
        const start = chat.draft.indexOf(item.inlineText);
        if (start >= 0) atoms.push({ start, end: start + item.inlineText.length, marker: item.inlineText, label: item.inlineText, title: item.error ?? item.name, key: item.key, state: item.state });
      } else if (item.kind === "url") for (const token of chatUrlTokens(chat.draft).filter(token => token.url === normalizeChatUrl(item.sourceUrl ?? item.attachment?.url ?? item.name))) {
        const url = new URL(token.url);
        atoms.push({ start: token.start, end: token.end, marker: token.literal, label: url.hostname, title: token.url, key: item.key, state: item.state });
      }
    }
    for (const group of groups.filter(group => chat.toolGroupIds.includes(group.id))) {
      const marker = `@${group.label}`, start = chat.draft.indexOf(marker);
      if (start >= 0) {
        if (chat.editingMessageId !== null) chat.onInlineToolSelect(group.id, group.label, true);
        atoms.push({ start, end: start + marker.length, marker, label: group.label, title: group.label, group });
      }
    }
    atoms.sort((a, b) => a.start - b.start);
    const fragment = document.createDocumentFragment(); let cursor = 0;
    for (const atom of atoms) {
      if (atom.start < cursor) continue;
      fragment.append(document.createTextNode(chat.draft.slice(cursor, atom.start)));
      const chip = document.createElement("span"); chip.contentEditable = "false"; chip.dataset.marker = atom.marker; chip.className = "chat-inline-chip"; chip.title = atom.title;
      const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg"); icon.setAttribute("viewBox", "0 0 24 24"); icon.setAttribute("aria-hidden", "true");
      // Copy the incumbent icon path rendered below, rather than maintain a second icon library.
      const template = root.parentElement?.querySelector(`[data-chip-icon="${atom.group?.id ?? (atom.key && atom.marker.startsWith("http") ? "url" : "text")}"] svg`);
      if (template) icon.innerHTML = template.innerHTML;
      const label = document.createElement("span"); label.className = "chat-inline-chip-label"; label.textContent = atom.label;
      chip.append(icon, label);
      if (atom.state && atom.state !== "ready") { const status = document.createElement("span"); status.textContent = atom.state === "uploading" ? "등록 중" : "등록 실패"; status.setAttribute("role", "status"); chip.append(status); }
      if (atom.key && atom.marker.startsWith("http")) {
        const disclosure = document.createElement("button"); disclosure.type = "button"; disclosure.className = "chat-inline-disclosure"; disclosure.textContent = "주소"; disclosure.setAttribute("aria-label", `전체 주소: ${atom.title}`); disclosure.onclick = () => { disclosure.textContent = disclosure.textContent === "주소" ? atom.title : "주소"; }; chip.append(disclosure);
      }
      if (atom.key && ["failed", "cancelled"].includes(atom.state ?? "")) { const retry = document.createElement("button"); retry.type = "button"; retry.textContent = "재시도"; retry.disabled = disabled; retry.onmousedown = event => event.preventDefault(); retry.onclick = () => chat.onRetryAttachment(atom.key!); chip.append(retry); }
      const remove = document.createElement("button"); remove.type = "button"; remove.disabled = disabled; remove.textContent = "×"; remove.setAttribute("aria-label", `${atom.title} 삭제`);
      remove.onmousedown = event => event.preventDefault(); remove.onclick = () => { change(chat.draft.slice(0, atom.start) + chat.draft.slice(atom.end), { start: atom.start, end: atom.start }); inputRef.current?.focus(); };
      chip.append(remove); fragment.append(chip); cursor = atom.end;
    }
    fragment.append(document.createTextNode(chat.draft.slice(cursor)));
    root.replaceChildren(fragment);
    if (focused) select({ start: Math.min(caret.start, chat.draft.length), end: Math.min(caret.end, chat.draft.length) });
    if (changed) updateMention(chat.draft, Math.min(caret.end, chat.draft.length));
  });

  return <>
    <div className="chat-inline-editor">
      <div className="chat-inline-icon-templates" hidden aria-hidden="true">{groups.map(group => <span data-chip-icon={group.id} key={group.id}><Icon name={presentationFor(group.id).icon} size={13} /></span>)}<span data-chip-icon="url"><Icon name="link" size={13} /></span><span data-chip-icon="text"><Icon name="book" size={13} /></span></div>
      <div ref={inputRef} id={id} role="textbox" aria-multiline="true" contentEditable={!disabled} suppressContentEditableWarning tabIndex={0} aria-disabled={disabled} data-placeholder="직관 도우미에게 물어보세요"
        aria-label="직관 도우미에게 질문" aria-autocomplete="list" aria-controls={mention ? listId : undefined} aria-activedescendant={mention && options.length ? `${listId}-${Math.min(active, options.length - 1)}` : undefined}
        className="chat-inline-editable"
        onInput={() => { if (composing.current) return; const text = inlineEditorText(inputRef.current!); change(text, position()); if (/\s$/.test(text)) chat.onCommitUrls(text); }}
        onClick={() => updateMention(chat.draft)} onKeyUp={event => { if (!["ArrowDown", "ArrowUp", "Enter", "Tab", "Escape"].includes(event.key)) updateMention(chat.draft); }}
        onBeforeInput={event => { const type = (event.nativeEvent as InputEvent).inputType; if (type === "historyUndo" || type === "historyRedo") { event.preventDefault(); undo(type === "historyRedo"); } }}
        onPaste={event => {
          if (composing.current || disabled || !available || event.clipboardData.files.length) return;
          event.preventDefault(); const text = event.clipboardData.getData("text/plain");
          const marker = chat.onPasteText(text); replace(marker ?? text);
          if (Array.from(text).length < 2000) chat.onCommitUrls(text, true);
        }}
        onCopy={event => { const at = position(); if (at.start !== at.end) { event.preventDefault(); event.clipboardData.setData("text/plain", chat.draft.slice(at.start, at.end)); } }}
        onCut={event => { const at = position(); if (!disabled && at.start !== at.end) { event.preventDefault(); event.clipboardData.setData("text/plain", chat.draft.slice(at.start, at.end)); replace("", at); } }}
        onBlur={() => { savedPosition.current = position(); if (!composing.current) chat.onCommitUrls(chat.draft); }}
        onCompositionStart={() => { composing.current = true; setMention(null); chat.onCompositionChange(true); onCompositionChange(true); }}
        onCompositionEnd={() => { composing.current = false; const text = inlineEditorText(inputRef.current!); change(text, position()); onCompositionChange(false); window.setTimeout(() => chat.onCompositionChange(false), 0); }}
        onKeyDown={event => {
          if (disabled || event.target !== event.currentTarget || event.nativeEvent.isComposing || composing.current || event.keyCode === 229) return;
          if ((event.metaKey || event.ctrlKey) && (event.key.toLowerCase() === "z" || event.key.toLowerCase() === "y")) { event.preventDefault(); undo(event.shiftKey || event.key.toLowerCase() === "y"); return; }
          if (mention && options.length) {
            if (["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); setActive(value => (value + (event.key === "ArrowDown" ? 1 : options.length - 1)) % options.length); return; }
            if (event.key === "Enter" || event.key === "Tab") { event.preventDefault(); selectTool(options[Math.min(active, options.length - 1)]); return; }
            if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setMention(null); return; }
          }
          if (["Backspace", "Delete", "ArrowLeft", "ArrowRight"].includes(event.key)) {
            const at = position();
            let offset = 0;
            for (const child of Array.from(inputRef.current?.childNodes ?? [])) {
              const length = inlineEditorText(child).length;
              if (child instanceof HTMLElement && child.dataset.marker !== undefined) {
                const backward = event.key === "Backspace" || event.key === "ArrowLeft";
                if (event.key === "Backspace" && at.start === at.end && at.start === offset + length + 1 && chat.draft[at.start - 1] === " ") {
                  event.preventDefault(); replace("", { start: at.start - 1, end: at.start }); return;
                }
                if (at.start === at.end && (backward ? at.start === offset + length : at.start === offset)) {
                  event.preventDefault();
                  if (event.key === "Backspace" || event.key === "Delete") replace("", { start: offset, end: offset + length });
                  else select({ start: backward ? offset : offset + length, end: backward ? offset : offset + length });
                  return;
                }
              }
              offset += length;
            }
          }
          if (event.key === "Enter") { event.preventDefault(); if (event.shiftKey) replace("\n"); else onSend(); }
        }} />
    </div>
    {mention && options.length > 0 && <div id={listId} className="chat-tool-autocomplete" role="listbox" aria-label="도구 선택">{options.map((group, index) => <button type="button" role="option" aria-selected={index === active} id={`${listId}-${index}`} key={group.id} onMouseDown={event => event.preventDefault()} onClick={() => selectTool(group)}>@{group.label}</button>)}</div>}
  </>;
}
