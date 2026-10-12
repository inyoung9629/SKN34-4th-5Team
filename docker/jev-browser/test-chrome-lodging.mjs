import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { navigate, searchUrl, nameMatches, searchReady } from './chrome-lodging.mjs';

test('navigation accepts a usable document while subresources are loading', { timeout: 1000 }, async () => {
  const context = { document: { body: {}, readyState: 'interactive' }, location: { href: 'https://nol.yanolja.com/' } };
  const browser = { call: async (method, args) => method === 'Page.navigate' ? {} :
    { result: { value: vm.runInNewContext(args.expression, context) } } };
  await navigate(browser, context.location.href);
});

test('navigation waits for the new document instead of accepting about:blank', { timeout: 1000 }, async () => {
  let checks = 0;
  const browser = { call: async (method, args) => {
    if (method === 'Page.navigate') return {};
    checks += 1;
    return { result: { value: vm.runInNewContext(args.expression, {
      document: { body: {}, readyState: 'interactive' },
      location: { href: checks === 1 ? 'about:blank' : 'https://nol.yanolja.com/' },
    }) } };
  } };
  await navigate(browser, 'https://nol.yanolja.com/');
  assert.equal(checks, 2);
});

test('navigation errors remain errors', async () => {
  const browser = { call: async () => ({ errorText: 'navigation failed' }) };
  await assert.rejects(navigate(browser, 'https://nol.yanolja.com/'), { status: 'error' });
});

test('search URL stays on NOL and safely encodes the literal query', () => {
  const url = new URL(searchUrl('서울 송파구 호텔상주 & q=other'));
  assert.equal(url.origin, 'https://nol.yanolja.com');
  assert.equal(url.searchParams.get('q'), '서울 송파구 호텔상주 & q=other');
  assert.equal([...url.searchParams].length, 1);
});

test('discovery accepts reordered hotel labels but keeps branch identity', () => {
  assert.equal(nameMatches('잠실 상주 호텔', '호텔상주'), true);
  assert.equal(nameMatches('부평 호텔봄', '호텔봄'), true); // Address is checked separately.
  assert.equal(nameMatches('잠실 포레스타 2 호텔', '포레스타호텔'), false);
  assert.equal(nameMatches('잠실 호텔', '호텔'), false);
});

test('an explicit empty result ends search without waiting for nonexistent links', () => {
  assert.equal(searchReady({ links: [], body: '일치하는 상품이 없어요 지역명, 상품명으로 검색어를 변경해 보세요' }), 'no_results');
  assert.equal(searchReady({ links: [], body: '마이 찜 장바구니' }), '');
});
