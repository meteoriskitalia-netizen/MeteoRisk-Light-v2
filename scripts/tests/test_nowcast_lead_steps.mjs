#!/usr/bin/env node
// TEST 1.3.0.81 — NOWCAST A PASSO CRESCENTE FINO A +180 MIN.
// Verifica il CONTRATTO CLIENT (leads[] dinamico) con i 15 frame del server:
//   - 15 frame con passi 5/15/30, nessun numero hardcodato;
//   - una lacuna (image:null) non rompe la lista/timeline;
//   - etichetta «+25 min · HH:MM» e «affidabilità bassa» SOLO oltre 90 min;
//   - la legenda usa i numeri MISURATI (reliability_by_lead), non una frase
//     generica; senza dati resta la frase statica della release.
'use strict';

import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';
import { appPath } from './_app_path.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const HTML = appPath();
const src = fs.readFileSync(HTML, 'utf8');

let failures = 0;
function ok(label, cond, detail = '') {
  console.log(`  [${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? ` · ${detail}` : ''}`);
  if (!cond) failures++;
}
function failFast(label, cond) {
  if (!cond) { console.error(`FATAL: ${label}`); process.exit(1); }
}
function extractFn(name) {
  const base = name.replace('()', '');
  const esc = base.replace(/[()]/g, c => '\\' + c);
  const re = new RegExp(esc + '\\([^)]*\\) \\{[\\s\\S]*?\\n    \\}');
  const m = src.match(re);
  failFast(`${base}() non estratta`, !!m);
  return m[0];
}

// ---------- harness: solo le funzioni pure del blocco nowcast ----------
let pure = '';
pure += '\nvar nowcastState = { data: null, frame: 0 };';
for (const n of ['function nowcastLeadList', 'function nowcastLeads',
  'function nowcastFrameLabel', 'function nowcastLowReliability',
  'function nowcastCsiTxt', 'function nowcastReliabilityText',
  'function nowcastUpdateReliabilityNote']) {
  pure += '\n' + extractFn(n);
}
const noteEl = { textContent: '' };
const ctx = {
  nowcastState: { data: null, frame: 0 },
  document: { getElementById(id) { return id === 'nowcast-legend-note' ? noteEl : null; } },
  console,
};
vm.runInNewContext(pure, ctx);

// ---------- dati: 15 frame, passo crescente, UNA lacuna dichiarata ----------
const leads = [];
for (const lead of [5, 10, 15, 20, 25, 30, 35, 40, 45, 60, 75, 90, 120, 150, 180]) {
  leads.push({
    lead_min: lead,
    step_min: lead <= 45 ? 5 : (lead <= 90 ? 15 : 30),
    valid_time: new Date(Date.UTC(2026, 10, 25, 16, 15 + lead)).toISOString()
      .replace(/\.\d{3}Z$/, 'Z'),
    image: 'nowcast_lead' + String(lead).padStart(3, '0') + '.webp',
    official: [15, 30, 60].indexOf(lead) !== -1,
  });
}
const reliability = {
  schema: 'meteorisk.nowcast_reliability/1',
  cases: 24,
  horizons: {
    30: { lead_min: 30, nowcast_csi: 0.5176, persistence_csi: 0.4583,
          nowcast_beats_persistence: true, low_reliability: false },
    90: { lead_min: 90, nowcast_csi: 0.2926, persistence_csi: 0.2852,
          nowcast_beats_persistence: true, low_reliability: false },
    120: { lead_min: 120, nowcast_csi: 0.2422, persistence_csi: 0.2413,
           nowcast_beats_persistence: true, low_reliability: true },
    180: { lead_min: 180, nowcast_csi: 0.1606, persistence_csi: 0.1736,
           nowcast_fss: 0.1442, persistence_fss: 0.1931,
           nowcast_beats_persistence: false, low_reliability: true },
  },
};
const data = {
  available: true, source_slot: '2026-11-25T16:15:00Z',
  low_reliability_over_min: 90,
  leads: leads,
  reliability_by_lead: reliability,
};

// ---------- 1. lista dinamica: 15 -> 14 (lacuna saltata, non rotta) ----------
ctx.nowcastState.data = data;
const list = ctx.nowcastLeadList(data);
ok('15 lead con immagine -> 15 voci', list.length === 15);
ok('lead ordinati per lead_min', list.every((v, i, a) => i === 0 || a[i - 1].lead < v.lead));
const gapList = ctx.nowcastLeadList(Object.assign({}, data, {
  leads: leads.map(l => Object.assign({}, l, { image: null })),
}));
ok('tutti i lead senza immagine -> lista vuota (nessun crash)', gapList.length === 0);
const oneGap = ctx.nowcastLeadList(Object.assign({}, data, {
  leads: leads.map(l => l.lead_min === 75 ? Object.assign({}, l, { image: null }) : l),
}));
ok('una lacuna a +75 non rompe la lista (14 frame, +60 e +90 adiacenti)',
  oneGap.length === 14 && oneGap.some(x => x.lead === 60) && oneGap.some(x => x.lead === 90));
ok('passi diversi arrivano al client (5/15/30)',
  oneGap.filter(x => x.lead <= 45).every(x => x.lead % 5 === 0) &&
  oneGap.some(x => x.lead === 90) && oneGap.some(x => x.lead === 180));

// ---------- 2. etichetta per frame ----------
const item25 = ctx.nowcastLeadList(data).find(x => x.lead === 25);
const label25 = ctx.nowcastFrameLabel(item25);
ok('etichetta +25: «+25 min · HH:MM»', /^\+25 min · \d{2}:\d{2}$/.test(label25), label25);
const item30 = ctx.nowcastLeadList(data).find(x => x.lead === 30);
ok('+30 ufficiale resta marcato', /· ufficiale$/.test(ctx.nowcastFrameLabel(item30)));
const item90 = ctx.nowcastLeadList(data).find(x => x.lead === 90);
ok('+90 NON è «affidabilità bassa» (soglia: oltre 90)',
  !/affidabilità bassa/.test(ctx.nowcastFrameLabel(item90)), ctx.nowcastFrameLabel(item90));
for (const lead of [120, 150, 180]) {
  const it = ctx.nowcastLeadList(data).find(x => x.lead === lead);
  ok(`+${lead} è «affidabilità bassa»`, /· affidabilità bassa$/.test(ctx.nowcastFrameLabel(it)),
    ctx.nowcastFrameLabel(it));
}
ok('nowcastLowReliability usa la soglia del server (90)',
  ctx.nowcastLowReliability(90) === false && ctx.nowcastLowReliability(91) === true);

// ---------- 3. legenda con i numeri misurati ----------
const txt = ctx.nowcastReliabilityText(data);
ok('legenda cita +90 e +180 con i CSI misurati',
  !!txt && txt.indexOf('a +90 min CSI 0,29 contro 0,29') !== -1 &&
  txt.indexOf('a +180 min 0,16 contro 0,17') !== -1, txt || 'null');
ok('legenda cita FSS a +180 e la percentuale rispetto a +30',
  !!txt && txt.indexOf('FSS 0,14 contro 0,19') !== -1 &&
  txt.indexOf('31% di quella a +30 min') !== -1, txt || 'null');
ok('legenda dichiara che a +180 min è PEGGIORE della persistenza',
  !!txt && /a \+180 min il nowcast è PEGGIORE della persistenza/.test(txt), txt || 'null');
ctx.nowcastState.data = data;
ctx.nowcastUpdateReliabilityNote();
ok('la nota DOM viene aggiornata con la frase misurata',
  noteEl.textContent === txt, noteEl.textContent);
ok('senza reliability_by_lead la frase resta quella statica',
  ctx.nowcastReliabilityText({ available: true, leads: leads }) === null);
const htmlNote = src.match(/id="nowcast-legend-note"[^>]*>([^<]*)</);
ok('il markup ha una frase statica MISURATA (non «dopo 30 minuti cala»)',
  !!htmlNote && /misurato su 24 casi/.test(htmlNote[1]) &&
  /CSI 0,16 contro 0,17/.test(htmlNote[1]) && !/dopo 30 minuti/.test(htmlNote[1]),
  htmlNote ? htmlNote[1] : 'mancante');

// ---------- 4. nessun limite a +60 nel client ----------
ok('caption della timeline dichiara il passo crescente 5/15/30',
  /passo 5\/15\/30 min/.test(src));
ok('l\'etichetta usa la soglia del server (low_reliability_over_min), non 60',
  /low_reliability_over_min/.test(extractFn('function nowcastLowReliability')));

console.log(`\nRESULT: ${failures === 0 ? 'PASS' : 'FAIL'} (${failures} errori)`);
process.exit(failures === 0 ? 0 : 1);
