#!/usr/bin/env node
// VERIFICA BROWSER REALE (Chrome headless via CDP) del PRODOTTO nowcast pioggia 1.3.0.77.
//
// Apre l'APP_FILE di VERSION da un server statico locale (stessa origine dei
// dati: data/radar/nowcast.json e i WebP), con la rete esterna bloccata tranne
// unpkg/jsdelivr (Leaflet) e 127.0.0.1. Guida l'UI vera:
//   A) la voce «Nowcast pioggia» e' nella tendina, subito dopo «Radar Player»;
//   B) selezionandola compaiono overlay (+ primo frame), player e legenda;
//   C) play -> animazione a ~1 frame/700 ms; pausa; slider -> frame scelto;
//   D) la lista frame viene da leads[] (nessun 3/12 hardcodato);
//   E) tornando a «Radar Player» il nowcast sparisce e il radar si accende;
//   F) «— nessuno —» spegne tutto, nessun layer orfano;
//   G) gate: available:false o beats_persistence:false -> voce nascosta salvo
//      Sviluppo ON (null passa);
//   H) URL nowcast senza query-string e tutte le risposte 200; 0 eccezioni JS.
//
// USO: node scripts/validation/verify_ui_nowcast_13077.mjs [url]
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
const URL_APP = process.argv[2] || ('http://127.0.0.1:' + PORT_HTTP + '/' + APP_FILE);
const CHROME = process.env.CHROME ||
  path.join(process.env.LOCALAPPDATA || '', 'ms-playwright', 'chromium-1243', 'chrome-win64', 'chrome.exe');
if (!fs.existsSync(CHROME)) {
  console.error('FATAL: Chrome non trovato: ' + CHROME);
  process.exit(1);
}

const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8', '.json': 'application/json',
  '.webp': 'image/webp', '.png': 'image/png', '.svg': 'image/svg+xml',
  '.css': 'text/css', '.geojson': 'application/json', '.ico': 'image/x-icon'
};
const server = http.createServer((req, res) => {
  let p = decodeURIComponent((req.url || '/').split('?')[0]);
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
  await waitFor(async () => evaluate("typeof map !== 'undefined' && !!map"), 30000, 'map inizializzata');
  await waitFor(async () => evaluate("typeof nowcastState !== 'undefined' && nowcastState.loaded === true"), 30000, 'nowcastState.loaded');
  await sleep(600);

  out.dom = await evaluate(`(async function () {
    var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
    function disp(id) { var e = document.getElementById(id); return e ? getComputedStyle(e).display : null; }
    function countNowcast() {
      var n = 0, urls = '';
      map.eachLayer(function (l) {
        if (l instanceof L.ImageOverlay && String(l.options.className || '').indexOf('nowcast-field') >= 0) { n++; urls += (l._url || '') + ';'; }
      });
      return { n: n, urls: urls };
    }
    function optionState() {
      var sel = document.getElementById('product-select');
      var opt = null, idx = -1, i;
      for (i = 0; i < sel.options.length; i++) if (sel.options[i].value === 'nowcast') { opt = sel.options[i]; idx = i; break; }
      if (!opt) return null;
      var cs = getComputedStyle(opt);
      return { text: opt.textContent, title: opt.getAttribute('title') || '', index: idx,
               prev: idx > 0 ? sel.options[idx - 1].value : null,
               hidden: !!opt.hidden, disabled: !!opt.disabled, display: cs.display,
               offsetH: opt.offsetHeight, visible: !opt.hidden && !opt.disabled && cs.display !== 'none' };
    }
    var res = {};
    res.app_version = (typeof APP_VERSION !== 'undefined') ? APP_VERSION : null;
    res.data = { available: nowcastState.data && nowcastState.data.available,
                 beats: nowcastState.data && nowcastState.data.beats_persistence,
                 leads: (nowcastState.data && nowcastState.data.leads ? nowcastState.data.leads.length : 0) };
    res.leads = nowcastLeads().map(function (x) { return { lead: x.lead, image: x.image, valid: x.valid }; });
    res.option_initial = optionState();
    res.initial = { active: activeProductId, panel: disp('nowcast-panel'), count: countNowcast() };

    // B) seleziona il prodotto nowcast
    var sel = document.getElementById('product-select');
    sel.value = 'nowcast'; selectProduct('nowcast');
    await sleep(1200);
    var leads = nowcastLeads();
    var l1 = nowcastState.layer;
    var firstUrl = l1 && l1._url;
    res.on = {
      active: activeProductId, layer: !!l1, hasLayer: !!(l1 && map.hasLayer(l1)),
      url: l1 && l1._url, opacity: l1 && l1.options.opacity, pane: l1 && l1.options.pane,
      paneZ: map.getPane('nowcastPane') ? getComputedStyle(map.getPane('nowcastPane')).zIndex : null,
      panel: disp('nowcast-panel'), legend: disp('nowcast-legend'), player: disp('nowcast-player-wrap'),
      slider: (function () { var s = document.getElementById('nowcast-slider'); return s ? { min: s.min, max: s.max, value: s.value, disabled: s.disabled } : null; })(),
      label: (document.getElementById('nowcast-frame-label') || {}).textContent,
      expectedFirst: leads.length ? ('+' + leads[0].lead + ' min') : null,
      count: countNowcast(),
      preloaded: nowcastState.preloaded.length,
      mapCard: (function () { var m = document.querySelector('.map-card'); return m ? getComputedStyle(m).display : null; })(),
      others: { radarPlayer: !!isRadarPlayerActive, satellite: !!isSatelliteActive,
                live: !!isLivePanelActive, supercells: !!liveShowSupercells },
      img_load: await new Promise(function (r) {
        var im = new Image(); im.onload = function () { r({ ok: true, w: im.naturalWidth, h: im.naturalHeight }); };
        im.onerror = function () { r({ ok: false }); }; im.src = firstUrl;
      })
    };

    // C) play -> animazione
    var startFrame = nowcastState.frame, startUrl = nowcastState.layer && nowcastState.layer._url;
    var btn = document.getElementById('nowcast-play');
    btn.click();
    var playing = !!nowcastState.timer;
    await sleep(1800);
    var midFrame = nowcastState.frame, midUrl = nowcastState.layer && nowcastState.layer._url;
    btn.click();  // pausa
    var paused = !nowcastState.timer;
    await sleep(900);
    var afterPauseFrame = nowcastState.frame;
    res.play = { playing: playing, startFrame: startFrame, midFrame: midFrame, advanced: midFrame !== startFrame,
                 urlChanged: midUrl !== startUrl, midUrl: midUrl, paused: paused,
                 frozen: afterPauseFrame === midFrame, btnText: btn.textContent };

    // D) slider -> ultimo frame
    var s = document.getElementById('nowcast-slider');
    var last = leads.length - 1;
    s.value = String(last); nowcastSliderInput(s.value);
    await sleep(400);
    var lastUrl = nowcastState.layer && nowcastState.layer._url;
    res.slider = { last: last, frame: nowcastState.frame,
                   url: lastUrl,
                   expectedUrl: leads.length ? ('data/radar/' + leads[last].image) : null,
                   label: (document.getElementById('nowcast-frame-label') || {}).textContent,
                   expectedLabel: leads.length ? ('+' + leads[last].lead + ' min') : null,
                   count: countNowcast(),
                   img_load: await new Promise(function (r) {
                     var im = new Image(); im.onload = function () { r({ ok: true }); };
                     im.onerror = function () { r({ ok: false }); }; im.src = lastUrl;
                   }) };

    // D2) lista dinamica: 12 lead sintetici (nessun 3/12 hardcodato)
    var savedLeads = nowcastState.data.leads;
    var fake = []; for (var q = 1; q <= 12; q++) fake.push({ lead_min: q * 5, valid_time: '2026-11-24T12:' + (q * 5 < 10 ? '0' : '') + (q * 5) + ':00Z', image: 'nowcast_lead0' + (q * 5 < 10 ? '0' : '') + (q * 5) + '.webp' });
    nowcastState.data.leads = fake;
    nowcastUpdateSlider(); nowcastUpdateLabel();
    var s2 = document.getElementById('nowcast-slider');
    res.dynamic = { n: nowcastLeads().length, max: s2.max, first: nowcastFrameLabel(nowcastLeads()[0]),
                    leads: nowcastLeads().map(function (x) { return x.lead; }) };
    nowcastState.data.leads = savedLeads;
    nowcastUpdateSlider();

    // E) torna a Radar Player
    sel.value = 'radar-player'; selectProduct('radar-player');
    await sleep(3000);
    res.toRadar = { active: activeProductId, radar: !!isRadarPlayerActive,
                    nowcastOn: !!nowcastState.on, layer: !!nowcastState.layer,
                    count: countNowcast(), panel: disp('nowcast-panel'), timer: !!nowcastState.timer };

    // F) «— nessuno —»
    sel.value = ''; selectProduct('');
    await sleep(2000);
    res.none = { active: activeProductId, count: countNowcast(), panel: disp('nowcast-panel'),
                 layer: !!nowcastState.layer, timer: !!nowcastState.timer };

    // G) gate: Sviluppo OFF + beats false/null, available false
    if (typeof toggleSviluppo === 'function') toggleSviluppo(false);
    nowcastState.data.beats_persistence = false; nowcastSyncProductOption();
    res.gate_false_devoff = optionState();
    nowcastState.data.beats_persistence = null; nowcastSyncProductOption();
    res.gate_null_devoff = optionState();
    nowcastState.data.available = false; nowcastState.data.beats_persistence = true; nowcastSyncProductOption();
    res.gate_unavail_devoff = optionState();
    toggleSviluppo(true); nowcastSyncProductOption();
    res.gate_dev_on = optionState();
    toggleSviluppo(false);
    nowcastState.data.available = true; nowcastState.data.beats_persistence = true; nowcastSyncProductOption();
    res.gate_restored = optionState();

    return res;
  })()`);

  const d = out.dom;
  const checks = [];
  const ck = (label, cond, detail) => checks.push({ check: label, pass: !!cond, dettaglio: detail });
  ck('APP_VERSION = 1.3.0.77', d.app_version === '1.3.0.77', d.app_version);
  ck('dati live: available true e beats_persistence true', d.data.available === true && d.data.beats === true, JSON.stringify(d.data));
  ck('option «Nowcast pioggia» presente, con title, subito dopo «Radar Player»',
    d.option_initial && d.option_initial.text === 'Nowcast pioggia' && d.option_initial.title.length > 10 &&
    d.option_initial.prev === 'radar-player', JSON.stringify(d.option_initial));
  ck('option visibile con dati validi e Sviluppo OFF', d.option_initial && d.option_initial.visible === true, JSON.stringify(d.option_initial));
  ck('all\'avvio nowcast spento (nessun overlay, pannello nascosto)', d.initial.active !== 'nowcast' && d.initial.count.n === 0 && d.initial.panel === 'none', JSON.stringify(d.initial));
  ck('selezione prodotto: activeProductId = nowcast', d.on.active === 'nowcast', d.on.active);
  ck('selezione prodotto: overlay presente e in mappa', d.on.layer === true && d.on.hasLayer === true, JSON.stringify({ layer: d.on.layer, has: d.on.hasLayer }));
  ck('ON: primo frame visibile (lead_min minimo)', d.on.url && /nowcast_lead0\d\d\.webp$/.test(d.on.url) && d.on.label.indexOf(d.on.expectedFirst) === 0, d.on.url + ' / ' + d.on.label + ' atteso ' + d.on.expectedFirst);
  ck('ON: un solo overlay nowcast', d.on.count.n === 1, JSON.stringify(d.on.count));
  ck('ON: pane nowcastPane z 407, opacita 0.75', d.on.pane === 'nowcastPane' && d.on.paneZ === '407' && d.on.opacity === 0.75, d.on.pane + '/' + d.on.paneZ + '/' + d.on.opacity);
  ck('ON: pannello, legenda e player visibili', d.on.panel === 'block' && d.on.legend !== 'none' && d.on.player !== 'none', d.on.panel + '/' + d.on.legend + '/' + d.on.player);
  ck('ON: slider costruito sui lead reali (max = n-1)', d.on.slider && d.on.slider.min === '0' && parseInt(d.on.slider.max, 10) === d.leads.length - 1, JSON.stringify(d.on.slider));
  ck('ON: precaricati tutti i frame', d.on.preloaded === d.leads.length, d.on.preloaded + ' vs ' + d.leads.length);
  ck('ON: immagine del primo frame caricata', d.on.img_load && d.on.img_load.ok === true && d.on.img_load.w > 0, JSON.stringify(d.on.img_load));
  ck('ON: la mappa resta visibile (.map-card non none)', d.on.mapCard !== 'none', d.on.mapCard);
  ck('ON: gli altri layer interferenti sono spenti', d.on.others.radarPlayer === false && d.on.others.satellite === false && d.on.others.live === false && d.on.others.supercells === false, JSON.stringify(d.on.others));
  ck('play: timer attivo e frame avanzato', d.play.playing === true && d.play.advanced === true, JSON.stringify(d.play));
  ck('play: URL dell\'overlay cambiato (stesso layer)', d.play.urlChanged === true, d.play.midUrl);
  ck('pausa: timer fermo e frame congelato', d.play.paused === true && d.play.frozen === true, JSON.stringify(d.play));
  ck('slider: ultimo frame, URL e label coerenti', d.slider.frame === d.slider.last && d.slider.url === d.slider.expectedUrl && d.slider.label.indexOf(d.slider.expectedLabel) === 0, JSON.stringify(d.slider));
  ck('slider: immagine dell\'ultimo frame caricata', d.slider.img_load && d.slider.img_load.ok === true, JSON.stringify(d.slider.img_load));
  ck('slider: sempre un solo overlay (setUrl, nessun accumulo)', d.slider.count.n === 1, JSON.stringify(d.slider.count));
  ck('lista dinamica: 12 lead sintetici -> slider max 11, primo +5', d.dynamic.n === 12 && d.dynamic.max === '11' && d.dynamic.first.indexOf('+5 min') === 0, JSON.stringify(d.dynamic));
  ck('ritorno a Radar Player: nowcast spento e radar acceso', d.toRadar.active === 'radar-player' && d.toRadar.radar === true && d.toRadar.nowcastOn === false && d.toRadar.layer === false && d.toRadar.count.n === 0 && d.toRadar.panel === 'none', JSON.stringify(d.toRadar));
  ck('«nessuno»: tutto spento, nessun layer orfano', d.none.active === '' && d.none.count.n === 0 && d.none.layer === false && d.none.panel === 'none' && d.none.timer === false, JSON.stringify(d.none));
  ck('gate: dev OFF + beats false -> option nascosta', d.gate_false_devoff && d.gate_false_devoff.visible === false && d.gate_false_devoff.hidden === true && d.gate_false_devoff.disabled === true, JSON.stringify(d.gate_false_devoff));
  ck('gate: beats null vale ok (non nascosta)', d.gate_null_devoff && d.gate_null_devoff.visible === true, JSON.stringify(d.gate_null_devoff));
  ck('gate: dev OFF + available false -> option nascosta', d.gate_unavail_devoff && d.gate_unavail_devoff.visible === false, JSON.stringify(d.gate_unavail_devoff));
  ck('gate: Sviluppo ON -> option visibile anche con gate chiuso', d.gate_dev_on && d.gate_dev_on.visible === true, JSON.stringify(d.gate_dev_on));
  ck('gate: dati ripristinati -> option di nuovo visibile', d.gate_restored && d.gate_restored.visible === true, JSON.stringify(d.gate_restored));

  out.net = netNowcast;
  const nq = netNowcast.filter((r) => r.hasQuery);
  const bad = netNowcast.filter((r) => r.status !== 200);
  ck('rete: richieste nowcast senza query-string', nq.length === 0, JSON.stringify(nq));
  ck('rete: tutte le risposte nowcast 200', bad.length === 0, JSON.stringify(bad));
  ck('rete: JSON + almeno un WebP richiesti', netNowcast.length >= 2, JSON.stringify(netNowcast.map((r) => r.url)));

  const ncErrors = consoleErrors.filter((t) => /nowcast|NOWCAST|toggleNowcast|nowcastSlider/i.test(t));
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
