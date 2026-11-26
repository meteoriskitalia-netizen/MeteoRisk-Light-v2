#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — nowcast_field.py (novembre 2026)

NOWCAST DEL CAMPO DI PIOGGIA — non e' un'estensione di `forecast.py`.

Perche' esiste
--------------
`forecast.py` sposta il CONTORNO di una struttura convettiva e per farlo
dipende dal moto della cella (tracking). Il 2026-11-24 le strutture erano 0:
senza un oggetto da tracciare quel layer non produce nulla. Questo modulo
sposta il CAMPO DI PIOGGIA (SRI, mm/h) e non da' un nome a nessuna cella:
l'ingresso e' la griglia, l'uscita e' la stessa griglia trasportata.

Vincoli rispettati (dichiarati anche nell'output)
-------------------------------------------------
- NON usa il VMI convertito con una Z-R: la sorgente e' il prodotto DPC SRI
  (Surface Rainfall Intensity, mm/h Float32, stessa griglia 1200x1400 a 1 km).
- NON usa ICON-2I / `nwp_motion.py`.
- NON usa il tracking ungherese / `tracking.py`: nessuna dipendenza dal
  risultato di `tracking.py`, nessuna identita' di cella.
- NESSUNA crescita di intensita': solo trasporto (semi-Lagrangiano). Il
  campionamento bilineare e' una combinazione convessa, quindi il massimo del
  campo previsto non puo' superare il massimo del campo osservato.
- Un vettore sopra `SPEED_CAP_KMH` (110 km/h) NON viene azzerato: viene
  SOSTITUITO con la mediana del vicinato dei vettori sotto cap (0 solo se non
  esiste nessun vicino valido, e il caso e' contato). Un moto non fisico non
  viene passato al trasporto e non ferma la pioggia.

Flusso ottico
-------------
Implementazione PROPRIA e deterministica di Lucas-Kanade piramidale denso
(numpy + scipy.ndimage), perche' nel runner NON sono installati ne' pySTEPS ne'
OpenCV (`scripts/radar_engine/requirements.txt` = numpy/rasterio/scipy/pyproj/
h5py/matplotlib) e aggiungere OpenCV sarebbe una dipendenza binaria pesante
proprio sul percorso del run radar. Il prompt ammette Lucas-Kanade: qui e'
Lucas-Kanade con finestra gaussiana e piramide (2x, 4x), risolto a 2 km e
interpolato a 1 km. Costo misurato nel report; nessun peso da scaricare.

Il flusso e' stimato fra frame CONSECUTIVI (passo 5 min) sulle ultime
`FLOW_PAIRS` coppie e combinato con la MEDIANA per componente (robusta a una
coppia anomala). Una coppia che non riesce a stimare un vettore (fuori dalla
`texture`, cioe' nessun gradiente: l'eco e' comparsa dopo) vale NaN, NON 0:
con la mediana di (0, 0, x) il risultato sarebbe 0 e quel pezzo di pioggia
non si muoverebbe mai. I buchi residui vengono riempiti con la mediana del
vicinato (`fill_motion`) e i pixel senza nessun vicino valido restano fermi
ma sono contati e dichiarati (`no_neighbor_motion`).

Ingresso
--------
Ultimi `FRAMES_WANTED` (6) fotogrammi SRI, passo 5 minuti. I raster sono
riusati dal bundle DPC gia' scaricato da `fetch.fetch_product_bundle`
(`bundle.dpc_bundle`, SRI trattenuto su tutti gli slot: costo aggiuntivo ZERO).
Se il bundle non e' disponibile si usa `fetch.fetch_frames(product="SRI")`,
che passa da `source_dpc.get_latest_product` / `download_product`: nessun
secondo downloader.

Se i fotogrammi utili sono < 3 il file viene scritto lo stesso con
`available: false` e `null_reason` (`sri_unavailable` se il fetch e' fallito,
`sri_frames_lt_3` se ne sono arrivati meno di 3). Il run del radar NON fallisce
mai per questo modulo: `main.py` lo chiama dentro try/except.

Uscita
------
`data/radar/nowcast.json` (schema `meteorisk.nowcast_field/1`) + **15 WebP**
georeferenziati in EPSG:3857 a PASSO CRESCENTE: +5..+45 ogni 5 minuti, +60,
+75, +90 ogni 15, +120, +150, +180 ogni 30 (`nowcast_lead005.webp` ...
`nowcast_lead180.webp`), con alpha=0 fuori dal dominio radar. I frame sono
CUMULATIVI: il +180 e' il +5 avanzato 36 volte (semi-Lagrangiano a passi di
5 minuti), non un trasporto indipendente ne' un salto secco. I tre lead
**ufficiali di verifica** restano 15/30/60 e portano `official: true`; gli
altri servono al player e sono dichiarati come tali. Ogni lead porta il suo
`step_min` (passo pubblicato) e `reliability` dall'affidabilita' MISURATA.

Il JSON porta bbox/risoluzione delle immagini e la legenda in mm/h. Il WebP e'
stato scelto DOPO aver misurato il JSON con i campi grezzi: 15 campi float su
1200x1400 sarebbero ~100 MB per run, le 15 immagini costano ~125 kB in totale
sullo stesso caso (misurato, v. report).

La maschera del dominio e' `valid_mask` del frame SRI piu' recente (la stessa
usata dal resto del motore: -9999 = fuori copertura). Il campo non viene mai
esteso fuori da quella maschera: nessuna geometria inventata sopra il mare.
"""

import datetime as _dt
import json
import math
import os
import tempfile
import warnings

import numpy as np

from . import clock as clock_mod
from . import fetch as fetch_mod
from . import models
from . import nowcast_ensemble as ensemble_mod
from . import orography as orography_mod

SCHEMA = "meteorisk.nowcast_field/1"
SOURCE = "DPC_SRI"
PRODUCT_TYPE = "SRI"

FRAMES_WANTED = 6               # ultimi 6 fotogrammi (25 minuti di storia)
STEP_MIN = 5                    # cadenza misurata del prodotto SRI (PT5M)
# 15 FRAME A PASSO CRESCENTE fino a +3 ore (come Meteo&Radar/WetterOnline):
#   +5..+45 ogni 5 min (9 frame), +60/+75/+90 ogni 15 min (3 frame),
#   +120/+150/+180 ogni 30 min (3 frame).
# Il TRASPORTO resta CUMULATIVO a passi da 5 minuti: il frame a +N usa N/5
# avanzamenti da 5 minuti (il +180 e' il +5 ripetuto 36 volte, non un salto).
LEADS_MIN = tuple(range(5, 46, 5)) + (60, 75, 90, 120, 150, 180)
# Passo pubblicato di ogni frame = distanza dal frame precedente.
STEP_MIN_BY_LEAD = {
    5: 5, 10: 5, 15: 5, 20: 5, 25: 5, 30: 5, 35: 5, 40: 5, 45: 5,
    60: 15, 75: 15, 90: 15,
    120: 30, 150: 30, 180: 30,
}
# Verifica ufficiale: solo questi tre (invariata rispetto alla 1.3.0.71).
# I frame oltre +60 sono per il player e NON entrano nel verdetto ufficiale.
OFFICIAL_LEADS = (15, 30, 60)
# Oltre questo orizzonte il frame e' etichettato "affidabilita' bassa" nel
# client: la misura dell'affidabilita' per lead e' in reliability_by_lead.
LOW_RELIABILITY_OVER_MIN = 90
FLOW_PAIRS = 3                  # coppie consecutive usate per il flusso
SPEED_CAP_KMH = 110.0           # sopra: vettore SOSTITUITO dal vicinato
MOTION_FILL_MAX_RADIUS = 10     # px - raggio massimo del riempimento
MOTION_SMOOTH_PX = 3            # mediana 3x3 dentro la pioggia (0 = spenta)
RAIN_MIN_MMH = 0.05             # sotto: nessuna pioggia disegnata (0.0 e' valido)
DOMAIN_MASK_SOURCE = "latest_sri_valid_mask"
MOTION = "optical_flow"
MOTION_ALGORITHM = "lucas_kanade_pyramidal"
# ESTENSIONE DEL MOTO A TUTTO IL DOMINIO (fix del "muro", 2026-11-25).
# Il trasporto campiona il campo NEL PUNTO DI ARRIVO (`_warp(out, -u, -v)`):
# un vettore 0 fuori dall'eco congela il bordo d'attacco e cancella ogni eco
# piu' piccola dello spostamento (misurato: area al 65% a +60 min, fronte
# fermo a +0,0 km). Il moto viene quindi esteso al pixel VALIDO PIU' VICINO e
# poi levigato (la divergenza del campo esteso duplica massa nel
# ricampionamento: senza smoothing +14% in 60 min). La maschera del dominio
# resta applicata SOLO all'output, non al moto.
MOTION_EXTEND_SIGMA_PX = 25
MOTION_EXTEND_METHOD = "nearest_valid_gaussian"
# TARATURA DICHIARATA (NON una calibrazione): il flusso ottico misura la
# velocita' del PATTERN, piu' alta di quella della massa. Su 3 ancore reali
# (2026-11-24T19:55Z, 2026-11-25T09:30Z, 2026-11-25T15:00Z) lo spostamento
# misurato del centroide e' 25-33% sotto il vettore stimato; il fattore 0,8 e'
# stato fissato SU QUELLE 3 ANCORE e poi confermato sui 24 casi del backtest
# (12 giornate x 2 ancore, campione DISGIUNTO): CSI@60 0,4108 -> 0,4228,
# FAR@60 0,348 -> 0,335. Non e' una calibrazione fisica: un fattore unico per
# tutti i regimi. Da rivedere quando ci sara' un campione piu' ampio.
MOTION_SPEED_FACTOR = 0.8

OUTPUT_CRS = "EPSG:3857"
OUTPUT_RES_M = 1500             # risoluzione delle immagini (misurata: v. report)
WEBP_QUALITY = 80
IMAGE_NAMES = {lead: "nowcast_lead%03d.webp" % lead for lead in LEADS_MIN}
VERIFICATION_FILE = "nowcast_verification.json"
# Affidabilita' per orizzonte: MISURATA dal backtest
# `scripts/validation/nowcast_reliability_backtest.py` e scritta accanto al
# nowcast. Il motore la incorpora in `nowcast.json` (reliability_by_lead) senza
# ricalcolare nulla: un file assente -> null con motivo, niente numeri inventati.
RELIABILITY_FILE = "nowcast_reliability.json"

# Legenda (mm/h -> colore). STESSA SCALA DEL RADAR SRI DEL CLIENT: i sei stop
# sono copiati da `DPC_SRI_STOPS` di mri-light-1.3.0.77.html (righe 10983-10990,
# tabella Universal Blue: blu -> azzurro -> ciano -> giallo -> arancio -> rosso)
# con gli stessi RGB, cosi' "pioggia prevista" e "pioggia osservata" hanno lo
# stesso significato cromatico. La lista va anche nel JSON (`legend`).
# CODE: sotto 1 mm/h il colore e' quello del primo stop; sopra 100 mm/h quello
# dell'ultimo (il radar SRI si ferma a 100). L'alpha del nowcast resta una
# rampa separata (0 sotto RAIN_MIN_MMH, 200/255 da ALPHA_FULL_MMH): il client
# radar SRI, invece, rende trasparenti i pixel grezzi < 25/255 (~9,8 mm/h).
PALETTE = (
    (1.0, (0, 85, 136)),
    (5.0, (0, 163, 224)),
    (10.0, (136, 221, 238)),
    (20.0, (255, 238, 0)),
    (50.0, (255, 170, 0)),
    (100.0, (193, 0, 0)),
)
ALPHA_FULL_MMH = 0.20           # da qui in su alpha pieno (200/255)
ALPHA_MAX = 200

# Soglie di valutazione (CSI/POD/FAR) e regola di decisione dichiarata.
VERIFY_THRESHOLDS_MMH = (0.5, 2.0, 5.0)
VERIFY_DECISION_LEADS = (15, 30, 60)
VERIFY_DECISION_THRESHOLDS = (0.5, 2.0)
VERIFY_MIN_CASES = 5            # sotto: beats_persistence = null
VERIFY_PUBLISH_MIN_CASES = 20   # sotto: nessun punteggio pubblicato
VERIFY_MIN_RAIN_PIXELS = 10000  # pixel di pioggia osservata accumulati

LIMITS = (
    "Trasporto puro: nessuna crescita/decadimento dell'intensita'. Il flusso "
    "ottico stima il moto a 2 km e lo interpola a 1 km; non e' un Doppler. "
    "Oltre 30 minuti l'affidabilita' cala: la posizione dipende da un moto "
    "medio, non dalla vita della cella. Il moto e' definito dove c'e' eco ed "
    "esteso al pixel valido piu' vicino su tutto il dominio: il trasporto "
    "sposta l'eco ma non crea pioggia oltre quella osservata all'ancora. La "
    "velocita' e' ridotta del fattore dichiarato in `motion_speed_factor` "
    "(0,8: taratura su 3 ancore, confermata su 24 casi disgiunti; non e' una "
    "calibrazione fisica). La massa totale non cresce (la crescita numerica "
    "del ricampionamento viene rimossa; il decadimento reale non e' "
    "modellato). La maschera e' quella del dominio radar: fuori non viene "
    "disegnato nulla."
)


# ---------------------------------------------------------------------------
# utilita' di base
# ---------------------------------------------------------------------------
def _iso(ms):
    if ms is None:
        return None
    return _dt.datetime.fromtimestamp(int(ms) / 1000.0,
                                      _dt.timezone.utc).strftime(
                                          "%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_text(path, text):
    """Scrittura atomica (tmp nella stessa dir + os.replace), JSON validato."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    json.loads(text)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".nowcast-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _rain_array(rd):
    """(campo float32 con 0 fuori dominio, maschera dominio bool).

    SRI: 0.0 = assenza di pioggia VALIDA, -9999 = fuori copertura. Il dominio
    e' esattamente `valid_mask` (la maschera gia' usata dal resto del motore):
    qui non si aggiunge nessuna geometria."""
    data = np.asarray(rd.data, dtype=np.float32)
    valid = np.asarray(rd.valid_mask, dtype=bool)
    out = np.where(valid, data, 0.0).astype(np.float32)
    out[~np.isfinite(out)] = 0.0
    np.maximum(out, 0.0, out=out)
    return out, valid


def _intensity(field):
    """Trasformazione per il flusso ottico: sqrt(mm/h).

    La pioggia e' molto asimmetrica (0.05 .. 70 mm/h): la radice comprime il
    range e mantiene il contrasto sui nuclei deboli, senza inventare valori.
    """
    return np.sqrt(field, dtype=np.float32)


# ---------------------------------------------------------------------------
# flusso ottico denso (Lucas-Kanade piramidale)
# ---------------------------------------------------------------------------
def _downsample(a, w):
    """Media 2x2 pesata sui pixel validi -> (array, maschera)."""
    h, wd = a.shape
    H = (h // 2) * 2
    W = (wd // 2) * 2
    if H < 2 or W < 2:
        return a, w
    aa = a[:H, :W].reshape(H // 2, 2, W // 2, 2)
    ww = w[:H, :W].reshape(H // 2, 2, W // 2, 2).astype(np.float32)
    num = (aa * ww).sum(axis=(1, 3), dtype=np.float32)
    den = ww.sum(axis=(1, 3), dtype=np.float32)
    out = np.zeros_like(num, dtype=np.float32)
    np.divide(num, den, out=out, where=den > 0)
    return out, den > 0.5


def _gauss(a, sigma):
    from scipy import ndimage
    return ndimage.gaussian_filter(a, sigma, mode="nearest", truncate=3.0)


def _warp(a, u, v, order=1, prefilter=True):
    """Campionamento di `a` in (r+v, c+u). Ritorna (out, inside).

    `order=1` e' il bilineare usato dal flusso ottico (combinazione convessa:
    nessun overshoot). `order=3` e' il cubico, disponibile per il trasporto
    a spostamento composto (una sola interpolazione per lead).
    `prefilter=False` quando `a` sono gia' i coefficienti di spline: il
    pre-filtro si fa UNA volta per tutti i lead (misurato: 15 campionamenti
    cubici 10,9 s -> 8,5 s sull'intera griglia DPC)."""
    from scipy import ndimage
    h, w = a.shape
    rr = np.arange(h, dtype=np.float32)[:, None] + v
    cc = np.arange(w, dtype=np.float32)[None, :] + u
    out = ndimage.map_coordinates(a, [rr, cc], order=int(order),
                                  mode="constant", cval=0.0,
                                  prefilter=bool(prefilter)).astype(np.float32)
    if int(order) > 1:
        # il cubico ha lobi negativi: un'intensita' negativa non esiste.
        np.maximum(out, 0.0, out=out)
    inside = (rr >= 0) & (rr <= h - 1) & (cc >= 0) & (cc <= w - 1)
    return out, inside


def _lk_level(i0, i1, valid, u, v, sigma, iters, max_inc):
    """Un livello della piramide: itera Lucas-Kanade con warp incrementale."""
    u = u.copy()
    v = v.copy()
    gy, gx = np.gradient(i0)
    for _ in range(int(iters)):
        i1w, inside = _warp(i1, u, v)
        vw, _ = _warp(valid.astype(np.float32), u, v)
        ok = valid & inside & (vw > 0.99)
        it = (i1w - i0) * ok
        gxx = _gauss((gx * gx) * ok, sigma)
        gyy = _gauss((gy * gy) * ok, sigma)
        gxy = _gauss((gx * gy) * ok, sigma)
        gxt = _gauss((gx * it), sigma)
        gyt = _gauss((gy * it), sigma)
        det = gxx * gyy - gxy * gxy
        good = det > 1e-6
        du = np.zeros_like(det, dtype=np.float32)
        dv = np.zeros_like(det, dtype=np.float32)
        np.divide(-(gyy * gxt - gxy * gyt), det, out=du, where=good)
        np.divide(-(gxx * gyt - gxy * gxt), det, out=dv, where=good)
        np.clip(du, -max_inc, max_inc, out=du)
        np.clip(dv, -max_inc, max_inc, out=dv)
        u += du
        v += dv
    return u, v


def _upsample_flow(u, v, shape):
    """Riporta (u, v) alla forma `shape` (bilineare), SCALANDO i valori.

    Il vettore e' espresso in PIXEL della griglia corrente: passando a una
    griglia piu' fine lo stesso spostamento fisico vale `ratio` volte piu'
    pixel. Senza questo fattore il moto risulta diviso per 4 (misurato sul
    test sintetico: 6 px -> 1,45 px)."""
    from scipy import ndimage
    sy = float(shape[0]) / u.shape[0]
    sx = float(shape[1]) / u.shape[1]
    uu = ndimage.zoom(u, (sy, sx), order=1, mode="nearest") * np.float32(sx)
    vv = ndimage.zoom(v, (sy, sx), order=1, mode="nearest") * np.float32(sy)
    # ritaglio/padding esatto (zoom puo' sbagliare di 1 px per arrotondamento)
    out = []
    for a in (uu, vv):
        b = np.zeros(shape, dtype=np.float32)
        h = min(shape[0], a.shape[0])
        w = min(shape[1], a.shape[1])
        b[:h, :w] = a[:h, :w]
        out.append(b)
    return out[0], out[1]


def estimate_flow(prev, curr, valid, smooth_sigma=2.0):
    """Flusso denso (u, v) in PIXEL per frame da `prev` a `curr`.

    `prev`/`curr` sono campi mm/h (non negativi); `valid` il dominio comune.
    Ritorna (u, v) float32 alla risoluzione piena. Zero dove il dato non
    permette una stima (nessun gradiente / fuori dominio): nessun vettore
    inventato.
    """
    i0 = _intensity(prev)
    i1 = _intensity(curr)
    # piramide 4x, 2x (il livello pieno non serve: il campo e' liscio a 1 km)
    levels = []
    a0, a1, mask = i0, i1, np.asarray(valid, dtype=bool)
    for _ in range(2):
        a0, m0 = _downsample(a0, mask)
        a1, m1 = _downsample(a1, mask)
        mask = m0 & m1
        levels.append((a0, a1, mask))
    # levels: [quarter, half] (dal piu' grossolano al piu' fine)
    a0c, a1c, wc = levels[0]
    u = np.zeros_like(a0c, dtype=np.float32)
    v = np.zeros_like(a0c, dtype=np.float32)
    u, v = _lk_level(a0c, a1c, wc, u, v, sigma=4.0, iters=8, max_inc=2.0)
    a0h, a1h, wh = levels[1]
    u, v = _upsample_flow(u, v, a0h.shape)
    u, v = _lk_level(a0h, a1h, wh, u, v, sigma=3.0, iters=5, max_inc=1.5)
    u, v = _upsample_flow(u, v, prev.shape)
    if smooth_sigma:
        u = _gauss(u, float(smooth_sigma))
        v = _gauss(v, float(smooth_sigma))
    u = np.where(valid, u, 0.0).astype(np.float32)
    v = np.where(valid, v, 0.0).astype(np.float32)
    return u, v


def _median_stack(arrays):
    """Mediana per componente su una lista di array (robusta a 1 coppia).

    Tenuta per compatibilita': il moto usa `_nan_median_stack`, che ignora le
    non-stime invece di farle votare 0."""
    if len(arrays) == 1:
        return arrays[0]
    return np.median(np.stack(arrays, axis=0), axis=0).astype(np.float32)


def _nan_median_stack(arrays):
    """Mediana per componente con le non-stime come NaN.

    Il risultato e' NaN solo se TUTTE le coppie mancano: e' il marcatore
    "vettore mancante" (da riempire), NON uno zero (che ferma la pioggia)."""
    if len(arrays) == 1:
        return np.asarray(arrays[0], dtype=np.float32).copy()
    stack = np.stack([np.asarray(a, dtype=np.float32) for a in arrays], axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(stack, axis=0)
    return med.astype(np.float32)


def estimate_motion(frames, warnings):
    """Moto (u, v) per frame, mediana delle ultime `FLOW_PAIRS` coppie.

    Una coppia che non riesce a stimare un vettore vale NaN (non 0): la
    mediana di (0, 0, x) sarebbe 0 e il pixel non si muoverebbe mai. Il
    risultato e' NaN dove NESSUNA coppia ha una stima; il riempimento dal
    vicinato lo fa `compute` con `fill_motion`, sulla maschera di pioggia."""
    pairs = min(FLOW_PAIRS, len(frames) - 1)
    if pairs < 1:
        return None
    us, vs, used, dts = [], [], 0, []
    for k in range(pairs, 0, -1):
        prev, vprev, tprev = frames[-1 - k]
        curr, vcurr, tcurr = frames[-k]
        dt_s = (int(tcurr) - int(tprev)) / 1000.0
        if dt_s <= 0 or dt_s > 15 * 60:
            # buco temporale: il vettore non e' riferibile al passo di 5 min
            warnings.append("nowcast: coppia a {} s esclusa dal moto".format(
                dt_s))
            continue
        both = vprev & vcurr
        # il flusso si stima dove almeno uno dei due frame ha pioggia:
        # sulle aree secche il gradiente e' nullo e non c'e' nulla da muovere.
        texture = both & ((prev > 0.02) | (curr > 0.02))
        if float(texture.sum()) < 50:
            warnings.append("nowcast: coppia senza pioggia, esclusa dal moto")
            continue
        u, v = estimate_flow(prev, curr, texture)
        # non-stima = NaN; in pioggia il moto vero non e' mai 0 e lo 0
        # fermerebbe l'eco (era il 71% dei pixel fermi misurati il 24/11).
        measured = np.isfinite(u) & np.isfinite(v) & ((u != 0.0) | (v != 0.0))
        # normalizza al PASSO NOMINALE di 5 minuti (px/frame -> px/5min)
        scale = (STEP_MIN * 60.0) / float(dt_s)
        us.append(np.where(measured, u * scale, np.nan).astype(np.float32))
        vs.append(np.where(measured, v * scale, np.nan).astype(np.float32))
        dts.append(dt_s)
        used += 1
    if not used:
        return None
    return {
        "u": _nan_median_stack(us),
        "v": _nan_median_stack(vs),
        "pairs": used,
        "pair_dt_s": sorted(set(dts)),
        "algorithm": MOTION_ALGORITHM,
    }


# ---------------------------------------------------------------------------
# cap 110 km/h e trasporto semi-Lagrangiano
# ---------------------------------------------------------------------------
def pixel_speed_kmh(u, v, pixel_km, step_s):
    """Modulo della velocita' (km/h) di un vettore in pixel/step."""
    px_km = np.hypot(np.asarray(u, dtype=np.float32),
                     np.asarray(v, dtype=np.float32)) * float(pixel_km)
    return px_km * 3600.0 / float(step_s)


def _window_median(u, v, rows, cols, size):
    """Mediana nan-aware di (u, v) nella finestra size x size attorno a
    (rows, cols). NaN dove la finestra non contiene nessun valore finito."""
    k = int(size) // 2
    out = []
    for a in (u, v):
        ap = np.pad(np.asarray(a, dtype=np.float32), k, mode="constant",
                    constant_values=np.nan)
        vals = [ap[rows + dy, cols + dx]
                for dy in range(int(size)) for dx in range(int(size))]
        stack = np.stack(vals, axis=0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            out.append(np.nanmedian(stack, axis=0).astype(np.float32))
    return out[0], out[1]


def fill_motion(u, v, missing, valid, max_radius=MOTION_FILL_MAX_RADIUS):
    """Riempie `missing` con la MEDIANA del vicinato di `valid`.

    Il raggio cresce di 1 px per passo fino a `max_radius` (~10 px). I pixel
    che restano senza nessun vicino valido diventano NaN (marcatore
    "mancante"), NON 0: lo zero fermerebbe la pioggia e non e' distinguibile
    da un moto nullo. Ritorna (u, v, {"filled", "unfilled"})."""
    u = np.array(u, dtype=np.float32, copy=True)
    v = np.array(v, dtype=np.float32, copy=True)
    known = np.asarray(valid, dtype=bool) & np.isfinite(u) & np.isfinite(v)
    todo = np.asarray(missing, dtype=bool).copy() & ~known
    filled = 0
    radius = 1
    while todo.any() and radius <= int(max_radius):
        rr, cc = np.nonzero(todo)
        if rr.size == 0:
            break
        mu, mv = _window_median(np.where(known, u, np.nan),
                                np.where(known, v, np.nan), rr, cc,
                                2 * radius + 1)
        ok = np.isfinite(mu) & np.isfinite(mv)
        if ok.any():
            u[rr[ok], cc[ok]] = mu[ok]
            v[rr[ok], cc[ok]] = mv[ok]
            got = np.zeros(todo.shape, dtype=bool)
            got[rr[ok], cc[ok]] = True
            known |= got
            todo &= ~got
            filled += int(ok.sum())
        radius += 1
    if todo.any():
        u[todo] = np.nan
        v[todo] = np.nan
    return u, v, {"filled": filled, "unfilled": int(todo.sum())}


def cap_and_replace(u, v, pixel_km, step_s, cap_kmh=SPEED_CAP_KMH, rain=None):
    """Vettori sopra `cap_kmh` -> MEDIANA del vicinato (non 0).

    `rain` limita i vicini accettati ai pixel che trasportano pioggia: un
    vettore sopra cap sul bordo dell'eco non viene "aggiustato" con lo zero
    di un'area secca. I pixel sopra cap senza nessun vicino valido restano
    NaN (il chiamante li conta e li dichiara). Ritorna
    (u, v, {"over_cap", "replaced", "unfilled", "over_mask"})."""
    u = np.asarray(u, dtype=np.float32)
    v = np.asarray(v, dtype=np.float32)
    speed = pixel_speed_kmh(u, v, pixel_km, step_s)
    finite = np.isfinite(u) & np.isfinite(v)
    over = finite & (speed > float(cap_kmh))
    info = {"over_cap": int(over.sum()), "replaced": 0, "unfilled": 0,
            "over_mask": over}
    if not info["over_cap"]:
        return u, v, info
    valid = finite & ~over & ((u != 0.0) | (v != 0.0))
    if rain is not None:
        valid = valid & np.asarray(rain, dtype=bool)
    cu, cv, fill = fill_motion(u, v, missing=over, valid=valid)
    info["replaced"] = int(fill["filled"])
    info["unfilled"] = int(fill["unfilled"])
    return cu, cv, info


def cap_speed(u, v, pixel_km, step_s, cap_kmh=SPEED_CAP_KMH):
    """Compatibilita': sostituisce i vettori sopra cap e ritorna
    (u, v, n_over_cap). 0 solo se non esiste nessun vicino valido.

    `n_over_cap` e' il numero di vettori che superavano il cap (non piu'
    "azzerati": il nome del vecchio contatore resta nel JSON per i lettori)."""
    cu, cv, info = cap_and_replace(u, v, pixel_km, step_s, cap_kmh)
    return (np.nan_to_num(cu, nan=0.0), np.nan_to_num(cv, nan=0.0),
            info["over_cap"])


def semi_lagrangian(field, u, v, steps, domain):
    """Trasporto puro: `field(x) <- field(x - u)` per `steps` passi.

    Il campionamento e' bilineare (combinazione convessa: nessun overshoot) e
    il risultato fuori dominio e' 0. Nessun termine di crescita/decadimento."""
    out = np.asarray(field, dtype=np.float32).copy()
    for _ in range(int(steps)):
        smp, inside = _warp(out, -u, -v)
        out = np.where(inside & domain, smp, 0.0).astype(np.float32)
    return out


# ---------------------------------------------------------------------------
# ingresso: ultimi 6 fotogrammi SRI
# ---------------------------------------------------------------------------
def frames_from_bundle(bundle):
    """Raster SRI (slot, campo, maschera) gia' scaricati dal bundle DPC.

    `fetch.fetch_product_bundle` trattiene SRI su TUTTI gli slot
    (`retain_non_reference`): qui non si scarica nulla. Un bundle senza SRI
    (fase 2 disabilitata/fallita) ritorna [] e il chiamante fa il fallback.
    """
    bundle = bundle or {}
    entries = bundle.get("entries") or {}
    out = {}
    for slot, by_name in entries.items():
        e = (by_name or {}).get("sri")
        rd = (e or {}).get("raster")
        if rd is None:
            continue
        data, valid = _rain_array(rd)
        ts = int(rd.time_ms if rd.time_ms is not None else slot)
        out[ts] = (data, valid, slot)
    frames = []
    for ts in sorted(out):
        data, valid, slot = out[ts]
        frames.append((data, valid, ts))
    return frames[-FRAMES_WANTED:]


def fetch_sri_frames(config, clock, warnings):
    """Fallback: ultimi 6 SRI con il downloader ESISTENTE del motore.

    Passa da `fetch.fetch_frames` -> `source_dpc.get_latest_product` /
    `download_product`: nessun secondo downloader. Non solleva: un guasto di
    rete e' `[]` + warning (il modulo scrive available:false)."""
    period_s = int((config.get("source") or {}).get("period_s") or 300)
    try:
        rasters, fwarn, _ts = fetch_mod.fetch_frames(
            config, product=PRODUCT_TYPE, max_frames=FRAMES_WANTED,
            anchor_ms=clock.anchor_ms(period_s))
    except models.SourceError as exc:
        warnings.append(f"nowcast: SRI fetch failed ({exc})")
        return []
    except Exception as exc:                    # rete/parsing imprevisto
        warnings.append("nowcast: SRI fetch failed ({}:{})".format(
            exc.__class__.__name__, exc))
        return []
    warnings.extend(w for w in (fwarn or []) if w)
    frames = []
    for rd in rasters:
        data, valid = _rain_array(rd)
        frames.append((data, valid, int(rd.time_ms)))
    frames.sort(key=lambda f: f[2])
    return frames[-FRAMES_WANTED:]


def collect_frames(bundle, config, clock, warnings):
    """(frames, origine). Prima il bundle (costo zero), poi il fallback."""
    frames = frames_from_bundle(getattr(bundle, "dpc_bundle", None))
    if len(frames) >= 3:
        return frames, "dpc_bundle"
    fallback = fetch_sri_frames(config, clock, warnings)
    if len(fallback) > len(frames):
        return fallback, "fetch_frames"
    return frames, "dpc_bundle"


# ---------------------------------------------------------------------------
# griglia: riproiezione EPSG:3857 + immagini
# ---------------------------------------------------------------------------
def _output_grid(rd, res_m=OUTPUT_RES_M):
    """Griglia EPSG:3857 (rettangolo) che contiene il dominio del raster.

    Il GeoTIFF SRI e' una Transverse Mercator: i suoi bordi NON sono un
    rettangolo lon/lat (misurato: 14 gradi di larghezza a sud, 16 a nord).
    Un overlay Leaflet con un bbox lon/lat sarebbe sbagliato di decine di km,
    quindi le immagini sono riproiettate in Mercator e il JSON dichiara bounds
    e risoluzione in EPSG:3857."""
    from pyproj import Transformer
    from rasterio.transform import from_origin

    rows, cols = int(rd.rows), int(rd.cols)
    # non bastano i 4 angoli: in Transverse Mercator i meridiani sono curvi e
    # il bordo puo' sporgere di qualche pixel a meta' lato. Si campionano i
    # bordi ogni 5% (84 punti) per un envelope corretto.
    pts = []
    for i in range(21):
        r = (rows - 1) * i / 20.0
        c = (cols - 1) * i / 20.0
        pts.extend([(r, 0), (r, cols - 1), (0, c), (rows - 1, c)])
    lonlat = [rd.pixel_to_lonlat(r, c) for r, c in pts]
    tr = Transformer.from_crs("EPSG:4326", OUTPUT_CRS, always_xy=True)
    xs, ys = tr.transform([p[0] for p in lonlat], [p[1] for p in lonlat])
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    res = float(res_m)
    width = max(1, int(math.ceil((maxx - minx) / res)))
    height = max(1, int(math.ceil((maxy - miny) / res)))
    dst_transform = from_origin(minx, maxy, res, res)
    return {
        "crs": OUTPUT_CRS,
        "bounds": [round(minx, 3), round(miny, 3),
                   round(maxx, 3), round(maxy, 3)],
        "width": width, "height": height, "resolution_m": res,
        "dst_transform": dst_transform,
        "bbox_lonlat": [round(min(p[0] for p in lonlat), 5),
                        round(min(p[1] for p in lonlat), 5),
                        round(max(p[0] for p in lonlat), 5),
                        round(max(p[1] for p in lonlat), 5)],
    }


def reproject_to_grid(field, valid, rd, grid):
    """Campo mm/h sulla griglia EPSG:3857; fuori dominio -> NaN."""
    from rasterio.warp import reproject, Resampling
    src = np.where(valid, field, np.nan).astype(np.float32)
    dst = np.full((grid["height"], grid["width"]), np.nan, dtype=np.float32)
    reproject(source=src, destination=dst,
              src_transform=rd.transform, src_crs=rd.crs,
              dst_transform=grid["dst_transform"], dst_crs=grid["crs"],
              src_nodata=np.nan, dst_nodata=np.nan,
              resampling=Resampling.bilinear)
    dst[~np.isfinite(dst)] = np.nan
    dst[dst < 0.0] = 0.0
    return dst


def _rgba(field):
    """mm/h -> RGBA (alpha 0 sotto RAIN_MIN_MMH e fuori dominio)."""
    v = np.asarray(field, dtype=np.float32)
    shape = v.shape
    vv = np.where(np.isfinite(v), v, 0.0)
    out = np.zeros((shape[0], shape[1], 4), dtype=np.uint8)
    nodes = np.array([p[0] for p in PALETTE], dtype=np.float32)
    cols = np.array([p[1] for p in PALETTE], dtype=np.float32)
    for ch in range(3):
        # ROUND HALF UP: il client usa Math.round (104.5 -> 105). Il troncamento
        # di astype(uint8) dava 104 a 2 mm/h, cioe' 1/255 di differenza su un
        # punto medio esatto fra due stop.
        out[:, :, ch] = np.clip(np.floor(
            np.interp(vv, nodes, cols[:, ch]) + 0.5), 0, 255).astype(np.uint8)
    ramp = np.clip((vv - RAIN_MIN_MMH) / (ALPHA_FULL_MMH - RAIN_MIN_MMH),
                   0.0, 1.0)
    alpha = (ramp * ALPHA_MAX).astype(np.uint8)
    alpha[~np.isfinite(v)] = 0
    out[:, :, 3] = alpha
    return out


def write_webp(path, field, quality=WEBP_QUALITY):
    """Scrive il campo come WebP RGBA. Ritorna i byte scritti."""
    try:
        from PIL import Image
    except ImportError as exc:      # dichiarato, non silenzioso
        raise RuntimeError("pillow_missing") from exc
    img = Image.fromarray(np.ascontiguousarray(_rgba(field)))
    tmp = path + ".tmp"
    img.save(tmp, format="WEBP", quality=int(quality), method=4)
    os.replace(tmp, path)
    return os.path.getsize(path)


# ---------------------------------------------------------------------------
# calcolo
# ---------------------------------------------------------------------------
def _lead_stats(field, valid, pixel_km):
    """Statistiche pubblicabili del campo previsto (mm/h)."""
    if field is None:
        return None
    f = np.asarray(field, dtype=np.float32)
    ok = np.isfinite(f) & np.asarray(valid, dtype=bool)
    if not ok.any():
        return {"max_mmh": None, "mean_mmh": None, "rain_pixels": 0,
                "rain_area_km2": 0.0, "area_by_threshold_km2": {}}
    vals = f[ok]
    area = float(pixel_km) ** 2
    return {
        "max_mmh": round(float(vals.max()), 2),
        "mean_mmh": round(float(vals.mean()), 4),
        "rain_pixels": int((vals > RAIN_MIN_MMH).sum()),
        "rain_area_km2": round(float((vals > RAIN_MIN_MMH).sum()) * area, 1),
        "area_by_threshold_km2": {
            str(t): round(float((vals >= t).sum()) * area, 1)
            for t in VERIFY_THRESHOLDS_MMH},
    }


def _pixel_km(rd):
    """Lato del pixel in km dal transform (|a|, |e| in metri)."""
    try:
        px = math.sqrt(abs(float(rd.transform.a) * float(rd.transform.e))) / 1000.0
    except (AttributeError, TypeError, ValueError):
        return 1.0
    return px if px > 0 else 1.0


def _unavailable(reason, clock, extra=None):
    payload = {
        "schema": SCHEMA,
        "available": False,
        "null_reason": reason,
        "source": SOURCE,
        "source_slot": None,
        "generated_at": clock.now_iso(),
        "unit": "mm/h",
        "motion": MOTION,
        "motion_algorithm": MOTION_ALGORITHM,
        "speed_cap_kmh": SPEED_CAP_KMH,
        "speed_cap_mode": "replaced_by_neighbor_median",
        "vectors_zeroed_by_cap": None,
        "vectors_over_cap_total": None,
        "vectors_replaced_by_cap": None,
        "vectors_unfilled_by_cap": None,
        "motion_filled_pixels": None,
        "motion_unfilled_pixels": None,
        "motion_null_reason": None,
        "motion_smoothing_px": MOTION_SMOOTH_PX,
        "motion_extended": False,
        "motion_extended_method": MOTION_EXTEND_METHOD,
        "motion_extended_sigma_px": MOTION_EXTEND_SIGMA_PX,
        "motion_pixels_known": None,
        "motion_pixels_from_zero": None,
        "motion_extend_null_reason": None,
        "motion_speed_factor": MOTION_SPEED_FACTOR,
        "mass_growth_capped": True,
        "mass_cap_steps": None,
        "frames_used": 0,
        "leads": [],
        "beats_persistence": None,
        "reliability_by_lead": None,
        "low_reliability_over_min": LOW_RELIABILITY_OVER_MIN,
        "verification": {"status": "not_evaluated",
                         "beats_persistence": None,
                         "null_reason": "nowcast_unavailable"},
        "limits": LIMITS,
    }
    if extra:
        payload.update(extra)
    return payload


def load_verification(out_dir):
    """Risultato del replay (file piccolo, scritto dallo script di validazione).

    Se il file non c'e' il verdetto resta null con motivo: NON si inventa un
    `beats_persistence`."""
    path = os.path.join(out_dir, VERIFICATION_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"status": "not_evaluated", "beats_persistence": None,
                "null_reason": "verification_file_missing",
                "expected_file": VERIFICATION_FILE}
    if not isinstance(data, dict):
        return {"status": "not_evaluated", "beats_persistence": None,
                "null_reason": "verification_file_invalid"}
    # Schema 2 (archivio/verifica): la SERIE completa (una riga per slot x lead
    # x soglia) resta nel file su disco. Incorporarla in nowcast.json, che viene
    # committato ogni 10 minuti, lo farebbe crescere con la serie (misurato:
    # 67 kB al primo run, fino a ~9 MB con la retention di 7 giorni). Qui si
    # tiene solo il riassunto che serve al client (beats + conteggi).
    series = data.get("series")
    if isinstance(series, dict):
        data = dict(data)
        data.pop("series", None)
        counts = series.get("counts") or {}
        data["series_summary"] = {
            "rows": counts.get("rows"),
            "slots": counts.get("slots"),
            "retention_days": series.get("retention_days"),
            "full_series_in": VERIFICATION_FILE,
        }
    return data


def resolve_motion(motion, latest, domain, pixel_km, step_s):
    """Campo di moto EFFETTIVO applicato al trasporto.

    (1) vettori sopra cap -> mediana del vicinato (mai 0 se esiste un vicino
    valido, cercato dentro la pioggia); (2) buchi dentro la pioggia ->
    mediana del vicinato, raggio fino a `MOTION_FILL_MAX_RADIUS`; (3) mediana
    `MOTION_SMOOTH_PX` x `MOTION_SMOOTH_PX` dentro la pioggia; (4) gli
    isolati restano 0 ma sono contati e dichiarati (`no_neighbor_motion`).

    Ritorna (u, v, info); `info` contiene i contatori pubblicati nel JSON."""
    rain = np.asarray(domain, dtype=bool) & (
        np.asarray(latest, dtype=np.float32) > RAIN_MIN_MMH)
    u, v, cap_info = cap_and_replace(motion["u"], motion["v"], pixel_km,
                                     step_s, rain=rain)
    over_rain = cap_info["over_mask"] & rain
    known = rain & np.isfinite(u) & np.isfinite(v)
    u, v, fill_info = fill_motion(u, v, missing=rain & ~known, valid=known)
    if MOTION_SMOOTH_PX >= 3:
        rr, cc = np.nonzero(rain)
        if rr.size:
            mu, mv = _window_median(np.where(rain, u, np.nan),
                                    np.where(rain, v, np.nan), rr, cc,
                                    MOTION_SMOOTH_PX)
            ok = np.isfinite(mu) & np.isfinite(mv)
            u[rr[ok], cc[ok]] = mu[ok]
            v[rr[ok], cc[ok]] = mv[ok]
    replaced_cap = int((over_rain & np.isfinite(u) & np.isfinite(v)).sum())
    unfilled = rain & ~(np.isfinite(u) & np.isfinite(v))
    info = {
        "rain_pixels": int(rain.sum()),
        "over_cap_total": int(cap_info["over_cap"]),
        "over_cap_rain": int(over_rain.sum()),
        "replaced_by_cap": replaced_cap,
        "unfilled_by_cap": int(over_rain.sum()) - replaced_cap,
        "filled": int(fill_info["filled"]),
        "unfilled": int(unfilled.sum()),
        "smooth_px": int(MOTION_SMOOTH_PX),
    }
    u = np.where(np.isfinite(u), u, 0.0).astype(np.float32)
    v = np.where(np.isfinite(v), v, 0.0).astype(np.float32)
    return u, v, info


def extend_motion(u, v, domain, sigma=MOTION_EXTEND_SIGMA_PX):
    """Estende il moto a TUTTO il dominio (il trasporto lo campiona a valle).

    `semi_lagrangian` usa il vettore del pixel di DESTINAZIONE: con 0 fuori
    dall'eco il bordo d'attacco non puo' avanzare e le eco piccole spariscono.
    Qui ogni pixel prende il vettore del pixel valido piu' vicino (trasformata
    di distanza euclidea) e poi il campo viene levigato con una gaussiana:
    la divergenza di un campo a gradini duplica massa nel ricampionamento.

    La maschera del dominio resta applicata all'output dal trasporto: qui
    serve solo a delimitare dove il moto ha senso (fuori il campo e' 0).

    Ritorna (u, v, info) con i contatori pubblicati nel JSON."""
    from scipy import ndimage

    u = np.asarray(u, dtype=np.float32)
    v = np.asarray(v, dtype=np.float32)
    dom = np.asarray(domain, dtype=bool)
    known = ((u != 0.0) | (v != 0.0)) & dom
    n_known = int(known.sum())
    if n_known == 0:
        return u, v, {"extended": False, "method": MOTION_EXTEND_METHOD,
                      "sigma_px": int(sigma), "pixels_known": 0,
                      "pixels_from_zero": 0,
                      "null_reason": "no_known_motion"}
    if n_known == int(dom.sum()):
        return u, v, {"extended": False, "method": MOTION_EXTEND_METHOD,
                      "sigma_px": int(sigma), "pixels_known": n_known,
                      "pixels_from_zero": 0,
                      "null_reason": "motion_already_dense"}
    idx = ndimage.distance_transform_edt(~known, return_distances=False,
                                         return_indices=True)
    ue = ndimage.gaussian_filter(u[tuple(idx)].astype(np.float32),
                                 float(sigma), mode="nearest", truncate=3.0)
    ve = ndimage.gaussian_filter(v[tuple(idx)].astype(np.float32),
                                 float(sigma), mode="nearest", truncate=3.0)
    info = {"extended": True, "method": MOTION_EXTEND_METHOD,
            "sigma_px": int(sigma), "pixels_known": n_known,
            "pixels_from_zero": int((dom & ~known).sum()),
            "null_reason": None}
    return ue.astype(np.float32), ve.astype(np.float32), info


def compute(bundle, config, clock, warnings, out_dir=None, orographic=None):
    """Calcola il nowcast. Ritorna (payload, campi_nativi).

    `campi_nativi` = {lead_min: ndarray mm/h} piu' `_domain`, `_grid`, `_rd`
    per la validazione offline (nessun secondo calcolo).

    `orographic=None` -> si usa il flag `orographic_enhancement.enabled` della
    config; True/False lo forzano (usato dall'A/B di `nowcast_verify`).
    """
    leads = leads_min(config)
    frames, origin = collect_frames(bundle, config, clock, warnings)
    if not frames:
        return _unavailable("sri_unavailable", clock,
                            {"frames_origin": origin}), None
    if len(frames) < 3:
        return _unavailable("sri_frames_lt_3", clock,
                            {"frames_origin": origin,
                             "frames_used": len(frames)}), None

    latest, domain, latest_ts = frames[-1]
    if float((latest > RAIN_MIN_MMH).sum()) == 0:
        # Trasporto puro: se l'ultimo campo e' asciutto non c'e' nulla da
        # spostare (il nowcast non genera pioggia).
        return _unavailable("no_rain_in_latest_frame", clock,
                            {"frames_origin": origin,
                             "frames_used": len(frames)}), None

    motion = estimate_motion(frames, warnings)
    if motion is None:
        return _unavailable("motion_unavailable", clock,
                            {"frames_origin": origin,
                             "frames_used": len(frames)}), None

    rd = None
    # il raster SRI piu' recente serve per griglia/CRS (il tuple non lo porta)
    for slot, by_name in ((getattr(bundle, "dpc_bundle", None) or {})
                          .get("entries") or {}).items():
        e = (by_name or {}).get("sri")
        if (e or {}).get("raster") is not None and \
                int(e["raster"].time_ms) == latest_ts:
            rd = e["raster"]
            break
    if rd is None:
        rd = _fallback_raster(config, clock, latest_ts, warnings)
    if rd is None:
        return _unavailable("sri_grid_unavailable", clock,
                            {"frames_origin": origin,
                             "frames_used": len(frames)}), None

    pixel_km = _pixel_km(rd)
    step_s = STEP_MIN * 60
    u, v, motion_info = resolve_motion(motion, latest, domain, pixel_km,
                                       step_s)
    u, v, extend_info = extend_motion(u, v, domain)
    # FATTORE DI VELOCITA' (taratura dichiarata): vedi MOTION_SPEED_FACTOR.
    if MOTION_SPEED_FACTOR != 1.0:
        u = u * np.float32(MOTION_SPEED_FACTOR)
        v = v * np.float32(MOTION_SPEED_FACTOR)
    # `speed` e' il moto EFFETTIVO applicato (dopo estensione e fattore)
    speed = pixel_speed_kmh(u, v, pixel_km, step_s)
    rain = domain & (latest > RAIN_MIN_MMH)
    grid = _output_grid(rd)

    native = {}
    stepped = latest.copy()
    done = 0
    mass_capped = 0
    mass_scale = 1.0
    mass_scale_by_lead = {}
    # TRASPORTO A SPOSTAMENTO COMPOSTO (default): un solo campionamento per
    # lead sullo spostamento TOTALE (v * lead), invece di `lead/5` passi
    # semi-Lagrangiani da 5 minuti ognuno con il proprio ricampionamento
    # (Germann & Zawadzki 2002/2003, "interpolate once"). Il vecchio schema
    # applica lo stesso filtro 6 volte a +30 e 12 a +60: misurato su un campo
    # sintetico a banda limitata, l'energia HF residua e' 0,18/0,13 contro
    # 0,88/0,71 del warp unico cubico, e la banda di Nyquist si svuota.
    # `nowcast_transport_mode="steps"` riproduce il vecchio schema per l'A/B.
    transport_mode = str(config.get("nowcast_transport_mode")
                         or "single").lower()
    if transport_mode not in ("single", "steps"):
        transport_mode = "single"
    transport_order = int(config.get("nowcast_transport_order") or 3)
    if transport_order not in (1, 3):
        transport_order = 3
    transport_source = latest
    if transport_mode == "single" and transport_order == 3:
        # pre-filtro di spline UNA volta: i lead differiscono solo per lo
        # spostamento, non per il campo di partenza
        from scipy import ndimage as _ndi
        transport_source = _ndi.spline_filter(
            np.asarray(latest, dtype=np.float32), order=3, mode="mirror")

    # ENHANCEMENT OROGRAFICO: precalcolato UNA volta sul raster nativo (la
    # quota e il vento NWP non dipendono dal lead), applicato a ogni frame
    # TRASPORTATO prima delle statistiche e della rampa alpha. Se la quota o
    # l'ambiente mancano, `ctx["applied"]` e' False con un motivo esplicito e
    # il campo resta il trasporto puro.
    oro_cfg = orography_mod.config_for(config)
    if orographic is not None:
        oro_cfg = dict(oro_cfg)
        oro_cfg["enabled"] = bool(orographic)
    oro_ctx = None
    try:
        oro_ctx = orography_mod.prepare(
            rd, {"orographic_enhancement": oro_cfg}, latest_ts, warnings)
    except Exception as exc:  # layer additivo: il nowcast resta il trasporto
        warnings.append("orographic: prepare failed (%s)" % exc)
        oro_ctx = {"schema": orography_mod.SCHEMA,
                   "enabled": bool(oro_cfg["enabled"]), "applied": False,
                   "null_reason": "prepare_error:%s" % exc.__class__.__name__}
    oro_lead_stats = {}

    for lead in leads:
        steps = int(lead // STEP_MIN)
        if transport_mode == "single":
            # spostamento TOTALE in pixel: u,v sono per STEP_MIN (5 min)
            tot_u = (u * np.float32(steps)).astype(np.float32)
            tot_v = (v * np.float32(steps)).astype(np.float32)
            smp, inside = _warp(transport_source, -tot_u, -tot_v,
                                order=transport_order,
                                prefilter=(transport_source is latest))
            stepped = np.where(inside & domain, smp, 0.0).astype(np.float32)
            before = float(latest[domain].sum())
            cur = float(stepped.sum())
            # CAP DELLA SOLA CRESCITA, come nel path a passi: il cubico ha
            # lobi negativi e puo' aggiungere massa. Il calo (pioggia che esce
            # dal dominio) resta: non viene re-inflazza.
            mass_scale = 1.0
            if cur > before > 0.0:
                mass_scale = before / cur
                stepped *= np.float32(mass_scale)
                mass_capped += 1
            mass_scale_by_lead[lead] = round(float(mass_scale), 8)
        else:
            while done < steps:
                before = float(stepped.sum())
                stepped = semi_lagrangian(stepped, u, v, 1, domain)
                # CAP DELLA SOLA CRESCITA (solo guadagno): il trasporto puro
                # non crea massa, lo schema di ricampionamento con un campo di
                # moto non divergenza-free invece la duplica. Il calo (pioggia
                # che esce dal dominio) resta: non viene re-inflazza.
                cur = float(stepped.sum())
                if cur > before > 0.0:
                    stepped *= np.float32(before / cur)
                    mass_scale *= before / cur
                    mass_capped += 1
                done += 1
            mass_scale_by_lead[lead] = round(float(mass_scale), 8)
        if oro_ctx and oro_ctx.get("applied"):
            corrected, st = orography_mod.apply(stepped, oro_ctx, oro_cfg)
            native[lead] = corrected
            oro_lead_stats[lead] = st
        else:
            native[lead] = stepped

    oro_block = orography_mod.summarize(oro_ctx)
    reliability = reliability_for(bundle, config, out_dir)
    if oro_block.get("applied") and oro_lead_stats:
        off = [oro_lead_stats[l] for l in OFFICIAL_LEADS if l in oro_lead_stats]
        if off:
            oro_block["delta_mean_mm_h"] = round(
                float(np.mean([s["delta_mean_mm_h"] for s in off])), 4)
            oro_block["delta_max_mm_h"] = round(
                float(np.max([s["delta_max_mm_h"] for s in off])), 4)
            oro_block["pixels_changed_max"] = int(
                max(s["pixels_changed"] for s in off))
            # nomi espliciti richiesti dal contratto del layer
            oro_block["cells_corrected"] = oro_block["pixels_changed_max"]
            oro_block["upslope_mean_ms"] = (oro_block.get("upslope_ms") or {}).get("mean")
            oro_block["delta_mean_scope"] = (
                "media del delta sui soli pixel cambiati, mediata sui lead "
                "ufficiali 15/30/60; delta_max_mm_h = massimo sui lead ufficiali")
        oro_block["per_lead"] = {
            str(k): {kk: vv for kk, vv in v.items() if kk != "applied"}
            for k, v in sorted(oro_lead_stats.items())}
        oro_block["env_time"] = _iso(latest_ts)
        oro_block["env_held_constant_over_horizon"] = True

    payload = {
        "schema": SCHEMA,
        "available": True,
        "null_reason": None,
        "source": SOURCE,
        "source_slot": _iso(latest_ts),
        "generated_at": clock.now_iso(),
        "unit": "mm/h",
        "motion": MOTION,
        "motion_algorithm": MOTION_ALGORITHM,
        "motion_pairs": motion["pairs"],
        "speed_cap_kmh": SPEED_CAP_KMH,
        "speed_cap_mode": "replaced_by_neighbor_median",
        # contato sui pixel che TRASPORTANO PIOGGIA (prima era su tutto il
        # dominio: 6.712 contro 3.597 reali). Non sono piu' azzerati: sono
        # sostituiti dalla mediana del vicinato.
        "vectors_zeroed_by_cap": motion_info["over_cap_rain"],
        "vectors_over_cap_total": motion_info["over_cap_total"],
        "vectors_replaced_by_cap": motion_info["replaced_by_cap"],
        "vectors_unfilled_by_cap": motion_info["unfilled_by_cap"],
        "motion_filled_pixels": motion_info["filled"],
        "motion_unfilled_pixels": motion_info["unfilled"],
        "motion_null_reason": ("no_neighbor_motion"
                               if motion_info["unfilled"] else None),
        "motion_smoothing_px": motion_info["smooth_px"],
        # estensione del moto a tutto il dominio (fix "muro"): senza, il
        # bordo d'attacco resta fermo e le eco piccole spariscono.
        "motion_extended": bool(extend_info["extended"]),
        "motion_extended_method": extend_info["method"],
        "motion_extended_sigma_px": extend_info["sigma_px"],
        "motion_pixels_known": extend_info["pixels_known"],
        "motion_pixels_from_zero": extend_info["pixels_from_zero"],
        "motion_extend_null_reason": extend_info["null_reason"],
        "motion_speed_factor": MOTION_SPEED_FACTOR,
        "mass_growth_capped": True,
        "mass_cap_steps": mass_capped,
        "transport_mode": transport_mode,
        "transport_order": transport_order,
        "transport_warps_per_lead": (1 if transport_mode == "single"
                                     else None),
        "frames_used": len(frames),
        "frames_origin": origin,
        "frames_slots": [_iso(f[2]) for f in frames],
        "domain_mask": DOMAIN_MASK_SOURCE,
        "leads_total": len(leads),
        "official_leads": list(OFFICIAL_LEADS),
        "leads": [
            {
                "lead_min": lead,
                "step_min": step_min_for(lead, leads),
                "valid_time": _iso(latest_ts + lead * 60000),
                "unit": "mm/h",
                "image": IMAGE_NAMES.get(lead),
                "image_crs": OUTPUT_CRS,
                # ufficiale = entra nella verifica contro la persistenza;
                # gli altri frame sono per il player (dichiarato).
                "official": lead in OFFICIAL_LEADS,
                # affidabilita' MISURATA per orizzonte (null se non misurato)
                "reliability": _reliability_entry(lead, reliability),
                "stats": _lead_stats(native[lead], domain, pixel_km),
            }
            for lead in leads
        ],
        "grid": {k: v for k, v in grid.items() if k != "dst_transform"},
        "legend": [{"mmh": p[0], "rgb": list(p[1])} for p in PALETTE],
        "motion_pair_dt_s": motion.get("pair_dt_s"),
        "motion_stats": {
            "median_speed_kmh": round(float(np.median(speed[rain])), 2)
            if bool(rain.any()) else None,
            "p95_speed_kmh": round(float(np.percentile(speed[rain], 95)), 2)
            if bool(rain.any()) else None,
            "pixels_over_cap": motion_info["over_cap_rain"],
            "pixels_over_cap_total": motion_info["over_cap_total"],
        },
        "limits": LIMITS,
        "orographic_enhancement": oro_block,
    }

    # ------------------------------------------------------------------
    # ENSEMBLE PROBABILISTICO (additivo, `ensemble.enabled`). Base: il
    # campo deterministico gia' corretto (moto esteso, cap di massa,
    # orografia). Nessuna fisica nuova: moto perturbato per membro,
    # rasatura dell'eco, fattore d'intensita' e di coda, massa conservata.
    # La cascata e' SPENTA in produzione: il rumore di banda non si applica
    # (`noise.applied = false`, `cascade_gains_unavailable`), dichiarato.
    # ------------------------------------------------------------------
    _ens_cfg = dict(config.get("ensemble") or {})
    _ens_payload = ensemble_mod.disabled_payload("disabled_by_config", _ens_cfg)
    _ens_fields = {}
    if bool(_ens_cfg.get("enabled")):
        _ens_leads = tuple(int(x) for x in
                           (_ens_cfg.get("leads_min") or OFFICIAL_LEADS))
        _ens_leads = tuple(l for l in _ens_leads if l in native)
        _prob_thr = float(_ens_cfg.get("prob_image_threshold_mmh") or 0.5)
        _img_thrs = tuple(float(x) for x in
                          (_ens_cfg.get("image_thresholds_mmh") or (_prob_thr,)))
        _img_leads = tuple(int(x) for x in
                           (_ens_cfg.get("image_leads") or _ens_leads))
        _per_lead = {}
        _t_ens = 0.0
        _mass_target = None
        if bool(_ens_cfg.get("mass_conservation", True)):
            _mass_target = {
                int(l): float(np.asarray(native[l], dtype=np.float64)[domain]
                              .sum()) for l in _ens_leads}
        try:
            _blocks, _fields = ensemble_mod.run_leads(
                {l: native[l] for l in _ens_leads}, u, v, domain, _ens_leads,
                None, _ens_cfg, pixel_km, step_s, latest_ts,
                latest=latest, post=None,
                rain_mask=(domain & (latest > RAIN_MIN_MMH)),
                mass_target=_mass_target)
        except Exception as exc:      # layer additivo: mai rompere il nowcast
            _blocks, _fields = {}, {}
            warnings.append("nowcast: ensemble failed ({}:{})".format(
                exc.__class__.__name__, exc))
        for _lead in _ens_leads:
            _blk = _blocks.get(int(_lead))
            if _blk is None:
                _per_lead[str(_lead)] = {"applied": False,
                                         "null_reason": "ensemble_failed"}
                continue
            _t_ens += float(_blk["cost_seconds"]["total"])
            _blk["images"] = {}
            if int(_lead) in _img_leads:
                for _thr_img in _img_thrs:
                    _key = "prob_%s" % ensemble_mod._thr_key(_thr_img)
                    if _key not in _fields[int(_lead)]["probabilities"]:
                        continue
                    if abs(_thr_img - _prob_thr) < 1e-9:
                        _name = ensemble_mod.PROB_IMAGE_NAMES.get(int(_lead))
                    else:
                        _name = ensemble_mod.prob_image_name(_lead, _thr_img)
                    if _name:
                        _blk["images"][_key] = _name
            _blk["prob_image_threshold_mmh"] = _prob_thr
            _blk["image_thresholds_mmh"] = list(_img_thrs)
            # la mappa si disegna da un campo smussato (solo immagine):
            # dichiarato qui accanto ai nomi dei file.
            _blk["image_smoothing_px"] = float(
                ensemble_mod.PROB_IMAGE_SMOOTH_PX)
            _per_lead[str(_lead)] = _blk
            _ens_fields[int(_lead)] = _fields[int(_lead)]
        _ens_payload = ensemble_mod.disabled_payload(
            "ensemble_not_applied", _ens_cfg)
        _ens_payload.update({
            "enabled": True,
            "applied": bool(_ens_fields),
            "null_reason": (None if _ens_fields else
                            "ensemble_not_applied_on_any_lead"),
            "members": int(_ens_cfg.get("members") or ensemble_mod.MEMBERS),
            "method": ensemble_mod.METHOD,
            "thresholds_mmh": [float(x) for x in
                               (_ens_cfg.get("thresholds_mmh")
                                or ensemble_mod.THRESHOLDS_MMH)],
            "prob_decimals": ensemble_mod.PROB_DECIMALS,
            "prob_image_threshold_mmh": _prob_thr,
            "image_thresholds_mmh": [float(x) for x in _img_thrs],
            "image_smoothing_px": float(ensemble_mod.PROB_IMAGE_SMOOTH_PX),
            "legend": ensemble_mod.legend_payload(),
            "legend_unit": "probability 0-1",
            "leads": _per_lead,
            "cost_seconds": {"total": round(_t_ens, 3)},
            "reference": ensemble_mod.REFERENCE,
            "limits": ensemble_mod.LIMITS,
        })
        if _ens_fields and _per_lead:
            _first = _per_lead[str(next(iter(_ens_fields)))]
            _ens_payload["motion_perturbation"] = _first.get(
                "motion_perturbation")
            _ens_payload["noise"] = _first.get("noise")
        native["_ensemble"] = _ens_fields
    payload["ensemble"] = _ens_payload

    verification = verification_for(bundle, config, out_dir)
    payload["verification"] = verification
    payload["beats_persistence"] = verification.get("beats_persistence")
    payload["reliability_by_lead"] = reliability
    payload["low_reliability_over_min"] = LOW_RELIABILITY_OVER_MIN
    ab = verification.get("orography_ab") or {}
    payload["beats_persistence_no_orog"] = ab.get("beats_persistence_no_orog")
    payload["beats_persistence_orog"] = ab.get("beats_persistence_orog")
    native["_domain"] = domain
    native["_grid"] = grid
    native["_rd"] = rd
    native["_motion"] = (u, v)
    # Scala cumulata del cap di massa per lead: i frame restano CUMULATIVI
    # (f_L = scala_L/scala_prev * trasporto(f_prev)) e la scala e' dichiarata.
    native["_mass_scale"] = mass_scale_by_lead
    return payload, native


def verification_for(bundle, config, out_dir=None):
    """Verifica dal file accanto all'out_dir (o iniettata nel bundle, test)."""
    ver = getattr(bundle, "_nowcast_verification", None)
    if isinstance(ver, dict):
        return ver
    if out_dir:
        return load_verification(out_dir)
    return load_verification(
        (config.get("output") or {}).get("out_dir") or ".")


def leads_min(config):
    """Orizzonti del nowcast: default `LEADS_MIN`.

    Una lista diversa (`config['nowcast_leads_min']`) esiste SOLO per i
    backtest: la produzione, `IMAGE_NAMES` e il client usano i 15 frame del
    contratto. Con la lista di default il payload non cambia di forma.
    """
    raw = (config or {}).get("nowcast_leads_min")
    if not raw:
        return LEADS_MIN
    try:
        out = tuple(sorted({int(x) for x in raw if int(x) > 0}))
    except (TypeError, ValueError):
        return LEADS_MIN
    return out or LEADS_MIN


def step_min_for(lead, leads=None):
    """Passo pubblicato del frame: distanza dal lead precedente.

    Con il contratto di produzione: 5 min fino a +45, 15 min a +60/+75/+90,
    30 min a +120/+150/+180. Per il primo frame il passo e' 5 min (non c'e'
    un precedente pubblicato). Il TRASPORTO sottostante resta a 5 minuti.
    Con `leads` esplicito la tabella non si usa: il passo si ricava dalla
    lista data (e' il caso dei backtest con orizzonti diversi).
    """
    lead = int(lead)
    if leads is None:
        return STEP_MIN_BY_LEAD.get(lead, STEP_MIN)
    seq = sorted(int(x) for x in leads)
    if lead in seq:
        i = seq.index(lead)
        return int(lead - seq[i - 1]) if i > 0 else STEP_MIN
    prev = [x for x in seq if x < lead]
    return int(lead - prev[-1]) if prev else STEP_MIN


def load_reliability(out_dir):
    """Affidabilita' per orizzonte dal file scritto dal backtest.

    File assente o invalido -> `None`: il payload non dichiara numeri che non
    esistono. Il file e' piccolo (una voce per orizzonte) e viene incorporato
    per intero in `nowcast.json` (`reliability_by_lead`).
    """
    path = os.path.join(out_dir or ".", RELIABILITY_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(
            data.get("horizons"), dict):
        return None
    return data


def reliability_for(bundle, config, out_dir=None):
    """Affidabilita' iniettata nel bundle (test) o letta accanto all'output."""
    injected = getattr(bundle, "_nowcast_reliability", None)
    if isinstance(injected, dict):
        return injected
    if out_dir:
        return load_reliability(out_dir)
    return load_reliability(
        (config.get("output") or {}).get("out_dir") or ".")


def _reliability_entry(lead, doc):
    """Voce compatta per un lead (None se quell'orizzonte non e' misurato)."""
    if not isinstance(doc, dict):
        return None
    horizons = doc.get("horizons") or {}
    row = horizons.get(str(int(lead)))
    if not isinstance(row, dict):
        return None
    keys = ("lead_min", "cases", "level", "nowcast_csi", "persistence_csi",
            "csi_delta", "nowcast_fss", "persistence_fss", "fss_delta",
            "nowcast_beats_persistence", "low_reliability")
    out = {k: row.get(k) for k in keys if k in row}
    return out or None


def _fallback_raster(config, clock, ts, warnings):
    """Raster di georeferenziazione quando il bundle non c'e' (raro)."""
    try:
        rasters, _w, _ts = fetch_mod.fetch_frames(
            config, product=PRODUCT_TYPE, max_frames=1,
            anchor_ms=int(ts))
    except Exception as exc:
        warnings.append(f"nowcast: grid raster unavailable ({exc})")
        return None
    return rasters[-1] if rasters else None


# ---------------------------------------------------------------------------
# scrittura
# ---------------------------------------------------------------------------
def write_products(out_dir, payload, native):
    """Scrive i WebP dei lead pubblicati. Ritorna il dizionario delle misure
    (il JSON lo scrive `run` DOPO aver inserito le misure: un solo file).

    Un frame che non si riesce a scrivere NON annulla gli altri: il lead
    corrispondente resta con `image: null` e `image_error`, cosi' il player puo'
    saltare quel frame senza rompersi (lacuna dichiarata, non silenziosa)."""
    os.makedirs(out_dir, exist_ok=True)
    measured = {"images": {}, "image_errors": {}, "json_bytes": 0}
    if payload.get("available") and native:
        rd = native["_rd"]
        grid = native["_grid"]
        for entry in (payload.get("leads") or []):
            lead = int(entry["lead_min"])
            name = entry.get("image") or IMAGE_NAMES.get(lead)
            if not name:
                continue
            try:
                field = reproject_to_grid(native[lead], native["_domain"],
                                          rd, grid)
                path = os.path.join(out_dir, name)
                size = write_webp(path, field)
                measured["images"][name] = size
                # statistiche sulla griglia pubblicata (non su quella nativa)
                entry["stats"]["published_pixels"] = int(
                    np.isfinite(field).sum())
            except Exception as exc:
                reason = "{}:{}".format(exc.__class__.__name__, exc)
                measured["image_errors"][name] = reason
                entry["image"] = None
                entry["image_error"] = reason
        # immagini di PROBABILITA' (colore = probabilita', legenda 0-100%):
        # una per ogni soglia del selettore client, per i lead ufficiali.
        _ens = (payload.get("ensemble") or {})
        for _lead_key, _blk in (_ens.get("leads") or {}).items():
            try:
                _lead = int(_lead_key)
            except (TypeError, ValueError):
                continue
            _fields = (native.get("_ensemble") or {}).get(_lead) or {}
            for _prob_key, _name in (_blk.get("images") or {}).items():
                _prob = (_fields.get("probabilities") or {}).get(_prob_key)
                if _prob is None or not _name:
                    continue
                try:
                    _pub = reproject_to_grid(_prob, native["_domain"], rd,
                                             grid)
                    _size = ensemble_mod.write_prob_webp(
                        os.path.join(out_dir, _name), _pub)
                    measured.setdefault("probability_images", {})[_name] = _size
                    _blk.setdefault("published", {})[_prob_key] = {
                        "pixels": int(np.isfinite(_pub).sum()),
                        "max": (round(float(np.nanmax(_pub)), 4)
                                if np.isfinite(_pub).any() else None),
                    }
                except Exception as _exc:
                    _reason = "{}:{}".format(_exc.__class__.__name__, _exc)
                    measured.setdefault("probability_errors", {})[_name] = _reason
    return measured


def run(bundle, config, out_dir, clock=None, warnings=None):
    """Entry point chiamato da main.py: calcola e scrive, non solleva mai
    per condizioni attese (dato assente -> available:false)."""
    clock = clock if isinstance(clock, clock_mod.Clock) else \
        clock_mod.from_config(config)
    warnings = warnings if warnings is not None else \
        getattr(bundle, "warnings", [])
    payload, native = compute(bundle, config, clock, warnings, out_dir=out_dir)
    try:
        measured = write_products(out_dir, payload, native)
    except Exception as exc:
        # un encoder che manca non deve lasciare il prodotto a meta': si
        # degrada a `available:false` DICHIARATO e si scrive comunque il JSON.
        warnings.append("nowcast: image write failed ({}:{})".format(
            exc.__class__.__name__, exc))
        payload = dict(payload, available=False,
                       null_reason="image_write_failed:{}".format(
                           exc.__class__.__name__),
                       leads=[])
        measured = {"images": {}, "image_errors": {}}
    payload["output_bytes"] = {"images": measured["images"]}
    if measured.get("image_errors"):
        payload["output_bytes"]["image_errors"] = measured["image_errors"]
        warnings.append(
            "nowcast: {} frame su {} non scritti (il player li salta)".format(
                len(measured["image_errors"]), len(LEADS_MIN)))
        if not measured["images"]:
            # nessun frame utilizzabile: il prodotto non e' mostrabile. Lo si
            # dichiara (available:false) invece di lasciare un layer vuoto.
            payload = dict(payload, available=False,
                           null_reason="image_write_failed:all_frames")
            warnings.append("nowcast: 0 frame su {} -> available:false".format(
                len(LEADS_MIN)))
    path = os.path.join(out_dir, "nowcast.json")
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False,
                                        allow_nan=False))
    # la dimensione del JSON si conosce solo dopo la scrittura: resta nella
    # riga di log e nel bundle (non si riscrive il file per un contatore).
    measured["json_bytes"] = os.path.getsize(path)
    bundle.nowcast = payload
    bundle.nowcast_measured = measured
    return payload


# ---------------------------------------------------------------------------
# valutazione (usata dallo script di replay, non dal run operativo)
# ---------------------------------------------------------------------------
def contingency(pred, obs, domain, threshold):
    """(hits, misses, false_alarms, correct_negatives) su dominio valido."""
    ok = np.isfinite(pred) & np.isfinite(obs) & np.asarray(domain, dtype=bool)
    p = np.asarray(pred)[ok] >= threshold
    o = np.asarray(obs)[ok] >= threshold
    hits = int(np.sum(p & o))
    misses = int(np.sum((~p) & o))
    fa = int(np.sum(p & (~o)))
    cn = int(np.sum((~p) & (~o)))
    return hits, misses, fa, cn


def scores(counts):
    """CSI / POD / FAR da (hits, misses, fa, cn); None se non definito."""
    hits, misses, fa, cn = [int(x) for x in counts]
    pod = (hits / float(hits + misses)) if (hits + misses) else None
    far = (fa / float(hits + fa)) if (hits + fa) else None
    csi = (hits / float(hits + misses + fa)) if (hits + misses + fa) else None
    return {"csi": csi, "pod": pod, "far": far,
            "hits": hits, "misses": misses, "false_alarms": fa,
            "correct_negatives": cn}
