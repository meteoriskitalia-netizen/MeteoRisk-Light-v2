#!/usr/bin/env node
// TEST 1.2.4.9 — FAST-PATH WebP + POOL PRELOAD + REGIONE 'domain'.
// LIMITI: statico sul sorgente + behavior su funzioni pure (fmWebpFrameUrl) e su un pool
// SIMULATO (Image/layer mock). NON esegue un browser, NON valida L.imageOverlay reale,
// NON valida il rendering Leaflet, NON tocca il server (render_fm_webp.py).
'use strict';

import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const HTML = path.join(ROOT, 'mri-light-1.2.4.13.html');
const src = fs.readFileSync(HTML, 'utf8');
const INDEX_WORKFLOW = fs.readFileSync(path.join(ROOT, '.github', 'workflows', 'forecast-maps-index.yml'), 'utf8');

let failures = 0;
function ok(label, cond, detail = '') {
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
failFast('marcatore iniziale non trovato', bStart >= 0);
failFast('marcatore finale non trovato', bEnd > bStart);
const lineStart = src.lastIndexOf('\n', bStart) + 1;
const endLineStart = src.lastIndexOf('\n', bEnd) + 1;
const moduleSrc = src.slice(lineStart, endLineStart);
failFast('blocco FM vuoto', moduleSrc.length > 0);

// ---------- A. STATICHE (sul blocco FM; A16/A17 su tutto il file) ----------
ok('A1: var FM_PRELOAD_CONCURRENCY definita (4-6)',
  has(/var FM_PRELOAD_CONCURRENCY\s*=\s*[456]/, moduleSrc));
ok('A2: var fmPendingPreloadLayers definita',
  has(/var fmPendingPreloadLayers\s*=\s*\[/, moduleSrc));
ok('A3: preload usa new Image() (fast-path WebP)',
  has(/function fmPreloadWebpFrame\s*\(/, moduleSrc) && has(/new Image\(\)/, moduleSrc));
ok('A4: preload ha fallback .om (fmPreloadOmFrame)',
  has(/function fmPreloadOmFrame\s*\(/, moduleSrc));
ok('A5: dispatcher fmPreloadFrame presente',
  has(/function fmPreloadFrame\s*\(/, moduleSrc));
ok('A6: pool (numero concorrenza) usato in fmStartPreload',
  has(/state\.running\s*<\s*FM_PRELOAD_CONCURRENCY/, moduleSrc));
ok('A7: NESSUN gate tema singolo su fmWebpIndex.theme',
  !/fmWebpIndex\.theme\s*&&\s*fmWebpIndex\.theme\s*!==\s*fmWebpTheme\(\)/.test(moduleSrc));
ok('A8: fmWebpFrameUrl usa fmWebpIndex.themes (array)',
  /fmWebpIndex\.themes[\s\S]{0,120}indexOf\(theme\)/.test(moduleSrc));
ok('A9: fmWebpFrameUrl usa varEntry.themes[theme] (path per-tema)',
  /varEntry\.themes\s*&&\s*varEntry\.themes\[theme\]/.test(moduleSrc));
// A10 (adeguato): il progetto indicava "nessun varEntry.dir"; il NEW del progetto stesso
// lo usa come FALLBACK di compatibilita' per il tema light. L'intento reale e' che il gate
// legacy top-level sia rimosso: si verifica l'assenza di varEntry.valid_times.
ok('A10: gate legacy varEntry.valid_times rimosso (valid_times letto dal tema)',
  !/varEntry\.valid_times\b/.test(moduleSrc));
ok('A11: error handler overlay WebP presente (once/on error)',
  /fmWebpLayer\.once\('error'/.test(moduleSrc) || /fmWebpLayer\.on\('error'/.test(moduleSrc));
ok('A12: var fmWebpFailedUrl (anti-loop) presente',
  has(/var fmWebpFailedUrl\s*=/, moduleSrc));
ok('A13: token anti-race fmMetaReqGen',
  has(/var fmMetaReqGen\s*=/, moduleSrc) && has(/myGen\s*!==\s*fmMetaReqGen/, moduleSrc));
ok('A14: token anti-race fmWebpIndexGen',
  has(/var fmWebpIndexGen\s*=/, moduleSrc) && has(/gen\s*!==\s*fmWebpIndexGen/, moduleSrc));
ok('A15: FM_RENDER_REGION_MAP mappa domain->domain',
  /FM_RENDER_REGION_MAP\s*=\s*\{[^}]*'domain'\s*:\s*'domain'/.test(moduleSrc));
ok('A15b: Emisfero nord disponibile ai modelli globali e collegato al renderer',
  /'north'\s*:\s*'north'/.test(moduleSrc) &&
  /id:\s*'north',\s*label:\s*'Emisfero nord'/.test(moduleSrc) &&
  /return\s*\['north',\s*'domain',\s*'europe',\s*'italy',\s*'natl'\]/.test(moduleSrc));
ok('A15c: index-builder autorizza e pubblica north/index.json',
  /\(italia\|europa\|north\|domain\)\/index\\\.json/.test(INDEX_WORKFLOW) &&
  (INDEX_WORKFLOW.match(/data\/fm2-rendered\/north\/index\.json/g) || []).length >= 2);
ok('A15d: asse est/ovest corretto (Greenwich in basso)',
  /function fmPolarLongitude\(dx,\s*dy\)\s*\{\s*return Math\.atan2\(-dx,\s*dy\)/.test(moduleSrc));
ok('A15e: playback attende il WebP e Leaflet riceve sempre un URL',
  /if\s*\(!fmPlaybackFramePending\)\s*fmNextFrame\(\)/.test(moduleSrc) &&
  /L\.imageOverlay\(webp\.url,\s*wb/.test(moduleSrc) &&
  !/L\.imageOverlay\(readyImage\s*\|\|\s*webp\.url/.test(moduleSrc));
// ensureChartJs vive FUORI dal blocco FM -> asserzioni sull'intero file.
ok("A16: ensureChartJs ha handle singolo _chartJsWaitTimer",
  has(/var _chartJsWaitTimer/));
ok("A17: ensureChartJs non crea piu' var _t = setInterval per-chiamata",
  !/var _t = setInterval/.test(src));

// ---------- B. VM (esecuzione del blocco reale) ----------
const ctx = {
  document: { getElementById() { return null; } },
  window: { addEventListener() {} },
  L: undefined,
  isFeatureEnabled() { return true; },
  fetch() { throw new Error('fetch non consentito'); },
  setInterval() { return 0; },
  clearInterval() {},
  AbortController: undefined,
  console,
};
try {
  vm.runInNewContext(moduleSrc, ctx, { filename: 'forecastMaps.js' });
} catch (e) {
  failFast('eval blocco FM fallito: ' + e.message + '\n' + (e.stack || ''), false);
}
failFast('fmWebpFrameUrl non estratta', typeof ctx.fmWebpFrameUrl === 'function');
failFast('FM_PRELOAD_CONCURRENCY non esposta', typeof ctx.FM_PRELOAD_CONCURRENCY === 'number');
ok('C6: orientamento polare Greenwich in basso',
  Math.abs(ctx.fmPolarLongitude(0, 1)) < 1e-9 && Math.abs(Math.abs(ctx.fmPolarLongitude(0, -1)) - 180) < 1e-9);
ok("C7: asse est/ovest corretto dopo l'inversione del lookup",
  Math.abs(ctx.fmPolarLongitude(1, 0) + 90) < 1e-9 && Math.abs(ctx.fmPolarLongitude(-1, 0) - 90) < 1e-9);

// Il timer del player non deve invalidare il frame WebP mentre sta ancora caricando.
let playTick = null, playAdvanceCount = 0;
const originalNextFrame = ctx.fmNextFrame;
ctx.setTimeout = setTimeout; ctx.clearTimeout = clearTimeout;
ctx.setInterval = function (fn) { playTick = fn; return 7; };
ctx.clearInterval = function () {};
ctx.fmNextFrame = function () { playAdvanceCount++; };
ctx.fmMeta = { valid_times: ['2026-10-14T16:00Z', '2026-10-14T17:00Z'] };
ctx.fmStartPlay();
ctx.fmPlaybackSetFramePending(true);
playTick();
ok('C8: il player aspetta il WebP pendente', playAdvanceCount === 0);
ctx.fmPlaybackSetFramePending(false);
playTick();
ok('C9: il player riparte quando il WebP e pronto', playAdvanceCount === 1);
ctx.fmStopPlay();
ctx.fmNextFrame = originalNextFrame;

// ---------- C. BEHAVIOR: fmWebpFrameUrl (light/dark/valid_times/regione) ----------
const VALID = '2026-10-14T16:00Z';
function setIndex(themes) {
  ctx.fmDomain = 'dwd_icon_eu';
  ctx.fmVariable = 'temperature_2m';
  ctx.fmRegion = 'europe';            // -> fmRenderRegionId() === 'europa'
  ctx.fmIdx = 0;
  ctx.fmMeta = { valid_times: [VALID] };
  ctx.fmWebpIndexRegion = 'europa';
  ctx.fmWebpIndex = {
    themes: themes,
    theme: themes[0],
    domains: {
      dwd_icon_eu: {
        bounds: [[36.5, 6.5], [47.2, 18.6]],
        variables: {
          temperature_2m: {
            unit: '\u00b0C',
            dir: 'dwd_icon_eu/temperature_2m',
            themes: {
              light: { dir: 'dwd_icon_eu/temperature_2m', valid_times: [VALID] },
              dark: { dir: 'dwd_icon_eu/temperature_2m/dark', valid_times: [VALID] },
            },
          },
        },
      },
    },
  };
}
const BASE = ctx.FM_RENDERED_BASE;
const TOKEN = VALID.replace(/:/g, '').replace('Z', '');

setIndex(['light', 'dark']);
const lf = ctx.fmWebpFrameUrl(0);
ok('C1: light -> url da themes.light.dir',
  !!lf && lf.url === BASE + 'europa/dwd_icon_eu/temperature_2m/' + TOKEN + '.webp',
  lf && lf.url);

// 1.2.4.13: il renderer produce SOLO il tema `light` (niente piu' `dark/`), quindi il
// client non deve piu' chiedere la variante scura. `ctx.fmOpts` e' un RESIDUO legacy:
// il modulo non lo legge piu' e questo test dimostra che il tema resta "light" anche se
// qualcosa provasse a impostare il flag scuro.
ctx.fmOpts = { dark: true };
const dk = ctx.fmWebpFrameUrl(0);
ok('C2: il tema scuro non esiste piu\' -> il frame usa SEMPRE themes.light.dir',
  !!dk && dk.url === BASE + 'europa/dwd_icon_eu/temperature_2m/' + TOKEN + '.webp',
  dk && dk.url);
ok('C2b: il percorso "dark" NON viene mai richiesto',
  !!dk && dk.url.indexOf('/dark/') < 0, dk && dk.url);

ctx.fmDomain = 'ecmwf_ifs025';
ctx.fmRegion = 'north';
ctx.fmIdx = 0;
ctx.fmMeta = { valid_times: [VALID] };
ctx.fmWebpIndexRegion = 'north';
ctx.fmWebpIndex = {
  themes: ['light'],
  domains: { ecmwf_ifs025: { bounds: [20, -180, 85.05112878, 180], variables: { temperature_850hPa: {
    dir: 'ecmwf_ifs025/temperature_850hPa',
    themes: { light: { dir: 'ecmwf_ifs025/temperature_850hPa', valid_times: [VALID] } },
  } } } },
};
ctx.fmVariable = 'temperature_850hPa';
const nh = ctx.fmWebpFrameUrl(0);
ok('C3: URL frame T850 della regione Emisfero nord',
  !!nh && nh.url === BASE + 'north/ecmwf_ifs025/temperature_850hPa/' + TOKEN + '.webp',
  nh && nh.url);

// valid_times letto da themes[theme]: se il tema non elenca il frame -> null
setIndex(['light']);
ctx.fmWebpIndex.domains.dwd_icon_eu.variables.temperature_2m.themes.light.valid_times = ['2099-01-01T00:00Z'];
ctx.fmIdx = 0;
ok('C3: valid_times preso da themes[theme] (frame assente -> null)',
  ctx.fmWebpFrameUrl(0) === null, String(ctx.fmWebpFrameUrl(0)));

// 1.2.4.13: l'indice NON pubblica il tema `light` (es. indice legacy con il solo `dark`):
// il client non puo' usare il frame -> null (stessa guardia, tema light).
setIndex(['dark']);
ok('C4: indice senza il tema light -> null', ctx.fmWebpFrameUrl(0) === null, String(ctx.fmWebpFrameUrl(0)));

// regione 'domain' -> fmRenderRegionId() === 'domain' (nuova mappa D)
ctx.fmDomain = 'dwd_icon_eu'; ctx.fmVariable = 'temperature_2m';
ctx.fmRegion = 'domain'; ctx.fmIdx = 0;
ctx.fmMeta = { valid_times: [VALID] };
ctx.fmWebpIndexRegion = 'domain';
ctx.fmWebpIndex = {
  themes: ['light'], theme: 'light',
  domains: { dwd_icon_eu: { bounds: [[36.5, 6.5], [47.2, 18.6]], variables: {
    temperature_2m: { dir: 'dwd_icon_eu/temperature_2m', themes: { light: { dir: 'dwd_icon_eu/temperature_2m', valid_times: [VALID] } } },
  } } },
};
const du = ctx.fmWebpFrameUrl(0);
ok('C5: regione "domain" -> url su <base>/domain/...',
  !!du && du.url === BASE + 'domain/dwd_icon_eu/temperature_2m/' + TOKEN + '.webp',
  du && du.url);
ok('C5b: FM_RENDER_REGION_MAP espone domain->domain',
  ctx.FM_RENDER_REGION_MAP && ctx.FM_RENDER_REGION_MAP['domain'] === 'domain');

// ---------- D. BEHAVIOR: pool preload preferisce il WebP ----------
const tick = (ms) => new Promise((r) => setTimeout(r, ms));
function makeMap() {
  return {
    _layers: new Set(), removed: [],
    removeLayer(l) { this._layers.delete(l); this.removed.push(l); },
    hasLayer(l) { return this._layers.has(l); },
    getPane() { return {}; }, createPane() { return {}; },
  };
}
function makeAdapter(created) {
  return {
    createTileLayer(url, opts) {
      const layer = {
        _url: url, _opts: opts, _handlers: {},
        on(ev, fn) { this._handlers[ev] = fn; return this; },
        addTo(m) {
          created.push(this);
          if (m && m._layers) m._layers.add(this);
          const self = this;
          setTimeout(function () { if (self._handlers && self._handlers.load) self._handlers.load(); }, 0);
          return this;
        },
      };
      return layer;
    },
  };
}

async function run(useWebp, times) {
  const created = [];
  const images = [];
  ctx.setTimeout = setTimeout;
  ctx.Image = function () {
    const s = { src: '' };
    images.push(s);
    setTimeout(function () { if (s.onload) s.onload(); }, 0);
    return s;
  };
  ctx.map = makeMap();
  ctx.fmAdapter = makeAdapter(created);
  ctx.document.getElementById = function () { return null; };
  ctx.fmDomain = 'dwd_icon_eu';
  ctx.fmVariable = 'temperature_2m';
  ctx.fmRegion = 'europe';
  ctx.fmMeta = { valid_times: times.slice() };
  ctx.fmIdx = 0;
  ctx.fmClipBounds = null;
  ctx.fmActive = false; // fmWarmNext no-op: conteggi esatti (nessun layer warm di coda)
  ctx.fmPreloadActive = false;
  ctx.fmPreloadGen = 0;
  ctx.fmPendingPreloadLayers = [];
  ctx.fmPendingPreloadLayer = null;
  ctx.fmWarmLayer = null;
  ctx.fmWarmKey = null;
  if (useWebp) {
    ctx.fmWebpIndexRegion = 'europa';
    ctx.fmWebpIndex = {
      themes: ['light'], theme: 'light',
      domains: { dwd_icon_eu: { bounds: [[0, 0], [1, 1]], variables: {
        temperature_2m: { dir: 'd/d', themes: { light: { dir: 'd/d', valid_times: times.slice() } } },
      } } },
    };
  } else { ctx.fmWebpIndex = null; ctx.fmWebpIndexRegion = null; }
  ctx.fmStartPreload();
  const startedNow = { created: created.length, images: images.length };
  await tick(150);
  return { created, images, startedNow };
}

const N = 10;
const TIMES = Array.from({ length: N }, (_, i) => `2026-10-14T${String(10 + i).padStart(2, '0')}:00Z`);

const webpRun = await run(true, TIMES);
ok('D1: con WebP disponibile il pool NON crea layer .om', webpRun.created.length === 0, `${webpRun.created.length}`);
ok('D2: con WebP il pool avvia esattamente FM_PRELOAD_CONCURRENCY Image',
  webpRun.startedNow.images === ctx.FM_PRELOAD_CONCURRENCY, `${webpRun.startedNow.images} / ${ctx.FM_PRELOAD_CONCURRENCY}`);
ok('D3: a fine corsa tutti i frame WebP precaricati', webpRun.images.length === N, `${webpRun.images.length}`);

const omRun = await run(false, TIMES);
ok('D4: senza WebP il pool crea al massimo FM_PRELOAD_CONCURRENCY layer .om',
  omRun.startedNow.created === ctx.FM_PRELOAD_CONCURRENCY, `${omRun.startedNow.created}`);
ok('D5: a fine corsa tutti i frame .om precaricati', omRun.created.length === N, `${omRun.created.length}`);
ok('D6: nessun layer .om residuo dopo il completamento', ctx.fmPendingPreloadLayers.length === 0,
  `${ctx.fmPendingPreloadLayers.length}`);

console.log(`\nRESULT: ${failures === 0 ? 'PASS' : 'FAIL'} (${failures} errori)`);
process.exit(failures === 0 ? 0 : 1);
