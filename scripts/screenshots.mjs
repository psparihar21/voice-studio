// Regenerate the README images from a running studio (use the demo project from make_demo.py):
//   python scripts/make_demo.py <demo>  &&  python -m voice_studio --data <demo> --port 8793
//   node scripts/screenshots.mjs http://localhost:8793 docs/images
// Drives headless Chrome over the DevTools protocol with Chrome's built-in fake microphone. Needs Node 22+ and ffmpeg.
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [url = 'http://localhost:8793', out = 'docs/images'] = process.argv.slice(2);
const CHROME = [process.env.CHROME, 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome'].find(p => p && existsSync(p));
if (!CHROME) throw new Error('Chrome not found; set CHROME to its path');
mkdirSync(out, { recursive: true });
const profile = mkdtempSync(join(tmpdir(), 'vs-shots-'));
const PORT = 9333;
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, '--hide-scrollbars',
  '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', '--autoplay-policy=no-user-gesture-required',
  '--force-color-profile=srgb', 'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));

let target;
for (let i = 0; i < 50 && !target; i++) {
  try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find(t => t.type === 'page'); } catch { await sleep(200); }
}
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r, { once: true }));
let seq = 0; const waiting = new Map();
ws.addEventListener('message', e => { const m = JSON.parse(e.data); if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); } });
const cdp = (method, params = {}) => new Promise((res, rej) => {
  const id = ++seq; waiting.set(id, m => m.error ? rej(new Error(method + ': ' + m.error.message)) : res(m.result));
  ws.send(JSON.stringify({ id, method, params }));
});
const js = async (expr) => (await cdp('Runtime.evaluate', { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true })).result.value;
const size = (width, height, scale = 2, mobile = false) => cdp('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: scale, mobile });
async function open() {
  await cdp('Page.navigate', { url });
  for (let i = 0; i < 50; i++) { if (await js(`return document.readyState === 'complete' && document.getElementById('pv').readyState >= 2`)) break; await sleep(200); }
  await sleep(1200);
  // Chrome's fake device has a debug name; show what a real USB mic looks like
  await js(`const $ = id => document.getElementById(id);
            if ($('micSel').options.length){ $('micTxt').textContent = 'USB Microphone'; [...$('micSel').options].forEach(o => o.textContent = 'USB Microphone'); $('ck2').className = 'ok'; }
            return true`);
}
// every run records one take for the hero shot; remove it again so the takes list stays the demo's
async function dropNewTakes() {
  await js(`for (let i = 0; i < 120; i++){
              const s = await (await fetch('/api/state')).json(), extra = (s.takes.full || []).filter(k => k.n > 3);
              if (!extra.length) return true;
              for (const k of extra) await fetch('/api/delete?file=' + k.file, {method: 'POST', headers: {'X-Voice-Studio': '1'}});
              await new Promise(r => setTimeout(r, 1000));
            }
            await fetch('/api/select?cue=full&n=3', {method: 'POST', headers: {'X-Voice-Studio': '1'}}); return true`);
  await js(`await fetch('/api/select?cue=full&n=3', {method: 'POST', headers: {'X-Voice-Studio': '1'}}); return true`);
}
async function shot(name, clipSel, scale = 2) {
  const p = {};
  if (clipSel) p.clip = { ...(await js(`const b = document.querySelector('${clipSel}').getBoundingClientRect(); return {x: b.x - 8, y: b.y - 8, width: b.width + 16, height: b.height + 16};`)), scale: 1 };
  const { data } = await cdp('Page.captureScreenshot', { format: 'png', ...p });
  writeFileSync(join(out, name), Buffer.from(data, 'base64'));
  console.log('wrote', join(out, name), scale);
}

try {
  await cdp('Page.enable'); await cdp('Runtime.enable');
  await cdp('Browser.grantPermissions', { origin: new URL(url).origin, permissions: ['audioCapture'] }).catch(() => {});

  // 1. overview: everything at rest, mic on and tested
  await size(1440, 900);
  await open();
  await dropNewTakes();
  await js(`localStorage.clear(); return true`);
  await open();
  await js(`const $ = id => document.getElementById(id);
            $('micBtn').click(); await new Promise(r => setTimeout(r, 1200));
            $('testBtn').click(); await new Promise(r => setTimeout(r, 4400));
            $('micTxt').textContent = 'USB Microphone'; [...$('micSel').options].forEach(o => o.textContent = 'USB Microphone');
            $('ck2').className = 'ok'; $('feedback').innerHTML = '';
            $('pv').currentTime = 20; await new Promise(r => setTimeout(r, 600)); return true`);
  await shot('overview.png');
  await size(1440, 1500);  // tall enough that the takes card is not cut by the scrolling side column
  await sleep(500);
  await shot('takes.png', '.side .card:last-of-type');
  await size(1440, 900);
  await sleep(500);

  // 2. recording: countdown to the next part, live badge, script panel following the picture
  await js(`const $ = id => document.getElementById(id);
            $('countIn').checked = false; $('followVid').checked = true;
            $('myScriptBtn').click(); await new Promise(r => setTimeout(r, 600));
            const p = $('scriptPanel'); Object.assign(p.style, {left: '596px', top: '398px', width: '410px', height: '200px'});
            $('recBtn').click(); await new Promise(r => setTimeout(r, 6300)); return true`);
  await shot('recording.png');
  await js(`document.getElementById('recBtn').click(); await new Promise(r => setTimeout(r, 1500)); document.getElementById('scriptClose').click(); return true`);
  await dropNewTakes();

  // 3. resizing the picture: frames for a GIF
  await size(1360, 850, 1);
  await open();
  const frames = mkdtempSync(join(tmpdir(), 'vs-frames-'));
  await js(`window.__st = document.querySelector('.stage'); window.__sh = document.querySelector('.shell');
            document.getElementById('pv').currentTime = 20; await new Promise(r => setTimeout(r, 600)); return true`);
  const base = await js(`return __st.offsetHeight`);
  const ease = t => t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
  const plan = [];
  const go = (kind, from, to, n) => { for (let i = 0; i <= n; i++) plan.push([kind, from + (to - from) * ease(i / n)]); };
  const hold = (kind, v, n) => { for (let i = 0; i < n; i++) plan.push([kind, v]); };
  hold('h', base, 6); go('h', base, 250, 14); hold('h', 250, 5); go('h', 250, 640, 18); hold('h', 640, 6); go('h', 640, base, 12); hold('h', base, 4);
  go('w', 390, 290, 10); hold('w', 290, 5); go('w', 290, 520, 14); hold('w', 520, 5); go('w', 520, 390, 10); hold('w', 390, 6);
  let n = 0;
  for (const [kind, v] of plan) {
    await js(kind === 'h'
      ? `__st.style.height = '${Math.round(v)}px'; __st.classList.add('sized'); document.getElementById('stageGrip').classList.add('drag'); document.getElementById('sideGrip').classList.remove('drag'); return true`
      : `__st.style.height = ''; __st.classList.remove('sized'); __sh.style.setProperty('--side-w', '${Math.round(v)}px'); document.getElementById('sideGrip').classList.add('drag'); document.getElementById('stageGrip').classList.remove('drag'); return true`);
    const { data } = await cdp('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(frames, `f${String(n++).padStart(3, '0')}.png`), Buffer.from(data, 'base64'));
  }
  execFileSync('ffmpeg', ['-v', 'error', '-y', '-framerate', '12', '-i', join(frames, 'f%03d.png'), '-vf',
    'scale=900:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=160:stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a',
    join(out, 'resize.gif')]);
  console.log('wrote', join(out, 'resize.gif'), n, 'frames');
  rmSync(frames, { recursive: true, force: true });

  // 4. phone
  await size(390, 844, 2, true);
  await open();
  await js(`localStorage.clear(); return true`);
  await open();
  await js(`document.getElementById('pv').currentTime = 20; await new Promise(r => setTimeout(r, 600)); return true`);
  await shot('phone.png');
} finally {
  ws.close(); chrome.kill();
  await sleep(500); rmSync(profile, { recursive: true, force: true, maxRetries: 5 });
}
