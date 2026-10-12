// Deterministic NOL discovery + rendered detail. Node 24, no JEV or model SDK.
import { spawn, spawnSync } from 'node:child_process';
import { mkdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const HOME = 'https://nol.yanolja.com/';
const MAX_BODY = 300_000;
const BLOCKED = /일시적으로 서비스를 이용|서비스 이용이 원활하지|Access Denied|verify you are human|unusual traffic|로봇이 아님|보안 확인/i;
const compact = s => s.toLowerCase().replace(/[^가-힣a-z0-9]/g, '');
const [root, name, seconds = '45', region = ''] = process.argv.slice(2);
const deadline = Date.now() + Number(seconds) * 1000;
const chromePath = process.env.LODGING_CHROME_PATH;
let active;
let stage = 'startup';
let pageState;

function fail(status) { throw Object.assign(new Error(status), { status }); }
function check() { if (Date.now() >= deadline) fail('timeout'); }
function detailUrl(value) {
  try {
    const u = new URL(value);
    if (u.protocol !== 'https:' || u.hostname !== 'nol.yanolja.com' || u.port || u.username || u.password ||
        !/^\/stay\/domestic\/\d+\/?$/.test(u.pathname)) return null;
    return `${u.origin}${u.pathname.replace(/\/$/, '')}?verticalCategory=PRODUCT_CATEGORY_KOREA_ACCOMMODATION`;
  } catch { return null; }
}

async function start(label) {
  check();
  const profile = join(root, label);
  await mkdir(profile);
  const child = spawn(chromePath, [
    '--remote-debugging-port=0', `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--window-size=1280,1000',
    ...(process.env.LODGING_CHROME_ARGS || '').split(/\s+/).filter(Boolean), 'about:blank',
  ], { stdio: 'ignore', windowsHide: true });
  const browser = active = { child, exited: false, pending: new Map(), seq: 0 };
  child.once('exit', () => { browser.exited = true; });
  child.once('error', () => { browser.exited = true; });
  let port;
  for (let i = 0; i < 100; i++) {
    check();
    if (browser.exited) fail('unavailable');
    try { port = (await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]; break; }
    catch { await delay(100); }
  }
  if (!port || !/^\d+$/.test(port)) fail('timeout');
  const info = await (await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(3000) })).json();
  const ws = browser.ws = new WebSocket(info.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('CDP unavailable')), 3000);
    ws.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
    ws.addEventListener('error', () => { clearTimeout(timer); reject(new Error('CDP unavailable')); }, { once: true });
  });
  ws.addEventListener('message', event => {
    const message = JSON.parse(event.data), item = browser.pending.get(message.id);
    if (!item) return;
    browser.pending.delete(message.id); clearTimeout(item.timer);
    if (message.error) item.reject(new Error('CDP error')); else item.resolve(message.result);
  });
  const rejectPending = () => {
    for (const item of browser.pending.values()) { clearTimeout(item.timer); item.reject(new Error('CDP closed')); }
    browser.pending.clear();
  };
  ws.addEventListener('close', rejectPending);
  ws.addEventListener('error', rejectPending);
  browser.call = (method, params = {}, sessionId = browser.session) => new Promise((resolve, reject) => {
    const id = ++browser.seq;
    const timeout = Math.max(1, Math.min(method === 'Page.navigate' ? 15000 : 5000, deadline - Date.now()));
    const timer = setTimeout(() => { browser.pending.delete(id); reject(Object.assign(new Error('CDP timeout'), { step: method })); }, timeout);
    browser.pending.set(id, { resolve, reject, timer });
    try { ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) })); }
    catch { browser.pending.delete(id); clearTimeout(timer); reject(new Error('CDP unavailable')); }
  });
  const targets = await browser.call('Target.getTargets');
  const tab = targets.targetInfos.find(t => t.type === 'page');
  if (!tab) fail('error');
  browser.session = (await browser.call('Target.attachToTarget', { targetId: tab.targetId, flatten: true })).sessionId;
  await browser.call('Page.enable');
  await browser.call('Runtime.enable');
  await browser.call('Browser.setDownloadBehavior', { behavior: 'deny' }, null);
  return browser;
}

async function close() {
  const b = active;
  if (!b) return;
  active = undefined;
  if (b.call) await b.call('Browser.close', {}, null).catch(() => {});
  b.ws?.close();
  for (let i = 0; i < 20 && !b.exited; i++) await delay(100);
  if (!b.exited && b.child.pid) {
    if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(b.child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore', timeout: 5000 });
    else b.child.kill('SIGKILL');
  }
  for (const p of b.pending.values()) clearTimeout(p.timer);
  b.pending.clear();
}

async function evaluate(b, expression) {
  check();
  const result = await b.call('Runtime.evaluate', { expression, returnByValue: true });
  if (result.exceptionDetails) fail('error');
  return result.result?.value;
}

async function snapshot(b) {
  check();
  await b.call('Page.bringToFront');
  // NOL can expose only its footer until the page has painted. Do not persist images.
  await b.call('Page.captureScreenshot', { format: 'png' });
  const doc = await evaluate(b, `(() => ({
    url:location.href, title:document.title, body:document.body?.innerText || '', readyState:document.readyState,
    links:[...document.querySelectorAll('a[aria-label]')].filter(e=>e.getClientRects().length)
      .map(e=>({name:e.getAttribute('aria-label').replace(/ 상품 상세 보기$/, ''),url:e.href}))
  }))()`);
  pageState = { title: doc.title.slice(0, 160), readyState: doc.readyState, bodyChars: doc.body.length,
    detailLinks: doc.links.filter(l => detailUrl(l.url)).length, queryMatches: new URL(doc.url).searchParams.get('q') === [region.trim(), name.trim()].filter(Boolean).join(' '),
    path: new URL(doc.url).pathname };
  if (BLOCKED.test(doc.body)) fail('blocked');
  if (new URL(doc.url).origin !== HOME.slice(0, -1)) fail('blocked');
  if (doc.body.length > MAX_BODY) fail('overflow');
  return doc;
}

export async function navigate(b, url) {
  check();
  const result = await b.call('Page.navigate', { url });
  if (result.errorText) fail('error');
  // Advertising and other subresources can keep NOL's load event pending.
  // The caller checks the actual search results or stable detail body next.
  await waitDom(b, 'location.href !== "about:blank" && !!document.body');
}

async function waitDom(b, predicate, milliseconds = 15000) {
  const until = Math.min(deadline, Date.now() + milliseconds);
  while (Date.now() < until) {
    check();
    try {
      if (await evaluate(b, predicate)) return;
    } catch (error) {
      // A navigation destroys the old execution context. Keep the same bounded wait.
      if (error.status === 'timeout') throw error;
    }
    await delay(150);
  }
  fail('timeout');
}

async function settledSnapshot(b, ready, milliseconds = 15000) {
  const until = Math.min(deadline, Date.now() + milliseconds);
  let previous = '', stableSince = 0;
  while (Date.now() < until) {
    let page;
    try { page = await snapshot(b); }
    catch (error) {
      // A navigation can replace its execution context between paint and DOM read.
      if (error.status) throw error;
      await delay(150);
      continue;
    }
    const value = ready(page);
    if (value && value === previous) {
      if (Date.now() - stableSince >= 750) return page;
    } else {
      previous = value || '';
      stableSince = Date.now();
    }
    await delay(250);
  }
  fail('partial');
}

export function searchUrl(query) {
  const url = new URL('/discovery/list/search/PRODUCT_CATEGORY_KOREA_ACCOMMODATION', HOME);
  url.searchParams.set('q', query);
  return url.href;
}

// The platform sometimes moves the generic hotel/motel word after the name.
export function nameMatches(observed, expected) {
  const core = value => compact(value).replace(/호텔|모텔/g, '');
  const actual = core(observed), wanted = core(expected);
  return (wanted.length > 0 && compact(observed).endsWith(compact(expected))) ||
    (wanted.length >= 2 && actual.endsWith(wanted));
}

export function searchReady(page) {
  const links = page.links.filter(l => detailUrl(l.url));
  if (links.length) return JSON.stringify(links);
  return /검색\s*결과가\s*없|일치하는 상품이 없어요/.test(page.body) ? 'no_results' : '';
}

async function collect() {
  if (!root || !chromePath || !name?.trim() || name.length > 255 || !Number.isFinite(Number(seconds)) || Number(seconds) < 1 || Number(seconds) > 60) fail('error');
  let b = await start('search');
  const searchStarted = Date.now();
  // Use the public search URL observed in NOL's UI. This avoids interacting
  // with a partially hydrated home header. We do not filter room availability.
  stage = 'search_navigation';
  const query = [region.trim(), name.trim()].filter(Boolean).join(' ');
  await navigate(b, searchUrl(query));
  stage = 'search_results';
  const results = await settledSnapshot(b, searchReady);
  const links = results.links.filter(l => detailUrl(l.url));
  const exact = links.filter(l => compact(l.name) === compact(name));
  const matches = exact.length ? exact : links.filter(l => nameMatches(l.name, name));
  const urls = [...new Set(matches.map(l => detailUrl(l.url)))];
  if (urls.length !== 1) fail(urls.length ? 'ambiguous' : 'not_found');
  await close();
  const searchMs = Date.now() - searchStarted;
  const detailStarted = Date.now();
  stage = 'detail_startup';
  b = await start('detail');
  stage = 'detail_navigation';
  await navigate(b, urls[0]);
  const detailReadyAt = Date.now();
  stage = 'detail_content';
  const page = await settledSnapshot(b, page =>
    /위치\/교통\s*\n[^\n]+\n\s*주소\s*복사/.test(page.body)
      && ((/시설\/서비스|서비스 및 시설/.test(page.body) && /이용 안내|이용안내/.test(page.body))
        // Some valid properties omit these headings. Keep the previous settling
        // window for that layout instead of requiring extra facilities to exist.
        || Date.now() - detailReadyAt >= 5500) ? page.body : '');
  if (detailUrl(page.url) !== urls[0]) fail('blocked');
  const address = page.body.match(/위치\/교통\s*\n([^\n]+)\n\s*주소\s*복사/);
  const titleName = page.title.match(/^(.*?)\s+(?:호텔\/리조트|모텔|펜션|게스트하우스|리조트)\s+예약/);
  if (!address || !titleName || !compact(page.body).includes(compact(titleName[1]))) fail('partial');
  return { status: 'read', source_kind: 'rendered_dom_snapshot', url: page.url,
    title: page.title, name: titleName[1].trim(), address: address[1].trim(),
    body_text: page.body, body_read: true, collected_at: new Date().toISOString(),
    timing: { search_ms: searchMs, detail_ms: Date.now() - detailStarted } };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  let result;
  try { result = await collect(); }
  catch (error) { result = { status: error.status || (Date.now() >= deadline ? 'timeout' : 'error'), stage, pageState,
    ...(error.step ? { step: error.step } : {}) }; }
  finally { await close(); }
  process.stdout.write(JSON.stringify(result));
}
