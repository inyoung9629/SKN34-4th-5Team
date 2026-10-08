// Actual Chromium CDP download, offline; mirrors upstream's tmpdir download path.
import { spawn } from 'node:child_process';
import { mkdtempSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import assert from 'node:assert/strict';
const directory = mkdtempSync(join(tmpdir(), 'jev-downloads-'));
assert.ok(directory.startsWith(process.env.JEV_PROFILE + '/tmp/'));
const chrome = spawn('/usr/bin/chromium', ['--headless=new', '--disable-dev-shm-usage', '--remote-debugging-port=9229', '--user-data-dir='+process.env.JEV_PROFILE, 'about:blank'], {stdio:'ignore'});
let targets;
for (let attempt=0; attempt<100; attempt++) {
  try { targets=await (await fetch('http://127.0.0.1:9229/json/list')).json(); break; } catch { await new Promise(r=>setTimeout(r,50)); }
}
const ws = new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);
await new Promise(r=>ws.addEventListener('open',r,{once:true}));
let id=0;
const pending=new Map();
ws.addEventListener('message',event=>{const m=JSON.parse(event.data); if(m.id){pending.get(m.id)?.(m);pending.delete(m.id);}});
async function send(method,params={}) { const key=++id; const response=new Promise(r=>pending.set(key,r));ws.send(JSON.stringify({id:key,method,params}));const result=await response;assert.ok(!result.error,JSON.stringify(result.error));return result; }
await send('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:directory});
await send('Runtime.evaluate',{expression:`(()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['download-sentinel'],{type:'text/plain'}));a.download='sentinel.txt';document.body.append(a);a.click()})()`});
const sentinel=join(directory,'sentinel.txt');
for(let attempt=0;attempt<100&&!existsSync(sentinel);attempt++)await new Promise(r=>setTimeout(r,50));
assert.equal(readFileSync(sentinel,'utf8'),'download-sentinel');
writeFileSync(process.env.SENTINEL_RECEIPT, sentinel);
if(process.env.SENTINEL_MODE==='success') { await send('Browser.close'); ws.close(); console.log('{"status":"done"}'); }
else await new Promise(()=>{});
