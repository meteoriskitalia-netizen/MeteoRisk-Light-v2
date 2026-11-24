#!/usr/bin/env node
// VERIFICA BROWSER REALE (Chrome headless via CDP) del PRODOTTO nowcast pioggia 1.3.0.79.
//
// Apre l'APP_FILE di VERSION da un server statico locale (stessa origine dei
// dati: data/radar/nowcast.json e i WebP), con la rete esterna bloccata tranne
// unpkg/jsdelivr (Leaflet) e 127.0.0.1. Guida l'UI vera:
//   A) Radar Player -> Nowcast: la timeline NON sparisce, passa a leads[] e
//      mostra «+N min · HH:MM»; il conteggio frame e' quello dei lead;
//   B) play/pausa/slider/preload della timeline funzionano sui frame nowcast;
//   C) Nowcast -> Radar Player: torna ai 25 slot e a «Frame: n/25»;
//   D) Nowcast -> «— nessuno —»: pannello nascosto, nessun timer, nessun layer;
//   E) nessun doppio player (#nowcast-player-wrap assente dal DOM);
//   F) legenda SRI 1/5/10/20/50/100 con gli stessi RGB del radar;
//   G) gate Sviluppo sulla voce del prodotto; 0 eccezioni JS.
//
// USO: node scripts/validation/verify_ui_nowcast_13079.mjs [url]
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
               visible: !opt.hidden && !opt.disabled && cs.display !== 'none' };
    }
    function tlState() {
      var panel = document.getElementById('sync-timeline-panel');
      var slider = document.getElementById('sync-slider');
      var frames = document.getElementById('tile-load-frames');
      var nc = document.getElementById('sync-nowcast-label');
      return {
        source: syncTimelineSource, length: syncTimeline.length, index: syncIndex,
        playing: isSyncPlaying, panelActive: !!(panel && panel.classList.contains('active')),
        sliderMax: slider ? slider.max : null, sliderValue: slider ? slider.value : null,
        time: (document.getElementById('sync-time-display') || {}).innerText,
        framesDisplay: frames ? getComputedStyle(frames).display : null,
        nowcastLabelDisplay: nc ? getComputedStyle(nc).display : null,
        nowcastLabel: nc ? nc.textContent : null,
        caption: (document.getElementById('sync-caption') || {}).innerText
      };
    }
    var res = {};
    res.app_version = (typeof APP_VERSION !== 'undefined') ? APP_VERSION : null;
    res.data = { available: nowcastState.data && nowcastState.data.available,
                 beats: nowcastState.data && nowcastState.data.beats_persistence,
                 leads: (nowcastState.data && nowcastState.data.leads ? nowcastState.data.leads.length : 0) };
    res.leads = nowcastLeads().map(function (x) { return { lead: x.lead, image: x.image, official: x.official }; });
    res.option_initial = optionState();
    res.initial = { active: activeProductId, panel: disp('nowcast-panel'), count: countNowcast(), tl: tlState() };
    res.no_dup_player = document.querySelectorAll('#nowcast-player-wrap, #nowcast-play, #nowcast-slider').length;

    var sel = document.getElementById('product-select');

    // A) RADAR PLAYER -> NOWCAST: la timeline non deve sparire.
    sel.value = 'radar-player'; selectProduct('radar-player');
    await sleep(2500);
    res.radar_first = tlState();
    sel.value = 'nowcast'; selectProduct('nowcast');
    await sleep(1200);
    var leads = nowcastLeads();
    var l1 = nowcastState.layer;
    var firstUrl = l1 && l1._url;
    res.on = {
      active: activeProductId, timelineActive: !!nowcastState.timelineActive,
      layer: !!l1, hasLayer: !!(l1 && map.hasLayer(l1)), url: firstUrl,
      opacity: l1 && l1.options.opacity, pane: l1 && l1.options.pane,
      paneZ: map.getPane('nowcastPane') ? getComputedStyle(map.getPane('nowcastPane')).zIndex : null,
      panel: disp('nowcast-panel'), legend: disp('nowcast-legend'), count: countNowcast(),
      preloaded: nowcastState.preloaded.length, tl: tlState(),
     	mapCard: (function () { var m = document.querySelector('.map-card'); return m ? getComputedStyle(m).display : null; })(),
      others: { radarPlayer: !!isRadarPlayerActive, satellite: !!isSatelliteActive,
                live: !!isLivePanelActive, supercells: !!liveShowSupercells },
      img_load: await new Promise(function (r) {
        var im = new Image(); im.onload = function () { r({ ok: true, w: im.naturalWidth, h: im.naturalHeight }); };
        im.onerror = function () { r({ ok: false }); }; im.src = firstUrl;
      })
    };

    // A2) legenda SRI + official
    var savedFrame = syncIndex;
    if (syncTimeline.length) applySyncFrame(syncTimeline.length - 1);
    res.officialLabelLast = (document.getElementById('sync-nowcast-label') || {}).textContent;
    if (syncTimeline.length) applySyncFrame(savedFrame);
    res.legend = {
      colors: Array.prototype.map.call(document.querySelectorAll('#nowcast-legend-ramp .nowcast-sw'), function (e) { return getComputedStyle(e).backgroundColor; }),
      ticks: Array.prototype.map.call(document.querySelectorAll('#nowcast-legend-ticks span'), function (e) { return e.textContent; }),
      note: ((document.getElementById('nowcast-legend') || {}).textContent || '')
    };
    res.officialLabel = { none: nowcastFrameLabel({ lead: 25, valid: null, official: false }),
                          yes: nowcastFrameLabel({ lead: 30, valid: null, official: true }) };

    // B) play/pausa sulla timeline nowcast
    var startFrame = syncIndex, startUrl = nowcastState.layer && nowcastState.layer._url;
    toggleSyncPlay();
    var playing = isSyncPlaying;
    await sleep(1600);
    var midFrame = syncIndex, midUrl = nowcastState.layer && nowcastState.layer._url;
    toggleSyncPlay();
    var paused = !isSyncPlaying;
    await sleep(900);
    res.play = { playing: playing, startFrame: startFrame, midFrame: midFrame, advanced: midFrame !== startFrame,
                 urlChanged: midUrl !== startUrl, midUrl: midUrl, paused: paused,
                 frozen: syncIndex === midFrame };

    // C) preload della timeline nowcast
    syncStartPreload();
    await sleep(1400);
    res.preload = { status: (document.getElementById('sync-preload-status') || {}).innerText,
                    btnDisabled: (document.getElementById('sync-preload-btn') || {}).disabled,
                    preloaded: nowcastState.preloaded.length, n: leads.length };

    // D) lista dinamica: 12 lead sintetici -> timeline 12, official riconoscibili
    var savedLeads = nowcastState.data.leads;
    var fake = []; for (var q = 1; q <= 12; q++) fake.push({ lead_min: q * 5, valid_time: '2026-11-24T12:' + (q * 5 < 10 ? '0' : '') + (q * 5) + ':00Z', image: 'nowcast_lead0' + (q * 5 < 10 ? '0' : '') + (q * 5) + '.webp' });
    nowcastState.data.leads = fake;
    nowcastState.timelineActive = true;
    buildSyncTimeline();
    var t12 = syncTimeline;
    res.dynamic = { n: t12.length, max: document.getElementById('sync-slider').max,
                    first: t12.length ? t12[0].lead : null,
                    official: t12.filter(function (x) { return x.official; }).map(function (x) { return x.lead; }),
                    label30: t12.length > 5 ? nowcastFrameLabel(t12[5]) : null };
    nowcastState.data.leads = savedLeads;
    buildSyncTimeline();

    // E) NOWCAST -> RADAR PLAYER
    sel.value = 'radar-player'; selectProduct('radar-player');
    await sleep(3000);
    res.toRadar = tlState();
    res.toRadar.radar = !!isRadarPlayerActive;
    res.toRadar.nowcastLayer = !!nowcastState.layer;
    res.toRadar.timelineActive = !!nowcastState.timelineActive;
    res.toRadar.count = countNowcast();

    // F) NOWCAST -> «— nessuno —»
    sel.value = 'nowcast'; selectProduct('nowcast');
    await sleep(900);
    res.noneBefore = tlState();
    sel.value = ''; selectProduct('');
    await sleep(1500);
    res.none = { active: activeProductId, panel: disp('nowcast-panel'), count: countNowcast(),
                 layer: !!nowcastState.layer, tl: tlState(), timelineActive: !!nowcastState.timelineActive };

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
  if (d && d.legend && d.legend.note) d.legend.note = d.legend.note.replace(/\s+/g, ' ').trim();
  const checks = [];
  const ck = (label, cond, detail) => checks.push({ check: label, pass: !!cond, dettaglio: detail });
  const SRI_RGB = ['rgb(0, 85, 136)', 'rgb(0, 163, 224)', 'rgb(136, 221, 238)', 'rgb(255, 238, 0)', 'rgb(255, 170, 0)', 'rgb(193, 0, 0)'];
  const nLeads = d.leads.length;

  ck('APP_VERSION = 1.3.0.79', d.app_version === '1.3.0.79', d.app_version);
  ck('dati live: available true e beats_persistence true', d.data.available === true && d.data.beats === true, JSON.stringify(d.data));
  ck('nessun doppio player nel DOM', d.no_dup_player === 0, d.no_dup_player);
  ck('option «Nowcast pioggia» presente, con title, dopo «Radar Player»',
    d.option_initial && d.option_initial.text === 'Nowcast pioggia' && d.option_initial.title.length > 10 && d.option_initial.prev === 'radar-player',
    JSON.stringify(d.option_initial));
  ck('Radar Player: timeline radar con 25 slot e «Frame: n/25»',
    d.radar_first.source === 'radar' && d.radar_first.length === 25 && d.radar_first.sliderMax === '24' && d.radar_first.framesDisplay !== 'none' && d.radar_first.panelActive === true,
    JSON.stringify(d.radar_first));
  ck('Radar Player -> Nowcast: il pannello timeline RESTA attivo', d.on.tl.panelActive === true, JSON.stringify(d.on.tl));
  ck('Radar Player -> Nowcast: sorgente nowcast e conteggio = lead', d.on.tl.source === 'nowcast' && d.on.tl.length === nLeads, JSON.stringify({ source: d.on.tl.source, len: d.on.tl.length, leads: nLeads }));
  ck('Nowcast: slider max = lead-1 e parte dal primo frame', d.on.tl.sliderMax === String(nLeads - 1) && d.on.tl.index === 0, JSON.stringify({ max: d.on.tl.sliderMax, idx: d.on.tl.index }));
  ck('Nowcast: etichetta «+N min · HH:MM» invece di Frame', d.on.tl.framesDisplay === 'none' && d.on.tl.nowcastLabelDisplay !== 'none' && /^\+\d+ min · \d{2}:\d{2}/.test(String(d.on.tl.nowcastLabel)), JSON.stringify({ frames: d.on.tl.framesDisplay, label: d.on.tl.nowcastLabel }));
  ck('Nowcast: time display «+N min»', /^\+\d+ min$/.test(String(d.on.tl.time)), d.on.tl.time);
  ck('Nowcast: caption della timeline descrive il nowcast', /Nowcast pioggia/.test(String(d.on.tl.caption)), d.on.tl.caption);
  ck('Nowcast: overlay presente (un solo layer) e immagine caricata',
    d.on.layer === true && d.on.hasLayer === true && d.on.count.n === 1 && d.on.img_load && d.on.img_load.ok === true,
    JSON.stringify({ layer: d.on.layer, count: d.on.count, img: d.on.img_load }));
  ck('Nowcast: pane z 407 e opacita 0.75', d.on.pane === 'nowcastPane' && d.on.paneZ === '407' && d.on.opacity === 0.75, d.on.pane + '/' + d.on.paneZ + '/' + d.on.opacity);
  ck('Nowcast: panel legenda e .map-card visibili', d.on.panel !== 'none' && d.on.legend !== 'none' && d.on.mapCard !== 'none', d.on.panel + '/' + d.on.legend + '/' + d.on.mapCard);
  ck('Nowcast: altri layer interferenti spenti', d.on.others.radarPlayer === false && d.on.others.satellite === false && d.on.others.live === false && d.on.others.supercells === false, JSON.stringify(d.on.others));
  ck('Nowcast: precaricati tutti i frame', d.on.preloaded === nLeads, d.on.preloaded + '/' + nLeads);
  ck('legenda: 6 valori SRI 1/5/10/20/50/100+', d.legend.ticks.join('|') === '1|5|10|20|50|100+', JSON.stringify(d.legend.ticks));
  ck('legenda: 6 colori IDENTICI alla rampa SRI del radar (DPC_SRI_STOPS)',
    d.legend.colors.length === 6 && d.legend.colors.every((c, i) => c === SRI_RGB[i]), JSON.stringify(d.legend.colors));
  ck('legenda: nota affidabilita e riga sotto 1 mm/h',
    d.legend.note.indexOf("dopo 30 minuti l'affidabilità cala") >= 0 &&
    /sotto 1 mm\/h il radar osservato è trasparente; il nowcast mostra la pioggia debole/.test(d.legend.note), '');
  ck('official: etichetta «· ufficiale» solo sul lead official',
    d.officialLabel.yes.indexOf('ufficiale') >= 0 && d.officialLabel.none.indexOf('ufficiale') < 0, JSON.stringify(d.officialLabel));
  ck('official: ultimo frame (60) marcato ufficiale', /ufficiale/.test(String(d.officialLabelLast)), d.officialLabelLast);
  ck('play: timer attivo e frame avanzato', d.play.playing === true && d.play.advanced === true, JSON.stringify(d.play));
  ck('play: URL dell\'overlay cambiato (stesso layer)', d.play.urlChanged === true, d.play.midUrl);
  ck('pausa: timer fermo e frame congelato', d.play.paused === true && d.play.frozen === true, JSON.stringify(d.play));
  ck('preload timeline nowcast: contatore n/n OK e pulsante riabilitato',
    new RegExp('^' + nLeads + '/' + nLeads + ' OK$').test(String(d.preload.status)) && d.preload.btnDisabled === false && d.preload.preloaded === nLeads,
    JSON.stringify(d.preload));
  ck('lista dinamica: 12 lead -> timeline 12, slider max 11, +5 primo',
    d.dynamic.n === 12 && d.dynamic.max === '11' && d.dynamic.first === 5, JSON.stringify(d.dynamic));
  ck('lista dinamica: official 15/30/60 e etichetta +30 ufficiale',
    d.dynamic.official.join(',') === '15,30,60' && /\+30 min/.test(String(d.dynamic.label30)) && /ufficiale/.test(String(d.dynamic.label30)), JSON.stringify(d.dynamic));
  ck('Nowcast -> Radar Player: torna a 25 slot, «Frame: n/25» e radar acceso',
    d.toRadar.source === 'radar' && d.toRadar.length === 25 && d.toRadar.sliderMax === '24' && d.toRadar.framesDisplay !== 'none' && d.toRadar.nowcastLabelDisplay === 'none' && d.toRadar.radar === true && d.toRadar.nowcastLayer === false && d.toRadar.timelineActive === false && d.toRadar.count.n === 0,
    JSON.stringify(d.toRadar));
  ck('Nowcast -> «nessuno»: pannello nascosto, nessun timer, nessun overlay',
    d.none.active === '' && d.none.panel === 'none' && d.none.tl.panelActive === false && d.none.tl.playing === false && d.none.count.n === 0 && d.none.timelineActive === false,
    JSON.stringify(d.none));
  ck('gate: dev OFF + beats false -> option nascosta', d.gate_false_devoff && d.gate_false_devoff.visible === false && d.gate_false_devoff.hidden === true && d.gate_false_devoff.disabled === true, JSON.stringify(d.gate_false_devoff));
  ck('gate: beats null vale ok (non nascosta)', d.gate_null_devoff && d.gate_null_devoff.visible === true, JSON.stringify(d.gate_null_devoff));
  ck('gate: dev OFF + available false -> option nascosta', d.gate_unavail_devoff && d.gate_unavail_devoff.visible === false, JSON.stringify(d.gate_unavail_devoff));
  ck('gate: Sviluppo ON -> option visibile', d.gate_dev_on && d.gate_dev_on.visible === true, JSON.stringify(d.gate_dev_on));
  ck('gate: dati ripristinati -> option visibile', d.gate_restored && d.gate_restored.visible === true, JSON.stringify(d.gate_restored));

  out.net = netNowcast;
  const nq = netNowcast.filter((r) => r.hasQuery);
  const bad = netNowcast.filter((r) => r.status !== 200);
  ck('rete: richieste nowcast senza query-string', nq.length === 0, JSON.stringify(nq));
  ck('rete: tutte le risposte nowcast 200', bad.length === 0, JSON.stringify(bad));
  ck('rete: JSON + almeno un WebP richiesti', netNowcast.length >= 2, JSON.stringify(netNowcast.map((r) => r.url)));

  const ncErrors = consoleErrors.filter((t) => /nowcast|NOWCAST|syncTimeline|sync-nowcast/i.test(t));
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
