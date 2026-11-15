#!/usr/bin/env node
// Verifica che la sintesi pubblica dell'organizzazione convettiva resti breve e non tecnica.
'use strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const src = fs.readFileSync(path.join(root, 'mri-light-1.2.4.97.html'), 'utf8');
const m = src.match(/function orgImpactNote\(r\) \{[\s\S]*?\n    \}/);
if (!m) throw new Error('orgImpactNote non trovata');
const ctx = { orgClassOf: (r) => r.orgClass, isSviluppoActive: false };
vm.runInNewContext(m[0], ctx);
const cases = [
  ['env', 'Il modello indica condizioni che potrebbero favorire temporali organizzati; non significa che una cella sia già presente.'],
  ['meso', 'Il modello stima una possibile cella organizzata, non confermata dalle osservazioni.'],
  ['none', 'Il modello non segnala particolari condizioni favorevoli a temporali organizzati.'],
];
for (const [orgClass, expected] of cases) {
  const text = ctx.orgImpactNote({ id: 'thunderstorm', orgClass });
  if (text.trim() !== expected) throw new Error(`Sintesi inattesa per ${orgClass}: ${text}`);
  if (/WMAXSHEAR|SRH|shear|soglia|m2\/s2|\d/.test(text)) throw new Error(`Dettagli tecnici nella sintesi ${orgClass}`);
}
if (ctx.orgImpactNote({ id: 'rain', orgClass: 'env' }) !== '') throw new Error('La nota si applica a un rischio non temporalesco');
if (src.includes('orgEvidence(tsOrgRisk)')) throw new Error('Il tooltip espone ancora i valori tecnici');
console.log('PASS: sintesi temporali breve e senza dettagli tecnici');
