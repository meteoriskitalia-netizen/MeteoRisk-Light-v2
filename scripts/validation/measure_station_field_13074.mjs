#!/usr/bin/env node
// MISURA (non test) della 1.3.0.74 sul campo centraline:
//   A) feather della maschera terra 1.3.0.73 vs 1.3.0.74 sulla COSTA REALE
//      (Liguria/Toscana) e su un CONFINE SINTETICO (retta diagonale), con la
//      stessa maschera province rasterizzata even-odd dell'app;
//   B) scala neve in cm: RGB renderizzato per 1/10/50/100/200/300 cm e
//      distanza RGB fra 10 e 100 cm.
//
// USO: node scripts/validation/measure_station_field_13074.mjs [73.html] [74.html]
'use strict';
import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const args = process.argv.slice(2);
const FILES = {
  '1.3.0.73': args[0] || path.join(ROOT, 'mri-light-1.3.0.73.html'),
  '1.3.0.74': args[1] || path.join(ROOT, 'mri-light-1.3.0.74.html')
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

function loadApp(version) {
  const file = FILES[version];
  const src = fs.readFileSync(file, 'utf8');
  const parts = [];
  for (const v of ['STATION_FIELD_SNOW_BREAKPOINTS', 'STATION_FIELD_SNOW_COLORS', 'STATION_FIELD_SNOW_SCALE']) {
    const s = extractVar(src, v); if (s) parts.push(s);
  }
  for (const f of ['stationFieldSoftenMask', 'stationFieldBandIndex', 'stationFieldScale']) {
    const s = extractFn(src, f); if (s) parts.push(s);
  }
  const ctx = { console, Math, Number, String, Object, Array, Float32Array, Uint8Array, isFinite };
  vm.createContext(ctx);
  vm.runInContext(parts.join('\n'), ctx);
  return ctx;
}

// Maschera terra dalle province (even-odd, scanline). Stessa proiezione di
// rasterLandMask nell'app (frame Web Mercator, W x H).
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

// Distanza (in pixel) di OGNI pixel dalla terra piu' vicina, entro maxD.
function distanceToLand(mask, W, H, maxD) {
  const dist = new Int16Array(W * H).fill(999);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    if (mask[y * W + x] >= 128) { dist[y * W + x] = 0; continue; }
    let best = 999;
    for (let dy = -maxD; dy <= maxD; dy++) {
      const yy = y + dy; if (yy < 0 || yy >= H) continue;
      for (let dx = -maxD; dx <= maxD; dx++) {
        const xx = x + dx; if (xx < 0 || xx >= W) continue;
        if (mask[yy * W + xx] >= 128) {
          const dd = Math.max(Math.abs(dx), Math.abs(dy));   // Chebyshev: 1 px = 8-vicini
          if (dd < best) best = dd;
        }
      }
    }
    dist[y * W + x] = best;
  }
  return dist;
}

function seaStats(soft, mask, dist, W, H, box) {
  const out = { sea: 0, seaAlpha: 0, seaAlphaGe2: 0, byDist: {}, maxGrad: 0, sumGrad: 0, nGrad: 0, maxDistAlpha: 0 };
  let x0 = 0, x1 = W - 1, y0 = 0, y1 = H - 1;
  if (box) { x0 = box.x0; x1 = box.x1; y0 = box.y0; y1 = box.y1; }
  for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
    const i = y * W + x, a = Math.round(soft[i] * 255);
    if (mask[i] >= 128) continue;
    out.sea++;
    if (a > 0) {
      out.seaAlpha++;
      const d = dist[i];
      if (d >= 2) out.seaAlphaGe2++;
      if (d > out.maxDistAlpha) out.maxDistAlpha = d;
      if (!out.byDist[d]) out.byDist[d] = { n: 0, sum: 0, max: 0 };
      out.byDist[d].n++; out.byDist[d].sum += a; if (a > out.byDist[d].max) out.byDist[d].max = a;
    }
  }
  // Larghezza del gradiente: attraversando il bordo su una riga, conta i pixel
  // non pieni (0 < alpha < 250) fra l'ultimo pieno e il primo trasparente.
  for (let y = y0; y <= y1; y++) {
    let run = 0, seenLand = false;
    for (let x = x0; x <= x1; x++) {
      const i = y * W + x, a = Math.round(soft[i] * 255);
      if (a >= 250) { seenLand = true; if (run > out.maxGrad) out.maxGrad = run; if (run) { out.sumGrad += run; out.nGrad++; } run = 0; continue; }
      if (a > 0) { run++; continue; }
      if (seenLand || run) { if (run > out.maxGrad) out.maxGrad = run; if (run) { out.sumGrad += run; out.nGrad++; } }
      run = 0; seenLand = false;
    }
  }
  return out;
}

function pct(n, d) { return d ? +((100 * n / d).toFixed(2)) : 0; }

const W = 256, H = 227, minLat = 36, maxLat = 48, minLon = 6, maxLon = 19;
const geo = JSON.parse(fs.readFileSync(process.env.STATIONS_GEO || path.join(ROOT, 'data/geography/province_italiane.geojson'), 'utf8'));
const mask = landMask(W, H, minLat, maxLat, minLon, maxLon, geo);
let land = 0; for (let i = 0; i < mask.length; i++) if (mask[i]) land++;
const dist = distanceToLand(mask, W, H, 8);

// Box costa Liguria/Toscana (lon 8,4-12,6 / lat 42,0-44,6) proiettato sul frame.
function toPx(lon, lat) {
  const mnM = Math.log(Math.tan(Math.PI / 4 + minLat * Math.PI / 360));
  const mxM = Math.log(Math.tan(Math.PI / 4 + maxLat * Math.PI / 360));
  const m = Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
  return [Math.round((lon - minLon) / (maxLon - minLon) * (W - 1)),
          Math.round((mxM - m) / (mxM - mnM) * (H - 1))];
}
const pNW = toPx(8.4, 44.6), pSE = toPx(12.6, 42.0);
const coastBox = { x0: Math.max(0, pNW[0]), x1: Math.min(W - 1, pSE[0]),
                   y0: Math.max(0, pNW[1]), y1: Math.min(H - 1, pSE[1]) };

// Confine "sintetico": semipiano diagonale (come un bordo di provincia sul raster).
const synth = new Uint8Array(W * H);
for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
  synth[y * W + x] = (x - 40 - y * 0.6) >= 0 ? 255 : 0;
}
const synthDist = distanceToLand(synth, W, H, 8);

const out = {
  frame: '36-48N 6-19E 256x227', terra_px: land,
  costa_liguria_toscana_box: coastBox,
  versioni: {}
};
for (const v of Object.keys(FILES)) {
  const ctx = loadApp(v);
  const t0 = process.hrtime.bigint();
  const soft = ctx.stationFieldSoftenMask(mask, W, H);
  const ms = Number(process.hrtime.bigint() - t0) / 1e6;
  const softSynth = ctx.stationFieldSoftenMask(synth, W, H);
  const coast = seaStats(soft, mask, dist, W, H, coastBox);
  const all = seaStats(soft, mask, dist, W, H, null);
  const conf = seaStats(softSynth, synth, synthDist, W, H, null);
  const byDist = {};
  for (const k of Object.keys(coast.byDist).sort((a, b) => a - b)) {
    const d = coast.byDist[k];
    byDist[k] = { px: d.n, alpha_medio: +(d.sum / d.n).toFixed(1), alpha_max: d.max };
  }
  // Neve: RGB per i valori richiesti.
  const snow = {};
  if (ctx.STATION_FIELD_SNOW_SCALE) {
    for (const cm of [0, 0.5, 1, 5, 10, 25, 50, 100, 200, 300]) {
      const band = ctx.stationFieldBandIndex(ctx.STATION_FIELD_SNOW_SCALE, cm);
      const c = ctx.STATION_FIELD_SNOW_SCALE.colors.light[band];
      snow[cm] = { rgb: [c[0], c[1], c[2]], alpha: c[3], banda: band };
    }
  }
  const c10 = snow[10] && snow[10].rgb, c100 = snow[100] && snow[100].rgb;
  const dRGB = (c10 && c100) ? [Math.abs(c10[0] - c100[0]), Math.abs(c10[1] - c100[1]), Math.abs(c10[2] - c100[2])] : null;
  out.versioni[v] = {
    soften_ms: +ms.toFixed(1),
    costa: {
      mare_px: coast.sea,
      mare_alpha_gt0: coast.seaAlpha, mare_alpha_gt0_pct: pct(coast.seaAlpha, coast.sea),
      mare_alpha_gt0_oltre_1px: coast.seaAlpha - (coast.byDist[1] ? coast.byDist[1].n : 0),
      mare_alpha_gt0_oltre_2px: coast.seaAlphaGe2,
      gradiente_larghezza_max_px: coast.maxGrad, mare_alpha_distanza_max_px: coast.maxDistAlpha,
      gradiente_larghezza_media_px: coast.nGrad ? +(coast.sumGrad / coast.nGrad).toFixed(2) : 0,
      per_distanza: byDist
    },
    tutta_italia: {
      mare_px: all.sea, mare_alpha_gt0: all.seaAlpha,
      mare_alpha_gt0_oltre_2px: all.seaAlphaGe2,
      mare_alpha_gt0_oltre_3px: Math.max(0, all.seaAlpha - Object.keys(all.byDist).filter((k) => +k <= 2).reduce((s, k) => s + all.byDist[k].n, 0)),
      gradiente_larghezza_max_px: all.maxGrad, mare_alpha_distanza_max_px: all.maxDistAlpha
    },
    confine_sintetico: {
      mare_alpha_gt0: conf.seaAlpha,
      mare_alpha_gt0_oltre_1px: conf.seaAlpha - (conf.byDist[1] ? conf.byDist[1].n : 0),
      mare_alpha_gt0_oltre_2px: conf.seaAlphaGe2,
      gradiente_larghezza_max_px: conf.maxGrad, mare_alpha_distanza_max_px: conf.maxDistAlpha,
      gradiente_larghezza_media_px: conf.nGrad ? +(conf.sumGrad / conf.nGrad).toFixed(2) : 0
    },
    neve: snow,
    neve_dist_10_100: dRGB
  };
}

console.log(JSON.stringify(out, null, 2));

// ---------- B) ALPHA FINALE del raster (ss=2 + downscale), dati reali ----------
// Quello che si vede e' l'alpha del raster dopo il downscale, non la sola
// maschera: si misura sullo stesso campo (temperatura) con le stazioni reali e
// si contano i pixel di MARE dipinti, per distanza dalla terra.
function loadBench(version) {
  const src = fs.readFileSync(FILES[version], 'utf8');
  const vars = ['STATION_FIELD_K', 'STATION_FIELD_CUTOFF_KM', 'STATION_FIELD_FADE_KM',
    'STATION_FIELD_BUCKET_DEG', 'STATION_FIELD_MAX_RING', 'STATION_FIELD_RAIN_CUTOFF_KM',
    'STATION_FIELD_SUPERSAMPLE', 'FM_TEMP2M_BREAKPOINTS', 'FM_TEMP2M_COLORS'];
  const fns = ['stationFieldDistKm', 'stationFieldIdwAt', 'stationFieldRaster',
    'stationFieldBandIndex', 'stationFieldScale', 'stationFieldDownscale', 'stationFieldSoftenMask'];
  const parts = [];
  for (const v of vars) { const s = extractVar(src, v); if (s) parts.push(s); }
  for (const f of fns) { const s = extractFn(src, f); if (s) parts.push(s); }
  const ctx = { console, Math, Date, Number, String, Object, Array, isFinite, JSON,
    Uint8ClampedArray, Float32Array, Uint8Array, Int32Array,
    stationFieldVar: 'temp', stationsDaily: null };
  vm.createContext(ctx);
  vm.runInContext(parts.join('\n'), ctx);
  ctx.FM_COLOR_SCALE_OVERRIDES = {
    temperature_2m: { breakpoints: ctx.FM_TEMP2M_BREAKPOINTS,
      colors: { light: ctx.FM_TEMP2M_COLORS, dark: ctx.FM_TEMP2M_COLORS } }
  };
  return ctx;
}
let stations = null;
try {
  stations = JSON.parse(fs.readFileSync(process.env.STATIONS_JSON || path.join(ROOT, 'data/stations/latest.json'), 'utf8'));
} catch (e) { stations = null; }
if (stations && stations.stations) {
  const NOW = Date.parse(stations.generated_at);
  const pts = [];
  for (const s of stations.stations) {
    let v = s.temp_mean_1h_c; if (v === null || v === undefined) v = s.temperature_2m;
    if (v === null || v === undefined || !isFinite(Number(v))) continue;
    const age = s.obs_time ? (NOW - Date.parse(s.obs_time)) / 60000 : 0;
    if (!(age <= 360)) continue;
    pts.push({ lat: s.lat, lon: s.lon, v: Number(v), id: s.id, wAge: Math.exp(-Math.max(0, age) / 120), ageMin: age });
  }
  out.raster_reale = { stazioni_temperatura: pts.length, generato: stations.generated_at, versioni: {} };
  const frameR = { W, H, minLat, maxLat, minLon, maxLon,
    mnM: Math.log(Math.tan(Math.PI / 4 + minLat * Math.PI / 360)),
    mxM: Math.log(Math.tan(Math.PI / 4 + maxLat * Math.PI / 360)), lonSpan: maxLon - minLon };
  frameR.mSpan = frameR.mxM - frameR.mnM;
  for (const v of Object.keys(FILES)) {
    const b = loadBench(v);
    const scale = b.stationFieldScale('temperature_2m');
    const r = b.stationFieldRaster(pts, scale, frameR, mask, 2);
    let softMask = null;
    if (v === '1.3.0.73') {
      // Fedele alla 1.3.0.73: il downscale RIAPPLICA la maschera morbida (soft^2).
      const sm = b.stationFieldSoftenMask(mask, W, H);
      softMask = new Uint8Array(sm.length);
      for (let i = 0; i < sm.length; i++) softMask[i] = Math.round(sm[i] * 255);
    }
    const d = b.stationFieldDownscale(r, W, H, 2, softMask);
    const sea = { mare_px: 0, alpha_gt0: 0, alpha_gt0_oltre_2px: 0, dist_max: 0, byDist: {} };
    for (let i = 0; i < d.alphas.length; i++) {
      if (mask[i] >= 128) continue;
      sea.mare_px++;
      const a = d.alphas[i];
      if (!(a > 0)) continue;
      sea.alpha_gt0++;
      const dd = dist[i];
      if (dd >= 2) sea.alpha_gt0_oltre_2px++;
      if (dd > sea.dist_max) sea.dist_max = dd;
      if (!sea.byDist[dd]) sea.byDist[dd] = { n: 0, sum: 0, max: 0 };
      sea.byDist[dd].n++; sea.byDist[dd].sum += a; if (a > sea.byDist[dd].max) sea.byDist[dd].max = a;
    }
    const bd = {};
    for (const k of Object.keys(sea.byDist).sort((x, y) => x - y)) {
      const q = sea.byDist[k];
      bd[k] = { px: q.n, alpha_medio: +(q.sum / q.n).toFixed(1), alpha_max: q.max };
    }
    out.raster_reale.versioni[v] = {
      mare_px: sea.mare_px, mare_alpha_gt0: sea.alpha_gt0,
      mare_alpha_gt0_oltre_2px: sea.alpha_gt0_oltre_2px,
      mare_alpha_distanza_max_px: sea.dist_max, per_distanza: bd
    };
  }
  console.log('\n--- ALPHA FINALE DEL RASTER (dati reali, temperatura) ---');
  console.log(JSON.stringify(out.raster_reale, null, 2));
}
