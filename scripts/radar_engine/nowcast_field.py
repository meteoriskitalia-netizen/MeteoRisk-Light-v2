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
- Un vettore sopra `SPEED_CAP_KMH` (110 km/h) viene AZZERATO (0,0), non
  clampato a 110: un moto non fisico non viene "aggiustato".

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
coppia anomala).

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
`data/radar/nowcast.json` (schema `meteorisk.nowcast_field/1`) + **12 WebP**
georeferenziati in EPSG:3857, uno per ogni multiplo di 5 minuti da +5 a +60
(`nowcast_lead005.webp` ... `nowcast_lead060.webp`), con alpha=0 fuori dal
dominio radar. I frame sono CUMULATIVI: il +10 e' il +5 avanzato di altri 5
minuti (semi-Lagrangiano a passi di 5), non un trasporto indipendente. I tre
lead **ufficiali di verifica** sono 15/30/60 e portano `official: true`; gli
altri servono al player e sono dichiarati come tali.

Il JSON porta bbox/risoluzione delle immagini e la legenda in mm/h. Il WebP e'
stato scelto DOPO aver misurato il JSON con i campi grezzi: 12 campi float su
1200x1400 sarebbero ~80 MB per run, le 12 immagini costano ~100 kB in totale
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

import numpy as np

from . import clock as clock_mod
from . import fetch as fetch_mod
from . import models

SCHEMA = "meteorisk.nowcast_field/1"
SOURCE = "DPC_SRI"
PRODUCT_TYPE = "SRI"

FRAMES_WANTED = 6               # ultimi 6 fotogrammi (25 minuti di storia)
STEP_MIN = 5                    # cadenza misurata del prodotto SRI (PT5M)
# UN FRAME OGNI 5 MINUTI da +5 a +60 (12 frame, player fluido come il radar).
# Il trasporto e' CUMULATIVO: il frame a +N usa N/5 passi da 5 minuti.
LEADS_MIN = tuple(range(5, 61, 5))
# Verifica ufficiale: solo questi tre (invariata rispetto alla 1.3.0.71).
OFFICIAL_LEADS = (15, 30, 60)
FLOW_PAIRS = 3                  # coppie consecutive usate per il flusso
SPEED_CAP_KMH = 110.0           # sopra questo valore il vettore e' AZZERATO
RAIN_MIN_MMH = 0.05             # sotto: nessuna pioggia disegnata (0.0 e' valido)
DOMAIN_MASK_SOURCE = "latest_sri_valid_mask"
MOTION = "optical_flow"
MOTION_ALGORITHM = "lucas_kanade_pyramidal"

OUTPUT_CRS = "EPSG:3857"
OUTPUT_RES_M = 1500             # risoluzione delle immagini (misurata: v. report)
WEBP_QUALITY = 80
IMAGE_NAMES = {lead: "nowcast_lead%03d.webp" % lead for lead in LEADS_MIN}
VERIFICATION_FILE = "nowcast_verification.json"

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
    "medio, non dalla vita della cella. La maschera e' quella del dominio "
    "radar: fuori non viene disegnato nulla."
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


def _warp(a, u, v):
    """Campionamento bilineare di `a` in (r+v, c+u). Ritorna (out, inside)."""
    from scipy import ndimage
    h, w = a.shape
    rr = np.arange(h, dtype=np.float32)[:, None] + v
    cc = np.arange(w, dtype=np.float32)[None, :] + u
    out = ndimage.map_coordinates(a, [rr, cc], order=1, mode="constant",
                                  cval=0.0).astype(np.float32)
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
    """Mediana per componente su una lista di array (robusta a 1 coppia)."""
    if len(arrays) == 1:
        return arrays[0]
    return np.median(np.stack(arrays, axis=0), axis=0).astype(np.float32)


def estimate_motion(frames, warnings):
    """Moto (u, v) per frame, mediana delle ultime `FLOW_PAIRS` coppie."""
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
        # normalizza al PASSO NOMINALE di 5 minuti (px/frame -> px/5min)
        scale = (STEP_MIN * 60.0) / float(dt_s)
        us.append(u * scale)
        vs.append(v * scale)
        dts.append(dt_s)
        used += 1
    if not used:
        return None
    return {
        "u": _median_stack(us),
        "v": _median_stack(vs),
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


def cap_speed(u, v, pixel_km, step_s, cap_kmh=SPEED_CAP_KMH):
    """Azzera (0,0) i vettori sopra il cap. NON clampa a `cap_kmh`.

    Ritorna (u, v, n_zeroed): il conteggio e' pubblicato nel JSON."""
    speed = pixel_speed_kmh(u, v, pixel_km, step_s)
    over = speed > float(cap_kmh)
    n = int(over.sum())
    if n:
        u = np.where(over, 0.0, u).astype(np.float32)
        v = np.where(over, 0.0, v).astype(np.float32)
    return u, v, n


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
        out[:, :, ch] = np.interp(vv, nodes, cols[:, ch]).astype(np.uint8)
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
        "frames_used": 0,
        "leads": [],
        "beats_persistence": None,
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
    return data


def compute(bundle, config, clock, warnings, out_dir=None):
    """Calcola il nowcast. Ritorna (payload, campi_nativi).

    `campi_nativi` = {lead_min: ndarray mm/h} piu' `_domain`, `_grid`, `_rd`
    per la validazione offline (nessun secondo calcolo)."""
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
    u, v, capped = cap_speed(motion["u"], motion["v"], pixel_km, step_s)
    speed = pixel_speed_kmh(motion["u"], motion["v"], pixel_km, step_s)
    grid = _output_grid(rd)

    native = {}
    stepped = latest.copy()
    done = 0
    for lead in LEADS_MIN:
        steps = int(lead // STEP_MIN)
        while done < steps:
            stepped = semi_lagrangian(stepped, u, v, 1, domain)
            done += 1
        native[lead] = stepped

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
        "speed_cap_mode": "vector_zeroed_not_clamped",
        "vectors_zeroed_by_cap": capped,
        "frames_used": len(frames),
        "frames_origin": origin,
        "frames_slots": [_iso(f[2]) for f in frames],
        "domain_mask": DOMAIN_MASK_SOURCE,
        "leads_total": len(LEADS_MIN),
        "official_leads": list(OFFICIAL_LEADS),
        "leads": [
            {
                "lead_min": lead,
                "valid_time": _iso(latest_ts + lead * 60000),
                "unit": "mm/h",
                "image": IMAGE_NAMES[lead],
                "image_crs": OUTPUT_CRS,
                # ufficiale = entra nella verifica contro la persistenza;
                # gli altri frame sono per il player (dichiarato).
                "official": lead in OFFICIAL_LEADS,
                "stats": _lead_stats(native[lead], domain, pixel_km),
            }
            for lead in LEADS_MIN
        ],
        "grid": {k: v for k, v in grid.items() if k != "dst_transform"},
        "legend": [{"mmh": p[0], "rgb": list(p[1])} for p in PALETTE],
        "motion_pair_dt_s": motion.get("pair_dt_s"),
        "motion_stats": {
            "median_speed_kmh": round(float(np.median(
                speed[domain & (latest > RAIN_MIN_MMH)])), 2)
            if bool((domain & (latest > RAIN_MIN_MMH)).any()) else None,
            "p95_speed_kmh": round(float(np.percentile(
                speed[domain & (latest > RAIN_MIN_MMH)], 95)), 2)
            if bool((domain & (latest > RAIN_MIN_MMH)).any()) else None,
            "pixels_over_cap": capped,
        },
        "limits": LIMITS,
    }
    verification = verification_for(bundle, config, out_dir)
    payload["verification"] = verification
    payload["beats_persistence"] = verification.get("beats_persistence")
    native["_domain"] = domain
    native["_grid"] = grid
    native["_rd"] = rd
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
    """Scrive i 12 WebP. Ritorna il dizionario delle misure (il JSON lo scrive
    `run` DOPO aver inserito le misure: un solo file, nessuna riscrittura).

    Un frame che non si riesce a scrivere NON annulla gli altri: il lead
    corrispondente resta con `image: null` e `image_error`, cosi' il player puo'
    saltare quel frame senza rompersi (lacuna dichiarata, non silenziosa)."""
    os.makedirs(out_dir, exist_ok=True)
    measured = {"images": {}, "image_errors": {}, "json_bytes": 0}
    if payload.get("available") and native:
        rd = native["_rd"]
        grid = native["_grid"]
        by_lead = {int(l["lead_min"]): l for l in (payload.get("leads") or [])}
        for lead in LEADS_MIN:
            entry = by_lead.get(lead)
            try:
                field = reproject_to_grid(native[lead], native["_domain"],
                                          rd, grid)
                path = os.path.join(out_dir, IMAGE_NAMES[lead])
                size = write_webp(path, field)
                measured["images"][IMAGE_NAMES[lead]] = size
                # statistiche sulla griglia pubblicata (non su quella nativa)
                if entry is not None:
                    entry["stats"]["published_pixels"] = int(
                        np.isfinite(field).sum())
            except Exception as exc:
                reason = "{}:{}".format(exc.__class__.__name__, exc)
                measured["image_errors"][IMAGE_NAMES[lead]] = reason
                if entry is not None:
                    entry["image"] = None
                    entry["image_error"] = reason
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
            warnings.append("nowcast: 0 frame su 12 -> available:false")
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
