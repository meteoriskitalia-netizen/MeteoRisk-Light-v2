#!/usr/bin/env node
// TEST 1.2.4.57 - POLICY DEL SERVICE WORKER (sw.js).
//
// COSA COPRE DAVVERO: sw.js viene ESEGUITO in un contesto vm con Cache API, self,
// clients e fetch SIMULATI in memoria. Il test guida gli handler reali
// (install / activate / fetch) e verifica il comportamento, non solo la presenza
// di stringhe: nessuna intercettazione per Range e per i metodi non-GET, niente
// 206/404 in cache, NETWORK-FIRST CON TIMEOUT per gli indici aggregati
// (index.json / timeline.json / color_scales.json) con fallback marcato
// `X-MRI-Cache: stale`, stale-while-revalidate per gli ALTRI json del branch,
// cache-first per i WebP, network-first con timeout e fallback in cache per la
// navigazione, potatura oltre il limite di voci, cancellazione delle cache di
// altre release.
//
// LIMITI DICHIARATI: e' un emulatore Node, non un browser. Non valida il
// comportamento reale di Chromium (quota di storage, `opaque` response, HTTP/2),
// non misura la cache su disco e non verifica la correttezza dei byte serviti da
// raw.githubusercontent.com. La Cache API simulata conserva l'ordine di
// inserimento come quella vera, quindi la potatura testata e' quella
// "LRU approssimata" dichiarata nel commento di sw.js, non una LRU esatta.
// Il comportamento nel browser (tempo alla prima risposta, run richiesta,
// fromServiceWorker) e' misurato a parte con CDP: questo file non lo sostituisce.
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
  const messages = [];   // 1.2.4.57: postMessage ai client (avviso di build nuova)
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
      clients: {
        claim: () => { flags.claim = true; return Promise.resolve(); },
        matchAll: () => Promise.resolve([{ postMessage: (m) => { messages.push(m); } }]),
      },
      addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    },
    fetchCalls,
    messages,
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
    const event = { request, responded: null, waits: [], respondWith(p) { this.responded = p; }, waitUntil(p) { this.waits.push(p); } };
    fire('fetch', event);
    return event;
  };
  return { ctx, caches, flags, listeners, fetchCalls, messages, fire, dispatchFetch };
}

const APP_VER = (() => {
  const g = (k) => (VERSION_SRC.match(new RegExp('^' + k + '=(.+)$', 'm')) || [])[1];
  return [g('MAJOR'), g('MINOR'), g('PATCH'), g('BUILD')].join('.');
})();

// ---------- A. STATICHE DI SICUREZZA (prima di eseguire) ----------
ok('A1: CACHE_NAME e\' la costante del modulo', /var CACHE_NAME = '[^']+';/.test(swSrc));
ok('A2: CACHE_NAME versionata con la versione della release', /var CACHE_NAME = 'mri-1.2.4.57';/.test(swSrc), 'app=' + APP_VER);
ok('A2b: CACHE_NAME corrisponde a VERSION (MAJOR.MINOR.PATCH.BUILD)', swSrc.indexOf("'mri-" + APP_VER + "'") >= 0);
ok('A3: nessun precache pesante (nessun addAll / cache.add)', !/\.addAll\s*\(/.test(swSrc) && !/cache\.add\s*\(/.test(swSrc));
ok('A4: limite di voci dichiarato a 1200', /var MAX_ENTRIES = 1200;/.test(swSrc));
ok('A5: il commento dichiara la LRU approssimata e il limite sul numero di voci',
  /LRU APPROSSIMATA/.test(swSrc) && /NUMERO di voci/.test(swSrc));
ok('A6: timeout della navigazione a 10000 ms (1.2.4.57: era 3000)', /var NETWORK_TIMEOUT_MS = 10000;/.test(swSrc));
ok('A6b: timeout degli indici a 2000 ms (network-first)', /var INDEX_TIMEOUT_MS = 2000;/.test(swSrc));
ok('A7: il marker di stale e\' dichiarato una volta sola', /var STALE_HEADER = 'X-MRI-Cache';/.test(swSrc));
ok('A8: gli indici sono riconosciuti da RE_INDEX_JSON (index|timeline) e RE_COLOR_SCALES_JSON',
  /var RE_INDEX_JSON = .*index\|timeline/.test(swSrc) && /var RE_COLOR_SCALES_JSON = .*color_scales/.test(swSrc));
ok('A9: request.cache e\' rispettato PRIMA di ogni regola (con la navigazione esclusa)',
  /function forcesNetwork\(request\)/.test(swSrc) && /if \(forcesNetwork\(request\) && !nav\) \{ event\.respondWith\(networkRespectingCacheMode\(request\)\); return; \}/.test(swSrc));
ok('A10: il messaggio di build nuova e\' dichiarato', /var BUILD_MESSAGE = 'mri-build-changed';/.test(swSrc));
ok('A11: la fetch tardiva della navigazione e\' tenuta viva da event.waitUntil',
  /event\.waitUntil\(network\.then/.test(swSrc) && /networkFirst\(request, event\)/.test(swSrc));

// ---------- B. INSTALL / ACTIVATE ----------
{
  const h = buildContext();
  const installEvent = { waitUntil: (p) => { installEvent.pending = p; } };
  h.fire('install', installEvent);
  ok('B1: install chiama skipWaiting', installEvent.pending !== undefined);
  await installEvent.pending;
  ok('B2: skipWaiting eseguito e nessuna cache creata all\'install',
    h.flags.skipWaiting === true && h.caches._stores.size === 0);

  h.caches._map('mri-1.2.4.57').set('https://x/y', new Response('cur', { status: 200 }));
  h.caches._map('mri-1.2.4.14').set('https://x/y', new Response('old', { status: 200 }));
  h.caches._map('altro-prefisso').set('https://x/z', new Response('old', { status: 200 }));
  const activateEvent = { waitUntil: (p) => { activateEvent.pending = p; } };
  h.fire('activate', activateEvent);
  await activateEvent.pending;
  const names = Array.from(h.caches._stores.keys());
  ok('B3: activate elimina le cache con nome diverso da CACHE_NAME e tiene la corrente',
    names.length === 1 && names[0] === 'mri-1.2.4.57', JSON.stringify(names));
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

// ---------- D. NETWORK-FIRST CON TIMEOUT (indici aggregati) ----------
const CACHE = 'mri-1.2.4.57';
const IDX = 'https://raw.githubusercontent.com/meteoriskitalia-netizen/MeteoRisk-Light-v2/refs/heads/fm2-rendered/data/fm2-rendered/north/index.json';
const IDX_JSON = { 'content-type': 'application/json' };
const bodyOfCache = async (h, url) => {
  const hit = h.caches._map(CACHE).get(url);
  return hit ? await hit.clone().text() : null;
};
const rejects = async (p) => { try { await p; return false; } catch (e) { return true; } };
{
  const h = buildContext();
  h.ctx._respond = async () => new Response('{"v":1}', { status: 200, headers: IDX_JSON });
  const e1 = await h.dispatchFetch(new Request(IDX));
  const r1 = await e1.responded;
  ok('D1: index.json intercettato e servito dalla RETE al primo colpo (cache vuota)',
    r1.ok && (await r1.text()) === '{"v":1}' && h.fetchCalls.length === 1);
  await new Promise((r) => setTimeout(r, 10));
  ok('D1b: la risposta di rete finisce in cache', (await bodyOfCache(h, IDX)) === '{"v":1}');

  // IL CUORE DELLA 1.2.4.57: con una copia in cache la seconda richiesta NON deve
  // servire la copia vecchia (era lo stale-while-revalidate della 1.2.4.55).
  h.ctx._respond = async () => new Response('{"v":2}', { status: 200, headers: IDX_JSON });
  const e2 = await h.dispatchFetch(new Request(IDX));
  const body2 = await (await e2.responded).text();
  ok('D2: con la cache piena vince la RETE (network-first), non la copia vecchia', body2 === '{"v":2}', `body=${body2}`);
  await new Promise((r) => setTimeout(r, 10));
  ok('D2b: la cache viene aggiornata alla risposta di rete', (await bodyOfCache(h, IDX)) === '{"v":2}');

  // Rete piu' lenta del timeout: si risponde SUBITO dalla cache, marcata stale, e la
  // fetch in volo continua ad aggiornare la cache per la richiesta successiva.
  vm.runInContext('INDEX_TIMEOUT_MS = 40;', h.ctx);
  h.ctx._respond = () => new Promise((res) => setTimeout(() => res(new Response('{"v":3}', { status: 200, headers: IDX_JSON })), 160));
  const t0 = Date.now();
  const e3 = await h.dispatchFetch(new Request(IDX));
  const r3 = await e3.responded;
  const elapsed = Date.now() - t0;
  const body3 = await r3.text();
  ok('D3: rete piu\' lenta del timeout -> si serve la CACHE (nessuna attesa oltre il timeout)',
    body3 === '{"v":2}' && elapsed >= 30 && elapsed < 150, `body=${body3} elapsed=${elapsed}ms`);
  ok('D3b: la copia servita in fallback e\' marcata X-MRI-Cache: stale',
    r3.headers.get('X-MRI-Cache') === 'stale');
  ok('D3c: il fallback NON cancella la cache', (await bodyOfCache(h, IDX)) === '{"v":2}');
  await new Promise((r) => setTimeout(r, 200));
  ok('D3d: la fetch in volo aggiorna comunque la cache (v3) per la richiesta dopo',
    (await bodyOfCache(h, IDX)) === '{"v":3}', `cache=${await bodyOfCache(h, IDX)}`);

  // Rete in errore con copia in cache: cache immediata e marcata, nessuna eccezione.
  h.ctx._respond = async () => { throw new Error('offline'); };
  const e4 = await h.dispatchFetch(new Request(IDX));
  const r4 = await e4.responded;
  ok('D4: rete in errore + cache -> si serve la cache marcata stale',
    (await r4.text()) === '{"v":3}' && r4.headers.get('X-MRI-Cache') === 'stale');
}
{
  // Niente cache e rete in errore: la promise RIFIUTA (nessuna risposta vuota inventata).
  const h = buildContext();
  h.ctx._respond = async () => { throw new Error('offline'); };
  const e = await h.dispatchFetch(new Request(IDX));
  const refused = await rejects(e.responded);
  ok('D5: senza rete e senza cache la risposta e\' un ERRORE (nessun 504/body finto)', refused === true);
  ok('D5b: nulla e\' finito in cache', h.caches._map(CACHE).size === 0);
}
{
  // Niente cache e rete lenta: si ASPETTA la rete (non si inventa una risposta vuota).
  const h = buildContext();
  vm.runInContext('INDEX_TIMEOUT_MS = 20;', h.ctx);
  h.ctx._respond = () => new Promise((res) => setTimeout(() => res(new Response('{"late":true}', { status: 200, headers: IDX_JSON })), 70));
  const t0 = Date.now();
  const e = await h.dispatchFetch(new Request(IDX));
  const r = await e.responded;
  const elapsed = Date.now() - t0;
  ok('D6: senza cache il timeout non inventa nulla: si aspetta la rete vera',
    (await r.text()) === '{"late":true}' && elapsed >= 60, `elapsed=${elapsed}ms`);
}
{
  // color_scales.json e timeline.json seguono la stessa policy degli indici.
  const h = buildContext();
  const cs = 'https://raw.githubusercontent.com/meteoriskitalia-netizen/MeteoRisk-Light-v2/refs/heads/fm2-rendered/data/fm2-rendered/color_scales.json';
  const tl = 'https://raw.githubusercontent.com/meteoriskitalia-netizen/MeteoRisk-Light-v2/refs/heads/fm2-rendered/data/fm2-rendered/north/timeline.json';
  h.ctx._respond = async () => new Response('{"s":1}', { status: 200, headers: IDX_JSON });
  const c1 = await h.dispatchFetch(new Request(cs));
  ok('D7: color_scales.json e\' intercettato (network-first)', (await c1.responded) !== null);
  h.ctx._respond = async () => new Response('{"s":2}', { status: 200, headers: IDX_JSON });
  const c2 = await h.dispatchFetch(new Request(cs));
  ok('D7b: color_scales.json -> vince la rete, non la cache', (await (await c2.responded).text()) === '{"s":2}');
  h.ctx._respond = async () => new Response('{"tl":1}', { status: 200, headers: IDX_JSON });
  await (await h.dispatchFetch(new Request(tl))).responded;
  h.ctx._respond = async () => new Response('{"tl":2}', { status: 200, headers: IDX_JSON });
  const t2 = await h.dispatchFetch(new Request(tl));
  ok('D7c: timeline.json -> vince la rete, non la cache', (await (await t2.responded).text()) === '{"tl":2}');
  // Un JSON del branch che NON e' un indice resta in stale-while-revalidate.
  const other = 'https://raw.githubusercontent.com/meteoriskitalia-netizen/MeteoRisk-Light-v2/refs/heads/fm2-rendered/data/fm2-rendered/north/ncep_gfs025/runs/20261028T000000Z/manifest.json';
  h.ctx._respond = async () => new Response('{"m":1}', { status: 200, headers: IDX_JSON });
  await (await h.dispatchFetch(new Request(other))).responded;
  h.ctx._respond = async () => new Response('{"m":2}', { status: 200, headers: IDX_JSON });
  const o2 = await h.dispatchFetch(new Request(other));
  const o2body = await (await o2.responded).text();
  await new Promise((r) => setTimeout(r, 10));
  ok('D8: un JSON NON-indice dello stesso branch resta stale-while-revalidate', o2body === '{"m":1}', `body=${o2body}`);
  ok('D8b: e la copia aggiornata in background e\' quella servita dopo', (await bodyOfCache(h, other)) === '{"m":2}');
  const latest = 'https://raw.githubusercontent.com/meteoriskitalia-netizen/MeteoRisk-Light-v2/refs/heads/fm2-ncep/data/fm2/ncep_gfs025/latest.json';
  const e4 = await h.dispatchFetch(new Request(latest));
  ok('D9: data/fm2/**/latest.json resta stale-while-revalidate (non e\' un indice aggregato)', e4.responded !== null);
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
  ok('G1: una 404 non viene messa in cache', h.caches._map('mri-1.2.4.57').size === 0);
  const webp = 'https://x.test/data/fm2-rendered/north/ncep_gfs025/temperature_2m/2026102300.webp';
  h.ctx._respond = async () => new Response('partial', { status: 206 });
  await (await h.dispatchFetch(new Request(webp))).responded;
  await new Promise((r) => setTimeout(r, 10));
  ok('G2: una 206 non viene messa in cache', h.caches._map('mri-1.2.4.57').size === 0);
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
  const keys = Array.from(h.caches._map('mri-1.2.4.57').keys());
  ok('H1: oltre MAX_ENTRIES le voci piu\' vecchie vengono eliminate', keys.length === 3, `keys=${keys.length}`);
  ok('H2: restano le 3 piu\' recenti (ordine di inserimento)', keys.every((k) => /2026102[234]\.webp$/.test(k)), JSON.stringify(keys));
}

// ---------- I. REGISTRAZIONE NELL'HTML ----------
ok('I1: l\'html registra il SW con URL relativo', /navigator\.serviceWorker\.register\('sw\.js'\)/.test(htmlSrc));
ok('I2: la registrazione avviene dopo l\'evento load', /addEventListener\('load', fmRegisterServiceWorker/.test(htmlSrc));
ok('I3: il fallimento di register() e\' silenzioso (solo console.debug)',
  /register\('sw\.js'\)[\s\S]{0,200}?\.catch\(function \(err\) \{[\s\S]{0,240}?console\.debug/.test(htmlSrc));
// 1.2.4.49: avviso di versione nuova. Il banner deve esistere, essere hidden di default,
// e comparire solo su updatefound/statechange 'activated' + controllerchange.
ok('I4: esiste il banner #app-update ed e\' hidden di default',
  /<button[^>]*id="app-update"[^>]*hidden[^>]*>Nuova versione disponibile/.test(htmlSrc));
ok('I5: il banner ricarica la pagina con location.reload()',
  /id="app-update"[^>]*onclick="location\.reload\(\)"/.test(htmlSrc));
ok('I6: l\'avviso ascolta updatefound + statechange activated',
  /addEventListener\('updatefound'/.test(htmlSrc) && /worker\.state === 'activated'/.test(htmlSrc));
ok('I7: la prima installazione non mostra il banner (guardia sul primo worker)',
  /fmFirstWorker = reg\.active \|\| reg\.installing/.test(htmlSrc) && /if \(worker && worker === fmFirstWorker\) return;/.test(htmlSrc));
// 1.2.4.57: il client deve accorgersi della risposta marcata stale e ricaricare i frame
// SOLO quando generated_at cambia. Sono controlli STATICI sul sorgente: il comportamento
// nel browser (run richiesta, tempo, rebuild) e' misurato con CDP, non qui.
ok('I8: il client legge il marker X-MRI-Cache: stale',
  /headers\.get\('X-MRI-Cache'\) === 'stale'/.test(htmlSrc));
ok('I9: esiste una sola funzione di rivalidazione (fmRevalidateRendered)',
  /async function fmRevalidateRendered\(/.test(htmlSrc) && (htmlSrc.match(/async function fmRevalidateRendered\(/g) || []).length === 1);
ok('I10: la rivalidazione ricostruisce solo se generated_at cambia',
  /doc\.generated_at !== oldGen/.test(htmlSrc) && /idx\.generated_at !== oldIdxGen/.test(htmlSrc));
ok('I11: il polling dei 5 minuti usa la stessa rivalidazione',
  /await fmRevalidateRendered\(\{ allowCold: true, reason: 'poll' \}\)/.test(htmlSrc));
ok('I12: la rivalidazione da stale e\' a 2 s con tetto di 3 giri',
  /var FM_STALE_RECHECK_MS = 2000;/.test(htmlSrc) && /var FM_STALE_RECHECK_MAX = 3;/.test(htmlSrc));
// 1.2.4.57 - causa 2 e causa 3 lato client.
ok('I13: il retry dei WebP distingue 404 (definitivo) da errore transitorio (backoff)',
  /function fmWebpFailure\(url, cb, opts\)/.test(htmlSrc) &&
  /FM_WEBP_RETRY_DELAYS = \[1000, 3000, 9000\]/.test(htmlSrc) &&
  /status === 404 \|\| status === 410/.test(htmlSrc));
ok('I14: il fallback `.om` da errore transitorio NON entra in blacklist',
  /fmWebpTransientUrl/.test(htmlSrc) && !/im\.onerror = function \(\) \{ fmDeadWebpRemember/.test(htmlSrc));
ok('I15: il client ascolta mri-build-changed e mostra il banner #app-update',
  /addEventListener\('message'[\s\S]{0,500}mri-build-changed/.test(htmlSrc) && /fmShowUpdateBanner\(null\)/.test(htmlSrc));
ok('I16: la probe HEAD non legge la cache (method HEAD + cache no-store)',
  /method: 'HEAD', cache: 'no-store'/.test(htmlSrc));

// ---------- K. request.cache ESPLICITO (CAUSA 1: priorita' su ogni regola) ----------
{
  const h = buildContext();
  const json = { 'content-type': 'application/json' };
  h.ctx._respond = async () => new Response('{"v":1}', { status: 200, headers: json });
  await (await h.dispatchFetch(new Request(IDX))).responded;
  await new Promise((r) => setTimeout(r, 10));
  h.ctx._respond = async () => new Response('{"v":2}', { status: 200, headers: json });
  const e1 = await h.dispatchFetch(new Request(IDX, { cache: 'no-store' }));
  const b1 = await (await e1.responded).text();
  ok('K1: no-store -> SEMPRE rete, anche con la cache piena', b1 === '{"v":2}', `body=${b1}`);
  await new Promise((r) => setTimeout(r, 10));
  ok('K1b: la risposta ok aggiorna la cache anche con no-store', (await bodyOfCache(h, IDX)) === '{"v":2}');
  h.ctx._respond = async () => { throw new Error('offline'); };
  const e2 = await h.dispatchFetch(new Request(IDX, { cache: 'no-store' }));
  ok('K2: no-store + rete in errore -> ERRORE (la cache non si legge)', await rejects(e2.responded));
  const e3 = await h.dispatchFetch(new Request(IDX, { cache: 'no-cache' }));
  const r3 = await e3.responded;
  ok('K3: no-cache + rete in errore -> cache ammessa e marcata stale',
    (await r3.text()) === '{"v":2}' && r3.headers.get('X-MRI-Cache') === 'stale');
  h.ctx._respond = async () => new Response('{"v":3}', { status: 200, headers: json });
  const e4 = await h.dispatchFetch(new Request(IDX, { cache: 'reload' }));
  ok('K4: reload -> vince la rete', (await (await e4.responded).text()) === '{"v":3}');
  const webp = 'https://x.test/data/fm2-rendered/north/ncep_gfs025/temperature_2m/2026102300.webp';
  h.ctx._respond = async () => new Response('IMG-1', { status: 200 });
  await (await h.dispatchFetch(new Request(webp))).responded;
  h.ctx._respond = async () => new Response('IMG-2', { status: 200 });
  const e5 = await h.dispatchFetch(new Request(webp, { cache: 'no-store' }));
  ok('K5: no-store su un WebP scavalca il cache-first', (await (await e5.responded).text()) === 'IMG-2');

  const h2 = buildContext();
  vm.runInContext('INDEX_TIMEOUT_MS = 20;', h2.ctx);
  h2.ctx._respond = async () => new Response('{"w":1}', { status: 200, headers: json });
  await (await h2.dispatchFetch(new Request(IDX))).responded;
  h2.ctx._respond = () => new Promise((res) => setTimeout(() => res(new Response('{"w":2}', { status: 200, headers: json })), 80));
  const t0 = Date.now();
  const e6 = await h2.dispatchFetch(new Request(IDX, { cache: 'no-store' }));
  const b6 = await (await e6.responded).text();
  const el6 = Date.now() - t0;
  ok('K6: no-store su un indice -> aspetta la rete, non il timeout dei 2 s', b6 === '{"w":2}' && el6 >= 70, `body=${b6} elapsed=${el6}ms`);

  // La navigazione e' l'ECCEZIONE dichiarata: un reload arriva con cache:'reload' e deve
  // continuare a usare la cache di fallback (altrimenti la causa 3 non ha rimedio).
  const h3 = buildContext();
  const navUrl = 'https://meteoriskitalia-netizen.github.io/MeteoRisk-Light-v2/index.html';
  h3.ctx._respond = async () => new Response('<html>CACHED</html>', { status: 200, headers: { 'content-type': 'text/html', etag: '"b1"' } });
  await (await h3.dispatchFetch(navRequest(navUrl))).responded;
  await new Promise((r) => setTimeout(r, 10));
  vm.runInContext('NETWORK_TIMEOUT_MS = 30;', h3.ctx);
  h3.ctx._respond = () => new Promise(() => { });
  const navReload = navRequest(navUrl);
  navReload.cache = 'reload';
  const e7 = await h3.dispatchFetch(navReload);
  const b7 = await (await e7.responded).text();
  ok('K7: navigazione con cache:reload -> resta la cache di fallback (timeout, causa 3)', b7 === '<html>CACHED</html>', `body=${b7}`);
}

// ---------- L. NAVIGAZIONE LENTA: cache subito + avviso di build nuova (CAUSA 3) ----------
{
  const h = buildContext();
  const url = 'https://meteoriskitalia-netizen.github.io/MeteoRisk-Light-v2/index.html';
  h.ctx._respond = async () => new Response('<html>OLD</html>', { status: 200, headers: { 'content-type': 'text/html', etag: '"build-1"' } });
  await (await h.dispatchFetch(navRequest(url))).responded;
  await new Promise((r) => setTimeout(r, 10));
  vm.runInContext('NETWORK_TIMEOUT_MS = 40;', h.ctx);
  h.ctx._respond = () => new Promise((res) => setTimeout(() => res(new Response('<html>NEW</html>', { status: 200, headers: { 'content-type': 'text/html', etag: '"build-2"' } })), 140));
  const t0 = Date.now();
  const e = await h.dispatchFetch(navRequest(url));
  const body = await (await e.responded).text();
  const el = Date.now() - t0;
  ok('L1: navigazione oltre il timeout -> cache SUBITO (primo paint non bloccato)',
    body === '<html>OLD</html>' && el >= 30 && el < 130, `body=${body} elapsed=${el}ms`);
  await new Promise((r) => setTimeout(r, 220));
  ok('L2: la risposta tardiva con ETag diverso manda mri-build-changed ai client',
    h.messages.some((m) => m && m.type === 'mri-build-changed'), JSON.stringify(h.messages));
  ok('L1b: la fetch tardiva e\' tenuta viva da event.waitUntil (il SW non muore)', e.waits.length === 1, 'waits=' + e.waits.length);
  ok('L3: la cache e\' aggiornata alla build nuova per la navigazione successiva',
    (await bodyOfCache(h, url)) === '<html>NEW</html>');
  const h2 = buildContext();
  h2.ctx._respond = async () => new Response('<html>SAME</html>', { status: 200, headers: { 'content-type': 'text/html', etag: '"same"' } });
  await (await h2.dispatchFetch(navRequest(url))).responded;
  await new Promise((r) => setTimeout(r, 10));
  vm.runInContext('NETWORK_TIMEOUT_MS = 30;', h2.ctx);
  h2.ctx._respond = () => new Promise((res) => setTimeout(() => res(new Response('<html>SAME</html>', { status: 200, headers: { 'content-type': 'text/html', etag: '"same"' } })), 100));
  await (await h2.dispatchFetch(navRequest(url))).responded;
  await new Promise((r) => setTimeout(r, 160));
  ok('L4: ETag identico -> nessun avviso (niente banner inutili)', h2.messages.length === 0, JSON.stringify(h2.messages));
}

// ---------- J. DEPLOY: sw.js DEVE FINIRE IN _site/sw.js ----------
const wfDir = path.join(ROOT, '.github', 'workflows');
for (const wf of ['deploy-pages.yml', 'radar-engine.yml', 'update-weather-data.yml']) {
  const p = path.join(wfDir, wf);
  const body = fs.existsSync(p) ? fs.readFileSync(p, 'utf8') : '';
  ok(`J: ${wf} copia sw.js in _site/sw.js`, /cp sw\.js _site\/sw\.js/.test(body));
}

console.log(failures === 0 ? '\nRESULT: PASS' : `\nRESULT: FAIL (${failures})`);
process.exit(failures === 0 ? 0 : 1);
