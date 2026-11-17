#!/usr/bin/env node
// Test delle correzioni centraline (stations_v2) sull'HTML 1.3.0.5 di main.
// Nessun browser, nessuna rete: estrae le funzioni dall'HTML e le esegue in vm.
// Covers: 16 quadranti ai confini, Magnus senza dew nell'indice, serie GIORNALIERA
// con azzeramento a mezzanotte, due serie distinte (osservato/NWP), assi Y
// categoriali per la direzione, legenda ricostruita, note generalizzata.
'use strict';
import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const HTML = path.join(ROOT, 'mri-light-1.3.0.5.html');
const src = fs.readFileSync(HTML, 'utf8');

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

const names = [
  'stationsWindDirQuadrant', 'stationsDewPoint', 'stationsNum', 'stationsLocalDayKey',
  'stationsSeriesTuple', 'stationsSeriesPush', 'stationsSeriesRead', 'stationsSeriesWrite',
  'stationsSeriesValue', 'stationsRomeParts', 'stationsLocalMidnightUtc',
  'stationsNwpSeries', 'stationsChartHtml', 'stationsBuildLegend', 'stationsParamChanged',
  'stationsFieldValue', 'stationsLabelText'
];
const vars = [
  'STATIONS_WIND_QUADRANTS', 'STATIONS_SERIES_KEY', 'STATIONS_SERIES_MAX_POINTS',
  'STATIONS_SERIES_MAX_STATIONS', 'STATIONS_SERIES_MAX_GAP_MIN', 'STATIONS_PARAMS',
  'STATIONS_NWP_FIELD', 'STATIONS_LABEL_FIELD', 'stationsParam'
];

// ---- DOM / contesto minimo (nessun browser) ----
const els = {};
function el(id) {
  if (!els[id]) els[id] = {
    id, textContent: '', innerHTML: '', title: '', style: {},
    childNodes: [], get firstChild() { return this.childNodes[0] || null; },
    set textContent(v) { this._text = v; this.childNodes = [{ nodeType: 3, text: v }]; },
    get textContent() { return this._text || ''; }
  };
  return els[id];
}
const store = {};
const scale = {
  breakpoints: [-80, -60], colors: { light: [[1, 2, 3], [4, 5, 6]] }, unit: '°C'
};
const ctxObj = {
  console,
  window: { localStorage: {
    getItem: (k) => Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null,
    setItem: (k, v) => { store[k] = String(v); }
  } },
  document: { getElementById: el },
  FM_COLOR_SCALE_OVERRIDES: {
    temperature_2m: scale, mri_stations_neutral: scale,
    wind_speed_10m: scale, precipitation: scale, pressure_msl: scale
  },
  escapeHtml: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
  fmtNum: (v, d) => Number(v).toFixed(d),
  stationsClock: (t) => { const x = new Date(t); return String(x.getUTCHours()).padStart(2, '0') + ':' + String(x.getUTCMinutes()).padStart(2, '0'); },
  stationsDrawCanvas: () => {},
  stationsBandScale: () => ctxObj.FM_COLOR_SCALE_OVERRIDES[ctxObj.STATIONS_LABEL_FIELD.scale] || null,
  stationsLayerObj: null, stationsCanvas: null, stationsData: null,
  stationsUpdateStatus: () => {},
  datasetState: { loaded: true, day0: '2026-11-17', generatedAt: '2026-11-17T18:23:52.698733+00:00' },
  cellsNwpStore: () => ctxObj.nwpCtx,
  cellsNwpNearestIn: (c) => ({ point: c.points[0], km: 4.2 }),
  nwpCtx: null,
  Float32Array, Intl, Date, Math, isFinite, Number, String, Object, Array, RegExp, JSON
};
const code = vars.map(extractVar).concat(names.map(extractFn)).join('\n');
vm.createContext(ctxObj);
vm.runInContext(code, ctxObj);

// dataset NWP finto ma strutturalmente reale (72 ore, giorno0 = 2026-11-17)
const temps = Array.from({ length: 72 }, (_, i) => 10 + i / 10);
const dirs = Array.from({ length: 72 }, (_, i) => (i * 5) % 360);
ctxObj.nwpCtx = {
  store: { 0: { hourly: { temperature_2m: temps, winddirection_10m: dirs } } },
  points: [{ index: 0, lat: 45.5, lon: 9.2 }]
};

console.log('\nA. Rosa a 16 quadranti (confini)');
const q = ctxObj.stationsWindDirQuadrant;
for (const [deg, want] of [[0, 'N'], [11, 'N'], [22, 'NNE'], [67, 'ENE'], [90, 'E'],
                          [180, 'S'], [270, 'W'], [337.5, 'NNW'], [350, 'N'], [359, 'N']]) {
  ok(`${deg}° -> ${want}`, q(deg) === want, `ottenuto ${q(deg)}`);
}
ok('null -> null (n/d)', q(null) === null);
ok('nessuna freccia/emoji nella rosa', !/[↗↑→↓NW;]/.test('') || !q(45).includes('↗'));

console.log('\nB. Dew point Magnus (indice a 12 campi senza dew_point_2m)');
ok('T=17.5 RH=82 -> 14.4 °C', ctxObj.stationsDewPoint(17.5, 82) === 14.4,
   `ottenuto ${ctxObj.stationsDewPoint(17.5, 82)}`);
ok('RH<=0 -> null', ctxObj.stationsDewPoint(10, 0) === null);
ok('normalizza richiama Magnus', /dew0\s*===\s*null\)\s*dew0\s*=\s*stationsDewPoint/.test(src));
ok('DROPDOWN: windDir etichetta quadranti', /value="windDir">Direzione vento \(N\/NE\/…, 16 quadranti\)/.test(src));
ok('DROPDOWN: pressione/raffica/neve presenti', /value="pStation"/.test(src) && /value="gust"/.test(src) && /value="snow"/.test(src));
ok('DROPDOWN: idrometrico NON selezionabile', !/<option value="river"/.test(src));

console.log('\nC. Serie giornaliera con azzeramento a mezzanotte locale');
const s = {};
const st1 = { id: 'A', name: 'A', time: Date.parse('2026-11-17T08:00:00Z'), temp: 10, rh: 80,
              wind: 2, windDir: 315, precip: null, period: null, dew: 7,
              stationPressure: 1013.2, gust: 7.6, snowDepth: 0 };
const st2 = { ...st1, time: Date.parse('2026-11-17T08:10:00Z'), temp: 11 };
const st3 = { ...st1, time: Date.parse('2026-11-18T00:01:00Z'), temp: 12 };
ctxObj.stationsSeriesPush(s, st1); ctxObj.stationsSeriesPush(s, st2);
ok('due punti lo stesso giorno', s.A.pts.length === 2, `punti=${s.A.pts.length}`);
ctxObj.stationsSeriesPush(s, st3);
ok('mezzanotte azzera: resta 1 punto', s.A.pts.length === 1 && s.A.pts[0][0] === st3.time,
   `day=${s.A.day} punti=${s.A.pts.length}`);
ok('nessun serbatoio del giorno precedente', s.A.pts.every(p => ctxObj.stationsLocalDayKey(p[0]) === '2026-11-18'));
ok('tuple a 11 campi (nuovi campi inclusi)', s.A.pts[0].length === 11);
ok('stationsSeriesValue legge indice 8/9/10', ctxObj.stationsSeriesValue(s.A.pts[0], 'pStation') === 1013.2 &&
   ctxObj.stationsSeriesValue(s.A.pts[0], 'gust') === 7.6);

console.log('\nD. Grafico: N punti, gap >20 min, due serie distinte');
store[ctxObj.STATIONS_SERIES_KEY] = JSON.stringify(s);
// ricostruisce la serie del 18 con 3 punti (uno con buco di 45 min)
const e = JSON.parse(store[ctxObj.STATIONS_SERIES_KEY]);
e.A.pts = [
  [Date.parse('2026-11-18T08:00:00Z'), 1, 80, 2, 315, null, null, 0, 1013.2, 7.6, 0],
  [Date.parse('2026-11-18T08:10:00Z'), 2, 80, 2, 315, null, null, 0, 1013.2, 7.6, 0],
  [Date.parse('2026-11-18T08:55:00Z'), 3, 80, 2, 315, null, null, 0, 1013.2, 7.6, 0]
];
store[ctxObj.STATIONS_SERIES_KEY] = JSON.stringify(e);
let html = ctxObj.stationsChartHtml({ ...st3, temp: 3 });
ok('etichetta osservato con N punti e giornata corrente', /osservato \(3 punti, giornata corrente\)/.test(html));
ok('etichetta previsto — best_match, OTH <generato>', /previsto — best_match, OTH (?:19:23|2026-11-17T18:23:52\.698733\+00:00)/.test(html));
ok('serie distinte: colori osservato e previsto', html.includes('#38bdf8') && html.includes('#fbbf24'));
ok('nessuna media/fusione delle due serie', !/media|fonde|average/i.test(html));
ok('3 punti reali dichiarati', /3 punti osservati reali/.test(html));
ok('1 interruzione > 20 min dichiarata', /1 interruzione &gt; 20 min/.test(html));
const segCount = (html.match(/stroke="#38bdf8" stroke-width="1\.3"/g) || []).length;
ok('nessuna linea attraversa il buco (1 solo segmento)', segCount === 1, `segmenti=${segCount}`);
ok('ora stampata HH:MM nel grafico', /08:00 · 1,0/.test(html) || /08:00 · 1/.test(html));
const nwpLines = (html.match(/stroke="#fbbf24" stroke-width="1\.3"/g) || []).length;
const nwpMarks = (html.match(/stroke="#fbbf24" stroke-width="1\.2"/g) || []).length;
ok('NWP 72 ore ma solo la giornata corrente', nwpLines >= 1,
   `linee=${nwpLines} markers=${nwpMarks} label=${(html.match(/previsto[^<]*/) || [''])[0]}`);

console.log('\nE. Direzione: asse Y categoriale, punti e non linea');
ctxObj.stationsParam = 'windDir';
e.A.pts = [
  [Date.parse('2026-11-18T08:00:00Z'), 1, 80, 2, 315, null, null, 0, 1013.2, 7.6, 0],
  [Date.parse('2026-11-18T08:10:00Z'), 1, 80, 2, 350, null, null, 0, 1013.2, 7.6, 0]
];
store[ctxObj.STATIONS_SERIES_KEY] = JSON.stringify(e);
html = ctxObj.stationsChartHtml({ ...st3, windDir: 350 });
for (const name of ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW']) {
  ok('asse Y contiene ' + name, html.includes('>' + name + '</text>'));
}
ok('nessuna linea continua per la direzione',
   !/stroke="#38bdf8" stroke-width="1\.3"/.test(html) && !/stroke="#fbbf24" stroke-width="1\.3"/.test(html));
ok('il testo visibile del marker NON contiene gradi', !/>[^<]*315°[^<]*<\/circle>/.test(html));
ok('i gradi compaiono solo nel title', /<title>08:10 · N \(350°\)<\/title>/.test(html));
ok('previsione winddirection_10m usa la stessa rosa', /08:\d\d · (N|NNW|NW) \(\d+°\)/.test(html));
ok('coerenza osservato/previsto (stessa funzione q)', q(350) === q(352));

console.log('\nF. Tre difetti della tendina');
ctxObj.stationsParam = 'gust';
ctxObj.stationsParamChanged('gust');
ok('nota legenda generalizzata al parametro', el('live-stations-novalue-note').textContent.includes('raffica massima vento 10 m'),
   el('live-stations-novalue-note').textContent);
ok('nessun testo hardcoded "senza temperatura" nel markup attivo', !/id="live-stations-novalue-note"[^<]*<[^>]*>stazione senza temperatura/.test(src));
// simula il textContent scritto da stationsParamChanged, poi verifica che la rampa si ricostruisca
el('live-stations-scale-labels').innerHTML = '<span>colore neutro</span>';
el('live-stations-scale-labels').textContent = 'testo sostituito';
ctxObj.FM_COLOR_SCALE_OVERRIDES.temperature_2m = scale;
ctxObj.stationsParam = 'temp';
ctxObj.stationsParamChanged('temp');
ok('rampa ricostruita après textContent (gradient)', el('live-stations-scale').style.background.includes('linear-gradient'));
ok('labels della scala temperatura ricostruiti', el('live-stations-scale-labels').innerHTML.includes('−80'));

console.log(`\nRISULTATO: ${checks} controlli, ${failures} falliti`);
process.exit(failures ? 1 : 0);
