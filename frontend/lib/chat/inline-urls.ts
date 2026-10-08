import type { ChatAttachmentDraft } from "./types";

export function normalizeChatUrl(value: string): string | null {
  try {
    if (!/^https?:\/\/[^/\s]/i.test(value)) return null;
    const url = new URL(value);
    const host = url.hostname.replace(/\.+$/, "");
    if (value.length > 2048 || /[\s\\]/.test(value) || url.username || url.password || !["", "80", "443"].includes(url.port)
      || !host.includes(".") || /(?:^|\.)(?:localhost|local|internal)$/i.test(host)
      || !host.split(".").every(label => /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/i.test(label))) return null;
    url.hostname = host;
    url.hash = "";
    return url.href;
  } catch { return null; }
}

export function chatUrlTokens(text: string) {
  const tokens: { url: string; literal: string; start: number; end: number }[] = [];
  for (const match of text.matchAll(/(?:^|[\s([<])https?:\/\/[^\s<>"`]+/gi)) {
    const prefix = match[0].search(/https?:\/\//i);
    let literal = match[0].slice(prefix).replace(/[.,!?;:]+$/, "");
    while (literal.endsWith(")") && (literal.match(/\)/g)?.length ?? 0) > (literal.match(/\(/g)?.length ?? 0)) literal = literal.slice(0, -1);
    const url = normalizeChatUrl(literal);
    const start = match.index! + prefix;
    if (url) tokens.push({ url, literal, start, end: start + literal.length });
  }
  return tokens;
}

export function inlineChatUrls(text: string): string[] {
  return [...new Set(chatUrlTokens(text).map(token => token.url))];
}

export function chatComposerContent(text: string, attachments: ChatAttachmentDraft[]): string {
  let content = text;
  for (const item of attachments) if (item.inlineText) content = content.split(item.inlineText).join(`첨부 참고 자료: ${item.name}`);
  return content.trim() || attachments.filter(item => item.kind === "url" && item.state === "ready" && item.attachment)
    .map(item => item.sourceUrl ?? item.attachment!.url ?? "").filter(Boolean).join("\n");
}
