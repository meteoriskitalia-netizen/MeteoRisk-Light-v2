#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Server-side pre-render of forecast-v2 (.om) frames to WebP.

Reads the engine output layout ``<in>/<domain>/latest.json`` plus the
per-frame ``<VALID>.om`` files, dequantizes each requested variable with the
omfiles reader, remaps rows into Web Mercator vertical spacing, colors every
pixel with the exact client scale (``color_scales.json``, WML v0.2.2) and
writes, for every requested REGION, ``<out>/<region>/<domain>/<var>/<VALID>.webp``
together with a per-variable ``latest.json`` and a per-region ``index.json``
consumable by the client's WebP path.

1.2.4.13 changes:
  * The DARK theme has been REMOVED: only ``light`` is rendered and the
    legacy ``<var>/dark/`` subtrees are pruned from the published tree.
  * The isolines (with the VALUE written on the image) and the WIND ARROWS
    are baked into the WebP: they are drawn on a separate, supersampled RGBA
    layer and alpha-composited over the base raster, which is NEVER
    resampled (the pixels where the overlay is transparent stay
    byte-identical to the base raster).

Regions
-------
The raster fast-path only ships two overlapping areas (``italia`` and
``europa``), NOT the whole globe. For each (region, domain) pair the rendered
extent is the intersection of the domain coverage ``bbox`` (engine-written,
manifest key ``bbox``) with the region bbox; pairs that do not intersect are
skipped. The raw frame array is cropped to that intersection on the domain grid
and only THEN resampled to Mercator, so each WebP spans exactly its own
``imageOverlay`` bounds.

Design notes (empirically confirmed against the live engine output):
  * ``OmFileReader.read_array`` returns values already in real units
    (precip mm, temperaure degC, cloud %) -- no manual scale/offset.
  * Frame arrays are 2D ``(ny, nx)`` float32 with row 0 = south (ascending
    latitude); scalar children carry ``crs_wkt``/``coordinates``/times.
  * The grid used for reprojection comes from the manifest ``grid`` block
    (already the domain's cropped ``out_grid``), NOT from the source ``crs_wkt``.
  * Color lookup is the DISCRETE "breakpoint" step used by the WML worker:
    ``colors[max(0, last_index_with_breakpoint <= value)]`` with raster alpha
    scaled to 0..255 and NaN forced transparent.

This script is standalone: the RENDER path only needs ``numpy``, ``omfiles`` and
``Pillow``. The heavy deps are captured lazily at import time (the ImportError is
stored, not raised) so the ``--index-only`` mode (pure JSON/filesystem) works even
in a bare Python env; the render path re-FATALS them on first use via
``_require_render_deps()`` with the same messages as before.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import re
import shutil
import sys
import time
import traceback

# Heavy deps (numpy/PIL/omfiles): capture ImportError at import time WITHOUT
# raising, so `--index-only` (no rendering) imports cleanly in a bare env.
# The render path calls _require_render_deps() and gets the original FATAL.
_IMPORT_ERRORS: dict = {}

try:
    import numpy as np
except Exception as exc:  # pragma: no cover
    np = None
    _IMPORT_ERRORS["numpy"] = exc

try:
    from PIL import Image
except Exception as exc:  # pragma: no cover
    Image = None
    _IMPORT_ERRORS["PIL"] = exc

try:
    from PIL import ImageDraw
except Exception as exc:  # pragma: no cover
    ImageDraw = None
    _IMPORT_ERRORS["PIL"] = exc

try:
    from PIL import ImageFont
except Exception as exc:  # pragma: no cover
    ImageFont = None
    _IMPORT_ERRORS["PIL"] = exc

try:
    from omfiles import OmFileReader
except Exception as exc:  # pragma: no cover
    OmFileReader = None
    _IMPORT_ERRORS["omfiles"] = exc


def _require_render_deps() -> None:
    """FATAL (same messages as the old eager import) if a render dep is missing."""
    if not _IMPORT_ERRORS:
        return
    for name, exc in _IMPORT_ERRORS.items():
        if name == "PIL":
            print("FATAL: Pillow (PIL) is required: %s" % exc, file=sys.stderr)
        elif name == "omfiles":
            print("FATAL: omfiles is required: %s" % exc, file=sys.stderr)
        else:
            print("FATAL: %s is required: %s" % (name, exc), file=sys.stderr)
    raise next(iter(_IMPORT_ERRORS.values()))


HERE = os.path.dirname(os.path.abspath(__file__))
COLOR_SCALES_PATH = os.path.join(HERE, "color_scales.json")

WEBP_VERSION = "fm2-webp-1"
MERC_LAT_LIMIT = 85.05112878
UPSAMPLE_TARGET = 512
UPSCALE_MAX_DEFAULT = 8

# Rendered regions, keyed by region id. Tuple is (lat_min, lon_min, lat_max,
# lon_max) -- the SAME ordering convention used by the engine (V2_BBOX /
# DOMAIN_BBOX). ``None`` is the "domain" sentinel: the region spans the whole
# engine manifest bbox (the domain's own coverage), NOT a fixed window.
REGIONS = {
    "italia": (36.5, 6.5, 47.2, 18.6),
    "europa": (34.0, -25.0, 72.0, 45.0),
    # Vista globale dei modelli tagliati sul Nord (dai 20°N al limite Mercator).
    # L'immagine resta in EPSG:3857, quindi si apre direttamente in Leaflet.
    "north": (20.0, -180.0, 85.05112878, 180.0),
    "domain": None,
}

# The 'domain' sentinel region is rendered only for NON-global domains: a global
# domain's "whole domain" is the globe, which the client already reaches through
# its other regions and which is far too large to publish as a single WebP.
# "Global" is decided by _is_global_bbox, whose thresholds are ALIGNED to the
# client predicate fmIsGlobalDomain (mri-light-1.2.4.11.html:
# lon_span > 300 || lat_span > 120) -- the same classification on both sides.
DOMAIN_REGION = "domain"
DOMAIN_REGION_SKIP_GLOBAL = True
NORTH_REGION = "north"
NORTH_REGION_SKIP_NON_GLOBAL = True


# --------------------------------------------------------------------------- #
# Color scales
# --------------------------------------------------------------------------- #
def load_color_scales(path: str = COLOR_SCALES_PATH) -> dict:
    """Load the WML v0.2.2 color scales (tolerates a UTF-8 BOM)."""
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    scales = data.get("scales")
    if not isinstance(scales, dict) or not scales:
        raise ValueError("color_scales.json: missing/empty 'scales' map")
    return scales


def _scale_variant(scales: dict, var: str, theme: str):
    """Return the {breakpoints, colors, unit} dict for var/theme, or None.

    Every shipped scale is nested ``{dark:{...}, light:{...}}``; a flat scale
    (no variants) is also tolerated defensively.
    """
    entry = scales.get(var)
    if entry is None:
        return None
    if "breakpoints" in entry and "colors" in entry:
        return entry  # already flat
    variant = entry.get(theme) or entry.get("light") or entry.get("dark")
    if variant is None or "breakpoints" not in variant or "colors" not in variant:
        return None
    return variant


def make_lookup(scales: dict, var: str, theme: str):
    """Build a vectorized RGBA lookup for one variable/theme.

    Returns ``None`` when no scale exists for the variable.
    """
    variant = _scale_variant(scales, var, theme)
    if variant is None:
        return None

    bps = np.asarray(variant["breakpoints"], dtype=np.float64)
    cols = np.asarray(variant["colors"], dtype=np.float64)
    if bps.ndim != 1 or cols.ndim != 2 or cols.shape[1] < 4 or cols.shape[0] != bps.shape[0]:
        raise ValueError("color_scales.json: malformed scale for %r" % var)

    rgb = np.clip(cols[:, 0:3], 0.0, 255.0).astype(np.uint8)
    alpha = np.clip(np.rint(cols[:, 3] * 255.0), 0.0, 255.0).astype(np.uint8)
    n = bps.shape[0]

    def lookup(values: np.ndarray) -> np.ndarray:
        """values: 2D float array -> RGBA uint8 (h, w, 4), NaN transparent."""
        flat = np.asarray(values, dtype=np.float64).reshape(-1)
        idx = np.searchsorted(bps, flat, side="right") - 1
        np.clip(idx, 0, n - 1, out=idx)
        out = np.empty((flat.size, 4), dtype=np.uint8)
        out[:, 0:3] = rgb[idx]
        out[:, 3] = alpha[idx]
        bad = ~np.isfinite(flat)
        if bad.any():
            out[bad, :] = 0
        return out.reshape(values.shape[0], values.shape[1], 4)

    return lookup


# --------------------------------------------------------------------------- #
# Reprojection (latitude -> Web Mercator vertical spacing)
# --------------------------------------------------------------------------- #
def _merc_y(lat_deg: float) -> float:
    lat = max(-MERC_LAT_LIMIT, min(MERC_LAT_LIMIT, float(lat_deg)))
    return math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0))


def _inv_merc_y(y: float) -> float:
    return math.degrees(2.0 * math.atan(math.exp(y)) - math.pi / 2.0)


def _upsample_factor(nx: int, ny: int, target: int, max_scale: int) -> int:
    """Integer upsampling factor for a grid, clamped to [1, max_scale]."""
    shorter = min(int(nx), int(ny))
    if shorter < 1 or target <= 0:
        return 1
    return max(1, min(int(max_scale), int(math.ceil(target / shorter))))


def resolve_upsample(args, nx: int, ny: int) -> int:
    """Resolve the per-crop upsampling factor from the parsed CLI arguments."""
    up = getattr(args, "upsample", "auto")
    if isinstance(up, str) and up.strip().lower() == "auto":
        return _upsample_factor(nx, ny, UPSAMPLE_TARGET,
                                int(getattr(args, "upsample_max", UPSCALE_MAX_DEFAULT)))
    try:
        return max(1, int(up))
    except (TypeError, ValueError):
        return 1


def build_row_resampler(grid: dict, scale: int = 1):
    """Return f(arr)->arr remapping rows to uniform Mercator spacing (row0=north).

    ``scale == 1`` is the historical row-only resampler: width stays ``nx``,
    height stays ``ny`` and the result is byte-identical. ``scale > 1``
    BILINEARLY interpolates the DATA VALUES onto an
    ``((nx-1)*scale+1, (ny-1)*scale+1)`` grid (Mercator rows, linear-lon
    columns) BEFORE the discrete breakpoint lookup, so band edges become smooth
    while every pixel still wears an EXACT palette color (never a blend). Fully
    vectorized with numpy.

    NaN policy: a non-finite sample never gains a color. At ``scale > 1`` a
    pixel is emitted only when ALL of its bilinear source corners are finite;
    otherwise it is NaN (transparent). At ``scale == 1`` the historical two-tap
    rule is kept verbatim.
    """
    ny = int(grid["ny"])
    nx = int(grid["nx"])
    dy = float(grid["dy"])
    lat_min = float(grid["latMin"])
    lat_north = lat_min + (ny - 1) * dy
    scale = max(1, int(scale))

    if scale <= 1:
        if ny <= 1:
            return lambda arr: np.asarray(arr, dtype=np.float32).copy()
        my_south = _merc_y(lat_min)
        my_north = _merc_y(lat_north)
        span = my_north - my_south
        positions = np.empty(ny, dtype=np.float64)
        for r in range(ny):
            t = r / (ny - 1)
            lat = _inv_merc_y(my_north - t * span)
            positions[r] = (lat - lat_min) / dy
        r0 = np.clip(np.floor(positions).astype(np.int64), 0, ny - 1)
        r1 = np.clip(r0 + 1, 0, ny - 1)
        w = (positions - r0)[:, None]

        def resample_1(arr: np.ndarray) -> np.ndarray:
            a = np.asarray(arr, dtype=np.float32)
            if a.shape[0] != ny:
                raise ValueError("row count %d != grid ny %d" % (a.shape[0], ny))
            top = a[r0, :]
            bot = a[r1, :]
            out = top * (1.0 - w) + bot * w
            fa = np.isfinite(top)
            fb = np.isfinite(bot)
            out = np.where(~fa & fb, bot, out)
            out = np.where(fa & ~fb, top, out)
            out = np.where(~fa & ~fb, np.float32(np.nan), out)
            return out.astype(np.float32)

        return resample_1

    out_nx = (nx - 1) * scale + 1
    out_ny = (ny - 1) * scale + 1
    col_pos = np.arange(out_nx, dtype=np.float64) / scale
    c0 = np.clip(col_pos.astype(np.int64), 0, nx - 1)
    c1 = np.clip(c0 + 1, 0, nx - 1)
    wc = (col_pos - c0).astype(np.float32)
    my_south = _merc_y(lat_min)
    my_north = _merc_y(lat_north)
    span = my_north - my_south
    row_pos = np.empty(out_ny, dtype=np.float64)
    for r in range(out_ny):
        t = r / (out_ny - 1)
        lat = _inv_merc_y(my_north - t * span)
        row_pos[r] = (lat - lat_min) / dy
    r0 = np.clip(np.floor(row_pos).astype(np.int64), 0, ny - 1)
    r1 = np.clip(r0 + 1, 0, ny - 1)
    wr = (row_pos - r0).astype(np.float32)
    wR0 = (1.0 - wr)[:, None]
    wR1 = wr[:, None]
    wC0 = (1.0 - wc)[None, :]
    wC1 = wc[None, :]
    one = np.float32(1.0)
    cover_eps = np.float32(1e-6)

    def resample_up(arr: np.ndarray) -> np.ndarray:
        a = np.asarray(arr, dtype=np.float32)
        if a.shape[0] != ny or a.shape[1] != nx:
            raise ValueError("shape %r != grid (ny=%d, nx=%d)"
                             % (tuple(a.shape), ny, nx))
        finite = np.isfinite(a)
        af = np.where(finite, a, np.float32(0.0))
        v00 = af[r0][:, c0]; v01 = af[r0][:, c1]
        v10 = af[r1][:, c0]; v11 = af[r1][:, c1]
        m00 = finite[r0][:, c0]; m01 = finite[r0][:, c1]
        m10 = finite[r1][:, c0]; m11 = finite[r1][:, c1]
        num = (v00 * wC0 + v01 * wC1) * wR0 + (v10 * wC0 + v11 * wC1) * wR1
        den = (m00 * wC0 + m01 * wC1) * wR0 + (m10 * wC0 + m11 * wC1) * wR1
        ok = den >= (one - cover_eps)
        safe = np.where(den > 0, den, one)
        out = np.where(ok, num / safe, np.float32(np.nan))
        return out.astype(np.float32)

    return resample_up


# --------------------------------------------------------------------------- #
# Isolines (contours) and wind arrows baked into the WebP
#
# 1.2.4.13: il WebP pre-renderizzato non e' piu' "solo raster a bande": le
# isolinee (con il VALORE scritto sopra) e le frecce del vento sono
# INCAPSULATE nel WebP stesso. Il raster base NON viene mai risamplato: le
# isolinee/frecce sono disegnate su un layer RGBA separato (sovracampionato
# 2x, ridimensionato con LANCZOS) e poi composte per alpha: i pixel dove il
# layer e' completamente trasparente restano byte-identici al raster base.
# --------------------------------------------------------------------------- #
# Intervallo delle isolinee per variabile (unita' native). Tabella ALLINEATA
# alla densita' "fitta" del client (fmContourInterval): solo le variabili per
# le quali l'isolinea da' informazione (gradiente termico, umidita',
# pressione, geopotenziale, vento) ne ricevono. Le altre (nubi,
# precipitazioni, neve, weather_code, cape, freezing level) restano "raster a
# bande": le isolinee aggiungerebbero solo rumore.
CONTOUR_INTERVALS = {
    "temperature_2m": 1.0,
    "dew_point_2m": 1.0,
    # Etichette ogni 4°C come la carta T850 di Meteociel; mantiene una densità
    # leggibile sull'intero emisfero nord.
    "temperature_850hPa": 4.0,
    "temperature_500hPa": 2.0,
    "relative_humidity_2m": 5.0,
    "relative_humidity_850hPa": 5.0,
    "relative_humidity_700hPa": 5.0,
    "pressure_msl": 5.0,
    "surface_pressure": 5.0,
    "geopotential_height_500hPa": 40.0,
    "geopotential_height_850hPa": 40.0,
    "geopotential_height_1000hPa": 40.0,
    "wind_speed_10m": 2.0,
    "wind_speed_850hPa": 2.0,
    "wind_speed_300hPa": 2.0,
    "wind_gusts_10m": 2.0,
}
CONTOUR_VARS = frozenset(CONTOUR_INTERVALS.keys())

# Variabili di VELOCITA' del vento -> variabile di DIREZIONE (gradi, "da cui
# soffia") letta dallo STESSO frame .om. Le direzioni NON hanno color scale
# (quindi non generano un WebP proprio): servono solo alle frecce.
ARROW_VARS = {
    "wind_speed_10m": "wind_direction_10m",
    "wind_speed_850hPa": "wind_direction_850hPa",
    "wind_speed_300hPa": "wind_direction_300hPa",
}

MAX_CONTOUR_LEVELS = 64           # cap sui livelli per variabile
LABEL_MIN_SPACING_PX = 110.0      # spaziatura minima fra le etichette
LABEL_FONT_PX = 12                # altezza del carattere (px di output)
LABEL_MIN_POLYLINE_PX = 60.0      # sotto questa lunghezza il livello non e' etichettato
CONTOUR_LINE_WIDTH = 1.2          # px (come lo strokeStyle del client)
CONTOUR_ALPHA = 0.95              # opacita' delle isolinee (client: globalAlpha 0.95)
CONTOUR_FALLBACK_COLOR = (15, 23, 42, 191)    # rgba(15,23,42,0.75) - inchiostro
INK_COLOR = (15, 23, 42, 242)                 # rgba(15,23,42,0.95) - testo
HALO_COLOR = (255, 255, 255, 217)             # bianco alpha 0.85 - alone
ARROW_COLOR = (15, 23, 42, 191)               # rgba(15,23,42,0.75) - frecce
ARROW_STEP_PX = 26.0              # passo del reticolo delle frecce (px output)
ARROW_LEN_MIN = 7.0               # lunghezza minima della freccia (px)
ARROW_LEN_MAX = 20.0              # lunghezza massima della freccia (px)
ARROW_MIN_SPEED = 0.5             # sotto questa velocita' (m/s) nessuna freccia
ARROW_LINE_WIDTH = 1.4            # px (come lo strokeStyle del client)
ARROW_HEAD_RATIO = 0.45           # lunghezza della punta / lunghezza freccia
ARROW_HEAD_HALF_WIDTH_RATIO = 0.22
OVERLAY_SIZE_LIMIT = 1600         # sopra questo lato l'overlay non e' piu' 2x
OVERLAY_SIZE_LIMIT_LARGE = 3200
OVERLAY_SUPERSAMPLE_SMALL = 2.0
OVERLAY_SUPERSAMPLE_MEDIUM = 1.5
OVERLAY_SUPERSAMPLE_LARGE = 1.0

# Tabella dei 16 casi di marching squares: coppie di LATI da collegare.
# Lati: 0 = ovest (fra z00 e z10), 1 = nord (fra z10 e z11), 2 = est (fra z01 e
# z11), 3 = sud (fra z00 e z01). Bit degli angoli: b00 (SO) = 1, b10 (NO) = 2,
# b11 (NE) = 4, b01 (SE) = 8. None = caso ambiguo (5 e 10: risolto con il
# decider asintotico sul centro bilineare).
_MS_CASE_PAIRS = {
    0: (),
    1: ((0, 3),),
    2: ((0, 1),),
    3: ((1, 3),),
    4: ((1, 2),),
    5: None,
    6: ((0, 2),),
    7: ((2, 3),),
    8: ((2, 3),),
    9: ((0, 2),),
    10: None,
    11: ((1, 2),),
    12: ((1, 3),),
    13: ((0, 1),),
    14: ((0, 3),),
    15: (),
}
# Casi ambigui: chiave = centro DENTRO il livello (i due angoli "inside" sono
# collegati fra loro -> i due angoli "outside" restano separati) oppure centro
# FUORI (gli "outside" sono collegati -> gli "inside" restano separati).
_MS_AMBIGUOUS = {
    5: {True: ((0, 1), (2, 3)), False: ((0, 3), (1, 2))},
    10: {True: ((0, 3), (1, 2)), False: ((0, 1), (2, 3))},
}


def contour_segments(z, level):
    """Segments of the iso-line ``level`` over the field ``z`` (marching squares).

    ``z`` is a 2D array of REAL units with row 0 = SOUTH. The result is an
    (N, 4) float64 array of segments ``(x0, y0, x1, y1)`` in DATA-GRID
    coordinates (x = column index, y = row index). Fully vectorized: the loop
    runs over the 16 marching-squares CASES, never over the cells.

    Cells with a non-finite corner are discarded; a crossing whose
    interpolation is degenerate (0/0, t non finito o fuori [0, 1]) is discarded
    too. The ambiguous cases (5 and 10) are resolved with the asymptotic
    decider: the bilinear centre ``(z00+z01+z10+z11)/4`` is compared with the
    level.
    """
    z = np.asarray(z)
    if z.ndim != 2 or z.shape[0] < 2 or z.shape[1] < 2:
        return np.zeros((0, 4), dtype=np.float64)
    level = float(level)
    z00 = z[:-1, :-1]
    z01 = z[:-1, 1:]
    z11 = z[1:, 1:]
    z10 = z[1:, :-1]
    fin = np.isfinite(z)
    v00 = fin[:-1, :-1]
    v01 = fin[:-1, 1:]
    v11 = fin[1:, 1:]
    v10 = fin[1:, :-1]
    b00 = (z00 >= level) & v00
    b01 = (z01 >= level) & v01
    b11 = (z11 >= level) & v11
    b10 = (z10 >= level) & v10
    case = (b00.astype(np.int16)
            | (b10.astype(np.int16) << 1)
            | (b11.astype(np.int16) << 2)
            | (b01.astype(np.int16) << 3))
    ok = v00 & v01 & v11 & v10
    case = np.where(ok, case, 16).astype(np.int16)

    out = []
    for c in range(16):
        sel = (case == c)
        if not sel.any():
            continue
        rows, cols = np.nonzero(sel)
        a00 = z00[rows, cols].astype(np.float64)
        a01 = z01[rows, cols].astype(np.float64)
        a11 = z11[rows, cols].astype(np.float64)
        a10 = z10[rows, cols].astype(np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            tL = (level - a00) / (a10 - a00)
            tT = (level - a10) / (a11 - a10)
            tR = (level - a01) / (a11 - a01)
            tB = (level - a00) / (a01 - a00)
        okL = np.isfinite(tL) & (tL >= 0.0) & (tL <= 1.0)
        okT = np.isfinite(tT) & (tT >= 0.0) & (tT <= 1.0)
        okR = np.isfinite(tR) & (tR >= 0.0) & (tR <= 1.0)
        okB = np.isfinite(tB) & (tB >= 0.0) & (tB <= 1.0)
        r = rows.astype(np.float64)
        cc = cols.astype(np.float64)
        # Punto di attraversamento di ogni lato (coordinate del grid dati).
        pts = {
            0: (cc, r + tL),          # ovest: fra z00 (SO) e z10 (NO)
            1: (cc + tT, r + 1.0),    # nord:  fra z10 (NO) e z11 (NE)
            2: (cc + 1.0, r + tR),    # est:   fra z01 (SE) e z11 (NE)
            3: (cc + tB, r),          # sud:   fra z00 (SO) e z01 (SE)
        }
        good = {0: okL, 1: okT, 2: okR, 3: okB}
        base_pairs = _MS_CASE_PAIRS[c]
        if base_pairs is None:
            centre = (a00 + a01 + a10 + a11) * 0.25
            inside = centre >= level
            groups = []
            for flag in (True, False):
                m = (inside == flag)
                if m.any():
                    groups.append((m, _MS_AMBIGUOUS[c][flag]))
        else:
            groups = [(None, base_pairs)]
        for mask, pairs in groups:
            for ea, eb in pairs:
                m = good[ea] & good[eb]
                if mask is not None:
                    m = m & mask
                if not m.any():
                    continue
                x0, y0 = pts[ea]
                x1, y1 = pts[eb]
                out.append(np.stack([x0[m], y0[m], x1[m], y1[m]], axis=1))
    if not out:
        return np.zeros((0, 4), dtype=np.float64)
    segs = np.concatenate(out, axis=0)
    # Scarta i segmenti DEGENERI (entrambi gli estremi sullo stesso punto:
    # capita quando il livello passa esattamente per un vertice della cella).
    # Non portano informazione e inquinerebbero il concatenamento.
    length = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])
    segs = segs[length > 1e-12]
    if segs.shape[0] == 0:
        return np.zeros((0, 4), dtype=np.float64)
    return segs


def contour_levels(interval, data_min, data_max):
    """Levels (multiples of ``interval``) inside ``[data_min, data_max]``.

    The levels are the "clean" values (12.0, not 11.999999) because they are
    written as labels on the image. If more than ``MAX_CONTOUR_LEVELS`` levels
    would be needed, the interval is DOUBLED (and the levels recomputed) until
    the count fits: the contour density stays bounded on any field.
    """
    try:
        step = float(interval)
        dmin = float(data_min)
        dmax = float(data_max)
    except (TypeError, ValueError):
        return []
    if not (np.isfinite(step) and step > 0.0):
        return []
    if not (np.isfinite(dmin) and np.isfinite(dmax)) or dmax < dmin:
        return []
    for _ in range(32):
        lo = int(math.ceil(dmin / step - 1e-9))
        hi = int(math.floor(dmax / step + 1e-9))
        if hi < lo:
            return []
        count = hi - lo + 1
        if count <= MAX_CONTOUR_LEVELS:
            return [round((lo + k) * step, 6) for k in range(count)]
        step *= 2.0
        if not np.isfinite(step) or step > 1e12:
            return []
    return []


def _grid_to_pixel(cgrid: dict, scale: int):
    """Map DATA-grid coordinates -> OUTPUT-raster pixels.

    Returns ``(row_of, col_of, H, W)``:
      * ``row_of[j]`` = output row (0 = NORD) of the data row j (0 = SUD);
      * ``col_of[i]`` = output column of the data column i;
      * ``H``/``W`` = output raster size.

    The vertical mapping is the SAME Mercator remap used by
    ``build_row_resampler``: the output raster has ``(ny-1)*scale+1`` rows,
    row 0 = north.
    """
    ny = int(cgrid["ny"])
    nx = int(cgrid["nx"])
    dy = float(cgrid["dy"])
    lat_min = float(cgrid["latMin"])
    scale = max(1, int(scale))
    H = (ny - 1) * scale + 1
    W = (nx - 1) * scale + 1
    my_s = _merc_y(lat_min)
    my_n = _merc_y(lat_min + (ny - 1) * dy)
    span = my_n - my_s
    lats = lat_min + np.arange(ny, dtype=np.float64) * dy
    mys = np.array([_merc_y(float(v)) for v in lats], dtype=np.float64)
    if span > 0:
        row_of = (H - 1) * (my_n - mys) / span
    else:
        row_of = np.arange(ny, dtype=np.float64) * (H - 1) / max(ny - 1, 1)
    col_of = np.arange(nx, dtype=np.float64) * float(scale)
    return row_of, col_of, H, W


def _data_to_pixel(row_of, col_of, scale, x, y):
    """``(x, y)`` in DATA-grid coords -> ``(px, py)`` in OUTPUT-raster pixels."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    px = x * float(scale)
    py = np.interp(y, np.arange(row_of.shape[0], dtype=np.float64), row_of)
    return px, py


def _level_color(variant, level):
    """RGBA of the scale band containing ``level``.

    Same logic as the client (``fmContourColor`` -> ``searchsorted`` on the
    breakpoints). Only the RGB of the band is used and the alpha is forced to
    0.95: many scales have ``colors[0]`` fully transparent, and the alpha of
    the scale would make the isoline invisible.
    """
    if not variant:
        return CONTOUR_FALLBACK_COLOR
    try:
        bps = np.asarray(variant["breakpoints"], dtype=np.float64)
        cols = np.asarray(variant["colors"], dtype=np.float64)
    except Exception:
        return CONTOUR_FALLBACK_COLOR
    if (bps.ndim != 1 or cols.ndim != 2 or bps.size == 0
            or cols.shape[0] != bps.shape[0] or cols.shape[1] < 3):
        return CONTOUR_FALLBACK_COLOR
    idx = int(np.searchsorted(bps, float(level), side="right") - 1)
    idx = max(0, min(int(bps.size) - 1, idx))
    rgb = np.clip(cols[idx, 0:3], 0.0, 255.0).astype(np.uint8)
    return (int(rgb[0]), int(rgb[1]), int(rgb[2]),
            int(round(CONTOUR_ALPHA * 255.0)))


def _font(px):
    """Pillow font of the requested pixel size (Pillow >= 10.1 supports ``size=``)."""
    try:
        return ImageFont.load_default(size=px)
    except (TypeError, AttributeError, ValueError):
        return ImageFont.load_default()


def _draw_label(draw, x, y, text, font, halo_r=1):
    """Text with a white halo and dark ink on top (orizzontale: no rotation)."""
    for dx in (-halo_r, 0, halo_r):
        for dy in (-halo_r, 0, halo_r):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=HALO_COLOR)
    draw.text((x, y), text, font=font, fill=INK_COLOR)


def _format_level(level, interval):
    """Label text of a level: integer if the interval is integer, else 1 decimal."""
    if float(interval).is_integer():
        return "%d" % int(round(level))
    return "%.1f" % float(level)


def _chain_segments(segs):
    """Chain marching-squares segments into polylines.

    ``segs`` is an (N, 4) sequence of ``(x0, y0, x1, y1)``. Two segments
    that share an edge compute the SAME crossing (same corner values, same
    formula), so the chaining is exact. Returns a list of polylines (lists of
    ``(x, y)``).
    """
    if segs is None or len(segs) == 0:
        return []

    def key(x, y):
        return (round(float(x), 9), round(float(y), 9))

    items = [(float(s[0]), float(s[1]), float(s[2]), float(s[3])) for s in segs]
    adj = {}
    for idx, (x0, y0, x1, y1) in enumerate(items):
        adj.setdefault(key(x0, y0), []).append((idx, True))
        adj.setdefault(key(x1, y1), []).append((idx, False))
    used = [False] * len(items)
    lines = []
    for i in range(len(items)):
        if used[i]:
            continue
        used[i] = True
        x0, y0, x1, y1 = items[i]
        pts = [(x0, y0), (x1, y1)]
        while True:
            nxt = None
            for idx, from_start in adj.get(key(pts[-1][0], pts[-1][1]), []):
                if not used[idx]:
                    nxt = (idx, from_start)
                    break
            if nxt is None:
                break
            idx, from_start = nxt
            used[idx] = True
            a, b, c, d = items[idx]
            pts.append((c, d) if from_start else (a, b))
        lines.append(pts)
    return lines


def _polyline_length(line):
    """Lenght of a polyline (sum of the segment lengths)."""
    total = 0.0
    for i in range(len(line) - 1):
        x0, y0 = line[i]
        x1, y1 = line[i + 1]
        total += math.hypot(x1 - x0, y1 - y0)
    return total


def _label_candidates(lines, spacing):
    """Anchor positions (px) along the polylines, one every ``spacing`` px."""
    out = []
    for line in lines:
        acc = 0.0
        nxt = spacing * 0.5
        for i in range(len(line) - 1):
            x0, y0 = line[i]
            x1, y1 = line[i + 1]
            seg = math.hypot(x1 - x0, y1 - y0)
            if seg <= 0.0:
                continue
            while nxt <= acc + seg:
                f = (nxt - acc) / seg
                out.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
                nxt += spacing
            acc += seg
    return out


def _min_distance(placed, c):
    """Distance from ``c`` to the closest already placed label (inf if none)."""
    if not placed:
        return float("inf")
    best = float("inf")
    for p in placed:
        d = math.hypot(p[0] - c[0], p[1] - c[1])
        if d < best:
            best = d
    return best


def _draw_contours(draw, sub, cgrid, scale, variant, interval, ss=1.0, placed=None,
                   force_labels=False):
    """Draw the isolines of ``sub`` (and their VALUE labels) on ``draw``.

    ``draw`` is an ImageDraw on the SUPERSAMPLED overlay: every coordinate is
    multiplied by ``ss``. Returns ``{"levels": [...], "labels": [...]}`` with the
    levels actually drawn and the ``(level, x, y)`` anchors of the labels (in
    OUTPUT pixel coordinates).
    """
    empty = {"levels": [], "labels": []}
    if sub is None:
        return empty
    z = np.asarray(sub)
    if z.ndim != 2 or z.size == 0:
        return empty
    finite = np.isfinite(z)
    if not finite.any():
        return empty
    dmin = float(z[finite].min())
    dmax = float(z[finite].max())
    levels = contour_levels(interval, dmin, dmax)
    if not levels:
        return empty
    row_of, col_of, H, W = _grid_to_pixel(cgrid, scale)
    if placed is None:
        placed = []
    font = _font(max(1, int(round(LABEL_FONT_PX * ss))))
    width = max(1, int(round(CONTOUR_LINE_WIDTH * ss)))
    drawn_levels = []
    drawn_labels = []
    for level in levels:
        segs = contour_segments(z, level)
        if len(segs) < 2:
            continue
        px0, py0 = _data_to_pixel(row_of, col_of, scale, segs[:, 0], segs[:, 1])
        px1, py1 = _data_to_pixel(row_of, col_of, scale, segs[:, 2], segs[:, 3])
        seg_px = np.stack([px0, py0, px1, py1], axis=1)
        lines = _chain_segments(seg_px)
        if not lines:
            continue
        color = _level_color(variant, level)
        for (x0, y0, x1, y1) in seg_px:
            draw.line([(x0 * ss, y0 * ss), (x1 * ss, y1 * ss)],
                      fill=color, width=width)
        drawn_levels.append(level)
        total = 0.0
        for line in lines:
            total += _polyline_length(line)
        if total < LABEL_MIN_POLYLINE_PX and not force_labels:
            continue
        text = _format_level(level, interval)
        candidates = _label_candidates(lines, LABEL_MIN_SPACING_PX)
        if force_labels and not candidates and lines:
            # Pressione e geopotenziale devono mantenere il valore anche sui
            # nuclei chiusi (minimi/massimi), spesso piu' corti della spaziatura
            # standard. Ancora l'etichetta al centro della linea piu' lunga.
            line = max(lines, key=_polyline_length)
            half = _polyline_length(line) * 0.5
            walked = 0.0
            for i in range(len(line) - 1):
                x0, y0 = line[i]
                x1, y1 = line[i + 1]
                seg = math.hypot(x1 - x0, y1 - y0)
                if seg > 0 and walked + seg >= half:
                    f = (half - walked) / seg
                    candidates = [(x0 + (x1 - x0) * f, y0 + (y1 - y0) * f)]
                    break
                walked += seg
        chosen = [c for c in candidates
                  if _min_distance(placed, c) >= LABEL_MIN_SPACING_PX]
        if not chosen and candidates:
            # Ogni livello DEVE avere il valore scritto sull'immagine: se la
            # spaziatura lo impedisce, si tiene il candidato piu' lontano.
            chosen = [max(candidates, key=lambda c: _min_distance(placed, c))]
        for c in chosen:
            placed.append(c)
            drawn_labels.append((level, c[0], c[1]))
            _draw_label(draw, c[0] * ss, c[1] * ss, text, font)
    return {"levels": drawn_levels, "labels": drawn_labels}


def _wind_flow_vector(direction_deg):
    """Unit vector of the FLOW (where the wind blows TO) in pixel axes.

    ``direction_deg`` is the meteorological direction "FROM which the wind
    blows" (0 = nord, 90 = est, 180 = sud, 270 = ovest): the convention of the
    engine (``wind_direction_from_uv``). The flow is the opposite direction.
    In pixel axes +x = EST and +y = SUD (the raster rows grow downwards), so::

        dir = 0   (da nord) -> la freccia punta a SUD   (dy > 0)
        dir = 180 (da sud)  -> la freccia punta a NORD   (dy < 0)
        dir = 90  (da est)  -> la freccia punta a OVEST  (dx < 0)
    """
    b = (np.asarray(direction_deg, dtype=np.float64) + 180.0) % 360.0
    rad = np.radians(b)
    return np.sin(rad), -np.cos(rad)


def _unwrap_direction(deg):
    """Unwrap the 0/360 discontinuity of the wind direction.

    Without this the bilinear resampler would average 350 deg with 10 deg and
    produce 180 deg (a wrong arrow) along the discontinuity. The unwrapped
    field is the SAME field on a continuous branch: sin/cos of the flow
    direction are unchanged.
    """
    rad = np.radians(np.asarray(deg, dtype=np.float64))
    rad = np.unwrap(rad, axis=1)
    rad = np.unwrap(rad.T, axis=1).T
    return np.degrees(rad)


def _draw_wind_arrows(draw, speed_px, dir_px, cgrid, scale, speed_ref, ss=1.0):
    """Draw the wind arrows on ``draw`` (supersampled overlay).

    ``speed_px``/``dir_px`` are the resampled fields in OUTPUT pixel space
    (same shape as the rendered raster); ``dir_px`` is the meteorological
    direction "from". The arrows are sampled on a coarse lattice (every
    ``ARROW_STEP_PX`` output pixels, at least 3 data cells). Returns the number
    of arrows drawn.
    """
    if speed_px is None or dir_px is None:
        return 0
    speed = np.asarray(speed_px, dtype=np.float64)
    dirs = np.asarray(dir_px, dtype=np.float64)
    if speed.ndim != 2 or speed.shape != dirs.shape:
        return 0
    row_of, col_of, H, W = _grid_to_pixel(cgrid, scale)
    ny = int(cgrid["ny"])
    nx = int(cgrid["nx"])
    step = max(ARROW_STEP_PX / float(scale), 3.0)
    idx_i = np.unique(np.arange(0, nx, step).astype(np.int64))
    idx_j = np.unique(np.arange(0, ny, step).astype(np.int64))
    col_px = np.rint(idx_i * float(scale)).astype(np.int64)
    row_px = np.rint(row_of[idx_j]).astype(np.int64)
    np.clip(col_px, 0, W - 1, out=col_px)
    np.clip(row_px, 0, H - 1, out=row_px)
    sp = speed[np.ix_(row_px, col_px)]
    dd = dirs[np.ix_(row_px, col_px)]
    ok = np.isfinite(sp) & np.isfinite(dd) & (sp >= ARROW_MIN_SPEED)
    if not ok.any():
        return 0
    ref = float(speed_ref) if speed_ref else 40.0
    if not (ref > 0.0):
        ref = 40.0
    ux, uy = _wind_flow_vector(dd)
    ratio = np.clip(sp / ref, 0.0, 1.0)
    length = ARROW_LEN_MIN + (ARROW_LEN_MAX - ARROW_LEN_MIN) * ratio
    head_len = ARROW_HEAD_RATIO * length
    half_w = ARROW_HEAD_HALF_WIDTH_RATIO * length
    width = max(1, int(round(ARROW_LINE_WIDTH * ss)))
    count = 0
    r_idx, c_idx = np.nonzero(ok)
    for r, c in zip(r_idx, c_idx):
        L = float(length[r, c])
        if L <= 0.0:
            continue
        dx = float(ux[r, c])
        dy = float(uy[r, c])
        cx = float(col_px[c])
        cy = float(row_px[r])
        tipx = cx + dx * (L * 0.5)
        tipy = cy + dy * (L * 0.5)
        tailx = cx - dx * (L * 0.5)
        taily = cy - dy * (L * 0.5)
        basex = tipx - dx * float(head_len[r, c])
        basey = tipy - dy * float(head_len[r, c])
        ppx = -dy
        ppy = dx
        c1x = basex + ppx * float(half_w[r, c])
        c1y = basey + ppy * float(half_w[r, c])
        c2x = basex - ppx * float(half_w[r, c])
        c2y = basey - ppy * float(half_w[r, c])
        draw.line([(tailx * ss, taily * ss), (basex * ss, basey * ss)],
                  fill=ARROW_COLOR, width=width)
        draw.polygon([(tipx * ss, tipy * ss),
                      (c1x * ss, c1y * ss),
                      (c2x * ss, c2y * ss)],
                     fill=ARROW_COLOR)
        count += 1
    return count


def _overlay_scale(h, w):
    """Supersampling factor of the overlay layer (anti-aliasing)."""
    m = max(int(h), int(w))
    if m <= OVERLAY_SIZE_LIMIT:
        return OVERLAY_SUPERSAMPLE_SMALL
    if m <= OVERLAY_SIZE_LIMIT_LARGE:
        return OVERLAY_SUPERSAMPLE_MEDIUM
    return OVERLAY_SUPERSAMPLE_LARGE


def _make_overlay(h, w, ss, draw_fn):
    """Build the (already resized) RGBA overlay layer for a base of (h, w)."""
    ow = max(1, int(math.ceil(w * ss)))
    oh = max(1, int(math.ceil(h * ss)))
    layer = Image.new("RGBA", (ow, oh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw_fn(draw, ss)
    if (ow, oh) != (w, h):
        layer = layer.resize((w, h), Image.LANCZOS)
    return layer


def _apply_overlay(base_rgba, draw_fn):
    """Composite the overlay on the base raster (the base is NEVER resampled).

    The overlay is drawn on a separate RGBA layer (supersampled), resized with
    LANCZOS to the base size and alpha-composited: the pixels where the layer
    is completely transparent stay byte-identical to the base raster.
    """
    arr = np.asarray(base_rgba)
    h = int(arr.shape[0])
    w = int(arr.shape[1])
    ss = _overlay_scale(h, w)
    layer = _make_overlay(h, w, ss, draw_fn)
    base = Image.fromarray(arr, "RGBA")
    return np.asarray(Image.alpha_composite(base, layer))


def _overlay_drawer(sub, cgrid, scale, variant, interval, placed,
                    force_labels=False,
                    speed_out=None, dir_out=None, speed_ref=0.0):
    """Build the draw callback of the overlay (isolines + wind arrows).

    ``sub`` is the data-grid field (for the isolines), ``speed_out``/
    ``dir_out`` are the OUTPUT-raster fields (for the arrows). The callback
    receives the ImageDraw of the supersampled layer and the supersampling
    factor.
    """
    def _draw(draw, ss):
        if interval is not None:
            _draw_contours(draw, sub, cgrid, scale, variant, interval,
                           ss=ss, placed=placed, force_labels=force_labels)
        if speed_out is not None and dir_out is not None:
            _draw_wind_arrows(draw, speed_out, dir_out, cgrid, scale, speed_ref,
                              ss=ss)
    return _draw


def _speed_reference(variant, default=40.0):
    """Reference speed (m/s) of a wind speed scale: its last breakpoint."""
    if not variant:
        return default
    try:
        bps = variant.get("breakpoints") or []
    except AttributeError:
        return default
    if not len(bps):
        return default
    try:
        return float(bps[-1])
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Region cropping
# --------------------------------------------------------------------------- #
def region_crop(bbox: dict, grid: dict, region):
    """Intersect a domain coverage bbox with a region bbox on the domain grid.

    ``bbox`` is the engine manifest object ``{lat_min,lon_min,lat_max,lon_max}``;
    ``grid`` is the engine ``out_grid`` ``{nx,ny,lonMin,latMin,dx,dy}`` (already
    the domain's crop, matching the frame array shape). ``region`` is a
    ``(lat_min, lon_min, lat_max, lon_max)`` tuple.

    Returns ``(row0, row1, col0, col1, cropped_grid)`` (half-open slices on the
    raw ``(ny, nx)`` array, row 0 = south) or ``None`` when the two boxes do not
    intersect. The cropped grid keeps the SAME ``dx``/``dy`` and snaps to grid
    columns/rows so the result is an exact sub-grid of the domain frame.
    """
    lat_min = max(float(bbox["lat_min"]), float(region[0]))
    lon_min = max(float(bbox["lon_min"]), float(region[1]))
    lat_max = min(float(bbox["lat_max"]), float(region[2]))
    lon_max = min(float(bbox["lon_max"]), float(region[3]))
    if lat_max <= lat_min or lon_max <= lon_min:
        return None

    ny = int(grid["ny"])
    nx = int(grid["nx"])
    dy = float(grid["dy"])
    dx = float(grid["dx"])
    g_lat0 = float(grid["latMin"])
    g_lon0 = float(grid["lonMin"])
    if dx <= 0 or dy <= 0 or nx < 1 or ny < 1:
        return None

    eps = 1e-9
    row0 = int(math.ceil((lat_min - g_lat0) / dy - eps))
    row1 = int(math.floor((lat_max - g_lat0) / dy + eps)) + 1
    col0 = int(math.ceil((lon_min - g_lon0) / dx - eps))
    col1 = int(math.floor((lon_max - g_lon0) / dx + eps)) + 1

    row0 = max(0, min(row0, ny))
    row1 = max(0, min(row1, ny))
    col0 = max(0, min(col0, nx))
    col1 = max(0, min(col1, nx))
    if row1 <= row0 or col1 <= col0:
        return None

    cropped = {
        "nx": col1 - col0,
        "ny": row1 - row0,
        "latMin": round(g_lat0 + row0 * dy, 10),
        "lonMin": round(g_lon0 + col0 * dx, 10),
        "dx": dx,
        "dy": dy,
    }
    return row0, row1, col0, col1, cropped


def resolve_region_bbox(region_value, domain_bbox):
    """Resolve a REGIONS entry to a bbox tuple.

    ``None`` is the 'domain' sentinel: it maps to the engine manifest bbox (the
    whole domain grid). Any other value is already a ``(lat_min, lon_min,
    lat_max, lon_max)`` tuple and is returned unchanged.
    """
    if region_value is None:
        return (float(domain_bbox["lat_min"]), float(domain_bbox["lon_min"]),
                float(domain_bbox["lat_max"]), float(domain_bbox["lon_max"]))
    return region_value


def _is_global_bbox(bbox: dict) -> bool:
    """True when the domain spans (near) the whole globe.

    Thresholds ALIGNED to the client predicate ``fmIsGlobalDomain``
    (``mri-light-1.2.4.11.html``): ``lon_span > 300 || lat_span > 120``.
    """
    return ((float(bbox["lon_max"]) - float(bbox["lon_min"])) > 300.0
            or (float(bbox["lat_max"]) - float(bbox["lat_min"])) > 120.0)


def cropped_bbox(cropped_grid: dict) -> dict:
    """Grid-aligned bbox object for a cropped grid (pixel-centre extremes)."""
    ny = int(cropped_grid["ny"])
    nx = int(cropped_grid["nx"])
    dy = float(cropped_grid["dy"])
    dx = float(cropped_grid["dx"])
    lat_min = float(cropped_grid["latMin"])
    lon_min = float(cropped_grid["lonMin"])
    return {
        "lat_min": round(lat_min, 10),
        "lon_min": round(lon_min, 10),
        "lat_max": round(lat_min + (ny - 1) * dy, 10),
        "lon_max": round(lon_min + (nx - 1) * dx, 10),
    }


def image_bounds(bbox: dict) -> list:
    """Leaflet ``imageOverlay`` bounds ``[[south, west], [north, east]]``."""
    return [
        [float(bbox["lat_min"]), float(bbox["lon_min"])],
        [float(bbox["lat_max"]), float(bbox["lon_max"])],
    ]


# --------------------------------------------------------------------------- #
# Manifest / frame helpers
# --------------------------------------------------------------------------- #
def valid_time_to_filename(valid_time: str) -> str:
    """``2026-10-14T16:00Z`` -> ``2026-10-14T1600`` (matche engine naming)."""
    return str(valid_time).replace(":", "").replace("Z", "")


def read_manifest(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def frame_array_names(reader) -> list:
    names = []
    for i in range(int(reader.num_children)):
        child = reader.get_child_by_index(i)
        if not child.is_scalar:
            names.append(child.name)
    return names


def read_frame_array(reader, name: str) -> np.ndarray:
    child = reader.get_child_by_name(name)
    if child is None:
        raise KeyError(name)
    arr = child.read_array((slice(None), slice(None)))
    return np.ascontiguousarray(np.asarray(arr, dtype=np.float32))


def write_json(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def save_webp(rgba: np.ndarray, path: str, quality: int, lossless: bool,
              method: int = 6) -> int:
    img = Image.fromarray(rgba, mode="RGBA")
    # method=6 (default) keeps the historical output byte-compatible; CI passes 4.
    kwargs = {"format": "WEBP", "method": int(method)}
    if lossless:
        kwargs["lossless"] = True
    else:
        kwargs["quality"] = int(quality)
    img.save(path, **kwargs)
    return os.path.getsize(path)


def prune_dir(dir_path: str, keep_files: set) -> int:
    """Delete stale ``*.webp`` in ``dir_path`` that are not in ``keep_files``.

    Returns the number of removed files. Mirrors the engine prune concept so the
    published tree does not grow without bound.
    """
    removed = 0
    try:
        entries = os.listdir(dir_path)
    except OSError:
        return 0
    for name in entries:
        if not name.lower().endswith(".webp"):
            continue
        if name in keep_files:
            continue
        try:
            os.remove(os.path.join(dir_path, name))
            removed += 1
        except OSError:
            pass
    return removed


# --------------------------------------------------------------------------- #
# Core render loop
# --------------------------------------------------------------------------- #
def _utcnow_iso() -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _run_id(reference_time: str | None) -> str:
    """Stable, path-safe identifier ordered chronologically for a model run."""
    cleaned = re.sub(r"[^0-9A-Za-z]", "", str(reference_time or ""))
    return cleaned or _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _theme_var_dir(out_root: str, region: str, domain: str, var: str, theme: str,
                   run_id: str | None = None) -> str:
    """Output directory for one region/domain/run/var/theme.

    1.2.4.13: the dark theme has been REMOVED (light only), so the output
    directory is ALWAYS the plain ``<region>/<domain>/<var>`` path: there is no
    ``dark/`` subdirectory any more.
    """
    if run_id:
        return os.path.join(out_root, region, domain, "runs", run_id, var)
    return os.path.join(out_root, region, domain, var)


def _prune_legacy_theme_dirs(out_root: str, region: str, domain: str, var: str) -> None:
    """Remove the legacy ``<var>/dark/`` subtree published by 1.2.4.12.

    ``prune_dir`` only deletes the stale ``*.webp`` files, so without this the
    dark subtree would stay in the published tree forever.
    """
    vdir = os.path.join(out_root, region, domain, var)
    shutil.rmtree(os.path.join(vdir, "dark"), ignore_errors=True)


def render_domain(domain_dir: str, out_root: str, scales: dict, args, regions: dict) -> tuple:
    """Render one domain into every intersecting region, for every requested theme.

    Returns ``(ok_frames, skipped, err, contrib, frames_done, bytes_written)``
    where ``contrib`` is a dict
    ``{region: {"bbox":..., "grid":..., "bounds":..., "variables": {...}}}``
    used by the caller to assemble each region ``index.json``, ``frames_done``
    is the number of ``.om`` frames actually opened/processed and
    ``bytes_written`` the total size of the WebP files produced (observability).
    """
    manifest_path = (args.watch_manifest or
                     os.path.join(domain_dir, "latest.json"))
    if args.watch_manifest:
        while True:
            try:
                manifest = read_manifest(manifest_path)
                break
            except (OSError, ValueError):
                time.sleep(0.25)
    else:
        if not os.path.isfile(manifest_path):
            return 0, 0, "no latest.json", {}, 0, 0
        manifest = read_manifest(manifest_path)
    if manifest.get("noop"):
        domain = os.path.basename(os.path.normpath(domain_dir))
        print("  [noop] %s: manifest invariato, nessun WebP da rigenerare" % domain)
        return 0, 0, "noop", {}, 0, 0
    grid = manifest.get("grid")
    bbox = manifest.get("bbox")
    frames = manifest.get("frames") or []
    reference_time = manifest.get("reference_time")
    run_id = _run_id(reference_time)
    expected_frames = len(manifest.get("valid_times") or frames)
    if (not isinstance(grid, dict) or not isinstance(bbox, dict)
            or (not frames and not args.watch_manifest)):
        return 0, 0, "manifest missing grid/bbox/frames", {}, 0, 0

    if args.limit_frames and args.limit_frames > 0:
        frames = frames[:args.limit_frames]

    domain = os.path.basename(os.path.normpath(domain_dir))
    if args.domains and domain not in args.domains:
        return 0, 0, None, {}, 0, 0

    # Pre-compute the per-region crop + Mercator resampler for this domain.
    crops = {}
    for rname in regions:
        if (rname == DOMAIN_REGION and DOMAIN_REGION_SKIP_GLOBAL
                and _is_global_bbox(bbox)):
            continue
        if (rname == NORTH_REGION and NORTH_REGION_SKIP_NON_GLOBAL
                and not _is_global_bbox(bbox)):
            continue
        rbbox = resolve_region_bbox(regions[rname], bbox)
        cr = region_crop(bbox, grid, rbbox)
        if cr is None:
            continue
        row0, row1, col0, col1, cgrid = cr
        scale = resolve_upsample(args, cgrid["nx"], cgrid["ny"])
        crops[rname] = {
            "row0": row0, "row1": row1, "col0": col0, "col1": col1,
            "grid": cgrid,
            "resample": build_row_resampler(cgrid, scale),
            "scale": scale,
        }
    if not crops:
        return 0, 0, "no region intersection", {}, 0, 0
    print("  [upsample] %s"
          % ", ".join("%s x%d" % (r, crops[r]["scale"]) for r in crops))

    # Decide variable set: CLI filter, else manifest var_order print-safe.
    var_order = [v for v in (manifest.get("var_order") or []) if isinstance(v, str)]
    requested = args.vars if args.vars else var_order

    rendered = 0
    skipped = 0
    frames_done = 0
    bytes_written = 0
    themes = ["light"]
    # rname -> var -> theme -> [frame records]
    var_frames = {rname: {} for rname in crops}
    var_meta = {}                                # var -> unit

    seen_frames = set()
    watch_complete = not bool(args.watch_manifest)
    while True:
      if args.watch_manifest:
        try:
            manifest = read_manifest(manifest_path)
        except (OSError, ValueError):
            time.sleep(0.25)
            continue
        if manifest.get("noop"):
            print("  [noop] %s: manifest invariato, nessun WebP da rigenerare" % domain)
            return 0, 0, "noop", {}, 0, 0
        available = manifest.get("frames") or []
        pending = []
        for candidate in available:
            key = (candidate.get("file"), candidate.get("valid_time"))
            if key not in seen_frames:
                seen_frames.add(key)
                pending.append(candidate)
        watch_complete = bool(manifest.get("complete"))
      else:
        pending = frames
        frames = []

      for frame in pending:
        fname = frame.get("file")
        valid_time = frame.get("valid_time")
        if not fname or not valid_time:
            skipped += 1
            continue
        frame_path = os.path.join(domain_dir, fname)
        if not os.path.isfile(frame_path):
            print("  ! missing frame %s" % frame_path, file=sys.stderr)
            skipped += 1
            continue

        try:
            reader = OmFileReader(frame_path)
        except Exception as exc:
            print("  ! cannot open %s: %s" % (frame_path, exc), file=sys.stderr)
            skipped += 1
            continue

        try:
            present = frame_array_names(reader)
            names = [v for v in requested if v in present] if requested else present
            if args.vars:
                missing = [v for v in requested if v not in present]
                if missing:
                    print("  ~ %s: vars absent in frame: %s"
                          % (valid_time, ",".join(missing)), file=sys.stderr)

            base_name = valid_time_to_filename(valid_time)
            # Cache per FRAME degli array di direzione del vento: letti una
            # volta sola (non una volta per regione/variabile).
            dir_cache = {}
            for var in names:
                lk = make_lookup(scales, var, themes[0])
                if lk is None:
                    print("  ~ no color scale for %r (skip)" % var, file=sys.stderr)
                    continue
                try:
                    arr = read_frame_array(reader, var)
                except Exception as exc:
                    print("  ! %s/%s read failed: %s" % (valid_time, var, exc),
                          file=sys.stderr)
                    continue

                variant = _scale_variant(scales, var, themes[0])
                if var not in var_meta:
                    var_meta[var] = variant.get("unit") if variant else None

                # Frecce vento: l'array di DIREZIONE (gradi, "da cui soffia")
                # non ha color scale (quindi non genera un WebP proprio) e va
                # letto esplicitamente dallo stesso frame .om.
                dir_arr = None
                dir_name = ARROW_VARS.get(var)
                if dir_name:
                    if dir_name in dir_cache:
                        dir_arr = dir_cache[dir_name]
                    else:
                        try:
                            raw_dir = read_frame_array(reader, dir_name)
                            dir_arr = _unwrap_direction(raw_dir)
                            dir_cache[dir_name] = dir_arr
                        except Exception as exc:
                            print("  ! %s/%s read failed: %s"
                                  % (valid_time, dir_name, exc), file=sys.stderr)
                            dir_arr = None

                interval = CONTOUR_INTERVALS.get(var)
                speed_ref = _speed_reference(variant) if dir_name else 0.0

                for rname, crop in crops.items():
                    sub = arr[crop["row0"]:crop["row1"], crop["col0"]:crop["col1"]]
                    arr_out = crop["resample"](sub)
                    rgba = lk(arr_out)

                    # Isolinee (con i valori) e frecce vento: disegnate su un
                    # layer RGBA separato e composte per alpha. Il raster base
                    # NON viene risamplato.
                    if interval is not None or dir_arr is not None:
                        dir_out = None
                        if dir_arr is not None:
                            dsub = dir_arr[crop["row0"]:crop["row1"],
                                           crop["col0"]:crop["col1"]]
                            dir_out = crop["resample"](dsub)
                        rgba = _apply_overlay(rgba, _overlay_drawer(
                            sub, crop["grid"], crop["scale"], variant, interval,
                            [], force_labels=(var in {"pressure_msl", "surface_pressure",
                                                      "geopotential_height_500hPa",
                                                      "geopotential_height_850hPa",
                                                      "geopotential_height_1000hPa"}),
                            speed_out=arr_out if dir_out is not None else None,
                            dir_out=dir_out, speed_ref=speed_ref))

                    vdir = _theme_var_dir(out_root, rname, domain, var, themes[0], run_id)
                    os.makedirs(vdir, exist_ok=True)
                    _prune_legacy_theme_dirs(out_root, rname, domain, var)
                    wpath = os.path.join(vdir, base_name + ".webp")
                    size = save_webp(rgba, wpath, args.quality, args.lossless,
                                     args.method)
                    rendered += 1
                    bytes_written += size

                    var_frames[rname].setdefault(var, {}).setdefault(
                        themes[0], []).append({
                            "file": base_name + ".webp",
                            "valid_time": valid_time,
                            "bytes": size,
                        })
        finally:
            try:
                reader.close()
            except Exception:
                pass
        frames_done += 1

      if watch_complete:
          break
      time.sleep(0.25)

    if not any(var_frames[r] for r in var_frames):
        return rendered, skipped, "nothing rendered", {}, frames_done, bytes_written

    generated_at = _utcnow_iso()

    contrib = {}
    for rname, frames_by_var in var_frames.items():
        if not frames_by_var:
            continue
        cgrid = crops[rname]["grid"]
        cbbox = cropped_bbox(cgrid)
        grid_summary = {"nx": int(cgrid["nx"]), "ny": int(cgrid["ny"])}
        bounds = image_bounds(cbbox)
        variables = {}
        for var, recs_by_theme in frames_by_var.items():
            unit = var_meta.get(var)
            entry = {
                "unit": unit,
                "dir": "%s/runs/%s/%s" % (domain, run_id, var),
                "themes": {},
            }
            for theme, recs in recs_by_theme.items():
                recs.sort(key=lambda r: r["valid_time"])
                keep = {r["file"] for r in recs}
                vdir = _theme_var_dir(out_root, rname, domain, var, theme, run_id)
                prune_dir(vdir, keep)
                shutil.rmtree(os.path.join(vdir, "dark"), ignore_errors=True)
                vmanifest = {
                    "version": WEBP_VERSION,
                    "generated_at": generated_at,
                    "reference_time": reference_time,
                    "run_id": run_id,
                    "expected_frames": expected_frames,
                    "region": rname,
                    "domain": domain,
                    "variable": var,
                    "theme": theme,
                    "unit": unit,
                    "bbox": cbbox,
                    "bounds": bounds,
                    "grid": grid_summary,
                    "frames": recs,
                    "valid_times": [r["valid_time"] for r in recs],
                }
                write_json(os.path.join(vdir, "latest.json"), vmanifest)
                sub = "%s/runs/%s/%s" % (domain, run_id, var)
                entry["themes"][theme] = {
                    "count": len(recs),
                    "valid_times": [r["valid_time"] for r in recs],
                    "dir": sub,
                    "manifest": "%s/latest.json" % sub,
                }
            variables[var] = entry
        contrib[rname] = {
            "bbox": cbbox,
            "bounds": bounds,
            "grid": grid_summary,
            "reference_time": reference_time,
            "current_run": run_id,
            "runs": [{
                "id": run_id,
                "reference_time": reference_time,
                "generated_at": generated_at,
                "expected_frames": expected_frames,
                "variables": variables,
            }],
            "variables": variables,
        }

    return rendered, skipped, None, contrib, frames_done, bytes_written


def write_region_index(out_root: str, region_name: str, region_bbox, domains: dict,
                       themes) -> None:
    """Write ``<out>/<region>/index.json`` summarising the published domains."""
    if region_bbox is None:
        # 'domain' sentinel: there is no single fixed region bbox -> use the
        # union of the indexed domains' own bboxes (fallback: the whole globe).
        lat_min = lon_min = lat_max = lon_max = None
        for d in domains.values():
            bb = (d or {}).get("bbox") or {}
            if bb.get("lat_min") is None:
                continue
            lat_min = bb["lat_min"] if lat_min is None else min(lat_min, bb["lat_min"])
            lon_min = bb["lon_min"] if lon_min is None else min(lon_min, bb["lon_min"])
            lat_max = bb["lat_max"] if lat_max is None else max(lat_max, bb["lat_max"])
            lon_max = bb["lon_max"] if lon_max is None else max(lon_max, bb["lon_max"])
        if lat_min is None:
            lat_min, lon_min, lat_max, lon_max = -90.0, -180.0, 90.0, 180.0
        region_bbox = (lat_min, lon_min, lat_max, lon_max)
    theme_list = list(themes)
    index = {
        "version": WEBP_VERSION,
        "generated_at": _utcnow_iso(),
        "region": region_name,
        "theme": theme_list[0] if theme_list else "light",
        "themes": theme_list,
        "bbox": {
            "lat_min": float(region_bbox[0]),
            "lon_min": float(region_bbox[1]),
            "lat_max": float(region_bbox[2]),
            "lon_max": float(region_bbox[3]),
        },
        "domains": domains,
    }
    rdir = os.path.join(out_root, region_name)
    os.makedirs(rdir, exist_ok=True)
    write_json(os.path.join(rdir, "index.json"), index)


def copy_color_scales(out_dir: str) -> None:
    """Publish the exact color scales the renderer used (single source of truth).

    Shared by the render path and the ``--index-only`` path so both publish the
    identical file with the identical code.
    """
    if os.path.isfile(COLOR_SCALES_PATH):
        try:
            shutil.copyfile(COLOR_SCALES_PATH, os.path.join(out_dir, "color_scales.json"))
        except OSError as exc:
            print("  ! could not copy color_scales.json: %s" % exc, file=sys.stderr)


# --------------------------------------------------------------------------- #
# --index-only: rebuild region indexes from the existing per-var manifests
# --------------------------------------------------------------------------- #
def _valid_var_manifest(obj) -> bool:
    """Structural validation of a per-var WebP ``latest.json`` (index-only)."""
    if not isinstance(obj, dict):
        return False
    valid_times = obj.get("valid_times")
    frames = obj.get("frames")
    if not isinstance(valid_times, list) or not valid_times:
        return False
    if not isinstance(frames, list) or not frames:
        return False
    if not all(isinstance(f, dict) and f.get("file") for f in frames):
        return False
    if obj.get("bounds") is None or obj.get("grid") is None:
        return False
    return True


def _collect_index_run(run_root: str, out_dir: str, region: str, domain: str,
                       run_id: str, themes, legacy: bool = False):
    """Read manifests for one archived/current run and shape its index entry."""
    variables = {}
    ref_time = generated_at = None
    expected = 0
    bbox = bounds = grid = None
    for var in sorted(os.listdir(run_root)):
        var_dir = os.path.join(run_root, var)
        if not os.path.isdir(var_dir):
            continue
        theme_manifests = {}
        invalid = False
        for theme in themes:
            tdir = var_dir if legacy else _theme_var_dir(
                out_dir, region, domain, var, theme, run_id)
            mpath = os.path.join(tdir, "latest.json")
            if not os.path.isfile(mpath):
                continue
            try:
                vman = read_manifest(mpath)
            except Exception as exc:
                print("  ! %s/%s/%s/%s: manifest illeggibile (%s): skip"
                      % (region, domain, run_id, var, exc), file=sys.stderr)
                invalid = True
                break
            if not _valid_var_manifest(vman):
                invalid = True
                break
            theme_manifests[theme] = (tdir, vman)
        if invalid or not theme_manifests:
            continue

        entry = {"unit": None, "dir": "%s/%s" % (domain, var), "themes": {}}
        for theme, (tdir, vman) in sorted(theme_manifests.items()):
            keep = {f["file"] for f in vman["frames"]}
            prune_dir(tdir, keep)
            if entry["unit"] is None:
                entry["unit"] = vman.get("unit")
            ref_time = ref_time or vman.get("reference_time")
            generated_at = generated_at or vman.get("generated_at")
            expected = max(expected, int(vman.get("expected_frames") or len(vman["valid_times"])))
            bbox = bbox or vman.get("bbox")
            bounds = bounds or vman.get("bounds")
            grid = grid or vman.get("grid")
            sub = ("%s/%s" % (domain, var) if legacy else
                   "%s/runs/%s/%s" % (domain, run_id, var))
            entry["dir"] = sub
            entry["themes"][theme] = {
                "count": len(vman["valid_times"]),
                "valid_times": list(vman["valid_times"]),
                "dir": sub,
                "manifest": "%s/latest.json" % sub,
            }
        variables[var] = entry

    if not variables:
        return None
    if legacy:
        ref_time = ref_time or generated_at
        run_id = "legacy-" + _run_id(ref_time)
    counts = [max((len(t.get("valid_times", [])) for t in v.get("themes", {}).values()), default=0)
              for v in variables.values()]
    actual = max(counts) if counts else 0
    pct = round(100.0 * min(actual, expected) / expected, 1) if expected else 100.0
    return {
        "id": run_id,
        "reference_time": ref_time,
        "generated_at": generated_at,
        "expected_frames": expected or actual,
        "frames_available": actual,
        "completion_pct": pct,
        "variables": variables,
        "bbox": bbox,
        "bounds": bounds,
        "grid": grid,
    }


def rebuild_index(in_dir: str, out_dir: str, domain_list, region_names, themes) -> int:
    """Rebuild ``<out>/<region>/index.json`` from the existing per-var manifests.

    ``--index-only`` mode (single-writer merge job): NO rendering, pure
    JSON/filesystem (works without numpy/Pillow/omfiles). ``domain_list`` is the
    EXPLICIT list of successfully rendered shard domains. For every region only
    the domains that (a) are present under ``in_dir`` (engine tree:
    ``<in>/<domain>/latest.json``) and (b) have at least ONE structurally valid
    var manifest are indexed; every valid theme dir is PRUNED against the
    keep-set of its OWN manifest (only ``*.webp``, by file name). Invalid var
    manifests are skipped ENTIRELY (NO prune, NO index entry). The index schema
    and writer are the render path's ones (``write_region_index``) to avoid any
    schema drift. Returns the number of region indexes written (0 = publish
    nothing).
    """
    written = 0
    for rname in region_names:
        region_bbox = REGIONS[rname]
        index_domains = {}
        region_themes = set()
        for domain in domain_list:
            if not os.path.isfile(os.path.join(in_dir, domain, "latest.json")):
                print("[index] %s/%s: escluso: non presente in --in" % (rname, domain))
                continue
            region_var_root = os.path.join(out_dir, rname, domain)
            if not os.path.isdir(region_var_root):
                print("[index] %s/%s: escluso: nessun output renderizzato"
                      % (rname, domain))
                continue

            runs_root = os.path.join(region_var_root, "runs")
            runs = []
            if os.path.isdir(runs_root):
                for run_id in os.listdir(runs_root):
                    rr = os.path.join(runs_root, run_id)
                    if os.path.isdir(rr):
                        rec = _collect_index_run(rr, out_dir, rname, domain,
                                                 run_id, themes)
                        if rec:
                            runs.append(rec)
            # Migrazione compatibile: la vecchia struttura <domain>/<var> resta
            # leggibile come una run finché il branch non ha tre run versionate.
            legacy_dirs = [n for n in os.listdir(region_var_root)
                           if n != "runs" and os.path.isdir(os.path.join(region_var_root, n))]
            if legacy_dirs:
                legacy_root = os.path.join(region_var_root, "__legacy_scan")
                # Legge le variabili una per una senza spostare i file del branch.
                legacy_vars = {}
                legacy_ref = legacy_generated = None
                legacy_expected = 0
                legacy_bbox = legacy_bounds = legacy_grid = None
                for var in legacy_dirs:
                    var_dir = os.path.join(region_var_root, var)
                    valid_manifests = []
                    for theme in themes:
                        mp = os.path.join(var_dir, "latest.json")
                        if os.path.isfile(mp):
                            try:
                                vm = read_manifest(mp)
                            except Exception:
                                continue
                            if _valid_var_manifest(vm):
                                valid_manifests.append((theme, vm))
                    if not valid_manifests:
                        continue
                    vm = valid_manifests[0][1]
                    legacy_ref = legacy_ref or vm.get("reference_time")
                    legacy_generated = legacy_generated or vm.get("generated_at")
                    legacy_expected = max(legacy_expected, int(vm.get("expected_frames") or len(vm["valid_times"])))
                    legacy_bbox = legacy_bbox or vm.get("bbox")
                    legacy_bounds = legacy_bounds or vm.get("bounds")
                    legacy_grid = legacy_grid or vm.get("grid")
                    var_entry = {"unit": vm.get("unit"), "dir": "%s/%s" % (domain, var), "themes": {}}
                    for theme, tman in valid_manifests:
                        var_entry["themes"][theme] = {
                            "count": len(tman["valid_times"]),
                            "valid_times": list(tman["valid_times"]),
                            "dir": "%s/%s" % (domain, var),
                            "manifest": "%s/latest.json" % (domain + "/" + var),
                        }
                    legacy_vars[var] = var_entry
                if legacy_vars:
                    legacy_counts = [max((len(t.get("valid_times", [])) for t in v.get("themes", {}).values()), default=0)
                                     for v in legacy_vars.values()]
                    legacy_actual = max(legacy_counts) if legacy_counts else 0
                    legacy_ref = legacy_ref or legacy_generated
                    runs.append({
                        "id": "legacy-" + _run_id(legacy_ref),
                        "reference_time": legacy_ref,
                        "generated_at": legacy_generated,
                        "expected_frames": legacy_expected or legacy_actual,
                        "frames_available": legacy_actual,
                        "completion_pct": round(100.0 * min(legacy_actual, legacy_expected) / legacy_expected, 1) if legacy_expected else 100.0,
                        "variables": legacy_vars,
                        "bbox": legacy_bbox, "bounds": legacy_bounds, "grid": legacy_grid,
                    })
            runs.sort(key=lambda r: str(r.get("reference_time") or r.get("id") or ""), reverse=True)
            runs = runs[:3]
            if not runs:
                print("[index] %s/%s: escluso: nessun manifest variabile valido"
                      % (rname, domain), file=sys.stderr)
                continue
            latest_run = runs[0]
            variables = latest_run["variables"]
            dom_bbox, dom_bounds, dom_grid = latest_run.get("bbox"), latest_run.get("bounds"), latest_run.get("grid")
            for run in runs:
                for variable in run["variables"].values():
                    region_themes.update(variable.get("themes", {}).keys())
            index_domains[domain] = {
                "bbox": dom_bbox,
                "bounds": dom_bounds,
                "grid": dom_grid,
                "variables": variables,
                "current_run": latest_run["id"],
                "runs": runs,
            }

        if not index_domains:
            print("[index] %s: NESSUN dominio valido -> index.json NON scritto"
                  % rname)
            continue
        theme_list = [t for t in themes if t in region_themes]
        write_region_index(out_dir, rname, region_bbox, index_domains, theme_list)
        print("[index] %s: index.json scritto (%d domini: %s)"
              % (rname, len(index_domains), ", ".join(sorted(index_domains))))
        written += 1
    return written


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _split_csv(value):
    if not value:
        return None
    items = [p.strip() for p in str(value).split(",") if p.strip()]
    return items or None


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Pre-render forecast-v2 .om frames to colored WebP per region."
    )
    ap.add_argument("--in", dest="in_dir", default="data/fm2",
                    help="engine output root containing <domain>/latest.json")
    ap.add_argument("--out", dest="out_dir", default="data/fm2-rendered",
                    help="output root (client default base is fm2-rendered/)")
    ap.add_argument("--regions", default=None,
                    help="comma-separated region allow-list (default: "
                         "italia,europa,domain)")
    ap.add_argument("--domains", default=None,
                    help="comma-separated domain allow-list (default: all found)")
    ap.add_argument("--vars", default=None,
                    help="comma-separated variable allow-list (default: manifest order)")
    ap.add_argument("--themes", default="light",
                    help="comma-separated color-scale themes (default: light; "
                         "the dark theme has been REMOVED in 1.2.4.13)")
    ap.add_argument("--quality", type=int, default=90, help="WebP quality (lossy)")
    ap.add_argument("--lossless", action="store_true", help="write lossless WebP")
    ap.add_argument("--limit-frames", type=int, default=0,
                    help="render at most N frames per domain (debug)")
    ap.add_argument("--method", type=int, default=6,
                    help="Pillow WebP encode method (0-6; 6=max compression/slowest)")
    ap.add_argument("--upsample", default="auto",
                    help="image upsampling: 'auto' targets ~%d px on the shorter axis "
                         "(capped by --upsample-max) or an integer factor >=1 "
                         "(1 = no upsampling, historical output)" % UPSAMPLE_TARGET)
    ap.add_argument("--upsample-max", type=int, default=UPSCALE_MAX_DEFAULT,
                    help="upper bound for the 'auto' upsampling factor "
                         "(default: %d)" % UPSCALE_MAX_DEFAULT)
    ap.add_argument("--index-only", action="store_true",
                    help="rebuild region index.json from existing per-var latest.json "
                         "manifests; no rendering (works without numpy/Pillow/omfiles)")
    ap.add_argument("--watch-manifest", default=None,
                    help="wait for atomically announced .om frames in an engine progress manifest")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    args.domains = _split_csv(args.domains)
    args.vars = _split_csv(args.vars)

    themes = _split_csv(args.themes) or ["light"]
    bad = [t for t in themes if t != "light"]
    if bad:
        print("FATAL: dark theme removed in 1.2.4.13 (only 'light' is allowed; "
              "requested: %s)" % ",".join(bad), file=sys.stderr)
        return 2
    args.themes = themes

    region_names = _split_csv(args.regions) or list(REGIONS.keys())
    regions = {}
    for name in region_names:
        if name not in REGIONS:
            print("FATAL: unknown region %r (known: %s)"
                  % (name, ",".join(REGIONS)), file=sys.stderr)
            return 2
        regions[name] = REGIONS[name]

    in_dir = os.path.abspath(args.in_dir)
    out_dir = os.path.abspath(args.out_dir)

    if not os.path.isdir(in_dir):
        print("FATAL: --in not a directory: %s" % in_dir, file=sys.stderr)
        return 2

    # Domain dirs carrying an engine manifest (the --in gate, both modes).
    in_domains = []
    for name in sorted(os.listdir(in_dir)):
        ddir = os.path.join(in_dir, name)
        if os.path.isdir(ddir) and os.path.isfile(os.path.join(ddir, "latest.json")):
            in_domains.append(name)
    if args.watch_manifest and args.domains:
        for name in args.domains:
            os.makedirs(os.path.join(in_dir, name), exist_ok=True)
            if name not in in_domains:
                in_domains.append(name)

    if args.index_only:
        # --index-only: rebuild the region indexes ONLY (no rendering, no heavy
        # deps). --domains is the EXPLICIT list of successfully rendered shard
        # domains; an empty list publishes nothing (exit 1).
        if not in_domains:
            print("FATAL: no domain dirs with latest.json under %s" % in_dir,
                  file=sys.stderr)
            return 2
        if not args.domains:
            print("FATAL: --index-only requires a non-empty --domains "
                  "(comma-separated rendered shard domains)", file=sys.stderr)
            return 2
        os.makedirs(out_dir, exist_ok=True)
        written = rebuild_index(in_dir, out_dir, args.domains, region_names, themes)
        if not written:
            print("[index] nessuna regione ha un dominio valido: nulla pubblicato.",
                  file=sys.stderr)
            return 1
        copy_color_scales(out_dir)
        print("[done] region index written: %d -> %s" % (written, out_dir))
        return 0

    _require_render_deps()

    try:
        scales = load_color_scales()
    except Exception as exc:
        print("FATAL: cannot load color scales: %s" % exc, file=sys.stderr)
        return 2

    domains = [os.path.join(in_dir, name) for name in in_domains]
    if args.domains:
        domains = [d for d in domains if os.path.basename(os.path.normpath(d)) in args.domains]
    if not domains:
        print("FATAL: no domain dirs with latest.json under %s" % in_dir, file=sys.stderr)
        return 2

    os.makedirs(out_dir, exist_ok=True)

    total_ok = 0
    any_domain_ok = False
    index_domains = {rname: {} for rname in regions}

    for ddir in domains:
        domain = os.path.basename(os.path.normpath(ddir))
        print("[domain] %s" % domain)
        t0 = time.perf_counter()
        try:
            ok, skipped, err, contrib, nframes, nbytes = render_domain(
                ddir, out_dir, scales, args, regions)
        except Exception:
            print("  ! domain %s crashed:" % domain, file=sys.stderr)
            traceback.print_exc()
            continue
        # Observability: per-domain timing/size (no frame caps anywhere).
        print("[render] %s: %d frame, %d webp, %d bytes, %.1fs"
              % (domain, nframes, ok, nbytes, time.perf_counter() - t0))
        if ok:
            any_domain_ok = True
            total_ok += ok
            print("  ok=%d skipped=%d%s"
                  % (ok, skipped, (" (%s)" % err) if err else ""))
        elif err == "noop":
            any_domain_ok = True
            print("  noop (manifest invariato)")
        else:
            print("  ok=0 skipped=%d (%s)" % (skipped, err or "empty"))
        for rname, entry in contrib.items():
            index_domains[rname][domain] = entry

    if total_ok > 0:
        for rname in regions:
            write_region_index(out_dir, rname, regions[rname], index_domains[rname], args.themes)

        # Publish the exact color scales the renderer used so the client legend and
        # the raster agree (single source of truth in the cache root).
        copy_color_scales(out_dir)

    print("[done] frames written: %d -> %s" % (total_ok, out_dir))
    return 0 if any_domain_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
