import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import ts from "typescript";

const source = path => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const compiled = ts.transpileModule(source("lib/google-lodging-pilot.ts"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { serializePilotReference, parsePilotReference, projectUiKitPlace, PILOT_REQUEST_LIMIT } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("pilot persistence allowlists ID and own stadium, never place content", () => {
  const reference = { version: 1, stadium: "MUNHAK", placeId: "test-place-id" };
  assert.deepEqual(JSON.parse(serializePilotReference(reference.placeId)), reference);
  assert.deepEqual(parsePilotReference(JSON.stringify({ ...reference, name: "숙소", address: "주소", type: "hotel", lat: 37, lng: 126 })), reference);
  for (const bad of [null, "{broken", "null", "[]", JSON.stringify({ ...reference, stadium: "JAMSIL" }), JSON.stringify({ ...reference, placeId: " " })]) {
    assert.equal(parsePilotReference(bad), null);
  }
  assert.throws(() => serializePilotReference(""));
  assert.throws(() => serializePilotReference("a".repeat(513)));
});

test("selection uses only UI Kit public ID and coordinates, rejects missing location", () => {
  const place = { id: "place-1", location: { lat: () => 37.4, lng: () => 126.7 }, name: "not exported", types: ["hotel"] };
  assert.deepEqual(projectUiKitPlace(place), { id: "place-1", lat: 37.4, lng: 126.7 });
  assert.equal(projectUiKitPlace(undefined), null);
  assert.equal(projectUiKitPlace({ id: "place-1" }), null);
  assert.equal(projectUiKitPlace({ ...place, location: { lat: () => NaN, lng: () => 126 } }), null);
  assert.equal(projectUiKitPlace({ ...place, location: { lat: () => 91, lng: () => 126 } }), null);
});

test("pilot is development-only, bounded and isolated from course/directions persistence", () => {
  assert.equal(PILOT_REQUEST_LIMIT, 4);
  const page = source("app/dev/google-lodging/page.tsx");
  assert.match(page, /process\.env\.NODE_ENV !== "development"\) notFound\(\)/);
  assert.match(page, /referrer: "strict-origin-when-cross-origin"/);
  const component = source("components/google-lodging-pilot.tsx");
  assert.match(component, /NEXT_PUBLIC_GOOGLE_MAPS_API_KEY/);
  assert.match(component, /loadKakaoMaps\(\{ referrerPolicy: "no-referrer" \}\)/);
  assert.match(component, /requestCount\.current >= PILOT_REQUEST_LIMIT/);
  assert.match(component, /request\.setAttribute\("max-result-count", "5"\)/);
  assert.match(component, /gmp-place-type/);
  assert.match(component, /직선 연결 \(실제 길찾기 아님\)/);
  assert.doesNotMatch(component, /GOOGLE_PLACES_API_KEY|fetchFields|shadowRoot|fetch\(|\/api\//);
  assert.match(component, /localStorage\.setItem\(PILOT_STORAGE_KEY, serializePilotReference\(selection\.id\)\)/);
});

test("Google SDK rejects a missing browser key without accessing browser or network", async () => {
  const sdkCode = ts.transpileModule(source("lib/google-places-ui-kit.ts"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
  }).outputText;
  const { loadGooglePlacesUiKit } = await import(`data:text/javascript;base64,${Buffer.from(sdkCode).toString("base64")}`);
  await assert.rejects(loadGooglePlacesUiKit(" "), /브라우저용 Google 키/);
});
