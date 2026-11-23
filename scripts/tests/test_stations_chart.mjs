#!/usr/bin/env node
// TEST 1.3.0.61 - GRAFICO DELLE CENTRALINE: caricamento del blob giornaliero,
// ordine delle sorgenti e serie appena iniziata.
//
// COSA COPRE DAVVERO (funzioni REALI estratte dall'html attivo ed eseguite in vm,
// nessun browser e nessuna rete):
//   1. `stationsDailyLoad` accetta lo schema `stations-daily-series/1` E `/2`.
//      Nessun test copriva il gate dello schema: e' il motivo per cui il difetto
//      della 1.3.0.60 (blob /2 rifiutato -> serie e finestre lunghe assenti) e'
//      passato inosservato.
//   2. L'ordine delle sorgenti e' RAW PRIMA (raw.githubusercontent, branch
//      stations-daily) e Pages solo come fallback: Pages risponde 404 quando il
//      restore in _site fallisce e il file vive sul branch.
//   3. Con `source === 'server'` e 1 punto la nota "serie iniziata da N
//      osservazioni (dalle HH:MM)" compare: il grafico con un solo punto non ha
//      una linea e senza nota restava muto.
//
// LIMITI DICHIARATI: emulatore Node. Non verifica che raw.githubusercontent
// risponda davvero, non misura il tempo del primo paint ne' il comportamento
// del Service Worker. Il gate dello schema /2 era GIA' corretto nella 1.3.0.60:
// quell'assert non e' un difetto nuovo, e' la copertura che mancava.

'use strict';
import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { appPath, appHtmlName } from './_app_path.mjs';

// Il percorso puo' essere passato come primo argomento (per misurare una copia
// pristina, es. `node test_stations_chart.mjs /tmp/1.3.0.60.html`).
const HTML = process.argv[2] ? path.resolve(process.argv[2]) : appPath();
if (!fs.existsSync(HTML)) {
  console.error('FATAL: html non trovato: ' + JSON.stringify(HTML));
  process.exit(2);
}
const src = fs.readFileSync(HTML, 'utf8');

let checks = 0, failures = 0;
function ok(label, cond, detail = '') {
  checks++;
  console.log(`  [${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? ' · ' + detail : ''}`);
  if (!cond) failures++;
}

// ---- estrattori (bilanciati: le tabelle nuove contengono `;` interni) --------
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

console.log('\nA. stationsDailyLoad: schema /1 e /2, ordine RAW-PRIMA');
// --- A1: schema 1 accettato ---
{
  const ctx = {
    stationsDaily: null, stationsDailyLoadedAt: 0, stationsDailyLoading: null,
    calls: [],
    fetchWithTimeout(url) {
      ctx.calls.push(String(url));
      return Promise.resolve({
        ok: true,
        json: async () => ({ schema: 'stations-daily-series/1', day: '2026-11-23', stations: { A: { pts: [] } } })
      });
    },
    STATIONS_DAILY_URL: 'https://pages.example/data/stations/daily_series.json',
    STATIONS_DAILY_RAW_URL: 'https://raw.example/stations-daily/data/stations/daily_series.json'
  };
  vm.createContext(ctx);
  vm.runInContext(extractVar('STATIONS_DAILY_URL') + '\n' + extractVar('STATIONS_DAILY_RAW_URL') + '\n' +
    extractFn('stationsDailyLoad'), ctx);
  const got = await ctx.stationsDailyLoad();
  ok('schema stations-daily-series/1 accettato', got && got.schema === 'stations-daily-series/1',
    got ? got.schema : String(got));
  // Le URL VERE sono quelle estratte dall'html: l'assert controlla l'ordine reale,
  // non una copia scritta nel test.
  ok('prima sorgente interrogata = RAW del branch stations-daily (non Pages)',
    /raw\.githubusercontent\.com/.test(ctx.calls[0]) && /stations-daily/.test(ctx.calls[0]) &&
    ctx.calls[0] !== ctx.STATIONS_DAILY_URL, ctx.calls[0]);
  ok('Pages resta come fallback dichiarato e non viene chiamato al primo colpo',
    ctx.calls.length === 1, 'chiamate=' + ctx.calls.length);
}
// --- A2: schema 2 accettato (il caso che nella 1.3.0.60 non era coperto) ---
{
  const ctx = {
    stationsDaily: null, stationsDailyLoadedAt: 0, stationsDailyLoading: null,
    calls: [],
    fetchWithTimeout(url) {
      ctx.calls.push(String(url));
      return Promise.resolve({
        ok: true,
        json: async () => ({ schema: 'stations-daily-series/2', day: '2026-11-23', stations: { A: { pts: [], rain_6h_mm: 4 } } })
      });
    },
    STATIONS_DAILY_URL: 'https://pages.example/data/stations/daily_series.json',
    STATIONS_DAILY_RAW_URL: 'https://raw.example/stations-daily/data/stations/daily_series.json'
  };
  vm.createContext(ctx);
  vm.runInContext(extractVar('STATIONS_DAILY_URL') + '\n' + extractVar('STATIONS_DAILY_RAW_URL') + '\n' +
    extractFn('stationsDailyLoad'), ctx);
  const got = await ctx.stationsDailyLoad();
  ok('schema stations-daily-series/2 accettato', got && got.schema === 'stations-daily-series/2',
    got ? got.schema : String(got));
  ok('con lo schema /2 il blob resta in memoria (stationsDaily)', ctx.stationsDaily !== null);
}
// --- A3: schema ignoto rifiutato e fallback alla sorgente successiva ---
{
  const ctx = {
    stationsDaily: null, stationsDailyLoadedAt: 0, stationsDailyLoading: null,
    calls: [],
    fetchWithTimeout(url) {
      ctx.calls.push(String(url));
      const bad = String(url) === ctx.STATIONS_DAILY_RAW_URL;
      return Promise.resolve({
        ok: true,
        json: async () => (bad
          ? { schema: 'stations-daily-series/0', day: '2026-11-23', stations: {} }
          : { schema: 'stations-daily-series/2', day: '2026-11-23', stations: {} })
      });
    },
    STATIONS_DAILY_URL: 'https://pages.example/data/stations/daily_series.json',
    STATIONS_DAILY_RAW_URL: 'https://raw.example/stations-daily/data/stations/daily_series.json'
  };
  vm.createContext(ctx);
  vm.runInContext(extractVar('STATIONS_DAILY_URL') + '\n' + extractVar('STATIONS_DAILY_RAW_URL') + '\n' +
    extractFn('stationsDailyLoad'), ctx);
  const got = await ctx.stationsDailyLoad();
  ok('schema ignoto su raw -> si passa a Pages (fallback reale)', got && got.schema === 'stations-daily-series/2' &&
    ctx.calls.length === 2, 'chiamate=' + ctx.calls.length + ' schema=' + (got ? got.schema : 'null'));
}

console.log('\nB. Serie appena iniziata: nota con N osservazioni e ora del primo punto');
{
  const FNS = ['stationsChartHtml', 'stationsSeriesSource', 'stationsSeriesPoints', 'stationsLocalDayKey',
    'stationsRomeParts', 'stationsSeriesValue', 'stationsSeriesRead', 'stationsClock'];
  const VARS = ['STATIONS_PARAMS', 'stationsParam', 'STATIONS_SERIES_MAX_GAP_MIN', 'STATIONS_WIND_QUADRANTS'];
  const store = {};
  const ctx = {
    console,
    window: { localStorage: { getItem: (k) => store[k] || null, setItem: (k, v) => { store[k] = String(v); } } },
    document: { getElementById: () => null },
    escapeHtml: (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'),
    fmtNum: (v, d) => Number(v).toFixed(d),
    datasetState: { generatedAt: '' },
    stationsNwpSeries: () => null,          // stub: la curva NWP non c'entra con questa nota
    stationsDaily: null,
    STATIONS_SERIES_KEY: 'k'
  };
  let code;
  try {
    code = VARS.map(extractVar).concat(FNS.map(extractFn)).join('\n');
  } catch (e) {
    console.error('FATAL: grafico centraline non estraibile da ' + appHtmlName() + ' — ' + e.message);
    process.exit(1);
  }
  vm.createContext(ctx);
  vm.runInContext(code, ctx);
  // Un solo punto del giorno corrente, blob del SERVER: e' il caso della nota.
  const t = Date.parse('2026-11-23T08:10:00Z');
  const day = ctx.stationsLocalDayKey(t);
  ctx.stationsDaily = {
    schema: 'stations-daily-series/2', day: day,
    stations: { A: { pts: [[t, 9.4, 70, 2, 315, null, null, 6.1, 1013, 7.6, 0]] } }
  };
  const st = { id: 'A', lat: 45, lon: 9, time: t, temp: 9.4, name: 'A' };
  const html = ctx.stationsChartHtml(st);
  ok('source server con 1 punto -> nota "serie iniziata da 1 osservazione"',
    /serie iniziata da 1 osservazione/.test(html), (html.match(/serie iniziata[^<]*/) || [''])[0]);
  ok('la nota dichiara che la giornata riparte da mezzanotte locale',
    /la giornata riparte da mezzanotte locale/.test(html));
  ok('la nota riporta l ora del primo punto (dalle HH:MM)',
    /dalle \d{2}:\d{2}/.test(html), (html.match(/dalle \d{2}:\d{2}/) || [''])[0]);
  ok('la nota non compare con 3 punti (caso normale)',
    (() => {
      ctx.stationsDaily.stations.A.pts.push(
        [t + 600000, 9.6, 68, 2, 315, null, null, 6.0, 1013, 7.6, 0],
        [t + 1200000, 9.9, 66, 2, 315, null, null, 5.9, 1013, 7.6, 0]);
      const h3 = ctx.stationsChartHtml(st);
      return !/serie iniziata da/.test(h3);
    })());
}

console.log('\nC. Blocco del codice: nessun nome di release congelato');
ok('il caricamento del blob non contiene un nome di release', !/mri-1\.\d/.test(extractFn('stationsDailyLoad')));
ok('URL raw del branch stations-daily dichiarato', /refs\/heads\/stations-daily/.test(src));

console.log(`\nRISULTATO: ${checks} controlli, ${failures} falliti`);
process.exit(failures ? 1 : 0);
