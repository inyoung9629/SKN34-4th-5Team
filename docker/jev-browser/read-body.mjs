// Deterministic DOM snapshot, no provider/model calls, credentials or shared profile.
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';

const [url, profile] = process.argv.slice(2);
const chrome = spawn(process.env.CHROME_PATH || '/usr/bin/chromium', [
  '--headless', '--remote-debugging-port=0', `--user-data-dir=${profile}`,
  ...(process.env.JEV_CHROME_ARGS || '').split(/\s+/).filter(Boolean), 'about:blank',
], { stdio: 'ignore' });
let socket;
try {
  let port;
  for (let i = 0; i < 100; i++) {
    try { port = (await readFile(`${profile}/DevToolsActivePort`, 'utf8')).split('\n')[0]; break; }
    catch { if (chrome.exitCode !== null) throw Error('browser_failed'); await delay(100); }
  }
  if (!port) throw Error('browser_not_ready');
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const target = targets.find(target => target.type === 'page');
  if (!target) throw Error('missing_page');
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let id = 0;
  const pending = new Map();
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    const waiter = pending.get(message.id);
    if (waiter) { pending.delete(message.id); message.error ? waiter.reject(Error('cdp_failed')) : waiter.resolve(message.result); }
  };
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const next = ++id; pending.set(next, { resolve, reject }); socket.send(JSON.stringify({ id: next, method, params }));
  });
  await call('Page.enable');
  const navigation = await call('Page.navigate', { url });
  if (navigation.errorText) throw Error('navigation_blocked');
  let ready = false;
  for (let i = 0; i < 60; i++) {
    const state = await call('Runtime.evaluate', { expression: 'document.readyState', returnByValue: true });
    if (state.result.value === 'complete') { ready = true; break; }
    await delay(250);
  }
  if (!ready) throw Error('partial');
  await delay(1000);
  const result = await call('Runtime.evaluate', {
    expression: `(() => { const body = document.body?.innerText || ''; const size = new TextEncoder().encode(body).length; return size > 2097152 ? {status:'overflow'} : {status:'ok', body, source_url:location.href, title:document.title, source_kind:'rendered_dom_snapshot'}; })()`,
    returnByValue: true,
  });
  const data = result.result.value;
  if (!data || typeof data !== 'object') throw Error('partial');
  if (data.status === 'ok' && (data.body.length < 80 || /captcha|access denied|verify you are human|ERR_|403 Forbidden|로봇이 아닙니다/i.test(data.body))) {
    console.log(JSON.stringify({status:'blocked', source_url:url}));
  } else console.log(JSON.stringify(data));
} catch (error) {
  console.log(JSON.stringify({status:error.message === 'partial' ? 'partial' : 'blocked', source_url:url}));
} finally {
  socket?.close(); chrome.kill('SIGKILL');
}
