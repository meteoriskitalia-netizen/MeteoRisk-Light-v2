#!/usr/bin/env node
// TEST 1.3.0.59 — SELETTORE UNICO Pro / Semplice / Sviluppo in ENTRAMBE le app.
// Verifica senza navigare:
//   - la Pro (APP_FILE di VERSION) ha il <select id="app-mode-select"> nell'HEADER
//     (fra .app-header e .time-controls, NON nella toolbar) con 3 opzioni e title;
//   - il mapping della Pro: simple -> "/simple/", pro -> toggleSviluppo(false),
//     dev -> toggleSviluppo(true);
//   - la Simple ha lo stesso selettore con default "simple" e mapping
//     pro -> "/", dev -> "/?dev=1", simple -> resta;
//   - il boot ?dev=1 della Pro chiama la funzione REALE toggleSviluppo(true).
'use strict';

import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const versionSrc = fs.readFileSync(path.join(ROOT, 'VERSION'), 'utf8');
const appFile = (/^APP_FILE=(.+)$/m.exec(versionSrc) || [])[1];
if (!appFile) { console.error('FATAL: APP_FILE non trovato in VERSION'); process.exit(1); }
const PRO_HTML = path.join(ROOT, appFile.trim());
const SIMPLE_HTML = path.join(ROOT, 'simple', 'index.html');
const pro = fs.readFileSync(PRO_HTML, 'utf8');
const simple = fs.readFileSync(SIMPLE_HTML, 'utf8');

let failures = 0;
function ok(label, cond, detail = '') {
  console.log(`  [${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? ` · ${detail}` : ''}`);
  if (!cond) failures++;
}
function failFast(label, cond) {
  if (!cond) { console.error(`FATAL: ${label}`); process.exit(1); }
}

function options(html) {
  const sel = /<select id="app-mode-select"[\s\S]*?<\/select>/.exec(html);
  if (!sel) return null;
  return [...sel[0].matchAll(/<option value="([^"]+)"([^>]*)>/g)].map((m) => ({
    value: m[1], selected: /\bselected\b/.test(m[2])
  }));
}

// ---------- 1. struttura: Pro ----------
{
  const opts = options(pro);
  failFast('Pro: <select id="app-mode-select"> non trovato', !!opts);
  ok('Pro: 3 opzioni pro/simple/dev',
    JSON.stringify(opts.map((o) => o.value)) === '["pro","simple","dev"]');
  ok('Pro: default "pro"', opts.find((o) => o.selected)?.value === 'pro');
  const selTag = /<select id="app-mode-select"[^>]*>/.exec(pro)[0];
  const title = (/title="([^"]*)"/.exec(selTag) || [])[1] || '';
  ok('Pro: title del selettore >= 6 caratteri', title.trim().length >= 6, title.slice(0, 40));
  ok('Pro: onchange chiama applyAppMode', /onchange="applyAppMode\(this\.value\)"/.test(selTag));
  // Posizione: dentro .app-header, prima di .time-controls (non nella toolbar).
  const headerStart = pro.indexOf('<div class="app-header">');
  const headerEnd = pro.indexOf('<div class="toolbar"');
  const selPos = pro.indexOf('id="app-mode-select"');
  const timePos = pro.indexOf('<div class="time-controls">');
  ok('Pro: selettore nell\'header (fra app-header e time-controls)',
    headerStart > -1 && selPos > headerStart && selPos < timePos && timePos < headerEnd);
  ok('Pro: una sola istanza del selettore',
    (pro.match(/id="app-mode-select"/g) || []).length === 1);
}

// ---------- 2. mapping: Pro ----------
{
  const begin = pro.indexOf('//#appmode# BEGIN');
  const end = pro.indexOf('//#appmode# END');
  failFast('Pro: marcatori //#appmode# non trovati', begin > -1 && end > begin);
  const block = pro.slice(begin, end);
  const calls = [];
  const ctx = {
    window: { location: { pathname: '/', search: '', href: '' }, history: { replaceState() {} } },
    document: { getElementById: () => null },
    toggleSviluppo: (v) => { calls.push(v); },
    isSviluppoActive: false
  };
  vm.runInNewContext(block, ctx);
  failFast('Pro: applyAppMode non estratta', typeof ctx.applyAppMode === 'function');
  ok('Pro: appModeHref("simple") === "/simple/"', ctx.appModeHref('simple') === '/simple/');
  ok('Pro: appModeHref("pro") === null', ctx.appModeHref('pro') === null);
  ok('Pro: appModeHref("dev") === null (resta nella Pro)', ctx.appModeHref('dev') === null);

  ctx.applyAppMode('simple');
  ok('Pro: applyAppMode("simple") naviga a /simple/', ctx.window.location.href === '/simple/');

  ctx.window.location.href = '';
  ctx.applyAppMode('dev');
  ok('Pro: applyAppMode("dev") chiama toggleSviluppo(true)', calls.length === 1 && calls[0] === true);

  calls.length = 0;
  ctx.isSviluppoActive = true;
  ctx.applyAppMode('pro');
  ok('Pro: applyAppMode("pro") con Sviluppo ON chiama toggleSviluppo(false)',
    calls.length === 1 && calls[0] === false);
}

// ---------- 3. ?dev=1: attiva la funzione reale ----------
{
  ok('Pro: boot riconosce ?dev=1', /dev=1/.test(pro) && /bootAppMode/.test(pro));
  ok('Pro: boot chiama toggleSviluppo(true) (stessa funzione dello switch)',
    /wantDev[\s\S]{0,200}toggleSviluppo\(true\)/.test(pro));
  ok('Pro: il selettore si sincronizza dallo stato reale (syncAppModeSelect)',
    /function syncAppModeSelect/.test(pro) && /isSviluppoActive/.test(pro));
  ok('Pro: toggleSviluppo aggiorna il selettore', /syncAppModeSelect\(\);\s*\n\s*}/.test(pro));
}

// ---------- 4. struttura + mapping: Simple ----------
{
  const opts = options(simple);
  failFast('Simple: <select id="app-mode-select"> non trovato', !!opts);
  ok('Simple: 3 opzioni pro/simple/dev',
    JSON.stringify(opts.map((o) => o.value)) === '["pro","simple","dev"]');
  ok('Simple: default "simple"', opts.find((o) => o.selected)?.value === 'simple');
  const selTag = /<select id="app-mode-select"[^>]*>/.exec(simple)[0];
  const title = (/title="([^"]*)"/.exec(selTag) || [])[1] || '';
  ok('Simple: title del selettore >= 6 caratteri', title.trim().length >= 6);
  ok('Simple: selettore nell\'header', /<header class="app-header">[\s\S]*?id="app-mode-select"/.test(simple));

  const m = /<script id="app-mode">([\s\S]*?)<\/script>/.exec(simple);
  failFast('Simple: blocco app-mode non trovato', !!m);
  const ctx = { window: { location: { href: '' } }, document: { getElementById: () => null } };
  vm.runInNewContext(m[1], ctx);
  ok('Simple: appModeHref("pro") === "/"', ctx.appModeHref('pro') === '/');
  ok('Simple: appModeHref("dev") === "/?dev=1"', ctx.appModeHref('dev') === '/?dev=1');
  ok('Simple: appModeHref("simple") === null', ctx.appModeHref('simple') === null);
  ctx.applyAppMode('pro');
  ok('Simple: applyAppMode("pro") naviga a /', ctx.window.location.href === '/');
  ctx.window.location.href = '';
  ctx.applyAppMode('dev');
  ok('Simple: applyAppMode("dev") naviga a /?dev=1', ctx.window.location.href === '/?dev=1');
  ctx.window.location.href = '';
  ctx.applyAppMode('simple');
  ok('Simple: applyAppMode("simple") resta (nessuna navigazione)', ctx.window.location.href === '');
}

console.log(`\nRESULT: ${failures === 0 ? 'PASS' : 'FAIL'} (${failures} errori)`);
process.exit(failures === 0 ? 0 : 1);
