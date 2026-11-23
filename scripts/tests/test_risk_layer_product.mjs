#!/usr/bin/env node
// TEST 1.3.0.54 - LA MAPPA DEI RISCHI APPARTIENE AL PRODOTTO «Mappa dei rischi».
//
// COSA COPRE DAVVERO: le funzioni riskLayerAllowedFor / isRiskProductActive /
// renderLegend / updateMapColors sono ESTRATTE dall'html attivo (APP_FILE letto da
// VERSION) ed eseguite in un contesto vm con DOM, L, map e layer provinciali finti.
// Verifica che:
//   (1) riskLayerAllowedFor sia PURA e dia true SOLO per 'rischi' (default sicuro
//       per qualunque id futuro: '', 'radar-player', 'live', 'sources', ... -> false);
//   (2) updateMapColors su un altro prodotto porti il riempimento province a
//       fillOpacity 0 (poligoni neutri ma interattivi, opacity 1) e nasconda
//       #legend-container; sul prodotto «rischi» torni 0.55 e la legenda visibile;
//   (3) il gate !isRiskProductActive() sia presente in TUTTI i punti del file
//       (helper + updateMapColors + updateMarkerSelection + updateRiskBadges +
//       renderLegend), e che syncProductUI / toggleForecastMaps / hydroPaintSignature
//       / syncContextualToggles siano cablati.
//
// LIMITI DICHIARATI: e' un emulatore Node, non un browser. Non misura il rendering
// Leaflet reale (hover/clic provincia), non verifica i CSS e non dice nulla sul
// comportamento dell'utente sul sito: il gate dinamico usa uno stub di
// provinceGeoJsonLayer che APPLICA le funzioni di stile come farebbe Leaflet.
// Nel caso «embed radar attivo» il test verifica che renderLegend NON tocchi il
// display di #legend-container (lo gestisce toggleRadarEmbed), non che l'embed
// disegni davvero.
'use strict';

import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const VERSION_SRC = fs.readFileSync(path.join(ROOT, 'VERSION'), 'utf8');
// Il client attivo e' APP_FILE di VERSION, NON il "primo html" che capita:
// un test che misura un altro file non sta misurando il prodotto pubblicato.
const APP_FILE = ((VERSION_SRC.match(/^APP_FILE=(.+)$/m) || [])[1] || '').trim();
if (!APP_FILE || !fs.existsSync(path.join(ROOT, APP_FILE))) {
  console.error('FATAL: APP_FILE di VERSION non trovato: ' + JSON.stringify(APP_FILE));
  process.exit(1);
}
const src = fs.readFileSync(path.join(ROOT, APP_FILE), 'utf8');
console.log('html misurato: ' + APP_FILE);

let failures = 0;
function ok(label, cond, detail = '') {
  console.log(`  [${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? ' -- ' + detail : ''}`);
  if (!cond) failures++;
}

// Estrae una funzione per nome con un contatore di graffe che SALTA stringhe,
// template literal e commenti: serve per riskLayerAllowedFor (una riga) e per
// renderLegend (template literal con graffe dentro i testi).
function extractFn(source, name) {
  const start = source.indexOf('function ' + name + '(');
  if (start < 0) throw new Error('funzione ' + name + ' non trovata');
  let i = source.indexOf('{', start);
  if (i < 0) throw new Error('corpo di ' + name + ' non trovato');
  let depth = 0, mode = null, quote = null;
  for (; i < source.length; i++) {
    const c = source[i], n = source[i + 1];
    if (mode === 'line') { if (c === '\n') mode = null; continue; }
    if (mode === 'block') { if (c === '*' && n === '/') { mode = null; i++; } continue; }
    if (mode === 'str') { if (c === '\\') { i++; continue; } if (c === quote) mode = null; continue; }
    if (mode === 'tpl') { if (c === '\\') { i++; continue; } if (c === '`') mode = null; continue; }
    if (c === '/' && n === '/') { mode = 'line'; i++; continue; }
    if (c === '/' && n === '*') { mode = 'block'; i++; continue; }
    if (c === '"' || c === "'") { mode = 'str'; quote = c; continue; }
    if (c === '`') { mode = 'tpl'; continue; }
    if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) return source.slice(start, i + 1); }
  }
  throw new Error('chiusura di ' + name + ' non trovata');
}

let code;
try {
  code = [
    extractFn(src, 'riskLayerAllowedFor'),
    extractFn(src, 'isRiskProductActive'),
    extractFn(src, 'riskLayerPaintAllowed'),
    extractFn(src, 'renderLegend'),
    extractFn(src, 'updateMapColors')
  ].join('\n\n');
} catch (e) {
  console.error('FATAL: ' + e.message);
  process.exit(1);
}

// ---------- DOM / LEAFLET FINTI ----------
const legendEl = { style: {}, innerHTML: '', insertAdjacentHTML() { this.innerHTML += '<extra>'; } };
const styleLog = [];
const provinceLayer = {
  last: null,
  setStyle(s) {
    const st = (typeof s === 'function') ? s({ properties: { SIGLA: 'TO' } }) : s;
    this.last = st;
    styleLog.push(st);
  }
};
const calls = { hidden: 0, borders: 0, badges: 0, cleared: 0, dpc: 0 };
const sandbox = {
  console,
  document: { getElementById(id) { return id === 'legend-container' ? legendEl : null; } },
  window: {},
  L: {},
  map: {
    removeLayer() {}, addLayer() {},
    getZoom() { return 8; },
    latLngToContainerPoint() { return { x: 0, y: 0 }; }
  },
  activeProductId: '',
  riskLayerOverride: false,
  mapColorsVisible: true,
  continuousMode: false,
  dpcAlertMode: 'off',
  forecastActive: false,
  radarEmbedActive: false,
  isSviluppoActive: false,
  currentMetric: 'risk',
  convCategory: 'thunderstorm',
  hailLevel: 'prob',
  FOG_BAND_COLORS: ['#000000', '#111111', '#222222', '#333333'],
  provinceSiglaMap: { TO: 0 },
  zoneFillColor() { return '#ef4444'; },
  provinceGeoJsonLayer: provinceLayer,
  hideContinuousOverlay() { calls.hidden++; },
  renderContinuousOverlay() {},
  renderDpcLegend() { calls.dpc++; },
  restyleBorderOverlay() { calls.borders++; },
  updateRiskBadges() { calls.badges++; },
  clearRiskBadges() { calls.cleared++; }
};
vm.createContext(sandbox);
try {
  vm.runInContext(code + '\n', sandbox);
} catch (e) {
  console.error('FATAL: esecuzione delle funzioni estratte fallita: ' + e.message);
  process.exit(1);
}
const setProduct = (id) => { sandbox.activeProductId = id; };
ok('A: riskLayerAllowedFor / isRiskProductActive / updateMapColors / renderLegend estratte ed eseguibili',
  typeof sandbox.riskLayerAllowedFor === 'function' && typeof sandbox.isRiskProductActive === 'function' &&
  typeof sandbox.updateMapColors === 'function' && typeof sandbox.renderLegend === 'function');

// ---------- 1) MATRICE PURA ----------
const IDS = ['', 'rischi', 'radar-player', 'live', 'sources', 'forecast', 'forecast-maps', 'teleconn', 'synwebp'];
const matrix = IDS.map((id) => id + '=' + sandbox.riskLayerAllowedFor(id)).join(', ');
ok('M1: riskLayerAllowedFor e\' vero SOLO per «rischi»',
  IDS.every((id) => sandbox.riskLayerAllowedFor(id) === (id === 'rischi')), matrix);
ok('M2: default sicuro per id futuri/sconosciuti (nessun id sconosciuto abilita il layer)',
  sandbox.riskLayerAllowedFor('nuovo-prodotto-futuro') === false &&
  sandbox.riskLayerAllowedFor(undefined) === false &&
  sandbox.riskLayerAllowedFor(null) === false &&
  sandbox.riskLayerAllowedFor('RISCHI') === false);
ok('M3: isRiskProductActive legge activeProductId (prodotto «rischi» -> true)',
  (setProduct('rischi'), sandbox.isRiskProductActive() === true) &&
  (setProduct('live'), sandbox.isRiskProductActive() === false));

// ---------- 2) GATE DINAMICO SU updateMapColors ----------
function run(productId) {
  setProduct(productId);
  styleLog.length = 0;
  sandbox.updateMapColors();
  return provinceLayer.last;
}

// prodotto «rischi»: colori pieni + legenda visibile
legendEl.style.display = 'sentinel';
let st = run('rischi');
ok('D1: prodotto «rischi» -> fillOpacity 0.55 (mappa dei rischi dipinta)',
  st && st.fillOpacity === 0.55, JSON.stringify(st));
ok('D2: prodotto «rischi» -> #legend-container visibile', legendEl.style.display === '',
  'display=' + JSON.stringify(legendEl.style.display));
ok('D3: prodotto «rischi» -> updateRiskBadges eseguito',
  calls.badges > 0, 'badges=' + calls.badges);

// prodotto «radar-player»: neutro + legenda nascosta
const before = { hidden: calls.hidden, cleared: calls.cleared, badges: calls.badges };
st = run('radar-player');
ok('D4: prodotto «radar-player» -> fillOpacity 0',
  st && st.fillOpacity === 0, JSON.stringify(st));
ok('D5: poligoni NEUTRI ma interattivi (opacity 1, fill #334155, weight 0)',
  st && st.opacity === 1 && st.fillColor === '#334155' && st.weight === 0,
  JSON.stringify(st));
ok('D6: prodotto «radar-player» -> #legend-container display none',
  legendEl.style.display === 'none', 'display=' + JSON.stringify(legendEl.style.display));
ok('D7: il gate nasconde la sfumatura continua e cancella i badge',
  calls.hidden > before.hidden && calls.cleared > before.cleared,
  'hidden=' + calls.hidden + ' cleared=' + calls.cleared);
ok('D8: il gate NON esegue updateRiskBadges (i badge sono gia\' stati rimossi)',
  calls.badges === before.badges, 'badges=' + calls.badges);
ok('D9: il gate non rimuove il layer province (setStyle chiamato, nessun removeLayer)',
  styleLog.length >= 1 && provinceLayer.last === st, 'setStyle=' + styleLog.length);

// ritorno a «rischi»: i colori tornano
st = run('rischi');
ok('D10: ritorno a «rischi» -> fillOpacity 0.55 e legenda visibile',
  st && st.fillOpacity === 0.55 && legendEl.style.display === '', JSON.stringify(st));

// ---------- 3) #legend-container CONDIVISO ----------
// Criticita' DPC attiva (hydroRiskSuspended): la legenda NON va nascosta.
sandbox.window.hydroRiskSuspended = true;
legendEl.style.display = '';
st = run('live');
ok('D11: con hydroRiskSuspended la legenda resta visibile anche fuori da «rischi»',
  legendEl.style.display === '' && st && st.fillOpacity === 0, 'display=' + JSON.stringify(legendEl.style.display));
sandbox.window.hydroRiskSuspended = false;

// Embed radar attivo: il display lo gestisce toggleRadarEmbed, non renderLegend.
sandbox.radarEmbedActive = true;
legendEl.style.display = 'sentinel';
run('radar-player');
ok('D12: con embed radar attivo renderLegend NON tocca il display della legenda',
  legendEl.style.display === 'sentinel', 'display=' + JSON.stringify(legendEl.style.display));
sandbox.radarEmbedActive = false;

// Allerte DPC (dpcAlertMode === 'on'): legenda DPC, non nascosta.
sandbox.dpcAlertMode = 'on';
legendEl.style.display = '';
run('forecast');
ok('D13: con dpcAlertMode === on la legenda resta visibile fuori da «rischi»',
  legendEl.style.display === '', 'display=' + JSON.stringify(legendEl.style.display));
sandbox.dpcAlertMode = 'off';

// ---------- 4) STATICHE: IL GATE C\'E\' IN TUTTI I PUNTI ----------
const strip = (s) => s.replace(/\/\/[^\r\n]*/g, '');
const occurrences = (src.match(/isRiskProductActive/g) || []).length;
ok('S1: isRiskProductActive compare almeno 5 volte nel file', occurrences >= 5, 'occorrenze=' + occurrences);

const fnGate = (name, gateFn) => {
  const body = strip(extractFn(src, name));
  return new RegExp('if \\(!' + (gateFn || 'isRiskProductActive') + '\\(\\)\\)').test(body);
};
ok('S2: updateMapColors ha il gate come primo controllo del riempimento (riskLayerPaintAllowed: prodotto O override Province)',
  fnGate('updateMapColors', 'riskLayerPaintAllowed'));
ok('S3: updateMarkerSelection ha il gate (secondo pittore, clic provincia; riskLayerPaintAllowed)',
  fnGate('updateMarkerSelection', 'riskLayerPaintAllowed'));
ok('S4: updateRiskBadges ha il gate (nessun badge fuori dal prodotto)', fnGate('updateRiskBadges'));
ok('S5: renderLegend ha il gate (legenda solo nel prodotto)', fnGate('renderLegend'));

const syncProduct = strip(extractFn(src, 'syncProductUI'));
ok('S6: syncProductUI chiude con updateMapColors() (cambio prodotto ridipinge/neutro)',
  /typeof updateMapColors === 'function'\) updateMapColors\(\)/.test(syncProduct));

const syncCtx = strip(extractFn(src, 'syncContextualToggles'));
ok('S7: syncContextualToggles nasconde il wrapper di #switch-riskbadges',
  /switch-riskbadges/.test(syncCtx) && /parentNode\.style\.display/.test(syncCtx));

const tglFm = strip(extractFn(src, 'toggleForecastMaps'));
const fmRestoreIdx = tglFm.indexOf('fmRestoreRiskOverlays()');
const fmPaintIdx = tglFm.indexOf('updateMapColors()');
ok('S8: toggleForecastMaps chiama updateMapColors() dopo fmRestoreRiskOverlays()',
  fmRestoreIdx >= 0 && fmPaintIdx > fmRestoreIdx,
  'restore=' + fmRestoreIdx + ' paint=' + fmPaintIdx);

const sig = strip(extractFn(src, 'hydroPaintSignature'));
ok('S9: hydroPaintSignature include activeProductId (niente ripittura stale)',
  /activeProductId/.test(sig));

ok('S10: la mappa dei rischi non usa map.removeLayer nel gate (hover/clic conservati)',
  !/map\.removeLayer/.test(strip(extractFn(src, 'updateMapColors'))));

console.log(failures === 0 ? '\nRESULT: PASS' : `\nRESULT: FAIL (${failures})`);
process.exit(failures === 0 ? 0 : 1);
