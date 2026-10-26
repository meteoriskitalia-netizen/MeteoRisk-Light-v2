#!/usr/bin/env node
// TEST 1.2.4.13 — MODULO FM "WEBP AUTO-SUFFICIENTI": i toggle opzionali (Isolinee /
// Frecce vento / Griglia / Palette scura) e la densita' delle isolinee sono RIMOSSI.
//
// CONTESTO: il renderer produce WebP auto-sufficienti (isolinee dense con i valori scritti
// sopra + frecce del vento) e solo il tema `light`. Quindi nel client:
//   - i 4 checkbox e la select della densita' non esistono piu' (nessun id residuo);
//   - il fast-path WebP non puo' piu' essere disabilitato da un toggle;
//   - il tema del WebP e' la costante `light` (il tema scuro non esiste lato server);
//   - la via di fallback WML/.om chiede SEMPRE isolinee + frecce vento, MAI la griglia;
//   - gli URL del modulo FM non portano piu' il flag di tema scuro.
//
// LIMITI: statico sul sorgente + behavior sul blocco FM reale valutato in una VM
// (fmWebpTheme / fmVectorUrl / fmContourInterval / fmTogglesChanged / fmDensityChanged).
// NON esegue il rendering in browser e NON tocca il server.
'use strict';

import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const HTML = path.join(ROOT, 'mri-light-1.2.4.36.html');
const src = fs.readFileSync(HTML, 'utf8');

let failures = 0;
let checks = 0;
function ok(label, cond, detail = '') {
  checks++;
  console.log(`  [${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? ` · ${detail}` : ''}`);
  if (!cond) failures++;
}
function failFast(label, cond) {
  if (!cond) { console.error(`FATAL: ${label}`); process.exit(1); }
}
const has = (re, inStr = src) => re.test(inStr);

// ---------- ESTRAZIONE BLOCCO FM ----------
const MARK_START = 'MAPPE PREVISIONALI (Open-Meteo Weather Map Layer)';
const MARK_END = 'Toggle Sviluppo: mostra/nasconde';
const bStart = src.indexOf(MARK_START);
const bEnd = src.indexOf(MARK_END);
failFast('marcatore iniziale del blocco FM non trovato', bStart >= 0);
failFast('marcatore finale del blocco FM non trovato', bEnd > bStart);
const lineStart = src.lastIndexOf('\n', bStart) + 1;
const endLineStart = src.lastIndexOf('\n', bEnd) + 1;
const moduleSrc = src.slice(lineStart, endLineStart);
failFast('blocco FM vuoto', moduleSrc.length > 0);

// ---------- A. UI: I CONTROLLI OPZIONALI NON ESISTONO PIU' ----------
for (const id of ['fm-chk-contours', 'fm-chk-arrows', 'fm-chk-grid', 'fm-chk-dark', 'fm-density-select']) {
  ok(`A: id="${id}" assente dall'HTML`,
    !src.includes(`id="${id}"`) && !src.includes(`id='${id}'`));
}
ok('A: nessun riferimento residuo a "fm-chk" o "fm-density" nel file',
  !/fm-chk|fm-density/.test(src));
ok('A: CSS morto rimosso (.fm-chk / .fm-density / .fm-display)',
  !/\.fm-chk\b/.test(src) && !/\.fm-density\b/.test(src) && !/\.fm-display\b/.test(src));
ok('A: le 3 select del pannello FM sono ancora al loro posto',
  ['fm-domain-select', 'fm-variable-select', 'fm-region-select'].every((id) => src.includes(`id="${id}"`)));
ok('A: la barra strumenti FM conserva la riga del player (layout non rotto)',
  /class="fm-player"/.test(src) && /id="fm-play-btn"/.test(src) && /id="fm-slider"/.test(src));

// ---------- B. STATO: IL CONTRATTO E' UNA COSTANTE ----------
ok('B: costante FM_OPTS definita con il contratto fisso',
  /var\s+FM_OPTS\s*=\s*\{\s*contours:\s*true,\s*arrows:\s*true,\s*arrowStyle:\s*'arrow',\s*grid:\s*false,\s*dark:\s*false,\s*density:\s*'fitta'\s*\}/.test(src));
ok('B: il blocco FM non usa piu\' alcuno stato "fmOpts" variabile',
  !/fmOpts/.test(moduleSrc));
ok('B: nessun gate "fmOpts.contours/arrows/grid/dark/density" nel blocco FM',
  !/fmOpts\.(contours|arrows|grid|dark|density)/.test(moduleSrc));
ok('B: l\'helper fmGetChk (solo checkbox) rimosso',
  !/function\s+fmGetChk\b/.test(src));

// ---------- C. FAST-PATH WEBP: NON PIU' DISABILITATO DA UN TOGGLE ----------
ok('C: wantWebp = !fmOverlayVariable (o eccezione regione "north"), nessun gate sui toggle',
  /var\s+wantWebp\s*=\s*!fmOverlayVariable\s*(\|\|\s*fmRegion\s*===\s*'north'\s*)?;/.test(moduleSrc));
ok('C: wantWebp non dipende da contours/arrows/grid',
  !/wantWebp\s*=[^;]*fmOpts/.test(moduleSrc));
ok('C: il guard sull\'overlay isobare MSL resta (fmOverlayVariable)',
  /wantWebp\s*=\s*!fmOverlayVariable/.test(moduleSrc) && /var\s+fmOverlayVariable\s*=/.test(moduleSrc));

// ---------- D. TEMA: SOLO "light" ----------
ok('D: fmWebpTheme() ritorna la costante "light"',
  /function\s+fmWebpTheme\s*\(\s*\)\s*\{\s*return\s+'light'\s*;\s*\}/.test(moduleSrc));

// ---------- E. URL: NESSUN FLAG DI TEMA SCURO ----------
ok('E: il blocco FM non costruisce piu\' URL con il flag di tema scuro',
  !/dark=true/.test(moduleSrc));
ok('E: il file non contiene piu\' "dark=true"',
  !/dark=true/.test(src));
ok('E: nessuna opzione "theme=dark" negli URL del modulo FM',
  !/theme=dark/.test(src));

// ---------- F. VM: BEHAVOR REALE DEL BLOCCO FM ----------
const ctx = {
  document: { getElementById() { return null; } },
  // Il blocco FM registra un listener 'resize' a livello di modulo:
  // senza stub su window l'eval muore con "window.addEventListener is not a function"
  // e il test non arriva a eseguire nessuna asserzione.
  window: { addEventListener() {}, removeEventListener() {} },
  L: undefined,
  isFeatureEnabled() { return true; },
  fetch() { throw new Error('fetch non consentito nei test'); },
  setInterval() { return 0; },
  clearInterval() {},
  AbortController: undefined,
  console,
};
try {
  vm.runInNewContext(moduleSrc, ctx, { filename: 'forecastMaps.js' });
} catch (e) {
  failFast('eval del blocco FM fallito: ' + e.message + '\n' + (e.stack || ''), false);
}
for (const fn of ['fmWebpTheme', 'fmVectorUrl', 'fmContourInterval', 'fmTogglesChanged', 'fmDensityChanged']) {
  failFast(`${fn} non estratta dal blocco FM`, typeof ctx[fn] === 'function');
}

// D (behavior): il tema resta "light" anche se un residuo legacy provasse a chiedere il dark.
ctx.fmOpts = { dark: true };
ok('D: fmWebpTheme() == "light" anche con un fmOpts.dark legacy = true',
  ctx.fmWebpTheme() === 'light', ctx.fmWebpTheme());

// F (behavior): fmVectorUrl chiede SEMPRE isolinee + frecce vento e MAI la griglia.
ctx.fmVariable = 'temperature_2m';
const BASE_URL = 'https://example.org/data/fm2/2026-10-10T2300.om?variable=temperature_2m';
const vurl = ctx.fmVectorUrl(BASE_URL);
ok('F: fmVectorUrl non restituisce mai null (il fallback ha sempre il layer vettoriale)',
  typeof vurl === 'string' && vurl.length > 0, String(vurl));
ok('F: fmVectorUrl contiene contours=true', vurl.includes('contours=true'), vurl);
ok('F: fmVectorUrl contiene arrows=true', vurl.includes('arrows=true'), vurl);
ok('F: fmVectorUrl contiene arrow_style=arrow', vurl.includes('arrow_style=arrow'), vurl);
ok('F: fmVectorUrl NON contiene mai grid=true', !vurl.includes('grid=true'), vurl);
ok('F: fmVectorUrl contiene intervals=<passo della variabile>',
  vurl.includes('intervals=1'), vurl);

// F (behavior): il contratto NON e' configurabile (FM_OPTS forzato a valori "off").
ctx.FM_OPTS.contours = false;
ctx.FM_OPTS.arrows = false;
ctx.FM_OPTS.grid = true;
ctx.FM_OPTS.dark = true;
const vurlForced = ctx.fmVectorUrl(BASE_URL);
ok('F: il contratto non e\' configurabile: FM_OPTS non cambia l\'URL del fallback',
  vurlForced.includes('contours=true') && vurlForced.includes('arrows=true') &&
  !vurlForced.includes('grid=true'), vurlForced);

// F (behavior): l'overlay MSL mantiene il passo sinottico di 5 hPa.
const vurlMsl = ctx.fmVectorUrl(BASE_URL, 'pressure_msl', true, 5);
ok('F: overlay MSL: intervals=5 (passo sinottico) + contours + arrows, mai grid',
  vurlMsl.includes('contours=true') && vurlMsl.includes('intervals=5') &&
  vurlMsl.includes('arrows=true') && !vurlMsl.includes('grid=true'), vurlMsl);

// F (behavior): fmContourInterval — valori IDENTICI alla tabella pre-1.2.4.13
// (il fattore di densita' x3/x6 e' stato rimosso: la densita' e' fissa "fitta" -> x1).
const EXPECTED_INTERVALS = [
  ['temperature_2m', 1],
  ['apparent_temperature', 1],
  ['wet_bulb_temperature_2m', 1],
  ['geopotential_height_500hPa', 40],
  ['geopotential_height_850hPa', 40],
  ['geopotential_height_1000hPa', 40],
  ['cloud_cover', 10],
  ['snow_depth', 3],
  ['wind_speed_10m', 2],
  ['wind_gusts_10m', 2],
  ['visibility', 2],
  ['relative_humidity_2m', 5],
  ['relative_humidity_700hPa', 5],
  ['pressure_msl', 5],
  ['surface_pressure', 5],
  ['precipitation', 1],
  ['rain', 1],
  ['showers', 1],
  ['snowfall', 1],
  ['freezing_rain', 1],
  ['graupel', 1],
  ['dew_point_2m', 2],
  ['temperature_850hPa', 2],
];
for (const [v, expected] of EXPECTED_INTERVALS) {
  const got = ctx.fmContourInterval(v);
  ok(`F: fmContourInterval("${v}") == ${expected} (identico alla versione con densita' "fitta")`,
    got === expected, `got=${got}`);
}
ok('F: nessun fattore di densita\' (x3/x6) nel codice delle isolinee',
  !/density\s*===\s*'media'/.test(moduleSrc) && !/density\s*===\s*'sparsa'/.test(moduleSrc));

// G: i handler dei toggle rimossi non devono leggere id inesistenti ne' lanciare eccezioni.
let rebuildCalls = 0;
ctx.fmRebuild = function () { rebuildCalls++; };
ctx.fmTogglesChanged();
ctx.fmDensityChanged();
ok('G: fmTogglesChanged/fmDensityChanged non lanciano eccezioni e ricostruiscono il layer',
  rebuildCalls === 2, `rebuild=${rebuildCalls}`);
ok('G: i handler non leggono piu\' checkbox/select rimossi',
  !/fmGetChk|fmEl\('fm-chk|fmEl\('fm-density/.test(moduleSrc));

// ---------- ESITO ----------
console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'}: ${checks - failures}/${checks} controlli superati`);
process.exit(failures === 0 ? 0 : 1);
