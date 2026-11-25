#!/usr/bin/env node
// VERIFICA BROWSER REALE (Chrome headless, rete ATTIVA) della 1.3.0.82:
// con la variabile Pioggia quali finestre compaiono/attive, il blob daily_series
// si carica, e quante stazioni usa la 6 h / 24 h.
//
// USO: node scripts/validation/verify_stations_windows_13082.mjs <url>
'use strict';
import { spawn } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';

const URL_APP = process.argv[2] || 'http://127.0.0.1:8765/mri-light-1.3.0.82.html';
const CHROME = process.env.CHROME ||
  path.join(process.env.LOCALAPPDATA || '', 'ms-playwright', 'chromium-1243', 'chrome-win64', 'chrome.exe');
const PORT = 9335;
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'mri-win-'));
const chrome = spawn(CHROME, [
  '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--disable-background-networking', '--disable-sync', '--disable-extensions',
  '--remote-debugging-port=' + PORT, '--user-data-dir=' + tmp, 'about:blank'
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(fn, ms, what) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try { const v = await fn(); if (v) return v; } catch (e) { }
    await sleep(500);
  }
  throw new Error('timeout: ' + what);
}
let id = 0; const pending = new Map(); let ws;
function send(method, params) {
  return new Promise((resolve, reject) => {
    const i = ++id; pending.set(i, { resolve, reject });
    ws.send(JSON.stringify({ id: i, method, params: params || {} }));
  });
}
async function evaluate(expression) {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error('JS: ' + (r.exceptionDetails.exception && r.exceptionDetails.exception.description || r.exceptionDetails.text));
  return r.result.value;
}

const out = { url: URL_APP, checks: [] };
const ck = (label, cond, detail) => out.checks.push({ check: label, pass: !!cond, dettaglio: detail });
try {
  const target = await waitFor(async () => {
    const res = await fetch('http://127.0.0.1:' + PORT + '/json/list');
    const arr = await res.json();
    return arr.find((t) => t.type === 'page' && t.webSocketDebuggerUrl) || null;
  }, 30000, 'target CDP');
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (!m.id) return; const p = pending.get(m.id); if (!p) return; pending.delete(m.id); if (m.error) p.reject(new Error(m.error.message)); else p.resolve(m.result); };
  await send('Runtime.enable'); await send('Page.enable');
  await send('Page.navigate', { url: URL_APP });
  // L'app non carica le centraline al boot: lo fa quando si sceglie la sorgente.
  // Qui si chiama direttamente stationsLoad() (function declaration globale).
  await waitFor(async () => await evaluate("typeof stationsLoad === 'function'"), 60000, 'stationsLoad');
  await evaluate("try { stationsLoad(true); } catch (e) { String(e); }");

  // Boot completo: il campo centraline ha letto latest.json (o index.json).
  await waitFor(async () => await evaluate("typeof stationsData !== 'undefined' && stationsData && stationsData.stations && stationsData.stations.length > 0"), 90000, 'stationsData');
  // Blob giornaliero: fire-and-forget all'avvio; si aspetta che arrivi.
  const dailyOk = await waitFor(async () => await evaluate("typeof stationsDaily !== 'undefined' && !!stationsDaily && !!stationsDaily.stations"), 90000, 'stationsDaily').then(() => true).catch(() => false);
  out.daily_loaded = dailyOk;
  out.daily_meta = await evaluate("(function(){ if (typeof stationsDaily === 'undefined' || !stationsDaily) return null; return { schema: stationsDaily.schema, day: stationsDaily.day, generated_at: stationsDaily.generated_at, stations: Object.keys(stationsDaily.stations || {}).length }; })()");
  out.app_version = await evaluate("(typeof APP_VERSION !== 'undefined') ? APP_VERSION : null");

  out.rain_options = await evaluate(`(function(){
    stationFieldVar = 'rain'; stationFieldWindow = '1h'; stationFieldSyncControls();
    var sel = document.getElementById('stationfield-window');
    var o = [];
    for (var i = 0; i < sel.options.length; i++) {
      var op = sel.options[i];
      o.push({ value: op.value, hidden: !!op.hidden, display: op.style.display,
               disabled: !!op.disabled, title: op.title || '' });
    }
    return o;
  })()`);

  const readWindow = (win) => evaluate(`(function(){
    stationFieldVar = 'rain'; stationFieldWindow = '${win}'; stationFieldSyncControls();
    var spec = stationFieldSpec('rain', '${win}');
    var now = Date.now();
    var col = stationFieldCollect(stationsData.stations, spec, now);
    var frame = { W: 256, H: 227, minLat: 36, maxLat: 48, minLon: 6, maxLon: 19,
      mnM: Math.log(Math.tan(Math.PI/4 + 36*Math.PI/360)),
      mxM: Math.log(Math.tan(Math.PI/4 + 48*Math.PI/360)), lonSpan: 13 };
    frame.mSpan = frame.mxM - frame.mnM;
    var r = null;
    try { r = stationFieldRaster(col.points, stationFieldScale(spec.scale), frame, null, 1, 'local'); } catch (e) {}
    return { available: spec.available, note: spec.note || '', partial: !!spec.partialLowerBound,
             partialOnly: !!spec.partialOnly, dailyField: spec.dailyField,
             ui_note: (document.getElementById('live-stationfield-window-note') || {}).textContent || '',
             total: col.total, withValue: col.withValue, used: col.used,
             partialWindow: col.partialWindow, points: col.points.length,
             painted: r ? r.painted : null,
             title: spec.partialTitle || '' };
  })()`);

  out.rain_6h = await readWindow('6h');
  out.rain_24h = await readWindow('24h');
  out.rain_1h = await readWindow('1h');
  await evaluate("stationFieldVar = 'rain'; stationFieldWindow = '1h'; stationFieldSyncControls();");

  // Le medie parziali restano NON disponibili; il massimo della raffica sì.
  out.mean_gate = await evaluate(`(function(){
    var w = stationFieldSpec('wind', '24h'); var t = stationFieldSpec('temp', '24h'); var g = stationFieldSpec('gust', '24h');
    var ww = stationFieldDailyState('wind_24h_ms'), tw = stationFieldDailyState('temp_24h_c'), gw = stationFieldDailyState('gust_max_24h_ms');
    return { wind_available: w.available, temp_available: t.available, gust_available: g.available,
             wind_complete: ww.complete, wind_partial: ww.partial,
             temp_complete: tw.complete, temp_partial: tw.partial,
             gust_total: gw.total, gust_partial_flag: gw.partial };
  })()`);

  const vis = out.rain_options.filter((o) => !o.hidden).map((o) => o.value).join(',');
  ck('Pioggia: finestre VISIBILI = 1h,3h,6h,24h', vis === '1h,3h,6h,24h', vis);
  ck('Pioggia: 5 min, 30 min e «oggi» NASCOSTE',
    out.rain_options.filter((o) => ['5m', '30m', 'day'].includes(o.value)).every((o) => o.hidden), JSON.stringify(out.rain_options.map((o) => [o.value, o.hidden])));
  ck('blob daily_series carico (schema/day/stazioni)', dailyOk && out.daily_meta && out.daily_meta.stations > 1000, JSON.stringify(out.daily_meta));
  const d6 = out.rain_options.find((o) => o.value === '6h');
  const d24 = out.rain_options.find((o) => o.value === '24h');
  ck('6 h e 24 h ABILITATE dopo il blob', d6 && d24 && !d6.disabled && !d24.disabled, JSON.stringify({ d6, d24 }));
  ck('24 h: disponibile e dichiarata parziale',
    out.rain_24h.available && out.rain_24h.partial && /cumulata da mezzanotte · parziale, possibile sottostima/.test(out.rain_24h.note),
    out.rain_24h.available + ' · ' + out.rain_24h.note);
  ck('24 h: la nota VISIBILE accanto a Finestra è la frase breve richiesta',
    out.rain_24h.ui_note === 'cumulata da mezzanotte · parziale, possibile sottostima', JSON.stringify(out.rain_24h.ui_note));
  ck('24 h: stazioni usate > 0 e pixel dipinti > 0', out.rain_24h.used > 0 && out.rain_24h.painted > 0,
    'used=' + out.rain_24h.used + ' painted=' + out.rain_24h.painted);
  ck('6 h: stazioni usate > 0 e pixel dipinti > 0', out.rain_6h.used > 0 && out.rain_6h.painted > 0,
    'used=' + out.rain_6h.used + ' painted=' + out.rain_6h.painted);
  ck('medie parziali: vento medio 24 h su tutte parziali -> BLOCCATO; temperatura disponibile perché ha stazioni complete; raffica massima disponibile',
    out.mean_gate.wind_available === false && out.mean_gate.wind_complete === 0 &&
    out.mean_gate.temp_available === (out.mean_gate.temp_complete > 0) &&
    out.mean_gate.gust_available === (out.mean_gate.gust_total > 0),
    JSON.stringify(out.mean_gate));
  const fails = out.checks.filter((c) => !c.pass);
  out.risultato = fails.length ? (fails.length + ' FAIL') : 'PASS';
  console.log(JSON.stringify(out, null, 2));
  process.exitCode = fails.length ? 1 : 0;
} finally {
  try { if (ws) ws.close(); } catch (e) { }
  try { chrome.kill(); } catch (e) { }
  setTimeout(() => { try { fs.rmSync(tmp, { recursive: true, force: true }); } catch (e) { } }, 500);
}
