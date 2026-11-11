#!/usr/bin/env node
// TEST 1.2.4.77 - POLICY DEL SERVICE WORKER (sw.js).
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
// FASE 4 (1.2.4.77): precache minima all'install (solo html + manifest + offline.html),
// tile OpenStreetMap cache-first con scadenza 7 giorni, frame radar (DPC S3) e
// satellite cache-first con scadenza 30 minuti e massimo 6 slot per famiglia,
// fallback al frame piu' recente della stessa sorgente per il satellite, marker
// `X-MRI-Cache: stale`, pagina offline in cache e risposta inline 503 come ultima
// guardia, manifest/icone/offline.html presenti e validi.
//
// LIMITI DICHIARATI: e' un emulatore Node, non un browser. Non valida il
// comportamento reale di Chromium (quota di storage, `opaque` response, HTTP/2),
// non misura la cache su disco e non verifica la correttezza dei byte serviti da
// raw.githubusercontent.com o da tile.openstreetmap.org. La Cache API simulata
// conserva l'ordine di inserimento come quella vera, quindi la potatura testata e'
// quella "LRU approssimata" dichiarata nel commento di sw.js, non una LRU esatta.
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
  const messages = [];   // 1.2.4.64: postMessage ai client (avviso di build nuova)
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
ok('A2: CACHE_NAME versionata con la versione della release', /var CACHE_NAME = 'mri-1.2.4.77';/.test(swSrc), 'app=' + APP_VER);
ok('A2b: CACHE_NAME corrisponde a VERSION (MAJOR.MINOR.PATCH.BUILD)', swSrc.indexOf("'mri-" + APP_VER + "'") >= 0);
ok('A3: precache minima dichiarata (solo index.html + manifest + offline.html), nessun addAll',
  (() => {
    const m = swSrc.match(/var PRECACHE_PATHS = \[([^\]]*)\];/);
    const list = m ? m[1] : '';
    return list.split(',').length === 3 &&
      /index\.html/.test(list) && /manifest\.webmanifest/.test(list) && /offline\.html/.test(list) &&
      !/\.webp|\.om|data\//.test(list) && !/\.addAll\s*\(/.test(swSrc);
  })());
ok('A4: limite di voci dichiarato a 1200', /var MAX_ENTRIES = 1200;/.test(swSrc));
ok('A5: il commento dichiara la LRU approssimata e il limite sul numero di voci',
  /LRU APPROSSIMATA/.test(swSrc) && /NUMERO di voci/.test(swSrc));
ok('A6: timeout della navigazione a 10000 ms (1.2.4.64: era 3000)', /var NETWORK_TIMEOUT_MS = 10000;/.test(swSrc));
ok('A6b: timeout degli indici a 2000 ms (network-first)', /var INDEX_TIMEOUT_MS = 2000;/.test(swSrc));
ok('A7: il marker di stale e\' dichiarato una volta sola', /var STALE_HEADER = 'X-MRI-Cache';/.test(swSrc));
ok('A8: gli indici sono riconosciuti da RE_INDEX_JSON (index|timeline) e RE_COLOR_SCALES_JSON',
  /var RE_INDEX_JSON = .*index\|timeline/.test(swSrc) && /var RE_COLOR_SCALES_JSON = .*color_scales/.test(swSrc));
ok('A9: request.cache e\' rispettato PRIMA di ogni regola (con la navigazione esclusa)',
  /function forcesNetwork\(request\)/.test(swSrc) && /if \(forcesNetwork\(request\) && !nav\) \{ event\.respondWith\(networkRespectingCacheMode\(request\)\); return; \}/.test(swSrc));
ok('A10: il messaggio di build nuova e\' dichiarato', /var BUILD_MESSAGE = 'mri-build-changed';/.test(swSrc));
ok('A11: la fetch tardiva della navigazione e\' tenuta viva da event.waitUntil',
  /event\.waitUntil\(network\.then/.test(swSrc) && /networkFirst\(request, event\)/.test(swSrc));
ok('A12: TTL dichiarati (tile 7 giorni, frame 30 minuti, 6 frame per famiglia)',
  /var TILE_TTL_MS = 7 \* 24 \* 60 \* 60 \* 1000;/.test(swSrc) &&
  /var FRAME_TTL_MS = 30 \* 60 \* 1000;/.test(swSrc) &&
  /var MAX_FRAMES_PER_FAMILY = 6;/.test(swSrc));
ok('A13: cache separate per tile e frame (la potatura non tocca i WebP previsionali)',
  /var TILE_CACHE = 'mri-tiles-1\.2\.4\.77';/.test(swSrc) &&
  /var FRAME_CACHE = 'mri-frames-1\.2\.4\.77';/.test(swSrc) &&
  /var APP_CACHES = \[CACHE_NAME, TILE_CACHE, FRAME_CACHE\];/.test(swSrc));
ok('A14: satellite/manifest.json usa il network-first degli indici',
  /RE_SAT_MANIFEST\.test\(path\)\) \{ event\.respondWith\(networkFirstIndex\(request\)\); return; \}/.test(swSrc));

// ---------- B. INSTALL / ACTIVATE ----------
{
  const h = buildContext();
  const installEvent = { waitUntil: (p) => { installEvent.pending = p; } };
  h.fire('install', installEvent);
  ok('B1: install chiama skipWaiting', installEvent.pending !== undefined);
  await installEvent.pending;
  const appStore = h.caches._map('mri-1.2.4.77');
  const installKeys = Array.from(appStore.keys());
  ok('B2: install precache SOLO i 3 asset dichiarati (html, manifest, offline.html)',
    h.flags.skipWaiting === true && appStore.size === 3 && h.caches._stores.size === 1 &&
    installKeys.every((k) => /(index\.html|manifest\.webmanifest|offline\.html)$/.test(k)) &&
    h.fetchCalls.length === 3, JSON.stringify(installKeys));

  h.caches._map('mri-1.2.4.77').set('https://x/y', new Response('cur', { status: 200 }));
  h.caches._map('mri-tiles-1.2.4.77').set('https://x/t', new Response('tile', { status: 200 }));
  h.caches._map('mri-frames-1.2.4.77').set('https://x/f', new Response('frame', { status: 200 }));
  h.caches._map('mri-1.2.4.14').set('https://x/y', new Response('old', { status: 200 }));
  h.caches._map('altro-prefisso').set('https://x/z', new Response('old', { status: 200 }));
  const activateEvent = { waitUntil: (p) => { activateEvent.pending = p; } };
  h.fire('activate', activateEvent);
  await activateEvent.pending;
  const names = Array.from(h.caches._stores.keys());
  ok('B3: activate tiene le 3 cache dell\'app e cancella quelle di altre release',
    names.length === 3 && ['mri-1.2.4.77', 'mri-tiles-1.2.4.77', 'mri-frames-1.2.4.77'].every((n) => names.indexOf(n) !== -1),
    JSON.stringify(names));
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
  const other = await h.dispatchFetch(new Request('https://x.test/data/stations/metar_latest.json'));
  ok('C4: una risorsa fuori dalla policy (data/stations/**) passa in rete (nessun respondWith)', other.responded === null);
  ok('C5: nessuna di quelle richieste ha toccato fetch/cache del SW',
    h.fetchCalls.length === 0 && h.caches._stores.size === 0);
}

// ---------- D. NETWORK-FIRST CON TIMEOUT (indici aggregati) ----------
const CACHE = 'mri-1.2.4.77';
const IDX = 'https://raw.githubusercontent.com/meteoriskitalia-netizen/MeteoRisk-Light-v2/refs/heads/fm2-rendered/data/fm2-rendered/north/index.json';
const IDX_JSON = { 'content-type': 'application/json' };
const bodyOfCache = async (h, url) => {
  const hit = h.caches._map(CACHE).get(url);
  return hit ? await hit.clone().text() : null;
};
const bodyOfCacheNamed = async (h, name, url) => {
  const hit = h.caches._map(name).get(url);
  return hit ? await hit.clone().text() : null;
};
// "Time travel" del TTL: riscrive SOLO il timestamp della copia in cache, corpo
// invariato. Serve a provare la scadenza senza attendere 7 giorni / 30 minuti.
const expireEntry = async (h, cacheName, url, ageMs) => {
  const store = h.caches._map(cacheName);
  const hit = store.get(url);
  if (!hit) throw new Error('expireEntry: voce assente ' + url);
  const body = await hit.clone().text();
  const headers = new Headers(hit.headers);
  headers.set('x-mri-cached-at', String(Date.now() - ageMs));
  store.set(url, new Response(body, { status: 200, headers }));
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

  // IL CUORE DELLA 1.2.4.64: con una copia in cache la seconda richiesta NON deve
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
  ok('G1: una 404 non viene messa in cache', h.caches._map('mri-1.2.4.77').size === 0);
  const webp = 'https://x.test/data/fm2-rendered/north/ncep_gfs025/temperature_2m/2026102300.webp';
  h.ctx._respond = async () => new Response('partial', { status: 206 });
  await (await h.dispatchFetch(new Request(webp))).responded;
  await new Promise((r) => setTimeout(r, 10));
  ok('G2: una 206 non viene messa in cache', h.caches._map('mri-1.2.4.77').size === 0);
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
  const keys = Array.from(h.caches._map('mri-1.2.4.77').keys());
  ok('H1: oltre MAX_ENTRIES le voci piu\' vecchie vengono eliminate', keys.length === 3, `keys=${keys.length}`);
  ok('H2: restano le 3 piu\' recenti (ordine di inserimento)', keys.every((k) => /2026102[234]\.webp$/.test(k)), JSON.stringify(keys));
}

// ---------- M. TILE DELLA MAPPA BASE (cache-first 7 giorni, 700 voci) ----------
const TILE_CACHE_NAME = 'mri-tiles-1.2.4.77';
const FRAME_CACHE_NAME = 'mri-frames-1.2.4.77';
const OSM_TILE = 'https://tile.openstreetmap.org/6/33/23.png';
{
  const h = buildContext();
  h.ctx._respond = async () => new Response('TILE-1', { status: 200 });
  const e1 = await h.dispatchFetch(new Request(OSM_TILE));
  ok('M1: tile OSM intercettata e servita dalla rete al primo colpo', (await (await e1.responded).text()) === 'TILE-1');
  await new Promise((r) => setTimeout(r, 10));
  ok('M1b: la tile finisce nella cache dedicata, NON in quella dell\'app',
    h.caches._map(TILE_CACHE_NAME).size === 1 && h.caches._map(CACHE).size === 0,
    `tile=${h.caches._map(TILE_CACHE_NAME).size} app=${h.caches._map(CACHE).size}`);
  const callsBefore = h.fetchCalls.length;
  h.ctx._respond = async () => new Response('TILE-2', { status: 200 });
  const r2 = await (await h.dispatchFetch(new Request(OSM_TILE))).responded;
  ok('M2: tile fresca -> cache-first, nessuna richiesta di rete',
    (await r2.text()) === 'TILE-1' && h.fetchCalls.length === callsBefore);
  await expireEntry(h, TILE_CACHE_NAME, OSM_TILE, 8 * 24 * 60 * 60 * 1000);
  h.ctx._respond = async () => new Response('TILE-3', { status: 200 });
  const r3 = await (await h.dispatchFetch(new Request(OSM_TILE))).responded;
  ok('M3: tile scaduta (8 giorni) -> va in rete e vince la rete', (await r3.text()) === 'TILE-3');
  await new Promise((r) => setTimeout(r, 10));
  ok('M3b: la copia in cache e\' aggiornata alla nuova tile',
    (await bodyOfCacheNamed(h, TILE_CACHE_NAME, OSM_TILE)) === 'TILE-3');
  await expireEntry(h, TILE_CACHE_NAME, OSM_TILE, 8 * 24 * 60 * 60 * 1000);
  h.ctx._respond = async () => { throw new Error('offline'); };
  const r4 = await (await h.dispatchFetch(new Request(OSM_TILE))).responded;
  ok('M4: tile scaduta + rete assente -> copia scaduta marcata X-MRI-Cache: stale',
    (await r4.text()) === 'TILE-3' && r4.headers.get('X-MRI-Cache') === 'stale');
}
{
  const h = buildContext();
  h.ctx._respond = async () => new Response('nope', { status: 404 });
  await (await h.dispatchFetch(new Request(OSM_TILE))).responded;
  await new Promise((r) => setTimeout(r, 10));
  ok('M5: una 404 su tile non entra in cache', h.caches._map(TILE_CACHE_NAME).size === 0);
}
{
  const h = buildContext();
  h.ctx._respond = async () => new Response('TILE-A', { status: 200 });
  await (await h.dispatchFetch(new Request(OSM_TILE))).responded;
  await new Promise((r) => setTimeout(r, 10));
  h.ctx._respond = async () => new Response('TILE-B', { status: 200 });
  const r = await (await h.dispatchFetch(new Request(OSM_TILE, { cache: 'no-store' }))).responded;
  ok('M6: no-store su una tile -> solo rete, cache scavalcata', (await r.text()) === 'TILE-B');
  ok('M6c: la risposta no-store NON viene scritta nella cache dell\'app', h.caches._map(CACHE).size === 0);
  h.ctx._respond = async () => { throw new Error('offline'); };
  const refused = await rejects((await h.dispatchFetch(new Request(OSM_TILE, { cache: 'no-store' }))).responded);
  ok('M6b: no-store + rete in errore -> errore, la cache non si legge', refused === true);
}

{
  const h = buildContext();
  h.ctx._respond = async () => new Response('NO-STORE-TILE', { status: 200, headers: { 'cache-control': 'no-store' } });
  const r = await (await h.dispatchFetch(new Request(OSM_TILE))).responded;
  await new Promise((r2) => setTimeout(r2, 10));
  ok('M7: una risposta con Cache-Control: no-store non entra nella cache tile',
    (await r.text()) === 'NO-STORE-TILE' && h.caches._map(TILE_CACHE_NAME).size === 0);
}

// ---------- N. FRAME RADAR E SATELLITE (cache-first 30 minuti, 6 slot/famiglia) ----------
const SAT_BASE = 'https://meteoriskitalia-netizen.github.io/MeteoRisk-Light-v2/satellite/eumetsat_italy/';
{
  const h = buildContext();
  const sat = SAT_BASE + '2026-11-11T07-35-00Z.webp';
  h.ctx._respond = async () => new Response('SAT-1', { status: 200 });
  const r1 = await (await h.dispatchFetch(new Request(sat))).responded;
  ok('N1: frame satellite intercettato e servito dalla rete', (await r1.text()) === 'SAT-1');
  await new Promise((r) => setTimeout(r, 10));
  ok('N1b: il frame finisce nella cache frame, NON in quella dell\'app',
    h.caches._map(FRAME_CACHE_NAME).size === 1 && h.caches._map(CACHE).size === 0);
  const callsBefore = h.fetchCalls.length;
  h.ctx._respond = async () => new Response('SAT-2', { status: 200 });
  const r2 = await (await h.dispatchFetch(new Request(sat))).responded;
  ok('N2: frame fresco -> cache-first, nessuna richiesta di rete',
    (await r2.text()) === 'SAT-1' && h.fetchCalls.length === callsBefore);
  await expireEntry(h, FRAME_CACHE_NAME, sat, 31 * 60 * 1000);
  h.ctx._respond = async () => { throw new Error('offline'); };
  const r3 = await (await h.dispatchFetch(new Request(sat))).responded;
  ok('N3: frame scaduto (31 min) + rete assente -> copia marcata stale',
    (await r3.text()) === 'SAT-1' && r3.headers.get('X-MRI-Cache') === 'stale');
}
{
  const h = buildContext();
  h.ctx._respond = async () => new Response('OLD', { status: 200 });
  await (await h.dispatchFetch(new Request(SAT_BASE + '2026-11-11T07-20-00Z.webp'))).responded;
  h.ctx._respond = async () => new Response('NEW', { status: 200 });
  await (await h.dispatchFetch(new Request(SAT_BASE + '2026-11-11T07-35-00Z.webp'))).responded;
  await new Promise((r) => setTimeout(r, 10));
  h.ctx._respond = async () => { throw new Error('offline'); };
  const r = await (await h.dispatchFetch(new Request(SAT_BASE + '2026-11-11T07-40-00Z.webp'))).responded;
  ok('N4: slot non in cache + offline -> il frame piu\' recente della stessa sorgente, marcato stale',
    (await r.text()) === 'NEW' && r.headers.get('X-MRI-Cache') === 'stale' &&
    r.headers.get('X-MRI-Frame') === '2026-11-11T07-35-00Z',
    `frame=${r.headers.get('X-MRI-Frame')}`);
}
{
  const h = buildContext();
  const dpc = 'https://s3-prod-dpc-radar-webp-cache.s3.eu-south-1.amazonaws.com/VMI/2026/11/11/0800/6/33/23/vmi.webp';
  h.ctx._respond = async () => new Response('DPC-1', { status: 200 });
  const r = await (await h.dispatchFetch(new Request(dpc))).responded;
  ok('N5: tile radar DPC riconosciuta e messa nella cache frame', (await r.text()) === 'DPC-1');
  await new Promise((r) => setTimeout(r, 10));
  ok('N5b: la tile DPC sta nella cache frame', h.caches._map(FRAME_CACHE_NAME).size === 1);
  h.ctx._respond = async () => { throw new Error('offline'); };
  const missing = 'https://s3-prod-dpc-radar-webp-cache.s3.eu-south-1.amazonaws.com/VMI/2026/11/11/0820/6/33/23/vmi.webp';
  const r2 = await (await h.dispatchFetch(new Request(missing))).responded;
  ok('N5c: tile DPC non in cache + offline -> 504, nessun fallback ad altri slot (niente mappa chimera)',
    r2.status === 504);
}
{
  const h = buildContext();
  const slots = ['07-00-00Z', '07-05-00Z', '07-10-00Z', '07-15-00Z', '07-20-00Z', '07-25-00Z', '07-30-00Z', '07-35-00Z'];
  for (const s of slots) {
    h.ctx._respond = async () => new Response('S-' + s, { status: 200 });
    await (await h.dispatchFetch(new Request(SAT_BASE + '2026-11-11T' + s + '.webp'))).responded;
    await new Promise((r) => setTimeout(r, 5));
  }
  const keys = Array.from(h.caches._map(FRAME_CACHE_NAME).keys());
  ok('N6: oltre 6 frame per famiglia i piu\' vecchi vengono eliminati', keys.length === 6, `keys=${keys.length}`);
  ok('N6b: restano i 6 slot piu\' recenti (07:10 -> 07:35)',
    keys.every((k) => /2026-11-11T07-(10|15|20|25|30|35)-00Z\.webp$/.test(k)), JSON.stringify(keys));
}
{
  const h = buildContext();
  const dpcOf = (slot) => 'https://s3-prod-dpc-radar-webp-cache.s3.eu-south-1.amazonaws.com/VMI/2026/11/11/' + slot + '/6/33/23/vmi.webp';
  for (const s of ['0700', '0705', '0710', '0715', '0720', '0725', '0730']) {
    h.ctx._respond = async () => new Response('D-' + s, { status: 200 });
    await (await h.dispatchFetch(new Request(dpcOf(s)))).responded;
    await new Promise((r) => setTimeout(r, 5));
  }
  const keys = Array.from(h.caches._map(FRAME_CACHE_NAME).keys());
  ok('N7: la potatura e\' per famiglia: 6 slot DPC VMI indipendenti dal satellite',
    keys.length === 6 && keys.every((k) => /\/VMI\//.test(k)), JSON.stringify(keys));
}
{
  // data/radar/** (prima pass-through) ora e' coperto in stale-while-revalidate.
  const h = buildContext();
  const rl = 'https://meteoriskitalia-netizen.github.io/MeteoRisk-Light-v2/data/radar/latest.json';
  h.ctx._respond = async () => new Response('{"r":1}', { status: 200, headers: IDX_JSON });
  await (await h.dispatchFetch(new Request(rl))).responded;
  h.ctx._respond = async () => new Response('{"r":2}', { status: 200, headers: IDX_JSON });
  const b = await (await (await h.dispatchFetch(new Request(rl))).responded).text();
  await new Promise((r) => setTimeout(r, 10));
  ok('N8: data/radar/** e\' stale-while-revalidate (offline: ultima copia nota)', b === '{"r":1}', `body=${b}`);
  ok('N8b: e la copia aggiornata in background e\' la nuova', (await bodyOfCache(h, rl)) === '{"r":2}');
}

// ---------- O. PAGINA DI FALLBACK OFFLINE ----------
{
  const h = buildContext();
  h.ctx._respond = async (u) => {
    if (/offline\.html$/.test(u)) return new Response('<!doctype html><title>off</title><h1>Sei offline</h1><p>i dati potrebbero essere vecchi</p>', { status: 200, headers: { 'content-type': 'text/html' } });
    if (/manifest\.webmanifest$/.test(u)) return new Response('{}', { status: 200 });
    return new Response('<html>APP</html>', { status: 200, headers: { 'content-type': 'text/html' } });
  };
  const installEvent = { waitUntil: (p) => { installEvent.pending = p; } };
  h.fire('install', installEvent);
  await installEvent.pending;
  const app = h.caches._map(CACHE);
  for (const k of Array.from(app.keys())) if (/index\.html$/.test(k)) app.delete(k);
  h.ctx._respond = async () => { throw new Error('offline'); };
  const r = await (await h.dispatchFetch(navRequest('https://meteoriskitalia-netizen.github.io/MeteoRisk-Light-v2/pagina-non-in-cache'))).responded;
  const body = await r.text();
  ok('O1: navigazione senza rete e senza html -> pagina offline precached (non l\'errore del browser)',
    r.status === 200 && /Sei offline/.test(body) && /i dati potrebbero essere vecchi/.test(body));
  ok('O2: la pagina offline e\' marcata X-MRI-Cache: offline', r.headers.get('X-MRI-Cache') === 'offline');
  for (const k of Array.from(app.keys())) if (/offline\.html$/.test(k)) app.delete(k);
  const r2 = await (await h.dispatchFetch(navRequest('https://meteoriskitalia-netizen.github.io/MeteoRisk-Light-v2/altra-pagina'))).responded;
  ok('O3: senza offline.html -> risposta inline 503 con lo stesso testo italiano',
    r2.status === 503 && /Sei offline/.test(await r2.text()));
}
{
  const h = buildContext();
  h.ctx._respond = async () => new Response('<html>CACHED</html>', { status: 200, headers: { 'content-type': 'text/html' } });
  await (await h.dispatchFetch(navRequest('https://meteoriskitalia-netizen.github.io/MeteoRisk-Light-v2/index.html'))).responded;
  await new Promise((r) => setTimeout(r, 10));
  h.ctx._respond = async () => { throw new Error('offline'); };
  const r = await (await h.dispatchFetch(navRequest('https://meteoriskitalia-netizen.github.io/MeteoRisk-Light-v2/'))).responded;
  ok('O4: con l\'html in cache la home offline resta l\'app (la pagina offline non la sostituisce)',
    (await r.text()) === '<html>CACHED</html>');
}

// ---------- P. MANIFEST, ICONE, PAGINA OFFLINE (asset reali) ----------
{
  const manifestPath = path.join(ROOT, 'manifest.webmanifest');
  const offlinePath = path.join(ROOT, 'offline.html');
  ok('P1: manifest.webmanifest esiste', fs.existsSync(manifestPath));
  let man = null;
  try { man = JSON.parse(fs.readFileSync(manifestPath, 'utf8')); } catch (e) { }
  ok('P2: manifest JSON valido con name/short_name/start_url/display/background/theme/lang',
    !!man && typeof man.name === 'string' && man.name.length > 0 &&
    typeof man.short_name === 'string' && man.short_name.length > 0 &&
    typeof man.start_url === 'string' && man.start_url.length > 0 &&
    man.display === 'standalone' &&
    typeof man.background_color === 'string' && typeof man.theme_color === 'string' &&
    man.lang === 'it');
  const icons = (man && Array.isArray(man.icons)) ? man.icons : [];
  const iconOk = (i, size) => {
    if (!i || typeof i.src !== 'string') return false;
    const p = path.join(ROOT, i.src);
    if (!fs.existsSync(p)) return false;
    const b = fs.readFileSync(p);
    const isPng = b.length > 24 && b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47;
    return isPng && b.readUInt32BE(16) === size && b.readUInt32BE(20) === size &&
      i.sizes === size + 'x' + size && i.type === 'image/png';
  };
  ok('P3: icona 192x192 dichiarata, file PNG reale 192x192',
    iconOk(icons.find((i) => i && i.sizes === '192x192'), 192));
  ok('P4: icona 512x512 dichiarata, file PNG reale 512x512',
    iconOk(icons.find((i) => i && i.sizes === '512x512'), 512));
  ok('P5: offline.html esiste e contiene il testo italiano richiesto',
    fs.existsSync(offlinePath) &&
    /Sei offline/.test(fs.readFileSync(offlinePath, 'utf8')) &&
    /i dati potrebbero essere vecchi/.test(fs.readFileSync(offlinePath, 'utf8')));
  ok('P6: l\'html dichiara manifest e apple-touch-icon',
    /<link rel="manifest" href="manifest\.webmanifest">/.test(htmlSrc) &&
    /<link rel="apple-touch-icon" href="icons\/icon-192\.png">/.test(htmlSrc));
  ok('P7: nessuna dipendenza esterna nel SW (no Workbox / importScripts)',
    !/workbox|importScripts\s*\(/i.test(swSrc));
}

// ---------- Q. DEPLOY: manifest, offline e icone finiscono in _site ----------
const deployWfDir = path.join(ROOT, '.github', 'workflows');
for (const wf of ['deploy-pages.yml', 'radar-engine.yml', 'update-weather-data.yml']) {
  const p = path.join(deployWfDir, wf);
  const body = fs.existsSync(p) ? fs.readFileSync(p, 'utf8') : '';
  ok(`Q: ${wf} pubblica manifest/offline/icone in _site`,
    /cp manifest\.webmanifest _site\/manifest\.webmanifest/.test(body) &&
    /cp offline\.html _site\/offline\.html/.test(body) &&
    /cp icons\/icon-192\.png _site\/icons\/icon-192\.png/.test(body) &&
    /cp icons\/icon-512\.png _site\/icons\/icon-512\.png/.test(body));
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
// 1.2.4.64: il client deve accorgersi della risposta marcata stale e ricaricare i frame
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
// 1.2.4.64 - causa 2 e causa 3 lato client.
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
