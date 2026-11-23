#!/usr/bin/env node
// TEST 1.3.0.57 - OVERRIDE MANUALE DEL LAYER PROVINCE (#switch-provinces).
//
// COSA COPRE DAVVERO: estrae dall'html attivo (APP_FILE di VERSION) le funzioni
// riskLayerAllowedFor / isRiskProductActive / riskLayerPaintAllowed /
// updateMapColors / updateRiskBadges / renderLegend / syncContextualToggles /
// toggleRiskLayerOverride / hydroPaintSignature e le esegue in un contesto vm
// con DOM, Leaflet e layer provinciali finti che APPLICANO lo stile come farebbe
// Leaflet. Verifica i 12 punti O1-O12 richiesti dalla release.
//
// LIMITI DICHIARATI: emulatore Node, non un browser. Non misura il rendering
// reale, il CSS, l'ordine di disegno dei pane Leaflet ne' il comportamento
// dell'utente. Il "nessun removeLayer" e' una verifica STATICA del sorgente
// delle funzioni di pittura, non una prova a runtime che nessun altro codice
// rimuova il layer.
'use strict';

import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const VERSION_SRC = fs.readFileSync(path.join(ROOT, 'VERSION'), 'utf8');
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
    extractFn(src, 'updateMapColors'),
    extractFn(src, 'updateRiskBadges'),
    extractFn(src, 'renderLegend'),
    extractFn(src, 'syncContextualToggles'),
    extractFn(src, 'toggleRiskLayerOverride')
  ].join('\n\n');
} catch (e) {
  console.error('FATAL: ' + e.message);
  process.exit(1);
}

// ---------- DOM / LEAFLET FINTI ----------
const legendEl = { style: {}, innerHTML: '', insertAdjacentHTML() { this.innerHTML += '<extra>'; } };
const pvInput = { checked: false, parentNode: { style: {} } };
const riskInput = { checked: true, parentNode: { style: {} } };
const styledEls = {};
['btn-radar', 'btn-satellite', 'btn-high-risk', 'high-risk-wrap', 'standalone-toggles-row'].forEach((id) => {
  styledEls[id] = { style: {} };
});
const styleLog = [];
const provinceLayer = {
  last: null,
  setStyle(s) {
    const st = (typeof s === 'function') ? s({ properties: { SIGLA: 'TO' } }) : s;
    this.last = st;
    styleLog.push(st);
  }
};
const calls = { hidden: 0, borders: 0, badgesDrawn: 0, cleared: 0, dpc: 0, metricSync: 0 };
const sandbox = {
  console,
  document: {
    getElementById(id) {
      if (id === 'legend-container') return legendEl;
      if (id === 'switch-provinces') return pvInput;
      if (id === 'switch-riskbadges') return riskInput;
      return styledEls[id] || null;
    },
    querySelectorAll() { return []; }
  },
  window: {},
  L: {
    layerGroup() { calls.badgesDrawn++; return { addTo() { return this; } }; }
  },
  map: {
    removeLayer() {},
    addLayer() {},
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
  riskBadgesActive: true,
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
  clearRiskBadges() { calls.cleared++; },
  syncMetricSelectEnabled() { calls.metricSync++; },
  syncDevRiskControls() {},
  isRadarToolsProduct() { return false; },
  regionsData: [{ lat: 45, lon: 9, sigla: 'TO' }],
  getAggregateForZone() { return { value: 1 }; },
  computeActiveRisks() { return []; },
  buildRiskTooltip() { return ''; },
  riskBadgeLayer: null
};
vm.createContext(sandbox);
try {
  vm.runInContext(code + '\n', sandbox);
} catch (e) {
  console.error('FATAL: esecuzione delle funzioni estratte fallita: ' + e.message);
  process.exit(1);
}
const setProduct = (id) => { sandbox.activeProductId = id; };
const setOverride = (v) => { sandbox.riskLayerOverride = !!v; pvInput.checked = !!v; };
const paint = () => { styleLog.length = 0; sandbox.updateMapColors(); return provinceLayer.last; };

// ---------- O1: PUREZZA ----------
const matrix = [];
let pureOk = true;
['', 'rischi', 'live', 'radar-player', 'sources'].forEach((id) => {
  [false, true].forEach((ov) => {
    setProduct(id); setOverride(ov);
    const got = sandbox.riskLayerPaintAllowed();
    const want = (id === 'rischi') || ov;
    matrix.push(id + '/' + (ov ? 'ON' : 'OFF') + '=' + got);
    if (got !== want) pureOk = false;
  });
});
const allowedUnaffected = (setOverride(true), sandbox.riskLayerAllowedFor('live') === false &&
  sandbox.riskLayerAllowedFor('rischi') === true);
ok('O1: riskLayerPaintAllowed e\' prodotto «rischi» O override (funzione pura); riskLayerAllowedFor non cambia con l\'override',
  pureOk && allowedUnaffected, matrix.join(' '));
setOverride(false);

// ---------- O2: live + override OFF -> fill 0 ----------
setProduct('live'); setOverride(false);
let st = paint();
ok('O2: live + override OFF -> fillOpacity 0 (layer province neutro)', st && st.fillOpacity === 0, JSON.stringify(st));

// ---------- O3: live + override ON -> fill 0.55 ----------
setOverride(true);
st = paint();
ok('O3: live + override ON -> fillOpacity 0.55 (layer province dipinto)', st && st.fillOpacity === 0.55, JSON.stringify(st));

// ---------- O4: rischi sempre 0.55 ----------
setProduct('rischi'); setOverride(false);
const r1 = paint();
setOverride(true);
const r2 = paint();
ok('O4: prodotto «rischi» -> fillOpacity 0.55 sia con override OFF sia ON',
  r1 && r2 && r1.fillOpacity === 0.55 && r2.fillOpacity === 0.55,
  'OFF=' + r1.fillOpacity + ' ON=' + r2.fillOpacity);

// ---------- O5: override ON + Colori OFF -> fill 0 ----------
setProduct('live'); setOverride(true); sandbox.mapColorsVisible = false;
st = paint();
ok('O5: override ON + «Colori» OFF -> fillOpacity 0 (il neutro vince sull\'override)',
  st && st.fillOpacity === 0, JSON.stringify(st));
sandbox.mapColorsVisible = true;

// ---------- O6: override ON + forecastActive -> fill 0 ----------
sandbox.forecastActive = true;
st = paint();
ok('O6: override ON + previsioni attive -> fillOpacity 0 (le mappe previsionali hanno la precedenza)',
  st && st.fillOpacity === 0, JSON.stringify(st));
sandbox.forecastActive = false;

// ---------- O7: nessun badge con l'override ----------
setProduct('live'); setOverride(true);
const before = { drawn: calls.badgesDrawn, cleared: calls.cleared };
sandbox.updateRiskBadges();
ok('O7: override ON fuori da «rischi» -> nessun badge disegnato (i badge restano gated al prodotto)',
  calls.badgesDrawn === before.drawn && calls.cleared > before.cleared,
  'layerGroup=' + calls.badgesDrawn + ' clear=' + calls.cleared);

// ---------- O8: legenda nascosta in live anche con override ----------
legendEl.style.display = '';
sandbox.renderLegend();
ok('O8: override ON in live -> #legend-container nascosto (la legenda resta gated al prodotto)',
  legendEl.style.display === 'none', 'display=' + JSON.stringify(legendEl.style.display));

// ---------- O9: switch nascosto nei rischi, visibile in live + handler ----------
setProduct('rischi');
sandbox.syncContextualToggles();
const inRisks = pvInput.parentNode.style.display;
setProduct('live');
sandbox.syncContextualToggles();
const inLive = pvInput.parentNode.style.display;
setOverride(false);
pvInput.checked = true;
const metricBefore = calls.metricSync;
const bordersBefore = calls.borders;
sandbox.toggleRiskLayerOverride();
ok('O9: switch «Province» nascosto nel prodotto rischi, visibile in live; il toggle legge la checkbox, ripittura, ridisegna i bordi e riallinea la metrica',
  inRisks === 'none' && inLive === '' && sandbox.riskLayerOverride === true &&
  calls.metricSync > metricBefore && calls.borders > bordersBefore,
  'risks=' + JSON.stringify(inRisks) + ' live=' + JSON.stringify(inLive) +
  ' override=' + sandbox.riskLayerOverride);

// ---------- O10: ordine dei gate in updateMapColors ----------
const body = extractFn(src, 'updateMapColors').replace(/\/\/[^\r\n]*/g, '');
const iPaint = body.indexOf('riskLayerPaintAllowed');
const iColors = body.indexOf('!mapColorsVisible');
const iCont = body.indexOf('continuousMode');
ok('O10: ordine dei gate in updateMapColors: riskLayerPaintAllowed < Colori OFF < vista continua',
  iPaint >= 0 && iColors > iPaint && iCont > iColors,
  'paint=' + iPaint + ' colors=' + iColors + ' continuous=' + iCont);

// ---------- O11: firma idro ----------
const sig = extractFn(src, 'hydroPaintSignature').replace(/\/\/[^\r\n]*/g, '');
ok('O11: hydroPaintSignature include riskLayerOverride (niente ripittura di uno stato vecchio)',
  /riskLayerOverride/.test(sig));

// ---------- O12: nessun removeLayer nelle funzioni di pittura ----------
const painters = ['updateMapColors', 'updateMarkerSelection', 'restyleBorderOverlay', 'riskLayerPaintAllowed']
  .map((n) => extractFn(src, n)).join('\n');
ok('O12: nessun map.removeLayer nei gate/pittori delle province (hover e clic conservati)',
  !/map\.removeLayer/.test(painters.replace(/\/\/[^\r\n]*/g, '')));

console.log(failures === 0 ? '\nRESULT: PASS' : `\nRESULT: FAIL (${failures})`);
process.exit(failures === 0 ? 0 : 1);
