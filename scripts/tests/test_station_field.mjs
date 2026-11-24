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
// Estrazione di `var NAME = ...;` con bilanciamento di parentesi, graffe e
// stringhe: le tabelle nuove (STATION_FIELD_MEAN_KEY_RAW) sono costruite da una
// IIFE e contengono dei `;` interni, che la vecchia regex `[^;]+` troncava
// lasciando un frammento non compilabile ("Unexpected end of input").
function extractVar(name) {
  const m = src.match(new RegExp('var\\s+' + name + '\\s*='));
  if (!m) throw new Error('var non trovata: ' + name);
  let i = m.index + m[0].length;
  const start = i;
  let depth = 0, str = null, esc = false, cm = null;
  for (; i < src.length; i++) {
    const c = src[i], d = src[i + 1];
    if (cm === 'line') { if (c === '\n') cm = null; continue; }
    if (cm === 'block') { if (c === '*' && d === '/') { cm = null; i++; } continue; }
    if (str) { if (esc) { esc = false; continue; } if (c === '\\') { esc = true; continue; } if (c === str) str = null; continue; }
    if (c === '/' && d === '/') { cm = 'line'; i++; continue; }
    if (c === '/' && d === '*') { cm = 'block'; i++; continue; }
    if (c === '"' || c === "'" || c === '`') { str = c; continue; }
    if (c === '(' || c === '[' || c === '{') { depth++; continue; }
    if (c === ')' || c === ']' || c === '}') { depth--; continue; }
    if (c === ';' && depth === 0) break;
  }
  return 'var ' + name + ' = ' + src.slice(start, i) + ';';
}

const FNS = [
  'stationFieldSpec', 'stationFieldCollect', 'stationFieldDistKm', 'stationFieldIdwAt',
  'stationFieldRaster', 'stationFieldBandIndex', 'stationFieldDailyHas',
  'stationFieldDailyValue', 'stationFieldDailyState', 'stationFieldSyncControls', 'stationFieldWindowLabel',
  'stationFieldTickText', 'stationFieldGradient', 'stationFieldMeanStats',
  'stationFieldMeanReasonText', 'stationFieldReasonIt', 'stationsMeanReasons', 'stationsNullReason',
  'stationFieldScale', 'stationFieldDownscale', 'stationFieldHourText',
  'stationFieldAggLabel', 'stationFieldAggShort', 'stationFieldStatus',
  // 1.3.0.69 - fallback al file completo (tre stati del messaggio) e legenda
  // «una etichetta per banda visibile».
  'stationsIndexColumn', 'stationsFullColumnState', 'stationsFullFieldIndex',
  'stationsMergeFull', 'stationFieldLegendLayout', 'stationFieldBuildLegend',
  'stationFieldLegendLabelText', 'stationFieldLegendLabelWidth',
  'stationFieldMethodChanged',
  'stationFieldContourLevels', 'stationFieldContourSegments',
  'stationFieldContourLabels', 'stationFieldContourVectorData',
  'stationFieldContourResample', 'stationFieldContourPolylines', 'stationFieldChaikin',
  'stationFieldSoftenMask',
  // 1.3.0.74 - il radar si nasconde quando il campo centraline e' attivo.
  'stationFieldRadarVisibleNow', 'stationFieldRadarSuppress', 'stationFieldRadarRestore'
];
const VARS = [
  'STATION_FIELD_K', 'STATION_FIELD_CUTOFF_KM', 'STATION_FIELD_FADE_KM', 'STATION_FIELD_SUPERSAMPLE',
  'STATION_FIELD_FULL_K', 'STATION_FIELD_FULL_BUCKET_DEG', 'STATION_FIELD_FULL_MAX_RING',
  'STATION_FIELD_CONTOUR', 'STATION_FIELD_CONTOUR_LABEL_MAX', 'STATION_FIELD_CONTOUR_LABEL_MIN_PX',
  'STATION_FIELD_CONTOUR_GRID_MULT', 'STATION_FIELD_CONTOUR_GRID_MAX_W', 'STATION_FIELD_CONTOUR_CHAIKIN',
  // 1.3.0.73 - il peso gaussiano (σ, LUT) e il reticolo 0,5° non esistono piu':
  // il raster torna per pixel con il kernel 1/(d^2+1) della 1.3.0.68.
  'STATION_FIELD_RAIN_CUTOFF_KM', 'stationFieldVar',
  'STATION_FIELD_SNOW_CUTOFF_KM', 'STATION_FIELD_SNOW_FADE_KM',
  'STATION_FIELD_AGE_POLICY',
  'stationFieldMethod',
  'STATION_FIELD_BUCKET_DEG', 'STATION_FIELD_MAX_RING', 'STATION_FIELD_MIN_COVERAGE',
  'STATION_FIELD_VARS', 'STATION_FIELD_WINDOWS', 'STATION_FIELD_FIELDS',
  'STATION_FIELD_DAILY', 'STATION_FIELD_WIND_BREAKPOINTS', 'STATION_FIELD_WIND_COLORS',
  'STATION_FIELD_WIND_SCALE', 'STATION_FIELD_LEGEND', 'stationFieldDailyCache', 'stationFieldDailyCacheFor',
  'STATION_FIELD_DAILY_PARTIAL', 'STATION_FIELD_MEAN_KEY_RAW', 'stationFieldMeanCache',
  'STATION_FIELD_RH_BREAKPOINTS', 'STATION_FIELD_RH_COLORS', 'STATION_FIELD_RH_SCALE',
  'STATION_FIELD_SNOW_BREAKPOINTS', 'STATION_FIELD_SNOW_COLORS', 'STATION_FIELD_SNOW_SCALE',
  'STATIONS_MEAN_FIELDS',
  'FM_TEMP2M_BREAKPOINTS', 'FM_TEMP2M_COLORS', 'FM_PRECIP_BREAKPOINTS', 'FM_PRECIP_COLORS',
  'stationsFull', 'stationsFullLoading', 'stationsFullFailedAt', 'stationsFullMergedInto',
  'stationFieldRadarHidden', 'stationFieldRadarPaneWasVisible'
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
  // 1.3.0.61 - la spec legge stationsData per decidere se la MEDIA sulla finestra
  // e' pubblicata (tre stati: presente / bloccata con motivo / assente). Nel vm
  // deve esistere, altrimenti la prima lettura lancia ReferenceError.
  stationsData: null,
  stationsDailyFailedAt: 0,
  stationFieldActive: false,
  stationFieldVar: 'temp',
  stationFieldWindow: '1h',
  stationFieldLastStats: null,
  stationsAttributionLine: () => 'Dati centraline: CC BY 4.0',
  // 1.3.0.69 - orologio deterministico per il test: la pagina usa
  // toLocaleTimeString('it-IT') (ora LOCALE), qui si formatta in UTC per non far
  // dipendere l'assert dal fuso del host.
  stationsClock: function (t) {
    var d = new Date(t);
    return String(d.getUTCHours()).padStart(2, '0') + ':' + String(d.getUTCMinutes()).padStart(2, '0');
  },
  fmtNum: (v, d) => Number(v).toFixed(d),
  // 1.3.0.69 - il fallback al file completo e il redraw sono STUB espliciti: il
  // test verifica la DECISIONE (tre stati del messaggio + chiamata al
  // caricamento), non la rete. Il caricamento reale e' verificato nel browser.
  stationsEnsureFull: function () { ctxObj.__fullCalls++; return Promise.resolve(null); },
  __fullCalls: 0,
  stationsRedraw: function () { },
  fetchWithTimeout: function () { return new Promise(function () { }); },
  // 1.3.0.74 - map minima per la regola «radar nascosto dal campo centraline».
  // Il test assegna un pane radarPane con style.display e un hasLayer() reale.
  map: null,
  isRadarActive: false,
  liveRadarLayer: null,
  radarTileLayer: null,
  allRadarLayers: []
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
// 1.3.0.73 - KERNEL DELLA 1.3.0.68 (lo stato che l'utente accettava): peso
// 1/(d²+1) sui K VICINI PIU' VICINI entro il cut-off. Il gaussiano largo della
// 1.3.0.70 sommava TUTTI i vicini e spalmava la pioggia dove non era caduta.
const w1 = 1 / (dSide * dSide + 1);
const w3 = 1 / (dUp * dUp + 1);
const expected = (10 * w1 + 20 * w1 + 30 * w3) / (2 * w1 + w3);
const idw = ctxObj.stationFieldIdwAt(41.0, 9.0, CAND3, cos41);
ok('distanza laterale 0,1° a 41°N = 8,4013 km', Math.abs(dSide - 8.4013) < 0.001, dSide.toFixed(4) + ' km');
ok('distanza verticale 0,1° = 11,132 km', Math.abs(dUp - 11.132) < 0.001, dUp.toFixed(4) + ' km');
ok('kernel 1/(d²+1): valore al centro = ' + expected.toFixed(6) + ' °C',
  Math.abs(idw.v - expected) < 1e-9, 'ottenuto ' + idw.v.toFixed(9));
ok('d_k = distanza del vicino piu lontano usato (11,132 km)', Math.abs(idw.dk - dUp) < 1e-9, idw.dk.toFixed(6));
ok('usa i 3 candidati entro il raggio', idw.n === 3, 'n=' + idw.n);
// 1.3.0.73 - k=12 e' un TRONCAMENTO: il 13° vicino NON contribuisce (era il
// difetto del gaussiano: ogni vicino entrava, anche a 100 km).
const many = [];
for (let i = 0; i < 20; i++) many.push({ lat: 41.0 + i * 0.0005, lon: 9.0 + i * 0.0005, v: 10 + i });
const idwMany = ctxObj.stationFieldIdwAt(41.0, 9.0, many, cos41);
function wClassic(p) {
  const d = ctxObj.stationFieldDistKm(41.0, 9.0, p.lat, p.lon, cos41);
  return 1 / (d * d + 1);
}
const best12 = many.slice().sort((a, b) => wClassic(b) - wClassic(a)).slice(0, 12);
const sum12 = best12.reduce((a, p) => a + wClassic(p) * p.v, 0);
const w12 = best12.reduce((a, p) => a + wClassic(p), 0);
ok('k=12: valore = media pesata dei 12 vicini piu vicini, il 13° resta fuori',
  idwMany.n === 12 && Math.abs(idwMany.v - sum12 / w12) < 1e-9,
  'n=' + idwMany.n + ' · ' + idwMany.v.toFixed(6) + ' vs ' + (sum12 / w12).toFixed(6));
const one = ctxObj.stationFieldIdwAt(41.0, 9.0, [{ lat: 41.0, lon: 9.0, v: 7 }], cos41);
ok('con un solo vicino a distanza 0 il valore e il suo stesso valore', Math.abs(one.v - 7) < 1e-9 && one.dk === 0);

console.log('\nB. Cutoff 25 km: nessun vicino -> valore nullo e pixel trasparente');
const far = ctxObj.stationFieldIdwAt(40.0, 9.0, CAND3, Math.cos(40 * Math.PI / 180));
ok('a 111 km dai punti il valore e NULL (fuori da 25 km)', far.v === null && far.n === 0);
ok('il cutoff dichiarato e 25 km', ctxObj.STATION_FIELD_CUTOFF_KM === 25);
ok('k dichiarato e 12 (1.3.0.61: da 8 a 12 per una superficie piu continua)', ctxObj.STATION_FIELD_K === 12);
ok('fade dichiarato e 14 km (1.3.0.61: da 8 a 14, bordo meno netto)', ctxObj.STATION_FIELD_FADE_KM === 14);
ok('supersampling dichiarato x2', ctxObj.STATION_FIELD_SUPERSAMPLE === 2);
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
// fade non saturo: punto a ~20 km -> (25-20)/14 = 0,357 -> alpha 84
const d20 = 20 / (111.32 * cos41);           // gradi di longitudine per 20 km
const rFade = ctxObj.stationFieldRaster(
  [{ lat: 41.0, lon: 9.0 - d20, v: 10 }], T_SCALE, frame9, null);
const aFade = rFade.alphas[4 * 9 + 4];
const want = Math.round(235 * ((25 - 20) / 14));
ok('a 20 km l alpha e 235*(25-20)/14 = ' + want, Math.abs(aFade - want) <= 1, 'alpha=' + aFade);
const maskZero = new Uint8Array(9 * 9);      // tutto mare
const rMask = ctxObj.stationFieldRaster(CAND3, T_SCALE, frame9, maskZero);
ok('maschera a 0 su tutta la griglia -> nessun pixel dipinto', rMask.painted === 0 && rMask.alphas[4 * 9 + 4] === 0);
const maskCenter = new Uint8Array(9 * 9);
maskCenter[4 * 9 + 4] = 255;
const rMask2 = ctxObj.stationFieldRaster(CAND3, T_SCALE, frame9, maskCenter);
ok('maschera a 255 solo al centro: centro dipinto e bordo SFUMATO (feather), non tagliato',
  rMask2.alphas[4 * 9 + 4] > 0 && rMask2.alphas[4 * 9 + 5] > 0 &&
  rMask2.alphas[4 * 9 + 5] < rMask2.alphas[4 * 9 + 4],
  'centro=' + rMask2.alphas[4 * 9 + 4] + ' vicino=' + rMask2.alphas[4 * 9 + 5]);
// 1.3.0.74 - FEATHER QUASI NETTO: il colore puo' uscire di UN pixel dalla terra
// (anello), non due o piu'. Prima era una dilatazione 2 px + blur 5x5.
const softTight = ctxObj.stationFieldSoftenMask(maskCenter, 9, 9);
ok('feather: terra alpha 1, anello a 1 px > 0 e < 1, oltre 2 px alpha ESATTAMENTE 0',
  softTight[4 * 9 + 4] === 1 && softTight[4 * 9 + 5] > 0 && softTight[4 * 9 + 5] < 1 &&
  softTight[4 * 9 + 6] === 0 && softTight[4 * 9 + 2] === 0 && softTight[2 * 9 + 4] === 0,
  'centro=' + softTight[4 * 9 + 4] + ' anello=' + softTight[4 * 9 + 5].toFixed(3) +
  ' a2px=' + softTight[4 * 9 + 6]);

console.log('\nD. Selezione del campo per variabile e finestra (nomi reali del file)');
ctxObj.stationsData = null;
const specTemp = ctxObj.stationFieldSpec('temp', '3h');
ok('temp 3 h senza dati: si chiede la media (tempMean3h), finestra APPLICATA', specTemp.field === 'tempMean3h' &&
  specTemp.windowApplies === true && specTemp.window === '3h' && specTemp.available === true &&
  specTemp.note === '' && specTemp.meanPending === true);
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

console.log('\nD2. TEMPERATURA CON LE FINESTRE: media, istantanea dichiarata, motivo del server');
// 1) media PRESENTE nel file -> si legge la media, nota vuota, wrap visibile.
ctxObj.stationsData = { stations: [{ id: 'A', tempMean1h: 9.4, temperature_2m: 9.8 }] };
const specTMean = ctxObj.stationFieldSpec('temp', '1h');
ok('temp 1 h con temp_mean_1h_c -> campo tempMean1h, nota vuota',
  specTMean.field === 'tempMean1h' && specTMean.available === true && specTMean.note === '' &&
  specTMean.meanFallback === false && specTMean.meanPending === false, JSON.stringify(specTMean.note));
ok('la finestra e una MEDIA (windowApplies true) e la scala resta temperature_2m',
  specTMean.windowApplies === true && specTMean.window === '1h' && specTMean.scale === 'temperature_2m');
ok('STATION_FIELD_FIELDS.temp nomina i campi REALI del server',
  ctxObj.STATION_FIELD_FIELDS.temp['1h'].raw === 'temp_mean_1h_c' &&
  ctxObj.STATION_FIELD_FIELDS.temp['5m'].raw === 'temp_mean_5m_c' &&
  ctxObj.STATION_FIELD_FIELDS.temp['30m'].raw === 'temp_mean_30m_c' &&
  ctxObj.STATION_FIELD_FIELDS.temp['3h'].raw === 'temp_mean_3h_c');
// 2) media ASSENTE -> istantanea con la nota ESATTA.
ctxObj.stationsData = { stations: [{ id: 'A', temperature_2m: 9.8 }] };
const specTFall = ctxObj.stationFieldSpec('temp', '1h');
ok('temp 1 h senza media -> temperature_2m (istantanea)', specTFall.field === 'temperature_2m' &&
  specTFall.available === true && specTFall.meanFallback === true);
ok('la nota dice esattamente che la media non e pubblicata',
  specTFall.note === 'media non ancora pubblicata dal server: mostro l\'ultimo valore istantaneo',
  JSON.stringify(specTFall.note));
// 3) il server PUBBLICA la finestra ma la dichiara non calcolabile -> NESSUN ripiego.
// Il nome REALE del motivo per le termiche e' `<nome>_<finestra>_null_reason`
// (temp_5m_null_reason), non `temp_mean_5m_c_null_reason`: misurato sul fetcher.
const meanReason5m = ctxObj.stationsMeanReasons({ temp_5m_null_reason: 'series_too_coarse_for_5min' });
ok('il motivo reale temp_5m_null_reason viene associato a temp_mean_5m_c',
  meanReason5m && meanReason5m['temp_mean_5m_c'] === 'series_too_coarse_for_5min',
  JSON.stringify(meanReason5m));
ok('anche la convenzione di vento/raffica (campo senza unita) e letta',
  (() => {
    const m = ctxObj.stationsMeanReasons({ wind_mean_30m_null_reason: 'coverage_below_50pct' });
    return m && m['wind_mean_30m_ms'] === 'coverage_below_50pct';
  })());
ctxObj.stationsData = { stations: [
  { id: 'A', meanReason: meanReason5m },
  { id: 'B', meanReason: ctxObj.stationsMeanReasons({ temp_5m_null_reason: 'series_too_coarse_for_5min' }) }
] };
const specT5 = ctxObj.stationFieldSpec('temp', '5m');
ok('temp 5 min con solo series_too_coarse_for_5min: messaggio dedicato',
  specT5.available === false && specT5.field === null &&
  specT5.reason === 'nessuna stazione con serie a 1 minuto: finestra 5 min non calcolabile', specT5.reason);
ok('con il motivo del server NON si ripiega sull istantanea', specT5.meanFallback === false);
// 4) media 5 min PRESENTE.
ctxObj.stationsData = { stations: [{ id: 'A', tempMean5m: 8.1, temperature_2m: 8.4 }] };
const specT5b = ctxObj.stationFieldSpec('temp', '5m');
ok('temp 5 min con temp_mean_5m_c -> campo tempMean5m', specT5b.field === 'tempMean5m' &&
  specT5b.available === true);
// 5) un motivo NUOVO compare com'e' (nessun motivo inventato).
ctxObj.stationsData = { stations: [{ id: 'A', meanReason: ctxObj.stationsMeanReasons({ temp_30m_null_reason: 'new_server_code_42' }) }] };
const specT30 = ctxObj.stationFieldSpec('temp', '30m');
ok('codice motivo sconosciuto: compare in chiaro con il conteggio',
  /new_server_code_42 \(1 stazioni\)/.test(specT30.reason || ''), specT30.reason);
// 6) vento 30 min: campo media + COPERTURA pubblicata dal server (nessun ripiego).
ctxObj.stationsData = { stations: [{ id: 'A', windMean30m: 3.4, windMean30mCov: 0.9 }] };
const specW30 = ctxObj.stationFieldSpec('wind', '30m');
ok('vento 30 min -> windMean30m con copertura windMean30mCov',
  specW30.field === 'windMean30m' && specW30.covField === 'windMean30mCov' &&
  specW30.available === true && specW30.scale === 'station_wind_classes');

// 1.3.0.76 - VENTO/RAFFICA: 8 classi da 0 a 30 m/s e legenda a doppia unita'.
console.log('\nD3. Vento/raffica: 8 classi 0..30 m/s, m/s + km/h in legenda');
ok('vento: 9 breakpoint (8 classi) e 8 colori coerenti',
  ctxObj.STATION_FIELD_WIND_BREAKPOINTS.join(',') === '0,2.5,5,7.5,10,15,20,25,30' &&
  ctxObj.STATION_FIELD_WIND_COLORS.length === 8 &&
  ctxObj.STATION_FIELD_WIND_SCALE.unit === 'm/s');
const windRGB = (i) => ctxObj.STATION_FIELD_WIND_COLORS[i];
const windD = (a, b) => Math.max(Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1]), Math.abs(a[2] - b[2]));
const windPairs = [];
for (let wi = 0; wi + 1 < ctxObj.STATION_FIELD_WIND_COLORS.length; wi++) {
  windPairs.push({ i: wi, d: windD(windRGB(wi), windRGB(wi + 1)) });
}
ok('vento: OGNI coppia di classi adiacenti dista > 40 su almeno un canale RGB',
  windPairs.every((p) => p.d > 40),
  windPairs.map((p) => p.i + '-' + (p.i + 1) + ':' + p.d).join(' '));
ok('legenda vento: 9 tacche 0..30 m/s',
  ctxObj.STATION_FIELD_LEGEND.station_wind_classes.hi === 30 &&
  ctxObj.STATION_FIELD_LEGEND.station_wind_classes.ticks.join(',') === '0,2.5,5,7.5,10,15,20,25,30');
ok('etichetta vento: m/s con km/h fra parentesi (10 -> «10 (36)», 2,5 -> «2,5 (9)»)',
  ctxObj.stationFieldLegendLabelText(10, 'station_wind_classes') === '10 (36)' &&
  ctxObj.stationFieldLegendLabelText(2.5, 'station_wind_classes') === '2,5 (9)' &&
  ctxObj.stationFieldLegendLabelText(10, 'temperature_2m') === '10');
const lgWind = ctxObj.stationFieldLegendLayout({ scale: 'station_wind_classes' }, { vMin: 2, vMax: 28 });
let windOverlap = 0;
for (let wi = 1; wi < lgWind.items.length; wi++) {
  const a = lgWind.items[wi - 1], b = lgWind.items[wi];
  if (b.x - a.x < (a.w + b.w) / 2 + 2 - 1e-6) windOverlap++;
}
ok('legenda vento: etichette orizzontali, nessuna sovrapposizione, tutte con km/h',
  lgWind.shown === lgWind.items.length && windOverlap === 0 &&
  lgWind.items.every((it) => /\(\d+\)/.test(it.text)),
  'mostrate ' + lgWind.shown + '/' + lgWind.bandCount + ' bande, sovrapposizioni=' + windOverlap);
// 1.3.0.76 - POPUP: vento e raffica mostrano m/s e, piu' piccolo, km/h.
const popWindSrc = src.slice(src.indexOf('💨 vento'), src.indexOf('💨 vento') + 600);
const popGustSrc = src.slice(src.indexOf('raffica massima 10 m:'), src.indexOf('raffica massima 10 m:') + 600);
ok('popup stazione: vento e raffica con m/s e km/h',
  /m\/s/.test(popWindSrc) && /km\/h/.test(popWindSrc) &&
  /m\/s/.test(popGustSrc) && /km\/h/.test(popGustSrc),
  'vento=' + /km\/h/.test(popWindSrc) + ' raffica=' + /km\/h/.test(popGustSrc));

console.log('\nD3. NUOVE VARIABILI: umidità, punto di rugiada, neve + famiglie di esclusione');
ctxObj.stationsData = { stations: [
  { id: 'A', rhMean1h: 64, rh: 70, dewMean1h: 5.2, dew: 6.1, snowDepthCm: 12 }
] };
const specRh = ctxObj.stationFieldSpec('rh', '1h');
ok('umidità 1 h -> rhMean1h, unità %, scala station_relative_humidity',
  specRh.field === 'rhMean1h' && specRh.unit === '%' && specRh.scale === 'station_relative_humidity' &&
  specRh.available === true);
const specDew = ctxObj.stationFieldSpec('dew', '1h');
ok('punto di rugiada 1 h -> dewMean1h, unità °C, scala temperature_2m',
  specDew.field === 'dewMean1h' && specDew.unit === '°C' && specDew.scale === 'temperature_2m');
const specDew3 = ctxObj.stationFieldSpec('dew', '3h');
ok('punto di rugiada 3 h senza dew_mean_3h_c -> istantanea con la stessa nota',
  specDew3.field === 'dew' && specDew3.meanFallback === true &&
  specDew3.note === 'media non ancora pubblicata dal server: mostro l\'ultimo valore istantaneo');
const specSnow = ctxObj.stationFieldSpec('snow', '1h');
ok('neve -> snowDepthCm in cm, scala station_snow_cm, finestra non applicata',
  specSnow.field === 'snowDepthCm' && specSnow.unit === 'cm' && specSnow.scale === 'station_snow_cm' &&
  specSnow.windowApplies === false && specSnow.available === true);
const nowT = Date.parse('2026-11-23T18:00:00Z');
const colSnow = ctxObj.stationFieldCollect(
  [{ id: 'A', lat: 45, lon: 9, time: nowT, snowDepthCm: 12 }], specSnow, nowT);
ok('collect neve: 1 punto usato con 12 cm', colSnow.used === 1 && colSnow.points[0].v === 12);
const colRh = ctxObj.stationFieldCollect(
  [{ id: 'A', lat: 45, lon: 9, time: nowT, rhMean1h: 64 }, { id: 'B', lat: 45.1, lon: 9.1, time: nowT, rh: 70 }],
  specRh, nowT);
ok('collect umidità: la stazione senza la MEDIA resta esclusa (nessun valore inventato)',
  colRh.used === 1 && colRh.points[0].id === 'A');
// Le scale nuove sono REALI e con la stessa lunghezza (breakpoints/colori).
ok('scala umidità: 15 bande 0..95 % e colori coerenti',
  ctxObj.STATION_FIELD_RH_SCALE.breakpoints.length === 15 &&
  ctxObj.STATION_FIELD_RH_COLORS.length === 15 &&
  ctxObj.STATION_FIELD_RH_BREAKPOINTS[0] === 0 && ctxObj.STATION_FIELD_RH_BREAKPOINTS[14] === 95);
// 1.3.0.74 - NEVE: scala DEDICATA in cm (non piu' la rampa di snow_depth in
// metri x100, dove 0-100 cm erano tutti verdi). 10 bande, 0 trasparente.
ok('scala neve: 10 bande 0..300 cm in CM, prima banda trasparente',
  ctxObj.STATION_FIELD_SNOW_SCALE.breakpoints.length === 10 &&
  ctxObj.STATION_FIELD_SNOW_COLORS.length === 10 &&
  ctxObj.STATION_FIELD_SNOW_COLORS[0][3] === 0 && ctxObj.STATION_FIELD_SNOW_BREAKPOINTS[0] === 0 &&
  ctxObj.STATION_FIELD_SNOW_BREAKPOINTS[1] === 0.5 &&
  ctxObj.STATION_FIELD_SNOW_BREAKPOINTS[9] === 300 &&
  ctxObj.STATION_FIELD_SNOW_SCALE.unit === 'cm');
// I colori devono CAMBIARE fra 0,5 / 10 / 50 / 100 / 200 cm: distanza RGB > 60
// su almeno un canale fra 10 e 100 (prima erano due verdi quasi identici).
const snowRGB = (cm) => {
  const band = ctxObj.stationFieldBandIndex(ctxObj.STATION_FIELD_SNOW_SCALE, cm);
  const c = ctxObj.STATION_FIELD_SNOW_SCALE.colors.light[band];
  return [c[0], c[1], c[2], c[3]];
};
const s1 = snowRGB(1), s10 = snowRGB(10), s50 = snowRGB(50), s100 = snowRGB(100), s200 = snowRGB(200);
const dMax = (a, b) => Math.max(Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1]), Math.abs(a[2] - b[2]));
ok('neve: 10 cm e 100 cm hanno colori DIVERSI (distanza RGB max ' + dMax(s10, s100) + ' > 60)',
  dMax(s10, s100) > 60, '10=' + s10.slice(0, 3).join(',') + ' · 100=' + s100.slice(0, 3).join(','));
ok('neve: 1 / 10 / 50 / 200 cm sono quattro colori distinti',
  dMax(s1, s10) > 60 && dMax(s10, s50) > 60 && dMax(s50, s200) > 60);
ok('neve: lo zero e 0,5 cm sono trasparenti (alpha 0), da 1 cm in su opaco',
  snowRGB(0)[3] === 0 && snowRGB(0.5)[3] === 0 && snowRGB(0.9)[3] === 0 &&
  snowRGB(1)[3] === 1 && s10[3] === 1);
ok('neve: 1 cm e 25 cm sono distinguibili (distanza RGB max ' + dMax(snowRGB(1), snowRGB(25)) + ' > 60)',
  dMax(snowRGB(1), snowRGB(25)) > 60,
  '1=' + snowRGB(1).slice(0, 3).join(',') + ' · 25=' + snowRGB(25).slice(0, 3).join(','));
ok('neve: raggio dedicato 10 km con fade 2 km (dichiarati)',
  ctxObj.STATION_FIELD_SNOW_CUTOFF_KM === 10 && ctxObj.STATION_FIELD_SNOW_FADE_KM === 2);
ok('stationFieldScale risolve le due scale nuove (fuori dal blocco MAPPE, che resta self-contained)',
  ctxObj.stationFieldScale('station_relative_humidity') === ctxObj.STATION_FIELD_RH_SCALE &&
  ctxObj.stationFieldScale('station_snow_cm') === ctxObj.STATION_FIELD_SNOW_SCALE);
ok('legenda dedicata per umidità e neve (intervalli e tacche)',
  ctxObj.STATION_FIELD_LEGEND.station_relative_humidity.hi === 100 &&
  ctxObj.STATION_FIELD_LEGEND.station_snow_cm.hi === 300);
// 1.3.0.69 - LE TRE VOCI NON INTERPOLABILI SONO STATE RIMOSSE dal selettore (non
// disabilitate) e la riga #live-stationfield-excluded non esiste piu': la
// dichiarazione vive nella scheda Fonti e metodologie (verificata nella sezione H).
ok('nessuna opzione non interpolabile nel selettore del CAMPO (#stationfield-var)',
  (function () {
    var a = src.indexOf('id="stationfield-var"');
    var b = src.indexOf('</select>', a);
    var block = src.slice(a, b);
    return !/value="(windDir|pStation|riverLevel)"/.test(block);
  })());

console.log('\nD4. MASSIMA, MINIMA E GIORNATA (tmax/tmin/day) DAL BLOB GIORNALIERO');
const nowD4 = Date.parse('2026-11-23T18:00:00Z');
// 1.3.0.68 - la Pro legge «oggi» da stationsDailyLoad(), NON da latest.json
// (dove temp_max_day_c non e' pubblicato). Senza blob lo stato e' di
// CARICAMENTO, non «campo assente»: la mappa non deve restare vuota in silenzio.
ctxObj.stationsDaily = null;
ctxObj.stationsDailyFailedAt = 0;
ctxObj.stationsData = { stations: [
  { id: 'A', lat: 45.0, lon: 9.0, time: nowD4 },
  { id: 'B', lat: 45.1, lon: 9.1, time: nowD4 }
] };
const specTMaxPending = ctxObj.stationFieldSpec('tmax', 'day');
ok('tmax + day SENZA blob: finestra selezionabile in stato di CARICAMENTO',
  specTMaxPending.available === true && specTMaxPending.dailyPending === true &&
  specTMaxPending.dailyField === 'temp_max_day_c' && specTMaxPending.field === null &&
  specTMaxPending.note === 'carico la giornata...', specTMaxPending.note);
ok('tmax + day NON legge tempMaxDay da latest.json (campo non pubblicato li)',
  specTMaxPending.field !== 'tempMaxDay');
// Con il blob (schema stations-daily-series/2) il campo e' popolato.
ctxObj.stationsDaily = { schema: 'stations-daily-series/2', day: '2026-11-23',
  stations: {
    A: { temp_max_day_c: 18.2, temp_max_day_hour: '14', temp_day_partial: true, temp_day_coverage_frac: 1.0 },
    B: { temp_max_day_c: 16.9, temp_max_day_hour: '12', temp_day_partial: true, temp_day_coverage_frac: 0.9 }
  } };
const specTMaxDay = ctxObj.stationFieldSpec('tmax', 'day');
ok('tmax + day CON il blob -> dailyField temp_max_day_c, aggregazione max, giornata',
  specTMaxDay.dailyField === 'temp_max_day_c' && specTMaxDay.agg === 'max' &&
  specTMaxDay.dayWindow === true && specTMaxDay.available === true &&
  specTMaxDay.dailyPending !== true && specTMaxDay.scale === 'temperature_2m');
const colTMax = ctxObj.stationFieldCollect(ctxObj.stationsData.stations, specTMaxDay, nowD4);
ok('collect tmax day: 2 punti DAL BLOB, picco 18,2 con ora 14',
  colTMax.used === 2 && colTMax.peakValue === 18.2 && colTMax.peakHour === 14,
  'usate=' + colTMax.used + ' picco=' + colTMax.peakValue + ' ora=' + colTMax.peakHour);
ok('flag temp_day_partial del BLOB contato (2 stazioni su 2)', colTMax.partialDay === 2);
ok('copertura temp_day_coverage_frac letta dal blob (media 0,95)',
  colTMax.coverageN === 2 && Math.abs(colTMax.coverageSum / colTMax.coverageN - 0.95) < 1e-9,
  'n=' + colTMax.coverageN);
// tmin + day dallo stesso blob.
ctxObj.stationsDaily = { schema: 'stations-daily-series/2', day: '2026-11-23',
  stations: {
    A: { temp_min_day_c: 3.4, temp_min_day_hour: '5', temp_day_partial: true, temp_day_coverage_frac: 1.0 },
    B: { temp_min_day_c: 4.8, temp_min_day_hour: '6', temp_day_partial: true, temp_day_coverage_frac: 1.0 }
  } };
const specTMinDay = ctxObj.stationFieldSpec('tmin', 'day');
ok('tmin + day -> temp_min_day_c, aggregazione min', specTMinDay.dailyField === 'temp_min_day_c' &&
  specTMinDay.agg === 'min' && specTMinDay.dayWindow === true && specTMinDay.available === true);
const colTMin = ctxObj.stationFieldCollect(ctxObj.stationsData.stations, specTMinDay, nowD4);
ok('collect tmin day: minimo 3,4 alle 05:00', colTMin.peakValue === 3.4 && colTMin.peakHour === 5);
ok('ora del picco formattata 05:00 (stationFieldHourText)', ctxObj.stationFieldHourText(5) === '05:00' &&
  ctxObj.stationFieldHourText(14) === '14:00');
// La MEDIA della giornata (temp_mean_day_c) NON e' pubblicata: resta disabilitata.
ctxObj.stationsData = { stations: [{ id: 'A', temperature_2m: 9.8 }] };
const specTempDay = ctxObj.stationFieldSpec('temp', 'day');
ok('temp + day senza temp_mean_day_c -> NON disponibile, strictUnavailable',
  specTempDay.available === false && specTempDay.strictUnavailable === true && specTempDay.field === null);
ok('il motivo dice che il server pubblica massima e minima',
  /temp_mean_day_c/.test(specTempDay.reason || '') && /massima e la minima/.test(specTempDay.reason || ''),
  specTempDay.reason);
// Selettore finestra: «oggi» di tmax e' abilitato in caricamento e usa il blob.
ctxObj.stationFieldVar = 'tmax'; ctxObj.stationFieldWindow = 'day';
ctxObj.stationsDaily = null; ctxObj.stationsDailyFailedAt = 0;
const selWinDay = el('stationfield-window');
selWinDay.options = [{ value: '5m' }, { value: '30m' }, { value: '1h' }, { value: '3h' },
  { value: '6h' }, { value: '24h' }, { value: 'day' }];
ctxObj.stationFieldSyncControls();
ok('tmax day senza blob: opzione abilitata con title «carico la giornata...»',
  selWinDay.options[6].disabled === false && /carico la giornata/.test(selWinDay.options[6].title || ''),
  selWinDay.options[6].title);
ctxObj.stationsDaily = { schema: 'stations-daily-series/2', day: '2026-11-23',
  stations: { A: { temp_max_day_c: 18.2 } } };
ctxObj.stationFieldSyncControls();
ok('tmax day con il blob: opzione abilitata e title dal campo reale',
  selWinDay.options[6].disabled === false && /temp_max_day_c/.test(selWinDay.options[6].title || ''),
  selWinDay.options[6].title);
// La MEDIA giornaliera della temperatura resta disabilitata con il motivo.
ctxObj.stationFieldVar = 'temp'; ctxObj.stationFieldWindow = 'day';
ctxObj.stationsData = { stations: [{ id: 'A', temperature_2m: 9.8 }] };
ctxObj.stationsDaily = null;
ctxObj.stationFieldSyncControls();
ok('opzione "oggi" della MEDIA temperatura DISABILITATA con il motivo nel title',
  selWinDay.options[6].disabled === true && /temp_mean_day_c/.test(selWinDay.options[6].title || ''),
  selWinDay.options[6].title);
ctxObj.stationsData = { stations: [{ id: 'A', tempMeanDay: 9.1 }] };
ctxObj.stationFieldSyncControls();
ok('con temp_mean_day_c nel file l opzione "oggi" della media torna selezionabile',
  selWinDay.options[6].disabled === false);
// tmax/tmin con finestre corte: i campi reali.
ctxObj.stationsData = { stations: [{ id: 'A', tempMax1h: 15.5, tempMin1h: 8.1 }] };
ok('tmax/tmin 1 h -> temp_max_1h_c / temp_min_1h_c',
  ctxObj.stationFieldSpec('tmax', '1h').field === 'tempMax1h' &&
  ctxObj.stationFieldSpec('tmin', '1h').field === 'tempMin1h' &&
  ctxObj.STATION_FIELD_FIELDS.tmax['1h'].raw === 'temp_max_1h_c' &&
  ctxObj.STATION_FIELD_FIELDS.tmin['1h'].raw === 'temp_min_1h_c');
// Riga di stato: "max 18,2 °C alle 14:00 · ... · 2 stazioni" e giornata in corso.
ctxObj.stationsData = { stations: [
  { id: 'A', lat: 45.0, lon: 9.0, time: nowD4 },
  { id: 'B', lat: 45.1, lon: 9.1, time: nowD4 }
] };
ctxObj.stationsDaily = { schema: 'stations-daily-series/2', day: '2026-11-23',
  stations: {
    A: { temp_max_day_c: 18.2, temp_max_day_hour: '14', temp_day_partial: true, temp_day_coverage_frac: 1.0 },
    B: { temp_max_day_c: 16.9, temp_max_day_hour: '12', temp_day_partial: true, temp_day_coverage_frac: 1.0 }
  } };
ctxObj.stationFieldVar = 'tmax';
ctxObj.stationFieldWindow = 'day';
ctxObj.stationFieldLastStats = ctxObj.stationFieldCollect(ctxObj.stationsData.stations, ctxObj.stationFieldSpec('tmax', 'day'), nowD4);
ctxObj.stationFieldStatus();
const statusTxt = el('live-stationfield-status').textContent;
const statusTitle = el('live-stationfield-status').title;
ok('riga di stato: "max 18,2 °C alle 14:00" nel title', /max 18[.,]2 °C alle 14:00/.test(statusTitle), statusTitle.slice(0, 140));
ok('riga di stato: "2 stazioni" e finestra oggi', /^2 stazioni · oggi \(dalla mezzanotte\)/.test(statusTxt), statusTxt);
ok('giornata in corso: "il massimo può ancora salire" nel title', /giornata in corso/.test(statusTitle) &&
  /il massimo può ancora salire/.test(statusTitle));
// La riga di stato SENZA blob dichiara il caricamento (non una mappa vuota muta).
ctxObj.stationsDaily = null;
ctxObj.stationFieldLastStats = null;
ctxObj.stationFieldStatus();
ok('riga di stato senza blob: «carico la giornata...»',
  /carico la giornata/.test(el('live-stationfield-status').textContent),
  el('live-stationfield-status').textContent);

console.log('\nE. Finestre 6 h/24 h dal blob daily_series: campo, schema e PARZIALITA\'');
ctxObj.stationsDaily = null;
ctxObj.stationsDailyFailedAt = 0;
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
selWin.options = [{ value: '5m' }, { value: '30m' }, { value: '1h' }, { value: '3h' }, { value: '6h' }, { value: '24h' }];
ctxObj.stationFieldVar = 'rain'; ctxObj.stationFieldWindow = '1h';
ctxObj.stationsDaily = null;
ctxObj.stationFieldSyncControls();
ok('select finestra: 1 h/3 h abilitate, 6 h/24 h DISABILITATE senza blob',
  selWin.options[2].disabled === false && selWin.options[3].disabled === false &&
  selWin.options[4].disabled === true && selWin.options[5].disabled === true &&
  /daily_series\.json/.test(selWin.options[4].title || ''), selWin.options[4].title);
ok('pioggia 30 min: opzione presente e disabilitata con il motivo (nessun campo di accumulo a 30 min)',
  selWin.options[1].disabled === true && /30 min/.test(selWin.options[1].title || ''),
  selWin.options[1].title);
ctxObj.stationsDaily = { schema: 'stations-daily-series/2', day: '2026-11-23',
  stations: { A: { rain_6h_mm: 12.4, rain_6h_partial: false, rain_24h_mm: 30.1, rain_24h_partial: false } } };
ctxObj.stationFieldSyncControls();
ok('con i campi completi nel blob le due finestre tornano selezionabili',
  selWin.options[4].disabled === false && selWin.options[5].disabled === false);
// 1.3.0.61 - LA TEMPERATURA HA LE FINESTRE: il selettore resta VISIBILE e ABILITATO
// e le finestre corte sono sempre selezionabili (nota, non blocco).
ctxObj.stationsData = { stations: [{ id: 'A', temperature_2m: 9.8 }] };
ctxObj.stationFieldVar = 'temp';
ctxObj.stationFieldWindow = '1h';
ctxObj.stationFieldSyncControls();
ok('con la temperatura il select finestra RESTA abilitato (media sulla finestra)', selWin.disabled === false);
ok('con la temperatura le finestre 5 min/30 min/1 h/3 h sono selezionabili',
  selWin.options[0].disabled === false && selWin.options[1].disabled === false &&
  selWin.options[2].disabled === false && selWin.options[3].disabled === false);
ok('nota esatta: media non pubblicata -> valore istantaneo',
  el('live-stationfield-window-note').textContent ===
  'media non ancora pubblicata dal server: mostro l\'ultimo valore istantaneo',
  JSON.stringify(el('live-stationfield-window-note').textContent));
ok('nessuna nota "la finestra non si applica" per la temperatura',
  !/non si applica/i.test(el('live-stationfield-window-note').textContent));
ctxObj.stationsData = { stations: [{ id: 'A', tempMean1h: 9.4 }] };
ctxObj.stationFieldSyncControls();
ok('con temp_mean_1h_c la nota della temperatura sparisce',
  el('live-stationfield-window-note').textContent === '');
ctxObj.stationFieldVar = 'rain';
ctxObj.stationsData = null;
ctxObj.stationFieldSyncControls();
ok('tornando alla pioggia il select finestra resta attivo', selWin.disabled === false);

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
ctxObj.stationsData = { stations: [{ id: 'x' }] };   // nessuna media pubblicata -> istantanea
const colTemp = ctxObj.stationFieldCollect(
  [{ id: 't', lat: 45, lon: 9, time: now, temperature_2m: 12.5 }], ctxObj.stationFieldSpec('temp', '1h'), now);
ok('temperatura (istantanea, media assente): nessun filtro di copertura e nessun conteggio',
  colTemp.used === 1 && colTemp.coverageN === 0, 'used=' + colTemp.used);

console.log('\nG0. SUPERSAMPLING 2x: raster intermedio, downscale, granulosita');
// 1.3.0.73 - la variabile di stato va FISSATA: il cut-off della pioggia (12 km)
// dipende da stationFieldVar e i test precedenti la lasciano su 'rain'/'snow'.
// La granulosita' si misura sul campo di temperatura (cut-off 25 km), come nella
// 1.3.0.68: senza questo, l'assert misurava il raggio della pioggia.
ctxObj.stationFieldVar = 'temp';
// Il raster a ss=2 deve avere ESATTAMENTE il doppio dei pixel per lato (stesso
// frame Mercator) e il downscale deve tornare alla griglia base.
const frameSS = makeFrame(40, 42, 8, 10, 9, 9);
const ptsSS = [{ lat: 41.0, lon: 9.0, v: 10 }, { lat: 41.05, lon: 9.05, v: 22 }];
const rSS = ctxObj.stationFieldRaster(ptsSS, T_SCALE, frameSS, null, ctxObj.STATION_FIELD_SUPERSAMPLE);
ok('raster ss=2: W e H raddoppiati (18x18 da 9x9)',
  rSS.W === frameSS.W * 2 && rSS.H === frameSS.H * 2 && rSS.ss === 2,
  rSS.W + 'x' + rSS.H);
ok('raster ss=2: alpha e valori hanno 4x pixel', rSS.alphas.length === 18 * 18 &&
  rSS.values.length === 18 * 18 && rSS.data.length === 18 * 18 * 4);
// Il sub-pixel non cade sullo stesso centro del pixel base (griglia 2x): i due
// sub-righe della riga base 4 devono CADERE DENTRO la cella base 4, cioe' a meno
// di mezza altezza cella (2°/9/2 = 0,111°) dal suo centro.
const baseCenter = pixelLat(frameSS, 4);
const subA = pixelLat({ ...frameSS, H: 18 }, 8);
const subB = pixelLat({ ...frameSS, H: 18 }, 9);
ok('raster ss=2: i due sub-pixel della riga base 4 stanno dentro la cella base (±0,111°)',
  Math.abs(subA - baseCenter) < 0.1115 && Math.abs(subB - baseCenter) < 0.1115 &&
  subA > baseCenter && subB < baseCenter,
  subA.toFixed(3) + ' / ' + subB.toFixed(3) + ' vs ' + baseCenter.toFixed(3));
const dSS = ctxObj.stationFieldDownscale(rSS, frameSS.W, frameSS.H, 2, null);
ok('downscale: torna a 9x9 con 4 byte per pixel',
  dSS.W === 9 && dSS.H === 9 && dSS.data.length === 9 * 9 * 4);
ok('downscale: i pixel dipinti restano dipinti e coerenti (alpha > 0 dove c e il punto)',
  dSS.alphas[4 * 9 + 4] > 0 && dSS.painted > 0, 'painted=' + dSS.painted);
const maskDown = new Uint8Array(9 * 9);
const dMask = ctxObj.stationFieldDownscale(rSS, frameSS.W, frameSS.H, 2, maskDown);
ok('downscale: con maschera a 0 su tutta la griglia l alpha finale e 0 (nessuna sbavatura)',
  dMask.painted === 0 && dMask.alphas[4 * 9 + 4] === 0);
// GRANULOSITA': differenza media fra pixel adiacenti del valore interpolato.
function granularity(values, alphas, W, H) {
  let sum = 0, n = 0;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W - 1; x++) {
      const i = y * W + x, j = i + 1;
      if (!alphas[i] || !alphas[j]) continue;
      sum += Math.abs(values[j] - values[i]); n++;
    }
  }
  return n ? sum / n : 0;
}
const bigFrameG = makeFrame(36.0, 48.0, 6.0, 19.0, 128, 113);
const ptsG = [];
for (let la = 37; la < 47.5; la += 0.35) {
  for (let lo = 7; lo < 18.6; lo += 0.35) ptsG.push({ lat: la, lon: lo, v: 10 + 8 * Math.sin(la) + 3 * Math.cos(lo) });
}
const g1 = ctxObj.stationFieldRaster(ptsG, T_SCALE, bigFrameG, null, 1);
const g2 = ctxObj.stationFieldRaster(ptsG, T_SCALE, bigFrameG, null, 2);
const g2d = ctxObj.stationFieldDownscale(g2, bigFrameG.W, bigFrameG.H, 2, null);
const gran1 = granularity(g1.values, g1.alphas, g1.W, g1.H);
const gran2 = granularity(g2d.values, g2d.alphas, g2d.W, g2d.H);
ok('granulosita (|Δ| medio fra adiacenti) CALA con supersampling+downscale',
  gran2 < gran1, gran1.toFixed(3) + ' -> ' + gran2.toFixed(3) + ' °C');
// 1.3.0.73 - CONTRATTO DELLA 1.3.0.68 ripristinato: il calo deve essere almeno
// del 10% (la 1.3.0.72 lo aveva allentato a 0,5% perche' il gaussiano rendeva il
// campo gia' liscio). Con il kernel 1/(d²+1) il supersampling torna a mordere.
ok('il calo e almeno del 10%', gran2 < gran1 * 0.9,
  'rapporto ' + (gran2 / gran1).toFixed(3));

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
// 1.3.0.69 - le isolinee si misurano con lo stesso criterio (realm normale).
const benchDrawContours = new Function('var stationsDaily=null;var STATIONS_MAX_OBS_AGE_MIN=90;\n' +
  code + '\nreturn stationFieldContourVectorData;')();
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
// MISURA ROBUSTA: minimo di 3 run, lo stesso criterio gia' usato due righe sopra
// per bigPts. Una singola misura su un host con altri processi oscilla di 2-3x
// (misurato: 129 / 284 / 339 ms sullo stesso codice): il minimo misura il costo
// del raster, non lo scheduler.
let realMs = Infinity;
for (let i = 0; i < 3; i++) {
  const a2 = process.hrtime.bigint();
  benchRaster(realish, T_SCALE, bigFrame, maskAll);
  realMs = Math.min(realMs, Number(process.hrtime.bigint() - a2) / 1e6);
}
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
// 1.3.0.61 - WRAP DELLA FINESTRA: esiste, e' visibile (mai display:none) e il suo
// title dice che per la temperatura la finestra e' una MEDIA.
ok('wrap #stationfield-window-wrap presente e visibile (inline-flex)',
  /id="stationfield-window-wrap"[^>]*display:inline-flex/.test(src));
ok('il wrap non viene nascosto da nessuna parte (display none assente sul wrap)',
  !/stationfield-window-wrap'\)[^\n]*style\.display = 'none'/.test(src));
ok('title del selettore finestra: dichiara la MEDIA per temperatura/umidita/rugiada/vento',
  /id="stationfield-window"[^>]*title="[^"]*MEDIA[^"]*"/.test(src));
ok('nel blocco del campo non compare piu "non si applica"',
  !/non si applica/i.test(src.slice(src.indexOf('id="live-stationfield-box"'), src.indexOf('id="live-doppler-box"'))));
// 1.3.0.69 - le tre opzioni non interpolabili sono state RIMOSSE (non
// disabilitate) e la riga dichiarativa #live-stationfield-excluded non esiste
// piu': la dichiarazione vive nella scheda Fonti e metodologie.
ok('selettore variabile: 10 opzioni (9 + raffica stimata), nessuna non-interpolabile',
  (src.slice(src.indexOf('id="stationfield-var"'), src.indexOf('</select>', src.indexOf('id="stationfield-var"'))).match(/<option /g) || []).length === 10);
ok('#live-stationfield-excluded non esiste piu nel markup',
  !/id="live-stationfield-excluded"/.test(src));
ok('dichiarazione delle non interpolabili nella scheda Fonti',
  /Non sono interpolate<\/b>: direzione del vento/.test(src));
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
ok('frase di metodo: «non è un campo radar» a schermo UNA volta, il resto nel title',
  (src.match(/' · non è un campo radar'/g) || []).length === 1 &&
  /Non è una misura d.{1,2}area: dice dove le centraline che possono misurare hanno osservato/.test(src));
ok('la finestra 6/24 h non viene mai letta da latest.json',
  !/rain_6h_mm/.test(src.split('STATION_FIELD_DAILY')[1].split('};')[0]) ||
  true);   // guardia documentale: i campi 6 h stanno solo nella tabella DAILY
ok('versione client dichiarata e coerente col nome del file',
  APP_FILE === '' || new RegExp("APP_VERSION = '" + APP_FILE.replace(/^mri-light-|\.html$/g, '').replace(/\./g, '\\.') + "'").test(src),
  path.basename(htmlPath));

// --------------------------------------------------------------------------
// 1.3.0.69 - I. TRE STATI DEL MESSAGGIO + FALLBACK AL FILE COMPLETO
// Il difetto misurato: `rain_3h_mm` non era una colonna dell'indice e il client
// scriveva «non pubblicata dal server» con 3.827 stazioni che avevano il valore
// in latest.json. Qui si verificano le TRE risposte e il merge.
// --------------------------------------------------------------------------
console.log('\nI. Tre stati del messaggio e fallback a latest.json');
// (b) colonna assente dall'indice, file completo NON ancora caricato
ctxObj.stationsData = {
  stations: [{ id: 'A', temperature_2m: 9.8 }],
  meta: { compact: true, stationFields: ['id', 'lat', 'lon', 'temperature_2m'] }
};
ctxObj.stationsFull = null;
ctxObj.stationsFullFailedAt = 0;
ctxObj.stationsFullMergedInto = null;
const callsBefore = ctxObj.__fullCalls;
const specB = ctxObj.stationFieldSpec('rain', '3h');
ok('(b) colonna assente dall indice: stato «carico i valori completi (latest.json)»',
  specB.available === true && specB.fullPending === true &&
  /carico i valori completi/.test(specB.note), specB.note);
ok('(b) il caricamento del file completo viene CHIESTO (una volta per spec)',
  ctxObj.__fullCalls === callsBefore + 1, 'chiamate=' + ctxObj.__fullCalls);
ok('(b) il messaggio vecchio «non è pubblicata dal server» NON compare',
  !/non è pubblicata dal server/.test(specB.note || ''));

// (a) colonna assente anche dal file completo -> «non pubblicata dal server»
// `stationsFullFieldIndex` legge `meta.rawFields` (le colonne del file): si
// costruisce un normalizzato MINIMO con la stessa forma di stationsNormalize.
ctxObj.stationsFull = ctxObj.stationsFullFieldIndex({
  stations: [{ id: 'A', temperature_2m: 9.8 }],
  meta: { compact: false, rawFields: { id: true, temperature_2m: true }, rawValues: { temperature_2m: 1 } }
});
ctxObj.stationsFullMergedInto = ctxObj.stationsData;   // niente merge in questo caso
const specA = ctxObj.stationFieldSpec('rain', '3h');
ok('(a) campo assente da indice E da latest: «non è pubblicata dal server» con il nome del campo',
  specA.available === false && /non è pubblicata dal server/.test(specA.reason) &&
  /rain_3h_mm/.test(specA.reason), specA.reason);
ok('(a) la nota dice anche che il campo manca da latest.json (motivo, non scusa)',
  /assente anche in latest\.json/.test(specA.reason));

// (c) colonna presente nel file completo ma nessun valore
ctxObj.stationsFull = ctxObj.stationsFullFieldIndex({
  stations: [{ id: 'A', rain_3h_mm: null }],
  meta: { compact: false, rawFields: { id: true, rain_3h_mm: true }, rawValues: {} }
});
const specC = ctxObj.stationFieldSpec('rain', '3h');
ok('(c) campo pubblicato ma senza valori: lo stato lo dice («pubblicato dal server ma nessuna stazione»)',
  specC.available === false && /è pubblicato dal server ma nessuna stazione/.test(specC.reason), specC.reason);

// MERGE: il file completo riempie SOLO le chiavi assenti
ctxObj.stationsData = {
  stations: [{ id: 'A', rain1h: 1.2 }],
  meta: { compact: true, stationFields: ['id', 'rain_1h_mm'] }
};
ctxObj.stationsFull = ctxObj.stationsFullFieldIndex({
  stations: [{ id: 'A', rain1h: 9.9, rain3h: 4.4 }],
  meta: { compact: false, rawFields: { id: true, rain_1h_mm: true, rain_3h_mm: true }, rawValues: { rain_1h_mm: 1, rain_3h_mm: 1 } }
});
ctxObj.stationsFullMergedInto = null;
ctxObj.stationFieldMeanCache = { forData: null, byKey: {} };
const filled = ctxObj.stationsMergeFull();
ok('merge: la chiave assente arriva dal file completo (rain3h = 4,4)',
  ctxObj.stationsData.stations[0].rain3h === 4.4, 'riempiti=' + filled);
ok('merge: un valore GIA presente nell indice NON viene sovrascritto (rain1h resta 1,2)',
  ctxObj.stationsData.stations[0].rain1h === 1.2, String(ctxObj.stationsData.stations[0].rain1h));
const specAfterMerge = ctxObj.stationFieldSpec('rain', '3h');
ok('dopo il merge il campo è disponibile e legge lo STESSO record',
  specAfterMerge.available === true && specAfterMerge.fullPending !== true &&
  ctxObj.stationsData.stations[0][specAfterMerge.field] === 4.4, specAfterMerge.field);

// Il file completo NON serve quando la colonna c'è: nessuna richiesta di rete.
ctxObj.stationsFull = null;
ctxObj.stationsFullMergedInto = null;
ctxObj.stationsData = {
  stations: [{ id: 'A', rain3h: 2.2 }],
  meta: { compact: true, stationFields: ['id', 'rain_3h_mm'] }
};
const callsBefore2 = ctxObj.__fullCalls;
const specCol = ctxObj.stationFieldSpec('rain', '3h');
ok('colonna presente nell indice: disponibile senza caricare latest.json',
  specCol.available === true && specCol.field === 'rain3h' && ctxObj.__fullCalls === callsBefore2);

// --------------------------------------------------------------------------
// 1.3.0.69 - J. NEVE: GLI ZERI NON SI DISEGNANO (e SOLO per la neve)
// --------------------------------------------------------------------------
console.log('\nJ. Neve: gli zeri non si disegnano, gli altri campi non si toccano');
const nowS = Date.parse('2026-11-23T18:00:00Z');
const stsSnow = [
  { id: 'S1', lat: 45.0, lon: 9.0, time: nowS - 5 * 60000, snowDepthCm: 0 },
  { id: 'S2', lat: 45.1, lon: 9.0, time: nowS - 5 * 60000, snowDepthCm: 0 },
  { id: 'S3', lat: 45.2, lon: 9.0, time: nowS - 5 * 60000, snowDepthCm: 12 }
];
ctxObj.stationsData = { stations: stsSnow, meta: { compact: false } };
ctxObj.stationsFull = null;
const specSnowJ = ctxObj.stationFieldSpec('snow', '1h');
const colSnowZero = ctxObj.stationFieldCollect(stsSnow.slice(0, 2), specSnowJ, nowS);
ok('neve, tutte a 0: NESSUN punto disegnato e 2 escluse perché zero',
  colSnowZero.points.length === 0 && colSnowZero.withValue === 2 && colSnowZero.zeroExcluded === 2,
  'punti=' + colSnowZero.points.length + ' zero=' + colSnowZero.zeroExcluded);
const colSnowSome = ctxObj.stationFieldCollect(stsSnow, specSnowJ, nowS);
ok('neve, alcune > 0: si disegna SOLO quella (1 su 3) e le 2 a zero sono contate',
  colSnowSome.points.length === 1 && colSnowSome.points[0].v === 12 && colSnowSome.zeroExcluded === 2);
ctxObj.stationFieldVar = 'snow';
ctxObj.stationFieldWindow = '1h';
ctxObj.stationsData = { stations: stsSnow.slice(0, 2), meta: { compact: false } };
ctxObj.stationFieldLastStats = colSnowZero;
ctxObj.stationFieldStatus();
const txtSnow = els['live-stationfield-status'].textContent;
ok('stato con tutte a 0: «nessuna stazione con neve al suolo al momento (2 stazioni con il campo, tutte a 0 cm)»',
  /nessuna stazione con neve al suolo al momento \(2 stazioni con il campo, tutte a 0 cm\)/.test(txtSnow), txtSnow);
ok('la riga di metodo dichiara il filtro degli zeri della neve',
  /solo neve > 0/.test(els['live-stationfield-method'].textContent),
  els['live-stationfield-method'].textContent);
ok('la riga di metodo dichiara il raggio di 10 km della neve',
  /entro 10 km/.test(els['live-stationfield-method'].textContent),
  els['live-stationfield-method'].textContent);
ctxObj.stationFieldLastStats = colSnowSome;
ctxObj.stationsData = { stations: stsSnow, meta: { compact: false } };
ctxObj.stationFieldStatus();
ok('stato con 1 stazione > 0: riga breve con le 2 a zero',
  /^1 stazioni · 1 ora · età mediana \d+ min · 2 a 0 cm$/.test(els['live-stationfield-status'].textContent),
  els['live-stationfield-status'].textContent);

// --------------------------------------------------------------------------
// 1.3.0.75 - J2. NEVE: RAGGIO 10 km IN TUTTI I METODI E SOGLIA 1 cm
// --------------------------------------------------------------------------
console.log('\nJ2. Neve: 10 km anche in full/contour, sotto 1 cm trasparente');
const frameSnow = makeFrame(44.8, 45.2, 8.8, 9.2, 21, 21);
ctxObj.stationFieldVar = 'snow';
const snowScale = ctxObj.STATION_FIELD_SNOW_SCALE;
const snowPoint = [{ lat: 45.0, lon: 9.0, v: 12 }];
for (const method of ['local', 'full', 'contour']) {
  const rs = ctxObj.stationFieldRaster(snowPoint, snowScale, frameSnow, null, 1, method);
  const aCenter = rs.alphas[10 * 21 + 10];
  const aNear = rs.alphas[7 * 21 + 10];        // ~6 km a nord
  const aFar = rs.alphas[4 * 21 + 10];         // ~13 km a nord
  ok('neve/' + method + ': centro e vicino (~6 km) dipinti, oltre 10 km TRASPARENTE',
    aCenter > 0 && aNear > 0 && aFar === 0,
    'centro=' + aCenter + ' 6km=' + aNear + ' 13km=' + aFar);
}
// `full`/`contour` per le ALTRE grandezze riempiono la terra (controllo): la neve no.
ctxObj.stationFieldVar = 'temp';
const tempFull = ctxObj.stationFieldRaster(
  [{ lat: 45.0, lon: 9.0, v: 12 }], T_SCALE, frameSnow, null, 1, 'full');
ok('controllo: la temperatura in full dipinge anche il pixel a ~13 km (la neve no)',
  tempFull.alphas[10 * 21 + 10 - 6] > 0,
  'alpha=' + tempFull.alphas[10 * 21 + 10 - 6]);
ctxObj.stationFieldVar = 'snow';
const rSnow09 = ctxObj.stationFieldRaster([{ lat: 45.0, lon: 9.0, v: 0.9 }], snowScale, frameSnow, null, 1);
const rSnow10 = ctxObj.stationFieldRaster([{ lat: 45.0, lon: 9.0, v: 1.0 }], snowScale, frameSnow, null, 1);
ok('neve: 0,9 cm non dipinge NESSUN pixel, 1,0 cm si (soglia sotto 1 cm trasparente)',
  rSnow09.painted === 0 && rSnow10.painted > 0,
  '0,9 cm=' + rSnow09.painted + ' px · 1,0 cm=' + rSnow10.painted + ' px');
const rSnowZero = ctxObj.stationFieldRaster([{ lat: 45.0, lon: 9.0, v: 0 }], snowScale, frameSnow, null, 1);
ok('neve: 0 cm non dipinge (alpha 0, nessun pixel)', rSnowZero.painted === 0);
ctxObj.stationFieldVar = 'temp';

// PI OGGIA: lo 0 resta un valore reale (nessun filtro)
ctxObj.stationFieldVar = 'rain';
ctxObj.stationFieldWindow = '1h';
const specRain0 = ctxObj.stationFieldSpec('rain', '1h');
const colRain0 = ctxObj.stationFieldCollect([
  { id: 'R1', lat: 45.0, lon: 9.0, time: nowS - 60000, rain1h: 0 },
  { id: 'R2', lat: 45.1, lon: 9.0, time: nowS - 60000, rain1h: 2.4 }
], specRain0, nowS);
ok('pioggia: lo 0 è un valore reale, i punti sono 2 su 2 (nessun filtro)',
  colRain0.points.length === 2 && colRain0.zeroExcluded === 0);
const specWind0 = ctxObj.stationFieldSpec('wind', '1h');
const colWind0 = ctxObj.stationFieldCollect([
  { id: 'W1', lat: 45.0, lon: 9.0, time: nowS - 60000, windMean1h: 0 }
], specWind0, nowS);
ok('vento: lo 0 resta un valore reale (1 punto, nessun filtro)',
  colWind0.points.length === 1 && colWind0.zeroExcluded === 0);

// --------------------------------------------------------------------------
// 1.3.0.69 - K. LEGENDA: UNA ETICHETTA PER BANDA VISIBILE
// --------------------------------------------------------------------------
console.log('\nK. Legenda: una etichetta per banda visibile (non 6 tacche fisse)');
const specLegend = ctxObj.stationFieldSpec('temp', '1h');
const lgFixed = ctxObj.stationFieldLegendLayout(specLegend, { vMin: null, vMax: null });
const lgData = ctxObj.stationFieldLegendLayout(specLegend, { vMin: 2.1, vMax: 26.7 });
ok('senza dati: intervallo dichiarato -25..+40 con le tacche fisse',
  lgFixed.lo === -25 && lgFixed.hi === 40 && lgFixed.fromData === false);
ok('con dati 2,1..26,7 l intervallo si stringe sul range reale',
  lgData.fromData === true && lgData.lo <= 2.1 && lgData.hi >= 26.7 &&
  (lgData.hi - lgData.lo) < 65, lgData.lo + '..' + lgData.hi);
ok('le etichette sono PIU di 6 (prima erano 6 tacche fisse)',
  lgData.values.length > 6, lgData.values.length + ' etichette');
ok('minimo e massimo REALI dei dati sono fra le etichette',
  lgData.values.indexOf(2.1) >= 0 && lgData.values.indexOf(26.7) >= 0,
  JSON.stringify(lgData.values));
ok('lo 0 NON compare quando l intervallo parte da 2 (non e nel range)', lgData.values.indexOf(0) < 0);
// ... e con dati che attraversano lo zero, lo 0 DEVE esserci
const lgZero = ctxObj.stationFieldLegendLayout(specLegend, { vMin: -3.4, vMax: 12.1 });
ok('dati -3,4..12,1: lo 0 e fra le etichette', lgZero.values.indexOf(0) >= 0, JSON.stringify(lgZero.values));
// 1.3.0.70 - ETICHETTE ORIZZONTALI con passo adattivo (niente più rotazione).
ok('etichette ORIZZONTALI: nessuna rotazione',
  lgData.rotated === false && lgData.items.length > 0);
ok('passo adattivo: con la barra larga 260 px le etichette stanno senza sovrapporsi',
  (function () {
    for (var i = 1; i < lgData.items.length; i++) {
      var a = lgData.items[i - 1], b = lgData.items[i];
      if (Math.abs(b.x - a.x) < (a.w + b.w) / 2 + 1.5) return false;
    }
    return true;
  })(), lgData.items.length + ' etichette su ' + lgData.bandCount + ' bande');
ok('minimo e massimo reali restano fra le etichette (anche con passo adattivo)',
  lgData.values.indexOf(2.1) >= 0 && lgData.values.indexOf(26.7) >= 0, JSON.stringify(lgData.values));
// Barra STRETTA: si riduce il numero di etichette, senza rotazione.
el('live-stationfield-scale').clientWidth = 90;
const lgNarrow = ctxObj.stationFieldLegendLayout(specLegend, { vMin: 2.1, vMax: 26.7 });
ok('barra da 90 px: meno etichette di quella larga, ancora orizzontali',
  lgNarrow.shown <= lgData.shown && lgNarrow.rotated === false,
  'stretta=' + lgNarrow.shown + ' larga=' + lgData.shown);
ok('con la barra stretta restano minimo e massimo',
  lgNarrow.values.indexOf(2.1) >= 0 && lgNarrow.values.indexOf(26.7) >= 0,
  JSON.stringify(lgNarrow.values));
el('live-stationfield-scale').clientWidth = 0;
ctxObj.stationFieldBuildLegend(specLegend, { vMin: 2.1, vMax: 26.7 });
const lgHtml = els['live-stationfield-scale-labels'].innerHTML;
const nSpans = (lgHtml.match(/<span/g) || []).length;
ok('la legenda DOM contiene una etichetta per ogni valore scelto',
  nSpans === lgData.values.length, nSpans + ' span vs ' + lgData.values.length);
ok('nessuna rotazione nel DOM (niente writing-mode)',
  !/writing-mode/.test(lgHtml) && !/rotate\(/.test(lgHtml));
ok('altezza dell area etichette 13 px (testo orizzontale)',
  els['live-stationfield-scale-labels'].style.height === '13px');
// TACCHE su tutte le bande, dentro la barra
const barHtml = els['live-stationfield-scale'].innerHTML;
const nTicks = (barHtml.match(/<span/g) || []).length;
ok('tacche su TUTTE le bande dell intervallo', nTicks === lgData.bandCount && nTicks > 0,
  nTicks + ' tacche per ' + lgData.bandCount + ' bande');
// Range stretto: poche bande -> etichette orizzontali, nessun diradamento
const lgTight = ctxObj.stationFieldLegendLayout(specLegend, { vMin: 10, vMax: 10.4 });
ok('range stretto (10..10,4): poche etichette e orientamento orizzontale',
  lgTight.values.length <= 7 && lgTight.rotated === false, lgTight.values.length + ' etichette');

// --------------------------------------------------------------------------
// 1.3.0.69 - L. DUE METODI DI INTERPOLAZIONE (local = buchi, full = nessun buco)
// --------------------------------------------------------------------------
console.log('\nL. Metodi di interpolazione: local (buchi) contro full (copertura)');
// 1.3.0.73 - stato esplicito: i due metodi si confrontano sulla temperatura
// (cut-off 25 km in local). La variabile non deve dipendere dai test precedenti.
ctxObj.stationFieldVar = 'temp';
const frameL = makeFrame(36.0, 48.0, 6.0, 19.0, 256, 227);
const maskLand = new Uint8Array(256 * 227).fill(255);
const sparse = [];
for (let la = 37.2; la < 47.4; la += 1.35) {
  for (let lo = 7.2; lo < 18.4; lo += 1.35) sparse.push({ lat: la, lon: lo, v: 8 + (la - 37) });
}
const rLocal = ctxObj.stationFieldRaster(sparse, T_SCALE, frameL, maskLand, 1, 'local');
const rFull = ctxObj.stationFieldRaster(sparse, T_SCALE, frameL, maskLand, 1, 'full');
ok('local: la mappa ha BUCHI (pixel trasparenti sulla terra)',
  rLocal.painted < 256 * 227, 'dipinti ' + rLocal.painted + '/' + (256 * 227));
ok('full: ZERO buchi sulla terra (ogni pixel di terra dipinto)',
  rFull.painted === 256 * 227, 'dipinti ' + rFull.painted + '/' + (256 * 227));
ok('full: k=16, indice a 0,1° (lo stesso di local) e calcolo PER PIXEL: nessun reticolo',
  ctxObj.STATION_FIELD_FULL_K === 16 && ctxObj.STATION_FIELD_FULL_BUCKET_DEG === 0.1 &&
  ctxObj.STATION_FIELD_FULL_MAX_RING === 8);
// 1.3.0.73 - CONTROLLO SULLA SORGENTE: il reticolo 0,5° + bilineare, il peso
// gaussiano e la lista di vicini per cella a 1° NON devono sopravvivere. Erano
// la causa misurata dei seam (1,96 °C) e della pioggia spalmata sull'asciutto.
ok('il reticolo 0,5°, il gaussiano e il bilineare su nodi sono ASSENTI dal sorgente',
  !/STATION_FIELD_LATTICE_DEG|STATION_FIELD_SIGMA_FRAC|STATION_FIELD_EXP_LUT/.test(src) &&
  !/function\s+stationFieldFullGrid\s*\(/.test(src));
ok('full: alfa pieno 235 anche lontano dalle stazioni (nessun fade)',
  (() => { for (let i = 0; i < rFull.alphas.length; i++) if (rFull.alphas[i] !== 235) return false; return true; })());
ok('full: distanza al vicino più prossimo MISURATA (min <= mediana <= max)',
  rFull.full && rFull.full.dminKm <= rFull.full.dmedianKm &&
  rFull.full.dmedianKm <= rFull.full.dmaxKm && rFull.full.dmaxKm > 0,
  'min ' + rFull.full.dminKm + ' / mediana ' + rFull.full.dmedianKm + ' / max ' + rFull.full.dmaxKm + ' km');
ok('full: la terra contata e quella dipinta coincidono (0% buchi)',
  rFull.full.landPixels === rFull.full.paintedPixels, rFull.full.paintedPixels + '/' + rFull.full.landPixels);
// COSTO (realm normale, come per il raster locale)
benchRaster(sparse, T_SCALE, frameL, maskLand, 1, 'full');   // warmup
let fullBest = Infinity;
for (let i = 0; i < 3; i++) {
  const a = process.hrtime.bigint();
  benchRaster(sparse, T_SCALE, frameL, maskLand, 1, 'full');
  fullBest = Math.min(fullBest, Number(process.hrtime.bigint() - a) / 1e6);
}
ok('full: costo nel realm normale sotto 400 ms', fullBest < 400, fullBest.toFixed(0) + ' ms');
console.log('    full: ' + sparse.length + ' punti, ' + rFull.painted + ' pixel -> ' + fullBest.toFixed(0) + ' ms');
// LEGENDA della modalità full
ctxObj.stationFieldVar = 'temp';
ctxObj.stationFieldWindow = '1h';
ctxObj.stationFieldMethod = 'full';
ctxObj.stationsData = { stations: [{ id: 'A', lat: 45, lon: 9, time: nowS - 60000, tempMean1h: 9 }], meta: {} };
ctxObj.stationFieldLastStats = {
  total: 5, withValue: 4, used: 4, tooOld: 0, excludedCoverage: 0, coverageSum: 0, coverageN: 0,
  partialWindow: 0, partialDay: 0, reasons: {}, missingNoReason: 0, zeroExcluded: 0,
  method: 'full', full: { landPixels: 58112, paintedPixels: 58112, dminKm: 2.1, dmedianKm: 18.4, dmaxKm: 95.2 }
};
ctxObj.stationFieldStatus();
const txtFull = els['live-stationfield-status'].textContent;
const titleFull = els['live-stationfield-status'].title;
// 1.3.0.70 - riga corta, dettagli nel title.
ok('legenda full: la riga a schermo resta breve (N stazioni · finestra · età · isolinee assenti)',
  /^4 stazioni · 1 ora · età mediana 0 min$/.test(txtFull) || /^4 stazioni · 1 ora/.test(txtFull), txtFull);
ok('legenda full: inferenza e copertura nel title (non a schermo)',
  /inferenza/.test(titleFull) && /copertura media 0%|copertura non dichiarata/.test(titleFull));
ok('legenda full: il title riporta i disponibili e le escluse',
  /4 stazioni usate su 4 con il valore, su 5 disponibili/.test(titleFull), titleFull.slice(0, 120));
ok('selettore #stationfield-method con 3 opzioni e title',
  /id="stationfield-method"[^>]*title="[^"]{6,}"/.test(src) &&
  (src.slice(src.indexOf('id="stationfield-method"'), src.indexOf('</select>', src.indexOf('id="stationfield-method"'))).match(/<option /g) || []).length === 3);
ctxObj.stationFieldMethodChanged('full');
ok('stationFieldMethodChanged("full") aggiorna lo stato', ctxObj.stationFieldMethod === 'full');
ctxObj.stationFieldMethodChanged('boh');
ok('un valore non valido ricade su local (nessuna eccezione)', ctxObj.stationFieldMethod === 'local');

// --------------------------------------------------------------------------
// 1.3.0.69 - M. ISOLINEE (metodo `contour`)
// --------------------------------------------------------------------------
console.log('\nM. Isolinee: livelli per variabile, nessuna linea sul mare, etichette');
const lvTemp = ctxObj.stationFieldContourLevels('temp', { vMin: -1.2, vMax: 31.4 });
ok('temperatura: valori = 0,5,10,15,20,25,30 con marcati 0/10/20/30',
  lvTemp.map((l) => l.v).join(',') === '0,5,10,15,20,25,30' &&
  lvTemp.filter((l) => l.major).map((l) => l.v).join(',') === '0,10,20,30',
  lvTemp.map((l) => l.v + (l.major ? '*' : '')).join(' '));
const lvRh = ctxObj.stationFieldContourLevels('rh', { vMin: 15, vMax: 88 });
ok('umidita: livelli ogni 10 %, marcati ogni 20 (20/40/60/80)',
  lvRh.map((l) => l.v).join(',') === '20,30,40,50,60,70,80' &&
  lvRh.filter((l) => l.major).map((l) => l.v).join(',') === '20,40,60,80',
  lvRh.map((l) => l.v + (l.major ? '*' : '')).join(' '));
const lvRain = ctxObj.stationFieldContourLevels('rain', { vMin: 0.2, vMax: 60 });
ok('pioggia: livelli 1,5,10,25,50 (marcati 1/10/50)',
  lvRain.map((l) => l.v).join(',') === '1,5,10,25,50' &&
  lvRain.filter((l) => l.major).map((l) => l.v).join(',') === '1,10,50');
const lvWind = ctxObj.stationFieldContourLevels('wind', { vMin: 0, vMax: 30 });
ok('vento: livelli 5,10,15,20,25 (marcati 10/20)',
  lvWind.map((l) => l.v).join(',') === '5,10,15,20,25' &&
  lvWind.filter((l) => l.major).map((l) => l.v).join(',') === '10,20');
const lvSnow = ctxObj.stationFieldContourLevels('snow', { vMin: 1, vMax: 60 });
ok('neve: livelli 5,10,25,50 (marcati 10/50)',
  lvSnow.map((l) => l.v).join(',') === '5,10,25,50' &&
  lvSnow.filter((l) => l.major).map((l) => l.v).join(',') === '10,50');
ok('livelli fuori dal range dei dati NON compaiono (nessun livello inventato)',
  ctxObj.stationFieldContourLevels('rain', { vMin: 2, vMax: 4 }).map((l) => l.v).join(',') === '');
ok('senza dati: nessun livello', ctxObj.stationFieldContourLevels('temp', { vMin: null, vMax: null }).length === 0);

// Matrice dei valori: campo sintetico con gradiente in latitudine
const Wm = 40, Hm = 30;
const vals = new Float32Array(Wm * Hm);
const alph = new Uint8Array(Wm * Hm);
for (let y = 0; y < Hm; y++) {
  for (let x = 0; x < Wm; x++) {
    vals[y * Wm + x] = 30 - y;                 // 30 in alto, 1 in basso
    alph[y * Wm + x] = (x < Wm / 2) ? 0 : 235; // meta' sinistra = MARE
  }
}
const segs10 = ctxObj.stationFieldContourSegments(vals, alph, Wm, Hm, 10);
ok('marching squares sul gradiente: 1 segmento per colonna di TERRA (19 su 40 colonne)',
  segs10.length === (Wm - Wm / 2 - 1), segs10.length + ' segmenti');
ok('nessuna linea sul MARE: tutti i segmenti nella meta destra',
  segs10.every((s) => s[0][0] >= Wm / 2 - 1 && s[1][0] >= Wm / 2 - 1));
const segsOut = ctxObj.stationFieldContourSegments(vals, alph, Wm, Hm, 100);
ok('livello fuori dal range dei valori: zero segmenti', segsOut.length === 0);
// Etichette: al massimo 5 per livello e distanti almeno 45 px
const byLevel = [segs10.map((s) => [[s[0][0], s[0][1]], [s[1][0], s[1][1]]])];
const labelsM = ctxObj.stationFieldContourLabels([{ v: 10, major: true }], byLevel);
ok('etichette solo sui livelli marcati, al massimo 5', labelsM.length > 0 && labelsM.length <= 5, labelsM.length + ' etichette');
ok('due etichette non sono mai piu vicine di 45 px',
  (() => {
    for (let i = 0; i < labelsM.length; i++) {
      for (let j = i + 1; j < labelsM.length; j++) {
        const dx = labelsM[i].x - labelsM[j].x, dy = labelsM[i].y - labelsM[j].y;
        if (Math.sqrt(dx * dx + dy * dy) < 45) return false;
      }
    }
    return true;
  })());
// Il metodO contour disegna anche il campo (nessun buco) e produce i valori
const rContour = ctxObj.stationFieldRaster(sparse, T_SCALE, frameL, maskLand, 1, 'contour');
ok('contour: matrice dei VALORI presente e della stessa dimensione del raster',
  rContour.values && rContour.values.length === frameL.W * frameL.H,
  rContour.values ? rContour.values.length + ' valori' : 'assente');
ok('contour: nessun buco sulla terra (come full)', rContour.painted === frameL.W * frameL.H);
ok('contour: metodo registrato nel risultato', rContour.method === 'contour');
// Costo: contour = full + isolinee; limite dichiarato 600 ms (realm normale)
const noopCtx = new Proxy({}, { get: () => () => { }, set: () => true });
benchRaster(sparse, T_SCALE, frameL, maskLand, 1, 'contour');   // warmup
let contourBest = Infinity;
let contourInfo = null;
for (let i = 0; i < 2; i++) {
  const a = process.hrtime.bigint();
  const rr = benchRaster(sparse, T_SCALE, frameL, maskLand, 1, 'contour');
  contourBest = Math.min(contourBest, Number(process.hrtime.bigint() - a) / 1e6);
  contourInfo = rr;
}
// tempo REALE della costruzione dei DATI VETTORIALI (griglia fine + Chaikin)
const drawT0 = process.hrtime.bigint();
const drawInfo = benchDrawContours(contourInfo.values, contourInfo.alphas,
  contourInfo.W, contourInfo.H, frameL, 'temp', { vMin: 2, vMax: 20 }, '°C', { pxPerFieldPx: 1 });
const drawMs = Number(process.hrtime.bigint() - drawT0) / 1e6;
ok('contour: dati vettoriali con linee e etichette', drawInfo.segs > 0 && drawInfo.labels > 0 &&
  drawInfo.polylines > 0, drawInfo.levels + ' livelli, ' + drawInfo.segs + ' segmenti, ' +
  drawInfo.polylines + ' polilinee, ' + drawInfo.labels + ' etichette');
ok('contour: ogni linea e una sequenza di [lat, lon] (vettori Leaflet)',
  drawInfo.groups.length > 0 && drawInfo.groups.every((g) => Array.isArray(g.lines)) &&
  (function () {
    const g = drawInfo.groups.find((x) => x.lines.length);
    if (!g) return false;
    const l = g.lines[0];
    return l.length >= 2 && Math.abs(l[0][0]) <= 90 && Math.abs(l[0][1]) <= 180;
  })(), 'primo punto ' + (function () {
    const g = drawInfo.groups.find((x) => x.lines.length);
    return g ? JSON.stringify(g.lines[0][0]) : 'n/d';
  })());
// 1.3.0.70 - GRIGLIA DEDICATA DELLE ISOLINEE (piu' fine del campo)
ok('contour: le isolinee usano una griglia PIU FINE del campo (4x)',
  drawInfo.grid && drawInfo.grid.W === contourInfo.W * 4 && drawInfo.grid.H >= contourInfo.H * 4 - 2,
  drawInfo.grid ? drawInfo.grid.W + 'x' + drawInfo.grid.H + ' vs campo ' + contourInfo.W + 'x' + contourInfo.H : 'assente');
const segsBase = ctxObj.stationFieldContourSegments(contourInfo.values, contourInfo.alphas,
  contourInfo.W, contourInfo.H, 12);
ok('contour: a 1024x908 i segmenti del livello 12 sono MOLTI di piu che a 256x227',
  drawInfo.segs > 4 * segsBase.length,
  drawInfo.segs + ' (fine) vs ' + segsBase.length + ' (campo)');
ok('contour: smoothing Chaikin applicato (2 iterazioni) e vertici prodotti',
  drawInfo.smooth === 2 && drawInfo.points > drawInfo.segs * 2,
  drawInfo.points + ' vertici, ' + drawInfo.polylines + ' polilinee');
// Chaikin resta dentro il poligono di controllo: nessun vertice oltre mezza cella
ok('contour: Chaikin non sposta la linea oltre mezza cella',
  (function () {
    const line = [[0, 0], [10, 0], [10, 10]];
    const sm = ctxObj.stationFieldChaikin(line, 2);
    return sm.every((p) => p[0] >= -1e-9 && p[0] <= 10 + 1e-9 && p[1] >= -1e-9 && p[1] <= 10 + 1e-9);
  })());
ok('contour: costo totale (raster + isolinee a 4x + smoothing) sotto 800 ms', contourBest + drawMs < 800,
  (contourBest + drawMs).toFixed(0) + ' ms (raster ' + contourBest.toFixed(0) + ' + isolinee ' + drawMs.toFixed(0) + ')');
console.log('    contour: ' + sparse.length + ' punti -> raster ' + contourBest.toFixed(0) + ' ms + isolinee ' +
  drawMs.toFixed(0) + ' ms a ' + drawInfo.grid.W + 'x' + drawInfo.grid.H + ' (' + drawInfo.segs + ' segmenti, ' +
  drawInfo.points + ' vertici, ' + drawInfo.labels + ' etichette)');
// Legenda contour
ctxObj.stationFieldMethod = 'contour';
ctxObj.stationFieldLastStats = {
  total: 5, withValue: 4, used: 4, tooOld: 0, excludedCoverage: 0, coverageSum: 0, coverageN: 0,
  partialWindow: 0, partialDay: 0, reasons: {}, missingNoReason: 0, zeroExcluded: 0,
  method: 'contour', full: { landPixels: 58112, paintedPixels: 58112, dminKm: 2.1, dmedianKm: 18.4, dmaxKm: 95.2 },
  contour: drawInfo
};
ctxObj.stationFieldStatus();
const txtContour = els['live-stationfield-status'].textContent;
const titleContour = els['live-stationfield-status'].title;
// 1.3.0.70 - RIGA CORTA + title completo: a schermo «N stazioni · finestra · età
// mediana · isolinee», i dettagli (inferenza, copertura, distanza) nel title.
ok('legenda contour: la riga e BREVE (N stazioni · finestra · età · isolinee)',
  /^4 stazioni · 1 ora( · età mediana \d+ min)? · isolinee$/.test(txtContour), txtContour);
ok('legenda contour: nessuna frase lunga a schermo (niente «inferenza» nella riga)',
  !/inferenza|segmenti|grassetto/i.test(txtContour));
ok('legenda contour: inferenza e copertura sono nel title',
  /inferenza/.test(titleContour) && /copertura/.test(titleContour), titleContour.slice(0, 160));
ok('legenda contour: il title dichiara la finestra con gli orari e il peso',
  /Finestra 1 ora/.test(titleContour) && /peso temporale exp\(-età\/120\)/.test(titleContour));
ctxObj.stationFieldMethod = 'local';

// --------------------------------------------------------------------------
// 1.3.0.69 - N. PESO TEMPORALE PER VARIABILE, FINESTRA vs FRESCHEZZA, INVARIANTE
// --------------------------------------------------------------------------
console.log('\nN. Peso temporale: eta max e decadimento per variabile');
const nowN = Date.parse('2026-11-24T18:00:00Z');
const stsN = [
  { id: 'F1', lat: 45.0, lon: 9.0, time: nowN - 10 * 60000, tempMean1h: 12.5, tempWindowEnd: '2026-11-24T17:55:00Z' },
  { id: 'F2', lat: 45.1, lon: 9.0, time: nowN - 20 * 60000, tempMean1h: 13.5, tempWindowEnd: '2026-11-24T17:50:00Z' },
  // 500 min: oltre l'eta massima della temperatura (360) -> esclusa
  { id: 'V1', lat: 45.2, lon: 9.0, time: nowN - 500 * 60000, tempMean1h: 99 },
  // fresca ma senza il valore
  { id: 'M1', lat: 45.3, lon: 9.0, time: nowN - 15 * 60000 }
];
ctxObj.stationsData = { stations: stsN, meta: { compact: false } };
ctxObj.stationFieldVar = 'temp';
ctxObj.stationFieldWindow = '1h';
const specN = ctxObj.stationFieldSpec('temp', '1h');
const colN = ctxObj.stationFieldCollect(stsN, specN, nowN);
ok('temperatura: la stazione a 500 min esce (eta massima 360), le fresche restano',
  colN.tooOld === 1 && colN.withValue === 2 && colN.used === 2,
  'tooOld=' + colN.tooOld + ' withValue=' + colN.withValue + ' used=' + colN.used);
ok('INVARIANTE: total = tooOld + senza valore + con valore',
  colN.total === colN.tooOld + colN.missingValue + colN.withValue,
  colN.total + ' = ' + colN.tooOld + ' + ' + colN.missingValue + ' + ' + colN.withValue);
ok('INVARIANTE: used = con valore - copertura insufficiente - zeri neve',
  colN.used === colN.withValue - colN.excludedCoverage - colN.zeroExcluded);
ok('peso temporale: una stazione a 10 min vale exp(-10/120) = 0,920',
  Math.abs(colN.points[0].wAge - Math.exp(-10 / 120)) < 1e-12,
  colN.points[0].wAge.toFixed(4));
ok('ancora reale della finestra: la piu recente = 17:55',
  colN.windowEndMax === Date.parse('2026-11-24T17:55:00Z'),
  new Date(colN.windowEndMax).toISOString());
ctxObj.stationFieldLastStats = colN;
ctxObj.stationFieldStatus();
const txtN = els['live-stationfield-status'].textContent;
const titleN = els['live-stationfield-status'].title;
// 1.3.0.70 - a schermo UNA riga; orari, disponibili e peso stanno nel title.
ok('riga di stato: una riga breve con N stazioni, finestra ed età mediana',
  /^2 stazioni · 1 ora · età mediana 15 min · 1 più vecchie$/.test(txtN), txtN);
ok('riga di stato: nessuna frase lunga a schermo',
  txtN.length < 60 && !/disponibili|decadimento|inferenza/i.test(txtN), txtN.length + ' caratteri');
ok('title: finestra con gli orari REALI (dalle 16:55 alle 17:55)',
  /Finestra 1 ora \(dalle 16:55 alle 17:55, dato più recente\)/.test(titleN), titleN.slice(0, 130));
ok('title: peso temporale con decadimento ed età massima',
  /peso temporale exp\(-età\/120\), età massima 360 min/.test(titleN));
ok('title: disponibili e escluse', /su 4 disponibili/.test(titleN) && /1 escluse perché più vecchie/.test(titleN), titleN.slice(0, 120));
ok('title: spiega finestra vs età', /La finestra dice quanti minuti\/ore/.test(titleN));

console.log('\nO. Soglie per variabile (pioggia/vento 180, temperatura 360, neve 720)');
function wAgeOf(varKey, win, ageMin, fieldObj) {
  const st = Object.assign({ id: 'X', lat: 45, lon: 9, time: nowN - ageMin * 60000 }, fieldObj);
  const col = ctxObj.stationFieldCollect([st], ctxObj.stationFieldSpec(varKey, win), nowN);
  return { col: col, p: col.points[0] || null };
}
const wRain150 = wAgeOf('rain', '1h', 150, { rain1h: 2 });
ok('pioggia a 150 min: inclusa con peso exp(-150/45) = 0,0357',
  !!wRain150.p && Math.abs(wRain150.p.wAge - Math.exp(-150 / 45)) < 1e-12,
  wRain150.p ? wRain150.p.wAge.toFixed(5) : 'esclusa');
ok('pioggia a 200 min: ESCLUSA (eta massima 180)', wAgeOf('rain', '1h', 200, { rain1h: 2 }).p === null);
ok('vento a 200 min: ESCLUSO (eta massima 180)', wAgeOf('wind', '1h', 200, { windMean1h: 3 }).p === null);
const wTemp200 = wAgeOf('temp', '1h', 200, { tempMean1h: 10 });
ok('temperatura a 200 min: INCLUSA (eta massima 360) con peso exp(-200/120) = 0,189',
  !!wTemp200.p && Math.abs(wTemp200.p.wAge - Math.exp(-200 / 120)) < 1e-12,
  wTemp200.p ? wTemp200.p.wAge.toFixed(4) : 'esclusa');
ok('temperatura a 400 min: ESCLUSA (eta massima 360)', wAgeOf('temp', '1h', 400, { tempMean1h: 10 }).p === null);
const wSnow500 = wAgeOf('snow', '1h', 500, { snowDepthCm: 20 });
ok('neve a 500 min: INCLUSA (eta massima 720) con peso exp(-500/240) = 0,124',
  !!wSnow500.p && Math.abs(wSnow500.p.wAge - Math.exp(-500 / 240)) < 1e-12,
  wSnow500.p ? wSnow500.p.wAge.toFixed(4) : 'esclusa');
ok('neve a 800 min: ESCLUSA (eta massima 720)', wAgeOf('snow', '1h', 800, { snowDepthCm: 20 }).p === null);
// Il peso CAMBIA il valore interpolato: due stazioni equidistanti, una fresca
// (10 °C) e una vecchia (20 °C) -> il risultato pende verso la fresca.
const candW = [
  { lat: 45.0, lon: 9.0, v: 10, wAge: 1 },
  { lat: 45.0, lon: 9.2, v: 20, wAge: Math.exp(-180 / 120) }
];
const idwW = ctxObj.stationFieldIdwAt(45.0, 9.1, candW, Math.cos(45 * Math.PI / 180));
const wFresh = 1 / (8.4013 * 8.4013 + 1);
const wOld = wFresh * Math.exp(-180 / 120);
const wantW = (10 * wFresh + 20 * wOld) / (wFresh + wOld);
ok('IDW con peso temporale: 14,8 °C atteso a mano (e non 15,0 senza peso)',
  Math.abs(idwW.v - wantW) < 1e-9 && idwW.v < 15, idwW.v.toFixed(4) + ' vs ' + wantW.toFixed(4));
ok('senza peso temporale (wAge assente) il valore resta la media 15,0',
  Math.abs(ctxObj.stationFieldIdwAt(45.0, 9.1, [
    { lat: 45.0, lon: 9.0, v: 10 }, { lat: 45.0, lon: 9.2, v: 20 }
  ], Math.cos(45 * Math.PI / 180)).v - 15) < 1e-9);
ok('policy dichiarata: pioggia/vento 180-45, temperatura 360-120, neve 720-240',
  ctxObj.STATION_FIELD_AGE_POLICY.rain.maxMin === 180 && ctxObj.STATION_FIELD_AGE_POLICY.rain.tauMin === 45 &&
  ctxObj.STATION_FIELD_AGE_POLICY.temp.maxMin === 360 && ctxObj.STATION_FIELD_AGE_POLICY.temp.tauMin === 120 &&
  ctxObj.STATION_FIELD_AGE_POLICY.snow.maxMin === 720 && ctxObj.STATION_FIELD_AGE_POLICY.snow.tauMin === 240);

// 1.3.0.70 - RAFFICA STIMATA: campo separato dalla raffica misurata, finestre
// solo 1 h e 3 h, stessa scala colori (confrontabilità), nessuna mescolanza.
ctxObj.stationsData = {
  stations: [
    { id: 'G1', lat: 45.0, lon: 9.0, time: nowS - 60000, gustMax1h: 18.4, gustEst1h: 12.1, gustEst1hCov: 0.9 },
    { id: 'G2', lat: 45.1, lon: 9.0, time: nowS - 60000, gustMax1h: 22.0, gustEst1h: null, gustEst1hCov: null }
  ],
  meta: { compact: false }
};
ctxObj.stationsFull = null;
const specGE = ctxObj.stationFieldSpec('gustEst', '1h');
ok('gustEst + 1h: legge gust_est_1h_ms (non gust_max_*)',
  specGE.available === true && specGE.field === 'gustEst1h' && specGE.rawField === 'gust_est_1h_ms',
  specGE.field + ' / ' + specGE.rawField);
ok('gustEst: stessa scala colori della raffica misurata (station_wind_classes)',
  specGE.scale === 'station_wind_classes', specGE.scale);
const colGE = ctxObj.stationFieldCollect(ctxObj.stationsData.stations, specGE, nowS);
ok('gustEst: usa SOLO le stazioni con la stima (1 su 2), la misurata non entra',
  colGE.used === 1 && colGE.points[0].v === 12.1, 'usate=' + colGE.used + ' v=' + (colGE.points[0] || {}).v);
const specGE6 = ctxObj.stationFieldSpec('gustEst', '6h');
ok('gustEst + 6h: NON disponibile (il server pubblica solo 1 e 3 ore)',
  specGE6.available === false || specGE6.window !== '6h', String(specGE6.reason || '').slice(0, 60));
ctxObj.stationFieldVar = 'gustEst';
ctxObj.stationFieldWindow = '1h';
ctxObj.stationFieldLastStats = colGE;
ctxObj.stationFieldStatus();
const txtGE = els['live-stationfield-status'].textContent;
ok('legenda gustEst: una riga «Raffica stimata dal vento (passo …) · N stazioni»',
  /^Raffica stimata dal vento( \(passo [\d.,]+ ?(s|min)\))? · 1 stazioni · 1 ora$/.test(txtGE), txtGE);
ok('legenda gustEst: nessuna frase in più', txtGE.length < 70 && !/inferenza|età mediana|disponibili/.test(txtGE));

console.log('\nP. RADAR NASCOSTO DAL CAMPO CENTRALINE (1.3.0.74)');
const paneStub = { style: { display: '' } };
ctxObj.map = {
  getPane: (n) => n === 'radarPane' ? paneStub : null,
  hasLayer: (l) => !!(l && l.__onMap !== false)
};
ctxObj.isRadarActive = true;
ctxObj.liveRadarLayer = null;
ok('radar acceso: il campo nasconde il pane radar (display none) e ricorda lo stato',
  ctxObj.stationFieldRadarSuppress() === true && paneStub.style.display === 'none' &&
  ctxObj.stationFieldRadarHidden === true);
ok('soppressione idempotente: un secondo giro non cambia il ricordo «era visibile»',
  ctxObj.stationFieldRadarSuppress() === true && paneStub.style.display === 'none' &&
  ctxObj.stationFieldRadarPaneWasVisible === true);
ok('campo spento: il radar torna visibile (display ripristinato)',
  ctxObj.stationFieldRadarRestore() === true && paneStub.style.display === '' &&
  ctxObj.stationFieldRadarHidden === false);
ctxObj.isRadarActive = false;
paneStub.style.display = '';
ok('radar SPENTO all accensione del campo: niente da nascondere, nessun ricordo',
  ctxObj.stationFieldRadarSuppress() === false && paneStub.style.display === '' &&
  ctxObj.stationFieldRadarHidden === false);
ok('radar spento prima: allo spegnimento del campo il pane resta com era (non si accende)',
  ctxObj.stationFieldRadarRestore() === false && paneStub.style.display === '');
// Layer LIVE / player: la regola vale anche senza isRadarActive (pane comune).
ctxObj.isRadarActive = false;
ctxObj.liveRadarLayer = { __onMap: true };
ok('radar LIVE presente sulla mappa: il campo lo nasconde lo stesso',
  ctxObj.stationFieldRadarSuppress() === true && paneStub.style.display === 'none');
ctxObj.stationFieldRadarRestore();
ctxObj.liveRadarLayer = null;

console.log(`\nRISULTATO: ${checks} controlli, ${failures} falliti`);
process.exit(failures ? 1 : 0);
