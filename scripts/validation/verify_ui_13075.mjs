#!/usr/bin/env node
// VERIFICA BROWSER HEADLESS degli stati AFA e della legenda vento (1.3.0.75 -> 1.3.0.78).
//
// Apre l'app in Chrome headless (CDP, nessuna dipendenza npm), con la rete
// esterna bloccata (host-resolver-rules) e un server statico locale gia' avviato
// sulla URL passata. Legge getComputedStyle REALE del DOM:
//   A) #btn-radar-afa OFF -> bordo #334155 (51,65,85), nessun border inline;
//   B) #afa-region-it selezionata -> bordo chiaro #cbd5e1 (203,213,225) e
//      sfondo #334155, DIVERSI da .sc-toggle.on (viola #a855f7 / #2e1065);
//   C) #btn-radar-afa con classe `on` -> bordo verde #22c55e (34,197,94).
//
// USO: node scripts/validation/verify_ui_13075.mjs <url>
'use strict';
import { spawn } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { fileURLToPath } from 'url';

const URL_APP = process.argv[2] || 'http://127.0.0.1:8765/mri-light-1.3.0.78.html';
const CHROME = process.env.CHROME ||
  path.join(process.env.LOCALAPPDATA || '', 'ms-playwright', 'chromium-1243', 'chrome-win64', 'chrome.exe');
const PORT = 9333;
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'mri-cdp-'));

// 1.3.0.78 - Nel browser headless con la rete esterna BLOCCATA il boot dell'app
// si interrompe prima delle `var` del campo centraline (misurato: STATION_FIELD_K,
// STATION_FIELD_SNOW_SCALE, L e stationsData sono `undefined`, mentre le function
// declaration sono disponibili). Per la legenda vento il test passa quindi le
// STESSE classi/colori letti dal file servito: il DOM, il CSS, le larghezze e la
// resa delle etichette restano quelli reali.
function readWindConfig() {
  const rel = new URL(URL_APP).pathname.replace(/^\//, '');
  const p = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..', rel);
  const src = fs.readFileSync(p, 'utf8');
  const strip = (t) => t.replace(/\/\/[^\n]*/g, '').replace(/\s+/g, ' ');
  const mBp = src.match(/var STATION_FIELD_WIND_BREAKPOINTS\s*=\s*(\[[^\]]*\]);/);
  const mCol = src.match(/var STATION_FIELD_WIND_COLORS\s*=\s*(\[[\s\S]*?\]);/);
  if (!mBp || !mCol) throw new Error('classi vento non trovate nel file servito');
  return { bp: Function('return ' + strip(mBp[1]))(), cols: Function('return ' + strip(mCol[1]))() };
}
const WC = readWindConfig();
const WIND_INJECT = 'if (typeof STATION_FIELD_WIND_SCALE === "undefined") {' +
  'window.STATION_FIELD_WIND_BREAKPOINTS = ' + JSON.stringify(WC.bp) + ';' +
  'window.STATION_FIELD_WIND_COLORS = ' + JSON.stringify(WC.cols) + ';' +
  'window.STATION_FIELD_WIND_SCALE = { type: "breakpoint", unit: "m/s", min: null, max: null,' +
  ' breakpoints: window.STATION_FIELD_WIND_BREAKPOINTS,' +
  ' colors: { light: window.STATION_FIELD_WIND_COLORS, dark: window.STATION_FIELD_WIND_COLORS } };' +
  'window.STATION_FIELD_LEGEND = window.STATION_FIELD_LEGEND || {};' +
  'window.STATION_FIELD_LEGEND.station_wind_classes = { lo: 0, hi: 30, ticks: window.STATION_FIELD_WIND_BREAKPOINTS.slice() };' +
  '}' +
  'if (typeof escapeHtml !== "function") window.escapeHtml = function (s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); };' +
  'if (typeof fmtNum !== "function") window.fmtNum = function (v, d) { return Number(v).toFixed(d); };';

const chrome = spawn(CHROME, [
  '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--disable-background-networking', '--disable-sync', '--disable-extensions',
  '--remote-debugging-port=' + PORT,
  '--user-data-dir=' + tmp,
  // Tutto cio' che non e' 127.0.0.1 non risolve: la pagina non resta appesa alla rete.
  '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
  URL_APP
], { stdio: 'ignore' });

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }async function waitFor(fn, ms, what) {
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
function send(method, params) {
  return new Promise((resolve, reject) => {
    const i = ++id;
    pending.set(i, { resolve, reject });
    ws.send(JSON.stringify({ id: i, method, params: params || {} }));
  });
}
async function evaluate(expression) {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error('JS: ' + (r.exceptionDetails.text || ''));
  return r.result.value;
}

const out = { url: URL_APP, chrome: CHROME, rete_esterna: 'bloccata (MAP * ~NOTFOUND, EXCLUDE 127.0.0.1)' };
try {
  const list = await waitFor(async () => {
    const res = await fetch('http://127.0.0.1:' + PORT + '/json/list');
    const arr = await res.json();
    const p = arr.find((t) => t.type === 'page' && t.webSocketDebuggerUrl &&
      (t.url || '').indexOf('mri-light') >= 0) ||
      arr.find((t) => t.type === 'page' && t.webSocketDebuggerUrl);
    return p || null;
  }, 30000, 'target CDP');
  ws = new WebSocket(list.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (!m.id) return;
    const p = pending.get(m.id);
    if (!p) return;
    pending.delete(m.id);
    if (m.error) p.reject(new Error(m.error.message));
    else p.resolve(m.result);
  };
  await send('Runtime.enable');
  await send('Page.enable');
  await send('Page.navigate', { url: URL_APP });
  await waitFor(async () => {
    const href = await evaluate('location.href');
    return typeof href === 'string' && href.indexOf('mri-light') >= 0;
  }, 30000, 'navigazione verso ' + URL_APP);
  await waitFor(async () => {
    return await evaluate("!!document.getElementById('btn-radar-afa')");
  }, 30000, '#btn-radar-afa nel DOM');
  await waitFor(async () => await evaluate('document.readyState'), 30000, 'document.readyState');
  // Lascia girare il boot dell'app (fetch locali) senza aspettare la rete esterna.
  await sleep(4000);

  out.dom = await evaluate(`(function () {
    var dbg = { href: location.href, title: document.title,
                htmlLen: (document.documentElement.outerHTML || '').length,
                bodyLen: (document.body ? document.body.innerHTML.length : -1),
                hasBtn: !!document.getElementById('btn-radar-afa'),
                sheets: document.styleSheets ? document.styleSheets.length : -1,
                windScaleType: (typeof STATION_FIELD_WIND_SCALE),
                kType: (typeof STATION_FIELD_K),
                snowScaleType: (typeof STATION_FIELD_SNOW_SCALE),
                lType: (typeof L),
                stationsDataType: (typeof stationsData),
                windBp: (typeof STATION_FIELD_WIND_SCALE !== 'undefined' && STATION_FIELD_WIND_SCALE) ? STATION_FIELD_WIND_SCALE.breakpoints.length : null,
                scaleFn: (typeof stationFieldScale),
                scaleWind: (typeof stationFieldScale === 'function') ? !!stationFieldScale('station_wind_classes') : null,
                err: (document.body ? document.body.innerText.slice(0, 200) : '') };
    var byId = function (id) { return document.getElementById(id); };
    var st = function (e) {
      if (!e) return null;
      var cs = getComputedStyle(e);
      return { presente: true, classi: e.className || '', display: cs.display,
               bordo: cs.borderTopColor, sfondo: cs.backgroundColor, testo: cs.color,
               aria: e.getAttribute('aria-pressed'), inlineBorder: e.style.borderColor || '' };
    };
    // Riferimento: lo stile .sc-toggle.on (viola) misurato sul DOM, non copiato.
    var ref = document.createElement('button');
    ref.className = 'sc-toggle on';
    ref.textContent = 'rif';
    document.body.appendChild(ref);
    var refOn = st(ref);
    ref.className = 'sc-toggle';
    var refOff = st(ref);
    ref.parentNode.removeChild(ref);
    var btn = byId('btn-radar-afa');
    var off = st(btn);
    if (btn) btn.classList.add('on');
    var on = st(btn);
    if (btn) btn.classList.remove('on');
    if (btn) btn.style.display = '';
    // Selettore regione: nascosto con l'AFA OFF, visibile quando il layer va ON.
    var sw = byId('afa-region-switch');
    var swOff = sw ? getComputedStyle(sw).display : null;
    var swAfterOn = null, swAfterOff = null, btnAfterOn = null;
    if (typeof liveAfaSyncButtons === 'function') {
      liveAfaSyncButtons(true);
      swAfterOn = sw ? getComputedStyle(sw).display : null;
      btnAfterOn = st(btn);
      liveAfaSyncButtons(false);
      swAfterOff = sw ? getComputedStyle(sw).display : null;
    }
    // 1.3.0.75 (quarta correzione) - riga #live-afa-status.
    var row = byId('live-afa-status');
    var rowState = function () {
      return row ? { display: getComputedStyle(row).display, html: row.innerHTML,
                     text: (row.textContent || '').trim() } : null;
    };
    // «Struttura convettiva» ON, AFA OFF: la riga NON deve esserci.
    window.liveShowSupercells = true;
    window.liveAfaEnabled = false;
    window.liveAfaMeta = null;
    window.liveUpdateAfaStatus();
    var rowScOnAfaOff = rowState();
    // AFA ON con pixel reali.
    window.liveAfaEnabled = true;
    window.liveAfaMeta = { slot: '2026-11-24T14:35:00Z', region: 'it', px: 582, hasSignal: true };
    window.liveUpdateAfaStatus();
    var rowAfaOn = rowState();
    // AFA ON senza segnale (has_signal false).
    window.liveAfaMeta = { slot: '2026-11-24T14:40:00Z', region: 'it', px: 0, hasSignal: false };
    window.liveUpdateAfaStatus();
    var rowNoSignal = rowState();
    // AFA ON senza metadati (es. GetMap Europa): area con emissione ottica.
    window.liveAfaMeta = { slot: '2026-11-24T14:45:00Z', region: 'eu', px: null, hasSignal: null };
    window.liveUpdateAfaStatus();
    var rowNoPx = rowState();
    // Ciclo reale via liveAfaSyncButtons.
    window.liveAfaEnabled = true;
    window.liveAfaMeta = { slot: '2026-11-24T14:50:00Z', region: 'it', px: 12, hasSignal: true };
    window.liveAfaSyncButtons(true);
    var rowSyncOn = rowState();
    window.liveAfaEnabled = false;
    window.liveAfaMeta = null;
    window.liveAfaSyncButtons(false);
    var rowSyncOff = rowState();
    // 1.3.0.78 - legenda VENTO reale: costruita nel DOM, larghezza 260 px,
    // etichette orizzontali con m/s + km/h. Si misura il rettangolo VERO.
    ${WIND_INJECT}
    var windLegend = null;
    var lab = byId('live-stationfield-scale-labels');
    if (lab && typeof stationFieldBuildLegend === 'function') {
      var oldParent = lab.parentNode, oldNext = lab.nextSibling;
      document.body.appendChild(lab);
      lab.style.position = 'relative';
      lab.style.display = 'block';
      lab.style.width = '260px';
      lab.style.height = '14px';
      try {
        stationFieldBuildLegend({ scale: 'station_wind_classes', unit: 'm/s', varKey: 'wind' },
          { vMin: null, vMax: null });
        var rects = [], ch = lab.children || [];
        for (var ci = 0; ci < ch.length; ci++) {
          var rr = ch[ci].getBoundingClientRect();
          rects.push({ text: ch[ci].textContent, left: rr.left, right: rr.right, w: rr.width });
        }
        var ov = 0;
        for (var qi = 1; qi < rects.length; qi++) {
          if (rects[qi].left < rects[qi - 1].right - 0.5) ov++;
        }
        var unitEl = byId('live-stationfield-unit');
        windLegend = { count: rects.length, overlap: ov,
                       texts: rects.map(function (r) { return r.text; }),
                       unit: unitEl ? unitEl.textContent : null };
      } finally {
        lab.parentNode.removeChild(lab);
        if (oldParent) oldParent.insertBefore(lab, oldNext);
      }
    }
    return {
      debug: dbg,
      wind_legend: windLegend,
      row_sc_on_afa_off: rowScOnAfaOff,
      row_afa_on: rowAfaOn,
      row_no_signal: rowNoSignal,
      row_no_px: rowNoPx,
      row_sync_on: rowSyncOn,
      row_sync_off: rowSyncOff,
      switch_afa_off: swOff,
      switch_afa_on: swAfterOn,
      switch_afa_off_again: swAfterOff,
      btn_afa_sync_on: btnAfterOn,
      btn_afa_off: off,
      btn_afa_on: on,
      regione_italia: st(byId('afa-region-it')),
      regione_europa: st(byId('afa-region-eu')),
      rif_sc_on: refOn,
      rif_sc_off: refOff,
      app_version: (typeof APP_VERSION !== 'undefined') ? APP_VERSION : null
    };
  })()`);

  const d = out.dom;
  console.log('DOM: ' + JSON.stringify(d));
  const checks = [];
  function ck(label, cond, detail) { checks.push({ check: label, pass: !!cond, dettaglio: detail }); }
  ck('pagina giusta: #afa-region-it e #btn-radar-afa presenti', !!(d && d.btn_afa_off && d.regione_italia),
    d ? ('btn=' + !!d.btn_afa_off + ' regione=' + !!d.regione_italia) : 'nessun DOM');
  if (!d || !d.btn_afa_off || !d.regione_italia) {
    out.checks = checks; out.risultato = 'FAIL: DOM atteso assente';
    console.log(JSON.stringify(out, null, 2));
    process.exitCode = 1;
    throw new Error('DOM atteso assente');
  }
  ck('AFA OFF: bordo neutro #334155', d.btn_afa_off && d.btn_afa_off.bordo === 'rgb(51, 65, 85)', d.btn_afa_off && d.btn_afa_off.bordo);
  ck('AFA OFF: nessun border-color inline nel markup', d.btn_afa_off && d.btn_afa_off.inlineBorder === '', JSON.stringify(d.btn_afa_off && d.btn_afa_off.inlineBorder));
  ck('AFA OFF: NON viola', d.btn_afa_off && d.btn_afa_off.bordo !== d.rif_sc_on.bordo, 'off=' + (d.btn_afa_off && d.btn_afa_off.bordo));
  ck('AFA ON (classe on): bordo verde #22c55e', d.btn_afa_on && d.btn_afa_on.bordo === 'rgb(34, 197, 94)', d.btn_afa_on && d.btn_afa_on.bordo);
  ck('Regione Italia: bordo chiaro #cbd5e1 (selezionata, non accesa)',
    d.regione_italia && d.regione_italia.bordo === 'rgb(203, 213, 225)', d.regione_italia && d.regione_italia.bordo);
  ck('Regione Italia: sfondo neutro #334155, diverso dal viola .on #2e1065',
    d.regione_italia && d.regione_italia.sfondo === 'rgb(51, 65, 85)' &&
    d.regione_italia.sfondo !== d.rif_sc_on.sfondo,
    (d.regione_italia && d.regione_italia.sfondo) + ' vs on ' + d.rif_sc_on.sfondo);
  ck('Regione Italia: bordo diverso da .on viola', d.regione_italia.bordo !== d.rif_sc_on.bordo,
    d.regione_italia.bordo + ' vs ' + d.rif_sc_on.bordo);
  ck('Regione Italia: aria-pressed true, classe is-selected e NON on',
    d.regione_italia.aria === 'true' && /is-selected/.test(d.regione_italia.classi) &&
    !/(^|\s)on(\s|$)/.test(d.regione_italia.classi), d.regione_italia.classi);
  ck('Regione Europa: non selezionata (classe senza is-selected)',
    d.regione_europa && !/is-selected/.test(d.regione_europa.classi) &&
    d.regione_europa.aria === 'false', d.regione_europa && d.regione_europa.classi);
  ck('Selettore regione NASCOSTO con AFA OFF', d.switch_afa_off === 'none', d.switch_afa_off);
  ck('Selettore regione VISIBILE quando l AFA va ON (liveAfaSyncButtons(true))',
    d.switch_afa_on === 'inline-flex' || d.switch_afa_on === 'flex', d.switch_afa_on);
  ck('Selettore regione torna NASCOSTO spegnendo l AFA', d.switch_afa_off_again === 'none', d.switch_afa_off_again);
  ck('AFA sincronizzato ON: bordo verde #22c55e',
    d.btn_afa_sync_on && d.btn_afa_sync_on.bordo === 'rgb(34, 197, 94)',
    d.btn_afa_sync_on && d.btn_afa_sync_on.bordo);
  ck('SC ON + AFA OFF: #live-afa-status NON a schermo e vuota',
    d.row_sc_on_afa_off && d.row_sc_on_afa_off.display === 'none' && d.row_sc_on_afa_off.html === '',
    d.row_sc_on_afa_off && (d.row_sc_on_afa_off.display + ' html=' + JSON.stringify(d.row_sc_on_afa_off.html)));
  ck('AFA ON: testo utile «AFA MTG-I · slot 14:35 · 582 px attivi»',
    d.row_afa_on && /AFA MTG-I · slot 14:35 · 582 px attivi/.test(d.row_afa_on.text),
    d.row_afa_on && d.row_afa_on.text);
  ck('nessun messaggio di debug («dato non pubblicato in latest.json»)',
    [d.row_sc_on_afa_off, d.row_afa_on, d.row_no_signal, d.row_no_px, d.row_sync_on, d.row_sync_off]
      .every((r) => !r || !/dato non pubblicato|vedi strutture/.test(r.text)));
  ck('AFA ON con has_signal false: «nessun pixel attivo»',
    d.row_no_signal && /slot 14:40 · nessun pixel attivo/.test(d.row_no_signal.text),
    d.row_no_signal && d.row_no_signal.text);
  ck('AFA ON senza metadati (Europa WMS): «area con emissione ottica»',
    d.row_no_px && /slot 14:45 · area con emissione ottica/.test(d.row_no_px.text),
    d.row_no_px && d.row_no_px.text);
  ck('liveAfaSyncButtons(true) con layer acceso: riga visibile con slot e pixel',
    d.row_sync_on && d.row_sync_on.display !== 'none' && /slot 14:50 · 12 px attivi/.test(d.row_sync_on.text),
    d.row_sync_on && d.row_sync_on.text);
  ck('liveAfaSyncButtons(false): riga nascosta e svuotata (nessun residuo)',
    d.row_sync_off && d.row_sync_off.display === 'none' && d.row_sync_off.html === '',
    d.row_sync_off && (d.row_sync_off.display + ' html=' + JSON.stringify(d.row_sync_off.html)));
  ck('legenda vento: etichette orizzontali senza sovrapposizioni, tutte con km/h',
    d.wind_legend && d.wind_legend.count >= 6 && d.wind_legend.overlap === 0 &&
    d.wind_legend.texts.every((t) => /^\d+(,\d+)? \(\d+\)$/.test(t)),
    d.wind_legend && (d.wind_legend.count + ' etichette, overlap=' + d.wind_legend.overlap + ' · ' + d.wind_legend.texts.join(' | ')));
  ck('legenda vento: unità «m/s · km/h» e tacca 10 (36)',
    d.wind_legend && /m\/s/.test(d.wind_legend.unit) && /km\/h/.test(d.wind_legend.unit) &&
    d.wind_legend.texts.indexOf('10 (36)') >= 0,
    d.wind_legend && (d.wind_legend.unit + ' · ' + d.wind_legend.texts.join(' | ')));
  ck('APP_VERSION = 1.3.0.78', d.app_version === '1.3.0.78', d.app_version);

  out.checks = checks;
  const fails = checks.filter((c) => !c.pass);
  out.risultato = fails.length ? (fails.length + ' FAIL') : 'PASS';
  console.log(JSON.stringify(out, null, 2));
  process.exitCode = fails.length ? 1 : 0;
} finally {
  try { if (ws) ws.close(); } catch (e) { }
  try { chrome.kill(); } catch (e) { }
  setTimeout(() => { try { fs.rmSync(tmp, { recursive: true, force: true }); } catch (e) { } }, 500);
}
