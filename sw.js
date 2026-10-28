/* MeteoRisk-Light 1.2.4.16 - Service Worker (cache applicativa).
 *
 * SCOPO: far partire l'app anche offline/su rete lenta e togliere dalla rete i
 * payload che non cambiano (WebP pre-renderizzati e indici delle mappe), senza
 * mai interferire con i frame `.om` letti in HTTP Range.
 *
 * POLICY (una sola, dichiarata qui e verificata da scripts/tests/test_sw_cache_policy.mjs):
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
 * `cache.put` SOLO per risposte `ok`: un 404/500 non deve diventare "la versione
 * buona" servita per sempre.
 *
 * LIMITE DI SPAZIO: contatore di entry della cache; oltre MAX_ENTRIES (1200) si
 * cancellano le chiavi piu' vecchie. La Cache API restituisce le chiavi in ordine
 * di INSERIMENTO: e' quindi una LRU APPROSSIMATA (una entry riletta non torna in
 * fondo), NON una LRU esatta, e il limite e' sul NUMERO di voci, non sui byte.
 *
 * VERSIONE: cambiare CACHE_NAME a ogni release dell'app; `activate` elimina tutte
 * le cache con nome diverso, cosi' un deploy nuovo non lascia dati vecchi appesi.
 */
'use strict';

var CACHE_NAME = 'mri-1.2.4.58';
var MAX_ENTRIES = 1200;
var NETWORK_TIMEOUT_MS = 10000;  // navigazione (1.2.4.56: era 3000)
var INDEX_TIMEOUT_MS = 2000;     // indici aggregati (1.2.4.56)
var STALE_HEADER = 'X-MRI-Cache';
var BUILD_MESSAGE = 'mri-build-changed';

// I WebP e gli index arrivano da raw.githubusercontent.com: si filtra sul PATH, non
// sull'host (lo stesso SW serve sia Pages sia il fast-path raw).
var RE_INDEX_JSON = /\/data\/fm2-rendered\/(?:.+\/)?(?:index|timeline)\.json$/i;
var RE_COLOR_SCALES_JSON = /\/data\/fm2-rendered\/color_scales\.json$/i;
var RE_RENDERED_JSON = /\/data\/fm2-rendered\/.+\.json$/i;
var RE_LATEST_JSON = /\/data\/fm2\/.+\/latest\.json$/i;
var RE_RENDERED_WEBP = /\/data\/fm2-rendered\/.+\.webp$/i;

function pathOf(url) {
    try { return url.pathname; } catch (e) { return ''; }
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

// Contatore di entry + potatura delle piu' vecchie (LRU approssimata, vedi sopra).
function trimCache(cache) {
    return cache.keys().then(function (keys) {
        if (keys.length <= MAX_ENTRIES) return 0;
        var excess = keys.length - MAX_ENTRIES;
        var chain = Promise.resolve();
        for (var i = 0; i < excess; i++) {
            (function (k) { chain = chain.then(function () { return cache.delete(k); }); })(keys[i]);
        }
        return chain.then(function () { return excess; });
    });
}

// put SOLO per risposte ok (206/404/500 non entrano in cache).
function putCache(request, response) {
    if (!response || !response.ok || response.status === 206) return Promise.resolve(false);
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
function markStale(hit) {
    try {
        var headers = new Headers(hit.headers);
        headers.set(STALE_HEADER, 'stale');
        return new Response(hit.body, { status: hit.status, statusText: hit.statusText, headers: headers });
    } catch (e) { return hit; }
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
                network.then(function (res) { resolve(res || fetch(request)); });
            }, function () {
                network.then(function (res) { resolve(res || fetch(request)); });
            });
        }, NETWORK_TIMEOUT_MS);
        network.then(function (res) {
            if (!settled) {
                settled = true;
                clearTimeout(timer);
                if (res) { resolve(res); return; }
                cacheMatch(request).then(function (hit) {
                    resolve(hit || new Response('', { status: 504, statusText: 'Gateway Timeout' }));
                }, function () {
                    resolve(new Response('', { status: 504, statusText: 'Gateway Timeout' }));
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

self.addEventListener('install', function (event) {
    // Nessun precache pesante: l'html (2 MB) e i WebP NON vengono scaricati qui.
    // skipWaiting: la nuova versione del SW prende il controllo senza attendere
    // la chiusura di tutte le schede.
    event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', function (event) {
    event.waitUntil(caches.keys().then(function (names) {
        return Promise.all(names.map(function (name) {
            if (name === CACHE_NAME) return null;
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
    if (nav) { event.respondWith(networkFirst(request, event)); return; }
    // Tutto il resto (radar, satellite, phenomena, dati meteo locali, CDN JS): network.
});
