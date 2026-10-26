#!/usr/bin/env node
// TEST 1.2.4.16 - ZONE A RISCHIO E MAPPE PREVISIONALI (stato PER LAYER).
//
// COSA COPRE DAVVERO: la funzione di decisione fmRiskOverlayPlan e' ESTRATTA dall'html di
// release ed eseguita su layer finti con un predicato di visibilita' controllato. Verifica
// che: (i) venga registrato lo stato di OGNI layer (non un booleano globale); (ii) si
// nascondano SOLO i layer visibili; (iii) un layer gia' spento resti spento al ripristino;
// (iv) il wiring in toggleForecastMaps chiami hide all'apertura e restore alla chiusura.
//
// LIMITI DICHIARATI: non tocca Leaflet (niente mappa/DOM): verifica la DECISIONE e il
// wiring statico, non il rendering. Il comportamento sul canvas reale e' stato misurato a
// parte sul sito live. MISURA 1.2.4.25 (Edge headless via CDP): il riempimento colorato
// (provinceGeoJsonLayer) era GIA' nascosto; il layer ancora visibile sopra le mappe era
// borderOverlayLayer (107 path bianchi, pane borderOverlayPane z 410), che la 1.2.4.16
// lasciava fuori di proposito. Dalla 1.2.4.25 anche borderOverlayLayer e' nella lista,
// mentre fmCountryBordersLayer e fmCoastlinesLayer restano la geografia di riferimento.
'use strict';
import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const htmls = fs.readdirSync(ROOT).filter((f) => /^mri-light-1\.2\.4\.38\.html$/.test(f));
if (!htmls.length) { console.error('FATAL: mri-light-1.2.4.38.html non trovato in ' + ROOT); process.exit(1); }
const src = fs.readFileSync(path.join(ROOT, htmls[0]), 'utf8');

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

let planSrc;
try { planSrc = extractFn(src, 'fmRiskOverlayPlan'); } catch (e) { console.error('FATAL: ' + e.message); process.exit(1); }
const sandbox = { console };
vm.createContext(sandbox);
vm.runInContext(planSrc + '\n', sandbox);
const fmRiskOverlayPlan = sandbox.fmRiskOverlayPlan;
ok('E1: fmRiskOverlayPlan estratta ed eseguibile', typeof fmRiskOverlayPlan === 'function');

// ---------- CASO 1: provincia + sfumatura continua visibili, un layer gia' spento ----------
const L = {
  provinces: { id: 'provinces' },
  continuous: { id: 'continuous' },
  provinceTO: { id: 'province:TO' },
  alreadyOff: { id: 'alreadyOff' }
};
const visible = new Set([L.provinces, L.continuous, L.provinceTO]);
const candidates = [
  { layer: L.continuous, name: 'continuous' },
  { layer: L.provinces, name: 'provinces' },
  { layer: L.provinceTO, name: 'province:TO' },
  { layer: L.alreadyOff, name: 'alreadyOff' }
];
const plan = fmRiskOverlayPlan(candidates, (l) => visible.has(l));
ok('P1: lo stato e\' registrato PER LAYER (4 voci, non un booleano globale)',
  Array.isArray(plan.state) && plan.state.length === 4, `state=${plan.state.length}`);
ok('P2: nasconde esattamente i 3 layer visibili', plan.hide.length === 3 && !plan.hide.includes(L.alreadyOff),
  `hide=${plan.hide.map((l) => l.id).join(',')}`);
ok('P3: conserva il nome di ogni layer (ripristino diagnostico)',
  plan.state.map((s) => s.name).join('|') === 'continuous|provinces|province:TO|alreadyOff');
ok('P4: lo stato ricorda i visibili e i non visibili',
  plan.state.filter((s) => s.visible).length === 3 && plan.state.filter((s) => !s.visible).length === 1);

// ---------- CASO 2: l'utente aveva GIA' spento tutto -> al ripristino resta spento ----------
const allOff = fmRiskOverlayPlan(candidates, () => false);
ok('P5: con le zone a rischio gia\' spente non si nasconde nulla', allOff.hide.length === 0);
ok('P6: e lo stato salvato dice "tutto spento" (nessun layer da riaccendere)',
  allOff.state.every((s) => s.visible === false));

// ---------- CASO 3: elenco vuoto (mappe aperte prima del caricamento province) ----------
const empty = fmRiskOverlayPlan([], () => true);
ok('P7: nessun candidato -> piano vuoto, nessuna eccezione', empty.hide.length === 0 && empty.state.length === 0);
ok('P8: input nullo gestito', fmRiskOverlayPlan(null, () => true).state.length === 0);

// ---------- CASO 4: il piano non deduplica (lo fa fmRiskOverlayCandidates a monte) ----------
const dup = fmRiskOverlayPlan([{ layer: L.provinces, name: 'a' }, { layer: L.provinces, name: 'b' }], () => true);
ok('P9: con lo stesso layer due volte il piano resta coerente (2 voci, 1 layer distinto)',
  dup.hide.length === 2 && new Set(dup.hide).size === 1);

// ---------- WIRING STATICO ----------
const strip = (s) => s.replace(/\/\/[^\r\n]*/g, '');
const toggleSrc = extractFn(src, 'toggleForecastMaps');
ok('W1: toggleForecastMaps chiama fmHideRiskOverlays() all\'apertura',
  /fmHideRiskOverlays\(\)/.test(strip(toggleSrc)));
ok('W2: toggleForecastMaps chiama fmRestoreRiskOverlays() alla chiusura',
  /fmRestoreRiskOverlays\(\)/.test(strip(toggleSrc)));
ok('W3: lo stato e\' un array di voci (fmRiskOverlaySaved), non un booleano',
  /var fmRiskOverlaySaved = null;/.test(src) && /fmRiskOverlaySaved = plan\.state;/.test(src));
ok('W4: fmRiskOverlayCandidates include provinceGeoJsonLayer (rischio + colorazione DPC)',
  /provinceGeoJsonLayer, 'provinces'/.test(src));
ok('W5: fmRiskOverlayCandidates include la sfumatura della vista continua (continuousOverlay)',
  /continuousOverlay, 'continuous'/.test(src));
ok('W6: fmRiskOverlayCandidates include le voci di provinceLayerMap',
  /provinceLayerMap\[k\], 'province:' \+ k/.test(src));
ok('W7: i bordi provinciali (rete bianca ISTAT) vengono nascosti con le mappe',
  /borderOverlayLayer, 'borders'/.test(src));
ok('W7b: i confini geografici di base (Natural Earth + coste) NON sono nella lista da nascondere',
  !/fmCountryBordersLayer|fmCoastlinesLayer/.test(extractFn(src, 'fmRiskOverlayCandidates')));
ok('W8: il ripristino salta i layer che erano spenti (if (!saved[i].visible) continue)',
  /if \(!saved\[i\]\.visible\) continue;/.test(src));
ok('W9: nessuna doppia registrazione se hide viene chiamata due volte',
  /if \(fmRiskOverlaySaved\) return;/.test(src));

console.log(failures === 0 ? '\nRESULT: PASS' : `\nRESULT: FAIL (${failures})`);
process.exit(failures === 0 ? 0 : 1);
