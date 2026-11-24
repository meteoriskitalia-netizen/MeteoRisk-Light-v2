#!/usr/bin/env node
// MISURA (non test) della regressione del campo centraline 1.3.0.72 -> 1.3.0.73.
//
// Confronta TRE html con gli STESSI dati reali (data/stations/latest.json) e la
// STESSA maschera terra (province_italiane.geojson rasterizzato qui, even-odd):
//   - 1.3.0.68  : il raster che l'utente accettava (riferimento per `local`);
//   - 1.3.0.72  : il reticolo 0,5Â° + bilineare + gaussiano largo (il difetto);
//   - 1.3.0.73  : il ripristino + maschera morbida (il candidato da pubblicare).
//
// MISURE (accettazione dichiarata nel brief):
//   a) pixel dipinti in `local` per la pioggia: 1.3.0.68 vs 1.3.0.73 (Â±10%);
//   b) zona asciutta: pixel con pioggia VISIBILE dove la stazione piu' vicina
//      segna 0 mm (1.3.0.72 deve essere peggiore del nuovo);
//   c) discontinuita' massima per colonna in `full` (l'artefatto 1,963 Â°C);
//   d) tempi: local < 400 ms, full < 400 ms, contour < 800 ms.
//
// USO: node scripts/validation/measure_station_field_13073.mjs <68.html> <72.html> <73.html>
// I due riferimenti non sono piu' su main: si materializzano dalla storia
//   git show bc417f0d:mri-light-1.3.0.68.html > ref68.html
//   git show 8b0d4b5b:mri-light-1.3.0.72.html > ref72.html
// e si passano come primi due argomenti (il terzo e' l'APP_FILE nuovo).
'use strict';
import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const args = process.argv.slice(2);
const FILES = {
  '1.3.0.68': args[0] || path.join(ROOT, '_ref68.html'),
  '1.3.0.72': args[1] || path.join(ROOT, '_ref72.html'),
  '1.3.0.73': args[2] || path.join(ROOT, 'mri-light-1.3.0.73.html')
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

const FNS = ['stationFieldDistKm', 'stationFieldIdwAt', 'stationFieldRaster', 'stationFieldBandIndex',
  'stationFieldScale', 'stationFieldDownscale', 'stationFieldSoftenMask', 'stationFieldFullGrid',
  'stationFieldContourLevels', 'stationFieldContourSegments', 'stationFieldContourLabels',
  'stationFieldContourResample', 'stationFieldContourPolylines', 'stationFieldChaikin',
  'stationFieldContourVectorData'];
const VARS = ['STATION_FIELD_K', 'STATION_FIELD_CUTOFF_KM', 'STATION_FIELD_FADE_KM',
  'STATION_FIELD_BUCKET_DEG', 'STATION_FIELD_MAX_RING', 'STATION_FIELD_SUPERSAMPLE',
  'STATION_FIELD_FULL_K', 'STATION_FIELD_FULL_BUCKET_DEG', 'STATION_FIELD_FULL_MAX_RING',
  'STATION_FIELD_RAIN_CUTOFF_KM', 'STATION_FIELD_CONTOUR', 'STATION_FIELD_CONTOUR_LABEL_MAX',
  'STATION_FIELD_CONTOUR_LABEL_MIN_PX', 'STATION_FIELD_CONTOUR_GRID_MULT',
  'STATION_FIELD_CONTOUR_GRID_MAX_W', 'STATION_FIELD_CONTOUR_CHAIKIN',
  // solo 1.3.0.70/0.72: gaussiano, LUT, reticolo (assenti nel ripristino)
  'STATION_FIELD_SIGMA_FRAC', 'STATION_FIELD_KM_PER_DEG', 'STATION_FIELD_EXP_STEPS',
  'STATION_FIELD_EXP_MAX', 'STATION_FIELD_EXP_LUT', 'STATION_FIELD_LATTICE_DEG',
  'FM_TEMP2M_BREAKPOINTS', 'FM_TEMP2M_COLORS', 'FM_PRECIP_BREAKPOINTS', 'FM_PRECIP_COLORS'];

function loadApp(version) {
  const file = FILES[version];
  const src = fs.readFileSync(file, 'utf8');
  const codeParts = [];
  const missing = [];
  for (const v of VARS) {
    const s = extractVar(src, v);
    if (s) codeParts.push(s); else missing.push('var ' + v);
  }
  for (const f of FNS) {
    const s = extractFn(src, f);
    if (s) codeParts.push(s); else missing.push('fn ' + f);
  }
  const ctx = {
    console, Math, Date, Number, String, Object, Array, isFinite, JSON,
    Uint8ClampedArray, Float32Array, Uint8Array, Int32Array,
    stationFieldVar: 'temp'
  };
  vm.createContext(ctx);
  vm.runInContext(codeParts.join('\n'), ctx);
  ctx.FM_COLOR_SCALE_OVERRIDES = {
    temperature_2m: {
      breakpoints: ctx.FM_TEMP2M_BREAKPOINTS,
      colors: { light: ctx.FM_TEMP2M_COLORS, dark: ctx.FM_TEMP2M_COLORS }
    },
    precipitation: {
      breakpoints: ctx.FM_PRECIP_BREAKPOINTS,
      colors: { light: ctx.FM_PRECIP_COLORS, dark: ctx.FM_PRECIP_COLORS }
    }
  };
  // realm normale, come nell'app: costi e risultati senza proxy del contesto.
  // FM_COLOR_SCALE_OVERRIDES e' una var del realm (nel test e' lo stesso):
  // stationFieldScale la legge a ogni chiamata, non e' una property del Function.
  const benchCode = 'var stationFieldVar = "temp";\n' + codeParts.join('\n') +
    '\nvar FM_COLOR_SCALE_OVERRIDES = {' +
    ' temperature_2m: { breakpoints: FM_TEMP2M_BREAKPOINTS, colors: { light: FM_TEMP2M_COLORS, dark: FM_TEMP2M_COLORS } },' +
    ' precipitation: { breakpoints: FM_PRECIP_BREAKPOINTS, colors: { light: FM_PRECIP_COLORS, dark: FM_PRECIP_COLORS } } };' +
    '\nreturn { raster: stationFieldRaster, downscale: stationFieldDownscale,' +
    ' scale: stationFieldScale, band: stationFieldBandIndex, setVar: function (v) { stationFieldVar = v; }, contour: ' +
    (extractFn(src, 'stationFieldContourVectorData') ? 'stationFieldContourVectorData' : 'null') + ' };';
  const bench = new Function('var stationsDaily=null;var STATIONS_MAX_OBS_AGE_MIN=90;\n' + benchCode)();
  bench.FM_COLOR_SCALE_OVERRIDES = ctx.FM_COLOR_SCALE_OVERRIDES;
  return { ctx, bench, missing, src };
}

function makeFrame(minLat, maxLat, minLon, maxLon, W, H) {
  const mnM = Math.log(Math.tan(Math.PI / 4 + minLat * Math.PI / 360));
  const mxM = Math.log(Math.tan(Math.PI / 4 + maxLat * Math.PI / 360));
  return { W, H, minLat, maxLat, minLon, maxLon, mnM, mxM, mSpan: mxM - mnM, lonSpan: maxLon - minLon };
}
function pixelLat(frame, j) {
  const m = frame.mxM - (j + 0.5) / frame.H * frame.mSpan;
  return (2 * Math.atan(Math.exp(m)) - Math.PI / 2) * 180 / Math.PI;
}
function pixelLon(frame, i) { return frame.minLon + (i + 0.5) / frame.W * frame.lonSpan; }

// Maschera terra dalle province (even-odd, scanline). Stessa proiezione di
// rasterLandMask nell'app: confronti fatti sulla stessa griglia.
function landMask(frame, geojson) {
  const W = frame.W, H = frame.H;
  const edges = [];
  for (const f of geojson.features) {
    const g = f.geometry; if (!g || !g.coordinates) continue;
    const polys = g.type === 'MultiPolygon' ? g.coordinates : [g.coordinates];
    for (const poly of polys) for (const ring of poly) {
      const pts = ring.map(([lon, lat]) => {
        const m = Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
        return [(lon - frame.minLon) / frame.lonSpan * (W - 1),
                (frame.mxM - m) / frame.mSpan * (H - 1)];
      });
      for (let i = 0; i + 1 < pts.length; i++) edges.push([pts[i], pts[i + 1]]);
    }
  }
  const mask = new Uint8Array(W * H);
  for (let y = 0; y < H; y++) {
    const yc = y + 0.5, xs = [];
    for (const [a, b] of edges) {
      const y0 = a[1], y1 = b[1];
      if ((y0 <= yc && y1 > yc) || (y1 <= yc && y0 > yc)) {
        xs.push(a[0] + (yc - y0) / (y1 - y0) * (b[0] - a[0]));
      }
    }
    xs.sort((p, q) => p - q);
    for (let k = 0; k + 1 < xs.length; k += 2) {
      const x0 = Math.ceil(xs[k] - 0.5), x1 = Math.floor(xs[k + 1] - 0.5);
      for (let x = Math.max(0, x0); x <= Math.min(W - 1, x1); x++) mask[y * W + x] = 255;
    }
  }
  return mask;
}

function haversineKm(lat1, lon1, lat2, lon2) {
  const R = 6371, t = Math.PI / 180;
  const dLat = (lat2 - lat1) * t, dLon = (lon2 - lon1) * t;
  const s = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * t) * Math.cos(lat2 * t) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}

// ---------- dati reali ----------
const raw = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/stations/latest.json'), 'utf8'));
const NOW = Date.parse(raw.generated_at);
const AGE_TAU = { rain: 45, temp: 120 }, AGE_MAX = { rain: 180, temp: 360 };
function collect(varKey) {
  const pts = [];
  for (const s of raw.stations) {
    let v, cov;
    if (varKey === 'rain') { v = s.rain_1h_mm; cov = s.rain_1h_coverage_frac; }
    else { v = s.temp_mean_1h_c; if (v === null || v === undefined) v = s.temperature_2m; cov = 1; }
    if (v === null || v === undefined || v === '' || !isFinite(Number(v))) continue;
    if (cov !== null && cov !== undefined && isFinite(Number(cov)) && Number(cov) < 0.5) continue;
    const age = s.obs_time ? (NOW - Date.parse(s.obs_time)) / 60000 : 0;
    if (!(age <= AGE_MAX[varKey])) continue;
    pts.push({ lat: s.lat, lon: s.lon, v: Number(v), id: s.id, wAge: Math.exp(-Math.max(0, age) / AGE_TAU[varKey]), ageMin: age });
  }
  return pts;
}
const ptsRain = collect('rain');
const ptsTemp = collect('temp');

const frame = makeFrame(36.0, 48.0, 6.0, 19.0, 256, 227);
const geo = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/geography/province_italiane.geojson'), 'utf8'));
const mask = landMask(frame, geo);
let landPixels = 0; for (let i = 0; i < mask.length; i++) if (mask[i]) landPixels++;

const apps = {};
for (const v of Object.keys(FILES)) {
  if (!fs.existsSync(FILES[v])) { console.log('FILE ASSENTE per ' + v + ': ' + FILES[v]); continue; }
  apps[v] = loadApp(v);
}
const out = { versioni: Object.keys(apps), stazioni: { pioggia: ptsRain.length, temperatura: ptsTemp.length }, terra: landPixels, frame: '36-48N 6-19E 256x227' };

function bench(fn, n = 3) {
  fn();
  let best = Infinity;
  for (let i = 0; i < n; i++) { const t = process.hrtime.bigint(); fn(); best = Math.min(best, Number(process.hrtime.bigint() - t) / 1e6); }
  return best;
}

// ---------- a) pixel dipinti in local, pioggia ----------
function localRun(app, varKey, pts) {
  const b = app.bench;
  b.setVar(varKey);
  const scale = b.scale('precipitation');
  const t0 = process.hrtime.bigint();
  const r = b.raster(pts, scale, frame, mask, 2);
  const ms = Number(process.hrtime.bigint() - t0) / 1e6;
  const d = b.downscale(r, frame.W, frame.H, 2, mask);
  let painted = 0, vis = 0;
  for (let i = 0; i < d.alphas.length; i++) if (d.alphas[i] > 0) { painted++; if (d.values[i] >= app.ctx.FM_PRECIP_BREAKPOINTS[1]) vis++; }
  return { painted, vis, ms, values: d.values, alphas: d.alphas };
}
const a = {};
for (const v of Object.keys(apps)) {
  if (!apps[v].bench.raster) continue;
  a[v] = localRun(apps[v], 'rain', ptsRain);
}
out.local_pioggia = {};
for (const v of Object.keys(a)) out.local_pioggia[v] = { dipinti: a[v].painted, visibili: a[v].vis, ms: +a[v].ms.toFixed(1) };
out.local_pioggia.stazioni_pioggia_gt0 = ptsRain.filter((p) => p.v > 0).length;
if (a['1.3.0.68'] && a['1.3.0.73']) {
  out.local_pioggia.delta_pct = +(((a['1.3.0.73'].painted - a['1.3.0.68'].painted) / a['1.3.0.68'].painted) * 100).toFixed(2);
}

// ---------- b) zona asciutta ----------
// Per ogni pixel VISIBILE (banda >= 1) si guarda la stazione piu' vicina: se
// segna 0 mm, quel pixel e' "colore dove non piove". Rapporto sul visibile.
function dryRun(app) {
  const b = app.bench;
  b.setVar('rain');
  const scale = b.scale('precipitation');
  const r = b.raster(ptsRain, scale, frame, mask, 2);
  const d = b.downscale(r, frame.W, frame.H, 2, mask);
  let vis = 0, dryPaint = 0, farRain = 0;
  const rainStations = ptsRain.filter((p) => p.v > 0);
  for (let j = 0; j < frame.H; j++) {
    const plat = pixelLat(frame, j);
    for (let i = 0; i < frame.W; i++) {
      const idx = j * frame.W + i;
      if (!d.alphas[idx]) continue;
      if (!(d.values[idx] >= app.ctx.FM_PRECIP_BREAKPOINTS[1])) continue;
      vis++;
      const plon = pixelLon(frame, i);
      let dNear = Infinity, vNear = null;
      for (const p of ptsRain) {
        const dd = haversineKm(plat, plon, p.lat, p.lon);
        if (dd < dNear) { dNear = dd; vNear = p.v; }
      }
      if (vNear === 0) dryPaint++;
      let dRain = Infinity;
      for (const p of rainStations) dRain = Math.min(dRain, haversineKm(plat, plon, p.lat, p.lon));
      if (dRain > 15) farRain++;
    }
  }
  return { visibili: vis, zero_vicino: dryPaint, oltre_15km_da_pioggia: farRain };
}
out.zona_asciutta = {};
for (const v of Object.keys(apps)) { if (apps[v].bench.raster) out.zona_asciutta[v] = dryRun(apps[v]); }

// ---------- c) discontinuita' massima per colonna in full ----------
// `full` esiste dalla 1.3.0.69: per la 1.3.0.68 la misura non si applica.
function fullDiscont(app) {
  const b = app.bench;
  b.setVar('temp');
  const scale = b.scale('temperature_2m');
  const args = [ptsTemp, scale, frame, mask, 1];
  if (app.ctx.STATION_FIELD_FULL_K !== undefined) args.push('full');
  const r = b.raster(...args);
  let max = 0, at = null, sum = 0, n = 0;
  for (let i = 0; i < frame.W; i++) {
    for (let j = 0; j + 1 < frame.H; j++) {
      const p0 = j * frame.W + i, p1 = (j + 1) * frame.W + i;
      if (!r.alphas[p0] || !r.alphas[p1]) continue;
      const dv = Math.abs(r.values[p1] - r.values[p0]);
      if (!isFinite(dv)) continue;
      sum += dv; n++;
      if (dv > max) { max = dv; at = { col: i, row: j }; }
    }
  }
  return { max: +max.toFixed(3), media: +(sum / n).toFixed(3), at };
}
out.discontinuita_full = {};
for (const v of Object.keys(apps)) { if (apps[v].bench.raster) out.discontinuita_full[v] = fullDiscont(apps[v]); }

// ---------- c-bis) discontinuita' su campo SINTETICO LISCIO ----------
// La misura sui dati reali e' dominata da un contrasto vero (Monte Rasu 6,2 °C e
// Tirso 16,8 °C a 9,1 km in Sardegna): per isolare l'ARTEFATTO (seam da lista di
// vicini per cella) si usa un campo liscio noto, senza gradienti veri.
function syntheticDiscont(app) {
  const b = app.bench;
  b.setVar('temp');
  const scale = b.scale('temperature_2m');
  const pts = [];
  for (let la = 37; la < 47.5; la += 0.35) {
    for (let lo = 7; lo < 18.6; lo += 0.35) {
      pts.push({ lat: la, lon: lo, v: 10 + 8 * Math.sin(la) + 3 * Math.cos(lo) });
    }
  }
  const args = [pts, scale, frame, mask, 1];
  if (app.ctx.STATION_FIELD_FULL_K !== undefined) args.push('full');
  const r = b.raster(...args);
  let max = 0, at = null, sum = 0, n = 0;
  for (let i = 0; i < frame.W; i++) {
    for (let j = 0; j + 1 < frame.H; j++) {
      const p0 = j * frame.W + i, p1 = (j + 1) * frame.W + i;
      if (!r.alphas[p0] || !r.alphas[p1]) continue;
      const dv = Math.abs(r.values[p1] - r.values[p0]);
      if (!isFinite(dv)) continue;
      sum += dv; n++;
      if (dv > max) { max = dv; at = { col: i, row: j }; }
    }
  }
  return { metodo: args.length > 5 ? 'full' : 'local', punti: pts.length,
           max: +max.toFixed(3), media: +(sum / n).toFixed(3), at };
}
out.discontinuita_sintetica = {};
for (const v of Object.keys(apps)) { if (apps[v].bench.raster) out.discontinuita_sintetica[v] = syntheticDiscont(apps[v]); }

// ---------- d) tempi ----------
const times = {};
for (const v of Object.keys(apps)) {
  const b = apps[v].bench;
  if (!b.raster) continue;
  const scaleT = b.scale('temperature_2m');
  b.setVar('temp');
  const tLocal = bench(() => b.downscale(b.raster(ptsTemp, scaleT, frame, mask, 2), frame.W, frame.H, 2, mask));
  let tFull = null, tContour = null;
  if (apps[v].ctx.STATION_FIELD_FULL_K !== undefined) {
    tFull = bench(() => b.raster(ptsTemp, scaleT, frame, mask, 1, 'full'));
    if (b.contour) {
      tContour = bench(() => {
        const r = b.raster(ptsTemp, scaleT, frame, mask, 1, 'contour');
        b.contour(r.values, r.alphas, r.W, r.H, frame, 'temp', { vMin: -10, vMax: 30 }, 'Â°C', { pxPerFieldPx: 1 });
      });
    }
  }
  times[v] = { local_ms: +tLocal.toFixed(0), full_ms: tFull === null ? null : +tFull.toFixed(0), contour_ms: tContour === null ? null : +tContour.toFixed(0) };
}
out.tempi = times;

console.log(JSON.stringify(out, null, 2));


