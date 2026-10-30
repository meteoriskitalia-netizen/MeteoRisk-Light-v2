#!/usr/bin/env node
// TEST 1.2.4.16 - RESET DEL PLAYER AL CAMBIO MODELLO/VARIABILE/RUN.
//
// COSA COPRE DAVVERO: la funzione PURA fmPlayerResetState e' ESTRATTA dall'html di release
// ed eseguita su stati di partenza diversi (fermo, in riproduzione, con frame in volo).
// Verifica che il nuovo stato sia sempre: non in riproduzione, timer azzerato, indice 0,
// slider '0', bottone play, nessun frame pending, generation incrementata (invalida i
// callback onload del modello precedente), loader attivo, contatore azzerato.
// Verifica inoltre il WIRING: i tre handler (fmDomainChanged, fmVariableChanged,
// fmRunChanged) chiamano fmResetPlayerForModelChange e che questa faccia davvero stop di
// play/preload/warm, rimozione layer e svuotamento della cache di decodifica.
//
// LIMITI DICHIARATI: non c'e' DOM/browser nel test: verifica lo STATO calcolato e il wiring
// statico, non che il canvas si ridisegni. La verifica in browser (network per 10 s dopo il
// cambio modello, slider a '0', play fermo) e' fatta a parte sul sito.
'use strict';
import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const htmls = fs.readdirSync(ROOT).filter((f) => /^mri-light-1.2.4.60\.html$/.test(f));
if (!htmls.length) { console.error('FATAL: mri-light-1.2.4.60.html non trovato in ' + ROOT); process.exit(1); }
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

let resetSrc;
try { resetSrc = extractFn(src, 'fmPlayerResetState'); } catch (e) { console.error('FATAL: ' + e.message); process.exit(1); }
const sandbox = { console };
vm.createContext(sandbox);
vm.runInContext(resetSrc + '\n', sandbox);
const fmPlayerResetState = sandbox.fmPlayerResetState;
ok('E1: fmPlayerResetState estratta ed eseguibile', typeof fmPlayerResetState === 'function');

// ---------- CASO 1: player a meta' animazione ----------
const mid = fmPlayerResetState({ isPlaying: true, idx: 57, generation: 42, framePending: true });
ok('R1: riproduzione ferma', mid.playing === false);
ok('R2: timer azzerato', mid.interval === null);
ok('R3: indice riportato a 0 (primo frame del nuovo modello)', mid.idx === 0);
ok('R4: slider esattamente "0" (stringa, come fm-slider.value)', mid.slider === '0');
ok('R5: bottone del player su play', mid.playButton === '▶');
ok('R6: nessun frame considerato in volo', mid.framePending === false);
ok('R7: generation incrementata (invalida i callback onload del modello precedente)',
  mid.generation === 43, `generation=${mid.generation}`);
ok('R8: loader attivo (stato di caricamento, non un frame vecchio)', mid.loader === true);
ok('R9: contatore/data azzerati', mid.timeLabel === '—');

// ---------- CASO 2: player gia' fermo ----------
const still = fmPlayerResetState({ isPlaying: false, idx: 0, generation: 7 });
ok('R10: da fermo il risultato e\' identico (idempotente sullo stato visibile)',
  still.playing === false && still.idx === 0 && still.slider === '0' && still.playButton === '▶');
ok('R11: la generation avanza comunque (un frame in volo va invalidato lo stesso)',
  still.generation === 8);

// ---------- CASO 3: stato assente / generation non numerica ----------
const none = fmPlayerResetState();
ok('R12: senza stato precedente non lancia e riparte da 0',
  none.idx === 0 && none.slider === '0' && none.playing === false);
const bad = fmPlayerResetState({ generation: 'x' });
ok('R13: generation non numerica -> parte da 1', bad.generation === 1, `generation=${bad.generation}`);
const undef = fmPlayerResetState({ generation: undefined });
ok('R14: generation undefined -> parte da 1', undef.generation === 1);

// ---------- WIRING STATICO ----------
const strip = (s) => s.replace(/\/\/[^\r\n]*/g, '');
ok('W1: fmDomainChanged chiama fmResetPlayerForModelChange', /fmResetPlayerForModelChange\('domain'\)/.test(src));
ok('W2: fmVariableChanged chiama fmResetPlayerForModelChange', /fmResetPlayerForModelChange\('variable'\)/.test(src));
ok('W3: fmRunChanged chiama fmResetPlayerForModelChange', /fmResetPlayerForModelChange\('run'\)/.test(src));
const resetFn = extractFn(src, 'fmResetPlayerForModelChange');
const resetBody = strip(resetFn);
ok('W4: il reset ferma la riproduzione (fmStopPlay)', /fmStopPlay\(\)/.test(resetBody));
ok('W5: il reset ferma il preload e il warm-ahead del modello precedente',
  /fmStopPreload\(\)/.test(resetBody) && /fmStopWarm\(\)/.test(resetBody));
ok('W6: il reset toglie i layer (nessun frame vecchio a schermo)', /fmRemoveLayers\(\)/.test(resetBody));
ok('W7: il reset svuota la cache delle immagini decodificate',
  /fmWebpDecodedImages = Object\.create\(null\)/.test(resetBody));
ok('W8: il reset azzera anche il frame polare (niente modello vecchio al polo)',
  /fmPolarFrame = null/.test(resetBody) && /fmPolarFrameUrl = null/.test(resetBody));
ok('W9: il reset porta lo slider a 0 e invalida la chiave dei layer',
  /sl\.value = st\.slider/.test(resetBody) && /fmLayerKey = null/.test(resetBody));
ok('W10: il reset mostra il loader finche\' il nuovo frame non e\' pronto', /fmLoaderShow\(\)/.test(resetBody));
ok('W11: fmVariableChanged non lascia piu\' il player in riproduzione (niente fmStopPlay sparito)',
  /fmResetPlayerForModelChange\('variable'\);/.test(src));
const domainFn = extractFn(src, 'fmDomainChanged');
const domainBody = strip(domainFn);
ok('W12: in fmDomainChanged l\'ordine e\' reset -> run display neutro -> fmLoadDomain',
  domainBody.indexOf('fmResetPlayerForModelChange') >= 0 &&
  domainBody.indexOf('fmResetPlayerForModelChange') < domainBody.indexOf('fmUpdateRunDisplay(null)') &&
  domainBody.indexOf('fmUpdateRunDisplay(null)') < domainBody.indexOf('fmLoadDomain()'));

// ---------- 1.2.4.26: IL PANNELLO NON RESTA SULLA RUN VECCHIA ----------
// Il clamp sul manifest .om (26/64 frame) riportava fmIdx a 0 mentre la timeline ne ha
// ~206: qui si verifica il WIRING del riancoraggio DOPO la risoluzione della variabile e
// il flag che decide se una timeline nuova puo' spostare il frame.
const loadFn = extractFn(src, 'fmLoadDomain');
const loadBody = strip(loadFn);
ok('W13: fmLoadDomain risolve la variabile e POI riancora su fmStartFrameIndex',
  loadBody.indexOf('fmPopulateVariables(vars)') >= 0 &&
  loadBody.indexOf('fmPopulateVariables(vars)') < loadBody.indexOf('fmStartFrameIndex()'));
ok('W14: il vecchio clamp a 0 sul manifest .om e\' sparito dal CODICE (i commenti non contano)',
  !/if \(fmIdx >= meta\.valid_times\.length\) fmIdx = 0;/.test(loadBody));
ok('W15: il limite usa la timeline quando e\' attiva (fmTimelineFramesActive().length)',
  /fmTimelineFramesActive\(\)\.length \|\| meta\.valid_times\.length/.test(loadBody));
ok('W16: fmUserMovedFrame dichiarato e azzerato nei 3 cambi (dominio/variabile/regione)',
  /var fmUserMovedFrame = false;/.test(src) &&
  (src.match(/fmUserMovedFrame = false;/g) || []).length >= 4);
ok('W17: slider/prev/next/play marcano il frame scelto e nascondono l\'avviso',
  (src.match(/fmMarkUserMovedFrame\(\);/g) || []).length >= 5);
ok('W18: avviso non bloccante "Nuova run disponibile" con link al frame preferito',
  /id="fm-newrun"/.test(src) && /function fmGoToPreferredFrame\(\)/.test(src) &&
  /Nuova run disponibile · vai/.test(src));
ok('W19: fmSetTimeline ha i due rami (non mosso -> preferred; mosso -> conserva + avviso)',
  /if \(!fmUserMovedFrame\) \{\s*\n\s*fmIdx = fmTimelinePreferredIndex\(frames\);/.test(src) &&
  /if \(runChanged\) fmShowNewRunNotice\(newPreferred\.run_id\);/.test(src));
ok('W20: frame sparito -> si sceglie il valid_time piu\' vicino, non la stessa posizione',
  /Date\.parse\(prevFrame\.valid_time\)/.test(src) && /Math\.abs\(t - prevT\)/.test(src));

console.log(failures === 0 ? '\nRESULT: PASS' : `\nRESULT: FAIL (${failures})`);
process.exit(failures === 0 ? 0 : 1);
