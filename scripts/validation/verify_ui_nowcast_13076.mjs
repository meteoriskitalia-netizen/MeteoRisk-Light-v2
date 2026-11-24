#!/usr/bin/env node
// VERIFICA BROWSER REALE (Chrome headless via CDP) del toggle NOWCAST PIOGGIA 1.3.0.76.
//
// Apre l'APP_FILE di VERSION da un server statico locale (stessa origine dei
// dati: data/radar/nowcast.json e i tre WebP), con la rete esterna bloccata
// tranne unpkg/jsdelivr (Leaflet) e 127.0.0.1. Guida l'UI vera:
//   A) toggle OFF di default, selettore lead nascosto;
//   B) ON -> imageOverlay nel pane nowcastPane z 407, opacita 0.75, URL +30;
//   C) cambio lead 30 -> 60: SOSTITUZIONE (un solo overlay, URL cambiato);
//   D) OFF -> overlay, legenda e selettore spariscono;
//   E) gate: dev OFF + beats_persistence false/null -> toggle nascosto;
//      dev ON -> visibile; available false -> nascosto con dev OFF;
//   F) cambio prodotto fuori da radar/LIVE -> layer rimosso.
// In piu': ogni risposta data/radar/nowcast* deve essere 200 e SENZA query-string,
// e non deve esserci nessuna eccezione JS.
//
// USO: node scripts/validation/verify_ui_nowcast_13076.mjs [url]
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
  // Solo il locale e le due CDN di Leaflet: il resto non risolve.
  '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE unpkg.com, EXCLUDE cdn.jsdelivr.net',
  'about:blank'
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(fn, ms, what) {
  const t0 = Date.now();
  let last = null;
  while (Date.now() - t0 < ms) {
    try { const v = await fn(); if (v) return v; } catch (e) { last = e; }
    await sleep(250);
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
  if (r.exceptionDetails) throw new Error('JS: ' + (r.exceptionDetails.exception && r.exceptionDetails.exception.description || r.exceptionDetails.text || ''));
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
  // `map` e `nowcastState` sono binding lessicali dello script classico: NON sono
  // proprieta' di window, quindi si leggono per nome (window.map e' undefined).
  await waitFor(async () => evaluate("typeof map !== 'undefined' && !!map"), 30000, 'map inizializzata');
  await waitFor(async () => evaluate("typeof nowcastState !== 'undefined' && !!nowcastState"), 30000, 'nowcastState definito');
  await waitFor(async () => evaluate("typeof nowcastState !== 'undefined' && nowcastState.loaded === true"), 30000, 'nowcastState.loaded');
  await sleep(800);

  out.dom = await evaluate(`(async function () {
    var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
    function disp(id) { var e = document.getElementById(id); return e ? getComputedStyle(e).display : null; }
    function countNowcast() {
      var n = 0, cls = '';
      map.eachLayer(function (l) {
        if (l instanceof L.ImageOverlay && String(l.options.className || '').indexOf('nowcast-field') >= 0) { n++; cls += (l._url || '') + ';'; }
      });
      return { n: n, urls: cls };
    }
    var wrap = document.getElementById('nowcast-toggle-wrap');
    var cb = document.getElementById('nowcast-toggle');
    var lead = document.getElementById('nowcast-lead');
    var leadWrap = document.getElementById('nowcast-lead-wrap');
    var res = {};
    res.app_version = (typeof APP_VERSION !== 'undefined') ? APP_VERSION : null;
    res.data_available = nowcastState.data && nowcastState.data.available;
    res.data_beats = nowcastState.data && nowcastState.data.beats_persistence;
    res.initial = { wrap: getComputedStyle(wrap).display, lead: getComputedStyle(leadWrap).display, checked: cb.checked, legend: disp('nowcast-legend') };

    // A) ON
    cb.checked = true; toggleNowcastField(true); await sleep(700);
    var l1 = nowcastState.layer;
    res.on = {
      layer: !!l1, hasLayer: !!(l1 && map.hasLayer(l1)),
      url: l1 && l1._url, opacity: l1 && l1.options.opacity, pane: l1 && l1.options.pane,
      paneZ: map.getPane('nowcastPane') ? getComputedStyle(map.getPane('nowcastPane')).zIndex : null,
      paneEvents: map.getPane('nowcastPane') ? getComputedStyle(map.getPane('nowcastPane')).pointerEvents : null,
      legend: disp('nowcast-legend'), lead: getComputedStyle(leadWrap).display,
      count: countNowcast(),
      bounds: l1 ? { sw: l1.getBounds().getSouthWest(), ne: l1.getBounds().getNorthEast() } : null
    };
    res.img_load = await new Promise(function (r) {
      var im = new Image(); im.onload = function () { r({ ok: true, w: im.naturalWidth, h: im.naturalHeight }); };
      im.onerror = function () { r({ ok: false }); }; im.src = res.on.url;
    });

    // B) cambio lead 30 -> 60
    lead.value = '60'; setNowcastLead(60); await sleep(500);
    var l2 = nowcastState.layer;
    res.lead60 = { url: l2 && l2._url, same_ref: l2 === l1, count: countNowcast(), old_on_map: !!(l1 && map.hasLayer(l1)) };

    // C) OFF
    cb.checked = false; toggleNowcastField(false); await sleep(300);
    res.off = { layer: !!nowcastState.layer, legend: disp('nowcast-legend'), lead: getComputedStyle(leadWrap).display, count: countNowcast() };

    // D) gate
    if (typeof toggleSviluppo === 'function') toggleSviluppo(false);
    nowcastSyncVisibility(); await sleep(50);
    res.gate_public = getComputedStyle(wrap).display;
    nowcastState.data.beats_persistence = false; nowcastSyncVisibility(); await sleep(50);
    res.gate_beats_false_devoff = getComputedStyle(wrap).display;
    nowcastState.data.beats_persistence = null; nowcastSyncVisibility(); await sleep(50);
    res.gate_beats_null_devoff = getComputedStyle(wrap).display;
    nowcastState.data.available = false; nowcastState.data.beats_persistence = true; nowcastSyncVisibility(); await sleep(50);
    res.gate_unavailable_devoff = getComputedStyle(wrap).display;
    toggleSviluppo(true); await sleep(50);
    res.gate_dev_on = getComputedStyle(wrap).display;
    toggleSviluppo(false); await sleep(50);
    nowcastState.data.available = true; nowcastState.data.beats_persistence = true; nowcastSyncVisibility(); await sleep(50);
    res.gate_restored = getComputedStyle(wrap).display;

    // E) cambio prodotto fuori da radar/LIVE
    cb.checked = true; toggleNowcastField(true); await sleep(400);
    res.product = { before_layer: !!nowcastState.layer, before_count: countNowcast() };
    activeProductId = 'rischi'; syncProductUI(); await sleep(200);
    res.product.after_layer = !!nowcastState.layer;
    res.product.after_count = countNowcast();
    res.product.after_checked = cb.checked;
    activeProductId = ''; syncProductUI(); await sleep(100);

    // F) nessun accumulo e rimozione finale
    nowcastShow(false); await sleep(100);
    res.final_count = countNowcast();
    return res;
  })()`);

  const d = out.dom;
  const checks = [];
  const ck = (label, cond, detail) => checks.push({ check: label, pass: !!cond, dettaglio: detail });
  ck('APP_VERSION = 1.3.0.76', d.app_version === '1.3.0.76', d.app_version);
  ck('dati live: available true e beats_persistence true', d.data_available === true && d.data_beats === true,
    'available=' + d.data_available + ' beats=' + d.data_beats);
  ck('toggle spento di default e visibile (gate pubblico)', d.initial.checked === false && d.initial.wrap !== 'none',
    JSON.stringify(d.initial));
  ck('selettore lead NASCOSTO con toggle OFF', d.initial.lead === 'none', d.initial.lead);
  ck('ON: overlay presente e in mappa', d.on.layer === true && d.on.hasLayer === true, JSON.stringify(d.on));
  ck('ON: immagine +30 (nowcast_lead030.webp)', /nowcast_lead030\.webp$/.test(String(d.on.url)), d.on.url);
  ck('ON: opacita 0.75', d.on.opacity === 0.75, d.on.opacity);
  ck('ON: pane dedicato nowcastPane', d.on.pane === 'nowcastPane', d.on.pane);
  ck('ON: pane z-index 407 e pointer-events none', d.on.paneZ === '407' && d.on.paneEvents === 'none', d.on.paneZ + '/' + d.on.paneEvents);
  ck('ON: legenda visibile', d.on.legend === 'block', d.on.legend);
  ck('ON: selettore lead visibile', d.on.lead === 'inline-flex' || d.on.lead === 'flex', d.on.lead);
  ck('ON: un solo overlay nowcast', d.on.count.n === 1, JSON.stringify(d.on.count));
  ck('ON: immagine realmente caricata', d.img_load && d.img_load.ok === true && d.img_load.w > 0, JSON.stringify(d.img_load));
  // Il mosaico SRI copre anche mare e Sicilia: 35,07-47,84N / 4,53-20,47E.
  ck('ON: bounds EPSG:3857 convertiti in lat/lon plausibili (34-49N, 4-21E)',
    d.on.bounds && d.on.bounds.sw.lat > 34 && d.on.bounds.ne.lat < 49 && d.on.bounds.sw.lng > 4 && d.on.bounds.ne.lng < 21,
    JSON.stringify(d.on.bounds));
  ck('cambio lead 60: URL cambiato in nowcast_lead060.webp', /nowcast_lead060\.webp$/.test(String(d.lead60.url)), d.lead60.url);
  ck('cambio lead: layer SOSTITUITO (nuova istanza, vecchia rimossa)', d.lead60.same_ref === false && d.lead60.old_on_map === false, JSON.stringify(d.lead60));
  ck('cambio lead: sempre UN solo overlay (nessun accumulo)', d.lead60.count.n === 1, JSON.stringify(d.lead60.count));
  ck('OFF: overlay rimosso', d.off.layer === false && d.off.count.n === 0, JSON.stringify(d.off));
  ck('OFF: legenda nascosta e selettore lead nascosto', d.off.legend === 'none' && d.off.lead === 'none', d.off.legend + '/' + d.off.lead);
  ck('gate: dev OFF + beats_persistence true -> visibile', d.gate_public !== 'none', d.gate_public);
  ck('gate: dev OFF + beats_persistence false -> NASCOSTO', d.gate_beats_false_devoff === 'none', d.gate_beats_false_devoff);
  ck('gate: beats_persistence null vale false -> NASCOSTO', d.gate_beats_null_devoff === 'none', d.gate_beats_null_devoff);
  ck('gate: dev OFF + available false -> NASCOSTO', d.gate_unavailable_devoff === 'none', d.gate_unavailable_devoff);
  ck('gate: Sviluppo ON -> visibile anche con gate pubblico chiuso', d.gate_dev_on !== 'none', d.gate_dev_on);
  ck('gate: ripristinati i dati -> di nuovo visibile', d.gate_restored !== 'none', d.gate_restored);
  ck('prodotto: overlay acceso prima del cambio', d.product.before_layer === true && d.product.before_count.n === 1, JSON.stringify(d.product));
  ck('prodotto fuori radar/LIVE: layer rimosso e checkbox spenta', d.product.after_layer === false && d.product.after_count.n === 0 && d.product.after_checked === false, JSON.stringify(d.product));
  ck('nessun overlay residuo finale', d.final_count.n === 0, JSON.stringify(d.final_count));

  // Rete: ogni nowcast servito con 200 e senza query-string.
  out.net = netNowcast;
  const nq = netNowcast.filter((r) => r.hasQuery);
  const bad = netNowcast.filter((r) => r.status !== 200);
  ck('rete: richieste nowcast senza query-string', nq.length === 0, JSON.stringify(nq));
  ck('rete: tutte le risposte nowcast 200', bad.length === 0, JSON.stringify(bad));
  ck('rete: almeno JSON + 1 WebP richiesti', netNowcast.length >= 2, JSON.stringify(netNowcast.map((r) => r.url)));

  // Eccezioni JS: nessuna, e nessun errore console riferito al nowcast.
  const ncErrors = consoleErrors.filter((t) => /nowcast|NOWCAST|toggleNowcast|setNowcastLead/i.test(t));
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
