/* MeteoRisk-Light 1.2.4.77 - Service Worker (cache applicativa + offline parziale).
 *
 * SCOPO: far partire l'app anche offline/su rete lenta e togliere dalla rete i
 * payload che non cambiano (WebP pre-renderizzati e indici delle mappe), senza
 * mai interferire con i frame `.om` letti in HTTP Range.
 *
 * POLICY (dichiarata qui e verificata da scripts/tests/test_sw_cache_policy.mjs):
 *   - metodi diversi da GET        -> pass-through (nessuna cache, nessun respondWith)
 *   - richieste con header Range   -> pass-through. I frame `.om` usano 206 Partial
 *                                    Content: metterli in cache romperebbe il player
 *                                    (una Cache API non compone le parti di un 206).
 *   - INDICI AGGREGATI delle mappe, cioe' <regione>/index.json, <regione>/timeline.json
 *     e color_scales.json sotto data/fm2-rendered/
 *                                -> NETWORK-FIRST con timeout 2000 ms (1.2.4.56). Se la
 *                                    rete risponde entro il timeout vince la rete e la
 *                                    copia viene aggiornata; se e' piu' lenta (o
 *                                    fallisce) si serve la cache con l'header
 *                                    `X-MRI-Cache: stale` e la cache NON viene toccata.
 *                                    Senza rete E senza cache la fetch fallisce come una
 *                                    fetch normale: nessuna risposta vuota inventata.
 *   - data/fm2-rendered/**.json (ogni altro JSON del branch, es. manifest di run)
 *                                -> stale-while-revalidate
 *   - data/fm2/**.json (latest.json del motore .om) -> stale-while-revalidate
 *   - data/fm2-rendered/**.webp    -> cache-first + aggiornamento in background. INVARIATO:
 *                                    un frame non cambia mai (il path contiene la run)
 *   - navigazione / index.html     -> network-first con timeout 10000 ms (1.2.4.56: era
 *                                    3000 ms; con 2.294.179 B di html un timeout a 3 s
 *                                    serviva la build precedente a chi scarica sotto
 *                                    ~6,12 Mbit/s). Se la rete arriva DOPO aver servito
 *                                    la cache e la build e' diversa (ETag/Last-Modified/
 *                                    Content-Length), il SW manda un postMessage ai
 *                                    client: il banner #app-update compare senza
 *                                    bloccare il primo paint.
 *   - navigazione SENZA rete e SENZA html in cache -> pagina `offline.html` (precached
 *                                    all'install, vedi sotto) marcata `X-MRI-Cache: offline`;
 *                                    se anche quella manca, risposta inline 503 con lo
 *                                    stesso testo italiano. Mai l'errore del browser.
 *
 *   - request.cache ESPLICITO (1.2.4.56, CAUSA 1) - HA LA PRIORITA' SU TUTTO:
 *       'no-store'            -> SOLO rete. La cache non si legge nemmeno se la rete
 *                                fallisce (semantica Fetch: no-store non usa la cache).
 *       'no-cache' / 'reload' -> rete SEMPRE; se la rete fallisce (errore o 5xx) la
 *                                copia in cache e' ammessa e viene marcata stale.
 *     La risposta ok aggiorna la cache in tutti e tre i casi.
 *     ECCEZIONE: la NAVIGAZIONE non entra in questa regola (un reload puo' arrivare con
 *     cache:'reload'): resta network-first con timeout e fallback alla build in cache,
 *     altrimenti la CAUSA 3 non avrebbe rimedio.
 *     Era il difetto misurato: il client chiedeva la timeline con `no-store` e l'index
 *     con `no-cache`, ma `staleWhileRevalidate` rispondeva `if (hit) return hit;` PRIMA
 *     della rete -> un index.json vecchio in CacheStorage restava la risposta "buona",
 *     il player chiedeva gli 8 frame di una run potata, tutti 404, e la mappa restava
 *     vuota ("Nessun frame caricabile: 8 tentativi consecutivi falliti").
 *   - tutto il resto               -> pass-through (network)
 * `cache.put` SOLO per risposte `ok` e senza `Cache-Control: no-store`: un 404/500 non
 * deve diventare "la versione buona" servita per sempre. Le risposte opache (status 0)
 * NON entrano in cache: per le immagini cross-origin il SW richiede in modalita' CORS
 * (i provider usati mandano `Access-Control-Allow-Origin: *`) e, se il CORS non c'e',
 * ripiega sulla fetch originale senza mettere nulla in cache.
 *
 * ===== FASE 4 (1.2.4.77): PWA INSTALLABILE E OFFLINE PARZIALE =====
 *
 * TRE cache separate (la potatura di una non tocca le altre):
 *   CACHE_NAME   'mri-1.2.4.80'          html, manifest, offline.html, indici e WebP
 *                                        previsionali, JSON di data/radar. INVARIATA.
 *   TILE_CACHE   'mri-tiles-1.2.4.80'    tile della mappa base (OpenStreetMap)
 *   FRAME_CACHE  'mri-frames-1.2.4.80'   frame radar (DPC / RainViewer) e satellite
 *
 *   - TILE DELLA MAPPA BASE: host tile.openstreetmap.org, path /z/x/y.png
 *                                -> CACHE-FIRST con scadenza 7 giorni, max 400 voci.
 *                                    Le tile cambiano raramente; il TTL e' NOSTRO
 *                                    (il server manda `cache-control: no-cache`, che
 *                                    riguarda la cache HTTP, non questa).
 *   - FRAME RADAR (DPC S3) e SATELLITE: -> CACHE-FIRST con scadenza 30 minuti,
 *                                    massimo 6 frame (slot) per famiglia, tetto 2000 voci.
 *                                    Fresco -> cache. Scaduto -> rete; se la rete
 *                                    manca si serve la copia scaduta marcata
 *                                    `X-MRI-Cache: stale`. Per il SATELLITE, se lo slot
 *                                    richiesto non e' in cache si serve il piu' recente
 *                                    disponibile della stessa sorgente, marcato stale e
 *                                    con `X-MRI-Frame: <slot servito>`.
 *                                    I tile radar NON fanno il fallback "frame piu'
 *                                    recente": mescolare tile di istanti diversi nella
 *                                    stessa griglia produrrebbe una mappa chimera.
 *   - data/radar/** (latest/tracks/structures/geojson) -> stale-while-revalidate
 *                                    (offline: ultima copia nota; online: vince la rete).
 *   - data/stations/** NON e' in cache per scelta: dato osservato che invecchia in
 *                                    minuti e fuori dalla promessa offline di fase 4.
 *
 * PRECACHE MINIMA (install): SOLO index.html + manifest.webmanifest + offline.html
 * (misurato: 2.611.826 B + ~1 KB + ~2 KB). Nessun WebP, nessun tile, nessun frame:
 * l'install non deve scaricare decine di MB. Il precache e' TOLLERANTE: se un URL
 * fallisce (offline al primo install) l'install prosegue e la cache si riempie al
 * primo uso.
 *
 * LIMITI DI SPAZIO (dichiarati; quota tipica browser ~50-100 MB per origine):
 *   CACHE_NAME   1200 voci (invariato)  - comportamento preesistente NON toccato
 *   TILE_CACHE    400 voci  ~16 MB      - tile misurata 18-49 KB a z5, 27 KB a z6
 *   FRAME_CACHE  2000 voci  ~6 MB       - satellite misurato 57.400 B/frame, DPC 1.308 B/tile
 *   Totale nuovo massimo dichiarato ~22 MB. Le cache sono SEPARATE: la potatura dei
 *   tile non puo' sfrattare i WebP delle mappe previsionali (e viceversa).
 *   La potatura e' una LRU APPROSSIMATA (la Cache API restituisce le chiavi in ordine
 *   di inserimento, una entry riletta non torna in fondo) e il limite e' sul
 *   NUMERO di voci, non sui byte.
 *
 * VERSIONE: cambiare CACHE_NAME, TILE_CACHE e FRAME_CACHE a ogni release dell'app;
 * `activate` elimina tutte le cache che non sono in APP_CACHES.
 */
'use strict';

var CACHE_NAME = 'mri-1.2.4.80';
var TILE_CACHE = 'mri-tiles-1.2.4.80';
var FRAME_CACHE = 'mri-frames-1.2.4.80';
var APP_CACHES = [CACHE_NAME, TILE_CACHE, FRAME_CACHE];
var MAX_ENTRIES = 1200;
var MAX_TILE_ENTRIES = 400;
var MAX_FRAME_ENTRIES = 2000;
var MAX_FRAMES_PER_FAMILY = 6;
var TILE_TTL_MS = 7 * 24 * 60 * 60 * 1000;   // 7 giorni
var FRAME_TTL_MS = 30 * 60 * 1000;           // 30 minuti
var NETWORK_TIMEOUT_MS = 10000;  // navigazione (1.2.4.56: era 3000)
var INDEX_TIMEOUT_MS = 2000;     // indici aggregati (1.2.4.56)
var STALE_HEADER = 'X-MRI-Cache';
var FRAME_HEADER = 'X-MRI-Frame';
var CACHED_AT_HEADER = 'x-mri-cached-at';
var BUILD_MESSAGE = 'mri-build-changed';
var OFFLINE_PAGE = 'offline.html';
var PRECACHE_PATHS = ['index.html', 'manifest.webmanifest', 'offline.html'];

// I WebP e gli index arrivano da raw.githubusercontent.com: si filtra sul PATH, non
// sull'host (lo stesso SW serve sia Pages sia il fast-path raw).
var RE_INDEX_JSON = /\/data\/fm2-rendered\/(?:.+\/)?(?:index|timeline)\.json$/i;
var RE_COLOR_SCALES_JSON = /\/data\/fm2-rendered\/color_scales\.json$/i;
var RE_RENDERED_JSON = /\/data\/fm2-rendered\/.+\.json$/i;
var RE_LATEST_JSON = /\/data\/fm2\/.+\/latest\.json$/i;
var RE_RENDERED_WEBP = /\/data\/fm2-rendered\/.+\.webp$/i;

// Fase 4 - tile e frame.
var OSM_HOST_RE = /(^|\.)tile\.openstreetmap\.org$/;
var RE_OSM_TILE = /^\/\d+\/\d+\/\d+\.png$/;
var DPC_HOST = 's3-prod-dpc-radar-webp-cache.s3.eu-south-1.amazonaws.com';
var RE_DPC_TILE = /^\/([A-Za-z0-9_]+)\/(\d{4})\/(\d{2})\/(\d{2})\/(\d{4})\/\d+\/\d+\/\d+\/[a-z0-9_]+\.webp$/;
var RAINVIEWER_HOST_RE = /(^|\.)rainviewer\.com$/;
var RE_RAINVIEWER_TILE = /^\/v2\/radar\/([^/]+)\/256\/\d+\/\d+\/\d+\/\d+\/[^/]+\.png$/;
var RE_SAT_FRAME = /\/satellite\/([^/]+)\/([^/]+)\.webp$/i;
var RE_SAT_MANIFEST = /\/satellite\/manifest\.json$/i;
var RE_RADAR_DATA = /\/data\/radar\/.+\.(?:json|geojson)$/i;

function pathOf(url) {
    try { return url.pathname; } catch (e) { return ''; }
}

function hostOf(url) {
    try { return String(url.hostname || '').toLowerCase(); } catch (e) { return ''; }
}

function isNavigation(request, url) {
    if (request.mode === 'navigate') return true;
    if (request.destination === 'document') return true;
    return /(^|\/)index\.html$/i.test(pathOf(url)) || pathOf(url) === '/';
}

function shouldSkip(request) {
    if (!request || request.method !== 'GET') return true;      // POST/PUT: mai in cache
    if (request.headers && request.headers.get('range')) return true; // 206 Range (.om)
    return false;
}

// Indirizzo canonico della home nello scope del SW: serve a far partire l'app
// offline anche se il browser chiede '/' invece di '/index.html'.
function homeKey() {
    try { return new URL('index.html', self.registration.scope).href; } catch (e) { return null; }
}

function scopeOrigin() {
    try { return new URL(self.registration.scope).origin; } catch (e) { return ''; }
}

function offlineKey() {
    try { return new URL(OFFLINE_PAGE, self.registration.scope).href; } catch (e) { return null; }
}

// Contatore di entry + potatura delle piu' vecchie (LRU approssimata, vedi sopra).
function trimCacheTo(cache, maxEntries) {
    return cache.keys().then(function (keys) {
        if (keys.length <= maxEntries) return 0;
        var excess = keys.length - maxEntries;
        var chain = Promise.resolve();
        for (var i = 0; i < excess; i++) {
            (function (k) { chain = chain.then(function () { return cache.delete(k); }); })(keys[i]);
        }
        return chain.then(function () { return excess; });
    });
}

function trimCache(cache) {
    return trimCacheTo(cache, MAX_ENTRIES);
}

// put SOLO per risposte ok (206/404/500/opache/no-store non entrano in cache).
function isNoStoreResponse(res) {
    try {
        return /\bno-store\b/i.test(String((res && res.headers && res.headers.get('cache-control')) || ''));
    } catch (e) { return false; }
}

function putCache(request, response) {
    if (!response || !response.ok || response.status === 206 || isNoStoreResponse(response)) return Promise.resolve(false);
    // Le tile e i frame hanno una cache DEDICATA: una richiesta con cache:'no-store'
    // arriva qui dalla regola generale e non deve finire nella cache dell'app.
    if (isTileOrFrameUrl(request.url)) return Promise.resolve(false);
    return caches.open(CACHE_NAME).then(function (cache) {
        return cache.put(request, response).then(function () { return trimCache(cache); }).then(function () { return true; });
    }).catch(function () { return false; });
}

function cacheMatch(request) {
    return caches.open(CACHE_NAME).then(function (cache) {
        return cache.match(request, { ignoreSearch: true }).then(function (hit) {
            if (hit) return hit;
            var hk = homeKey();
            if (hk && isNavigation(request, new URL(request.url))) return cache.match(hk, { ignoreSearch: true });
            return undefined;
        });
    });
}

function staleWhileRevalidate(request) {
    return cacheMatch(request).then(function (hit) {
        var network = fetch(request).then(function (res) {
            if (res && res.ok) putCache(request, res.clone());
            return res;
        }).catch(function () { return null; });
        // In cache: risposta immediata, aggiornamento in background.
        if (hit) return hit;
        return network.then(function (res) {
            return res || new Response('', { status: 504, statusText: 'Gateway Timeout' });
        });
    });
}

function cacheFirst(request) {
    return cacheMatch(request).then(function (hit) {
        // Aggiornamento in background anche quando si serve la copia in cache.
        var refresh = fetch(request).then(function (res) {
            if (res && res.ok) putCache(request, res.clone());
            return res;
        }).catch(function () { return null; });
        if (hit) return hit;
        return refresh.then(function (res) {
            return res || new Response('', { status: 504, statusText: 'Gateway Timeout' });
        });
    });
}

// 1.2.4.56 - copia di cache servita come fallback: si marca, cosi' il client sa che
// l'indice puo' essere vecchio e riprogramma UNA rivalidazione ravvicinata. Il corpo e'
// lo stesso (stream) e la cache NON viene toccata.
function markWith(hit, name, value) {
    try {
        var headers = new Headers(hit.headers);
        headers.set(name, value);
        return new Response(hit.body, { status: hit.status, statusText: hit.statusText, headers: headers });
    } catch (e) { return hit; }
}

function markStale(hit) {
    return markWith(hit, STALE_HEADER, 'stale');
}

// 1.2.4.56 - CAUSA 1: rispetto di `request.cache`, che ha la PRIORITA' su ogni regola.
// no-store            -> solo rete (la cache non si legge, nemmeno in errore)
// no-cache / reload   -> rete sempre; in errore (o 5xx) la cache e' ammessa, marcata stale
function cacheModeOf(request) {
    try { return String((request && request.cache) || 'default'); } catch (e) { return 'default'; }
}
function forcesNetwork(request) {
    var m = cacheModeOf(request);
    return m === 'no-store' || m === 'no-cache' || m === 'reload';
}
function cacheFallbackAllowed(request) {
    return cacheModeOf(request) !== 'no-store';
}
function networkRespectingCacheMode(request) {
    return fetch(request).then(function (res) {
        if (res && res.ok) putCache(request, res.clone());
        if (res && res.status >= 500 && cacheFallbackAllowed(request)) {
            return cacheMatch(request).then(function (hit) { return hit ? markStale(hit) : res; });
        }
        return res;
    }, function (err) {
        if (!cacheFallbackAllowed(request)) return Promise.reject(err);
        return cacheMatch(request).then(function (hit) {
            if (hit) return markStale(hit);
            return Promise.reject(err);
        }, function () { return Promise.reject(err); });
    });
}

// 1.2.4.56 - CAUSA 3: identificativo ECONOMICO della build servita (nessun hash del
// corpo da 2 MB). GitHub Pages manda ETag; in mancanza Last-Modified/Content-Length.
function buildIdOf(res) {
    try {
        if (!res || !res.headers) return null;
        return res.headers.get('etag') || res.headers.get('last-modified') || res.headers.get('content-length');
    } catch (e) { return null; }
}
function notifyBuildChanged() {
    try {
        if (!self.clients || !self.clients.matchAll) return;
        self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
            for (var i = 0; i < list.length; i++) {
                try { list[i].postMessage({ type: BUILD_MESSAGE }); } catch (e) { }
            }
        }).catch(function () { });
    } catch (e) { }
}

// 1.2.4.56 - NETWORK-FIRST DEGLI INDICI (index.json / timeline.json / color_scales.json).
// - rete entro INDEX_TIMEOUT_MS -> risposta di rete (e cache aggiornata);
// - rete piu' lenta              -> cache SUBITO, marcata stale; la fetch in volo continua
//                                   e aggiorna la cache per la richiesta successiva;
// - rete fallita                 -> cache, marcata stale;
// - niente rete e niente cache   -> la promise RIFIUTA (errore di rete vero, nessuna
//                                   risposta vuota inventata).
function networkFirstIndex(request) {
    return new Promise(function (resolve, reject) {
        var settled = false;
        var network = fetch(request).then(function (res) {
            if (res && res.ok) putCache(request, res.clone());
            return { res: res };
        }, function (err) { return { err: err }; });
        function serveCache(error) {
            cacheMatch(request).then(function (hit) {
                if (hit) resolve(markStale(hit));
                else reject(error || new Error('network-first: nessuna risposta'));
            }, function () {
                reject(error || new Error('network-first: cache non disponibile'));
            });
        }
        var timer = setTimeout(function () {
            if (settled) return;
            settled = true;
            cacheMatch(request).then(function (hit) {
                if (hit) resolve(markStale(hit));
                else network.then(function (r) { if (r.res) resolve(r.res); else reject(r.err); });
            }, function () {
                network.then(function (r) { if (r.res) resolve(r.res); else reject(r.err); });
            });
        }, INDEX_TIMEOUT_MS);
        network.then(function (r) {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            if (r.res) resolve(r.res);
            else serveCache(r.err);
        });
    });
}

// FASE 4 - pagina offline: prima la copia precached (asset reale), poi una risposta
// inline minimale come ultima guardia. Mai l'errore di rete del browser.
var INLINE_OFFLINE_HTML = '<!DOCTYPE html><html lang="it"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width, initial-scale=1">' +
    '<title>MeteoRisk Italia - offline</title></head><body style="margin:0;background:#0b1220;color:#e2e8f0;' +
    'font-family:system-ui,Segoe UI,Arial,sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center">' +
    '<main style="max-width:32rem;padding:2rem;text-align:center"><h1 style="font-size:1.5rem">Sei offline</h1>' +
    '<p style="color:#94a3b8">i dati potrebbero essere vecchi</p>' +
    '<p><a href="./index.html" style="color:#38bdf8">Apri MeteoRisk</a></p></main></body></html>';

function offlineResponse() {
    var key = offlineKey();
    var lookup = key
        ? caches.open(CACHE_NAME).then(function (cache) { return cache.match(key, { ignoreSearch: true }); }).catch(function () { return undefined; })
        : Promise.resolve(undefined);
    return lookup.then(function (hit) {
        if (hit) return markWith(hit, STALE_HEADER, 'offline');
        return new Response(INLINE_OFFLINE_HTML, {
            status: 503,
            statusText: 'Offline',
            headers: { 'content-type': 'text/html; charset=utf-8', 'X-MRI-Cache': 'offline' }
        });
    });
}

function networkFirst(request, event) {
    // 1.2.4.56 - CAUSA 3 (timeout 10 s) + avviso di build nuova quando la rete arriva
    // DOPO aver servito la cache. Il primo paint non si blocca: la cache esce al timeout.
    var servedCached = null;
    return new Promise(function (resolve) {
        var settled = false;
        var network = fetch(request).then(function (res) {
            if (res && res.ok) {
                putCache(request, res.clone());
                var hk = homeKey();
                if (hk && isNavigation(request, new URL(request.url))) {
                    caches.open(CACHE_NAME).then(function (cache) {
                        return cache.put(new Request(hk, { method: 'GET' }), res.clone());
                    }).catch(function () { });
                }
            }
            return res;
        }, function () { return null; });
        // La fetch in volo deve SOPRAVVIVERE alla risposta servita dalla cache: senza
        // waitUntil il browser puo' terminare il SW e la risposta tardiva (quella che
        // aggiorna la cache e avvisa della build nuova) non arriverebbe mai. MISURATO:
        // senza waitUntil la cache restava alla build vecchia e il banner non compariva.
        if (event && typeof event.waitUntil === 'function') {
            try { event.waitUntil(network.then(function () { })); } catch (e) { }
        }
        var timer = setTimeout(function () {
            if (settled) return;
            settled = true;
            cacheMatch(request).then(function (hit) {
                servedCached = hit || null;
                if (hit) { resolve(hit); return; }
                network.then(function (res) { resolve(res || offlineResponse()); });
            }, function () {
                network.then(function (res) { resolve(res || offlineResponse()); });
            });
        }, NETWORK_TIMEOUT_MS);
        network.then(function (res) {
            if (!settled) {
                settled = true;
                clearTimeout(timer);
                if (res) { resolve(res); return; }
                cacheMatch(request).then(function (hit) {
                    resolve(hit || offlineResponse());
                }, function () {
                    resolve(offlineResponse());
                });
                return;
            }
            // Risposta tardiva: se la build servita dalla cache e' DIVERSA, avvisa i client
            // (il banner #app-update esiste gia'). Nessun confronto se una delle due e'
            // senza ETag/Last-Modified/Content-Length: meglio nessun avviso che un falso.
            try {
                if (res && res.ok && servedCached && isNavigation(request, new URL(request.url))) {
                    var oldId = buildIdOf(servedCached);
                    var newId = buildIdOf(res);
                    if (oldId && newId && oldId !== newId) notifyBuildChanged();
                }
            } catch (e) { }
        });
    });
}

// ===================== FASE 4: TILE E FRAME =====================

// TTL: la copia porta l'istante di scrittura. Una copia senza timestamp e' trattata
// come scaduta (non si inventa una freschezza che non conosciamo).
function ageOf(res) {
    try {
        var raw = res.headers.get(CACHED_AT_HEADER);
        var t = raw ? Number(raw) : NaN;
        if (!isFinite(t) || t <= 0) return Infinity;
        return Math.max(0, Date.now() - t);
    } catch (e) { return Infinity; }
}

function stampedCopy(res) {
    try {
        var headers = new Headers(res.headers);
        headers.set(CACHED_AT_HEADER, String(Date.now()));
        return new Response(res.body, { status: res.status, statusText: res.statusText, headers: headers });
    } catch (e) { return res; }
}

function isCrossOrigin(url) {
    var origin = scopeOrigin();
    return !!origin && url.origin !== origin;
}

// Un'immagine cross-origin richiesta da <img> arriva con mode 'no-cors': la fetch
// originale darebbe una risposta OPACA (status 0, non cacheabile). Se il provider manda
// CORS (OSM, DPC S3 e RainViewer lo mandano: verificato) si richiede in mode 'cors' e si
// ottiene un 200 reale. Se il CORS non c'e' si ripiega sulla richiesta originale, che
// funziona ma NON viene messa in cache.
function fetchCacheable(request, url) {
    if (isCrossOrigin(url) && request.mode === 'no-cors') {
        var init = { mode: 'cors', credentials: 'omit', cache: 'no-store' };
        try {
            if (request.referrer && request.referrer !== 'about:client') init.referrer = request.referrer;
        } catch (e) { }
        return fetch(request.url, init).catch(function () { return fetch(request); });
    }
    // cache:'no-store' sulla fetch di rete: il TTL che vale e' quello del SW. Senza,
    // una tile "scaduta" per il SW poteva tornare 200 dalla cache HTTP (che per OSM
    // e' no-cache e per S3 e' immutable) e il marker stale non sarebbe mai comparso.
    return fetch(request.url, { cache: 'no-store', credentials: 'omit' });
}

// True per le risorse che hanno una cache DEDICATA (tile OSM, tile radar DPC,
// tile RainViewer, frame satellite): non vanno mai scritte nella CACHE_NAME.
function isTileOrFrameUrl(rawUrl) {
    var u;
    try { u = new URL(rawUrl); } catch (e) { return false; }
    var path = u.pathname;
    var host = hostOf(u);
    if (OSM_HOST_RE.test(host) && RE_OSM_TILE.test(path)) return true;
    if (host === DPC_HOST && RE_DPC_TILE.test(path)) return true;
    if (RAINVIEWER_HOST_RE.test(host) && RE_RAINVIEWER_TILE.test(path)) return true;
    return RE_SAT_FRAME.test(path);
}

// Famiglia e slot di un frame, ricavati dall'URL (nessun metadato da mantenere):
//   DPC        /VMI/2026/11/11/0800/6/33/23/vmi.webp  -> dpc:VMI | 202611110800
//   RainViewer /v2/radar/<hash>/256/6/33/23/2/1_1.png -> rainviewer | <hash>
//   Satellite  /satellite/<source>/<slot>.webp        -> sat:<source> | <slot>
function frameInfo(rawUrl) {
    var u;
    try { u = new URL(rawUrl); } catch (e) { return null; }
    var path = u.pathname;
    var host = hostOf(u);
    var m;
    if (host === DPC_HOST) {
        m = RE_DPC_TILE.exec(path);
        if (m) {
            var product = m[1].toUpperCase();
            var slot = m[2] + m[3] + m[4] + m[5];
            return { family: 'dpc:' + product, slot: slot, group: 'dpc:' + product + '|' + slot };
        }
    }
    if (RAINVIEWER_HOST_RE.test(host)) {
        m = RE_RAINVIEWER_TILE.exec(path);
        if (m) return { family: 'rainviewer', slot: m[1], group: 'rainviewer|' + m[1] };
    }
    m = RE_SAT_FRAME.exec(path);
    if (m) return { family: 'sat:' + m[1], slot: m[2], group: 'sat:' + m[1] + '|' + m[2] };
    return null;
}

// Tiene i MAX_FRAMES_PER_FAMILY gruppi (slot) usati piu' di recente per ogni famiglia.
// La Cache API restituisce le chiavi in ordine di INSERIMENTO: l'indice piu' alto e'
// il gruppo piu' recente. E' una LRU approssimata, dichiarata.
function pruneFrameGroups(cache, maxGroups) {
    return cache.keys().then(function (keys) {
        var families = new Map();
        keys.forEach(function (k, i) {
            var info = frameInfo(k.url);
            if (!info) return;
            var groups = families.get(info.family);
            if (!groups) { groups = new Map(); families.set(info.family, groups); }
            groups.set(info.group, i);
        });
        var doomed = [];
        families.forEach(function (groups) {
            var list = Array.from(groups.entries()).sort(function (a, b) { return b[1] - a[1]; });
            for (var i = maxGroups; i < list.length; i++) doomed.push(list[i][0]);
        });
        if (!doomed.length) return 0;
        var chain = Promise.resolve();
        keys.forEach(function (k) {
            var info = frameInfo(k.url);
            if (info && doomed.indexOf(info.group) !== -1) {
                chain = chain.then(function () { return cache.delete(k); });
            }
        });
        return chain.then(function () { return doomed.length; });
    });
}

function storeTile(request, res) {
    if (!res || !res.ok || res.status === 206 || isNoStoreResponse(res)) return Promise.resolve(false);
    return caches.open(TILE_CACHE).then(function (cache) {
        return cache.put(request, stampedCopy(res)).then(function () {
            return trimCacheTo(cache, MAX_TILE_ENTRIES);
        });
    }).catch(function () { return false; });
}

function storeFrame(request, res) {
    if (!res || !res.ok || res.status === 206 || isNoStoreResponse(res)) return Promise.resolve(false);
    return caches.open(FRAME_CACHE).then(function (cache) {
        return cache.put(request, stampedCopy(res)).then(function () {
            return pruneFrameGroups(cache, MAX_FRAMES_PER_FAMILY);
        }).then(function () {
            return trimCacheTo(cache, MAX_FRAME_ENTRIES);
        });
    }).catch(function () { return false; });
}

// Il frame piu' recente della famiglia ancora in cache (solo per immagini singole:
// satellite). Ordina per slot ISO; se lo slot non e' ordinabile usa l'ultimo inserito.
function newestInFamily(cache, family) {
    return cache.keys().then(function (keys) {
        var best = null;
        var bestSlot = null;
        var last = null;
        keys.forEach(function (k) {
            var info = frameInfo(k.url);
            if (!info || info.family !== family) return;
            last = k;
            if (info.slot && (bestSlot === null || info.slot > bestSlot)) {
                bestSlot = info.slot;
                best = k;
            }
        });
        var pick = best || last;
        if (!pick) return undefined;
        var picked = frameInfo(pick.url);
        return cache.match(pick, { ignoreSearch: true }).then(function (hit) {
            return hit ? { hit: hit, slot: (picked && picked.slot) || '' } : undefined;
        });
    });
}

// CACHE-FIRST CON SCADENZA.
//   fresco            -> cache, nessuna rete
//   scaduto/assente   -> rete (e cache aggiornata)
//   rete in errore/non-ok -> copia scaduta marcata stale; per il satellite, se manca
//                            anche quella, il frame piu' recente della famiglia.
function ttlCacheFirst(request, ttlMs, cacheName, store, opts) {
    var url;
    try { url = new URL(request.url); } catch (e) { return fetch(request); }
    return caches.open(cacheName).then(function (cache) {
        return cache.match(request, { ignoreSearch: true }).then(function (hit) {
            if (hit && ageOf(hit) <= ttlMs) return hit;
            var network = fetchCacheable(request, url).then(function (res) {
                if (res && res.ok && res.status !== 206) {
                    return store(request, res.clone()).then(function () { return res; });
                }
                return res;
            }).catch(function () { return null; });
            return network.then(function (res) {
                if (res && res.ok) return res;
                if (hit) return markStale(hit);
                if (opts && opts.family) {
                    return newestInFamily(cache, opts.family).then(function (found) {
                        if (found) return markWith(markStale(found.hit), FRAME_HEADER, found.slot);
                        return res || new Response('', { status: 504, statusText: 'Gateway Timeout' });
                    });
                }
                return res || new Response('', { status: 504, statusText: 'Gateway Timeout' });
            });
        });
    });
}

function tileCacheFirst(request) {
    return ttlCacheFirst(request, TILE_TTL_MS, TILE_CACHE, storeTile, null);
}

function frameCacheFirst(request) {
    var info = frameInfo(request.url);
    // Il fallback "frame piu' recente della famiglia" vale SOLO per il satellite:
    // un frame satellite e' una singola immagine. Per le tile radar (DPC/RainViewer)
    // servirebbe una tile di un altro istante nella stessa griglia = mappa chimera.
    var fam = (info && /^sat:/.test(info.family)) ? info.family : null;
    return ttlCacheFirst(request, FRAME_TTL_MS, FRAME_CACHE, storeFrame, fam ? { family: fam } : null);
}

function precacheUrls() {
    var scope = self.registration.scope;
    return PRECACHE_PATHS.map(function (p) {
        try { return new URL(p, scope).href; } catch (e) { return p; }
    });
}

// Precache MINIMA e TOLLERANTE: 3 URL, nessun addAll (un solo 404 non deve far
// fallire l'install), nessun WebP/tile/frame.
function precacheShell() {
    return caches.open(CACHE_NAME).then(function (cache) {
        return Promise.all(precacheUrls().map(function (u) {
            return fetch(u, { cache: 'reload' }).then(function (res) {
                if (res && res.ok && res.status !== 206) return cache.put(u, res.clone());
                return null;
            }).catch(function () { return null; });
        }));
    }).catch(function () { return null; });
}

self.addEventListener('install', function (event) {
    // 1.2.4.77: precache minima (html + manifest + pagina offline). I WebP e i frame
    // NON vengono scaricati qui: l'install deve restare leggera e non fallire offline.
    event.waitUntil(precacheShell().then(function () {
        return self.skipWaiting();
    }));
});

self.addEventListener('activate', function (event) {
    event.waitUntil(caches.keys().then(function (names) {
        return Promise.all(names.map(function (name) {
            if (APP_CACHES.indexOf(name) !== -1) return null;
            return caches.delete(name);   // cache di release precedenti
        }));
    }).then(function () {
        return self.clients.claim();
    }));
});

self.addEventListener('fetch', function (event) {
    var request = event.request;
    if (shouldSkip(request)) return;                    // Range / non-GET: nessuna intercettazione
    var url;
    try { url = new URL(request.url); } catch (e) { return; }
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return;
    var path = pathOf(url);
    var nav = isNavigation(request, url);
    // 1.2.4.56 - CAUSA 1: request.cache esplicito -> rete obbligatoria, PRIMA di ogni
    // regola (indici compresi). E' il fix del difetto misurato: la timeline chiesta con
    // `no-store` tornava dalla cache e il player usava una run gia' potata.
    // ECCEZIONE DICHIARATA: la NAVIGAZIONE resta sulla sua policy (network-first con
    // timeout e fallback alla build in cache). Un reload puo' arrivare con cache:'reload':
    // applicargli il rispetto di request.cache toglierebbe il fallback, cioe' la ragione
    // per cui esiste il timeout della navigazione (CAUSA 3).
    if (forcesNetwork(request) && !nav) { event.respondWith(networkRespectingCacheMode(request)); return; }
    // 1.2.4.56: PRIMA gli indici aggregati (network-first con timeout), poi il resto dei
    // JSON del branch (stale-while-revalidate), poi i WebP (cache-first, invariato).
    if (RE_INDEX_JSON.test(path) || RE_COLOR_SCALES_JSON.test(path)) { event.respondWith(networkFirstIndex(request)); return; }
    if (RE_RENDERED_JSON.test(path)) { event.respondWith(staleWhileRevalidate(request)); return; }
    if (RE_LATEST_JSON.test(path)) { event.respondWith(staleWhileRevalidate(request)); return; }
    if (RE_RENDERED_WEBP.test(path)) { event.respondWith(cacheFirst(request)); return; }
    // FASE 4 - tile della mappa base: cache-first 7 giorni.
    if (OSM_HOST_RE.test(hostOf(url)) && RE_OSM_TILE.test(path)) { event.respondWith(tileCacheFirst(request)); return; }
    // FASE 4 - frame radar (DPC / RainViewer) e satellite: cache-first 30 minuti.
    if (hostOf(url) === DPC_HOST && RE_DPC_TILE.test(path)) { event.respondWith(frameCacheFirst(request)); return; }
    if (RAINVIEWER_HOST_RE.test(hostOf(url)) && RE_RAINVIEWER_TILE.test(path)) { event.respondWith(frameCacheFirst(request)); return; }
    if (RE_SAT_FRAME.test(path)) { event.respondWith(frameCacheFirst(request)); return; }
    // FASE 4 - manifest satellite (piccolo, come un indice) e JSON/GeoJSON del radar.
    if (RE_SAT_MANIFEST.test(path)) { event.respondWith(networkFirstIndex(request)); return; }
    if (RE_RADAR_DATA.test(path)) { event.respondWith(staleWhileRevalidate(request)); return; }
    if (nav) { event.respondWith(networkFirst(request, event)); return; }
    // Tutto il resto (stations, phenomena, dati meteo locali, CDN JS): network.
});
