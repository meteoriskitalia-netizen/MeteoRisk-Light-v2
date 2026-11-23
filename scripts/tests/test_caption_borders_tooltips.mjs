#!/usr/bin/env node
// TEST 1.3.0.55 - CAPTION SENZA BLITZORTUNG, BORDI SOLO NEL PRODOTTO RISCHI,
// TOOLTIP COMPLETI, MASTER TOGGLE DELLE STRUTTURE CONVETTIVE.
//
// COSA COPRE DAVVERO:
//   A) updateSyncCaption: nessun ramo nomina il marchio della sorgente fulmini;
//      eseguita in vm con radar DPC + satellite e ZERO sorgenti fulmini
//      abilitate, la didascalia e' "Radar DPC ... + EUMETSAT ... (slot 5 min)"
//      sia con isLightningActive=false sia con il flag stale a true (A2).
//   B) restyleBorderOverlay: il ramo !isRiskProductActive() esiste ed e' DOPO
//      isSelected e PRIMA di continuousMode (la provincia selezionata resta
//      azzurra, fuori dal prodotto la rete bianca sparisce).
//   C) I 23 elementi (18 righe della tabella) hanno l'attributo title, e nessun
//      tooltip contiene "allerta", "probabilità"/"probabilita'", "supercella".
//   E) enableLinkedScProducts esiste, accende i tre flag PRIMA del fetch e viene
//      chiamata in toggleLiveSupercells prima di refreshLiveSupercells.
//
// LIMITI DICHIARATI: e' un emulatore Node, non un browser. La caption e'
// valutata su stub (non su Leaflet); restyleBorderOverlay e' verificata come
// TESTO (ordine dei rami), non eseguita su layer reali; i title sono letti come
// markup statico, non come tooltip renderizzati dal browser.
'use strict';

import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const VERSION_SRC = fs.readFileSync(path.join(ROOT, 'VERSION'), 'utf8');
const APP_FILE = ((VERSION_SRC.match(/^APP_FILE=(.+)$/m) || [])[1] || '').trim();
if (!APP_FILE || !fs.existsSync(path.join(ROOT, APP_FILE))) {
  console.error('FATAL: APP_FILE di VERSION non trovato: ' + JSON.stringify(APP_FILE));
  process.exit(1);
}
const src = fs.readFileSync(path.join(ROOT, APP_FILE), 'utf8');
console.log('html misurato: ' + APP_FILE);

let failures = 0;
function ok(label, cond, detail = '') {
  console.log(`  [${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? ' -- ' + detail : ''}`);
  if (!cond) failures++;
}

// Estrae una funzione per nome contando le graffe e saltando stringhe,
// template literal, regex e commenti.
function extractFn(source, name) {
  const start = source.indexOf('function ' + name + '(');
  if (start < 0) throw new Error('funzione ' + name + ' non trovata');
  let i = source.indexOf('{', start);
  if (i < 0) throw new Error('corpo di ' + name + ' non trovato');
  let depth = 0, mode = null, quote = null;
  for (; i < source.length; i++) {
    const c = source[i], n = source[i + 1];
    if (mode === 'line') { if (c === '\n') mode = null; continue; }
    if (mode === 'block') { if (c === '*' && n === '/') { mode = null; i++; } continue; }
    if (mode === 'str') {
      if (c === '\\') { i++; continue; }
      if (c === quote) mode = null;
      continue;
    }
    if (c === '/' && n === '/') { mode = 'line'; i++; continue; }
    if (c === '/' && n === '*') { mode = 'block'; i++; continue; }
    if (c === "'" || c === '"' || c === '`') { mode = 'str'; quote = c; continue; }
    if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) return source.slice(start, i + 1); }
  }
  throw new Error('chiusura di ' + name + ' non trovata');
}

// ---------------------------------------------------------------- A) caption
const captionSrc = extractFn(src, 'updateSyncCaption');
ok('A: updateSyncCaption non nomina il marchio Blitzortung', captionSrc.indexOf('Blitzortung') < 0);
ok('A: updateSyncCaption ha il gate sulle sorgenti abilitate (A2)',
  /isLightningActive\s*&&\s*enabledLightningSourceIds\(\)\.length\s*>\s*0/.test(captionSrc));

function runCaption(ctxOverrides) {
  const el = { innerText: '' };
  const ctx = Object.assign({
    document: { getElementById: () => el },
    isRadarActive: true,
    isSatelliteActive: true,
    isLightningActive: false,
    lightningSource: '',
    dpcProductType: 'VMI',
    getRadarProvider: () => ({ id: 'dpc' }),
    eumetsatLayerMap: { eumetsat_italy: { label: 'EUMETSAT IR Italia' } },
    satSource: 'eumetsat_italy',
    enabledLightningSourceIds: () => [],
  }, ctxOverrides);
  const sandbox = vm.createContext(ctx);
  vm.runInContext(captionSrc + '\nupdateSyncCaption();', sandbox);
  return el.innerText;
}
const cap1 = runCaption({});
ok('A: caption = radar DPC + satellite, slot 5 min, senza Blitzortung',
  cap1.indexOf('Radar DPC') >= 0 && cap1.indexOf('EUMETSAT IR Italia') >= 0 &&
  cap1.indexOf('slot 5 min') >= 0 && cap1.indexOf('Blitzortung') < 0, cap1);
const cap2 = runCaption({ isLightningActive: true, lightningSource: 'blitzortung' });
ok('A: con flag stale e zero sorgenti abilitate la caption resta pulita',
  cap2.indexOf('Blitzortung') < 0 && cap2.indexOf('Fulmini') < 0, cap2);

// ---------------------------------------------------------------- B) bordi
const borderSrc = extractFn(src, 'restyleBorderOverlay');
const iSel = borderSrc.indexOf('if (isSelected)');
const iRisk = borderSrc.indexOf('!riskLayerPaintAllowed()');
const iCont = borderSrc.indexOf('else if (continuousMode)');
ok('B: restyleBorderOverlay contiene !riskLayerPaintAllowed() (prodotto rischi O override Province)', iRisk >= 0);
ok('B: il ramo sta DOPO isSelected', iSel >= 0 && iRisk > iSel, `isSelected=${iSel} risk=${iRisk}`);
ok('B: il ramo sta PRIMA di continuousMode', iCont >= 0 && iRisk < iCont, `risk=${iRisk} continuous=${iCont}`);
ok('B: il ramo nasconde senza removeLayer',
  /layer\.setStyle\(\{\s*fill:\s*false,\s*weight:\s*0,\s*opacity:\s*0\s*\}\)/.test(borderSrc) &&
  borderSrc.indexOf('removeLayer') < 0);
ok('B: updateMapColors chiama restyleBorderOverlay fuori dal prodotto',
  /if \(!riskLayerPaintAllowed\(\)\) \{[\s\S]{0,400}?restyleBorderOverlay\(\);/.test(extractFn(src, 'updateMapColors')));

// ---------------------------------------------------------------- C) tooltip
const TOOLTIPS = {
  'btn-d0': 'Giorno della mappa dei rischi: Oggi',
  'btn-d1': 'Giorno della mappa dei rischi: Domani',
  'btn-d2': 'Giorno della mappa dei rischi: Dopodomani',
  'btn-forecast-maps': 'Mappe previsionali multi-modello (7 domini, 31 variabili): apre il pannello con timeline e selettore run',
  'btn-radar-player': 'Radar Player: timeline sincronizzata radar DPC + satellite EUMETSAT sugli ultimi 2 h (slot 5 min)',
  'btn-forecast': 'Previsioni della località selezionata: apre fascia oraria e marker e nasconde i poligoni delle province',
  'btn-caletta': 'Previsioni per La Caletta (Anguillara Sabazia) a pranzo o cena',
  'btn-live': 'Pannello LIVE: satellite e radar in tempo reale con struttura convettiva, centraline e area Doppler',
  'btn-sources': 'Fonti e metodologie: elenco delle fonti dati, licenze e delle soglie usate, con riferimenti bibliografici',
  'btn-radar': 'Radar Timeline: mostra i frame radar DPC sulla mappa, indipendente da Radar Player',
  'btn-satellite': 'Satellite EUMETSAT: mostra i frame satellitari sulla mappa, indipendente da Radar Player',
  'sync-play-btn': "Avvia/ferma l'animazione della timeline sincronizzata",
  'fm-play-btn': "Avvia/ferma l'animazione delle mappe previsionali",
  'metric-tab-thermalrisk': 'Metrica della mappa dei rischi: rischio termico (caldo/gelo per fascia altimetrica)',
  'metric-tab-conv': 'Metrica della mappa dei rischi: convezione organizzata (potenziale del modello NWP)',
  'metric-tab-hail': 'Metrica della mappa dei rischi: grandine (potenziale del modello NWP)',
  'caletta-btn-pranzo': "Mostra la previsione per l'ora di pranzo",
  'caletta-btn-cena': "Mostra la previsione per l'ora di cena",
  'fc-tab-provincia': 'Previsione della provincia selezionata',
  'fc-tab-aree': 'Sintesi per aree',
  'fc-tab-settimana': 'Previsione a 7 giorni',
  'mc-retry': 'Riprova a caricare il confronto fra modelli',
  'help-close': 'Chiudi la guida',
};
const ids = Object.keys(TOOLTIPS);
let withTitle = 0;
for (const id of ids) {
  const re = new RegExp('<[^>]*id="' + id + '"[^>]*>');
  const m = src.match(re);
  if (!m) { ok('C: #' + id + ' presente', false, 'tag non trovato'); continue; }
  const tag = m[0];
  const tm = tag.match(/title="([^"]*)"/);
  if (tm && tm[1] === TOOLTIPS[id]) withTitle++;
  else ok('C: #' + id + ' title esatto', false, tm ? 'testo diverso: ' + tm[1] : 'title assente');
}
ok('C: ' + ids.length + ' id con title esatto', withTitle === ids.length, withTitle + '/' + ids.length);
const titles = Object.values(TOOLTIPS).join(' ').toLowerCase();
ok('C: nessun tooltip usa "allerta"', titles.indexOf('allerta') < 0);
ok('C: nessun tooltip usa "probabilità"/"probabilita"',
  titles.indexOf('probabilità') < 0 && titles.indexOf('probabilita') < 0);
ok('C: nessun tooltip usa "supercella"', titles.indexOf('supercella') < 0);

// ---------------------------------------------------------------- E) master
const masterSrc = extractFn(src, 'enableLinkedScProducts');
ok('E: enableLinkedScProducts esiste', masterSrc.length > 0);
ok('E: accende trails/forecast/phenomena con scSetToggleBtn',
  masterSrc.indexOf('scShowTrails = true') >= 0 &&
  masterSrc.indexOf('scShowForecast = true') >= 0 &&
  masterSrc.indexOf('scShowPhenomena = true') >= 0 &&
  masterSrc.indexOf("scSetToggleBtn('sc-toggle-trails', true)") >= 0 &&
  masterSrc.indexOf("scSetToggleBtn('sc-toggle-forecast', true)") >= 0 &&
  masterSrc.indexOf("scSetToggleBtn('sc-toggle-phenomena', true)") >= 0);
ok('E: non spegne nulla in cascata', !/=\s*false/.test(masterSrc));
const liveToggle = extractFn(src, 'toggleLiveSupercells');
const iEnable = liveToggle.indexOf('enableLinkedScProducts()');
const iRefresh = liveToggle.indexOf('refreshLiveSupercells()');
ok('E: chiamata in toggleLiveSupercells', iEnable >= 0);
ok('E: chiamata PRIMA di refreshLiveSupercells', iEnable >= 0 && iRefresh > iEnable,
  `enable=${iEnable} refresh=${iRefresh}`);
ok('E: la chiamata sta nel ramo ON', liveToggle.slice(0, iEnable).lastIndexOf('if (liveShowSupercells)') > -1);

console.log(failures === 0 ? '\nTUTTI I CONTROLLI PASS' : `\n${failures} CONTROLLI FALLITI`);
process.exit(failures === 0 ? 0 : 1);
