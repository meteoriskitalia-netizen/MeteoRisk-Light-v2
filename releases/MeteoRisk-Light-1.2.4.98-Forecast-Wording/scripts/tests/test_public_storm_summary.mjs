#!/usr/bin/env node
'use strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
const root=path.join(path.dirname(fileURLToPath(import.meta.url)),'..','..');
const src=fs.readFileSync(path.join(root,'mri-light-1.2.4.98.html'),'utf8');
function extract(name){const m=src.match(new RegExp('function '+name+'\\(r\\) \\{[\\s\\S]*?\\n    \\}'));if(!m)throw new Error(name+' non trovata');return m[0]}
const ctx={orgClassOf:r=>r.orgClass,isSviluppoActive:false};
vm.runInNewContext(extract('orgImpactNote')+'\n'+extract('orgPhrase')+'\n'+extract('orgShortOf'),ctx);
const checks=[
 ['nota env',ctx.orgImpactNote({id:'thunderstorm',orgClass:'env'}),' Ambiente favorevole.'],
 ['nota meso',ctx.orgImpactNote({id:'thunderstorm',orgClass:'meso'}),' Possibile mesociclone previsto.'],
 ['nessun commento per none',ctx.orgImpactNote({id:'thunderstorm',orgClass:'none'}),''],
 ['frase env',ctx.orgPhrase({orgClass:'env'}),'ambiente favorevole'],
 ['frase meso',ctx.orgPhrase({orgClass:'meso'}),'possibile mesociclone'],
 ['nessuna frase per none',ctx.orgPhrase({orgClass:'none'}),'']
];
for(const [n,a,e] of checks)if(a!==e)throw new Error(n+': '+JSON.stringify(a)+' != '+JSON.stringify(e));
if(/non significa che una cella|non confermata dalle osservazioni|non prevede una cella organizzata/i.test(src))throw new Error('Disclaimer di assenza/osservazione presente');
if(ctx.orgImpactNote({id:'rain',orgClass:'env'})!=='')throw new Error('Nota applicata a rischio diverso dai temporali');
console.log('PASS: sintesi descrive solo il segnale previsionale');
