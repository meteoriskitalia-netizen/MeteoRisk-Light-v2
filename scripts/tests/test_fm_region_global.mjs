#!/usr/bin/env node
// TEST 1.2.4.15 - REGIONE EFFETTIVA PER DOMINIO (i 4 domini globali usano SOLO l'emisfero nord).
//
// COSA COPRE DAVVERO: estrae dall'html di release le funzioni reali
// fmRegionForDomain / fmDomainRegionIds / fmRenderRegionId e la costante
// FM_GLOBAL_DOMAINS, e le esegue in un contesto vm cambiando fmDomain/fmRegion.
// fmRenderRegionId e' la funzione che il client usa per costruire sia l'URL di
// index.json sia quello dei WebP, quindi il test copre il punto di decisione reale.
//
// LIMITI DICHIARATI: non fa rete (nessuna richiesta HTTP a fm2-rendered) ne' DOM
// (il <select> non viene renderizzato): verifica la decisione di regione, non il
// layout del selettore.
'use strict';

import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const HTML = path.join(ROOT, 'mri-light-1.2.4.23.html');
if (!fs.existsSync(HTML)) { console.error('FATAL: mri-light-1.2.4.23.html non trovato'); process.exit(1); }
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
  // le due costanti sono su una sola riga; il file puo' essere CRLF -> si taglia a fine riga
  const re = new RegExp('var ' + name + ' = [^\\r\\n]*;');
  const m = src.match(re);
  if (!m) throw new Error('var ' + name + ' non trovata');
  return m[0];
}

const sandbox = { console, fmDomain: 'ncep_gfs025', fmRegion: 'domain' };
vm.createContext(sandbox);
let code = '';
try {
  code = [
    extractVarLine('FM_GLOBAL_DOMAINS'),
    extractVarLine('FM_RENDER_REGION_MAP'),
    extractFn('fmDomainRegionIds'),
    extractFn('fmRegionForDomain'),
    extractFn('fmRenderRegionId'),
  ].join('\n');
} catch (e) {
  console.error('FATAL: ' + e.message);
  process.exit(1);
}
vm.runInContext(code, sandbox);
const { fmRegionForDomain, fmDomainRegionIds } = sandbox;
ok('E1: funzioni e costanti estratte dall\'html reale',
  typeof fmRegionForDomain === 'function' && typeof fmDomainRegionIds === 'function' &&
  Array.isArray(sandbox.FM_GLOBAL_DOMAINS) && typeof sandbox.FM_RENDER_REGION_MAP === 'object');

const GLOBALI = ['dwd_icon', 'ncep_gfs025', 'ecmwf_ifs025', 'meteofrance_arpege_world025'];
const REGIONALI = ['dwd_icon_eu', 'dwd_icon_d2', 'ncep_gfs013'];

// ---------- A. DOMINI GLOBALI -> north, SEMPRE ----------
ok('A1: FM_GLOBAL_DOMAINS contiene esattamente i 4 domini globali',
  GLOBALI.every((d) => sandbox.FM_GLOBAL_DOMAINS.indexOf(d) >= 0) && sandbox.FM_GLOBAL_DOMAINS.length === 4,
  JSON.stringify(sandbox.FM_GLOBAL_DOMAINS));
ok('A2: fmRegionForDomain(\'ncep_gfs025\', \'domain\') === \'north\'',
  fmRegionForDomain('ncep_gfs025', 'domain') === 'north');
for (const d of GLOBALI) {
  const got = ['domain', 'europe', 'italy', 'natl', 'north', '', null].map((r) => fmRegionForDomain(d, r));
  ok(`A3: ${d} chiede SEMPRE north qualunque sia la regione UI`,
    got.every((v) => v === 'north'), JSON.stringify(got));
}
ok('A4: per i globali la regione \'domain\' non e\' piu\' ammessa dal selettore',
  fmDomainRegionIds('ncep_gfs025').indexOf('domain') < 0 && fmDomainRegionIds('dwd_icon').indexOf('domain') < 0);
ok('A5: per i globali il selettore parte da \'north\' (Emisfero nord)',
  fmDomainRegionIds('ncep_gfs025')[0] === 'north' && fmDomainRegionIds('ecmwf_ifs025')[0] === 'north');

// ---------- B. DOMINI REGIONALI INVARIATI ----------
ok('B1: fmRegionForDomain(\'dwd_icon_eu\', \'domain\') === \'domain\'',
  fmRegionForDomain('dwd_icon_eu', 'domain') === 'domain');
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
ok('B6: regione non ammessa -> ricade sulla prima regione ammessa del dominio',
  fmRegionForDomain('dwd_icon_eu', 'arctic') === 'domain');

// ---------- C. IL PUNTO CHE COSTRUISCE GLI URL (fmRenderRegionId) ----------
function renderRegion(domain, region) {
  sandbox.fmDomain = domain; sandbox.fmRegion = region;
  return vm.runInContext('fmRenderRegionId()', sandbox);
}
ok('C1: ncep_gfs025 + selettore \'domain\' -> indice/WebP \'north\'', renderRegion('ncep_gfs025', 'domain') === 'north');
ok('C2: dwd_icon + selettore \'domain\' -> \'north\'', renderRegion('dwd_icon', 'domain') === 'north');
ok('C3: ecmwf_ifs025 + selettore \'europe\' -> \'north\' (la regione dati non cambia)',
  renderRegion('ecmwf_ifs025', 'europe') === 'north');
ok('C4: meteofrance_arpege_world025 + selettore \'italy\' -> \'north\'',
  renderRegion('meteofrance_arpege_world025', 'italy') === 'north');
ok('C5: dwd_icon_eu + \'domain\' -> \'domain\'', renderRegion('dwd_icon_eu', 'domain') === 'domain');
ok('C6: dwd_icon_eu + \'italy\' -> \'italia\' (mappa regione UI -> regione servita)',
  renderRegion('dwd_icon_eu', 'italy') === 'italia');
ok('C7: dwd_icon_eu + \'europe\' -> \'europa\'', renderRegion('dwd_icon_eu', 'europe') === 'europa');
ok('C8: ncep_gfs013 + \'domain\' -> \'domain\'', renderRegion('ncep_gfs013', 'domain') === 'domain');
ok('C9: nessuna regione UI -> null (nessun URL costruito)',
  (sandbox.fmDomain = 'ncep_gfs025', sandbox.fmRegion = '', vm.runInContext('fmRenderRegionId()', sandbox)) === null);

// ---------- D. CONTROLLI STATICI: UNA SOLA LOGICA, NESSUN URL CON LA REGIONE UI ----------
ok('D1: nessun URL costruito con FM_RENDERED_BASE + fmRegion (regione UI grezza)',
  !/FM_RENDERED_BASE\s*\+\s*fmRegion\b/.test(src));
ok('D2: index.json costruito con la regione risolta (serverRegion)',
  /FM_RENDERED_BASE \+ serverRegion \+ '\/index\.json'/.test(src));
ok('D3: la regola dei globali e\' centralizzata in fmRegionForDomain (usata da fmRenderRegionId)',
  /function fmRenderRegionId\(\)\s*\{[\s\S]{0,400}?fmRegionForDomain\(fmDomain, fmRegion\)/.test(src));
ok('D4: fmPopulateRegions non reintroduce \'domain\' per i globali',
  /function fmPopulateRegions\(\)/.test(src) && /var allowed = fmDomainRegionIds\(fmDomain\);/.test(src));
ok('D5: il polling periodico usa la regione risolta (fmRenderRegionId)',
  /var renderRegion = fmRenderRegionId\(\);/.test(src));

console.log(failures === 0 ? '\nRESULT: PASS' : `\nRESULT: FAIL (${failures})`);
process.exit(failures === 0 ? 0 : 1);
