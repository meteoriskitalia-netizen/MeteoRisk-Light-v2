#!/usr/bin/env node
// TEST 1.2.4.25 - REGIONE EFFETTIVA E REGIONE INIZIALE PER DOMINIO.
//
// COSA COPRE DAVVERO: estrae dall'html di release le funzioni reali
// fmRegionDefaultForDomain / fmRegionForDomain / fmDomainRegionIds / fmRenderRegionId
// e le costanti FM_GLOBAL_DOMAINS / FM_REGIONS, e le esegue in un contesto vm
// cambiando fmDomain/fmRegion. fmRenderRegionId e' la funzione che il client usa per
// costruire sia l'URL di index.json/timeline.json sia quello dei WebP, quindi il test
// copre il punto di decisione reale.
//
// CONTRATTO 1.2.4.25: i 4 domini globali si aprono su 'europe' (prima: 'north') e la
// regione scelta dall'utente vale anche per loro; i 3 regionali partono da 'domain'.
// CONTRATTO 1.2.4.26: la regione 'natl' (Nord Atlantico) e' stata RIMOSSA da FM_REGIONS
// e da tutti gli elenchi per dominio (fmRenderRegionId restituiva null e /natl/ e' 404:
// selezionarla non avviava nessuna richiesta). Le altre regioni restano identiche.
//
// LIMITI DICHIARATI: non fa rete (nessuna richiesta HTTP a fm2-rendered) ne' DOM
// (il <select> non viene renderizzato): verifica la decisione di regione, non il
// layout del selettore. Il vincolo "solo al primo caricamento" e' controllato con
// asserzioni statiche sul codice (fmRegionUserChosen in fmRegionChanged e in
// fmLoadDomain) piu' la verifica in browser reale fatta a mano e riportata nel report;
// qui non viene simulato fmLoadDomain (dipende da DOM e rete).
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
function extractVarLine(name) {
  // le costanti sono su una sola riga; il file puo' essere CRLF -> si taglia a fine riga
  const re = new RegExp('var ' + name + ' = [^\\r\\n]*;');
  const m = src.match(re);
  if (!m) throw new Error('var ' + name + ' non trovata');
  return m[0];
}
function extractVarBlock(name) {
  // FM_REGIONS e' un array multiriga: si estrae dal 'var' alla prima riga con '];'
  const start = src.indexOf('var ' + name + ' = [');
  if (start < 0) throw new Error('var ' + name + ' non trovata');
  const end = src.indexOf('\n];', start);
  if (end < 0) throw new Error('chiusura di ' + name + ' non trovata');
  return src.slice(start, end + 3);
}

const sandbox = { console, fmDomain: 'ncep_gfs025', fmRegion: 'domain' };
vm.createContext(sandbox);
let code = '';
try {
  code = [
    extractVarLine('FM_GLOBAL_DOMAINS'),
    extractVarLine('FM_RENDER_REGION_MAP'),
    extractVarBlock('FM_REGIONS'),
    extractFn('fmDomainRegionIds'),
    extractFn('fmRegionDefaultForDomain'),
    extractFn('fmRegionForDomain'),
    extractFn('fmRenderRegionId'),
  ].join('\n');
} catch (e) {
  console.error('FATAL: ' + e.message);
  process.exit(1);
}
vm.runInContext(code, sandbox);
const { fmRegionDefaultForDomain, fmRegionForDomain, fmDomainRegionIds } = sandbox;
ok('E1: funzioni e costanti estratte dall\'html reale',
  typeof fmRegionDefaultForDomain === 'function' && typeof fmRegionForDomain === 'function' &&
  typeof fmDomainRegionIds === 'function' && Array.isArray(sandbox.FM_GLOBAL_DOMAINS) &&
  Array.isArray(sandbox.FM_REGIONS) && typeof sandbox.FM_RENDER_REGION_MAP === 'object');

const GLOBALI = ['dwd_icon', 'ncep_gfs025', 'ecmwf_ifs025', 'meteofrance_arpege_world025'];
const REGIONALI = ['dwd_icon_eu', 'dwd_icon_d2', 'ncep_gfs013'];

// ---------- A. DOMINI GLOBALI: default 'europe', scelta utente rispettata ----------
ok('A1: FM_GLOBAL_DOMAINS contiene esattamente i 4 domini globali',
  GLOBALI.every((d) => sandbox.FM_GLOBAL_DOMAINS.indexOf(d) >= 0) && sandbox.FM_GLOBAL_DOMAINS.length === 4,
  JSON.stringify(sandbox.FM_GLOBAL_DOMAINS));
for (const d of GLOBALI) {
  ok(`A2: ${d} ha come regione iniziale 'europe'`,
    fmRegionDefaultForDomain(d) === 'europe', fmRegionDefaultForDomain(d));
}
ok('A3: per i globali \'domain\' (e i valori non validi) ricadono su \'europe\'',
  ['domain', '', null, undefined, 'arctic'].every((r) => fmRegionForDomain('ncep_gfs025', r) === 'europe'),
  JSON.stringify(['domain', '', null, undefined, 'arctic'].map((r) => fmRegionForDomain('ncep_gfs025', r))));
for (const d of GLOBALI) {
  const got = ['europe', 'italy', 'north'].map((r) => fmRegionForDomain(d, r));
  ok(`A4: ${d} rispetta la regione scelta (europe/italy/north)`,
    JSON.stringify(got) === JSON.stringify(['europe', 'italy', 'north']), JSON.stringify(got));
}
ok('A5: per i globali la regione \'domain\' non e\' ammessa dal selettore',
  GLOBALI.every((d) => fmDomainRegionIds(d).indexOf('domain') < 0));
ok('A6: 1.2.4.26 - i globali espongono north/europe/italy e NON piu\' natl',
  GLOBALI.every((d) => JSON.stringify(fmDomainRegionIds(d)) === JSON.stringify(['north', 'europe', 'italy'])),
  JSON.stringify(fmDomainRegionIds('ncep_gfs025')));

// ---------- B. DOMINI REGIONALI: default 'domain', invariati ----------
for (const d of REGIONALI) {
  ok(`B1: ${d} ha come regione iniziale 'domain'`,
    fmRegionDefaultForDomain(d) === 'domain' && fmRegionForDomain(d, 'domain') === 'domain');
}
ok('B2: dwd_icon_eu resta su domain/europe/italy',
  JSON.stringify(fmDomainRegionIds('dwd_icon_eu')) === JSON.stringify(['domain', 'europe', 'italy']));
ok('B3: dwd_icon_d2 resta su domain/europe/italy',
  JSON.stringify(fmDomainRegionIds('dwd_icon_d2')) === JSON.stringify(['domain', 'europe', 'italy']));
ok('B4: ncep_gfs013 resta su domain/europe/italy',
  JSON.stringify(fmDomainRegionIds('ncep_gfs013')) === JSON.stringify(['domain', 'europe', 'italy']));
ok('B5: per i regionali la regione scelta dall\'utente non viene forzata',
  fmRegionForDomain('dwd_icon_eu', 'europe') === 'europe' &&
  fmRegionForDomain('dwd_icon_d2', 'italy') === 'italy' &&
  fmRegionForDomain('ncep_gfs013', 'domain') === 'domain');
ok('B6: regione non ammessa -> default del dominio (regionali: domain)',
  fmRegionForDomain('dwd_icon_eu', 'arctic') === 'domain');

// ---------- C. IL PUNTO CHE COSTRUISCE GLI URL (fmRenderRegionId) ----------
function renderRegion(domain, region) {
  sandbox.fmDomain = domain; sandbox.fmRegion = region;
  return vm.runInContext('fmRenderRegionId()', sandbox);
}
ok('C1: ncep_gfs025 + selettore \'domain\' -> indice/WebP \'europa\' (default)',
  renderRegion('ncep_gfs025', 'domain') === 'europa');
ok('C2: dwd_icon + selettore \'domain\' -> \'europa\'', renderRegion('dwd_icon', 'domain') === 'europa');
ok('C3: ecmwf_ifs025 + selettore \'europe\' -> \'europa\'',
  renderRegion('ecmwf_ifs025', 'europe') === 'europa');
ok('C4: meteofrance_arpege_world025 + selettore \'italy\' -> \'italia\'',
  renderRegion('meteofrance_arpege_world025', 'italy') === 'italia');
ok('C5: ncep_gfs025 + selettore \'north\' -> \'north\' (scelta esplicita rispettata)',
  renderRegion('ncep_gfs025', 'north') === 'north');
ok('C6: dwd_icon_eu + \'domain\' -> \'domain\'', renderRegion('dwd_icon_eu', 'domain') === 'domain');
ok('C7: dwd_icon_eu + \'italy\' -> \'italia\' (mappa regione UI -> regione servita)',
  renderRegion('dwd_icon_eu', 'italy') === 'italia');
ok('C8: dwd_icon_eu + \'europe\' -> \'europa\'', renderRegion('dwd_icon_eu', 'europe') === 'europa');
ok('C9: ncep_gfs013 + \'domain\' -> \'domain\'', renderRegion('ncep_gfs013', 'domain') === 'domain');
ok('C10: nessuna regione UI -> null (nessun URL costruito)',
  (sandbox.fmDomain = 'ncep_gfs025', sandbox.fmRegion = '', vm.runInContext('fmRenderRegionId()', sandbox)) === null);

// ---------- D. CONTROLLI STATICI: UNA SOLA LOGICA, NESSUN URL CON LA REGIONE UI ----------
ok('D1: nessun URL costruito con FM_RENDERED_BASE + fmRegion (regione UI grezza)',
  !/FM_RENDERED_BASE\s*\+\s*fmRegion\b/.test(src));
ok('D2: index.json costruito con la regione risolta (serverRegion)',
  /FM_RENDERED_BASE \+ serverRegion \+ '\/index\.json'/.test(src));
ok('D3: la regola centralizzata usa fmRegionForDomain (fmRenderRegionId)',
  /function fmRenderRegionId\(\)\s*\{[\s\S]{0,500}?fmRegionForDomain\(fmDomain, fmRegion\)/.test(src));
ok('D4: fmPopulateRegions ricade sul default del dominio, non su allowed[0]',
  /if \(allowed\.indexOf\(fmRegion\) < 0\) fmRegion = fmRegionDefaultForDomain\(fmDomain\);/.test(src));
ok('D5: la scelta manuale alza fmRegionUserChosen',
  /function fmRegionChanged\(\)\s*\{[\s\S]{0,300}?fmRegionUserChosen = true;/.test(src));
ok('D6: il default si applica SOLO se l\'utente non ha scelto (fmLoadDomain)',
  /if \(!fmRegionUserChosen\) \{\s*\n\s*var defaultRegion = fmRegionDefaultForDomain\(fmDomain\);/.test(src));
// 1.2.4.57: la risoluzione della regione non e' piu' una riga del polling ma la PRIMA
// istruzione della funzione di rivalidazione condivisa (fmRevalidateRendered), che il
// polling richiama. L'asserzione vecchia cercava `var renderRegion = fmRenderRegionId();`
// nel polling: sostituita (non allentata) perche' il comportamento e' lo stesso e ora
// c'e' UNA sola strada, non due copie.
ok('D7: il polling periodico usa la regione risolta (via fmRevalidateRendered)',
  /await fmRevalidateRendered\(\{ allowCold: true, reason: 'poll' \}\)/.test(src) &&
  /async function fmRevalidateRendered\(opts\) \{\s*\n\s*opts = opts \|\| \{\};\s*\n\s*var region = fmRenderRegionId\(\);/.test(src));

// ---------- E. FM_REGIONS: 'natl' RIMOSSO (1.2.4.26) ----------
const regionIds = sandbox.FM_REGIONS.map((r) => r.id);
ok('E2: FM_REGIONS conserva 11 regioni e NON ha piu\' natl',
  JSON.stringify(regionIds) === JSON.stringify(['domain', 'north', 'europe', 'med', 'italy', 'namerica', 'samerica', 'africa', 'asia', 'oceania', 'arctic']),
  JSON.stringify(regionIds));
ok('E3: 1.2.4.26 - nessun dominio ammette natl (voce morta: fmRenderRegionId era null)',
  [...GLOBALI, ...REGIONALI].every((d) => fmDomainRegionIds(d).indexOf('natl') < 0));

console.log(failures === 0 ? '\nRESULT: PASS' : `\nRESULT: FAIL (${failures})`);
process.exit(failures === 0 ? 0 : 1);
