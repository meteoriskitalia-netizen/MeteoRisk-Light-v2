#!/usr/bin/env node
// TEST 1.2.4.31 - IL PARSER VERO DEL CLIENT LEGGE `projection` DALLA TIMELINE.
//
// COSA COPRE DAVVERO: estrae VERBATIM dall'html di release
// fmPolarNormalizeProjection e fmRegionProjection (le funzioni che decidono se
// disegnare un disco polare gia' proiettato o una striscia Mercator) e le
// esegue in un contesto vm cambiando fmTimeline / fmWebpIndex / fmDomain.
// Nessuna riscrittura della logica: il codice e' quello che gira nel browser.
//
// CONTRATTO (1.2.4.30+): precedenza dominio (`index.json` ->
// domains.<dominio>.projection) -> timeline di dominio (`timeline.json` ->
// domains.<dominio>.projection) -> timeline di regione (`timeline.json` ->
// projection) -> regione (`index.json` -> projection) -> 'mercator'. Una
// proiezione che non sia una STRINGA viene ignorata (typeof === 'string').
//
// PERCHE' CONTA: `build_timeline_index.py` scrive `projection` a livello
// dominio (sempre) e a livello regione (solo se TUTTI i domini concordano).
// Fino alla 1.2.4.29 il client leggeva della timeline SOLO la chiave di
// regione: con uno stato misto (ECMWF su disco, dwd_icon/ncep/arpege su
// striscia) quella chiave e' assente apposta, quindi senza `index.json` il
// disco veniva disegnato come striscia 2879x521 - il difetto misurato. Dalla
// 1.2.4.30 la chiave di dominio della timeline viene letta PRIMA di quella di
// regione: i casi sotto la coprono.
//
// LIMITI DICHIARATI: non fa rete e non disegna nulla (nessun canvas): verifica
// la DECISIONE di proiezione, non i pixel.
'use strict';

import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const HTML = path.join(ROOT, 'mri-light-1.2.4.40.html');
if (!fs.existsSync(HTML)) { console.error('FATAL: mri-light-1.2.4.40.html non trovato'); process.exit(1); }
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

const FN_NORMALIZE = extractFn('fmPolarNormalizeProjection');
const FN_REGION_PROJECTION = extractFn('fmRegionProjection');
ok('estratte fmPolarNormalizeProjection e fmRegionProjection dall\'html',
   FN_NORMALIZE.includes('stereographic') && FN_REGION_PROJECTION.includes('fmTimeline'));

// Le due funzioni finiscono in un contesto vm con lo stato del client. Gli
// oggetti globali che il client ha davvero (fmRunDomainEntry, fmTimeline,
// fmWebpIndex, fmDomain) si impostano per caso.
function makeCtx({ domain = 'ecmwf_ifs025', timeline = null, index = null } = {}) {
  const sandbox = {
    console,
    fmDomain: domain,
    fmTimeline: timeline,
    fmWebpIndex: index,
    fmRunDomainEntry: function () {
      return (index && index.domains) ? (index.domains[domain] || null) : null;
    },
  };
  vm.createContext(sandbox);
  vm.runInContext([FN_NORMALIZE, FN_REGION_PROJECTION].join('\n'), sandbox);
  return sandbox;
}

const TIMELINE_DISC = { region: 'north', projection: 'stereographic', domains: { ecmwf_ifs025: {} } };
const TIMELINE_STRIP = { region: 'north', domains: { dwd_icon: {} } };
const INDEX_MIXED = {
  region: 'north',
  domains: {
    ecmwf_ifs025: { projection: 'stereographic' },
    dwd_icon: { grid: { nx: 2879, ny: 521 } },
  },
};
// Stato MISTO come lo scrive build_timeline_index.py: la chiave di dominio c'e'
// su chi ha il disco, quella di regione NO (i domini non concordano).
const TIMELINE_MIXED = {
  region: 'north',
  domains: {
    ecmwf_ifs025: { projection: 'stereographic' },
    dwd_icon: { frames: 209 },
    ncep_gfs025: { frames: 209 },
  },
};
// Forma dei primi build del disco: `projection` come OGGETTO, da ignorare.
const TIMELINE_OBJ_DOMAIN = {
  region: 'north',
  domains: { ecmwf_ifs025: { projection: { type: 'polar_stereographic', size: 1000 } } },
};

console.log('--- senza index.json (solo timeline) ---');
ok('timeline con projection di regione -> stereographic',
   makeCtx({ timeline: TIMELINE_DISC }).fmRegionProjection() === 'stereographic');
ok('timeline senza projection -> mercator',
   makeCtx({ timeline: TIMELINE_STRIP, domain: 'dwd_icon' }).fmRegionProjection() === 'mercator');
ok('nessuna timeline e nessun index -> mercator',
   makeCtx({ timeline: null, index: null }).fmRegionProjection() === 'mercator');

console.log('--- senza index.json, chiave di DOMINIO nella timeline (fix 1.2.4.30) ---');
ok('stato misto: il dominio sul disco legge la timeline -> stereographic',
   makeCtx({ timeline: TIMELINE_MIXED }).fmRegionProjection() === 'stereographic');
ok('stato misto: il dominio ancora a striscia resta -> mercator',
   makeCtx({ timeline: TIMELINE_MIXED, domain: 'dwd_icon' }).fmRegionProjection() === 'mercator');
ok('projection OGGETTO nella timeline di dominio -> ignorata, fallback mercator',
   makeCtx({ timeline: TIMELINE_OBJ_DOMAIN }).fmRegionProjection() === 'mercator');

console.log('--- con index.json (stato misto reale) ---');
ok('dominio disco (ecmwf_ifs025) -> stereographic',
   makeCtx({ timeline: TIMELINE_STRIP, index: INDEX_MIXED }).fmRegionProjection() === 'stereographic');
ok('dominio striscia (dwd_icon, 2879x521) -> mercator',
   makeCtx({ timeline: TIMELINE_STRIP, domain: 'dwd_icon', index: INDEX_MIXED })
     .fmRegionProjection() === 'mercator');
ok('il dominio che DICHIARA una proiezione vince sulla timeline',
   makeCtx({
     timeline: TIMELINE_DISC,
     index: { domains: { dwd_icon: { projection: 'mercator' } } },
     domain: 'dwd_icon',
   }).fmRegionProjection() === 'mercator');

console.log('--- forme non valide ---');
ok('projection OGGETTO nel dominio -> ignorata, si passa alla timeline',
   makeCtx({
     timeline: TIMELINE_DISC,
     index: { domains: { ecmwf_ifs025: { projection: { type: 'polar_stereographic' } } } },
   }).fmRegionProjection() === 'stereographic');
ok('valore sconosciuto -> mercator',
   makeCtx({ timeline: { projection: 'plate-carree' } }).fmRegionProjection() === 'mercator');
// Il client NON fa trim: '  stereographic ' non e' riconosciuto. Per questo chi
// scrive la timeline normalizza (strip + lower) prima di pubblicare.
ok('stringa con spazi -> NON riconosciuta (il client non fa trim)',
   makeCtx({ timeline: { projection: '  stereographic ' } }).fmRegionProjection() === 'mercator');
ok('stringa gia\' normalizzata (strip+lower) -> stereographic',
   makeCtx({ timeline: { projection: '  StereoGraphic '.trim().toLowerCase() } })
     .fmRegionProjection() === 'stereographic');

console.log('--- controprova del difetto: chiave di REGIONE su uno stato misto ---');
// Se la timeline pubblicasse `projection` a livello regione mentre dwd_icon e'
// ancora una striscia, il client la applicherebbe a OGNI dominio: e' il difetto
// per cui la chiave va OMESSA (fix 09f8158b). Qui si mostra la decisione.
ok('regione stereographic + dwd_icon striscia -> il client dice stereographic (perche\' la chiave va OMESSA)',
   makeCtx({ timeline: { projection: 'stereographic' }, domain: 'dwd_icon', index: { domains: { dwd_icon: {} } } })
     .fmRegionProjection() === 'stereographic');

console.log(failures ? `\nFATAL: ${failures} controlli falliti` : '\nOK: tutti i controlli PASS');
process.exit(failures ? 1 : 0);
