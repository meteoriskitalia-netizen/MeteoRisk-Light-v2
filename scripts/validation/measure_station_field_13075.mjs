#!/usr/bin/env node
// MISURA (non test) della 1.3.0.75, neve del campo centraline.
//
// Confronta 1.3.0.74 (baseline) e 1.3.0.75 (candidata) sui DATI REALI
// (data/stations/latest.json, snow_depth_m -> cm come stationsNormalize):
//   A) pixel DIPINTI dalla neve sul frame Italia 256x227, metodo `local` e
//      metodo `full`, prima e dopo (alpha > 0 sul raster finale ss=2 +
//      downscale, la stessa strada che finisce sullo schermo);
//   B) zona SENZA stazioni con neve (Sicilia, lat 36,6-38,3 / lon 12,3-15,7):
//      pixel dipinti prima e dopo (atteso 0 dopo);
//   C) scala: 0 / 0,5 / 1 / 25 cm e distanza RGB fra 1 e 25 cm.
//
// USO: node scripts/validation/measure_station_field_13075.mjs <baseline.html> <candidata.html>
'use strict';
import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const args = process.argv.slice(2);
const FILES = {
  '1.3.0.74': args[0] || path.join(ROOT, 'mri-light-1.3.0.74.html'),
  '1.3.0.75': args[1] || path.join(ROOT, 'mri-light-1.3.0.75.html')
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

function loadApp(file) {
  const src = fs.readFileSync(file, 'utf8');
  const vars = ['STATION_FIELD_K', 'STATION_FIELD_CUTOFF_KM', 'STATION_FIELD_FADE_KM',
    'STATION_FIELD_BUCKET_DEG', 'STATION_FIELD_MAX_RING', 'STATION_FIELD_RAIN_CUTOFF_KM',
    'STATION_FIELD_FULL_K', 'STATION_FIELD_FULL_BUCKET_DEG', 'STATION_FIELD_FULL_MAX_RING',
    'STATION_FIELD_SNOW_CUTOFF_KM', 'STATION_FIELD_SNOW_FADE_KM',
    'STATION_FIELD_SNOW_BREAKPOINTS', 'STATION_FIELD_SNOW_COLORS', 'STATION_FIELD_SNOW_SCALE'];
  const fns = ['stationFieldDistKm', 'stationFieldIdwAt', 'stationFieldRaster',
    'stationFieldBandIndex', 'stationFieldScale', 'stationFieldDownscale', 'stationFieldSoftenMask'];
  const parts = [];
  for (const v of vars) { const s = extractVar(src, v); if (s) parts.push(s); }
  for (const f of fns) { const s = extractFn(src, f); if (s) parts.push(s); }
  const ctx = { console, Math, Date, Number, String, Object, Array, isFinite, JSON,
    Uint8ClampedArray, Float32Array, Uint8Array, Int32Array,
    stationFieldVar: 'snow', stationsDaily: null };
  vm.createContext(ctx);
  vm.runInContext(parts.join('\n'), ctx);
  return ctx;
}

// Maschera terra dalle province (even-odd, scanline): stessa proiezione di
// rasterLandMask nell'app.
function landMask(W, H, minLat, maxLat, minLon, maxLon, geojson) {
  const mnM = Math.log(Math.tan(Math.PI / 4 + minLat * Math.PI / 360));
  const mxM = Math.log(Math.tan(Math.PI / 4 + maxLat * Math.PI / 360));
  const mSpan = mxM - mnM, lonSpan = maxLon - minLon;
  const edges = [];
  for (const f of geojson.features) {
    const g = f.geometry; if (!g || !g.coordinates) continue;
    const polys = g.type === 'MultiPolygon' ? g.coordinates : [g.coordinates];
    for (const poly of polys) for (const ring of poly) {
      const pts = ring.map(([lon, lat]) => {
        const m = Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
        return [(lon - minLon) / lonSpan * (W - 1), (mxM - m) / mSpan * (H - 1)];
      });
      for (let i = 0; i + 1 < pts.length; i++) edges.push([pts[i], pts[i + 1]]);
    }
  }
  const mask = new Uint8Array(W * H);
  for (let y = 0; y < H; y++) {
    const yc = y + 0.5, xs = [];
    for (const [a, b] of edges) {
      const y0 = a[1], y1 = b[1];
      if ((y0 <= yc && y1 > yc) || (y1 <= yc && y0 > yc)) xs.push(a[0] + (yc - y0) / (y1 - y0) * (b[0] - a[0]));
    }
    xs.sort((p, q) => p - q);
    for (let k = 0; k + 1 < xs.length; k += 2) {
      const x0 = Math.ceil(xs[k] - 0.5), x1 = Math.floor(xs[k + 1] - 0.5);
      for (let x = Math.max(0, x0); x <= Math.min(W - 1, x1); x++) mask[y * W + x] = 255;
    }
  }
  return mask;
}

const W = 256, H = 227, minLat = 36, maxLat = 48, minLon = 6, maxLon = 19;
const geo = JSON.parse(fs.readFileSync(process.env.STATIONS_GEO || path.join(ROOT, 'data/geography/province_italiane.geojson'), 'utf8'));
const mask = landMask(W, H, minLat, maxLat, minLon, maxLon, geo);
let land = 0; for (let i = 0; i < mask.length; i++) if (mask[i]) land++;

function toPx(lon, lat) {
  const mnM = Math.log(Math.tan(Math.PI / 4 + minLat * Math.PI / 360));
  const mxM = Math.log(Math.tan(Math.PI / 4 + maxLat * Math.PI / 360));
  const m = Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
  return [Math.round((lon - minLon) / (maxLon - minLon) * (W - 1)),
          Math.round((mxM - m) / (mxM - mnM) * (H - 1))];
}
// Sicilia (isola): lat 36,6-38,3 / lon 12,3-15,7. Nessuna stazione con neve
// entro 10 km in questo dataset: il campo deve restare trasparente.
const pNW = toPx(12.3, 38.3), pSE = toPx(15.7, 36.6);
const sicily = { x0: Math.max(0, pNW[0]), x1: Math.min(W - 1, pSE[0]),
                 y0: Math.max(0, pNW[1]), y1: Math.min(H - 1, pSE[1]) };

const stations = JSON.parse(fs.readFileSync(process.env.STATIONS_JSON || path.join(ROOT, 'data/stations/latest.json'), 'utf8'));
const NOW = Date.parse(stations.generated_at);
const pts = [];
for (const s of stations.stations) {
  const m = s.snow_depth_m;
  if (m === null || m === undefined || !isFinite(Number(m))) continue;
  const cm = Math.round(Number(m) * 1000) / 10;   // come stationsNormalize
  if (!(cm > 0)) continue;                        // gli zeri non entrano nel campo
  const age = s.obs_time ? (NOW - Date.parse(s.obs_time)) / 60000 : 0;
  if (!(age <= 720)) continue;                    // policy neve: max 720 min
  pts.push({ lat: s.lat, lon: s.lon, v: cm, id: s.id,
             wAge: Math.exp(-Math.max(0, age) / 240), ageMin: age });
}

const frame = { W, H, minLat, maxLat, minLon, maxLon,
  mnM: Math.log(Math.tan(Math.PI / 4 + minLat * Math.PI / 360)),
  mxM: Math.log(Math.tan(Math.PI / 4 + maxLat * Math.PI / 360)), lonSpan: maxLon - minLon };
frame.mSpan = frame.mxM - frame.mnM;

function countPainted(d) {
  let painted = 0, landPainted = 0, sicilyPainted = 0;
  for (let i = 0; i < d.alphas.length; i++) {
    if (!(d.alphas[i] > 0)) continue;
    painted++;
    if (mask[i] >= 128) landPainted++;
    const x = i % W, y = (i / W) | 0;
    if (x >= sicily.x0 && x <= sicily.x1 && y >= sicily.y0 && y <= sicily.y1) sicilyPainted++;
  }
  return { painted, landPainted, sicilyPainted, pctLand: +((100 * landPainted / land).toFixed(2)) };
}

const out = {
  frame: '36-48N 6-19E 256x227',
  generato: stations.generated_at,
  stazioni_con_neve_cm_gt0: pts.length,
  terra_px: land,
  sicilia_box: sicily,
  versioni: {}
};
for (const v of Object.keys(FILES)) {
  if (!fs.existsSync(FILES[v])) { out.versioni[v] = { errore: 'file assente: ' + FILES[v] }; continue; }
  const ctx = loadApp(FILES[v]);
  const scale = ctx.stationFieldScale('station_snow_cm');
  const row = { cutoff_km: ctx.STATION_FIELD_SNOW_CUTOFF_KM || null,
                fade_km: ctx.STATION_FIELD_SNOW_FADE_KM || null, metodi: {} };
  for (const method of ['local', 'full']) {
    const t0 = process.hrtime.bigint();
    const r = ctx.stationFieldRaster(pts, scale, frame, mask, 2, method);
    const d = ctx.stationFieldDownscale(r, W, H, 2, null);
    const ms = Number(process.hrtime.bigint() - t0) / 1e6;
    row.metodi[method] = { ms: +ms.toFixed(0), raster_painted: r.painted, ...countPainted(d) };
  }
  const snow = {};
  for (const cm of [0, 0.5, 1, 5, 10, 25, 50, 100, 200, 300]) {
    const band = ctx.stationFieldBandIndex(scale, cm);
    const c = scale.colors.light[band];
    snow[cm] = { rgb: [c[0], c[1], c[2]], alpha: c[3], banda: band };
  }
  row.scala = snow;
  const a = snow[1].rgb, b = snow[25].rgb;
  row.neve_dist_1_25 = Math.max(Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1]), Math.abs(a[2] - b[2]));
  out.versioni[v] = row;
}

console.log(JSON.stringify(out, null, 2));
