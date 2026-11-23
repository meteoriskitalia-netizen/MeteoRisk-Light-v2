#!/usr/bin/env node
// TEST 1.3.0.53 - PALETTE DPC + LEGENDE RADAR/AFA.
//
// COSA COPRE DAVVERO (non solo presenza di stringhe):
//  1. la ricolorazione DPC usa la tabella RADAR_DBZ_SCALE per la VMI e NON una LUT
//     a 4 bande sul valore R grezzo (1.3.0.45);
//  2. la decodifica del tile DPC e' quella MISURATA: dBZ = R*60/255 (R=0 -> 0,
//     R=127.5 -> 30, R=255 -> 60), mm/h = R*100/255, mm = R*200/255,
//     °C = -70 + R*105/255;
//  3. dpcRecolorPixels, eseguita su pixel veri, produce il colore Universal Blue
//     dello stop giusto e NON applica la rampa dBZ a SRI/IR_108;
//  4. il gate `isXyz` che nascondeva la legenda per DPC non c'e' piu': con
//     radarProvider='dpc' la legenda resta visibile e dichiara asse e unita';
//     per SRI l'asse e' mm/h (misura del prodotto, non conversione);
//  5. la legenda AFA esiste, usa CC BY 4.0 e non usa piu' il lessico «scariche»;
//  6. afaSlotIsoForUi() esiste, e' usata nei DUE onclick e, con scRadarData=null e
//     syncTimeline popolata, restituisce lo slot della timeline (eseguita in vm).
//
// Il test FALLISCE sul client 1.3.0.45 pristino (le funzioni e i campi srcMin/
// srcMax non esistono, il gate isXyz c'e', la legenda AFA non c'e').
// LIMITI DICHIARATI: harness Node/vm, non un browser. Non valida il rendering
// (antialiasing, compositing, TileLayer Leaflet) ne' la resa del tile DPC reale:
// verifica il CONTRATTO del codice e il comportamento delle funzioni pure.
'use strict';

import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
// Percorso esplicito (usato per provare il test sul file pristino) oppure APP_FILE.
function appPath() {
  const arg = process.argv[2];
  if (arg) return path.resolve(arg);
  const versionSrc = fs.readFileSync(path.join(ROOT, 'VERSION'), 'utf8');
  const appFile = ((versionSrc.match(/^APP_FILE=(.+)$/m) || [])[1] || '').trim();
  if (appFile && fs.existsSync(path.join(ROOT, appFile))) return path.join(ROOT, appFile);
  // fallback: html piu' recente per versione numerica
  const files = fs.readdirSync(ROOT).filter((f) => /^mri-light-\d+(\.\d+)+\.html$/.test(f));
  files.sort((a, b) => {
    const va = a.match(/\d+/g).map(Number);
    const vb = b.match(/\d+/g).map(Number);
    for (let i = 0; i < Math.max(va.length, vb.length); i += 1) {
      const d = (va[i] || 0) - (vb[i] || 0);
      if (d !== 0) return d;
    }
    return 0;
  });
  return files.length ? path.join(ROOT, files[files.length - 1]) : null;
}

const HTML = appPath();
if (!HTML || !fs.existsSync(HTML)) {
  console.error('FATAL: html dell app non trovato in ' + ROOT);
  process.exit(1);
}
const src = fs.readFileSync(HTML, 'utf8');
console.log('client: ' + path.basename(HTML) + ' (' + src.length + ' byte)');

let failures = 0;
function ok(label, cond, detail = '') {
  console.log(`  [${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? ' · ' + detail : ''}`);
  if (!cond) failures++;
}
function failFast(label, cond) {
  if (!cond) { console.error('FATAL: ' + label); process.exit(1); }
}

// ---------- estrazione dal sorgente HTML ----------
function extractBlock(re, label) {
  const m = src.match(re);
  failFast(label + ' non trovato nel client', !!m);
  return m[0];
}
function extractFn(name) {
  const start = src.indexOf(name + '(');
  failFast(name + '() assente nel client', start !== -1);
  const brace = src.indexOf('{', start);
  failFast(name + '() senza corpo', brace !== -1);
  let depth = 0;
  for (let i = brace; i < src.length; i += 1) {
    if (src[i] === '{') depth += 1;
    else if (src[i] === '}') {
      depth -= 1;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  failFast(name + '() senza chiusura', false);
  return '';
}

// ---------- A. la decodifica e la rampa DPC ----------
const productsSrc = extractBlock(/var DPC_PRODUCTS = \{[\s\S]*?\n    \};/, 'DPC_PRODUCTS');
const dbzScaleSrc = extractBlock(/var RADAR_DBZ_SCALE = \[[\s\S]*?\n    \];/, 'RADAR_DBZ_SCALE');
const sriStopsSrc = extractBlock(/var DPC_SRI_STOPS = \[[\s\S]*?\n    \];/, 'DPC_SRI_STOPS');
const cumStopsSrc = extractBlock(/var DPC_CUM_STOPS = \[[\s\S]*?\n    \];/, 'DPC_CUM_STOPS');
const tempStopsSrc = extractBlock(/var DPC_TEMP_STOPS = \[[\s\S]*?\n    \];/, 'DPC_TEMP_STOPS');
const recolorSrc = extractFn('function dpcRecolorPixels');

ok('A1: dpcRecolorPixels usa RADAR_DBZ_SCALE per la VMI',
  /RADAR_DBZ_SCALE/.test(recolorSrc) && /scale === 'dbz'/.test(recolorSrc));
ok('A2: la vecchia LUT a 4 bande sul valore R grezzo NON c\u2019e\u2019 piu\u2019',
  !/var BANDS = \[/.test(recolorSrc) && !/intensit\u00e0 dominante/.test(recolorSrc));
ok('A3: DPC_PRODUCTS dichiara srcMin/srcMax/unit/scale per ogni prodotto',
  (productsSrc.match(/srcMin:/g) || []).length === 10 &&
  (productsSrc.match(/srcMax:/g) || []).length === 10 &&
  /'IR_108'[\s\S]*srcMin: -70[\s\S]*srcMax: 35/.test(productsSrc),
  'prodotti=' + (productsSrc.match(/srcMin:/g) || []).length);
ok('A4: HRD non ha ricolorazione (scale none) e la soglia R<25 e\u2019 dichiarata',
  /'HRD'[\s\S]*scale: 'none'/.test(productsSrc) && /DPC_TRANSPARENT_R = 25/.test(src));

// ---------- B. comportamento delle funzioni pure (vm) ----------
const sandbox = {
  DPC_PRODUCTS: null,
  RADAR_DBZ_SCALE: null,
  DPC_SRI_STOPS: null,
  DPC_CUM_STOPS: null,
  DPC_TEMP_STOPS: null,
  DPC_DBZ_STOPS: null,
  DPC_TRANSPARENT_R: 25,
  dpcProductType: 'VMI',
  Math,
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext([
  productsSrc,
  dbzScaleSrc,
  sriStopsSrc,
  cumStopsSrc,
  tempStopsSrc,
  'var DPC_DBZ_STOPS = null;',
  extractFn('function dpcParseRgba'),
  extractFn('function dpcDbzStopsFromScale'),
  extractFn('function dpcValueFromR'),
  extractFn('function dpcLerpStops'),
  recolorSrc,
].join('\n'), sandbox);

const near = (a, b) => Math.abs(a - b) < 1e-9;
ok('B1: dBZ = R*60/255 · R=0 -> 0', near(sandbox.dpcValueFromR(0, 'VMI'), 0));
ok('B2: dBZ = R*60/255 · R=127.5 -> 30', near(sandbox.dpcValueFromR(127.5, 'VMI'), 30));
ok('B3: dBZ = R*60/255 · R=255 -> 60', near(sandbox.dpcValueFromR(255, 'VMI'), 60));
ok('B4: SRI R=255 -> 100 mm/h e cumulata R=255 -> 200 mm',
  near(sandbox.dpcValueFromR(255, 'SRI'), 100) && near(sandbox.dpcValueFromR(255, 'SRT24'), 200));
ok('B5: IR_108 R=0 -> -70 °C e R=255 -> 35 °C',
  near(sandbox.dpcValueFromR(0, 'IR_108'), -70) && near(sandbox.dpcValueFromR(255, 'IR_108'), 35));

// pixel VMI a 30 dBZ esatti (R=127.5 -> 127.5*60/255 = 30): stop Universal Blue
let px = [127.5, 0, 0, 255];
sandbox.dpcProductType = 'VMI';
sandbox.dpcRecolorPixels(px);
ok('B6: VMI a 30 dBZ -> stop Universal Blue 30 dBZ (0,85,136)',
  px[0] === 0 && px[1] === 85 && px[2] === 136 && px[3] === 255, px.join(','));
// pixel SRI a 100 mm/h: rosso scuro, NON il magenta dei 60 dBZ
let pxSri = [255, 0, 0, 255];
sandbox.dpcProductType = 'SRI';
sandbox.dpcRecolorPixels(pxSri);
ok('B7: SRI a R=255 -> rosso (193,0,0), non la rampa dBZ (255,119,255)',
  pxSri[0] === 193 && pxSri[1] === 0 && pxSri[2] === 0 && pxSri[3] === 255, pxSri.join(','));
// pixel "no-data" (R<25) -> trasparente
let pxVoid = [0, 0, 0, 255];
sandbox.dpcRecolorPixels(pxVoid);
ok('B8: pixel senza dato (R<25) -> alpha 0', pxVoid[3] === 0, pxVoid.join(','));
// IR_108 a 0 °C -> ciano (136,221,238)
let pxTemp = [170, 0, 0, 255];
sandbox.dpcProductType = 'IR_108';
sandbox.dpcRecolorPixels(pxTemp);
const tempOk = Math.abs(pxTemp[0] - 136) < 6 && Math.abs(pxTemp[1] - 221) < 6 && Math.abs(pxTemp[2] - 238) < 6;
ok('B9: IR_108 a ~0 °C -> ciano della scala termica', tempOk, pxTemp.join(','));

// ---------- C. legenda precipitazioni ----------
const legendSrc = extractFn('function renderRadarDbzScale');
ok('C1: il gate isXyz che nascondeva la legenda per DPC non esiste piu\u2019',
  !/isXyz/.test(legendSrc) && !/row\.style\.display = 'none'/.test(legendSrc));
const row = { style: {}, innerHTML: '' };
const legendCtx = {
  radarProvider: 'dpc',
  dpcProductType: 'VMI',
  DPC_PRODUCTS: sandbox.DPC_PRODUCTS,
  RADAR_DBZ_SCALE: sandbox.RADAR_DBZ_SCALE,
  DPC_SRI_STOPS: sandbox.DPC_SRI_STOPS,
  DPC_CUM_STOPS: sandbox.DPC_CUM_STOPS,
  DPC_TEMP_STOPS: sandbox.DPC_TEMP_STOPS,
  document: { getElementById: (id) => (id === 'radar-legend-dbz' ? row : null) },
  escapeHtml: (s) => String(s),
  Math,
};
legendCtx.window = legendCtx;
vm.createContext(legendCtx);
vm.runInContext([
  dbzScaleSrc, sriStopsSrc, cumStopsSrc, tempStopsSrc,
  extractFn('function radarDbzToMmh'),
  extractFn('function radarFmtMmh'),
  extractFn('function radarRampHtml'),
  legendSrc,
].join('\n'), legendCtx);
legendCtx.renderRadarDbzScale();
ok('C2: con DPC la legenda e\u2019 visibile (display \'\') e contiene la palette Universal Blue',
  row.style.display === '' && /Universal Blue \(DPC · RainViewer · LibreWXR\)/.test(row.innerHTML));
ok('C3: la legenda dichiara gli mm/h equivalenti Z=200·R^1,6 (0,15 · 0,65 · 2,7)',
  /mm\/h equivalenti \(stima Z=200·R<sup>1,6<\/sup>\)/.test(row.innerHTML) &&
  /0,15/.test(row.innerHTML) && /0,65/.test(row.innerHTML) && /2,7/.test(row.innerHTML));
ok('C4: la legenda dichiara il tile lossy e la VMI come massimo sulla verticale',
  /lossy/.test(row.innerHTML) && /massimo sulla verticale/.test(row.innerHTML));
legendCtx.dpcProductType = 'SRI';
row.innerHTML = '';
legendCtx.renderRadarDbzScale();
ok('C5: con SRI l\u2019asse e\u2019 mm/h (misura del prodotto, non conversione)',
  /mm\/h<\/b> · SRI \(DPC\)/.test(row.innerHTML) && /non<\/b> una conversione/.test(row.innerHTML));
legendCtx.radarProvider = 'rainviewer';
legendCtx.dpcProductType = 'VMI';
row.innerHTML = '';
legendCtx.renderRadarDbzScale();
ok('C6: RainViewer usa la stessa rampa dBZ (nessuna regressione)',
  /Universal Blue \(DPC · RainViewer · LibreWXR\)/.test(row.innerHTML));

// ---------- D. legenda AFA ----------
const afaStart = src.indexOf('id="afa-legend-box"');
failFast('legenda AFA assente', afaStart !== -1);
const afaBox = src.slice(afaStart, src.indexOf('<!-- Selettore ORARIO a slider sotto la mappa', afaStart));
ok('D1: la legenda AFA esiste dentro .map-card con il titolo dichiarativo',
  /AFA · area con emissione ottica \(MTG-I LI\)/.test(afaBox));
ok('D2: la legenda AFA dichiara CC BY 4.0 e il layer mtg_fd:li_afa',
  /CC BY 4.0/.test(afaBox) && /mtg_fd:li_afa/.test(afaBox));
ok('D3: la legenda AFA non usa piu\u2019 la parola «scariche»',
  !/scariche/i.test(afaBox));
ok('D4: la legenda AFA spiega che nessun pixel attivo NON significa zero fulmini',
  /non<\/b> significa «zero fulmini»/.test(afaBox));
ok('D5: i due onclick usano afaSlotIsoForUi()',
  (src.match(/onclick="liveToggleAfaLayer\(!liveAfaEnabled, afaSlotIsoForUi\(\)\)"/g) || []).length === 2);

// ---------- E. afaSlotIsoForUi() in vm ----------
const afaFn = extractFn('function afaSlotIsoForUi');
const slotFn = extractFn('function liveAfaSlotIso');
const SLOT_MS = Date.UTC(2026, 10, 23, 10, 0, 0);
const afaCtx = {
  scRadarData: null,
  syncTimeline: [
    { iso: '2026-11-23T09:55:00.000Z', time: SLOT_MS / 1000 - 300 },
    { iso: '2026-11-23T10:00:00.000Z', time: SLOT_MS / 1000 },
  ],
  syncIndex: 1,
  radarFrames: [],
  Date,
};
afaCtx.window = afaCtx;
vm.createContext(afaCtx);
vm.runInContext([slotFn, afaFn].join('\n'), afaCtx);
ok('E1: con scRadarData=null e syncTimeline popolata -> slot della timeline',
  afaCtx.afaSlotIsoForUi() === '2026-11-23T10:00:00.000Z', String(afaCtx.afaSlotIsoForUi()));
afaCtx.syncTimeline = [];
afaCtx.radarFrames = [{ time: SLOT_MS / 1000 }];
ok('E2: senza timeline -> ISO dell\u2019ultimo frame radar',
  afaCtx.afaSlotIsoForUi() === '2026-11-23T10:00:00.000Z', String(afaCtx.afaSlotIsoForUi()));
afaCtx.radarFrames = [];
afaCtx.scRadarData = { slot_iso: '2026-11-23T09:45:00.000Z' };
ok('E3: senza timeline ne\u2019 frame radar -> slot del pannello SC',
  afaCtx.afaSlotIsoForUi() === '2026-11-23T09:45:00.000Z');
afaCtx.scRadarData = null;
ok('E4: senza nessuna sorgente -> null (il toggle resta spento, non inventa Date.now())',
  afaCtx.afaSlotIsoForUi() === null);

console.log(`\nRESULT: ${failures === 0 ? 'PASS' : 'FAIL'} (${failures} errori)`);
process.exit(failures === 0 ? 0 : 1);
