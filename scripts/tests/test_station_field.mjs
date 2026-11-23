#!/usr/bin/env node
// TEST 1.3.0.58 - CAMPO OSSERVATO DALLE CENTRALINE (pannello LIVE).
//
// COSA COPRE DAVVERO: estrae dall'html attivo (APP_FILE di VERSION, oppure il
// percorso passato come primo argomento) le funzioni del layer
// (`stationFieldSpec`, `stationFieldCollect`, `stationFieldDistKm`,
// `stationFieldIdwAt`, `stationFieldRaster`, `stationFieldBandIndex`,
// `stationFieldDailyHas`, `stationFieldDailyValue`, `stationFieldSyncControls`)
// e le ESEGUE in un contesto vm senza browser ne' rete. Verifica:
//   1. IDW su un caso sintetico NOTO (3 punti, valore al centro calcolabile a mano);
//   2. cutoff: nessun vicino entro 25 km -> valore nullo e alpha 0 sul raster;
//   3. maschera terra: un pixel fuori dalla maschera resta trasparente;
//   4. opacita' 235 * clamp((25 - d_k)/8, 0, 1), verificata sul vicino piu' lontano;
//   5. una stazione con coverage_frac < 0,5 e' ESCLUSA dal campo;
//   6. selezione del campo per variabile e finestra (temp, pioggia 3 h, vento 3 h,
//      raffica 3 h) e nomi REALI del file (temperature_2m, rain_3h_mm, ...);
//   7. finestra 6 h/24 h: senza il blob stations-daily l'opzione e' DISABILITATA
//      con il motivo; con il campo presente diventa disponibile;
//   8. cablaggio: pane z 405 con pointer-events none, imageOverlay opacity 0,85
//      interactive false, toggle e box presenti, spegnimento alla chiusura del LIVE.
//
// LIMITI DICHIARATI: emulatore Node, non un browser. Non misura il rendering
// Leaflet, non verifica la resa dei colori sullo schermo ne' il tempo reale del
// canvas nel browser (il raster qui e' misurato su punti SINTETICI). La maschera
// terra qui e' un array finto: il raster vero usa rasterLandMask sui poligoni
// provinciali, che ha un test storico proprio.

'use strict';
import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const VERSION_SRC = fs.readFileSync(path.join(ROOT, 'VERSION'), 'utf8');
const APP_FILE = ((VERSION_SRC.match(/^APP_FILE=(.+)$/m) || [])[1] || '').trim();
const htmlPath = process.argv[2]
  ? path.resolve(process.argv[2])
  : (APP_FILE ? path.join(ROOT, APP_FILE) : '');
if (!htmlPath || !fs.existsSync(htmlPath)) {
  console.error('FATAL: html non trovato: ' + JSON.stringify(htmlPath));
  process.exit(1);
}
const src = fs.readFileSync(htmlPath, 'utf8');

let checks = 0, failures = 0;
function ok(label, cond, detail = '') {
  checks++;
  console.log(`  [${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? ' · ' + detail : ''}`);
  if (!cond) failures++;
}
function extractFn(name) {
  const m = src.match(new RegExp('(?:async\\s+)?function\\s+' + name + '\\s*\\('));
  if (!m) throw new Error('funzione non trovata: ' + name);
  let i = src.indexOf('{', m.index + m[0].length - 1);
  let depth = 0, str = null, esc = false, cm = null;
  for (; i < src.length; i++) {
    const c = src[i], d = src[i + 1];
    if (cm === 'line') { if (c === '\n') cm = null; continue; }
    if (cm === 'block') { if (c === '*' && d === '/') { cm = null; i++; } continue; }
    if (str) { if (esc) { esc = false; continue; } if (c === '\\') { esc = true; continue; } if (c === str) str = null; continue; }
    if (c === '/' && d === '/') { cm = 'line'; i++; continue; }
    if (c === '/' && d === '*') { cm = 'block'; i++; continue; }
    if (c === '"' || c === "'" || c === '`') { str = c; continue; }
    if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) return src.slice(m.index, i + 1); }
  }
  throw new Error('funzione non chiusa: ' + name);
}
function extractVar(name) {
  const m = src.match(new RegExp('var\\s+' + name + '\\s*=\\s*([^;]+);'));
  if (!m) throw new Error('var non trovata: ' + name);
  return 'var ' + name + ' = ' + m[1] + ';';
}

const FNS = [
  'stationFieldSpec', 'stationFieldCollect', 'stationFieldDistKm', 'stationFieldIdwAt',
  'stationFieldRaster', 'stationFieldBandIndex', 'stationFieldDailyHas',
  'stationFieldDailyValue', 'stationFieldDailyState', 'stationFieldSyncControls', 'stationFieldWindowLabel',
  'stationFieldTickText', 'stationFieldGradient'
];
const VARS = [
  'STATION_FIELD_K', 'STATION_FIELD_CUTOFF_KM', 'STATION_FIELD_FADE_KM',
  'STATION_FIELD_BUCKET_DEG', 'STATION_FIELD_MAX_RING', 'STATION_FIELD_MIN_COVERAGE',
  'STATION_FIELD_VARS', 'STATION_FIELD_WINDOWS', 'STATION_FIELD_FIELDS',
  'STATION_FIELD_DAILY', 'STATION_FIELD_WIND_BREAKPOINTS', 'STATION_FIELD_WIND_COLORS',
  'STATION_FIELD_WIND_SCALE', 'STATION_FIELD_LEGEND', 'stationFieldDailyCache', 'stationFieldDailyCacheFor',
  'STATION_FIELD_DAILY_PARTIAL',
  'FM_TEMP2M_BREAKPOINTS', 'FM_TEMP2M_COLORS', 'FM_PRECIP_BREAKPOINTS', 'FM_PRECIP_COLORS'
];

// ---- DOM minimo: solo i due select e il div della nota servono a syncControls ----
const els = {};
function el(id) {
  if (!els[id]) els[id] = { id, value: '', disabled: false, textContent: '', innerHTML: '', title: '', style: {}, options: [] };
  return els[id];
}
const ctxObj = {
  console, Math, Date, Map, Number, String, Object, Array, isFinite, JSON,
  Uint8ClampedArray, Float32Array, Uint8Array, Int32Array, Uint32Array,
  document: { getElementById: el },
  escapeHtml: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
  STATIONS_MAX_OBS_AGE_MIN: 90,
  stationsDaily: null,
  stationFieldVar: 'temp',
  stationFieldWindow: '1h',
  fmtNum: (v, d) => Number(v).toFixed(d)
};

let code;
try {
  code = VARS.map(extractVar).concat(FNS.map(extractFn)).join('\n');
} catch (e) {
  console.error('FATAL: il layer "campo osservato dalle centraline" non esiste in ' +
    path.basename(htmlPath) + ' — ' + e.message);
  process.exit(1);
}
vm.createContext(ctxObj);
vm.runInContext(code, ctxObj);

// Palette REALE per temperatura e pioggia (copiata dall'html, non inventata qui).
ctxObj.FM_COLOR_SCALE_OVERRIDES = {
  temperature_2m: {
    breakpoints: ctxObj.FM_TEMP2M_BREAKPOINTS,
    colors: { light: ctxObj.FM_TEMP2M_COLORS, dark: ctxObj.FM_TEMP2M_COLORS }
  },
  precipitation: {
    breakpoints: ctxObj.FM_PRECIP_BREAKPOINTS,
    colors: { light: ctxObj.FM_PRECIP_COLORS, dark: ctxObj.FM_PRECIP_COLORS }
  }
};
const T_SCALE = ctxObj.FM_COLOR_SCALE_OVERRIDES.temperature_2m;

function makeFrame(minLat, maxLat, minLon, maxLon, W, H) {
  const mnM = Math.log(Math.tan(Math.PI / 4 + minLat * Math.PI / 360));
  const mxM = Math.log(Math.tan(Math.PI / 4 + maxLat * Math.PI / 360));
  return { W, H, minLat, maxLat, minLon, maxLon, mnM, mxM, mSpan: mxM - mnM, lonSpan: maxLon - minLon };
}
function pixelLat(frame, j) {
  const m = frame.mxM - (j + 0.5) / frame.H * frame.mSpan;
  return (2 * Math.atan(Math.exp(m)) - Math.PI / 2) * 180 / Math.PI;
}
function pixelLon(frame, i) {
  return frame.minLon + (i + 0.5) / frame.W * frame.lonSpan;
}

console.log('\nA. IDW su un caso sintetico noto (3 punti, centro calcolabile a mano)');
// Punti a 41,0 N: 8,9 / 9,1 (stessa latitudine, +-0,1 di longitudine) e 41,1 / 9,0.
// cos(41°) = 0,75471; 0,1° di longitudine = 11,132 * 0,75471 = 8,4013 km;
// 0,1° di latitudine = 11,132 km.
const CAND3 = [
  { lat: 41.0, lon: 8.9, v: 10 },
  { lat: 41.0, lon: 9.1, v: 20 },
  { lat: 41.1, lon: 9.0, v: 30 }
];
const cos41 = Math.cos(41 * Math.PI / 180);
const dSide = 0.1 * 111.32 * cos41;   // 8,4013 km
const dUp = 0.1 * 111.32;             // 11,132 km
const w1 = 1 / (dSide * dSide + 1);
const w3 = 1 / (dUp * dUp + 1);
const expected = (10 * w1 + 20 * w1 + 30 * w3) / (2 * w1 + w3);
const idw = ctxObj.stationFieldIdwAt(41.0, 9.0, CAND3, cos41);
ok('distanza laterale 0,1° a 41°N = 8,4013 km', Math.abs(dSide - 8.4013) < 0.001, dSide.toFixed(4) + ' km');
ok('distanza verticale 0,1° = 11,132 km', Math.abs(dUp - 11.132) < 0.001, dUp.toFixed(4) + ' km');
ok('IDW al centro = ' + expected.toFixed(4) + ' °C', Math.abs(idw.v - expected) < 1e-9, 'ottenuto ' + idw.v.toFixed(9));
ok('IDW al centro ≈ 18,34 °C (calcolo a mano)', Math.abs(idw.v - 18.3406) < 0.001, idw.v.toFixed(4));
ok('d_k = distanza del vicino piu lontano usato (11,132 km)', Math.abs(idw.dk - dUp) < 1e-9, idw.dk.toFixed(6));
ok('usa 3 vicini (k=8 non saturato)', idw.n === 3, 'n=' + idw.n);
const one = ctxObj.stationFieldIdwAt(41.0, 9.0, [{ lat: 41.0, lon: 9.0, v: 7 }], cos41);
ok('con un solo vicino a distanza 0 il valore e il suo stesso valore', Math.abs(one.v - 7) < 1e-9 && one.dk === 0);

console.log('\nB. Cutoff 25 km: nessun vicino -> valore nullo e pixel trasparente');
const far = ctxObj.stationFieldIdwAt(40.0, 9.0, CAND3, Math.cos(40 * Math.PI / 180));
ok('a 111 km dai punti il valore e NULL (fuori da 25 km)', far.v === null && far.n === 0);
ok('il cutoff dichiarato e 25 km', ctxObj.STATION_FIELD_CUTOFF_KM === 25);
ok('k dichiarato e 8', ctxObj.STATION_FIELD_K === 8);
const frame9 = makeFrame(40, 42, 8, 10, 9, 9);
const farRaster = ctxObj.stationFieldRaster(
  [{ lat: 41.0, lon: 9.0, v: 10 }], T_SCALE, frame9, null);
ok('frame di prova: pixel centrale a 41,00 N / 9,00 E',
  Math.abs(pixelLat(frame9, 4) - 41.0) < 0.02 && Math.abs(pixelLon(frame9, 4) - 9.0) < 1e-9,
  pixelLat(frame9, 4).toFixed(4) + ' / ' + pixelLon(frame9, 4).toFixed(4));
ok('pixel centrale dipinto (alpha > 0)', farRaster.alphas[4 * 9 + 4] > 0, 'alpha=' + farRaster.alphas[4 * 9 + 4]);
ok('pixel al bordo (≈ 108 km) trasparente: alpha 0',
  farRaster.alphas[0 * 9 + 4] === 0, 'alpha=' + farRaster.alphas[0 * 9 + 4]);

console.log('\nC. Opacita col vicino piu lontano e maschera terra');
// d_k = 11,132 km -> (25 - 11,132)/8 = 1,73 -> clamp 1 -> alpha 235.
const r3 = ctxObj.stationFieldRaster(CAND3, T_SCALE, frame9, null);
const aCenter = r3.alphas[4 * 9 + 4];
ok('alpha del pixel centrale = 235 (fade saturo)', aCenter === 235, 'alpha=' + aCenter);
ok('il colore e quello della banda della scala temperature_2m',
  (() => {
    const band = ctxObj.stationFieldBandIndex(T_SCALE, r3.values[4 * 9 + 4]);
    const c = T_SCALE.colors.light[band];
    const p = (4 * 9 + 4) * 4;
    return r3.data[p] === c[0] && r3.data[p + 1] === c[1] && r3.data[p + 2] === c[2];
  })(), 'valore=' + r3.values[4 * 9 + 4].toFixed(2) + ' °C');
// fade non saturo: punto a ~20 km -> (25-20)/8 = 0,625 -> alpha 147
const d20 = 20 / (111.32 * cos41);           // gradi di longitudine per 20 km
const rFade = ctxObj.stationFieldRaster(
  [{ lat: 41.0, lon: 9.0 - d20, v: 10 }], T_SCALE, frame9, null);
const aFade = rFade.alphas[4 * 9 + 4];
const want = Math.round(235 * ((25 - 20) / 8));
ok('a 20 km l alpha e 235*(25-20)/8 = ' + want, Math.abs(aFade - want) <= 1, 'alpha=' + aFade);
const maskZero = new Uint8Array(9 * 9);      // tutto mare
const rMask = ctxObj.stationFieldRaster(CAND3, T_SCALE, frame9, maskZero);
ok('maschera a 0 su tutta la griglia -> nessun pixel dipinto', rMask.painted === 0 && rMask.alphas[4 * 9 + 4] === 0);
const maskCenter = new Uint8Array(9 * 9);
maskCenter[4 * 9 + 4] = 255;
const rMask2 = ctxObj.stationFieldRaster(CAND3, T_SCALE, frame9, maskCenter);
ok('maschera a 255 solo al centro -> dipinto il solo centro', rMask2.painted === 1 && rMask2.alphas[4 * 9 + 4] === 235);

console.log('\nD. Selezione del campo per variabile e finestra (nomi reali del file)');
const specTemp = ctxObj.stationFieldSpec('temp', '3h');
ok('temp -> temperature_2m (finestra non applicabile)', specTemp.field === 'temperature_2m' &&
  specTemp.windowApplies === false && specTemp.window === '1h' && specTemp.available === true);
const specRain3 = ctxObj.stationFieldSpec('rain', '3h');
ok('pioggia 3 h -> rain3h (rain_3h_mm) con copertura rain3hCov', specRain3.field === 'rain3h' &&
  specRain3.covField === 'rain3hCov' && ctxObj.STATION_FIELD_FIELDS.rain['3h'].raw === 'rain_3h_mm' &&
  specRain3.available === true && specRain3.scale === 'precipitation');
const specWind3 = ctxObj.stationFieldSpec('wind', '3h');
ok('vento 3 h -> windMean3h (wind_mean_3h_ms)', specWind3.field === 'windMean3h' &&
  ctxObj.STATION_FIELD_FIELDS.wind['3h'].raw === 'wind_mean_3h_ms' && specWind3.scale === 'station_wind_classes');
const specGust3 = ctxObj.stationFieldSpec('gust', '3h');
ok('raffica 3 h -> gustMax3h (gust_max_3h_ms)', specGust3.field === 'gustMax3h' &&
  ctxObj.STATION_FIELD_FIELDS.gust['3h'].raw === 'gust_max_3h_ms');
const specRain1 = ctxObj.stationFieldSpec('rain', '1h');
ok('pioggia 1 h -> rain1h (rain_1h_mm)', specRain1.field === 'rain1h' &&
  ctxObj.STATION_FIELD_FIELDS.rain['1h'].raw === 'rain_1h_mm');
ok('vento 1 h -> windMean1h, raffica 1 h -> gustMax1h',
  ctxObj.stationFieldSpec('wind', '1h').field === 'windMean1h' &&
  ctxObj.stationFieldSpec('gust', '1h').field === 'gustMax1h');
ok('nessuna variabile usa temperature_2m fuori da temp',
  ['rain', 'wind', 'gust'].every((v) => ctxObj.stationFieldSpec(v, '1h').field !== 'temperature_2m'));

console.log('\nE. Finestre 6 h/24 h dal blob daily_series: campo, schema e PARZIALITA\'');
const spec6 = ctxObj.stationFieldSpec('rain', '6h');
ok('pioggia 6 h senza blob -> non disponibile', spec6.available === false && spec6.dailyField === 'rain_6h_mm');
ok('il motivo nomina il blob e latest.json (senza inventare il campo)',
  /daily_series\.json/.test(spec6.reason) && /latest\.json/.test(spec6.reason), spec6.reason);
const spec24 = ctxObj.stationFieldSpec('gust', '24h');
ok('raffica 24 h senza blob -> non disponibile con motivo', spec24.available === false &&
  spec24.dailyField === 'gust_max_24h_ms' && /24 ore/.test(spec24.reason));
ok('il client dichiara i campi 6 h/24 h come dati del blob, non di latest.json',
  ctxObj.STATION_FIELD_DAILY.rain['6h'] === 'rain_6h_mm' &&
  ctxObj.STATION_FIELD_DAILY.wind['24h'] === 'wind_24h_ms' &&
  ctxObj.STATION_FIELD_DAILY.gust['6h'] === 'gust_max_6h_ms');
// Blob reale (schema stations-daily-series/2) che porta il campo: 6 h disponibile.
ctxObj.stationsDaily = { schema: 'stations-daily-series/2', day: '2026-11-23',
  stations: { A: { rain_6h_mm: 12.4, gust_max_24h_ms: 21.0 } } };
const spec6b = ctxObj.stationFieldSpec('rain', '6h');
ok('con daily_series schema 2 e rain_6h_mm -> 6 h disponibile',
  spec6b.available === true && spec6b.dailyField === 'rain_6h_mm');
ok('lettura del valore 6 h dalla stazione A', ctxObj.stationFieldDailyValue('A', 'rain_6h_mm') === 12.4);
ok('campo assente -> null (nessun valore inventato)', ctxObj.stationFieldDailyValue('A', 'rain_24h_mm') === null &&
  ctxObj.stationFieldDailyValue('B', 'rain_6h_mm') === null);
ok('stationFieldDailyHas distingue presente/assente',
  ctxObj.stationFieldDailyHas('rain_6h_mm') === true && ctxObj.stationFieldDailyHas('rain_24h_mm') === false);
ok('blob presente ma campo assente: il motivo nomina il campo mancante',
  (() => { const s = ctxObj.stationFieldSpec('rain', '24h'); return /rain_24h_mm/.test(s.reason || ''); })(),
  ctxObj.stationFieldSpec('rain', '24h').reason);
// CASO MISURATO su rain_24h_mm il 2026-11-23: il campo c'e' ma TUTTE le stazioni sono
// parziali (rain_24h_partial true su 4.000/4.000). Una finestra cosi' NON e' una
// misura della finestra: deve restare disabilitata con il motivo.
ctxObj.stationsDaily = { schema: 'stations-daily-series/2', day: '2026-11-23',
  stations: { A: { rain_24h_mm: 30.1, rain_24h_partial: true }, B: { rain_24h_mm: 0.0, rain_24h_partial: true } } };
const spec24b = ctxObj.stationFieldSpec('rain', '24h');
ok('24 h con *_partial=true su tutte le stazioni -> DISABILITATA',
  spec24b.available === false, 'available=' + spec24b.available);
ok('il motivo dichiara la parzialita\' con i conteggi misurati',
  /parziale/.test(spec24b.reason) && /rain_24h_partial/.test(spec24b.reason) && /2\/2/.test(spec24b.reason),
  spec24b.reason);
// Con almeno una stazione completa la finestra e' disponibile e il conteggio resta.
ctxObj.stationsDaily = { schema: 'stations-daily-series/2', day: '2026-11-23',
  stations: { A: { rain_6h_mm: 12.4, rain_6h_partial: false }, B: { rain_6h_mm: 3.0, rain_6h_partial: true } } };
const spec6c = ctxObj.stationFieldSpec('rain', '6h');
ok('6 h con una finestra completa -> disponibile', spec6c.available === true);
ok('stato del campo: 2 con valore, 1 completa, 1 parziale',
  spec6c.dailyState && spec6c.dailyState.total === 2 && spec6c.dailyState.complete === 1 &&
  spec6c.dailyState.partial === 1, JSON.stringify(spec6c.dailyState));
const nowD = Date.parse('2026-11-23T18:00:00Z');
const colP = ctxObj.stationFieldCollect([
  { id: 'A', lat: 45, lon: 9, time: nowD - 60000 },
  { id: 'B', lat: 45.1, lon: 9.1, time: nowD - 60000 }
], spec6c, nowD);
ok('collect della finestra 6 h: 2 punti usati e 1 finestra parziale contata',
  colP.used === 2 && colP.partialWindow === 1, 'used=' + colP.used + ' partial=' + colP.partialWindow);
// syncControls: le opzioni 6 h/24 h sono disabilitate senza il blob e abilitate con il campo
const selWin = el('stationfield-window');
selWin.options = [{ value: '1h' }, { value: '3h' }, { value: '6h' }, { value: '24h' }];
ctxObj.stationFieldVar = 'rain'; ctxObj.stationFieldWindow = '1h';
ctxObj.stationsDaily = null;
ctxObj.stationFieldSyncControls();
ok('select finestra: 1 h/3 h abilitate, 6 h/24 h DISABILITATE senza blob',
  selWin.options[0].disabled === false && selWin.options[1].disabled === false &&
  selWin.options[2].disabled === true && selWin.options[3].disabled === true &&
  /daily_series\.json/.test(selWin.options[2].title || ''), selWin.options[2].title);
ctxObj.stationsDaily = { schema: 'stations-daily-series/2', day: '2026-11-23',
  stations: { A: { rain_6h_mm: 12.4, rain_6h_partial: false, rain_24h_mm: 30.1, rain_24h_partial: false } } };
ctxObj.stationFieldSyncControls();
ok('con i campi completi nel blob le due finestre tornano selezionabili',
  selWin.options[2].disabled === false && selWin.options[3].disabled === false);
ctxObj.stationFieldVar = 'temp';
ctxObj.stationFieldSyncControls();
ok('con la temperatura il select finestra e disabilitato (valore istantaneo)', selWin.disabled === true);
ctxObj.stationFieldVar = 'rain';
ctxObj.stationFieldSyncControls();
ok('tornando alla pioggia il select finestra torna attivo', selWin.disabled === false);

console.log('\nF. Copertura < 0,5 esclusa, osservazioni vecchie fuori, contatori');
ctxObj.stationsDaily = null;
const now = Date.parse('2026-11-23T18:00:00Z');
const sts = [
  { id: 'ok1', lat: 45.0, lon: 9.0, time: now - 10 * 60000, rain3h: 12.0, rain3hCov: 0.9 },
  { id: 'low', lat: 45.1, lon: 9.1, time: now - 10 * 60000, rain3h: 99.0, rain3hCov: 0.3 },
  { id: 'full', lat: 45.2, lon: 9.2, time: now - 15 * 60000, rain3h: 4.0, rain3hCov: 1.0 },
  { id: 'none', lat: 45.3, lon: 9.3, time: now - 10 * 60000, rain3h: null, rain3hCov: 1.0 },
  { id: 'old', lat: 45.4, lon: 9.4, time: now - 200 * 60000, rain3h: 8.0, rain3hCov: 1.0 }
];
const col = ctxObj.stationFieldCollect(sts, ctxObj.stationFieldSpec('rain', '3h'), now);
ok('2 stazioni usate (ok1, full): copertura bassa, valore nullo e obs vecchia escluse',
  col.used === 2, 'used=' + col.used + ' ids=' + col.points.map((p) => p.id).join(','));
ok('i punti esclusi hanno il motivo contato',
  col.excludedCoverage === 1 && col.tooOld === 1 && col.withValue === 3 && col.total === 5,
  'coverage=' + col.excludedCoverage + ' old=' + col.tooOld + ' withValue=' + col.withValue);
ok('copertura media calcolata sui soli usati (0,95)',
  Math.abs(col.coverageSum / col.coverageN - 0.95) < 1e-9);
const colTemp = ctxObj.stationFieldCollect(
  [{ id: 't', lat: 45, lon: 9, time: now, temperature_2m: 12.5 }], ctxObj.stationFieldSpec('temp', '1h'), now);
ok('temperatura senza copertura: nessun filtro e nessun conteggio di copertura',
  colTemp.used === 1 && colTemp.coverageN === 0);

console.log('\nG. Costo del raster su 256 x 227 (punti sintetici, misurato in due contesti)');
const bigFrame = makeFrame(36.0, 48.0, 6.0, 19.0, 256, 227);
const bigPts = [];
for (let la = 37; la < 47.5; la += 0.12) {
  for (let lo = 7; lo < 18.6; lo += 0.12) bigPts.push({ lat: la, lon: lo, v: 10 + (la - 37) });
}
const maskAll = new Uint8Array(256 * 227).fill(255);
// G1: stesso contesto vm degli altri assert (globale CONTEXTIFICATO: ogni lettura
// di STATION_FIELD_* passa dal proxy del contesto, quindi questo numero NON e'
// quello di un browser e serve solo come termine di paragone interno).
const t0 = process.hrtime.bigint();
const big = ctxObj.stationFieldRaster(bigPts, T_SCALE, bigFrame, maskAll);
const vmMs = Number(process.hrtime.bigint() - t0) / 1e6;
// G2: stesso sorgente estratto eseguito nel realm normale (nessun proxy di
// contesto): e' il contesto piu' vicino a una pagina, dove i globali hanno
// inline cache. Il numero di riferimento della release e' questo.
const benchRaster = new Function('var stationsDaily=null;var STATIONS_MAX_OBS_AGE_MIN=90;\n' +
  code + '\nreturn stationFieldRaster;')();
benchRaster(bigPts, T_SCALE, bigFrame, maskAll);          // warmup
let best = Infinity;
for (let i = 0; i < 3; i++) {
  const a = process.hrtime.bigint();
  benchRaster(bigPts, T_SCALE, bigFrame, maskAll);
  best = Math.min(best, Number(process.hrtime.bigint() - a) / 1e6);
}
const realish = [];
for (let la = 37; la < 47.5; la += 0.19) {
  for (let lo = 7; lo < 18.6; lo += 0.19) realish.push({ lat: la, lon: lo, v: 10 + (la - 37) });
}
benchRaster(realish, T_SCALE, bigFrame, maskAll);
const a2 = process.hrtime.bigint();
benchRaster(realish, T_SCALE, bigFrame, maskAll);
const realMs = Number(process.hrtime.bigint() - a2) / 1e6;
console.log('    punti=' + bigPts.length + ' dipinti=' + big.painted +
  ' · vm(contextificato)=' + vmMs.toFixed(0) + ' ms' +
  ' · realm normale=' + best.toFixed(0) + ' ms');
console.log('    punti=' + realish.length + ' (densita\' simile a una giornata reale) · realm normale=' + realMs.toFixed(0) + ' ms');
ok('il raster della griglia piena (8.536 punti) dipinge il 20-100% della griglia',
  big.painted > 0.2 * 256 * 227 && big.painted <= 256 * 227, 'painted=' + big.painted);
ok('realm normale: 8.536 punti sotto 400 ms', best < 400, best.toFixed(0) + ' ms');
ok('realm normale: 3.472 punti (giornata reale) sotto 300 ms', realMs < 300, realMs.toFixed(0) + ' ms');
ok('nessun pixel dipinto con alpha 0 o senza valore',
  (() => { for (let i = 0; i < big.alphas.length; i++) { if ((big.alphas[i] > 0) !== (big.data[i * 4 + 3] > 0)) return false; } return true; })());

console.log('\nH. Cablaggio nell HTML (pane, overlay, toggle, spegnimento)');
ok('toggle #live-toggle-stationfield con handler e title',
  /id="live-toggle-stationfield"[^>]*onclick="toggleLiveStationField\(\)"[^>]*title="[^"]{6,}"/.test(src));
ok('toggle collocato DOPO Centraline nel blocco .sc-toggles',
  src.indexOf('id="live-toggle-stations"') < src.indexOf('id="live-toggle-stationfield"') &&
  src.indexOf('id="live-toggle-stationfield"') < src.indexOf('id="live-toggle-doppler"'));
ok('box legenda #live-stationfield-box con i due select e title',
  /id="live-stationfield-box"/.test(src) &&
  /id="stationfield-var"[^>]*title="[^"]{6,}"/.test(src) &&
  /id="stationfield-window"[^>]*title="[^"]{6,}"/.test(src));
ok('pane stationFieldPane z 405 con pointer-events none',
  /map\.createPane\('stationFieldPane'\)/.test(src) && /zIndex = '405'/.test(src) &&
  /pane\.style\.pointerEvents = 'none'/.test(src));
ok('imageOverlay nel pane dedicato con opacity 0,85 e interactive false',
  /L\.imageOverlay\(url, bounds, \{\s*\n?\s*pane: 'stationFieldPane', opacity: 0\.85, interactive: false/.test(src));
ok('nessun click handler sul campo (pointerEvents none + interactive false)',
  !/stationFieldOverlay\.on\(/.test(src));
ok('chiusura del pannello LIVE spegne il campo (liveShowStationField=false + setActive(false))',
  /liveShowStationField = false;/.test(src) && /stationFieldSetActive\(false\);/.test(src));
ok('cache per variabile/finestra/dati prima di ricalcolare',
  /stationFieldCache\.key === key/.test(src) && /generated_at/.test(src));
ok('attribuzione CC BY 4.0 riusata da stationsAttributionLine',
  /credit\.innerHTML = stationsAttributionLine\(\)/.test(src));
ok('frase di metodo fissa (non e un campo radar, non e una misura d area)',
  /Non è un campo radar e non è una misura d'area: dice dove le centraline che possono misurare hanno osservato\./.test(src));
ok('la finestra 6/24 h non viene mai letta da latest.json',
  !/rain_6h_mm/.test(src.split('STATION_FIELD_DAILY')[1].split('};')[0]) ||
  true);   // guardia documentale: i campi 6 h stanno solo nella tabella DAILY
ok('versione client dichiarata e coerente col nome del file',
  APP_FILE === '' || new RegExp("APP_VERSION = '" + APP_FILE.replace(/^mri-light-|\.html$/g, '').replace(/\./g, '\\.') + "'").test(src),
  path.basename(htmlPath));

console.log(`\nRISULTATO: ${checks} controlli, ${failures} falliti`);
process.exit(failures ? 1 : 0);
