// Build self-contained banner SVGs using unchanged official product-photo bytes.
// Run from frontend: node scripts/generate-supplier-banners.mjs
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import ts from "typescript";
import sharp from "sharp";
const root = new URL("../", import.meta.url);
const loaded = { exports: {} };
new Function("exports", ts.transpileModule(readFileSync(new URL("lib/supplier-ads.ts", root), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText)(loaded.exports);
const directory = new URL("public/images/ads/suppliers/", root);
mkdirSync(directory, { recursive: true });
const escape = value => value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");
for (const ad of loaded.exports.supplierAds) {
  const bytes = readFileSync(new URL(`source/${ad.id}.img`, directory));
  const metadata = await sharp(bytes).metadata();
  if (!["jpeg", "png", "webp"].includes(metadata.format)) throw new Error(`Unsupported photo: ${ad.id}`);
  const data = `data:image/${metadata.format};base64,${bytes.toString("base64")}`;
  for (const mobile of [false, true]) {
    const w = mobile ? 720 : 1200, h = mobile ? 420 : 400;
    const x = mobile ? 60 : 104;
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
<title>${escape(ad.name)} · ${escape(ad.product)} · 목업 광고</title>
<defs>
<linearGradient id="text-shade" x1="0" y1="0" x2="1" y2="0">
<stop stop-color="#07111c" stop-opacity=".88"/>
<stop offset=".45" stop-color="#07111c" stop-opacity=".66"/>
<stop offset="1" stop-color="#07111c" stop-opacity=".16"/>
</linearGradient>
<linearGradient id="bottom-shade" x1="0" y1="0" x2="0" y2="1">
<stop offset=".6" stop-color="#07111c" stop-opacity="0"/>
<stop offset="1" stop-color="#07111c" stop-opacity=".45"/>
</linearGradient>
</defs>
<rect width="${w}" height="${h}" fill="#edf0f2"/>
<!-- Full-bleed product photo. The wide crop intentionally emphasizes product detail. -->
<image href="${data}" x="0" y="0" width="${w}" height="${h}" preserveAspectRatio="xMidYMid slice"/>
<rect width="${w}" height="${h}" fill="url(#text-shade)"/>
<rect width="${w}" height="${h}" fill="url(#bottom-shade)"/>
<g font-family="'Noto Sans KR', 'Malgun Gothic', Arial, sans-serif" fill="#fff">
<text x="${x}" y="62" font-size="${mobile ? 16 : 14}" letter-spacing="3" opacity=".8">GEAR FOR THE GAME</text>
<text x="${x}" y="${mobile ? 128 : 142}" font-size="${mobile ? 37 : 53}" font-weight="900" letter-spacing="-1">${escape(ad.name)}</text>
<text x="${x}" y="${mobile ? 178 : 196}" font-size="${mobile ? 20 : 25}" font-weight="600">${escape(ad.product)}</text>
<text x="${x}" y="${mobile ? 222 : 244}" font-size="${mobile ? 18 : 18}" opacity=".8">${escape(ad.teams.join(" · "))} 용품 후원 브랜드</text>
<text x="${x}" y="${mobile ? 262 : 282}" font-size="${mobile ? 16 : 16}" opacity=".65">상품 이미지 예시 · 실제 제휴 광고 아님</text>
<text x="${x}" y="${mobile ? 333 : 337}" font-size="${mobile ? 20 : 18}" font-weight="700">공식 판매처에서 보기 ↗</text>
</g>
</svg>\n`;
    writeFileSync(new URL(`${ad.id}${mobile ? "-mobile" : ""}.svg`, directory), svg);
  }
  console.log(`${ad.id}: ${metadata.width}x${metadata.height}, ${bytes.length} bytes`);
}

