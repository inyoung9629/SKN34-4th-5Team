import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createRequire } from "node:module";
import { existsSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-chat-answer-test-"));
after(() => rmSync(scratch, { recursive: true, force: true }));
symlinkSync(join(frontend, "node_modules"), join(scratch, "node_modules"), "dir");
const media = readFileSync(join(frontend, "lib/media-url.ts"), "utf8");
writeFileSync(join(scratch, "media.cjs"), ts.transpileModule(media, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText);
const source = readFileSync(join(frontend, "components/chat-answer.tsx"), "utf8").replace(/^import "@\/styles\/chat-answer\.css";$/m, "").replaceAll("@/lib/media-url", "./media.cjs");
writeFileSync(join(scratch, "answer.cjs"), ts.transpileModule(source, {
  fileName: "chat-answer.tsx", compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText);
const { ChatAnswer, safeChatUrl } = createRequire(import.meta.url)(join(scratch, "answer.cjs"));
const render = text => renderToStaticMarkup(React.createElement(ChatAnswer, { text }));
const sourcePlaces = [
  { name: "첫 카페", time: "12:00", placeUrl: "https://example.com/first" },
  { name: "둘째 식당", time: "13:00", placeUrl: "https://example.com/second" },
];
const renderPlaces = text => renderToStaticMarkup(React.createElement(ChatAnswer, { text, places: sourcePlaces }));

for (const [name, markdown] of [
  ["multiline strong", "**12:00 첫 카페\n13:00 둘째 식당**"],
  ["GFM table rows", "| 시간 | 장소 |\n| --- | --- |\n| 12:00 | 첫 카페 |\n| 13:00 | 둘째 식당 |"],
  ["tight nested lists", "- 12:00 첫 카페\n  - 13:00 둘째 식당"],
  ["loose nested lists", "- 12:00 첫 카페\n\n  설명\n\n  - 13:00 둘째 식당"],
]) {
  test(`place sources associate once at each boundary: ${name}`, () => {
    const html = renderPlaces(markdown);
    assert.equal((html.match(/class="chat-place-source"/g) ?? []).length, 2, html);
    assert.match(html, /첫 카페[\s\S]*href="https:\/\/example.com\/first"[\s\S]*둘째 식당[\s\S]*href="https:\/\/example.com\/second"/);
    if (name === "multiline strong") assert.match(html, /example.com\/first[\s\S]*<br\/>[\s\S]*둘째 식당/);
    if (name === "GFM table rows") {
      const rows = html.match(/<tr>[\s\S]*?<\/tr>/g);
      assert.equal(rows.filter(row => row.includes("chat-place-source")).length, 2);
      assert.match(rows[1], /<td>첫 카페[\s\S]*chat-place-source[\s\S]*<\/td>/);
    }
    assert.doesNotMatch(html, /<a[^>]*>[^<]*<a/);
  });
}

test("explicit place source and Yanolja links are not duplicated", () => {
  const html = renderPlaces("12:00 첫 카페 [출처](https://example.com/first)\n13:00 둘째 식당");
  assert.equal((html.match(/href="https:\/\/example.com\/first"/g) ?? []).length, 1);
  const stay = { name: "호텔", time: "20:00", placeUrl: "https://nol.yanolja.com/stay/domestic/123" };
  const lodging = renderToStaticMarkup(React.createElement(ChatAnswer, { text: "20:00 호텔 [야놀자](https://nol.yanolja.com/stay/domestic/123)", places: [stay] }));
  assert.equal((lodging.match(/href=/g) ?? []).length, 1);
});

test("standard Markdown preserves headings, emphasis, lists, line breaks, code and GFM tables", () => {
  const html = render("# 제목\n\n**굵게** *강조* `inline`\n다음 줄\n\n- 하나\n  - 중첩\n\n3. 셋\n\n```js\n<tag>\n```\n\n| 선수 | 기록 |\n| --- | --- |\n| 곽빈 | 10 |\n");
  for (const pattern of [/<h3>제목<\/h3>/, /<strong>굵게<\/strong>/, /<em>강조<\/em>/, /<br\/>/, /<ul>/, /<ol start="3">/, /&lt;tag&gt;/, /class="chat-table-scroll"/, /<th>선수<\/th>/]) assert.match(html, pattern);
});

test("real internal Next links and HTTPS external links render with safe attributes", () => {
  const html = render("[선수](/standings/players/68220) [팀](/standings/teams/OB) [구장](/stadiums/JAMSIL) [코스](/routes/123) [출처](https://www.tving.com/path)");
  assert.match(html, /href="\/standings\/players\/68220"/);
  assert.match(html, /href="\/stadiums\/JAMSIL"/);
  assert.match(html, /target="_blank" rel="noopener noreferrer"/);
  assert.doesNotMatch(html, /javascript:/);
});

test("standalone detail links become compact Next anchors with formatting, title and decorative arrow", () => {
  for (const href of ["/standings/players/68220#career", "/standings/teams/OB", "/stadiums/JAMSIL", "/routes/123", "/community?post=free-sample-1", "/community/teams?team=LG&post=42"]) {
    const html = render(`  [**상세** *정보* \`보기\`](<${href}> "상세 페이지")  \n`);
    assert.match(html, /<p><a[^>]*class="chat-detail-link"/);
    assert.ok(html.includes(`href="${href.replaceAll("&", "&amp;")}"`));
    assert.match(html, /title="상세 페이지"/);
    assert.match(html, /<strong>상세<\/strong> <em>정보<\/em> <code>보기<\/code>/);
    assert.match(html, /<span aria-hidden="true"> →<\/span>/);
    assert.equal((html.match(/<a\b/g) ?? []).length, 1);
    assert.doesNotMatch(html, /<button|role="button"|tabindex=|target=/i);
  }
  assert.match(source, /prefetch=\{false\}/);
});

test("standalone TVING athlete detail from the browser repro renders as a decorated external anchor", () => {
  const html = render("[곽빈 상세 보기](https://www.tving.com/sports/kbo/athlete/68220)");
  assert.match(html, /<p><a[^>]*class="chat-detail-link"/);
  assert.match(html, /href="https:\/\/www\.tving\.com\/sports\/kbo\/athlete\/68220"/);
  assert.match(html, /target="_blank" rel="noopener noreferrer"/);
  assert.match(html, /곽빈 상세 보기<span aria-hidden="true"> →<\/span>/);
});

test("only validated standalone TVING athlete and uppercase team paths receive external detail styling", () => {
  for (const href of ["https://www.tving.com/sports/kbo/team/OB", "https://www.tving.com/sports/kbo/team/LG", "https://www.tving.com:443/sports/kbo/athlete/68220"]) {
    const html = render(`[**상세** 정보](<${href}> "상세 페이지")`);
    assert.match(html, /class="chat-detail-link"/);
    assert.ok(html.includes(`href="${safeChatUrl(href)}"`), href);
    assert.match(html, /title="상세 페이지"/);
    assert.match(html, /target="_blank" rel="noopener noreferrer"/);
    assert.match(html, /<strong>상세<\/strong> 정보<span aria-hidden="true"> →<\/span>/);
    assert.equal((html.match(/<a\b/g) ?? []).length, 1);
    assert.doesNotMatch(html, /<button|role="button"|tabindex=/i);
  }
  for (const href of [
    "https://www.tving.com.evil.example/sports/kbo/athlete/68220",
    "https://tving.com/sports/kbo/athlete/68220",
    "https://www.tving.com:444/sports/kbo/athlete/68220",
    "https://www.tving.com/sports/kbo/athlete/68220/extra",
    "https://www.tving.com/sports/kbo/athlete/68220/",
    "https://www.tving.com/sports/kbo/athlete/abc",
    "https://www.tving.com/sports/kbo/team/ob",
    "https://www.tving.com/sports/kbo/team/OB1",
    "https://www.tving.com/sports/kbo/team/%4f%42",
    "https://www.tving.com/sports/kbo/athlete/68220%2fextra",
    "https://www.tving.com/sports/kbo/schedule",
    "http://www.tving.com/sports/kbo/athlete/68220",
    "https://user:pass@www.tving.com/sports/kbo/athlete/68220",
    "javascript:alert(1)", "//www.tving.com/sports/kbo/athlete/68220",
  ]) {
    assert.doesNotMatch(render(`[상세](<${href}>)`), /chat-detail-link|aria-hidden/, href);
  }
});

test("inline, multiple, non-paragraph, external and general navigation links stay plain", () => {
  for (const markdown of [
    "자세한 [선수 정보](/standings/players/68220)를 확인하세요.",
    "[선수](/standings/players/68220) [팀](/standings/teams/OB)",
    "[선수](/standings/players/68220)\n다음 줄",
    "# [선수](/standings/players/68220)",
    "**[선수](/standings/players/68220)**",
    "[출처](https://www.tving.com/path)",
    "자세한 [곽빈 상세 보기](https://www.tving.com/sports/kbo/athlete/68220)를 확인하세요.",
    "[선수](https://www.tving.com/sports/kbo/athlete/68220) [팀](https://www.tving.com/sports/kbo/team/OB)",
    "# [선수](https://www.tving.com/sports/kbo/athlete/68220)",
    "**[팀](https://www.tving.com/sports/kbo/team/OB)**",
    ...["/", "/standings", "/stadiums", "/routes", "/community", "/community/teams?team=LG", "/community/predictions?team=OB", "/community/members/1"].map(url => `[목록](${url})`),
  ]) {
    const html = render(markdown);
    assert.match(html, /<a\b/, markdown);
    assert.doesNotMatch(html, /chat-detail-link|aria-hidden/, markdown);
  }
  for (const href of ["/routes/new", "/standings/players/%2e%2e", "/routes/123?redirect=evil", "/community?post=1&post=2", "javascript:alert(1)", "//evil.example/path"]) {
    assert.doesNotMatch(render(`[상세](<${href}>)`), /<a\b|chat-detail-link/, href);
  }
});

test("compact detail link styling preserves focus, hit area and narrow-popup wrapping", () => {
  const css = readFileSync(join(frontend, "styles/chat-answer.css"), "utf8");
  assert.match(css, /a\.chat-detail-link[^}]*min-height: 44px/);
  assert.match(css, /a\.chat-detail-link[^}]*max-width: 100%/);
  assert.match(css, /a\.chat-detail-link[^}]*white-space: normal; overflow-wrap: anywhere/);
  assert.match(css, /a\.chat-detail-link:hover/);
  assert.match(css, /a:focus-visible[^}]*outline: 2px/);
});

test("dangerous schemes, credentials, protocol-relative and unsafe internal paths never become navigable", () => {
  const blocked = ["javascript:alert(1)", "data:text/html,test", "vbscript:test", "file:///tmp/x", "http://example.com", "https://user:pass@example.com", "//evil.example/path", "/\\evil.example", "/admin", "/api/v1/delete", "/login", "/dev/stadium-food", "/routes/new", "/standings/players/../admin", "/standings/players/%2e%2e", "/routes/123?redirect=https://evil.example", "https://example.com\n/path"];
  for (const url of blocked) {
    assert.equal(safeChatUrl(url), undefined, url);
    // A newline is invalid link syntax; GFM may independently autolink its safe first line.
    if (!url.includes("\n")) assert.doesNotMatch(render(`[blocked](<${url}>)`), /<a\b/, url);
  }
  assert.equal(safeChatUrl("/standings/players/68220#career"), "/standings/players/68220#career");
  for (const url of ["/community?post=42", "/community/teams?team=LG&post=42", "/community/predictions?team=OB&date=2026-10-06&game=game_1", "/community/members/1?tab=comments&page=2"]) {
    assert.equal(safeChatUrl(url), url);
    assert.match(render(`[공개 글](<${url}>)`), /<a\b/);
  }
  for (const url of ["/community?redirect=https://evil.example", "/community?post=javascript:alert", "/community?post=1&post=2", "/community/teams?team=LG%26redirect%3Devil", "/community?post=%2F%2Fevil.example"]) assert.equal(safeChatUrl(url), undefined, url);
});

for (const url of ["//evil.example/", "//evil.example/community", "//", "//example:bad/community", "https://example:bad/community", "https:example.com", "https:/example.com"]) {
  test(`protocol-relative or malformed URL fails closed when rendered: ${url}`, () => {
    assert.doesNotMatch(render(`[blocked](<${url}>)`), /<a\b/, url);
    assert.doesNotMatch(render(`![blocked](<${url}>)`), /<img\b/, url);
    assert.equal(safeChatUrl(url), undefined, url);
  });
}

test("inherited query keys never crash rendering or become public links", () => {
  for (const path of ["/community", "/community/teams", "/community/predictions", "/community/members/1", "/standings"]) {
    for (const key of ["constructor", "toString", "__proto__"]) {
      const url = `${path}?${key}=1`;
      assert.doesNotMatch(render(`[blocked](<${url}>)`), /<a\b/, url);
      assert.equal(safeChatUrl(url), undefined, url);
    }
  }
});

test("canonical community links preserve bounded opaque IDs and typed queries", () => {
  const routeSource = readFileSync(join(frontend, "lib/team-community.ts"), "utf8");
  writeFileSync(join(scratch, "community.cjs"), ts.transpileModule(routeSource, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText);
  const { getCommunityPostHref } = createRequire(import.meta.url)(join(scratch, "community.cjs"));
  for (const board of ["free", "teams"]) {
    for (const id of ["42", "free-sample-1", "free/a b", "a".repeat(40)]) {
      const url = getCommunityPostHref({ id, board, teamCode: "LG" });
      assert.equal(safeChatUrl(url), url);
      assert.ok(render(`[공개 글](<${url}>)`).includes(`href="${url.replaceAll("&", "&amp;")}"`), url);
    }
  }
  for (const query of ["post=", `post=${"a".repeat(41)}`, "post=free%00id", "post=free%5Cid", "team=lg", "page=0", "tab=constructor"]) {
    const url = `/community?${query}`;
    assert.doesNotMatch(render(`[blocked](<${url}>)`), /<a\b/, url);
    assert.equal(safeChatUrl(url), undefined, url);
  }
});

test("raw HTML is disabled and blocked images retain accessible fallback text", () => {
  const html = render('<script>alert(1)</script>\n\n<img src="https://evil.example/x" onerror="alert(1)">\n\n![사진](data:image/png;base64,aaaa)');
  assert.doesNotMatch(html, /<script|<img|onerror=/);
  assert.match(html, /role="img" aria-label="사진"/);
  assert.match(html, /이미지를 표시할 수 없어요/);
});

test("image allowlist matches actual sources and verified local files only", () => {
  const good = ["https://image.tving.com/ntgs/sports/kbo/player/68220.png", "https://myseatcheck.com/wp-content/uploads/photo.webp", "/images/stadiums/exteriors/jamsil.jpg", "/images/stadiums/seating-maps/jamsil.png", "/images/stadiums/parking-maps/daejeon-parking.jpg"];
  for (const url of good) {
    assert.equal(safeChatUrl(url, true), url);
    if (url.startsWith("/")) assert.ok(existsSync(join(frontend, "public", url)));
    const html = render(`![실제 사진](${url})`);
    assert.match(html, /<img/);
    assert.match(html, /alt="실제 사진"/);
    assert.match(html, /loading="lazy"/);
    assert.match(html, /referrerPolicy="no-referrer"/);
  }
  for (const url of ["https://image.tving.com.evil.example/x", "http://image.tving.com/x", "https://image.tving.com:444/x", "https://user@image.tving.com/x", "https://example.com/photo.png", "/images/not-real.png", "/images/stadiums/exteriors/../x.jpg", "//image.tving.com/x"]) {
    assert.equal(safeChatUrl(url, true), undefined, url);
    assert.doesNotMatch(render(`![사진](${url})`), /<img\b/, url);
  }
});

test("live progress and restored conversation surfaces share ChatAnswer and bounded overflow styles", () => {
  for (const name of ["chat-workspace", "chat-popup", "chat-progress"]) assert.match(readFileSync(join(frontend, `components/${name}.tsx`), "utf8"), /<ChatAnswer/);
  const css = readFileSync(join(frontend, "styles/chat-answer.css"), "utf8");
  assert.match(css, /\.chat-answer img[^}]*max-width: 100%/);
  assert.match(css, /\.chat-table-scroll[^}]*overflow-x: auto/);
  assert.match(source, /onError=\{\(\) => setFailed\(true\)\}/);
});
