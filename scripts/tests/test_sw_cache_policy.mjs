#!/usr/bin/env node
// TEST 1.2.4.16 - POLICY DEL SERVICE WORKER (sw.js).
//
// COSA COPRE DAVVERO: sw.js viene ESEGUITO in un contesto vm con Cache API, self,
// clients e fetch SIMULATI in memoria. Il test guida gli handler reali
// (install / activate / fetch) e verifica il comportamento, non solo la presenza
// di stringhe: nessuna intercettazione per Range e per i metodi non-GET, niente
// 206/404 in cache, stale-while-revalidate per gli indici, cache-first per i
// WebP, network-first con timeout e fallback in cache per la navigazione,
// potatura oltre il limite di voci, cancellazione delle cache di altre release.
//
// LIMITI DICHIARATI: e' un emulatore Node, non un browser. Non valida il
// comportamento reale di Chromium (quota di storage, `opaque` response, HTTP/2),
// non misura la cache su disco e non verifica la correttezza dei byte serviti da
// raw.githubusercontent.com. La Cache API simulata conserva l'ordine di
// inserimento come quella vera, quindi la potatura testata e' quella
// "LRU approssimata" dichiarata nel commento di sw.js, non una LRU esatta.
'use strict';

import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const SW_PATH = path.join(ROOT, 'sw.js');
if (!fs.existsSync(SW_PATH)) { console.error('FATAL: sw.js non trovato in ' + ROOT); process.exit(1); }
const swSrc = fs.readFileSync(SW_PATH, 'utf8');
const VERSION_SRC = fs.readFileSync(path.join(ROOT, 'VERSION'), 'utf8');
const htmlName = (fs.readdirSync(ROOT).filter((f) => /^mri-light-\d+(\.\d+)+\.html$/.test(f))[0]) || '';
const htmlSrc = htmlName ? fs.readFileSync(path.join(ROOT, htmlName), 'utf8') : '';

let failures = 0;
function ok(label, cond, detail = '') {
  console.log(`  [${cond ? 'PASS' : 'FAIL'}] ${label}${detail ? ' -- ' + detail : ''}`);
  if (!cond) failures++;
}

// ---------- CACHE API SIMULATA (ordine di inserimento come quella vera) ----------
function makeCaches() {
  const stores = new Map();   // nome -> Map(chiave -> Response)
  const normKey = (req, opts) => {
    const raw = typeof req === 'string' ? req : req.url;
    const u = new URL(raw);
    return opts && opts.ignoreSearch ? u.origin + u.pathname : u.href;
  };
  const caches = {
    _stores: stores,
    _map(name) { if (!stores.has(name)) stores.set(name, new Map()); return stores.get(name); },
    keys: async () => Array.from(stores.keys()),
    delete: async (name) => stores.delete(name),
    has: async (name) => stores.has(name),
    open: async (name) => {
      const m = caches._map(name);
      return {
        put: async (req, res) => { m.set(normKey(req), res); },
        match: async (req, opts) => { const hit = m.get(normKey(req, opts)); return hit ? hit.clone() : undefined; },
        keys: async () => Array.from(m.keys()).map((k) => new Request(k, { method: 'GET' })),
        delete: async (req) => m.delete(normKey(req)),
      };
    },
    match: async (req, opts) => {
      for (const m of stores.values()) { const hit = m.get(normKey(req, opts)); if (hit) return hit.clone(); }
      return undefined;
    },
  };
  return caches;
}

// Node rifiuta navRequest(url): il SW legge solo
// url/method/mode/destination/headers, quindi si passa un oggetto-request equivalente.
function navRequest(url) {
  return { url: url, method: 'GET', mode: 'navigate', destination: 'document', headers: new Headers() };
}

function buildContext() {
  const caches = makeCaches();
  const flags = { skipWaiting: false, claim: false };
  const listeners = {};
  const fetchCalls = [];
  const ctx = {
    console,
    URL,
    Request,
    Response,
    Headers,
    Promise,
    setTimeout,
    clearTimeout,
    caches,
    self: {
      registration: { scope: 'https://meteoriskitalia-netizen.github.io/MeteoRisk-Light-v2/' },
      skipWaiting: () => { flags.skipWaiting = true; return Promise.resolve(); },
      clients: { claim: () => { flags.claim = true; return Promise.resolve(); } },
      addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    },
    fetchCalls,
    _respond: null,
  };
  ctx.fetch = async (req) => {
    const url = typeof req === 'string' ? req : req.url;
    fetchCalls.push(url);
    return ctx._respond(url, req);
  };
  ctx._respond = async () => new Response('{"ok":true}', { status: 200 });
  vm.createContext(ctx);
  vm.runInContext(swSrc, ctx);
  const fire = (type, event) => (listeners[type] || []).forEach((fn) => fn(event));
  const dispatchFetch = async (request) => {
    const event = { request, responded: null, respondWith(p) { this.responded = p; } };
    fire('fetch', event);
    return event;
  };
  return { ctx, caches, flags, listeners, fetchCalls, fire, dispatchFetch };
}

const APP_VER = (() => {
  const g = (k) => (VERSION_SRC.match(new RegExp('^' + k + '=(.+)$', 'm')) || [])[1];
  return [g('MAJOR'), g('MINOR'), g('PATCH'), g('BUILD')].join('.');
})();

// ---------- A. STATICHE DI SICUREZZA (prima di eseguire) ----------
ok('A1: CACHE_NAME e\' la costante del modulo', /var CACHE_NAME = '[^']+';/.test(swSrc));
ok('A2: CACHE_NAME versionata con la versione della release', /var CACHE_NAME = 'mri-1.2.4.46';/.test(swSrc), 'app=' + APP_VER);
ok('A2b: CACHE_NAME corrisponde a VERSION (MAJOR.MINOR.PATCH.BUILD)', swSrc.indexOf("'mri-" + APP_VER + "'") >= 0);
ok('A3: nessun precache pesante (nessun addAll / cache.add)', !/\.addAll\s*\(/.test(swSrc) && !/cache\.add\s*\(/.test(swSrc));
ok('A4: limite di voci dichiarato a 1200', /var MAX_ENTRIES = 1200;/.test(swSrc));
ok('A5: il commento dichiara la LRU approssimata e il limite sul numero di voci',
  /LRU APPROSSIMATA/.test(swSrc) && /NUMERO di voci/.test(swSrc));
ok('A6: timeout della navigazione a 3000 ms', /var NETWORK_TIMEOUT_MS = 3000;/.test(swSrc));

// ---------- B. INSTALL / ACTIVATE ----------
{
  const h = buildContext();
  const installEvent = { waitUntil: (p) => { installEvent.pending = p; } };
  h.fire('install', installEvent);
  ok('B1: install chiama skipWaiting', installEvent.pending !== undefined);
  await installEvent.pending;
  ok('B2: skipWaiting eseguito e nessuna cache creata all\'install',
    h.flags.skipWaiting === true && h.caches._stores.size === 0);

  h.caches._map('mri-1.2.4.46').set('https://x/y', new Response('cur', { status: 200 }));
  h.caches._map('mri-1.2.4.14').set('https://x/y', new Response('old', { status: 200 }));
  h.caches._map('altro-prefisso').set('https://x/z', new Response('old', { status: 200 }));
  const activateEvent = { waitUntil: (p) => { activateEvent.pending = p; } };
  h.fire('activate', activateEvent);
  await activateEvent.pending;
  const names = Array.from(h.caches._stores.keys());
  ok('B3: activate elimina le cache con nome diverso da CACHE_NAME e tiene la corrente',
    names.length === 1 && names[0] === 'mri-1.2.4.46', JSON.stringify(names));
  ok('B4: activate chiama clients.claim', h.flags.claim === true);
}

// ---------- C. NON INTERCETTAZIONE (Range / non-GET) ----------
{
  const h = buildContext();
  const post = await h.dispatchFetch(new Request('https://x.test/data/fm2/ncep_gfs025/latest.json', { method: 'POST' }));
  ok('C1: POST non intercettata (nessun respondWith)', post.responded === null);
  const ranged = await h.dispatchFetch(new Request('https://x.test/data/fm2/ncep_gfs025/t2m.om', { headers: { Range: 'bytes=0-1023' } }));
  ok('C2: richiesta con header Range non intercettata (player .om intatto)', ranged.responded === null);
  const rangedWebp = await h.dispatchFetch(new Request('https://x.test/data/fm2-rendered/north/x/y.webp', { headers: { Range: 'bytes=0-99' } }));
  ok('C3: Range su un WebP resta pass-through (nessun respondWith)', rangedWebp.responded === null);
  const other = await h.dispatchFetch(new Request('https://x.test/data/radar/latest.json'));
  ok('C4: una risorsa non prevista dalla policy passa in rete (nessun respondWith)', other.responded === null);
  ok('C5: nessuna di quelle richieste ha toccato fetch/cache del SW',
    h.fetchCalls.length === 0 && h.caches._stores.size === 0);
}

// ---------- D. STALE-WHILE-REVALIDATE (indici) ----------
{
  const h = buildContext();
  const url = 'https://raw.githubusercontent.com/meteoriskitalia-netizen/MeteoRisk-Light-v2/refs/heads/fm2-rendered/data/fm2-rendered/north/index.json';
  h.ctx._respond = async () => new Response('{"v":1}', { status: 200, headers: { 'content-type': 'application/json' } });
  const e1 = await h.dispatchFetch(new Request(url));
  const r1 = await e1.responded;
  ok('D1: index.json gestito dal SW (respondWith) e servito dalla rete al primo colpo',
    r1.ok && (await r1.text()) === '{"v":1}' && h.fetchCalls.length === 1);
  h.ctx._respond = async () => new Response('{"v":2}', { status: 200, headers: { 'content-type': 'application/json' } });
  const e2 = await h.dispatchFetch(new Request(url));
  const r2 = await e2.responded;
  const body2 = await r2.text();
  await new Promise((r) => setTimeout(r, 10));
  ok('D2: seconda richiesta servita dalla CACHE (stale) mentre la rete e\' chiamata in background',
    body2 === '{"v":1}' && h.fetchCalls.length === 2, `body=${body2} fetch=${h.fetchCalls.length}`);
  const e3 = await h.dispatchFetch(new Request(url));
  const body3 = await (await e3.responded).text();
  ok('D3: la copia aggiornata in background e\' quella servita al colpo successivo', body3 === '{"v":2}', `body=${body3}`);
  const latest = 'https://raw.githubusercontent.com/meteoriskitalia-netizen/MeteoRisk-Light-v2/refs/heads/fm2-ncep/data/fm2/ncep_gfs025/latest.json';
  const e4 = await h.dispatchFetch(new Request(latest));
  ok('D4: anche data/fm2/**/latest.json e\' intercettato (stale-while-revalidate)', e4.responded !== null);
}

// ---------- E. CACHE-FIRST (WebP) ----------
{
  const h = buildContext();
  const url = 'https://raw.githubusercontent.com/meteoriskitalia-netizen/MeteoRisk-Light-v2/refs/heads/fm2-rendered/data/fm2-rendered/north/ncep_gfs025/temperature_2m/2026102300.webp';
  h.ctx._respond = async () => new Response('WEBP-1', { status: 200 });
  const e1 = await h.dispatchFetch(new Request(url));
  ok('E1: WebP servito dalla rete al primo colpo', (await (await e1.responded).text()) === 'WEBP-1');
  h.ctx._respond = async () => new Response('WEBP-2', { status: 200 });
  const e2 = await h.dispatchFetch(new Request(url));
  const body2 = await (await e2.responded).text();
  const callsAfterHit = h.fetchCalls.length;
  ok('E2: WebP in cache -> cache-first (nessuna attesa della rete)', body2 === 'WEBP-1');
  await new Promise((r) => setTimeout(r, 10));
  ok('E3: con la copia in cache il refresh in background viene comunque lanciato', callsAfterHit >= 2);
}

// ---------- F. NETWORK-FIRST CON TIMEOUT (navigazione) ----------
{
  const h = buildContext();
  const url = 'https://meteoriskitalia-netizen.github.io/MeteoRisk-Light-v2/index.html';
  h.ctx._respond = async () => new Response('<html>1</html>', { status: 200, headers: { 'content-type': 'text/html' } });
  const e1 = await h.dispatchFetch(navRequest(url));
  ok('F1: navigazione servita dalla rete e messa in cache', (await (await e1.responded).text()) === '<html>1</html>');
  // rete che non risponde: il timeout deve far scattare il fallback in cache
  vm.runInContext('NETWORK_TIMEOUT_MS = 25;', h.ctx);
  h.ctx._respond = () => new Promise(() => { });
  const t0 = Date.now();
  const e2 = await h.dispatchFetch(navRequest(url));
  const body2 = await (await e2.responded).text();
  const elapsed = Date.now() - t0;
  ok('F2: rete appesa -> dopo il timeout si serve la copia in cache', body2 === '<html>1</html>', `elapsed=${elapsed}ms`);
  ok('F3: il fallback scatta sul timeout (non prima) e non blocca la pagina', elapsed >= 20 && elapsed < 2000, `elapsed=${elapsed}ms`);
  const rootEvent = await h.dispatchFetch(navRequest('https://meteoriskitalia-netizen.github.io/MeteoRisk-Light-v2/'));
  const rootBody = await (await rootEvent.responded).text();
  ok('F4: offline la HOME (\'/\') riparte dalla copia canonica index.html dello scope', rootBody === '<html>1</html>', `body=${rootBody.slice(0, 20)}`);
}

// ---------- G. NIENTE 206/404 IN CACHE ----------
{
  const h = buildContext();
  const url = 'https://raw.githubusercontent.com/meteoriskitalia-netizen/MeteoRisk-Light-v2/refs/heads/fm2-rendered/data/fm2-rendered/north/index.json';
  h.ctx._respond = async () => new Response('nope', { status: 404 });
  await (await h.dispatchFetch(new Request(url))).responded;
  await new Promise((r) => setTimeout(r, 10));
  ok('G1: una 404 non viene messa in cache', h.caches._map('mri-1.2.4.46').size === 0);
  const webp = 'https://x.test/data/fm2-rendered/north/ncep_gfs025/temperature_2m/2026102300.webp';
  h.ctx._respond = async () => new Response('partial', { status: 206 });
  await (await h.dispatchFetch(new Request(webp))).responded;
  await new Promise((r) => setTimeout(r, 10));
  ok('G2: una 206 non viene messa in cache', h.caches._map('mri-1.2.4.46').size === 0);
}

// ---------- H. LIMITE DI VOCI (potatura delle piu' vecchie) ----------
{
  const h = buildContext();
  vm.runInContext('MAX_ENTRIES = 3;', h.ctx);
  const mk = (i) => new Request('https://x.test/data/fm2-rendered/north/ncep_gfs025/temperature_2m/2026102' + i + '.webp');
  for (let i = 0; i < 5; i++) {
    const e = await h.dispatchFetch(mk(i));
    await e.responded;
    await new Promise((r) => setTimeout(r, 5));
  }
  const keys = Array.from(h.caches._map('mri-1.2.4.46').keys());
  ok('H1: oltre MAX_ENTRIES le voci piu\' vecchie vengono eliminate', keys.length === 3, `keys=${keys.length}`);
  ok('H2: restano le 3 piu\' recenti (ordine di inserimento)', keys.every((k) => /2026102[234]\.webp$/.test(k)), JSON.stringify(keys));
}

// ---------- I. REGISTRAZIONE NELL'HTML ----------
ok('I1: l\'html registra il SW con URL relativo', /navigator\.serviceWorker\.register\('sw\.js'\)/.test(htmlSrc));
ok('I2: la registrazione avviene dopo l\'evento load', /addEventListener\('load', fmRegisterServiceWorker/.test(htmlSrc));
ok('I3: il fallimento di register() e\' silenzioso (solo console.debug)',
  /register\('sw\.js'\)[\s\S]{0,200}?\.catch\(function \(err\) \{[\s\S]{0,240}?console\.debug/.test(htmlSrc));
// 1.2.4.46: avviso di versione nuova. Il banner deve esistere, essere hidden di default,
// e comparire solo su updatefound/statechange 'activated' + controllerchange.
ok('I4: esiste il banner #app-update ed e\' hidden di default',
  /<button[^>]*id="app-update"[^>]*hidden[^>]*>Nuova versione disponibile/.test(htmlSrc));
ok('I5: il banner ricarica la pagina con location.reload()',
  /id="app-update"[^>]*onclick="location\.reload\(\)"/.test(htmlSrc));
ok('I6: l\'avviso ascolta updatefound + statechange activated',
  /addEventListener\('updatefound'/.test(htmlSrc) && /worker\.state === 'activated'/.test(htmlSrc));
ok('I7: la prima installazione non mostra il banner (guardia sul primo worker)',
  /fmFirstWorker = reg\.active \|\| reg\.installing/.test(htmlSrc) && /if \(worker && worker === fmFirstWorker\) return;/.test(htmlSrc));

// ---------- J. DEPLOY: sw.js DEVE FINIRE IN _site/sw.js ----------
const wfDir = path.join(ROOT, '.github', 'workflows');
for (const wf of ['deploy-pages.yml', 'radar-engine.yml', 'update-weather-data.yml']) {
  const p = path.join(wfDir, wf);
  const body = fs.existsSync(p) ? fs.readFileSync(p, 'utf8') : '';
  ok(`J: ${wf} copia sw.js in _site/sw.js`, /cp sw\.js _site\/sw\.js/.test(body));
}

console.log(failures === 0 ? '\nRESULT: PASS' : `\nRESULT: FAIL (${failures})`);
process.exit(failures === 0 ? 0 : 1);
