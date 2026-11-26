#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — nowcast_ensemble.py (novembre 2026)

NOWCAST PROBABILISTICO: ENSEMBLE A 20 MEMBRI DEL CAMPO DI PIOGGIA.

Perche' esiste
--------------
Il nowcast deterministico a 1 km / +60 min non puo' essere affidabile: le
scale fini (2-8 km) hanno una correlazione a un passo misurata di ~0,37 e con
qualunque trasporto la loro POSIZIONE e' incerta. Un campo singolo non dice
quanto fidarsi. L'ensemble invece produce una probabilita' di superamento che
si puo' verificare (Brier, reliability, CRPS) e che resta onesta anche dove la
posizione e' sbagliata.

Come e' fatto (due sorgenti di incertezza, nessuna fisica nuova)
----------------------------------------------------------------
1) PERTURBAZIONE DEL MOTO (STEPS/BPS). Ogni membro sposta il campo corretto
   di una quantita' costante: +-`MOTION_PARALLEL_MS` = 10,88 m/s lungo il moto
   e +-`MOTION_PERPENDICULAR_MS` = 5,76 m/s perpendicolare (valori dalla
   letteratura STEPS/BPS, CITATI e non tarati su questo campione). I 20 membri
   sono il prodotto cartesiano di 5 livelli paralleli x 4 perpendicolari:
       paralleli  {-1, -0.5, 0, +0.5, +1} * 10,88 m/s
       perpendic. {-1, -1/3, +1/3, +1}   * 5,76 m/s
   e' un disegno STRATIFICATO deterministico (nessun campionamento casuale):
   due run con lo stesso istante danno gli stessi membri. La perturbazione e'
   RIDOTTA se il moto e' instabile: `factor = clip(coherence/0,5,0,1) *
   clip(median_speed/5,0,1)` (coherence = |vettore mediano| / velocita'
   mediana sui pixel di pioggia). Con moto incoerente o quasi nullo la
   direzione non e' definita e la perturbazione tende a zero: non si inventa
   una direzione.

2) RUMORE PER BANDA (non parametrico, conserva la varianza). Il membro si
   costruisce sul TRASPORTO (prima di matching e decadimento) come
   `g_b * Y_b + sqrt(1-g_b^2) * |Y_b| * exp(i*theta_b)`, con `theta` casuale
   e SIMMETRICO (Hermitian: il campo risultante e' reale). La potenza attesa
   per frequenza e' `g^2|Y|^2 + (1-g^2)|Y|^2 = |Y|^2`: la varianza di ogni
   banda e' conservata, la parte predicibile (`g_b`) resta coerente e la
   parte impredicibile diventa rumore con lo spettro della banda. Metodo non
   parametrico come `noise_method='nonparametric'` di STEPS. Il rumore e'
   limitato al supporto dell'eco: senza, la fase casuale distribuirebbe
   pioggia debole su tutto il dominio (misurato). Senza cascata NON si
   aggiunge rumore e il campo lo dichiara.
   DOPO il rumore ogni membro riceve la STESSA correzione del run
   deterministico (matching dei quantili + decadimento, `post`): i membri
   sono consistenti con il campo pubblicato e il matching non falsa la
   conservazione della varianza perche' avviene dopo.

La base dell'ensemble e' il TRASPORTO del run (`raw[lead]`, prima di cascata,
matching e decadimento): e' l'unica base su cui i guadagni `g_b` hanno un
significato fisico. Ogni membro viene poi corretto come il campo operativo
(stessa cascata, stesso matching, stesso decadimento), quindi l'ensemble e'
consistente con il prodotto pubblicato. La perturbazione del moto e' una
TRASLAZIONE costante composta DOPO il trasporto deterministico. Non e' la
re-integrazione semi-lagrangiana per membro: l'errore e' O(Delta * grad u) ed
e' MISURATO in `audit/ensemble_advection_ab.json` (differenza fra i due metodi
sugli stessi campi). La modalita' esatta esiste (`advection_mode="advect"`,
usata solo dall'A/B).

Output (griglia nativa 1 km, 1400x1200)
--------------------------------------
Per ogni lead: `prob_0p5`, `prob_2`, `prob_5`, `prob_10`, `prob_20`
(probabilita' di superamento 0-1, quantizzata a 0,01), `p10`, `p50`, `p90`
del campo di pioggia, `spread` (deviazione standard fra membri). Le immagini
WebP mostrano la PROBABILITA' (colore), non l'intensita', con legenda 0-100%:
`nowcast_prob015.webp`, `nowcast_prob030.webp`, `nowcast_prob060.webp`
(soglia `prob_image_threshold_mmh`, default 0,5 mm/h = "piove / non piove").

Onesta' dei numeri
------------------
- Le probabilita' sono FREQUENZE DI ENSEMBLE, non probabilita' calibrate: la
  calibrazione si misura (Brier/reliability/CRPS). Se il saggio di spread non
  regge, il parametro da muovere e' dichiarato nel report, non ritoccato qui.
- La verifica (Brier, reliability, CRPS, spread-skill) e' in
  `scripts/validation/nowcast_ensemble_backtest.py`; il verdetto e i limiti in
  `docs/NOWCAST_ENSEMBLE_ESITO.md`.
- Con `ensemble.enabled=false` il modulo non viene chiamato e `nowcast.json`
  resta byte-identico a prima (nessuna chiave nuova).
"""

import math
import os
import time

import numpy as np

SCHEMA = "meteorisk.nowcast_ensemble/1"
METHOD = "motion_perturbation+nonparametric_band_noise"

MEMBERS = 20
MOTION_PARALLEL_MS = 10.88          # STEPS/BPS, citato (non tarato qui)
MOTION_PERPENDICULAR_MS = 5.76      # STEPS/BPS, citato (non tarato qui)
PARALLEL_LEVELS = (-1.0, -0.5, 0.0, 0.5, 1.0)
PERPENDICULAR_LEVELS = (-1.0, -1.0 / 3.0, 1.0 / 3.0, 1.0)

THRESHOLDS_MMH = (0.5, 2.0, 5.0, 10.0, 20.0)
QUANTILES = (10, 50, 90)
PROB_DECIMALS = 2                   # quantizzazione a 0,01
NOISE_METHOD = "nonparametric"

# perturbazione di OCCORRENZA e INTENSITA' (novembre 2026)
OCCURRENCE_BLOCK_KM = 10.0        # scala spaziale delle buche nell'eco
INTENSITY_BLOCK_KM = 20.0         # scala spaziale del fattore moltiplicativo
OCCURRENCE_MAX_DRY = 0.95         # nessun pixel resta asciutto al 100%
REFERENCE_OCCURRENCE = (
    "Rasatura dell'eco: P(asciutto | intensita') = 1 - P(pioggia >= 0,5 mm/h) "
    "MISURATA sul set di sviluppo (curva in `audit/occurrence_curve.json`), "
    "applicata con un campo uniforme spazialmente correlato. Fattore "
    "moltiplicativo log-normale per membro e pixel, media 1, "
    "deviazione sigma calibrata: allarga la coda senza spostare la massa."
)

# perturbazione del moto SPAZIALMENTE VARIABILE (novembre 2026)
MOTION_MODE = "field"               # "field" (default) | "rigid" (A/B storico)
MOTION_BLOCK_KM = 100.0             # lato del blocco di errore del moto
DEFORMATION_FRACTION = 0.6          # ampiezza deformazione / ampiezza parallela
DEFORMATION_SCALE_KM = 100.0        # la deformazione e' un tasso per 100 km
MAX_PERTURBATION_MS = 60.0          # cap sul vettore di errore (dichiarato)
MOTION_FIELD_REFERENCE = (
    "Errore di moto per blocchi da 100 km (campo liscio, non traslazione "
    "rigida) + deformazione lineare (divergenza, stiramento, taglio, "
    "rotazione) con ampiezza 0,6x la perturbazione parallela. Ampiezze da "
    "STEPS/BPS (Bowler et al. 2006; Germann et al. 2006), scalate da "
    "`motion_inflation` MISURATO sul backtest, non scelte a mano."
)

# damping del moto instabile (dichiarato, non tarato)
COHERENCE_FULL = 0.5                # coherence >= 0.5 -> nessuna riduzione
WEAK_MOTION_KMH = 5.0               # sotto: la direzione non e' affidabile
RAIN_MIN_MMH = 0.05                 # = nowcast_field.RAIN_MIN_MMH (dichiarato)

PROB_PALETTE = (
    (0.00, (235, 245, 255)),
    (0.10, (150, 205, 255)),
    (0.30, (55, 140, 250)),
    (0.50, (55, 195, 95)),
    (0.70, (245, 215, 45)),
    (0.85, (245, 130, 35)),
    (1.00, (190, 25, 60)),
)
PROB_ALPHA_MIN = 0.01               # sotto 1%: trasparente
PROB_ALPHA_FULL = 0.60              # da 60% in su: alpha pieno
PROB_ALPHA_MAX = 215
WEBP_QUALITY = 80
PROB_IMAGE_NAMES = {
    15: "nowcast_prob015.webp",
    30: "nowcast_prob030.webp",
    60: "nowcast_prob060.webp",
}


def prob_image_name(lead, thr):
    """Nome dell'immagine per (lead, soglia): `nowcast_prob015_t2p0.webp`."""
    key = ("%g" % float(thr)).replace(".", "p")
    return "nowcast_prob%03d_t%s.webp" % (int(lead), key)

REFERENCE = (
    "Perturbazione del moto e disegno a 20 membri: STEPS/BPS (Bowler, Pierce "
    "& Seed 2006, QJRMS 132:1149; Germann et al. 2006, MWR 134:2838) - "
    "+-10,88 m/s paralleli e +-5,76 m/s perpendicolari, CITATI. Rumore per "
    "banda non parametrico: STEPS `noise_method='nonparametric'` con cascata "
    "dyadica di `nowcast_cascade.py`; la varianza di ogni banda e' riportata "
    "al livello pre-cascata."
)

LIMITS = (
    "Probabilita' = frequenza di ensemble, NON calibrata in produzione: la "
    "calibrazione e' misurata dal backtest (Brier/reliability/CRPS). La "
    "perturbazione del moto e' una traslazione costante composta dopo il "
    "trasporto deterministico, non una re-integrazione per membro: errore "
    "O(Delta*grad u) misurato in audit/ensemble_advection_ab.json. Il rumore "
    "ripristina la varianza pre-cascata della banda ma non la struttura fine "
    "osservata: e' rumore con lo spettro della banda, non una previsione del "
    "dettaglio. I membri non sono indipendenti (disegno stratificato). "
    "Nessuna perturbazione dell'ambiente (CAPE, shear) e nessun membro NWP."
)


# ---------------------------------------------------------------------------
# disegno dei membri
# ---------------------------------------------------------------------------
def member_offsets(members=MEMBERS):
    """Offset (parallelo, perpendicolare) in m/s per ogni membro.

    Prodotto cartesiano deterministico di `PARALLEL_LEVELS` x
    `PERPENDICULAR_LEVELS`, troncato a `members`. Media ~0 su entrambi gli
    assi; std attesa ~7,7 m/s parallela e ~4,3 m/s perpendicolare.
    """
    out = []
    for a in PARALLEL_LEVELS:
        for b in PERPENDICULAR_LEVELS:
            out.append((float(a) * MOTION_PARALLEL_MS,
                        float(b) * MOTION_PERPENDICULAR_MS))
    return out[:int(members)]


def motion_instability(u, v, rain, pixel_km, step_s):
    """Quanto il moto e' instabile: fattore di riduzione in [0,1] + misure.

    `coherence` = |vettore mediano| / mediana della velocita' sui pixel di
    pioggia (1 = tutti i pixel concordi, 0 = direzioni incoerenti). Il fattore
    e' `clip(coherence/COHERENCE_FULL) * clip(median_speed/WEAK_MOTION_KMH)`.
    """
    rain = np.asarray(rain, dtype=bool)
    if not rain.any():
        return {"factor": 0.0, "coherence": None, "median_speed_kmh": None,
                "null_reason": "no_rain_pixels"}
    uu = np.asarray(u, dtype=np.float32)[rain]
    vv = np.asarray(v, dtype=np.float32)[rain]
    speed = np.hypot(uu, vv) * float(pixel_km) * 3600.0 / float(step_s)
    med_u = float(np.median(uu))
    med_v = float(np.median(vv))
    med_speed = float(np.median(speed))
    vec = math.hypot(med_u, med_v) * float(pixel_km) * 3600.0 / float(step_s)
    coherence = (vec / med_speed) if med_speed > 1e-6 else 0.0
    factor = min(1.0, max(0.0, coherence / COHERENCE_FULL)) * \
        min(1.0, max(0.0, med_speed / WEAK_MOTION_KMH))
    direction = {"u": med_u, "v": med_v}
    if vec > 1e-9:
        direction = {"u": med_u / math.hypot(med_u, med_v),
                     "v": med_v / math.hypot(med_u, med_v)}
    return {"factor": round(float(factor), 6),
            "coherence": round(float(coherence), 6),
            "median_speed_kmh": round(float(med_speed), 3),
            "median_vector_px_step": [round(med_u, 6), round(med_v, 6)],
            "direction_unit": {k: round(float(c), 6)
                               for k, c in direction.items()},
            "null_reason": None}


def displacement_for(lead_min, off_parallel_ms, off_perp_ms, instability,
                     pixel_km):
    """Spostamento (dx, dy) in pixel per il membro, al lead richiesto.

    La direzione e' quella del moto MEDIANO; se il moto e' instabile il
    fattore riduce l'ampiezza. La conversione m/s -> pixel usa il lead intero:
    `px = v_ms * lead_s / (pixel_km*1000)`.
    """
    d = instability.get("direction_unit") or {"u": 1.0, "v": 0.0}
    factor = float(instability.get("factor") or 0.0)
    seconds = float(lead_min) * 60.0
    scale = seconds / (float(pixel_km) * 1000.0)
    par = float(off_parallel_ms) * factor * scale
    perp = float(off_perp_ms) * factor * scale
    ux, uy = float(d.get("u", 1.0)), float(d.get("v", 0.0))
    nx, ny = uy, -ux                      # perpendicolare (rotazione -90)
    return (par * ux + perp * nx, par * uy + perp * ny)


# ---------------------------------------------------------------------------
# rumore per banda
# ---------------------------------------------------------------------------
_HALF_INDEX_CACHE = {}


def _half_band_index(shape, bands):
    """Indice di banda per lo spettro REALE (rfft2): (h, w//2+1)."""
    key = (int(shape[0]), int(shape[1]), int(bands))
    hit = _HALF_INDEX_CACHE.get(key)
    if hit is not None:
        return hit
    edges = np.asarray(nowcast_cascade_edges(bands), dtype=np.float32)
    ky = np.fft.fftfreq(int(shape[0])).astype(np.float32)
    kx = np.fft.fftfreq(int(shape[1]))[:int(shape[1]) // 2 + 1].astype(np.float32)
    k = 2.0 * np.hypot(ky[:, None], kx[None, :])
    idx = np.searchsorted(edges, k, side="right").astype(np.int8)
    _HALF_INDEX_CACHE[key] = idx
    return idx


def nowcast_cascade_edges(bands):
    """Soglie radiali dyadiche 2^-(bands-1) ... 2^-1 (stessa formula della
    cascata 1.2.4.x, che in questa linea e' stata rimossa: la cascata e'
    spenta e il rumore di banda non si applica)."""
    bands = int(bands)
    if bands < 2:
        return ()
    return tuple(2.0 ** -p for p in range(bands - 1, 0, -1))


def band_gain_arrays(state, lead_min):
    """(g[half], noise_scale[half]) per il lead, o (None, None).

    `g` = guadagno g_b della cascata per ogni frequenza; `noise_scale` =
    sqrt(1-g_b^2). Il membro e' `g*Y + noise_scale*|Y|*exp(i*theta)`:
    potenza attesa `g^2|Y|^2 + (1-g^2)|Y|^2 = |Y|^2`, cioe' la varianza di
    ogni banda del trasporto e' conservata (la parte predicibile decade, il
    resto diventa rumore). DC forzato a g=1 e rumore 0. Lo spettro e' quello
    REALE di `rfft2` (h, w//2+1).
    """
    if not state or not state.get("gains_by_lead"):
        return None, None
    gains = state["gains_by_lead"].get(int(lead_min))
    if gains is None:
        return None, None
    shape = np.shape(state["domain"])
    idx = _half_band_index(shape, len(gains))
    g = np.asarray(gains, dtype=np.float32)[idx]
    g = np.clip(g, 0.0, 1.0).copy()
    g.flat[0] = 1.0
    scale = np.sqrt(np.maximum(0.0, 1.0 - g * g)).astype(np.float32)
    scale = scale.copy()
    scale.flat[0] = 0.0
    return g.astype(np.float32), scale


def cascade_noise(field, gain_arr, noise_scale, domain, rng, support=None,
                  workers=1):
    """Trasporto -> parte predicibile (g) + rumore a fase casuale.

    Spettro REALE (`rfft2`): `Y_out = g*Y + sqrt(1-g^2)*|Y|*exp(i*theta)`.
    `irfft2` impone la simmetria hermitiana, quindi il campo e' reale per
    costruzione e la potenza attesa per frequenza e' `|Y|^2` (varianza della
    banda conservata).
    `support` limita il solo RUMORE alla maschera dell'eco: la fase casuale
    distribuisce l'energia di una banda su TUTTO il dominio e senza supporto
    comparirebbe pioggia debole dove il trasporto deterministico e' asciutto
    (misurato: probabilita' media 0,45 su un dominio con il 15% di pioggia).
    """
    from scipy import fft as sfft
    src = np.asarray(field, dtype=np.float32)
    dom = np.asarray(domain, dtype=np.float32)
    Y = sfft.rfft2(src * dom, workers=int(workers))
    amp = np.abs(Y).astype(np.float32)
    det = sfft.irfft2(Y * gain_arr, s=src.shape,
                      workers=int(workers)).astype(np.float32)
    if float(np.max(noise_scale)) <= 0.0:
        out = det * dom
        np.maximum(out, 0.0, out=out)
        return out
    theta = rng.uniform(-math.pi, math.pi, Y.shape).astype(np.float32)
    phase = np.exp(1j * theta).astype(np.complex64)
    noise = sfft.irfft2(phase * (amp * noise_scale), s=src.shape,
                        workers=int(workers)).astype(np.float32)
    if support is not None:
        noise *= np.asarray(support, dtype=np.float32)
        noise *= dom
    out = det + noise
    out *= dom
    np.maximum(out, 0.0, out=out)
    return out


_UPSAMPLE_CACHE = {}


def _upsample(coarse, shape):
    """Bilineare dal reticolo grossolano alla griglia piena (bordi replicati)."""
    from scipy import ndimage
    ny, nx = coarse.shape
    key = (int(shape[0]), int(shape[1]), int(ny), int(nx))
    coords = _UPSAMPLE_CACHE.get(key)
    if coords is None:
        yy = np.linspace(0.0, ny - 1.0, int(shape[0]), dtype=np.float32)
        xx = np.linspace(0.0, nx - 1.0, int(shape[1]), dtype=np.float32)
        coords = np.meshgrid(yy, xx, indexing="ij")
        if len(_UPSAMPLE_CACHE) > 8:
            _UPSAMPLE_CACHE.clear()
        _UPSAMPLE_CACHE[key] = coords
    return ndimage.map_coordinates(coarse, list(coords), order=1,
                                   mode="nearest").astype(np.float32)


def smooth_normal(rng, shape, pixel_km, block_km, dom):
    """Campo normale standardizzato, liscio su scala `block_km`."""
    h, w = shape
    ny = max(3, int(round(h * float(pixel_km) / float(block_km))) + 1)
    nx = max(3, int(round(w * float(pixel_km) / float(block_km))) + 1)
    z = rng.standard_normal((ny, nx)).astype(np.float32)
    z = _upsample(z, shape)
    d = np.asarray(dom, dtype=bool)
    if d.any():
        m = float(z[d].mean())
        s = float(z[d].std())
        z = (z - np.float32(m)) / np.float32(max(s, 1e-6))
    return z.astype(np.float32)


def occurrence_arrays(curve):
    """Curva [intensita', P(pioggia>=0,5)] -> array per np.interp."""
    pts = sorted([(float(p[0]), float(p[1])) for p in (curve or [])])
    if len(pts) < 2:
        return None, None
    return (np.array([p[0] for p in pts], dtype=np.float32),
            np.array([p[1] for p in pts], dtype=np.float32))


def block_motion_error(rng, shape, pixel_km, amp_parallel_ms, amp_perp_ms,
                       direction, block_km=MOTION_BLOCK_KM):
    """Errore di moto per BLOCCHI (m/s), liscio e spazialmente variabile.

    Un vettore casuale per blocco di `block_km`, nel sistema solidale al moto
    (parallelo ±amp_parallel, perpendicolare ±amp_perp), poi interpolato
    bilinearmente. Non è una traslazione rigida: blocchi adiacenti hanno
    errori diversi, quindi il campo si deforma mentre si sposta.
    """
    h, w = shape
    ny = max(3, int(round(h * float(pixel_km) / float(block_km))) + 1)
    nx = max(3, int(round(w * float(pixel_km) / float(block_km))) + 1)
    par = (rng.uniform(-1.0, 1.0, (ny, nx)).astype(np.float32)
           * np.float32(amp_parallel_ms))
    perp = (rng.uniform(-1.0, 1.0, (ny, nx)).astype(np.float32)
            * np.float32(amp_perp_ms))
    ux, uy = float(direction[0]), float(direction[1])
    nxv, nyv = uy, -ux
    du = par * np.float32(ux) + perp * np.float32(nxv)
    dv = par * np.float32(uy) + perp * np.float32(nyv)
    return _upsample(du, shape), _upsample(dv, shape)


def deformation_field(rng, shape, pixel_km, amp_ms, center_px):
    """Deformazione lineare (m/s) attorno al centroide: div, vort, stretch, shear.

    `du = a X + b Y`, `dv = c X + e Y` con
      a=(div+stretch)/2, e=(div-stretch)/2, b=(shear-vort)/2, c=(shear+vort)/2
    e X,Y in unità di `DEFORMATION_SCALE_KM`. È il termine che allunga/contrae
    il nucleo (e lo ruota), oltre a spostarlo: è la parte che decorrela i
    picchi, che una traslazione rigida non tocca.
    """
    h, w = shape
    yy = np.arange(h, dtype=np.float32)[:, None]
    xx = np.arange(w, dtype=np.float32)[None, :]
    X = ((xx - float(center_px[1])) * float(pixel_km)
         / float(DEFORMATION_SCALE_KM))
    Y = ((yy - float(center_px[0])) * float(pixel_km)
         / float(DEFORMATION_SCALE_KM))
    div = float(rng.uniform(-1.0, 1.0)) * amp_ms
    vort = float(rng.uniform(-1.0, 1.0)) * amp_ms
    stretch = float(rng.uniform(-1.0, 1.0)) * amp_ms
    shear = float(rng.uniform(-1.0, 1.0)) * amp_ms
    a = 0.5 * (div + stretch)
    e = 0.5 * (div - stretch)
    b = 0.5 * (shear - vort)
    c = 0.5 * (shear + vort)
    du = (a * X + b * Y).astype(np.float32)
    dv = (c * X + e * Y).astype(np.float32)
    return du, dv, {"divergence": round(div, 4),
                    "vorticity": round(vort, 4),
                    "stretch": round(stretch, 4),
                    "shear": round(shear, 4)}


def member_motion_error(rng, shape, pixel_km, step_s, inst, motion_inflation,
                        rain, center_px, block_km=MOTION_BLOCK_KM,
                        deformation_fraction=DEFORMATION_FRACTION):
    """Errore di moto (px/passo) per un membro: blocchi + deformazione.

    Ampiezze = valori STEPS/BPS × `motion_inflation` × fattore di stabilità
    del moto. L'errore è centrato sulla pioggia (media nulla) per non
    aggiungere una deriva globale; la deformazione è centrata sul centroide.
    Il vettore totale è limitato a `MAX_PERTURBATION_MS`.
    """
    h, w = shape
    factor = float(inst.get("factor") or 0.0)
    d = inst.get("direction_unit") or {"u": 1.0, "v": 0.0}
    direction = (float(d.get("u", 1.0)), float(d.get("v", 0.0)))
    amp_par = MOTION_PARALLEL_MS * float(motion_inflation) * factor
    amp_perp = MOTION_PERPENDICULAR_MS * float(motion_inflation) * factor
    du, dv = block_motion_error(rng, shape, pixel_km, amp_par, amp_perp,
                                direction, block_km)
    comps = None
    if amp_par > 0.0 and deformation_fraction > 0.0:
        ddu, ddv, comps = deformation_field(
            rng, shape, pixel_km, amp_par * float(deformation_fraction),
            center_px)
        du = du + ddu
        dv = dv + ddv
    rain = np.asarray(rain, dtype=bool)
    if rain.any():
        du = du - np.float32(float(du[rain].mean()))
        dv = dv - np.float32(float(dv[rain].mean()))
    k = float(step_s) / (float(pixel_km) * 1000.0)
    du = (du * np.float32(k)).astype(np.float32)
    dv = (dv * np.float32(k)).astype(np.float32)
    mag = np.hypot(du, dv)
    cap = np.float32(MAX_PERTURBATION_MS * k)
    over = mag > cap
    if bool(over.any()):
        s = np.where(over, cap / np.maximum(mag, 1e-9), 1.0).astype(np.float32)
        du = du * s
        dv = dv * s
    info = {"factor": round(factor, 6),
            "amp_parallel_ms": round(amp_par, 4),
            "amp_perp_ms": round(amp_perp, 4),
            "blocks": [int(max(3, int(round(h * float(pixel_km)
                                            / float(block_km))) + 1)),
                       int(max(3, int(round(w * float(pixel_km)
                                            / float(block_km))) + 1))],
            "block_km": float(block_km),
            "deformation": comps,
            "max_perturbation_ms": MAX_PERTURBATION_MS,
            "pixels_over_cap": int(over.sum()),
            "mean_abs_px_step": round(float(np.abs(du).mean()
                                            + np.abs(dv).mean()) / 2.0, 5)}
    return du, dv, info


# ---------------------------------------------------------------------------
# statistiche dell'ensemble
# ---------------------------------------------------------------------------
def _probabilities(stack, thresholds):
    out = {}
    for thr in thresholds:
        p = (stack >= np.float32(thr)).mean(axis=0, dtype=np.float32)
        out["prob_%s" % _thr_key(thr)] = np.round(
            p, PROB_DECIMALS).astype(np.float32)
    return out


def _thr_key(thr):
    return ("%.1f" % float(thr)).replace(".", "p")


def _quantiles(stack, quantiles=QUANTILES):
    """p10/p50/p90 senza upcast a float64 (partition su float32)."""
    m = stack.shape[0]
    idx = [int(round((q / 100.0) * (m - 1))) for q in quantiles]
    part = np.partition(stack, idx, axis=0)
    out = {}
    for q, i in zip(quantiles, idx):
        out["p%d" % int(q)] = part[i].astype(np.float32)
    return out


def _spread(stack):
    return stack.std(axis=0, dtype=np.float32)


def _prob_stats(prob, domain, pixel_km):
    """Sintesi pubblicabile di una mappa di probabilita' (non la mappa)."""
    p = np.asarray(prob, dtype=np.float32)[domain]
    if p.size == 0:
        return {"pixels": 0, "mean": None, "max": None,
                "fraction_ge_0p5": None}
    return {"pixels": int(p.size),
            "mean": round(float(p.mean()), 4),
            "max": round(float(p.max()), 4),
            "fraction_ge_0p5": round(float((p >= 0.5).mean()), 6),
            "area_ge_0p5_km2": round(
                float((p >= 0.5).sum()) * float(pixel_km) ** 2, 1)}


# ---------------------------------------------------------------------------
# immagini: colore = PROBABILITA' (non intensita')
# ---------------------------------------------------------------------------
def probability_rgba(prob):
    """Probabilita' 0-1 -> RGBA. Alpha 0 sotto PROB_ALPHA_MIN."""
    v = np.asarray(prob, dtype=np.float32)
    vv = np.where(np.isfinite(v), np.clip(v, 0.0, 1.0), 0.0)
    out = np.zeros((vv.shape[0], vv.shape[1], 4), dtype=np.uint8)
    nodes = np.array([p[0] for p in PROB_PALETTE], dtype=np.float32)
    cols = np.array([p[1] for p in PROB_PALETTE], dtype=np.float32)
    for ch in range(3):
        out[:, :, ch] = np.interp(vv, nodes, cols[:, ch]).astype(np.uint8)
    ramp = np.clip((vv - PROB_ALPHA_MIN) / (PROB_ALPHA_FULL - PROB_ALPHA_MIN),
                   0.0, 1.0)
    alpha = (ramp * PROB_ALPHA_MAX).astype(np.uint8)
    alpha[~np.isfinite(v)] = 0
    alpha[vv < PROB_ALPHA_MIN] = 0
    out[:, :, 3] = alpha
    return out


def write_prob_webp(path, prob, quality=WEBP_QUALITY):
    """Scrive la mappa di probabilita' come WebP RGBA. Ritorna i byte."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("pillow_missing") from exc
    img = Image.fromarray(np.ascontiguousarray(probability_rgba(prob)))
    tmp = path + ".tmp"
    img.save(tmp, format="WEBP", quality=int(quality), method=4)
    os.replace(tmp, path)
    return os.path.getsize(path)


def legend_payload():
    return [{"prob": float(p[0]), "rgb": list(p[1])} for p in PROB_PALETTE]


# ---------------------------------------------------------------------------
# ensemble
# ---------------------------------------------------------------------------
def _stats_for_lead(members, thresholds, dom, pixel_km):
    """Probabilità/quantili/spread + sintesi per un lead. Ritorna (fields, stats)."""
    stack = np.stack(members, axis=0)
    probs = _probabilities(stack, thresholds)
    quants = _quantiles(stack)
    spread = _spread(stack)
    stats = {
        "spread_mean_mmh": round(float(spread[dom].mean()), 4)
        if dom.any() else None,
        "spread_max_mmh": round(float(spread[dom].max()), 4)
        if dom.any() else None,
        "prob": {k: _prob_stats(v, dom, pixel_km) for k, v in probs.items()},
        "quantiles": {k: _prob_stats(v, dom, pixel_km)
                      for k, v in quants.items()},
    }
    fields = {"probabilities": probs, "quantiles": quants, "spread": spread,
              "members": stack, "mean": stack.mean(axis=0, dtype=np.float32)}
    return fields, stats


def run_leads(transports, u, v, domain, leads, cascade_state, cfg, pixel_km,
              step_s, seed_slot, latest=None, post=None, rain_mask=None,
              mass_target=None):
    """Ensemble su TUTTI i lead costruendo ogni membro una volta sola.

    `transports` = {lead: campo trasportato pre-cascata} (modalità `rigid`).
    `latest` = campo osservato all'ancora (modalità `field`: si integra da qui
    con il moto perturbato per membro). `post(field, lead)` = matching+decay.
    Ritorna ({lead: blocco}, {lead: campi}).
    """
    cfg = cfg or {}
    t0 = time.time()
    dom = np.asarray(domain, dtype=bool)
    leads_sorted = sorted(int(x) for x in leads)
    if rain_mask is None:
        base_ref = transports.get(leads_sorted[0]) if transports else latest
        rain = dom & (np.asarray(base_ref) >= RAIN_MIN_MMH)
    else:
        rain = np.asarray(rain_mask, dtype=bool) & dom
    inst = motion_instability(u, v, rain, pixel_km, step_s)
    members = int(cfg.get("members") or MEMBERS)
    thresholds = tuple(float(x) for x in
                       (cfg.get("thresholds_mmh") or THRESHOLDS_MMH))
    workers = int(cfg.get("fft_workers") or 1)
    mode = str(cfg.get("motion_mode") or MOTION_MODE)
    integration = str(cfg.get("integration") or "single")
    motion_inflation = float(cfg.get("motion_inflation") or 1.0)
    noise_inflation = float(cfg.get("noise_inflation") or 1.0)
    offsets = member_offsets(members)
    block_km = float(cfg.get("motion_block_km") or MOTION_BLOCK_KM)
    deformation_fraction = float(cfg.get("deformation_fraction")
                                 if cfg.get("deformation_fraction") is not None
                                 else DEFORMATION_FRACTION)
    ur = np.asarray(u, dtype=np.float32)
    vr = np.asarray(v, dtype=np.float32)
    if rain.any():
        rr, cc = np.nonzero(rain)
        center_px = (float(rr.mean()), float(cc.mean()))
    else:
        center_px = (dom.shape[0] / 2.0, dom.shape[1] / 2.0)
    gains = {}
    for lead in leads_sorted:
        gain, nscale = band_gain_arrays(cascade_state, lead)
        if nscale is not None and noise_inflation != 1.0:
            nscale = (nscale * np.float32(noise_inflation)).astype(np.float32)
        gains[lead] = (gain, nscale)
    intensity_sigma = float(cfg.get("intensity_sigma") or 0.0)
    tail_factor = float(cfg.get("tail_factor") or 1.0)
    tail_disp = float(cfg.get("tail_dispersion") or 0.15)
    tail_pct = float(cfg.get("tail_percentile") or 90.0)
    occ_scale = float(cfg.get("occurrence_scale") or 0.0)
    occ_i, occ_p = occurrence_arrays(cfg.get("occurrence_curve"))
    occ_block = float(cfg.get("occurrence_block_km") or OCCURRENCE_BLOCK_KM)
    int_block = float(cfg.get("intensity_block_km") or INTENSITY_BLOCK_KM)

    acc = {lead: [] for lead in leads_sorted}
    t_adv = t_noise = t_post = 0.0
    motion_info = None
    for i in range(members):
        rng = np.random.default_rng(
            (int(seed_slot) // 60000) * 1000003 + i * 7919)
        # campi di perturbazione COERENTI fra i lead dello stesso membro
        rng_pert = np.random.default_rng(
            (int(seed_slot) // 60000) * 1000003 + i * 7919 + 77777)
        z_int = (smooth_normal(rng_pert, dom.shape, pixel_km, int_block, dom)
                 if intensity_sigma > 0.0 else None)
        z_occ = (smooth_normal(rng_pert, dom.shape, pixel_km, occ_block, dom)
                 if (occ_scale > 0.0 and occ_i is not None) else None)
        u_occ = None
        if z_occ is not None:
            from scipy.special import ndtr
            u_occ = ndtr(z_occ).astype(np.float32)
        k_m = 1.0
        if tail_factor > 1.0:
            # fattore di coda per membro: media ~tail_factor, dispersione
            # `tail_disp` fra i membri (le code non sono tutte uguali)
            zk = float(rng_pert.standard_normal())
            k_m = 1.0 + (tail_factor - 1.0) * (1.0 + tail_disp * zk)
            k_m = float(np.clip(k_m, 1.0, 1.0 + 3.0 * (tail_factor - 1.0)))
        base_by_lead = {}
        if mode == "rigid" and int(members) <= len(offsets):
            par, perp = offsets[i]
            dx, dy = displacement_for(leads_sorted[-1],
                                      par * motion_inflation,
                                      perp * motion_inflation, inst, pixel_km)
            ta = time.time()
            for lead in leads_sorted:
                src = transports[lead]
                if cfg.get("advection_mode") == "advect" and latest is not None:
                    steps = max(1, int(round(float(lead) / 5.0)))
                    from . import nowcast_field as nf
                    base_by_lead[lead] = nf.semi_lagrangian(
                        np.asarray(latest, np.float32),
                        ur + np.float32(dx / steps),
                        vr + np.float32(dy / steps), steps, dom)
                else:
                    from . import nowcast_field as nf
                    du_f = np.full(dom.shape, np.float32(-dx), dtype=np.float32)
                    dv_f = np.full(dom.shape, np.float32(-dy), dtype=np.float32)
                    shifted, inside = nf._warp(np.asarray(src, np.float32),
                                               du_f, dv_f)
                    base_by_lead[lead] = np.where(inside & dom, shifted,
                                                  0.0).astype(np.float32)
            t_adv += time.time() - ta
        else:
            du_px, dv_px, motion_info = member_motion_error(
                rng, dom.shape, pixel_km, step_s, inst, motion_inflation,
                rain, center_px, block_km=block_km,
                deformation_fraction=deformation_fraction)
            from . import nowcast_field as nf
            ta = time.time()
            if integration == "single":
                # spostamento TOTALE composto dopo il trasporto deterministico:
                # 1 warp per lead invece di L/5. Il campo di errore resta
                # spazialmente variabile (blocchi + deformazione); cambia solo
                # il punto in cui si valuta lo spostamento (fine corsa).
                for lead in leads_sorted:
                    steps = int(lead // 5)
                    du_f = (du_px * np.float32(steps)).astype(np.float32)
                    dv_f = (dv_px * np.float32(steps)).astype(np.float32)
                    shifted, inside = nf._warp(
                        np.asarray(transports[lead], np.float32),
                        -du_f, -dv_f)
                    base_by_lead[lead] = np.where(inside & dom, shifted,
                                                  0.0).astype(np.float32)
                motion_info["integration"] = "single_shift"
            else:
                field = np.asarray(latest, np.float32).copy()
                done = 0
                for lead in leads_sorted:
                    steps = int(lead // 5)
                    while done < steps:
                        field = nf.semi_lagrangian(field, ur + du_px,
                                                   vr + dv_px, 1, dom)
                        done += 1
                    base_by_lead[lead] = field
                motion_info["integration"] = "sequential_steps"
            t_adv += time.time() - ta
        for lead in leads_sorted:
            field = base_by_lead[lead]
            gain, nscale = gains[lead]
            if gain is not None and nscale is not None and \
                    float(nscale.max()) > 0.0:
                tn = time.time()
                support = None
                if str(cfg.get("noise_support") or "echo") == "echo":
                    support = (field >= RAIN_MIN_MMH)
                field = cascade_noise(field, gain, nscale, dom, rng,
                                      support=support, workers=workers)
                t_noise += time.time() - tn
            else:
                field = np.where(dom, field, 0.0).astype(np.float32)
            if post is not None:
                tp = time.time()
                field = np.asarray(post(field, lead), dtype=np.float32)
                field = np.where(dom, field, 0.0).astype(np.float32)
                t_post += time.time() - tp
            if z_int is not None:
                # fattore log-normale a media 1: allarga la coda senza
                # spostare la massa attesa (la massa esatta la impone il passo
                # successivo)
                field = (field * np.exp(
                    np.float32(intensity_sigma) * z_int
                    - np.float32(0.5 * intensity_sigma ** 2))).astype(
                        np.float32)
            if mass_target and lead in mass_target:
                # la deformazione divergente sposterebbe l'acqua senza
                # conservarla: ogni membro conserva la massa del campo
                # deterministico (stesso integrale di pioggia sul dominio).
                target = float(mass_target[lead])
                mass = float(field.sum(dtype=np.float64))
                if mass > 0.0 and target > 0.0:
                    field = (field * np.float32(target / mass)).astype(
                        np.float32)
                    field = np.where(dom, field, 0.0).astype(np.float32)
            if u_occ is not None:
                p_rain = np.interp(field, occ_i, occ_p).astype(np.float32)
                p_dry = np.clip(np.float32(occ_scale) * (1.0 - p_rain),
                                0.0, np.float32(OCCURRENCE_MAX_DRY))
                cutoff = float(cfg.get("occurrence_max_intensity") or 0.0)
                if cutoff > 0.0:
                    # rasatura solo dell'eco DEBOLE: sopra il taglio la
                    # probabilita' di 2/5 mm/h non deve calare
                    expo = np.clip((field - np.float32(cutoff)) / np.float32(0.25),
                                   -50.0, 50.0).astype(np.float32)
                    taper = 1.0 / (1.0 + np.exp(expo))
                    p_dry = p_dry * taper.astype(np.float32)
                field = np.where(u_occ < p_dry, np.float32(0.0), field)
                field = np.where(dom, field, 0.0).astype(np.float32)
            if k_m > 1.0:
                # coda: moltiplicatore SOLO sopra il p90 dell'intensita'
                wet = dom & (field > 0.0)
                if bool(wet.any()):
                    thr = float(np.percentile(field[wet], tail_pct))
                    hi = field > np.float32(thr)
                    field = np.where(hi, field * np.float32(k_m), field)
                    field = np.where(dom, field, 0.0).astype(np.float32)
            acc[lead].append(field)

    blocks = {}
    fields = {}
    t_stat = time.time()
    for lead in leads_sorted:
        f, st = _stats_for_lead(acc[lead], thresholds, dom, pixel_km)
        fields[lead] = f
        blocks[lead] = {
            "schema": SCHEMA, "enabled": True, "applied": True,
            "null_reason": None, "method": METHOD, "members": members,
            "leads_min": [lead], "thresholds_mmh": list(thresholds),
            "prob_decimals": PROB_DECIMALS,
            "motion_perturbation": {
                "parallel_ms": MOTION_PARALLEL_MS,
                "perpendicular_ms": MOTION_PERPENDICULAR_MS,
                "design": ("field_block+deformation" if mode != "rigid"
                           else "stratified_deterministic"),
                "mode": mode,
                "integration": integration,
                "block_km": block_km,
                "deformation_fraction": deformation_fraction,
                "instability": inst,
                "motion_inflation": motion_inflation,
                "noise_inflation": noise_inflation,
                "error_field": motion_info,
            },
            "noise": {
                "method": NOISE_METHOD,
                "applied": bool(gains[lead][1] is not None
                                and float(gains[lead][1].max()) > 0.0),
                "null_reason": (None if (gains[lead][1] is not None
                                         and float(gains[lead][1].max()) > 0.0)
                                else "cascade_gains_unavailable"),
                "variance_conservation":
                    "band_power_expected_equal_to_transport_(g^2 + 1-g^2)",
                "support": str(cfg.get("noise_support") or "echo"),
                "post_transform_applied": bool(post is not None),
            },
            "stats": st,
            "cost_seconds": {"total": round(time.time() - t0, 3)},
            "pixels": int(dom.sum()),
            "reference": REFERENCE,
            "limits": LIMITS,
        }
    t_stat = time.time() - t_stat
    share = {"advection": round(t_adv, 3), "noise": round(t_noise, 3),
             "post": round(t_post, 3)}
    for lead in leads_sorted:
        blocks[lead]["cost_seconds"] = dict(
            share, statistics=round(t_stat / max(1, len(leads_sorted)), 3),
            total=round(time.time() - t0, 3))
    return blocks, fields


def run(transport_field, u, v, domain, leads, cascade_state, cfg, pixel_km,
        step_s, seed_slot, advect_latest=None, rain_mask=None, post=None):
    """Variante a UN lead (rigid/composite), usata da test e A/B storici.

    La produzione usa `run_leads` (moto variabile, tutti i lead, massa).
    `transport_field` = campo trasportato pre-cascata; `advect_latest` =
    `(latest, u, v, domain)` per la modalita' esatta; `rain_mask` = pioggia
    osservata per misurare la stabilita' del moto.
    """
    cfg = cfg or {}
    t0 = time.time()
    dom = np.asarray(domain, dtype=bool)
    if rain_mask is None:
        rain = dom & (np.asarray(transport_field) >= RAIN_MIN_MMH)
    else:
        rain = np.asarray(rain_mask, dtype=bool) & dom
    inst = motion_instability(u, v, rain, pixel_km, step_s)
    offsets = member_offsets(int(cfg.get("members") or MEMBERS))
    thresholds = tuple(float(x) for x in
                       (cfg.get("thresholds_mmh") or THRESHOLDS_MMH))
    workers = int(cfg.get("fft_workers") or 1)
    mode = str(cfg.get("advection_mode") or "composite")
    gain, nscale = band_gain_arrays(cascade_state, leads)
    motion_inflation = float(cfg.get("motion_inflation") or 1.0)
    noise_inflation = float(cfg.get("noise_inflation") or 1.0)
    if nscale is not None and noise_inflation != 1.0:
        nscale = (nscale * np.float32(noise_inflation)).astype(np.float32)

    members = []
    t_adv = 0.0
    t_noise = 0.0
    t_post = 0.0
    for i, (par, perp) in enumerate(offsets):
        dx, dy = displacement_for(leads, par * motion_inflation,
                                  perp * motion_inflation, inst, pixel_km)
        if mode == "advect" and advect_latest is not None:
            latest, _u, _v, _dom = advect_latest
            steps = max(1, int(round(float(leads) / 5.0)))
            du = dx / float(steps)
            dv = dy / float(steps)
            ta = time.time()
            from . import nowcast_field as nf
            field = nf.semi_lagrangian(np.asarray(latest, np.float32),
                                       _u + np.float32(du), _v + np.float32(dv),
                                       steps, _dom)
            t_adv += time.time() - ta
        else:
            from . import nowcast_field as nf
            ta = time.time()
            du_field = np.full(dom.shape, np.float32(-dx), dtype=np.float32)
            dv_field = np.full(dom.shape, np.float32(-dy), dtype=np.float32)
            shifted, inside = nf._warp(np.asarray(transport_field,
                                                  dtype=np.float32),
                                       du_field, dv_field)
            field = np.where(inside & dom, shifted, 0.0).astype(np.float32)
            t_adv += time.time() - ta
        if gain is not None and float(nscale.max()) > 0.0:
            tn = time.time()
            rng = np.random.default_rng(
                (int(seed_slot) // 60000) * 1000003
                + i * 7919 + int(leads) * 40503)
            support = None
            if str(cfg.get("noise_support") or "echo") == "echo":
                support = (field >= RAIN_MIN_MMH)
            field = cascade_noise(field, gain, nscale, dom, rng,
                                  support=support, workers=workers)
            t_noise += time.time() - tn
        else:
            field = np.where(dom, field, 0.0).astype(np.float32)
        if post is not None:
            tp = time.time()
            field = np.asarray(post(field, leads), dtype=np.float32)
            field = np.where(dom, field, 0.0).astype(np.float32)
            t_post += time.time() - tp
        members.append(field)

    t_stat = time.time()
    stack = np.stack(members, axis=0)
    probs = _probabilities(stack, thresholds)
    quants = _quantiles(stack)
    spread = _spread(stack)
    t_stat = time.time() - t_stat

    noise_applied = bool(gain is not None and nscale is not None
                         and float(nscale.max()) > 0.0)
    block = {
        "schema": SCHEMA,
        "enabled": True,
        "applied": True,
        "null_reason": None,
        "method": METHOD,
        "members": len(members),
        "leads_min": [int(leads)],
        "thresholds_mmh": list(thresholds),
        "prob_decimals": PROB_DECIMALS,
        "motion_perturbation": {
            "parallel_ms": MOTION_PARALLEL_MS,
            "perpendicular_ms": MOTION_PERPENDICULAR_MS,
            "levels_parallel": list(PARALLEL_LEVELS),
            "levels_perpendicular": list(PERPENDICULAR_LEVELS),
            "design": "stratified_deterministic",
            "instability": inst,
            "motion_inflation": motion_inflation,
            "noise_inflation": noise_inflation,
            "applied_offset_px": {
                "dx": round(float(displacement_for(leads, MOTION_PARALLEL_MS,
                                                   0.0, inst, pixel_km)[0]), 4),
                "dy": round(float(displacement_for(leads, 0.0,
                                                   MOTION_PERPENDICULAR_MS,
                                                   inst, pixel_km)[1]), 4)},
            "advection_mode": mode,
        },
        "noise": {
            "method": NOISE_METHOD,
            "applied": noise_applied,
            "null_reason": (None if noise_applied
                            else "cascade_gains_unavailable"),
            "variance_conservation": ("band_power_expected_equal_to_transport_"
                                       "(g^2 + 1-g^2)"),
            "support": str(cfg.get("noise_support") or "echo"),
            "post_transform_applied": bool(post is not None),
        },
        "stats": {
            "spread_mean_mmh": round(float(spread[dom].mean()), 4)
            if dom.any() else None,
            "spread_max_mmh": round(float(spread[dom].max()), 4)
            if dom.any() else None,
            "prob": {k: _prob_stats(v, dom, pixel_km)
                     for k, v in probs.items()},
            "quantiles": {k: _prob_stats(v, dom, pixel_km)
                          for k, v in quants.items()},
        },
        "cost_seconds": {
            "advection": round(t_adv, 3),
            "noise": round(t_noise, 3),
            "post": round(t_post, 3),
            "statistics": round(t_stat, 3),
            "total": round(time.time() - t0, 3),
        },
        "pixels": int(dom.sum()),
        "reference": REFERENCE,
        "limits": LIMITS,
    }
    fields = {"probabilities": probs, "quantiles": quants, "spread": spread}
    if cfg.get("keep_members"):
        fields["members"] = stack
        fields["mean"] = stack.mean(axis=0, dtype=np.float32)
    return block, fields


def disabled_payload(reason="disabled_by_config", cfg=None):
    """Blocco `ensemble` quando il flag e' spento: dichiara, non simula."""
    cfg = cfg or {}
    return {
        "schema": SCHEMA,
        "enabled": False,
        "applied": False,
        "null_reason": reason,
        "method": METHOD,
        "members": int(cfg.get("members") or MEMBERS),
        "thresholds_mmh": [float(x) for x in
                           (cfg.get("thresholds_mmh") or THRESHOLDS_MMH)],
        "reference": REFERENCE,
        "limits": LIMITS,
    }
