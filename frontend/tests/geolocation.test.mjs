import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync(new URL("../lib/geolocation.ts", import.meta.url), "utf8");
const exports = {};
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { exports });
const { requestCurrentLocation } = exports;

test("HTTPS requests a fresh accurate GPS position only on explicit invocation", () => {
  let success, options, point;
  const environment = { secure: true, geolocation: { getCurrentPosition(onSuccess, _, args) { success = onSuccess; options = args; } } };
  assert.equal(success, undefined);
  requestCurrentLocation(value => { point = value; }, assert.fail, environment);
  assert.equal(options.enableHighAccuracy, true);
  assert.equal(options.maximumAge, 0);
  assert.equal(options.timeout, 12000);
  success({ coords: { latitude: 37.55, longitude: 126.97 } });
  assert.equal(point.lat, 37.55);
  assert.equal(point.lng, 126.97);
});

test("insecure and unsupported browsers explain why GPS cannot run", () => {
  for (const environment of [{ secure: false, geolocation: { getCurrentPosition: assert.fail } }, { secure: true }]) {
    let message;
    requestCurrentLocation(assert.fail, value => { message = value; }, environment);
    assert.match(message, /지도에서 출발지/);
    assert.match(message, environment.secure ? /지원하지/ : /HTTPS/);
  }
});

test("permission denied, timeout and unavailable errors give distinct recovery instructions", () => {
  for (const [code, expected] of [[1, /권한/], [2, /위치 설정/], [3, /시간이 초과/]]) {
    let message;
    requestCurrentLocation(assert.fail, value => { message = value; }, { secure: true, geolocation: { getCurrentPosition(_, reject) { reject({ code }); } } });
    assert.match(message, expected);
  }
});

test("invalid GPS coordinates are not sent into the map or chatbot", () => {
  requestCurrentLocation(assert.fail, message => assert.match(message, /좌표/), { secure: true, geolocation: {
    getCurrentPosition(success) { success({ coords: { latitude: NaN, longitude: 127 } }); },
  } });
});
