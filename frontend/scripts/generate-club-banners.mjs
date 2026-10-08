// Deterministic artwork generation, not an AI-generated/reconstructed logo.
// Run from frontend: node scripts/generate-club-banners.mjs
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = new URL("../", import.meta.url);
const source = readFileSync(new URL("lib/club-ads.ts", root), "utf8");
const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const loaded = { exports: {} };
new Function("exports", output)(loaded.exports);
const { clubAds } = loaded.exports;
const directory = new URL("public/images/ads/clubs/", root);
mkdirSync(directory, { recursive: true });
const escape = value => value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");

for (const club of clubAds) {
  const logo = readFileSync(new URL(`public/images/teams/${club.code.toLowerCase()}.svg`, root), "utf8");
  for (const mobile of [false, true]) {
    const w = mobile ? 720 : 1200, h = mobile ? 420 : 400;
    const x = mobile ? 62 : 108;
    const logoX = mobile ? 437 : 802, logoY = mobile ? 80 : 40, logoSize = mobile ? 240 : 320;
    const embeddedLogo = logo.replace(/<svg\b[^>]*>/, `<svg x="${logoX}" y="${logoY}" width="${logoSize}" height="${logoSize}" viewBox="0 0 96 96" fill="none" xmlns="http://www.w3.org/2000/svg">`);
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
<title>${escape(club.name)} 공식 인스타그램 안내 · 목업 광고</title>
<defs>
  <linearGradient id="club-bg" x1="0" y1="0" x2="1" y2=".65"><stop stop-color="${club.color}"/><stop offset="1" stop-color="${club.dark}"/></linearGradient>
  <radialGradient id="club-glow"><stop stop-color="#ffffff" stop-opacity=".22"/><stop offset="1" stop-color="#ffffff" stop-opacity="0"/></radialGradient>
</defs>
<rect width="${w}" height="${h}" fill="url(#club-bg)"/>
<ellipse cx="${w * .8}" cy="180" rx="390" ry="350" fill="url(#club-glow)"/>
<g fill="none" stroke="#ffffff" stroke-opacity=".10">
  <circle cx="${w * .8}" cy="195" r="205"/><circle cx="${w * .8}" cy="195" r="235"/><circle cx="${w * .8}" cy="195" r="290"/>
  <path d="M${w * .5} ${h} L${w * .85} 0 L${w} 0" stroke-width="48" stroke-opacity=".04"/>
</g>
<g font-family="'Noto Sans KR', 'Malgun Gothic', Arial, sans-serif" fill="#ffffff">
  <text x="${x}" y="65" font-size="${mobile ? 17 : 14}" letter-spacing="3" opacity=".82">CLUB STORIES / ${escape(club.english)}</text>
  <text x="${x}" y="${mobile ? 155 : 152}" font-size="${mobile ? 43 : 58}" font-weight="900" letter-spacing="-2">${escape(club.name)}</text>
  <text x="${x}" y="${mobile ? 205 : 210}" font-size="${mobile ? 25 : 32}" font-weight="500">경기 밖의 순간까지, 함께.</text>
  <text x="${x}" y="${mobile ? 250 : 255}" font-size="${mobile ? 19 : 19}" opacity=".8">공식 인스타그램에서 만나보세요.</text>
  <text x="${x}" y="${mobile ? 330 : 331}" font-size="${mobile ? 20 : 17}" font-weight="700">구단 소식 보러 가기 ↗</text>
</g>
${embeddedLogo}
</svg>
`;
    writeFileSync(new URL(`${club.code.toLowerCase()}${mobile ? "-mobile" : ""}.svg`, directory), svg);
  }
}
console.log(`Generated ${clubAds.length * 2} club banner artworks in ${fileURLToPath(directory)}`);
