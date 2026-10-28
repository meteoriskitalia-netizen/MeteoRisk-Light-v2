#!/usr/bin/env node
// TEST 1.2.4.31 - LA PROIEZIONE (E I BOUNDS) SONO PROPRIETA' DEL FRAME.
//
// COSA COPRE DAVVERO: estrae VERBATIM dall'html di release
//   fmPolarNormalizeProjection, fmProjectionConflictOnce, fmRunRecordForId,
//   fmSelectedRun, fmRegionProjection, fmTimelineBounds, fmTimelineFrameBounds
// e le esegue in un contesto vm con le globali che il client ha davvero
// (fmDomain, fmTimeline, fmWebpIndex, fmSelectedRunId, fmClipBounds).
// Nessuna riscrittura della logica: il codice e' quello che gira nel browser.
//
// CONTRATTO (1.2.4.31+): precedenza
//   frame della timeline -> run dell'index -> dominio (index, poi timeline) ->
//   regione (timeline, poi index) -> 'mercator'.
// Il difetto misurato il 2026-10-26 su ecmwf_ifs025/north: 36 frame della run
// 20261025T120000Z (striscia 2879x521) dichiarati stereographic perche' il
// DOMINIO lo era (run 20261025T180000Z su disco 1000x1000).
//
// LIMITI DICHIARATI: non fa rete e non disegna nulla (nessun canvas): verifica la
// DECISIONE di proiezione e di bounds, non i pixel.
'use strict';

import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const HTML = path.join(ROOT, 'mri-light-1.2.4.57.html');
if (!fs.existsSync(HTML)) { console.error('FATAL: mri-light-1.2.4.57.html non trovato'); process.exit(1); }
const src = fs.readFileSync(HTML, 'utf8');

let failures = 0;
function ok(label, cond, detail = '') {
  console.log(`  [${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? ' -- ' + detail : ''}`);
  if (!cond) failures++;
}
function extractFn(name) {
  const marker = 'function ' + name + '(';
  const i = src.indexOf(marker);
  if (i < 0) throw new Error('funzione ' + name + ' non trovata');
  const end = src.indexOf('\n}', i);
  if (end < 0) throw new Error('chiusura di ' + name + ' non trovata');
  return src.slice(i, end + 2);
}

const FNS = ['fmPolarNormalizeProjection', 'fmProjectionConflictOnce', 'fmRunRecordForId',
             'fmSelectedRun', 'fmRegionProjection', 'fmTimelineBounds',
             'fmTimelineFrameBounds'].map(extractFn);
ok('estratte 7 funzioni verbatim dall\'html',
   FNS.join('\n').includes('fmTimelineFrameBounds') && FNS.join('\n').includes('runKnown'));

const DOMAIN = 'ecmwf_ifs025';
const DISC = 'stereographic';
const STRIP_BOUNDS = [[20, -180], [85, 179.75]];
const DISC_BOUNDS = [[20, -180], [90, 180]];
const DISC_META = { type: 'polar_stereographic', size: 1000, edge_lat: 20, max_lat: 90 };

// Index come lo scrive il renderer NUOVO su uno stato misto: la run nuova e' il
// disco, le due precedenti sono strisce e non hanno la chiave; il dominio NON
// dichiara (le run discordano). Vedi scripts/tests/test_render_fm_webp_projection_runs.py.
function mixedIndex() {
  return {
    region: 'north',
    domains: {
      [DOMAIN]: {
        bounds: DISC_BOUNDS,
        projection: undefined,
        current_run: '20261025T180000Z',
        runs: [
          { id: '20261025T180000Z', projection: DISC, projection_meta: DISC_META, bounds: DISC_BOUNDS },
          { id: '20261025T120000Z', bounds: STRIP_BOUNDS },
          { id: '20261025T060000Z', bounds: STRIP_BOUNDS },
        ],
      },
    },
  };
}
function mixedTimeline() {
  const timeline = [];
  for (let i = 0; i < 36; i++) timeline.push({ valid_time: `2026-10-25T${String(i % 24).padStart(2, '0')}:00Z`, run_id: '20261025T120000Z', bucket: 1, file: `f${i}.webp`, projection: 'mercator' });
  for (let i = 0; i < 49; i++) timeline.push({ valid_time: `2026-10-26T${String(i % 24).padStart(2, '0')}:00Z`, run_id: '20261025T180000Z', bucket: 0, file: `g${i}.webp`, projection: 'stereographic' });
  return {
    region: 'north',
    domains: { [DOMAIN]: { current_run: '20261025T180000Z', runs: [], timeline_groups: { g0: { frames: 85, variables: ['cape'], timeline } } } },
  };
}

function makeCtx({ domain = DOMAIN, timeline = null, index = null, selectedRunId = null, warns = null } = {}) {
  const sandbox = {
    console: { warn: (msg) => { if (warns) warns.push(String(msg)); } },
    fmDomain: domain,
    fmTimeline: timeline,
    fmWebpIndex: index,
    fmSelectedRunId: selectedRunId,
    fmClipBounds: null,
    fmRunDomainEntry: function () {
      return (index && index.domains) ? (index.domains[domain] || null) : null;
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(FNS.join('\n'), sandbox);
  return sandbox;
}

// ---------------------------------------------------------------------------
console.log('--- il FRAME vince sul dominio (difetto misurato: 36 frame su 85) ---');
{
  const ctx = makeCtx({ timeline: mixedTimeline(), index: mixedIndex() });
  const frames = ctx.fmTimeline.domains[DOMAIN].timeline_groups.g0.timeline;
  const counts = {};
  let wrong = 0;
  for (const entry of frames) {
    const got = ctx.fmRegionProjection(null, entry);
    counts[got] = (counts[got] || 0) + 1;
    const want = entry.run_id === '20261025T120000Z' ? 'mercator' : 'stereographic';
    if (got !== want) wrong++;
  }
  ok('85 frame: 36 mercator + 49 stereographic, 0 sbagliati',
     counts.mercator === 36 && counts.stereographic === 49 && wrong === 0,
     JSON.stringify(counts));
  ok('il dominio dell\'index NON dichiara projection (le run discordano)',
     typeof ctx.fmWebpIndex.domains[DOMAIN].projection !== 'string');
}

console.log('--- il FRAME vince anche se il dominio dice il contrario ---');
ok('frame mercator + dominio stereographic -> mercator',
   makeCtx({ timeline: { projection: DISC, domains: { [DOMAIN]: { projection: DISC } } },
             index: { domains: { [DOMAIN]: { projection: DISC, runs: [] } } } })
     .fmRegionProjection(null, { run_id: 'x', projection: 'mercator' }) === 'mercator');
ok('frame stereographic + dominio assente -> stereographic',
   makeCtx({ index: { domains: { [DOMAIN]: { runs: [] } } } })
     .fmRegionProjection(null, { run_id: 'x', projection: 'stereographic' }) === 'stereographic');
ok('frame con projection OGGETTO -> ignorato (si usa la run/il dominio)',
   makeCtx({ index: { domains: { [DOMAIN]: { projection: DISC, runs: [] } } } })
     .fmRegionProjection(null, { run_id: 'x', projection: { type: 'polar_stereographic' } }) === DISC);

console.log('--- timeline VECCHIA (frame senza chiave): decide la RUN dell\'index ---');
ok('run 20261025T120000Z senza chiave -> mercator (non il dominio)',
   makeCtx({ index: mixedIndex() })
     .fmRegionProjection(null, { run_id: '20261025T120000Z', file: 'x.webp' }) === 'mercator');
ok('run 20261025T180000Z su disco -> stereographic',
   makeCtx({ index: mixedIndex() })
     .fmRegionProjection(null, { run_id: '20261025T180000Z', file: 'x.webp' }) === DISC);
ok('run non piu\' indicizzata (potata) -> fallback al dominio',
   makeCtx({
     index: { domains: { [DOMAIN]: { projection: DISC, runs: [{ id: 'altro' }] } } },
   }).fmRegionProjection(null, { run_id: '20250101T000000Z', file: 'x.webp' }) === DISC);

console.log('--- catena di fallback (nessun frame, nessuna run) ---');
ok('dominio (index) -> stereographic',
   makeCtx({ index: { domains: { [DOMAIN]: { projection: DISC } } } }).fmRegionProjection() === DISC);
ok('dominio (timeline) -> stereographic senza index',
   makeCtx({ timeline: { domains: { [DOMAIN]: { projection: DISC } } } }).fmRegionProjection() === DISC);
ok('regione (timeline) -> stereographic',
   makeCtx({ timeline: { projection: DISC } }).fmRegionProjection() === 'stereographic');
ok('regione (index) -> stereographic',
   makeCtx({ index: { projection: DISC, domains: {} } }).fmRegionProjection() === DISC);
ok('niente -> mercator', makeCtx().fmRegionProjection() === 'mercator');
ok('valore sconosciuto -> mercator',
   makeCtx({ timeline: { projection: 'plate-carree' } }).fmRegionProjection() === 'mercator');
ok('risultato SEMPRE una stringa',
   typeof makeCtx({ index: mixedIndex() }).fmRegionProjection(null, { run_id: '20261025T120000Z' }) === 'string');

console.log('--- conflitto frame/index: vince il frame e si logga UNA volta ---');
{
  const warns = [];
  const index = { domains: { [DOMAIN]: { projection: DISC, runs: [{ id: 'r1', projection: DISC }] } } };
  const ctx = makeCtx({ index, warns });
  const frame = { run_id: 'r1', projection: 'mercator' };
  ctx.fmRegionProjection(null, frame);
  ctx.fmRegionProjection(null, frame);
  ctx.fmRegionProjection(null, frame);
  ok('3 chiamate con lo stesso conflitto -> 1 solo warning', warns.length === 1,
     JSON.stringify(warns));
  ctx.fmRegionProjection(null, { run_id: 'r1', projection: 'stereographic' });
  ok('frame e index concordi (stereographic) -> nessun warning nuovo', warns.length === 1,
     JSON.stringify(warns.length));
  const warns2 = [];
  const ctx2 = makeCtx({ index: { domains: { [DOMAIN]: { projection: 'mercator', runs: [{ id: 'r1', projection: 'mercator' }] } } }, warns: warns2 });
  ctx2.fmRegionProjection(null, { run_id: 'r1', projection: 'stereographic' });
  ok('conflitto opposto (frame disco, index striscia) -> 1 warning',
     warns2.length === 1 && warns2[0].includes('vince il frame stereographic'),
     JSON.stringify(warns2));
  ok('nessun warning quando frame e index concordano',
     (() => { const w = []; const c = makeCtx({ index, warns: w });
       c.fmRegionProjection(null, { run_id: 'r1', projection: DISC });
       c.fmRegionProjection(null, { run_id: 'r1', projection: DISC });
       return w.length; })() === 0);
}

console.log('--- BOUNDS del frame dalla SUA run (non dal dominio) ---');
{
  const ctx = makeCtx({ index: mixedIndex() });
  const oldBounds = ctx.fmTimelineFrameBounds({ run_id: '20261025T120000Z' });
  const newBounds = ctx.fmTimelineFrameBounds({ run_id: '20261025T180000Z' });
  ok('frame della striscia -> bounds 20..85', JSON.stringify(oldBounds) === JSON.stringify(STRIP_BOUNDS),
     JSON.stringify(oldBounds));
  ok('frame del disco -> bounds 20..90', JSON.stringify(newBounds) === JSON.stringify(DISC_BOUNDS),
     JSON.stringify(newBounds));
  ok('run non indicizzata -> fallback ai bounds del dominio',
     JSON.stringify(ctx.fmTimelineFrameBounds({ run_id: 'ignota' })) === JSON.stringify(DISC_BOUNDS));
  ok('senza index -> clip/dominio (fmTimelineBounds)',
     makeCtx({ index: null }).fmTimelineFrameBounds({ run_id: 'x' }) === null);
}

console.log(failures ? `\nFATAL: ${failures} controlli falliti` : '\nOK: tutti i controlli PASS');
process.exit(failures ? 1 : 0);
