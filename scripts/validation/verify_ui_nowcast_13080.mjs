#!/usr/bin/env node
// VERIFICA BROWSER REALE (Chrome headless via CDP) del nowcast 1.3.0.80.
//
// Server statico locale con JSON MUTABILE: /__shift?min=<eta slot>&step=<min>
// cambia source_slot e i valid_time dei lead. Il test guida l'UI vera:
//   A) Radar Player -> Nowcast: timeline condivisa, step lead, legenda SRI;
//   B) auto-refresh: cambio source_slot -> timeline ricostruita senza reload;
//   C) pausa su +30 -> nuovo slot -> resta +30 con immagine NUOVA (blob);
//   D) freschezza: slot vecchio di 40 min -> «slot HH:MM · 40 min fa»;
//   E) tasto Aggiorna: stato «aggiorno…» -> «slot HH:MM»;
//   F) document.hidden: nessun fetch; al ritorno riprende;
//   G) cambio prodotto: intervallo cancellato, nessun fetch residuo;
//   H) nessun 404 e nessuna query-string sulle richieste nowcast.
//
// USO: node scripts/validation/verify_ui_nowcast_13080.mjs
'use strict';
import { spawn } from 'child_process';
import fs from 'fs';
import http from 'http';
import os from 'os';
import path from 'path';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..'));
const VERSION = fs.readFileSync(path.join(ROOT, 'VERSION'), 'utf8');
const APP_FILE = ((VERSION.match(/^APP_FILE=(.+)$/m) || [])[1] || '').trim();
if (!APP_FILE || !fs.existsSync(path.join(ROOT, APP_FILE))) {
  console.error('FATAL: APP_FILE di VERSION non trovato: ' + JSON.stringify(APP_FILE));
  process.exit(1);
}
const PORT_HTTP = 8767;
const PORT_CDP = 9334;
const URL_APP = 'http://127.0.0.1:' + PORT_HTTP + '/' + APP_FILE;
const CHROME = process.env.CHROME ||
  path.join(process.env.LOCALAPPDATA || '', 'ms-playwright', 'chromium-1243', 'chrome-win64', 'chrome.exe');
if (!fs.existsSync(CHROME)) { console.error('FATAL: Chrome non trovato: ' + CHROME); process.exit(1); }

const NOWCAST_FILE = path.join(ROOT, 'data', 'radar', 'nowcast.json');
const baseNowcast = JSON.parse(fs.readFileSync(NOWCAST_FILE, 'utf8'));
const mockNowcast = JSON.parse(JSON.stringify(baseNowcast));
const shiftState = { min: 0, step: 0 };

const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8', '.json': 'application/json',
  '.webp': 'image/webp', '.png': 'image/png', '.svg': 'image/svg+xml',
  '.css': 'text/css', '.geojson': 'application/json', '.ico': 'image/x-icon'
};
const server = http.createServer((req, res) => {
  let p = decodeURIComponent((req.url || '/').split('?')[0]);
  if (p === '/__shift') {
    const q = new URLSearchParams((req.url || '').split('?')[1] || '');
    const min = parseInt(q.get('min') || '0', 10) || 0;
    const step = parseInt(q.get('step') || '0', 10) || 0;
    shiftState.min = min;            // eta' ASSOLUTA dello slot
    shiftState.step += step;         // i valid_time si spostano in avanti
    const slot = new Date(Date.now() - shiftState.min * 60000).toISOString();
    mockNowcast.source_slot = slot;
    mockNowcast.generated_at = slot;
    mockNowcast.leads = baseNowcast.leads.map(function (l) {
      const ms = new Date(l.valid_time).getTime() + shiftState.step * 60000;
      return Object.assign({}, l, { valid_time: new Date(ms).toISOString() });
    });
    res.writeHead(204); res.end(); return;
  }
  if (p === '/data/radar/nowcast.json') {
    res.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' });
    res.end(JSON.stringify(mockNowcast)); return;
  }
  if (p === '/') p = '/index.html';
  const abs = path.resolve(ROOT, '.' + p);
  if (!abs.startsWith(ROOT + path.sep) || !fs.existsSync(abs) || fs.statSync(abs).isDirectory()) {
    res.writeHead(404, { 'content-type': 'text/plain' }); res.end('404'); return;
  }
  res.writeHead(200, { 'content-type': MIME[path.extname(abs).toLowerCase()] || 'application/octet-stream' });
  fs.createReadStream(abs).pipe(res);
});
await new Promise((r) => server.listen(PORT_HTTP, '127.0.0.1', r));

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'mri-nc-cdp-'));
const chrome = spawn(CHROME, [
  '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--disable-background-networking', '--disable-sync', '--disable-extensions',
  '--remote-debugging-port=' + PORT_CDP,
  '--user-data-dir=' + tmp,
  '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE unpkg.com, EXCLUDE cdn.jsdelivr.net',
  'about:blank'
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(fn, ms, what) {
  const t0 = Date.now();
  let last = null;
  while (Date.now() - t0 < ms) {
    try { const v = await fn(); if (v) return v; } catch (e) { last = e; }
    await sleep(200);
  }
  throw new Error('timeout: ' + what + (last ? ' · ' + last.message : ''));
}

let id = 0;
const pending = new Map();
let ws;
const netNowcast = [];
const exceptions = [];
const consoleErrors = [];
function send(method, params) {
  return new Promise((resolve, reject) => {
    const i = ++id;
    pending.set(i, { resolve, reject });
    ws.send(JSON.stringify({ id: i, method, params: params || {} }));
  });
}
async function evaluate(expression) {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error('JS: ' + ((r.exceptionDetails.exception && r.exceptionDetails.exception.description) || r.exceptionDetails.text || ''));
  return r.result.value;
}
function jsonCount() { return netNowcast.filter((r) => /nowcast\.json$/.test(r.url)).length; }
async function shift(min, step) {
  await fetch('http://127.0.0.1:' + PORT_HTTP + '/__shift?min=' + min + '&step=' + step);
}
const waitPage = (expr, ms, what) => waitFor(() => evaluate(expr), ms, what);

const out = { url: URL_APP, app_file: APP_FILE, chrome: CHROME };
try {
  const list = await waitFor(async () => {
    const res = await fetch('http://127.0.0.1:' + PORT_CDP + '/json/list');
    const arr = await res.json();
    return arr.find((t) => t.type === 'page' && t.webSocketDebuggerUrl) || null;
  }, 30000, 'target CDP');
  ws = new WebSocket(list.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.method === 'Network.responseReceived') {
      const u = (m.params.response && m.params.response.url) || '';
      if (u.indexOf('/data/radar/nowcast') >= 0) {
        netNowcast.push({ url: u.replace('http://127.0.0.1:' + PORT_HTTP, ''), status: m.params.response.status, hasQuery: u.indexOf('?') >= 0 });
      }
    } else if (m.method === 'Runtime.exceptionThrown') {
      exceptions.push((m.params.exceptionDetails && m.params.exceptionDetails.text) || 'exception');
    } else if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      try { consoleErrors.push(m.params.args.map((a) => String(a.value || a.description || '')).join(' ')); } catch (e) { }
    }
    if (!m.id) return;
    const p = pending.get(m.id);
    if (!p) return;
    pending.delete(m.id);
    if (m.error) p.reject(new Error(m.error.message)); else p.resolve(m.result);
  };
  await send('Runtime.enable');
  await send('Page.enable');
  await send('Network.enable');
  await send('Page.navigate', { url: URL_APP });
  await waitFor(async () => (await evaluate('document.readyState')) === 'complete', 30000, 'readyState complete');
  await waitFor(async () => evaluate("typeof map !== 'undefined' && !!map"), 30000, 'map');
  await waitFor(async () => evaluate("typeof nowcastState !== 'undefined' && nowcastState.loaded === true"), 30000, 'nowcast caricato');
  await sleep(500);

  // A) Radar -> Nowcast (timeline condivisa)
  out.A = await evaluate(`(async function () {
    var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
    // Headless: la pagina puo' risultare hidden. La visibilita' viene forzata
    // e poi pilotata dal test (F) per provare il gate document.hidden.
    window.__ncHidden = false;
    Object.defineProperty(document, 'hidden', { configurable: true, get: function () { return !!window.__ncHidden; } });
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: function () { return window.__ncHidden ? 'hidden' : 'visible'; } });
    var sel = document.getElementById('product-select');
    sel.value = 'radar-player'; selectProduct('radar-player'); await sleep(2200);
    var radar = { source: syncTimelineSource, len: syncTimeline.length, max: document.getElementById('sync-slider').max };
    sel.value = 'nowcast'; selectProduct('nowcast'); await sleep(1000);
    var leads = nowcastLeads();
    return {
      app: APP_VERSION, radar: radar,
      source: syncTimelineSource, len: syncTimeline.length, leads: leads.length,
      panelActive: document.getElementById('sync-timeline-panel').classList.contains('active'),
      timer: nowcastState.refreshTimer !== null, timelineActive: !!nowcastState.timelineActive,
      slot: nowcastState.data.source_slot, slotLabel: document.getElementById('nowcast-slot').textContent,
      overlay: !!nowcastState.layer, count: (function(){var n=0;map.eachLayer(function(l){if(l instanceof L.ImageOverlay && String(l.options.className||'').indexOf('nowcast-field')>=0)n++;});return n;})(),
      noDup: document.querySelectorAll('#nowcast-player-wrap').length,
      refreshBtn: (function(){var b=document.getElementById('nowcast-refresh');return b?{text:b.textContent,title:b.getAttribute('title')||'',disabled:b.disabled}:null;})()
    };
  })()`);

  // B) auto-refresh: cambio source_slot -> timeline ricostruita senza reload
  const before = await evaluate('({slot: nowcastState.data.source_slot, ms: syncTimeline[0] && syncTimeline[0].ms})');
  before.n = jsonCount();
  await shift(5, 10);
  await evaluate('nowcastAutoTick(); true');
  await waitPage(`(() => { const s = nowcastState.data.source_slot; return s !== ${JSON.stringify(before.slot)} && syncTimeline[0] && syncTimeline[0].ms !== ${JSON.stringify(before.ms)}; })()`, 15000, 'timeline ricostruita');
  await sleep(500);
  out.B = await evaluate(`(function () {
    return {
      slot: nowcastState.data.source_slot, ms: syncTimeline[0].ms, len: syncTimeline.length,
      slotLabel: document.getElementById('nowcast-slot').textContent,
      overlayUrl: nowcastState.layer && nowcastState.layer._url,
      requests: ${jsonCount()}
    };
  })()`);
  out.B.jsonBefore = before.n;
  out.B.jsonAfter = jsonCount();

  // C) pausa su +30 -> nuovo slot -> resta +30 con immagine nuova (blob)
  out.C = await evaluate(`(function () {
    var leads = nowcastLeads(), idx = -1;
    for (var i = 0; i < leads.length; i++) if (leads[i].lead === 30) { idx = i; break; }
    if (isSyncPlaying) toggleSyncPlay();
    applySyncFrame(idx);
    return { idx: idx, lead: syncTimeline[syncIndex].lead, url: nowcastState.layer && nowcastState.layer._url };
  })()`);
  await shift(10, 10);
  await evaluate('nowcastAutoTick(); true');
  await waitPage(`(() => { const t = nowcastLeads(); let idx=-1; for (let i=0;i<t.length;i++) if (t[i].lead===30) idx=i; return !!nowcastState.layer && nowcastState.layer._url !== ${JSON.stringify(out.C.url)} && syncTimeline[syncIndex].lead === 30; })()`, 15000, 'blob +30');
  await sleep(400);
  out.C.after = await evaluate(`(async function () {
    var url = nowcastState.layer && nowcastState.layer._url;
    var img = await new Promise(function (r) { var im = new Image(); im.onload = function(){r({ok:true,w:im.naturalWidth});}; im.onerror=function(){r({ok:false});}; im.src = url; });
    return { lead: syncTimeline[syncIndex].lead, url: url, blob: /^blob:/.test(String(url)), img: img, slider: document.getElementById('sync-slider').value };
  })()`);

  // D) freschezza > 30 min
  await shift(40, 0);
  await evaluate('nowcastAutoTick(); true');
  await waitPage(`/min fa/.test(document.getElementById('nowcast-slot').textContent)`, 15000, 'etichetta min fa');
  out.D = await evaluate(`({ slotLabel: document.getElementById('nowcast-slot').textContent })`);

  // E) tasto Aggiorna
  await shift(2, 0);
  out.E = await evaluate(`(function () {
    var n0 = ${jsonCount()};
    document.getElementById('nowcast-refresh').click();
    return { refreshing: nowcastState.refreshing, state: document.getElementById('nowcast-slot').textContent, n0: n0 };
  })()`);
  await waitPage('nowcastState.refreshing === false', 15000, 'refresh manuale');
  await sleep(500);
  out.E.after = await evaluate(`({
    slotLabel: document.getElementById('nowcast-slot').textContent,
    btn: (function(){var b=document.getElementById('nowcast-refresh');return {text:b.textContent,disabled:b.disabled};})(),
    n1: ${jsonCount()}
  })`);

  // F) document.hidden: nessun fetch; al ritorno riprende
  out.F = await evaluate(`(function () {
    window.__ncHidden = true;
    Object.defineProperty(document, 'hidden', { configurable: true, get: function () { return !!window.__ncHidden; } });
    nowcastState.lastFetchMs = 0;
    return { start: ${jsonCount()}, hidden: document.hidden };
  })()`);
  await evaluate('nowcastAutoTick(); true');
  await sleep(800);
  out.F.hiddenCount = jsonCount();
  await evaluate('window.__ncHidden = false; nowcastAutoTick(); true');
  await waitPage('nowcastState.refreshing === false', 15000, 'ripresa visibile');
  await sleep(500);
  out.F.visibleCount = jsonCount();

  // G) cambio prodotto: timer cancellato, nessun fetch residuo
  out.G = await evaluate(`(async function () {
    var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
    var sel = document.getElementById('product-select');
    sel.value = 'radar-player'; selectProduct('radar-player'); await sleep(2200);
    return { timer: nowcastState.refreshTimer, timelineActive: !!nowcastState.timelineActive,
             source: syncTimelineSource, len: syncTimeline.length, panelActive: document.getElementById('sync-timeline-panel').classList.contains('active'),
             n0: ${jsonCount()} };
  })()`);
  await evaluate('nowcastState.lastFetchMs = 0; nowcastAutoTick(); true');
  await sleep(800);
  out.G.n1 = jsonCount();
  await evaluate("var s=document.getElementById('product-select'); s.value=''; selectProduct(''); true");
  await sleep(600);
  out.G.timerAfterNone = await evaluate('nowcastState.refreshTimer');
  out.G.activeAfterNone = await evaluate('activeProductId');

  // H) legenda/official/gate (nowcast non attivo: solo funzioni pure/DOM)
  out.H = await evaluate(`(function () {
    function optState() {
      var sel = document.getElementById('product-select'), opt = null, idx = -1;
      for (var i = 0; i < sel.options.length; i++) if (sel.options[i].value === 'nowcast') { opt = sel.options[i]; idx = i; break; }
      var cs = getComputedStyle(opt);
      return { text: opt.textContent, title: opt.getAttribute('title') || '', prev: idx > 0 ? sel.options[idx-1].value : null,
               hidden: !!opt.hidden, disabled: !!opt.disabled, display: cs.display,
               visible: !opt.hidden && !opt.disabled && cs.display !== 'none' };
    }
    var colors = Array.prototype.map.call(document.querySelectorAll('#nowcast-legend-ramp .nowcast-sw'), function (e) { return getComputedStyle(e).backgroundColor; });
    var ticks = Array.prototype.map.call(document.querySelectorAll('#nowcast-legend-ticks span'), function (e) { return e.textContent; });
    var note = (document.getElementById('nowcast-legend').textContent || '');
    var official = { none: nowcastFrameLabel({lead:25,valid:null,official:false}), yes: nowcastFrameLabel({lead:30,valid:null,official:true}) };
    var savedLeads = nowcastState.data.leads;
    var fake = []; for (var q=1;q<=12;q++) fake.push({lead_min:q*5,valid_time:'2026-11-24T12:'+(q*5<10?'0':'')+(q*5)+':00Z',image:'nowcast_lead0'+(q*5<10?'0':'')+(q*5)+'.webp'});
    nowcastState.data.leads = fake; nowcastState.timelineActive = true; buildSyncTimeline();
    var dyn = { n: syncTimeline.length, max: document.getElementById('sync-slider').max, first: syncTimeline[0].lead,
                official: syncTimeline.filter(function(x){return x.official;}).map(function(x){return x.lead;}), label30: nowcastFrameLabel(syncTimeline[5]) };
    nowcastState.data.leads = savedLeads; nowcastState.timelineActive = false; buildSyncTimeline();
    if (typeof toggleSviluppo === 'function') toggleSviluppo(false);
    nowcastState.data.beats_persistence = false; nowcastSyncProductOption(); var gateFalse = optState();
    nowcastState.data.beats_persistence = null; nowcastSyncProductOption(); var gateNull = optState();
    nowcastState.data.available = false; nowcastState.data.beats_persistence = true; nowcastSyncProductOption(); var gateUnav = optState();
    toggleSviluppo(true); nowcastSyncProductOption(); var gateDev = optState();
    toggleSviluppo(false); nowcastState.data.available = true; nowcastState.data.beats_persistence = true; nowcastSyncProductOption();
    return { option: optState(), colors: colors, ticks: ticks, note: note, official: official, dyn: dyn,
             gate: { f: gateFalse, n: gateNull, u: gateUnav, d: gateDev } };
  })()`);
  if (out.H && out.H.note) out.H.note = out.H.note.replace(/\s+/g, ' ').trim();

  const A = out.A, B = out.B, C = out.C, D = out.D, E = out.E, F = out.F, G = out.G, H = out.H;
  const checks = [];
  const ck = (label, cond, detail) => checks.push({ check: label, pass: !!cond, dettaglio: detail });
  const SRI_RGB = ['rgb(0, 85, 136)', 'rgb(0, 163, 224)', 'rgb(136, 221, 238)', 'rgb(255, 238, 0)', 'rgb(255, 170, 0)', 'rgb(193, 0, 0)'];

  ck('APP_VERSION = 1.3.0.80', A.app === '1.3.0.80', A.app);
  ck('nessun doppio player nel DOM', A.noDup === 0, A.noDup);
  ck('Radar Player: 25 slot', A.radar.source === 'radar' && A.radar.len === 25 && A.radar.max === '24', JSON.stringify(A.radar));
  ck('Radar -> Nowcast: pannello attivo, sorgente nowcast, N lead', A.panelActive === true && A.source === 'nowcast' && A.len === A.leads, JSON.stringify({ p: A.panelActive, s: A.source, l: A.len, leads: A.leads }));
  ck('Nowcast attivo: intervallo di auto-refresh attivo (5 min)', A.timer === true && A.timelineActive === true, JSON.stringify({ timer: A.timer, active: A.timelineActive }));
  ck('etichetta slot iniziale «slot HH:MM» con eventuale eta', /^slot \d{2}:\d{2}( · \d+ min fa)?$/.test(A.slotLabel), A.slotLabel);
  ck('tasto Aggiorna presente, con title, non disabilitato', A.refreshBtn && A.refreshBtn.text === 'Aggiorna' && A.refreshBtn.title.length > 10 && A.refreshBtn.disabled === false, JSON.stringify(A.refreshBtn));
  ck('auto-refresh: source_slot cambiato e timeline ricostruita (ms diverso)', B.slot !== A.slot && B.ms !== before.ms && B.len === A.leads, JSON.stringify({ slot: B.slot, ms: B.ms, old: before.ms, len: B.len }));
  ck('auto-refresh: nuova ora dello slot nell\'etichetta', /^slot \d{2}:\d{2}$/.test(B.slotLabel), B.slotLabel);
  ck('auto-refresh: overlay ancora presente e unico', A.count === 1, A.count);
  ck('auto-refresh: richiesta JSON effettuata', B.jsonAfter > B.jsonBefore, B.jsonBefore + ' -> ' + B.jsonAfter);
  ck('pausa su +30: resta +30 dopo il nuovo slot', C.after && C.after.lead === 30, JSON.stringify(C.after));
  ck('pausa su +30: immagine nuova via blob (nessuna query)', C.after && C.after.blob === true && C.after.url !== C.url, JSON.stringify({ url: C.after && C.after.url, old: C.url }));
  ck('pausa su +30: immagine blob caricata', C.after && C.after.img && C.after.img.ok === true && C.after.img.w > 0, JSON.stringify(C.after && C.after.img));
  ck('freschezza: slot vecchio di >30 min dichiarato «· N min fa»', /^slot \d{2}:\d{2} · \d{2} min fa$/.test(D.slotLabel), D.slotLabel);
  ck('Aggiorna: stato «aggiorno…» impostato subito', E.refreshing === true && E.state === 'aggiorno…', JSON.stringify(E));
  ck('Aggiorna: al termine «slot HH:MM», pulsante riabilitato', /^slot \d{2}:\d{2}/.test(E.after.slotLabel) && E.after.btn.text === 'Aggiorna' && E.after.btn.disabled === false, JSON.stringify(E.after));
  ck('Aggiorna: ha fatto una nuova richiesta JSON', E.after.n1 > E.n0, E.n0 + ' -> ' + E.after.n1);
  ck('document.hidden: nessun fetch', F.hiddenCount === F.start, F.start + ' -> ' + F.hiddenCount);
  ck('ritorno visibile: riprende con un fetch', F.visibleCount > F.hiddenCount, F.hiddenCount + ' -> ' + F.visibleCount);
  ck('cambio prodotto: intervallo cancellato e sorgente radar', G.timer === null && G.timelineActive === false && G.source === 'radar' && G.len === 25, JSON.stringify(G));
  ck('cambio prodotto: nessun fetch residuo dal tick', G.n1 === G.n0, G.n0 + ' -> ' + G.n1);
  ck('«nessuno»: nessun timer attivo', G.timerAfterNone === null && G.activeAfterNone === '', JSON.stringify({ t: G.timerAfterNone, a: G.activeAfterNone }));
  ck('option «Nowcast pioggia» con title, dopo Radar Player', H.option.text === 'Nowcast pioggia' && H.option.title.length > 10 && H.option.prev === 'radar-player', JSON.stringify(H.option));
  ck('legenda: 6 valori SRI 1/5/10/20/50/100+', H.ticks.join('|') === '1|5|10|20|50|100+', JSON.stringify(H.ticks));
  ck('legenda: 6 RGB identici a DPC_SRI_STOPS', H.colors.length === 6 && H.colors.every((c, i) => c === SRI_RGB[i]), JSON.stringify(H.colors));
  ck('legenda: note (affidabilita + sotto 1 mm/h)', H.note.indexOf("dopo 30 minuti l'affidabilità cala") >= 0 && /sotto 1 mm\/h/.test(H.note), '');
  ck('official: etichetta su +30 e non su +25', H.official.yes.indexOf('ufficiale') >= 0 && H.official.none.indexOf('ufficiale') < 0, JSON.stringify(H.official));
  ck('lista dinamica: 12 lead, slider max 11, official 15/30/60', H.dyn.n === 12 && H.dyn.max === '11' && H.dyn.first === 5 && H.dyn.official.join(',') === '15,30,60', JSON.stringify(H.dyn));
  ck('gate: beats false -> nascosta; null -> visibile; unavailable -> nascosta; dev ON -> visibile',
    H.gate.f.visible === false && H.gate.n.visible === true && H.gate.u.visible === false && H.gate.d.visible === true,
    JSON.stringify(H.gate));

  const badNet = netNowcast.filter((r) => r.status !== 200);
  const queryNet = netNowcast.filter((r) => r.hasQuery);
  out.net = netNowcast;
  ck('rete: nessun 404/errore sulle richieste nowcast', badNet.length === 0, JSON.stringify(badNet));
  ck('rete: nessuna query-string (cache-buster) sulle richieste nowcast', queryNet.length === 0, JSON.stringify(queryNet));
  const ncErrors = consoleErrors.filter((t) => /nowcast|NOWCAST|sync-nowcast/i.test(t));
  out.exceptions = exceptions;
  out.console_errors_nowcast = ncErrors;
  ck('nessuna eccezione JS non gestita', exceptions.length === 0, JSON.stringify(exceptions));
  ck('nessun errore console sul nowcast', ncErrors.length === 0, JSON.stringify(ncErrors));

  out.checks = checks;
  const fails = checks.filter((c) => !c.pass);
  out.risultato = fails.length ? (fails.length + ' FAIL') : 'PASS';
  console.log(JSON.stringify(out, null, 2));
  process.exitCode = fails.length ? 1 : 0;
} finally {
  try { if (ws) ws.close(); } catch (e) { }
  try { chrome.kill(); } catch (e) { }
  try { server.close(); } catch (e) { }
  setTimeout(() => { try { fs.rmSync(tmp, { recursive: true, force: true }); } catch (e) { } }, 500);
}
