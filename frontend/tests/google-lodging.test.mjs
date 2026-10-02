import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = name => readFileSync(new URL(`../${name}`, import.meta.url), "utf8");
const code = ts.transpileModule(source("lib/google-lodging.ts"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const lodging = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
const { googleLodgingId, googleLodgingStop, locatedStop, canRequestDirections, referenceOnlyStop, LODGING_CATEGORIES, LODGING_SEARCH_LIMIT, lodgingResultMessage } = lodging;

test("ID allowlist, generic label, and unresolved coordinates", () => {
  for (const id of ["", " ", "a/b", "x".repeat(221)]) assert.throws(() => googleLodgingStop(id));
  const stop = googleLodgingStop("test-id");
  assert.equal(googleLodgingId(stop), "test-id");
  assert.equal(stop.category, "숙박");
  assert.equal(stop.name, "선택한 숙소");
  assert.equal(locatedStop(stop), false);
  assert.equal(googleLodgingId({placeId: "collected:SBIZ:1"}), null);
});
test("Google points never enter the persistent directions service", () => {
  const cafe = {name: "카페", category: "카페", lat: 37.5, lng: 127};
  const google = googleLodgingStop("test", 37.4, 126.7);
  assert.equal(locatedStop(google), true);
  assert.equal(canRequestDirections([cafe, google]), false);
  assert.equal(canRequestDirections([cafe]), true);
  assert.equal(canRequestDirections([{...cafe, lat: NaN}]), false);
  assert.ok(Number.isNaN(referenceOnlyStop(google).lat));
  assert.equal(referenceOnlyStop(cafe), cafe);
});
test("widget only uses public UI Kit fields and manual bounded queries", () => {
  const panel = source("components/google-lodging-panel.tsx");
  assert.doesNotMatch(panel, /fetchFields|shadowRoot|\.displayName|\.primaryType|localStorage/);
  assert.match(panel, /gmp-place-type/);
  assert.match(panel, /pageQueries >= LIMIT/);
  assert.match(panel, /operation\.current\+\+/);
  assert.match(source("components/course-travel.tsx"), /canRequestDirections\(stops\)/);
});

test("lodging filters use real provider types and results never promise exhaustiveness", () => {
  assert.deepEqual(LODGING_CATEGORIES, [{ id: "hotel", label: "호텔" }, { id: "motel", label: "모텔" }, { id: "inn", label: "여관" }]);
  assert.equal(LODGING_SEARCH_LIMIT, 20);
  assert.match(lodgingResultMessage("hotel", 20), /한도.*빠질 수/);
  assert.match(lodgingResultMessage("motel", 7), /모텔 7곳/);
  assert.match(lodgingResultMessage("inn", 0), /실제로 숙소가 없는 뜻은 아니/);
});

// Exercise the real component handlers with a small DOM/React fixture. No SDK,
// network request or Google content is needed to verify request configuration.
function panelHarness(stadium = { code: "TEST", name: "테스트 구장", lat: 37.5, lng: 127 }) {
  class Element {
    constructor(tag) { this.tag = tag; this.attributes = {}; this.children = []; this.listeners = new Map(); }
    setAttribute(name, value) { this.attributes[name] = value; }
    append(...children) { this.children.push(...children); }
    replaceChildren() { this.children = []; }
    addEventListener(name, fn) { this.listeners.set(name, fn); }
    removeEventListener(name) { this.listeners.delete(name); }
    remove() { this.removed = true; }
    fire(name, event) { this.listeners.get(name)?.(event); }
  }
  const host = new Element("div"), handle = {}, effects = [], states = [], selected = [];
  let sdkCalls = 0;
  const jsx = (type, props) => {
    if (type === "div" && props.ref) props.ref.current = host;
    return { type, props };
  };
  const react = {
    useRef: current => ({ current }),
    useState: value => { const slot = states.length; states.push(value); return [value, next => { states[slot] = next; }]; },
    useEffect: fn => effects.push(fn()),
    useLayoutEffect: fn => fn(),
    useImperativeHandle: (ref, create) => { if (ref) ref.current = create(); },
  };
  const dependencies = {
    react,
    "react/jsx-runtime": { jsx, jsxs: jsx },
    "@/lib/google-lodging": lodging,
    "@/lib/google-places-ui-kit": { loadGooglePlacesUiKit: async () => { sdkCalls++; } },
    "@/lib/google-lodging-pilot": { projectUiKitPlace: place => place ?? null },
    "./google-lodging-panel.module.css": { default: {} },
  };
  const compiled = ts.transpileModule(source("components/google-lodging-panel.tsx"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} };
  new Function("module", "exports", "require", "document", "process", compiled)(module, module.exports,
    name => { assert.ok(name in dependencies, name); return dependencies[name]; },
    { createElement: tag => new Element(tag) }, { env: { NEXT_PUBLIC_GOOGLE_MAPS_API_KEY: "test-key" } });
  module.exports.GoogleLodgingPanel({ ref: handle, stadium, stops: [], onSelect: stop => selected.push(stop) });
  return { host, states, selected, sdkCalls: () => sdkCalls, close: () => effects.forEach(fn => fn?.()), search: async kind => {
    handle.current.search(kind); await Promise.resolve(); return host.children[0];
  } };
}

test("manual hotel/motel/inn queries request 20 in-radius results with bottom attribution", async () => {
  const panel = panelHarness();
  try {
    assert.equal(panel.sdkCalls(), 0, "mounting must not query");
    for (const { id } of LODGING_CATEGORIES) {
      const widget = await panel.search(id);
      assert.equal(widget.tag, "gmp-place-search");
      assert.equal(widget.attributes["attribution-position"], "bottom");
      const request = widget.children.find(child => child.tag === "gmp-place-nearby-search-request");
      assert.deepEqual(request.attributes, { "included-primary-types": id, "max-result-count": "20", "rank-preference": "DISTANCE", "location-restriction": "2500@37.5,127" });
      const config = widget.children.find(child => child.tag === "gmp-place-content-config");
      assert.deepEqual(config.children.map(child => child.tag), ["gmp-place-address", "gmp-place-type", "gmp-place-attribution"]);
      assert.equal(config.children[2].attributes["light-scheme-color"], "gray");
      widget.places = Array.from({ length: 20 }, () => ({}));
      widget.fire("gmp-load");
      assert.ok(panel.states.some(value => typeof value === "string" && /한도.*빠질 수/.test(value)));
    }
    assert.equal(panel.sdkCalls(), 3);
  } finally { panel.close(); }
});

test("pending requests cannot duplicate calls and selection stays a generic ID reference", async () => {
  const panel = panelHarness();
  try {
    const widget = await panel.search("inn");
    await panel.search("inn");
    assert.equal(panel.sdkCalls(), 1);
    widget.places = [];
    widget.fire("gmp-load");
    widget.fire("gmp-select", { place: { id: "test-inn", lat: 37.5, lng: 127 } });
    assert.equal(panel.selected[0].name, "선택한 숙소");
    assert.equal(panel.selected[0].category, "숙박", "a filter is not a verified legal business type");
    assert.equal(panel.selected[0].placeId, "google-ui-kit:test-inn");
  } finally { panel.close(); }
});
