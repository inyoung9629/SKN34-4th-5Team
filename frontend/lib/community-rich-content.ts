export type RichRun = { text: string; font: "sans" | "serif" | "mono"; size: 12 | 14 | 16 | 18 | 20 | 24 | 28 | 32; color: "#26354b" | "#e1131b" | "#246bf3" | "#18825c" | "#7550ae"; bold: boolean; italic: boolean; underline: boolean };
export type RichBlock = { type: "paragraph"; align: "left" | "center" | "right"; runs: RichRun[] } | { type: "image"; id: string };
export type RichContentDoc = { version: 1; blocks: RichBlock[] };

export const richSizes = [12, 14, 16, 18, 20, 24, 28, 32] as const;
export const richColors = ["#26354b", "#e1131b", "#246bf3", "#18825c", "#7550ae"] as const;
export const richFonts = { sans: '"Noto Sans KR", Arial, sans-serif', serif: 'Georgia, "Times New Roman", serif', mono: '"Courier New", monospace' } as const;
export function isRichContentDoc(value: unknown): value is RichContentDoc {
  if (!value || typeof value !== "object") return false;
  const doc = value as Record<string, unknown>;
  if (doc.version !== 1 || !Array.isArray(doc.blocks) || !doc.blocks.length || doc.blocks.length > 500) return false;
  return doc.blocks.every(block => {
    if (!block || typeof block !== "object") return false;
    const item = block as Record<string, unknown>;
    if (item.type === "image") return typeof item.id === "string" && /^[0-9a-f-]{36}$/i.test(item.id);
    if (item.type !== "paragraph" || !["left", "center", "right"].includes(String(item.align)) || !Array.isArray(item.runs) || item.runs.length > 500) return false;
    return item.runs.every(run => {
      if (!run || typeof run !== "object") return false;
      const text = run as Record<string, unknown>;
      return typeof text.text === "string" && !/[\r\n]/.test(text.text) && text.text.length <= 20000
        && ["sans", "serif", "mono"].includes(String(text.font)) && richSizes.includes(text.size as RichRun["size"])
        && richColors.includes(text.color as RichRun["color"]) && ["bold", "italic", "underline"].every(key => typeof text[key] === "boolean");
    });
  });
}
const imagePath = /^\/api\/community\/images\/([0-9a-fA-F-]{36})\/$/;
const emptyRun: RichRun = { text: "", font: "sans", size: 16, color: "#26354b", bold: false, italic: false, underline: false };

export function communityImageUrl(id: string) { return `/api/v1/community/images/${id}/`; }

export function richText(doc: RichContentDoc) {
  return doc.blocks.map(block => block.type === "image" ? "[이미지]" : block.runs.map(run => run.text).join("")).join("\n").trim();
}

export function plainRichDoc(value: string): RichContentDoc {
  return { version: 1, blocks: value.split(/\r?\n/).map(line => ({ type: "paragraph", align: "left", runs: line ? [{ ...emptyRun, text: line }] : [] })) };
}

function fontOf(value: string): RichRun["font"] {
  const lower = value.toLowerCase();
  return /georgia|times|serif/.test(lower) && !lower.includes("sans") ? "serif" : /courier|monospace|mono/.test(lower) ? "mono" : "sans";
}
function sizeOf(value: string, fallback: RichRun["size"]): RichRun["size"] {
  const px = Number.parseInt(value, 10);
  if (richSizes.includes(px as RichRun["size"])) return px as RichRun["size"];
  const htmlSize = Number(value);
  return ([12, 14, 16, 18, 20, 24, 32] as const)[htmlSize - 1] ?? fallback;
}
function colorOf(value: string, fallback: RichRun["color"]): RichRun["color"] {
  const lower = value.toLowerCase().trim();
  if (richColors.includes(lower as RichRun["color"])) return lower as RichRun["color"];
  const rgb = lower.match(/^rgb\((\d+),\s*(\d+),\s*(\d+)\)$/);
  if (rgb) {
    const hex = `#${rgb.slice(1).map(part => Number(part).toString(16).padStart(2, "0")).join("")}`;
    if (richColors.includes(hex as RichRun["color"])) return hex as RichRun["color"];
  }
  return fallback;
}
function alignment(node: HTMLElement, fallback: "left" | "center" | "right") {
  const value = node.style.textAlign || node.getAttribute("align") || "";
  return value === "center" || value === "right" ? value : value === "left" ? "left" : fallback;
}

export function readRichEditor(root: HTMLElement): RichContentDoc {
  const blocks: RichBlock[] = [];
  let paragraph: Extract<RichBlock, { type: "paragraph" }> = { type: "paragraph", align: "left", runs: [] };
  const flush = (force = false) => {
    if (paragraph.runs.length || force) blocks.push(paragraph);
    paragraph = { type: "paragraph", align: "left", runs: [] };
  };
  const walk = (node: Node, style: RichRun, align: "left" | "center" | "right") => {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node.textContent?.replace(/\r?\n/g, "") ?? "";
      if (text) paragraph.runs.push({ ...style, text });
      return;
    }
    if (!(node instanceof HTMLElement)) return;
    const tag = node.tagName.toLowerCase();
    if (tag === "img") {
      const imageId = node.getAttribute("data-image-id");
      if (imageId && /^[0-9a-f-]{36}$/i.test(imageId)) {
        flush(); blocks.push({ type: "image", id: imageId.toLowerCase() });
        return;
      }
      const path = new URL(node.getAttribute("src") || "", location.origin);
      const match = path.origin === location.origin && path.search === "" ? path.pathname.match(imagePath) : null;
      if (match) { flush(); blocks.push({ type: "image", id: match[1].toLowerCase() }); }
      return;
    }
    if (tag === "br") { flush(true); return; }
    const block = ["div", "p", "li", "h1", "h2", "h3", "blockquote"].includes(tag);
    const nextAlign = alignment(node, align);
    const next: RichRun = {
      ...style,
      font: fontOf(node.style.fontFamily || (tag === "font" ? node.getAttribute("face") || "" : richFonts[style.font])),
      size: sizeOf(node.style.fontSize || (tag === "font" ? node.getAttribute("size") || "" : ""), style.size),
      color: colorOf(node.style.color || (tag === "font" ? node.getAttribute("color") || "" : ""), style.color),
      bold: style.bold || ["b", "strong"].includes(tag) || node.style.fontWeight === "bold" || Number(node.style.fontWeight) >= 600,
      italic: style.italic || ["i", "em"].includes(tag) || node.style.fontStyle === "italic",
      underline: style.underline || tag === "u" || node.style.textDecoration.includes("underline"),
    };
    if (block && paragraph.runs.length) flush();
    paragraph.align = nextAlign;
    const before = blocks.length;
    node.childNodes.forEach(child => walk(child, next, nextAlign));
    if (block && (paragraph.runs.length || blocks.length === before)) flush(true);
  };
  root.childNodes.forEach(child => walk(child, emptyRun, "left"));
  if (paragraph.runs.length) flush();
  return { version: 1, blocks: blocks.length ? blocks : [{ type: "paragraph", align: "left", runs: [] }] };
}

export function writeRichEditor(root: HTMLElement, doc: RichContentDoc) {
  root.replaceChildren();
  doc.blocks.forEach(block => {
    if (block.type === "image") {
      const img = document.createElement("img");
      img.src = communityImageUrl(block.id);
      img.dataset.imageId = block.id;
      img.alt = "첨부 이미지";
      root.append(img);
      return;
    }
    const line = document.createElement("div");
    line.style.textAlign = block.align;
    if (!block.runs.length) line.append(document.createElement("br"));
    block.runs.forEach(run => {
      const span = document.createElement("span");
      span.textContent = run.text;
      span.style.fontFamily = richFonts[run.font];
      span.style.fontSize = `${run.size}px`;
      span.style.color = run.color;
      span.style.fontWeight = run.bold ? "bold" : "normal";
      span.style.fontStyle = run.italic ? "italic" : "normal";
      span.style.textDecoration = run.underline ? "underline" : "none";
      line.append(span);
    });
    root.append(line);
  });
}
