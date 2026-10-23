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
 *   - data/fm2-rendered/**.json    -> stale-while-revalidate (index.json aggregati)
 *   - data/fm2/**.json (latest.json del motore .om) -> stale-while-revalidate
 *   - data/fm2-rendered/**.webp    -> cache-first + aggiornamento in background
 *   - navigazione / index.html     -> network-first con timeout 3 s, fallback alla
 *                                    copia in cache (deploy nuovo visibile subito,
 *                                    app comunque avviabile offline)
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

var CACHE_NAME = 'mri-1.2.4.16';
var MAX_ENTRIES = 1200;
var NETWORK_TIMEOUT_MS = 3000;

// I WebP e gli index arrivano da raw.githubusercontent.com: si filtra sul PATH, non
// sull'host (lo stesso SW serve sia Pages sia il fast-path raw).
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

function networkFirst(request) {
    return new Promise(function (resolve) {
        var settled = false;
        var timer = setTimeout(function () {
            if (settled) return;
            settled = true;
            cacheMatch(request).then(function (hit) { resolve(hit || fetch(request)); })
                .catch(function () { resolve(fetch(request)); });
        }, NETWORK_TIMEOUT_MS);
        fetch(request).then(function (res) {
            if (res && res.ok) {
                putCache(request, res.clone());
                var hk = homeKey();
                if (hk && isNavigation(request, new URL(request.url))) {
                    caches.open(CACHE_NAME).then(function (cache) {
                        return cache.put(new Request(hk, { method: 'GET' }), res.clone());
                    }).catch(function () { });
                }
            }
            if (!settled) { settled = true; clearTimeout(timer); resolve(res); }
        }).catch(function () {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            cacheMatch(request).then(function (hit) {
                resolve(hit || new Response('', { status: 504, statusText: 'Gateway Timeout' }));
            }).catch(function () {
                resolve(new Response('', { status: 504, statusText: 'Gateway Timeout' }));
            });
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
    if (RE_RENDERED_JSON.test(path)) { event.respondWith(staleWhileRevalidate(request)); return; }
    if (RE_LATEST_JSON.test(path)) { event.respondWith(staleWhileRevalidate(request)); return; }
    if (RE_RENDERED_WEBP.test(path)) { event.respondWith(cacheFirst(request)); return; }
    if (isNavigation(request, url)) { event.respondWith(networkFirst(request)); return; }
    // Tutto il resto (radar, satellite, phenomena, dati meteo locali, CDN JS): network.
});
