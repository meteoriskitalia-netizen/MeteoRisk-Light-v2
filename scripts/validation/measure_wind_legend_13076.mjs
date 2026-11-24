#!/usr/bin/env node
// MISURA (non test) della legenda VENTO/RAFFICA 1.3.0.75 -> 1.3.0.76.
//   A) quante etichette mostra la legenda adattiva (260 px, 200 px, 320 px)
//      prima e dopo, e se si sovrappongono (stessa stima di larghezza usata dal
//      codice: nessuna tolleranza inventata);
//   B) distanza RGB fra classi adiacenti (massimo sui tre canali);
//   C) testo delle tacche: m/s e km/h.
//
// USO: node scripts/validation/measure_wind_legend_13076.mjs <prima.html> <dopo.html>
'use strict';
import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const args = process.argv.slice(2);
const FILES = {
  '1.3.0.75': args[0] || path.join(ROOT, 'mri-light-1.3.0.75.html'),
  '1.3.0.78': args[1] || path.join(ROOT, 'mri-light-1.3.0.78.html')
};

function extractFn(src, name) {
  const m = src.match(new RegExp('(?:async\\s+)?function\\s+' + name + '\\s*\\('));
  if (!m) return null;
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
  return null;
}
function extractVar(src, name) {
  const m = src.match(new RegExp('var\\s+' + name + '\\s*='));
  if (!m) return null;
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

function loadApp(file, width) {
  const src = fs.readFileSync(file, 'utf8');
  const vars = ['STATION_FIELD_WIND_BREAKPOINTS', 'STATION_FIELD_WIND_COLORS',
    'STATION_FIELD_WIND_SCALE', 'STATION_FIELD_LEGEND'];
  const fns = ['stationFieldBandIndex', 'stationFieldScale', 'stationFieldTickText',
    'stationFieldLegendLabelText', 'stationFieldLegendLabelWidth', 'stationFieldLegendLayout'];
  const parts = [];
  for (const v of vars) { const s = extractVar(src, v); if (s) parts.push(s); }
  for (const f of fns) { const s = extractFn(src, f); if (s) parts.push(s); }
  const ctx = { console, Math, Number, String, Object, Array, isFinite,
    document: { getElementById: () => ({ clientWidth: width }) } };
  vm.createContext(ctx);
  vm.runInContext(parts.join('\n'), ctx);
  return ctx;
}

function overlapCount(items) {
  let n = 0, minGap = null;
  for (let i = 1; i < items.length; i++) {
    const a = items[i - 1], b = items[i];
    const gap = (b.x - a.x) - (a.w + b.w) / 2;
    if (minGap === null || gap < minGap) minGap = gap;
    if (gap < 2 - 1e-6) n++;
  }
  return { overlaps: n, minGapPx: minGap === null ? null : +minGap.toFixed(2) };
}

const out = { etichette: {}, colori: {}, unita: {} };
for (const v of Object.keys(FILES)) {
  if (!fs.existsSync(FILES[v])) { out[v] = { errore: 'file assente' }; continue; }
  const row = { larghezze: {} };
  const ctx260 = loadApp(FILES[v], 260);
  for (const width of [200, 260, 320]) {
    const ctx = loadApp(FILES[v], width);
    const lg = ctx.stationFieldLegendLayout({ scale: 'station_wind_classes' }, { vMin: null, vMax: null });
    const ov = overlapCount(lg.items);
    row.larghezze[width] = {
      bande: lg.bandCount, mostrate: lg.shown, passo: lg.step,
      sovrapposizioni: ov.overlaps, distanza_minima_px: ov.minGapPx,
      etichette: lg.items.map((it) => it.text)
    };
  }
  row.etichette_candidate = ctx260.STATION_FIELD_WIND_BREAKPOINTS.length;
  row.breakpoints = ctx260.STATION_FIELD_WIND_BREAKPOINTS.slice();
  row.html_10 = ctx260.stationFieldLegendLabelText(10, 'station_wind_classes');
  row.html_2_5 = ctx260.stationFieldLegendLabelText(2.5, 'station_wind_classes');
  row.html_10_senza_scala = ctx260.stationFieldLegendLabelText(10, 'temperature_2m');
  const cols = ctx260.STATION_FIELD_WIND_COLORS;
  const dist = [];
  for (let i = 0; i + 1 < cols.length; i++) {
    const a = cols[i], b = cols[i + 1];
    dist.push({ coppia: i + '-' + (i + 1), d: Math.max(Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1]), Math.abs(a[2] - b[2])) });
  }
  row.colori = { n: cols.length, adiacenti: dist, min: Math.min(...dist.map((x) => x.d)) };
  out.etichette[v] = row;
}
console.log(JSON.stringify(out, null, 2));
