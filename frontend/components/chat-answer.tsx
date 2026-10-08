"use client";

import { Children, cloneElement, isValidElement, useState, type ComponentProps } from "react";
import Link from "next/link";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import "@/styles/chat-answer.css";

import { safeChatUrl } from "@/lib/media-url";
export { safeChatUrl } from "@/lib/media-url";

import type { ChatCoursePlace } from "@/lib/chat/types";
import type { Element, Root } from "hast";

export function ChatPlaceSource({ place }: { place: ChatCoursePlace }) {
  if (!place.placeUrl) return null;
  const label = /^https:\/\/myseatcheck\.com\//.test(place.placeUrl) ? "매장 상세 위치"
    : /^https:\/\/nol\.yanolja\.com\/stay\/domestic\/\d+$/.test(place.placeUrl) ? "야놀자" : "출처";
  return <a className="chat-place-source" href={place.placeUrl} target="_blank" rel="noopener noreferrer"
    aria-label={`${place.name} ${label} (새 창)`} title={place.address ?? `${place.name} 장소 정보 확인`}>{label} <span aria-hidden="true">↗</span></a>;
}

function ChatImage({ src, alt, title }: ComponentProps<"img">) {
  const [failed, setFailed] = useState(false);
  const label = alt?.trim() || "참고 이미지";
  if (!src || failed) return <span className="chat-image-fallback" role="img" aria-label={label}>{label} · 이미지를 표시할 수 없어요.</span>;
  // Standard image keeps approved sources direct; no optimizer/proxy expands the trust boundary.
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt={label} title={title} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailed(true)} />;
}

export function ChatAnswer({ text, places = [] }: { text: string; places?: ChatCoursePlace[] }) {
  const placeSources = () => (tree: Root) => {
    const cited = new Set<number>();
    const addSources = (block: Element) => {
      let line = "";
      let links = new Set<string>();
      const sources = (): Element[] => {
        const result = places.flatMap((place, i): Element[] => {
          const href = place.placeUrl && safeChatUrl(place.placeUrl);
          if (cited.has(i) || !href || !line.includes(place.name) || (place.time && !line.includes(place.time))) return [];
          cited.add(i);
          if (links.has(href)) return [];
          const label = /^https:\/\/myseatcheck\.com\//.test(href) ? "매장 상세 위치"
            : /^https:\/\/nol\.yanolja\.com\/stay\/domestic\/\d+$/.test(href) ? "야놀자" : "출처";
          return [{ type: "element", tagName: "a", properties: { href, className: ["chat-place-source"],
            ariaLabel: `${place.name} 장소 정보 출처 (새 창)`, title: `${place.name} 장소 정보 확인` },
            children: [{ type: "text", value: `${label} ` }, { type: "element", tagName: "span",
              properties: { ariaHidden: "true" }, children: [{ type: "text", value: "↗" }] }] }];
        });
        line = "";
        links = new Set();
        return result;
      };
      const inline = (node: Element, inLink = false) => {
        node.children = node.children.flatMap(child => {
          if (child.type === "text") line += child.value;
          if (child.type === "element") {
            if (["ul", "ol"].includes(child.tagName)) return [...sources(), child];
            if (child.tagName === "br" && !inLink) return [...sources(), child];
            if (child.tagName === "a" && typeof child.properties.href === "string") links.add(child.properties.href);
            inline(child, inLink || child.tagName === "a");
            if (["td", "th"].includes(child.tagName)) line += " ";
          }
          return [child];
        });
      };
      inline(block);
      const target = block.tagName === "tr" ? block.children.findLast(child => child.type === "element" && child.tagName === "td") : block;
      if (target?.type === "element") target.children.push(...sources());
    };
    const visit = (node: Root | Element) => {
      for (const child of node.children) {
        if (child.type !== "element") continue;
        if (["p", "tr"].includes(child.tagName) || (child.tagName === "li" && !child.children.some(item =>
          item.type === "element" && item.tagName === "p"))) {
          addSources(child);
          if (child.tagName === "li") visit(child);
        } else visit(child);
      }
    };
    visit(tree);
  };
  return <div className="chat-answer"><Markdown
    skipHtml
    remarkPlugins={[remarkGfm, remarkBreaks]}
    rehypePlugins={[placeSources]}
    urlTransform={(url, key) => safeChatUrl(url, key === "src")}
    components={{
      h1: ({ children }) => <h3>{children}</h3>,
      h2: ({ children }) => <h3>{children}</h3>,
      h4: ({ children }) => <h3>{children}</h3>,
      h5: ({ children }) => <h3>{children}</h3>,
      h6: ({ children }) => <h3>{children}</h3>,
      p: ({ node, children }) => {
        const content = node?.children.filter(child => child.type !== "text" || child.value.trim());
        const link = content?.length === 1 && content[0].type === "element" && content[0].tagName === "a" ? content[0] : undefined;
        const href = typeof link?.properties.href === "string" ? safeChatUrl(link.properties.href) : undefined;
        const url = href ? new URL(href, "https://chat.invalid") : undefined;
        const detail = url && (href?.startsWith("/")
          ? (/^\/(?:standings\/(?:players\/[0-9]+|teams\/[A-Z0-9]+)|stadiums\/[A-Z]+|routes\/[A-Za-z0-9-]+)$/.test(url.pathname)
            || (["/community", "/community/teams"].includes(url.pathname) && url.searchParams.has("post")))
          : url.origin === "https://www.tving.com" && /^\/sports\/kbo\/(?:athlete\/[0-9]+|team\/[A-Z]+)$/.test(url.pathname));
        return <p>{detail ? Children.map(children, child => isValidElement<{ className?: string }>(child)
          ? cloneElement(child, { className: "chat-detail-link" }) : child) : children}</p>;
      },
      a: ({ node, href, children, title, className }) => {
        if (href && className === "chat-place-source") return <a href={href} className={className} title={title}
          target="_blank" rel="noopener noreferrer" aria-label={String(node?.properties.ariaLabel)}>{children}</a>;
        const label = Children.toArray(children).join("");
        if (["야놀자", "메뉴 근거", "후기 근거"].includes(label)) {
          if (!href || !href.startsWith("https://") || /[<>"\\]|%22|%3c|%3e|%5c/i.test(href) || (label === "야놀자" && !/^https:\/\/nol\.yanolja\.com\/stay\/domestic\/\d+$/.test(href))) return <span>{children}</span>;
          return <a href={href} className="chat-place-source" target="_blank" rel="noopener noreferrer" aria-label={`${label} 확인 (새 창)`}>{label} <span aria-hidden="true">↗</span></a>;
        }
        return !href ? <span>{children}</span> : href.startsWith("/")
        ? <Link href={href} title={title} className={className} prefetch={false}>{children}{className === "chat-detail-link" && <span aria-hidden="true"> →</span>}</Link>
        : <a href={href} title={title} className={className} target="_blank" rel="noopener noreferrer">{children}{className === "chat-detail-link" && <span aria-hidden="true"> →</span>}</a>;
      },
      img: ({ src, alt, title }) => <ChatImage key={typeof src === "string" ? src : "blocked"} src={src} alt={alt} title={title} />,
      table: ({ children }) => <div className="chat-table-scroll" role="region" aria-label="답변 표" tabIndex={0}><table>{children}</table></div>,
    }}
  >{text}</Markdown></div>;
}
