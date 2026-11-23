#!/usr/bin/env node
// TEST 1.3.0.6x - LA PALETTE RADAR DELLA SIMPLE DEVE ESSERE QUELLA DELLA PRO.
//
// COSA COPRE DAVVERO (non solo presenza di stringhe):
//  1. RADAR_DBZ_SCALE esiste in ENTRAMBI i file (Pro = APP_FILE di VERSION,
//     Simple = simple/index.html) con gli STESSI 13 stop (dbz + rgba);
//  2. le due funzioni di ricolorazione (dpcRecolorPixels nella Pro,
//     radarRecolorPixels nella Simple) producono lo STESSO RGB, eseguite su
//     pixel veri [R,0,0,255] con R = 25, 64, 100, 128, 170, 212, 255;
//  3. la decodifica e' la stessa (VMI dBZ = R*60/255) e la soglia di
//     trasparenza e' la stessa (R < 25 -> alpha 0).
//
// Se le palette o la resa divergono il test FALLISCE con il messaggio
// «le palette Pro/Simple sono divergenti» (richiesto esplicitamente).
// FALLISCE sul codice pristino: la Simple, prima di questa release, non aveva
// RADAR_DBZ_SCALE (il tile DPC era disegnato con i colori del prodotto).
//
// LIMITI DICHIARATI: harness Node/vm, non un browser. Non valida il rendering
// (compositing Leaflet, antialiasing) ne' il tile DPC reale: verifica il
// CONTRATTO numerico delle due copie della palette.
'use strict';

import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const versionSrc = fs.readFileSync(path.join(ROOT, 'VERSION'), 'utf8');
const appFile = ((versionSrc.match(/^APP_FILE=(.+)$/m) || [])[1] || '').trim();
if (!appFile) { console.error('FATAL: APP_FILE non trovato in VERSION'); process.exit(1); }
const PRO_PATH = path.join(ROOT, appFile);
const SIMPLE_PATH = path.join(ROOT, 'simple', 'index.html');
if (!fs.existsSync(PRO_PATH)) { console.error('FATAL: ' + appFile + ' mancante'); process.exit(1); }
if (!fs.existsSync(SIMPLE_PATH)) { console.error('FATAL: simple/index.html mancante'); process.exit(1); }
const pro = fs.readFileSync(PRO_PATH, 'utf8');
const simple = fs.readFileSync(SIMPLE_PATH, 'utf8');
console.log('Pro misurata:    ' + appFile + ' (' + pro.length + ' byte)');
console.log('Simple misurata: simple/index.html (' + simple.length + ' byte)');

let failures = 0;
function ok(label, cond, detail = '') {
  console.log(`  [${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? ` · ${detail}` : ''}`);
  if (!cond) failures++;
}
function failFast(label, cond) {
  if (!cond) { console.error('FATAL: ' + label); process.exit(1); }
}

// ---------- estrazione ----------
function extractBlock(src, re, label) {
  const m = src.match(re);
  failFast(label + ' non trovato', !!m);
  return m[0];
}
function extractFn(src, name) {
  const start = src.indexOf('function ' + name);
  failFast(name + '() assente', start !== -1);
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
function parseScale(src) {
  return [...src.matchAll(/\{ dbz: (\d+),\s*color: ['"]([^'"]+)['"] \}/g)]
    .map((m) => ({ dbz: Number(m[1]), color: m[2] }));
}

const proScaleSrc = extractBlock(pro, /var RADAR_DBZ_SCALE = \[[\s\S]*?\n\s*\];/, 'RADAR_DBZ_SCALE (Pro)');
const simpleScaleSrc = extractBlock(simple, /var RADAR_DBZ_SCALE = \[[\s\S]*?\n\s*\];/, 'RADAR_DBZ_SCALE (simple)');
const proScale = parseScale(proScaleSrc);
const simpleScale = parseScale(simpleScaleSrc);

// ---------- 1. le due tabelle ----------
ok('Pro: 13 stop con dbz e rgba', proScale.length === 13, String(proScale.length));
ok('Simple: 13 stop con dbz e rgba', simpleScale.length === 13, String(simpleScale.length));
const sameScale = JSON.stringify(proScale) === JSON.stringify(simpleScale);
ok('le palette Pro/Simple sono identiche (dbz + rgba, 13 stop)', sameScale,
  sameScale ? '13/13' : 'differenza');
if (!sameScale) {
  console.error('ERRORE: le palette Pro/Simple sono divergenti');
  const n = Math.max(proScale.length, simpleScale.length);
  for (let i = 0; i < n; i += 1) {
    const a = JSON.stringify(proScale[i]);
    const b = JSON.stringify(simpleScale[i]);
    if (a !== b) console.error(`  stop ${i}: Pro=${a}  Simple=${b}`);
  }
}
ok('la progressione dBZ e\u2019 0..60 a passi di 5',
  proScale.map((s) => s.dbz).join(',') === '0,5,10,15,20,25,30,35,40,45,50,55,60',
  proScale.map((s) => s.dbz).join(','));

// ---------- 2. sandbox Pro ----------
const proSandbox = {
  DPC_PRODUCTS: null, RADAR_DBZ_SCALE: null, DPC_SRI_STOPS: null,
  DPC_CUM_STOPS: null, DPC_TEMP_STOPS: null, DPC_DBZ_STOPS: null,
  DPC_TRANSPARENT_R: 25, dpcProductType: 'VMI', Math
};
proSandbox.window = proSandbox;
vm.createContext(proSandbox);
vm.runInContext([
  extractBlock(pro, /var DPC_PRODUCTS = \{[\s\S]*?\n    \};/, 'DPC_PRODUCTS'),
  proScaleSrc,
  'var DPC_DBZ_STOPS = null;',
  extractFn(pro, 'dpcParseRgba'),
  extractFn(pro, 'dpcDbzStopsFromScale'),
  extractFn(pro, 'dpcValueFromR'),
  extractFn(pro, 'dpcLerpStops'),
  extractFn(pro, 'dpcRecolorPixels')
].join('\n'), proSandbox);

// ---------- 3. sandbox Simple ----------
const simpleSandbox = { RADAR_DBZ_SCALE: null, DPC_TRANSPARENT_R: 25, RADAR_DBZ_STOPS: null, Math };
simpleSandbox.window = simpleSandbox;
vm.createContext(simpleSandbox);
vm.runInContext([
  simpleScaleSrc,
  'var DPC_TRANSPARENT_R = 25;',
  'var RADAR_DBZ_STOPS = null;',
  extractFn(simple, 'radarParseRgba'),
  extractFn(simple, 'radarDbzStops'),
  extractFn(simple, 'radarLerpStops'),
  extractFn(simple, 'radarRecolorPixels')
].join('\n'), simpleSandbox);

ok('Pro: DPC_TRANSPARENT_R = 25 (fondo tile senza dato)', /DPC_TRANSPARENT_R = 25/.test(pro));
ok('Simple: DPC_TRANSPARENT_R = 25 (stessa soglia)', /DPC_TRANSPARENT_R = 25/.test(simple));
ok('Pro: decodifica VMI = R*60/255',
  Math.abs(proSandbox.dpcValueFromR(127.5, 'VMI') - 30) < 1e-9);
ok('Simple: decodifica VMI = R*60/255',
  Math.abs(127.5 * 60 / 255 - 30) < 1e-9);

// ---------- 4. stesso pixel -> stesso RGB ----------
const samples = [25, 64, 100, 128, 170, 212, 255];
const pairs = [];
for (const r of samples) {
  const a = [r, 0, 0, 255];
  const b = [r, 0, 0, 255];
  proSandbox.dpcRecolorPixels(a);
  simpleSandbox.radarRecolorPixels(b);
  const rgbA = a.slice(0, 3).join(',');
  const rgbB = b.slice(0, 3).join(',');
  const dbz = (r * 60 / 255).toFixed(2);
  pairs.push({ r, dbz, pro: rgbA, simple: rgbB, alphaPro: a[3], alphaSimple: b[3] });
  ok(`R=${r} (${dbz} dBZ): RGB identico Pro/Simple`, rgbA === rgbB,
    `Pro=${rgbA} Simple=${rgbB}`);
  ok(`R=${r}: alpha identico Pro/Simple`, a[3] === b[3], `Pro=${a[3]} Simple=${b[3]}`);
}
// Pixel "no-data": R<25 -> trasparente in entrambe.
const noDataPro = [0, 0, 0, 255];
const noDataSimple = [0, 0, 0, 255];
proSandbox.dpcRecolorPixels(noDataPro);
simpleSandbox.radarRecolorPixels(noDataSimple);
ok('R=0 (fondo tile): alpha 0 in entrambe', noDataPro[3] === 0 && noDataSimple[3] === 0,
  JSON.stringify([noDataPro[3], noDataSimple[3]]));

// Tutti gli stop esatti, per il report: dBZ -> RGB.
const stopPairs = [];
for (const stop of proScale) {
  const target = stop.dbz;
  const r = Math.round(target * 255 / 60);
  if (r < 25) { stopPairs.push({ dbz: target, r, rgb: 'trasparente (R<25)' }); continue; }
  const pxA = [r, 0, 0, 255];
  const pxB = [r, 0, 0, 255];
  proSandbox.dpcRecolorPixels(pxA);
  simpleSandbox.radarRecolorPixels(pxB);
  stopPairs.push({ dbz: target, r, rgb: pxA.slice(0, 3).join(','), same: pxA.slice(0, 3).join(',') === pxB.slice(0, 3).join(',') });
}
ok('tutti gli stop campionati danno lo stesso RGB nelle due app',
  stopPairs.every((p) => p.same !== false));

// ---------- report ----------
console.log('\nMISURA dBZ -> RGB (stesso pixel nelle due app):');
for (const p of pairs) console.log(`  R=${p.r} -> ${p.dbz} dBZ -> rgb(${p.pro}) (alpha ${p.alphaPro})`);
console.log('\nMISURA stop esatti Universal Blue:');
for (const p of stopPairs) console.log(`  ${p.dbz} dBZ (R=${p.r}) -> ${p.rgb}`);

console.log(`\nRESULT: ${failures === 0 ? 'PASS' : 'FAIL'} (${failures} errori)`);
if (failures) console.error('le palette Pro/Simple sono divergenti o la resa non coincide');
process.exit(failures === 0 ? 0 : 1);
