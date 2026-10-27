#!/usr/bin/env node
// TEST 1.2.4.16 - CAPOLO POLARE: MEDIANA ROBUSTA + DISSOLVENZA CUBICA.
//
// COSA COPRE DAVVERO: le funzioni pure del cap sono ESTRATTE dall'html di release
// (fmPolarMedian, fmPolarCapMean, fmPolarCapColor) e confrontate con la formula della
// 1.2.4.15 (dissolvenza LINEARE, capolo = MEDIA). La differenza viene misurata con gli
// stessi criteri usati sul canvas reale: deviazione standard angolare lungo un anello
// attorno al polo (e colori distinti) sulla proiezione del painter.
//
// LIMITI DICHIARATI: il test non disegna il canvas (niente DOM) e non usa i WebP di
// produzione; la riga 0 e' sintetica. La geometria (stereografica polare) e' REPLICATA nel
// test con le stesse costanti del painter, e il test verifica staticamente che la formula
// nel sorgente sia identica: se il painter cambia proiezione, S7 lo segnala.
'use strict';
import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const htmls = fs.readdirSync(ROOT).filter((f) => /^mri-light-1.2.4.49\.html$/.test(f));
if (!htmls.length) { console.error('FATAL: mri-light-1.2.4.49.html non trovato in ' + ROOT); process.exit(1); }
const HTML = path.join(ROOT, htmls[0]);
const src = fs.readFileSync(HTML, 'utf8');

let failures = 0;
function ok(label, cond, detail = '') {
  console.log(`  [${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? ' -- ' + detail : ''}`);
  if (!cond) failures++;
}
function extractFn(source, name) {
  const marker = 'function ' + name + '(';
  const i = source.indexOf(marker);
  if (i < 0) throw new Error('funzione ' + name + ' non trovata');
  const end = source.indexOf('\n}', i);
  if (end < 0) throw new Error('chiusura di ' + name + ' non trovata');
  return source.slice(i, end + 2);
}

// ---------- ESTRAZIONE DAL SORGENTE REALE ----------
let medianSrc, capMeanSrc, capColorSrc;
try {
  medianSrc = extractFn(src, 'fmPolarMedian');
  capMeanSrc = extractFn(src, 'fmPolarCapMean');
  capColorSrc = extractFn(src, 'fmPolarCapColor');
} catch (e) {
  console.error('FATAL: ' + e.message);
  process.exit(1);
}
const sandbox = { console, fmPolarFrame: null };
vm.createContext(sandbox);
vm.runInContext('var fmPolarCapCache = null;\n' + medianSrc + '\n' + capMeanSrc + '\n' + capColorSrc + '\n', sandbox);
const fmPolarMedian = sandbox.fmPolarMedian;
const fmPolarCapMean = sandbox.fmPolarCapMean;
const fmPolarCapColor = sandbox.fmPolarCapColor;

ok('E1: fmPolarMedian, fmPolarCapMean e fmPolarCapColor estratte ed eseguibili',
  typeof fmPolarMedian === 'function' && typeof fmPolarCapMean === 'function' && typeof fmPolarCapColor === 'function');

// ---------- MEDIANA (helper puro) ----------
ok('M1: mediana dispari', fmPolarMedian([3, 1, 2]) === 2, String(fmPolarMedian([3, 1, 2])));
ok('M2: mediana pari = media dei due centrali', fmPolarMedian([1, 2, 3, 4]) === 3, String(fmPolarMedian([1, 2, 3, 4])));
ok('M3: mediana di array vuoto = 0', fmPolarMedian([]) === 0);
ok('M4: la mediana non riordina l\'array di input', (() => {
  const a = [9, 1, 5];
  fmPolarMedian(a);
  return a[0] === 9 && a[1] === 1 && a[2] === 5;
})());

// ---------- RIGA 0 SINTETICA: campo + etichette/frecce del renderer ----------
const SW = 1440, SH = 4, NORTH = 85.0;
function blankRow() { return new Uint8ClampedArray(SW * SH * 4); }
// Riga A: campo COSTANTE 200 + 300 etichette bianche (20%) e 20 frecce scure.
// Serve a isolare la robustezza del capolo: la MEDIANA resta 200, la MEDIA si sposta.
const rowLabels = blankRow();
for (let x = 0; x < SW; x++) {
  const i = x * 4;
  const label = (x % 5 === 0);
  const arrow = (x % 72 === 36);
  const v = label ? 255 : (arrow ? 15 : 200);
  rowLabels[i] = v; rowLabels[i + 1] = label ? 255 : (arrow ? 23 : 200); rowLabels[i + 2] = label ? 255 : (arrow ? 42 : 200); rowLabels[i + 3] = 255;
}
sandbox.fmPolarFrame = { pixels: { width: SW, height: SH, data: rowLabels }, bounds: [[20, -180], [NORTH, 180]] };
const capLabels = fmPolarCapMean(rowLabels, SW);
let meanR = 0;
for (let x = 0; x < SW; x++) meanR += rowLabels[x * 4];
meanR = Math.round(meanR / SW);
ok('C1: il capolo e\' la MEDIANA del campo (200) e non la media trascinata da etichette/frecce',
  capLabels[0] === 200 && meanR !== 200,
  `mediana=${capLabels[0]} mediaTuttiIPixel=${meanR}`);

// Riga B: campo con struttura angolare FORTE (sinusoide intorno a 120): serve al confronto
// old/new sull'anello del polo (il ventaglio della 1.2.4.15 "spalma" proprio questa).
const row = blankRow();
for (let x = 0; x < SW; x++) {
  const i = x * 4;
  const wave = Math.round(120 + 110 * Math.sin((x / SW) * Math.PI * 2));
  const label = (x % 36 === 0);          // 40 etichette bianche
  const arrow = (x % 36 === 18);         // 40 tratti scuri (frecce)
  row[i] = label ? 255 : (arrow ? 15 : wave);
  row[i + 1] = label ? 255 : (arrow ? 23 : wave);
  row[i + 2] = label ? 255 : (arrow ? 42 : wave);
  row[i + 3] = 255;
}
sandbox.fmPolarFrame = { pixels: { width: SW, height: SH, data: row }, bounds: [[20, -180], [NORTH, 180]] };
const capMean = fmPolarCapMean(row, SW);

// ---------- CONFRONTO old (1.2.4.15: lerp lineare) vs new (1.2.4.16: cubica) ----------
function capColorLinearOld(mean, rowColor, lat, north) {
  const span = 90 - north;
  let t = span > 0 ? (lat - north) / span : 0;
  if (!(t > 0)) t = 0; else if (t > 1) t = 1;
  const w0 = 1 - t;                       // formula 1.2.4.15
  return mean.map((m, k) => Math.round(m + (rowColor[k] - m) * w0));
}
const rowColor = [255, 255, 255, 255];    // un pixel di etichetta: il caso peggiore
function distToMean(c) { return c.reduce((acc, v, k) => acc + Math.abs(v - capMean[k]), 0); }

const lats = [85.5, 86, 87, 88, 89, 89.5];
let ratioOk = true;
const rows = [];
for (const lat of lats) {
  const oldC = capColorLinearOld(capMean, rowColor, lat, NORTH);
  const newC = fmPolarCapColor(capMean, rowColor, lat, NORTH);
  const dOld = distToMean(oldC), dNew = distToMean(newC);
  rows.push({ lat, dOld, dNew, ratio: dOld ? (dNew / dOld) : 0 });
  if (!(dNew <= dOld)) ratioOk = false;
}
ok('D1: la dissolvenza cubica e\' sempre <= della lineare (piu\' vicina al capolo)', ratioOk,
  rows.map((r) => `${r.lat}: ${r.dOld}->${r.dNew}`).join(' | '));
const r88 = rows.find((r) => r.lat === 88);
ok('D2: a 88 N il residuo azimutale e\' sotto il 20% di quello della 1.2.4.15',
  r88 && r88.ratio < 0.20, r88 ? `ratio=${r88.ratio.toFixed(3)}` : 'n/a');
const r87 = rows.find((r) => r.lat === 87);
ok('D3: a 87 N il residuo azimutale e\' sotto il 40% di quello della 1.2.4.15',
  r87 && r87.ratio < 0.40, r87 ? `ratio=${r87.ratio.toFixed(3)}` : 'n/a');

// ---------- MISURA SULL'ANELLO (stessi criteri del canvas): std angolare e colori distinti ----------
// Geometria replicata dal painter: lat = 90 - 2*atan(rho*cap), rho = r/radius, cap = tan(35).
const CANVAS_W = 731, CANVAS_H = 532;
const RADIUS = Math.min(CANVAS_W, CANVAS_H) * 0.48;
const CAP = Math.tan(35 * Math.PI / 180);
function ringStats(useNew) {
  const out = {};
  for (const rad of [2, 4, 6, 8, 10, 12, 16, 20]) {
    let s = 0, s2 = 0, n = 0;
    const seen = new Set();
    for (let a = 0; a < 720; a++) {
      const th = a * Math.PI / 360;
      const dx = (rad * Math.cos(th)) / RADIUS, dy = (rad * Math.sin(th)) / RADIUS;
      const rho = Math.hypot(dx, dy);
      const lat = 90 - 2 * Math.atan(rho * CAP) * 180 / Math.PI;
      const lon = Math.atan2(-dx, dy) * 180 / Math.PI;
      const sx = Math.min(SW - 1, Math.max(0, Math.round(((lon + 180) / 360) * SW)));
      const rc = [row[sx * 4], row[sx * 4 + 1], row[sx * 4 + 2], row[sx * 4 + 3]];
      const c = (lat > NORTH)
        ? (useNew ? fmPolarCapColor(capMean, rc, lat, NORTH) : capColorLinearOld(capMean, rc, lat, NORTH))
        : rc;
      const lum = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
      s += lum; s2 += lum * lum; n++;
      seen.add(c[0] + ',' + c[1] + ',' + c[2]);
    }
    const m = s / n;
    out[rad] = { std: +Math.sqrt(Math.max(0, s2 / n - m * m)).toFixed(2), distinct: seen.size };
  }
  return out;
}
const oldRing = ringStats(false);
const newRing = ringStats(true);
ok('R1: sull\'anello del polo la std angolare della 1.2.4.16 e\' sempre <= della 1.2.4.15',
  [2, 4, 6].every((r) => newRing[r].std <= oldRing[r].std),
  [2, 4, 6].map((r) => `r${r}: ${oldRing[r].std}->${newRing[r].std}`).join(' | '));
ok('R2: al raggio 4 px la std angolare scende sotto il 60% (pixel non piu\' "spalmati")',
  newRing[4].std < oldRing[4].std * 0.6,
  `r4: ${oldRing[4].std} -> ${newRing[4].std}`);
ok('R3: i colori distinti sul raggio 4 px non aumentano (nessuna raggiera in piu\')',
  newRing[4].distinct <= oldRing[4].distinct,
  `r4 distinct: ${oldRing[4].distinct} -> ${newRing[4].distinct}`);
console.log('  [INFO] anello attorno al polo, std/distinct old(1.2.4.15) -> new(1.2.4.16):');
[2, 4, 6, 8, 10, 12, 16, 20].forEach((r) => {
  console.log(`         r=${String(r).padStart(2)}px  std ${String(oldRing[r].std).padStart(6)} -> ${String(newRing[r].std).padStart(6)}   distinct ${String(oldRing[r].distinct).padStart(3)} -> ${String(newRing[r].distinct).padStart(3)}`);
});

// ---------- CONTROLLI STATICI ----------
ok('S1: nessun clamp alla latitudine nord nel painter',
  !/lat\s*=\s*Math\.max\(south,\s*Math\.min\(north,\s*lat\)\)/.test(src));
ok('S2: il ramo del cap non campiona righe (niente sy/si) e usa riga 0 + colore del capolo',
  (() => {
    const i = src.indexOf('if (latTrue > north) {');
    if (i < 0) return false;
    const j = src.indexOf('continue;', i);
    const body = src.slice(i, j).replace(/\/\/[^\r\n]*/g, '');
    return !/\bsy\b/.test(body) && !/\bsi\b/.test(body) && /fmPolarCapColor\(/.test(body);
  })());
ok('S3: dissolvenza CUBICA nel sorgente (w0 = Math.pow(1 - t, 3))',
  /var w0 = Math\.pow\(1 - t, 3\);/.test(src));
ok('S4: la vista polare e\' attiva SOLO con un frame WebP (nessun percorso .om polare)',
  /fmPolarSetActive\(!!\(fmActive && fmRegion === 'north' && webp\)\)/.test(src));
ok('S5: non esiste alcun painter polare del frame .om (nessun .om nel blocco fmPolar*)',
  (() => {
    const i = src.indexOf('var fmPolarCanvas = null;');
    const j = src.indexOf('var FM_SYNTH_PRESSURE_GEOPOT', i);
    if (i < 0 || j < 0) return false;
    const block = src.slice(i, j);
    return !/\.om\b/.test(block.replace(/\/\/[^\r\n]*/g, ''));
  })());
ok('S6: il commento documenta che il .om si ferma a 85,05 N (262 righe) e perche\' non si campiona',
  /262 righe/.test(src) && /getValueFromLatLong/.test(src) && /85,05/.test(src));
ok('S7: proiezione inversa stereografica invariata (90 - 2*atan(rho*cap))',
  /90 - 2 \* Math\.atan\(rho \* cap\) \* 180 \/ Math\.PI/.test(src));
ok('S8: flip delle colonne invariato (sx = sw - 1 - sx)', /sx = sw - 1 - sx;/.test(src));

console.log(failures === 0 ? '\nRESULT: PASS' : `\nRESULT: FAIL (${failures})`);
process.exit(failures === 0 ? 0 : 1);
