import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

function load(file, dependencies = {}) {
  const source = readFileSync(new URL("../" + file, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } });
  const loadedModule = { exports: {} };
  new Function("module", "exports", "require", outputText)(loadedModule, loadedModule.exports, name => {
    if (!(name in dependencies)) throw new Error("Unexpected dependency: " + name);
    return dependencies[name];
  });
  return loadedModule.exports;
}
const domain = load("lib/ads.ts"), mocks = load("lib/ad-mocks.ts");
const client = load("lib/api/client.ts");
const apiWith = (mode, request) => load("lib/api/ads.ts", { "./client": { apiRequest: request }, "../ads": { ...domain, AD_MODE: mode }, "../ad-mocks": mocks });
const query = { placement: "home-club-banner", routeIds: [] };

test("mock banners link to the requested example advertisers", () => {
  const destinations = {
    "home-club-banner": "https://www.instagram.com/busanlottegiants/",
    "home-route-partner-banner": "https://naver.me/xSnyHYCQ",
  };
  for (const [placement, href] of Object.entries(destinations)) {
    const delivery = mocks.getMockDelivery(placement);
    assert.equal(delivery.ad.destination_url, href);
    assert.equal(domain.safeAdUrl(href), href);
    assert.equal(delivery.preview, true);
    assert.equal(delivery.token, "");
  }
});

test("ad modes fail closed for unknown values", () => {
  assert.equal(domain.parseAdMode(undefined), "mock");
  for (const mode of ["mock", "api", "off"]) assert.equal(domain.parseAdMode(mode), mode);
  assert.equal(domain.parseAdMode("invalid"), "off");
});
test("ad links accept local paths and HTTPS only", () => {
  assert.equal(domain.safeAdUrl("/routes"), "/routes");
  assert.equal(domain.safeAdUrl("https://example.com/ad"), "https://example.com/ad");
  for (const value of ["//example.com", "javascript:alert(1)", "http://example.com", "/\\example.com", "https://user:password@example.com", "/route\nnext"]) assert.equal(domain.safeAdUrl(value), null);
});
test("period boundaries, placement and active state are checked", () => {
  const ad = { ...mocks.mockAds[0], starts_at: "2026-10-01T00:00:00Z", ends_at: "2026-10-02T00:00:00Z" };
  const now = Date.parse(ad.starts_at);
  assert.equal(domain.isActiveAd(ad, ad.placement, now), true);
  assert.equal(domain.isActiveAd(ad, ad.placement, now - 1), false);
  assert.equal(domain.isActiveAd(ad, ad.placement, Date.parse(ad.ends_at)), false);
  assert.equal(domain.isActiveAd({ ...ad, active: false }, ad.placement, now), false);
  assert.equal(domain.isActiveAd({ ...ad, starts_at: "invalid" }, ad.placement, now), false);
  assert.equal(domain.isActiveAd(ad, "home-route-partner-banner", now), false);
});
test("mock and off modes never send traffic", async () => {
  for (const mode of ["mock", "off"]) {
    const api = apiWith(mode, () => { throw new Error("unexpected request"); });
    const delivery = await api.loadHomeAd(query, new AbortController().signal);
    if (mode === "off") assert.equal(delivery, null);
    else {
      assert.equal(delivery.preview, true);
      await api.trackHomeAd(delivery, "impression");
      await api.trackHomeAd(delivery, "click");
    }
  }
});
test("api query and events use v1 contract without sending route data in events", async () => {
  const calls = [], data = { ...mocks.getMockDelivery(query.placement), token: "signed", exposure_id: "exposure" };
  const api = apiWith("api", async (path, init) => { calls.push([path, init]); return data; });
  const delivery = await api.loadHomeAd({ ...query, routeIds: ["a/b", "a/b", "c"] }, new AbortController().signal);
  assert.equal(delivery.preview, false);
  const url = new URL(calls[0][0], "http://localhost");
  assert.equal(url.pathname, "/api/v1/ads/slots/");
  assert.deepEqual(url.searchParams.getAll("route_ids"), ["a/b", "c"]);
  await api.trackHomeAd(delivery, "click");
  const body = JSON.parse(calls[1][1].body);
  assert.equal(calls[1][0], "/api/v1/ads/events/");
  assert.equal(body.kind, "click");
  assert.equal(body.token, "signed");
  assert.ok(body.event_id);
  assert.equal(body.route_ids, undefined);
  await api.trackHomeAd({ ...delivery, preview: true }, "click");
  assert.equal(calls.length, 2);
});
test("empty, expired and unsigned API deliveries are hidden", async () => {
  const mock = mocks.getMockDelivery(query.placement);
  for (const data of [null, { ...mock, ad: null }, mock, { ...mock, token: "x", exposure_id: "x", valid_until: mock.server_now }]) {
    assert.equal(await apiWith("api", async () => data).loadHomeAd(query, new AbortController().signal), null);
  }
});
test("API failure does not fall back to a mock", async () => {
  const api = apiWith("api", async () => { throw new client.ApiError("offline", 503); });
  await assert.rejects(api.loadHomeAd(query, new AbortController().signal), /offline/);
});
test("lifetime uses server time and the earlier of token/ad expiry", () => {
  const data = mocks.getMockDelivery(query.placement);
  data.server_now = "2026-10-01T00:00:00Z";
  data.valid_until = "2026-10-01T01:00:00Z";
  assert.equal(domain.deliveryLifetime(data), 3600000);
  assert.equal(domain.deliveryLifetime({ ...data, valid_until: "invalid" }), 0);
});
test("impression requires continuous foreground visibility and disconnect cleans up", context => {
  context.mock.timers.enable({ apis: ["setTimeout"] });
  let callback, listener, disconnected = false, sent = 0;
  const previousObserver = globalThis.IntersectionObserver, previousDocument = globalThis.document;
  globalThis.IntersectionObserver = class { constructor(cb) { callback = cb; } observe() {} disconnect() { disconnected = true; } };
  globalThis.document = { visibilityState: "visible", addEventListener(_, fn) { listener = fn; }, removeEventListener() {} };
  try {
    const { observeAdImpression } = load("lib/ad-visibility.ts");
    const cleanup = observeAdImpression({}, () => sent++);
    const show = ratio => callback([{ isIntersecting: ratio > 0, intersectionRatio: ratio }]);
    show(.49); context.mock.timers.tick(2000); assert.equal(sent, 0);
    show(.5); context.mock.timers.tick(600);
    document.visibilityState = "hidden"; listener(); context.mock.timers.tick(2000); assert.equal(sent, 0);
    document.visibilityState = "visible"; listener(); context.mock.timers.tick(999); assert.equal(sent, 0);
    context.mock.timers.tick(1); assert.equal(sent, 1);
    show(1); context.mock.timers.tick(2000); assert.equal(sent, 1);
    cleanup(); assert.equal(disconnected, true);
  } finally {
    if (previousObserver === undefined) delete globalThis.IntersectionObserver; else globalThis.IntersectionObserver = previousObserver;
    if (previousDocument === undefined) delete globalThis.document; else globalThis.document = previousDocument;
  }
});
