#!/usr/bin/env node
// TEST 1.2.4.16 - CAPOLO POLARE (artefatto a stella al polo).
//
// COSA COPRE DAVVERO: la logica del cap e' ESTRATTA dall'html di release
// (fmPolarCapMean a MEDIANA + fmPolarCapColor con dissolvenza CUBICA, estratte per nome dal sorgente servito) e
// eseguita su un WebP sintetico in un contesto vm. Non e' una replica della
// funzione: se l'html cambia, il test legge il codice nuovo.
//
// LIMITI DICHIARATI: non disegna il canvas (niente DOM/browser), non valida il
// colore reale dei WebP di produzione ne' le isolinee disegnate dal renderer;
// verifica il comportamento al bordo del dato (lat = north), al polo (lat = 90)
// e la continuita' attorno a north. Il ramo di riproiezione e' controllato a
// livello statico (esistenza del ramo, assenza del vecchio clamp, flip di
// colonne e formula inversa invariati).
'use strict';

import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const htmls = fs.readdirSync(ROOT).filter((f) => /^mri-light-1.2.4.29.html$/.test(f));
if (!htmls.length) { console.error('FATAL: mri-light-1.2.4.29.html non trovato in ' + ROOT); process.exit(1); }
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
let capMeanSrc, capColorSrc, medianSrc;
try {
  capMeanSrc = extractFn(src, 'fmPolarCapMean');
  capColorSrc = extractFn(src, 'fmPolarCapColor');
  medianSrc = extractFn(src, 'fmPolarMedian');
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
ok('E1: fmPolarMedian, fmPolarCapMean e fmPolarCapColor estratte ed eseguibili', typeof fmPolarMedian === 'function' && typeof fmPolarCapMean === 'function' && typeof fmPolarCapColor === 'function');

// ---------- DATI SINTETICI: RIGA 0 CON COLORE VARIABILE E BUCHI TRASPARENTI ----------
const SW = 360, SH = 8, NORTH = 85.05112878;
function makeData(seed) {
  const data = new Uint8ClampedArray(SW * SH * 4);
  for (let x = 0; x < SW; x++) {
    const i = x * 4;
    const hole = x < 10;                    // primi 10 pixel: trasparenti (fuori dal dato)
    data[i] = hole ? 250 : (seed + x * 3) % 256;
    data[i + 1] = hole ? 250 : (seed + x * 5) % 256;
    data[i + 2] = hole ? 250 : (seed + x * 7) % 256;
    data[i + 3] = hole ? 0 : 255;
    for (let y = 1; y < SH; y++) {
      const j = (y * SW + x) * 4;
      data[j] = 1; data[j + 1] = 2; data[j + 2] = 3; data[j + 3] = 255;
    }
  }
  return data;
}
const data = makeData(17);
const pixels = { width: SW, height: SH, data };
sandbox.fmPolarFrame = { pixels: pixels, bounds: [[20, -180], [NORTH, 180]] };
const row0 = (x) => [data[x * 4], data[x * 4 + 1], data[x * 4 + 2], data[x * 4 + 3]];

const mean = fmPolarCapMean(data, SW);
// 1.2.4.16: il capolo e' la MEDIANA per canale dei soli x >= 10 (buchi trasparenti esclusi)
function medianOf(arr) {
  const c = arr.slice().sort((a, b) => a - b);
  const m = c.length >> 1;
  return (c.length % 2) ? c[m] : Math.round((c[m - 1] + c[m]) / 2);
}
const rv = [], gv = [], bv = [], av = [];
for (let x = 10; x < SW; x++) { const i = x * 4; rv.push(data[i]); gv.push(data[i + 1]); bv.push(data[i + 2]); av.push(data[i + 3]); }
const exp = [medianOf(rv), medianOf(gv), medianOf(bv), medianOf(av)];
ok('A1: colore del capolo = MEDIANA esatta dei pixel OPACHI della riga 0',
  mean.length === 4 && mean.every((v, k) => v === exp[k]), `capMean=[${mean}] atteso=[${exp}]`);
ok('A2: i pixel trasparenti non entrano nella mediana (i 10 buchi valgono 250,250,250)',
  mean[0] !== 250 || mean[1] !== 250 || mean[2] !== 250);
ok('A3: alpha del capolo = 255 (tutti i pixel contati sono opachi)', mean[3] === 255);
// A4: ROBUSTEZZA ALLE ETICHETTE/FRECCE DEL RENDERER. Riga 0 di 360 px con colore di campo
// [200,200,200] e 40 px bianchi (etichette disegnate DENTRO il WebP): la MEDIANA resta sul
// colore di campo, la MEDIA aritmetica no (sarebbe 206). È il motivo della 1.2.4.16.
const SW2 = 360, labelData = new Uint8ClampedArray(SW2 * 2 * 4);
let sr = 0, n = 0;
for (let x = 0; x < SW2; x++) {
  const i = x * 4;
  const white = (x % 9 === 0);                    // 40 etichette
  const v = white ? 255 : 200;
  labelData[i] = v; labelData[i + 1] = v; labelData[i + 2] = v; labelData[i + 3] = 255;
  sr += v; n++;
}
sandbox.fmPolarFrame = { pixels: { width: SW2, height: 2, data: labelData }, bounds: [[20, -180], [NORTH, 180]] };
const labelCap = fmPolarCapMean(labelData, SW2);
const arithmeticMean = Math.round(sr / n);
ok('A4: la mediana del capolo non e\' trascinata dalle etichette bianche (media != mediana)',
  labelCap[0] === 200 && arithmeticMean !== 200,
  `mediana=${labelCap[0]} media=${arithmeticMean}`);
sandbox.fmPolarFrame = { pixels: pixels, bounds: [[20, -180], [NORTH, 180]] };

// (i) BORDO DEL CAP: lat = north -> colore REALE della riga 0 all'azimuth del pixel
const sxBorder = 200;
const rowBorder = row0(sxBorder);
const cBorder = fmPolarCapColor(mean, rowBorder, NORTH, NORTH);
ok('i1: a lat = north il colore e\' quello della riga 0 all\'azimuth del pixel',
  cBorder.every((v, k) => v === rowBorder[k]), `cap=[${cBorder}] riga0=[${rowBorder}]`);
const cBorder2 = fmPolarCapColor(mean, row0(300), NORTH, NORTH);
ok('i2: a lat = north azimuth diversi danno colori diversi (il bordo resta il dato reale)',
  cBorder.join(',') !== cBorder2.join(','));

// (ii) CENTRO (lat = 90): nessuna variazione angolare
const cCenterA = fmPolarCapColor(mean, row0(20), 90, NORTH);
const cCenterB = fmPolarCapColor(mean, row0(300), 90, NORTH);
ok('ii1: al polo il colore NON dipende dall\'azimuth (stesso valore su 2 azimuth diversi)',
  cCenterA.join(',') === cCenterB.join(','), `A=[${cCenterA}] B=[${cCenterB}]`);
ok('ii2: varianza angolare al polo = 0 su tutti i canali',
  Math.max(...cCenterA.map((v, k) => Math.abs(v - cCenterB[k]))) === 0);
ok('ii3: al polo il colore e\' quello del capolo (media), non la riga 0',
  cCenterA.every((v, k) => v === mean[k]), `cap=[${cCenterA}] media=[${mean}]`);

// (iii) CONTINUITA' attorno a north (84.9 -> 85.1)
const rowMid = row0(150);
const cBelow = fmPolarCapColor(mean, rowMid, 84.9, NORTH);
const cAbove = fmPolarCapColor(mean, rowMid, 85.1, NORTH);
ok('iii1: a lat 84.9 (sotto il cap) il colore coincide con la riga 0 (nessun salto al bordo)',
  cBelow.every((v, k) => v === rowMid[k]));
const jump = Math.max(...cAbove.map((v, k) => Math.abs(v - cBelow[k])));
// 1.2.4.16: dissolvenza CUBICA -> la pendenza sul bordo e' 3x quella lineare, ma 0,1 gradi
// sopra il bordo sono ~0,6 px: il salto resta impercettibile (<= 5 livelli per canale).
ok('iii2: nessun salto tra 84.9 e 85.1 (delta max per canale <= 5)',
  jump <= 5, `delta=${jump}`);
const distTo = (c) => c.reduce((acc, v, k) => acc + Math.abs(v - mean[k]), 0);
const ladder = [85.06, 86, 88, 89.9].map((lat) => distTo(fmPolarCapColor(mean, rowMid, lat, NORTH)));
ok('iii3: avvicinandosi al polo il colore converge monotonicamente al capolo',
  ladder.every((d, i) => i === 0 || d <= ladder[i - 1]), `distanze=${ladder.map((d) => d.toFixed(1))}`);

// (iv) CACHE PER FRAME
const m1 = fmPolarCapMean(data, SW);
const data2 = makeData(200);
sandbox.fmPolarFrame.pixels.data.set(data2);          // stesso oggetto pixels -> frame invariato
const m2 = fmPolarCapMean(sandbox.fmPolarFrame.pixels.data, SW);
ok('iv1: la media e\' cacheata sul frame (stesso array, nessun ricalcolo per pixel/frame)',
  m1 === m2);
sandbox.fmPolarFrame = { pixels: { width: SW, height: SH, data: data2 }, bounds: [[20, -180], [NORTH, 180]] };
const m3 = fmPolarCapMean(data2, SW);
ok('iv2: al cambio frame la media viene ricalcolata', m3 !== m1 && m3.join(',') !== m1.join(','));

// ---------- CONTROLLI STATICI SUL RAMO DI RIPROIEZIONE ----------
ok('S1: il vecchio clamp su north e\' stato rimosso',
  !/lat\s*=\s*Math\.max\(south,\s*Math\.min\(north,\s*lat\)\)/.test(src));
ok('S2: esiste il ramo dedicato sopra il bordo nord (latTrue > north)',
  /if\s*\(latTrue > north\)\s*\{/.test(src));
const branchStart = src.indexOf('if (latTrue > north) {');
const branchEnd = src.indexOf('continue;', branchStart);
const branch = branchStart >= 0 && branchEnd > branchStart ? src.slice(branchStart, branchEnd) : '';
const branchCode = branch.replace(/\/\/[^\r\n]*/g, ''); // senza commenti (CRLF-safe)
ok('S3: nel ramo del cap NON si campiona la riga sy (nessun accesso a sy/si)',
  branch.length > 0 && !/\bsy\b/.test(branchCode) && !/\bsi\b/.test(branchCode));
ok('S4: nel ramo del cap si legge la riga 0 all\'azimuth (r0 = sx * 4)',
  /var r0 = sx \* 4;/.test(branch) && /fmPolarCapColor\(/.test(branch));
ok('S5: la media del capolo e\' calcolata una volta per frame prima del loop',
  /var capMean = fmPolarCapMean\(data, sw\);/.test(src));
ok('S6: flip di colonne invariato (sx = sw - 1 - sx)', /sx = sw - 1 - sx;/.test(src));
ok('S7: proiezione inversa stereografica invariata (90 - 2*atan(rho*cap))',
  /90 - 2 \* Math\.atan\(rho \* cap\) \* 180 \/ Math\.PI/.test(src));
ok('S8: il commento spiega il limite Mercator 85.05112878 e che il .om arriva a 90',
  /85\.05112878/.test(src) && /Mercator/.test(src) && /90 N/.test(src));

console.log(failures === 0 ? '\nRESULT: PASS' : `\nRESULT: FAIL (${failures})`);
process.exit(failures === 0 ? 0 : 1);
