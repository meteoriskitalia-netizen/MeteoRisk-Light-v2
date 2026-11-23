#!/usr/bin/env node
// TEST 1.3.0.59 — FORMATTER DELLA VISTA SIMPLE (simple/index.html).
// Estrae il blocco <script id="simple-formatters"> (funzioni PURE, nessun DOM
// al caricamento), lo valuta in un vm con window={} e verifica i formati,
// le classi dichiarate e le scale. Poi controlla la struttura della pagina
// (selettori, URL dati, parametri IDW) senza navigare.
'use strict';

import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const HTML_PATH = path.join(ROOT, 'simple', 'index.html');
const html = fs.readFileSync(HTML_PATH, 'utf8');

let failures = 0;
function ok(label, cond, detail = '') {
  console.log(`  [${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? ` · ${detail}` : ''}`);
  if (!cond) failures++;
}
function failFast(label, cond) {
  if (!cond) { console.error(`FATAL: ${label}`); process.exit(1); }
}

// ---------- estrazione blocco puro ----------
const m = /<script id="simple-formatters">([\s\S]*?)<\/script>/.exec(html);
failFast('blocco <script id="simple-formatters"> non trovato', !!m);
const ctx = { window: {}, Intl, Math, Date, isFinite, parseInt };
vm.runInNewContext(m[1], ctx);
const F = ctx.SimpleFmt || ctx.window.SimpleFmt;
failFast('SimpleFmt non esposto', F && typeof F.fmtNum === 'function');

// ---------- numeri ----------
{
  ok('fmtNum(12.345,1) === "12,3"', F.fmtNum(12.345, 1) === '12,3', F.fmtNum(12.345, 1));
  ok('fmtNum(0,1) === "0,0"', F.fmtNum(0, 1) === '0,0', F.fmtNum(0, 1));
  ok('fmtInt(62.7) === "63"', F.fmtInt(62.7) === '63', F.fmtInt(62.7));
  ok('fmtNum(null,1) === "—"', F.fmtNum(null, 1) === '—', F.fmtNum(null, 1));
  ok('fmtNum(NaN,1) === "—"', F.fmtNum(NaN, 1) === '—', F.fmtNum(NaN, 1));
  ok('fmtNum(Infinity,1) === "—"', F.fmtNum(Infinity, 1) === '—', F.fmtNum(Infinity, 1));
}

// ---------- WMO ----------
{
  ok('wmoIcon(0) === "☀️"', F.wmoIcon(0) === '☀️', F.wmoIcon(0));
  ok('wmoIcon(95) === "⛈️"', F.wmoIcon(95) === '⛈️', F.wmoIcon(95));
  ok('wmoLabel(65) cita "forte"', /forte/i.test(F.wmoLabel(65)), F.wmoLabel(65));
  ok('codice ignoto non inventa una descrizione', /Codice WMO 123/.test(F.wmoLabel(123)), F.wmoLabel(123));
}

// ---------- classi precipitazione DICHIARATE ----------
{
  ok('precipClass(0) === 0', F.precipClass(0) === 0);
  ok('precipClass(0.1) === 1', F.precipClass(0.1) === 1);
  ok('precipClass(1) === 1 (estremo superiore incluso)', F.precipClass(1) === 1);
  ok('precipClass(1.01) === 2', F.precipClass(1.01) === 2);
  ok('precipClass(5) === 2 (estremo superiore incluso)', F.precipClass(5) === 2);
  ok('precipClass(5.01) === 3', F.precipClass(5.01) === 3);
  ok('precipClass(15) === 3 (estremo superiore incluso)', F.precipClass(15) === 3);
  ok('precipClass(15.01) === 4', F.precipClass(15.01) === 4);
  ok('precipClass(30) === 4 (estremo superiore incluso)', F.precipClass(30) === 4);
  ok('precipClass(30.1) === 5', F.precipClass(30.1) === 5);
  ok('precipClass(null) === -1 (nessuna classe inventata)', F.precipClass(null) === -1);
  ok('6 classi dichiarate', F.PRECIP_CLASSES.length === 6, String(F.PRECIP_CLASSES.length));
  ok('precipColor(null) non usa un colore di classe', F.precipColor(null) === '#475569', F.precipColor(null));
}

// ---------- classi vento 5/10/15/20/25 ----------
{
  ok('windClass(4.9) === 0', F.windClass(4.9) === 0);
  ok('windClass(5) === 1', F.windClass(5) === 1);
  ok('windClass(10) === 2', F.windClass(10) === 2);
  ok('windClass(15) === 3', F.windClass(15) === 3);
  ok('windClass(20) === 4', F.windClass(20) === 4);
  ok('windClass(25) === 5', F.windClass(25) === 5);
  ok('windClass(null) === -1', F.windClass(null) === -1);
  ok('6 classi vento', F.WIND_CLASSES.length === 6);
}

// ---------- scale colore ----------
{
  const t0 = F.thermalColor(-10), t35 = F.thermalColor(35);
  ok('thermalColor(-10) === [49,54,149]', JSON.stringify(t0) === '[49,54,149]', JSON.stringify(t0));
  ok('thermalColor(35) === [215,48,39]', JSON.stringify(t35) === '[215,48,39]', JSON.stringify(t35));
  ok('rainColor(0) === null (niente colore per 0 mm)', F.rainColor(0) === null);
  ok('rainColor(30) === [219,39,119]', JSON.stringify(F.rainColor(30)) === '[219,39,119]', JSON.stringify(F.rainColor(30)));
}

// ---------- date ----------
{
  ok('fmtHour("2026-11-23T05:00") === "05:00"', F.fmtHour('2026-11-23T05:00') === '05:00');
  ok('fmtDayMonth("2026-11-23") === "11/23"', F.fmtDayMonth('2026-11-23') === '11/23', F.fmtDayMonth('2026-11-23'));
  ok('dowShort("2026-11-23") === "LUN"', F.dowShort('2026-11-23') === 'LUN', F.dowShort('2026-11-23'));
  ok('fmtClockRome(null) === "—"', F.fmtClockRome(null) === '—');
}

// ---------- distanza (IDW cutoff 30 km) ----------
{
  const d = F.haversineKm(41.89332, 12.482932, 45.4642, 9.19); // Roma-Milano
  ok('haversine Roma-Milano ≈ 477 km', d > 470 && d < 485, d.toFixed(2));
  ok('haversine stesso punto = 0', F.haversineKm(42, 12, 42, 12) === 0);
}

// ---------- struttura della pagina ----------
{
  ok('selettore "Cosa mostrare" con 4 modalità',
    /id="layer-select"[\s\S]*?value="precip"[\s\S]*?value="hydro"[\s\S]*?value="radar"[\s\S]*?value="stations"/.test(html));
  ok('selettore variabili centraline con 4 voci',
    /id="var-select"[\s\S]*?value="temp"[\s\S]*?value="rain3h"[\s\S]*?value="wind"[\s\S]*?value="gust"/.test(html));
  ok('legge i JSON reali (provinces/weekly/simple_hourly/hydro/radar/stations)',
    ['meteorisk-provinces.json', 'province_italiane.geojson', 'weekly.json',
     'simple_hourly.json', 'criticality.json', 'radar/latest.json', 'stations/latest.json']
      .every((n) => html.includes(n)));
  ok('IDW k=6 e cutoff 30 km dichiarati', /IDW k=6/.test(html) && /d > 30/.test(html));
  ok('canvas IDW 256x227', /var W = 256, H = 227/.test(html));
  ok('maschera terra via destination-in', /destination-in/.test(html));
  ok('nessun Service Worker registrato', !/serviceWorker\.register/.test(html));
  ok('link alla Pro "/" presente', /href="\/"/.test(html));
}

console.log(`\nRESULT: ${failures === 0 ? 'PASS' : 'FAIL'} (${failures} errori)`);
process.exit(failures === 0 ? 0 : 1);
