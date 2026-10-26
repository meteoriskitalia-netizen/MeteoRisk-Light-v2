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

Label font per region (1.2.4.26)
--------------------------------
``LABEL_FONT_PX`` (12 px) remains the historical size and the size still used
by every region that is already legible at the client's scale. The NORTH strip
is not: it is 2879x521 px (ncep_gfs025, 0.25 deg, upsampling x2), while the
client map pane is 560 px tall (``#leaflet-map`` CSS). With Leaflet
``fitBounds`` the displayed scale is ``min(panel_w / out_w, panel_h / out_h)``;
for the strip the width binds (521 * (1008/2879) = 182 px < 560), so the scale
is ``panel_w / out_w``. The measured desktop pane width is 1008-1382 px
(fit factor 0.35-0.48 of the strip width); 0.35 is the WORST case and is the
one used here. The Pillow default font has 9 px-high digits at 12 px
(``getbbox("0123456789")`` -> height 9); the readability target is 7 px on
screen, i.e. ``7 / 0.35 = 20`` px in the image, i.e.
``12 * 20 / 9 = 26.7`` -> ~26 px (the value quoted by the study).  The size is
NOT hard-coded: ``label_font_px`` inverts the metrics measured on THIS host
(Pillow 12.3 gives 18 px digits at 26 px, ratio 0.69, not 0.75, and 20 px only
at 29 px), so the empirical search lands on 29 px: with the same formula the
result is the SMALLEST size that actually reaches 7.0 px on screen.  The
hypothesis is a pane without padding that fits the image by its width (the
vertical fit is not binding for the 2879x521 strip).  ``--label-font-px``
forces one global size (0 = per-region auto); ``--label-font-px 12``
reproduces the historical bytes.  Labels are kept inside the image (and inside
the disc when enabled); the bound is enforced only when the font is enlarged,
so the 12-px regions keep their historical output byte-for-byte.

Polar disc for ``north`` (1.2.4.26, published since 1.2.4.30)
-------------------------------------------------------------
``--north-projection disc`` renders the north region with a north-polar
stereographic projection (edge at 20 N, circular alpha mask) instead of the
Mercator strip. The DEFAULT stays ``strip``, so a render without the flag is
byte-identical to the historical one; the five ``forecast-maps-*.yml``
workflows pass the flag explicitly. The client (>= 1.2.4.27,
``fmPolarPaintProjectedDisc``) draws the published square inside the clipped
20 N circle without sampling a single pixel. The disc is sampled from the real
domain data up to 90 N (the engine bbox reaches 90 N even though the Mercator
strip stops at 85.0511 N), is ``--north-disc-size`` px wide (default 1000) and
keeps Greenwich at the BOTTOM (same orientation as the client's polar canvas).

The manifests carry ``"projection": "stereographic"`` as a STRING -- that is
what ``fmRegionProjection`` reads; anything that is not a string is normalised
to ``mercator`` and the client would reproject the already projected disc
(measured: 4.23% agreement instead of 99.96%). The full geometry (centre,
standard parallel, formula, orientation) lives in a separate
``projection_meta`` object. With ``--north-projection strip`` neither key is
written, i.e. the historical manifests are unchanged.

Incremental render
------------------
``--existing-list <file>`` skips the (frame, variable) units that are ALREADY
published on the branch. It is a list of published paths, one per line, relative
to the branch root
(``data/fm2-rendered/<region>/<domain>/runs/<run_id>/<var>/<VALID>.webp``); only
the trailing six components are significant
(``<region>/<domain>/runs/<run_id>/<var>/<file>``), so the caller may prepend
whatever checkout prefix it uses. The renderer never derives this from the
filesystem: on a CI shard the published WebP live on the branch, NOT in the
artifact.

Five things MUST agree, or the published tree silently loses files:
  * the payload construction excludes the frames already published FOR THAT
    ``run_id`` (the output is run-scoped, so a frame of another run can never
    collide);
  * ``keep`` of ``prune_dir`` is the UNION of the listed names and the names
    written now -- a keep-set built from the delta alone would make the delta the
    whole directory, which is the trap;
  * the per-variable ``latest.json`` is the UNION of the frames already listed
    and the frames just rendered (the client reads ``valid_times`` from it);
  * ``expected_frames`` stays the HORIZON (``len(manifest.valid_times)``), never
    the delta, or ``completion_pct`` in the region index would be invented;
  * every manifest carries ``palette_sha256``: when it differs from the value
    found on disk the variable is RE-RENDERED instead of skipped, because the
    published pixels would otherwise keep the old colors with nothing saying so.
    Declared cost: a palette (or ``WEBP_VERSION``) change makes ONE cycle cost
    the full render again.

Without ``--existing-list`` (or with an EMPTY list) nothing changes: every frame
is rendered and the prune keeps exactly what this run wrote. The skip is gated on
PROOF -- the on-disk ``latest.json`` stating the same ``version`` and the same
palette, or an explicit ``--existing-palette-sha`` for a shard without manifests:
a list alone cannot prove which colors the published pixels have, so it buys the
manifest union and the prune protection, not the skip.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as _dt
import functools
import hashlib
import json
import math
import multiprocessing
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

# Published tree layout, relative to the branch root:
#   data/fm2-rendered/<region>/<domain>/runs/<run_id>/<var>/<VALID>.webp
# The incremental list is parsed from the TAIL of each path (see
# ``existing_key_from_path``), so this constant only documents the shape.
EXISTING_LIST_RUNS_COMPONENT = "runs"
_VALID_STEM_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2})(\d{2})$")

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


def palette_sha256(path: str | None = None) -> str:
    """sha256 of the palette file the pixels are computed with.

    ``WEBP_VERSION`` is a hand-maintained constant: it cannot notice a change in
    ``color_scales.json``. This digest can, so every per-variable manifest
    records it and the incremental path refuses to skip a variable whose
    published manifest was produced with a different palette.
    """
    h = hashlib.sha256()
    with open(path or COLOR_SCALES_PATH, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _clean_palette_sha(value) -> str | None:
    """Normalise ``--existing-palette-sha`` (lowercase hex) or return None."""
    if not value:
        return None
    s = str(value).strip().lower()
    if len(s) == 64 and all(c in "0123456789abcdef" for c in s):
        return s
    print("  ! --existing-palette-sha non e' uno sha256 esadecimale: ignorato (%r)"
          % value, file=sys.stderr)
    return None


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
    """Return f(arr, categorical=False)->arr remapping rows to Mercator spacing.

    ``scale == 1`` is the historical row-only resampler: width stays ``nx``,
    height stays ``ny`` and the result is byte-identical. ``scale > 1``
    BILINEARLY interpolates the DATA VALUES onto an
    ``((nx-1)*scale+1, (ny-1)*scale+1)`` grid (Mercator rows, linear-lon
    columns) BEFORE the discrete breakpoint lookup, so band edges become smooth
    while every pixel still wears an EXACT palette color (never a blend). Fully
    vectorized with numpy.

    ``categorical=True`` switches BOTH axes (the row remap AND the ``scale``
    upsample) to NEAREST-NEIGHBOUR. Averaging is meaningless on a CODE: 3 and
    45 average to 24, a WMO code nobody issued, which the discrete breakpoint
    lookup then paints with the "undefined" gray -- the measured gray seams
    between two coloured zones. See ``CATEGORICAL_VARS``.

    NaN policy: a non-finite sample never gains a color. At ``scale > 1`` a
    pixel is emitted only when ALL of its bilinear source corners are finite;
    otherwise it is NaN (transparent). At ``scale == 1`` the historical two-tap
    rule is kept verbatim. On the categorical path a pixel is emitted only when
    its NEAREST source sample is finite.
    """
    ny = int(grid["ny"])
    nx = int(grid["nx"])
    dy = float(grid["dy"])
    lat_min = float(grid["latMin"])
    lat_north = lat_min + (ny - 1) * dy
    scale = max(1, int(scale))

    if scale <= 1:
        if ny <= 1:
            def resample_1_flat(arr: np.ndarray, categorical: bool = False) -> np.ndarray:
                return np.asarray(arr, dtype=np.float32).copy()
            return resample_1_flat
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
        # Nearest source row: round-half-up, then clamp. Same pixel grid as the
        # two-tap remap (the output row centres do not move).
        nr = np.clip(np.floor(positions + 0.5).astype(np.int64), 0, ny - 1)
        w = (positions - r0)[:, None]

        def resample_1(arr: np.ndarray, categorical: bool = False) -> np.ndarray:
            a = np.asarray(arr, dtype=np.float32)
            if a.shape[0] != ny:
                raise ValueError("row count %d != grid ny %d" % (a.shape[0], ny))
            if categorical:
                return a[nr, :].astype(np.float32)
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
    # Nearest source column/row for the categorical path (both axes).
    nc = np.clip(np.floor(col_pos + 0.5).astype(np.int64), 0, nx - 1)
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
    nr = np.clip(np.floor(row_pos + 0.5).astype(np.int64), 0, ny - 1)
    wr = (row_pos - r0).astype(np.float32)
    wR0 = (1.0 - wr)[:, None]
    wR1 = wr[:, None]
    wC0 = (1.0 - wc)[None, :]
    wC1 = wc[None, :]
    one = np.float32(1.0)
    cover_eps = np.float32(1e-6)

    def resample_up(arr: np.ndarray, categorical: bool = False) -> np.ndarray:
        a = np.asarray(arr, dtype=np.float32)
        if a.shape[0] != ny or a.shape[1] != nx:
            raise ValueError("shape %r != grid (ny=%d, nx=%d)"
                             % (tuple(a.shape), ny, nx))
        if categorical:
            # NEAREST-NEIGHBOUR on both axes: a code is copied, never averaged.
            # The pixel is emitted only when that exact source sample is finite.
            return a[nr][:, nc].astype(np.float32)
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
# North-polar stereographic disc (optional `north` projection, 1.2.4.26)
#
# Used ONLY with ``--north-projection disc`` (default: the Mercator strip).
# The client's dedicated path does not exist yet, so the strip remains the
# published default; the disc is an opt-in experiment whose manifests carry
# explicit ``projection`` metadata.
#
# Convention: x = rho*sin(lon), y = rho*cos(lon) with row 0 at the TOP and
# y growing downwards -> Greenwich (0 deg) at the BOTTOM, east to the RIGHT,
# exactly like the client polar canvas (``fmPolarDrawOverlays`` uses
# ``point(lat, lon) = [cx + R*rho*sin(lon), cy + R*rho*cos(lon)]``).
# --------------------------------------------------------------------------- #
NORTH_DISC_EDGE_LAT = 20.0        # bordo del disco (lat del raggio massimo)
# The value the CLIENT reads: `fmRegionProjection` accepts only a STRING and
# normalises anything else to 'mercator' (which would reproject the already
# projected disc). The geometry lives in a separate `projection_meta` key.
POLAR_PROJECTION = "stereographic"


def polar_disc_geometry(size: int, edge_lat: float = NORTH_DISC_EDGE_LAT) -> dict:
    """Pixel -> (lat, lon) for a north-polar stereographic disc of ``size`` px.

    ``rho = tan((90-lat)/2) / tan((90-edge_lat)/2)`` is the stereographic radius
    normalised at the ``edge_lat`` circle (rho=1 on the border, rho=0 at the
    Pole). The mask is a HARD circle (alpha 0 outside; it is what the measured
    encode cost refers to). Returns arrays of shape ``(size, size)``.
    """
    size = max(2, int(size))
    c = (size - 1) * 0.5
    jj, ii = np.mgrid[0:size, 0:size]
    px = (ii - c) / c
    py = (jj - c) / c
    rho = np.hypot(px, py)
    cap = math.tan(math.radians((90.0 - float(edge_lat)) * 0.5))
    lat = 90.0 - 2.0 * np.degrees(np.arctan(rho * cap))
    lon = np.degrees(np.arctan2(px, py))
    return {
        "size": size,
        "center": c,
        "radius": c,
        "lat": lat,
        "lon": lon,
        "mask": rho <= 1.0 + 1e-9,
        "cap": cap,
    }


def polar_overlay_grid(size: int) -> dict:
    """``cgrid`` sentinel for the disc: sampled field and output are 1:1."""
    size = int(size)
    return {"nx": size, "ny": size, "dx": 1.0, "dy": 1.0,
            "latMin": 0.0, "lonMin": 0.0, "map": "identity"}


def polar_contour_grid(grid: dict, size: int, edge_lat: float) -> dict:
    """``cgrid`` for the disc isolines: SOURCE grid + polar projection metadata.

    Computing the contours on the source grid (e.g. 1440x281) instead of on the
    1000x1000 disc raster keeps the marching-squares cost identical to the
    strip (376k cells vs 1M) and projects every vertex with the exact
    stereographic formula, so the isolines stay the real model isolines.
    """
    cg = dict(grid)
    cg.update({"map": "polar", "size": int(size),
               "edge_lat": float(edge_lat)})
    return cg


def _project_polar(cgrid: dict, x, y):
    """Data-grid (col, row) -> disc pixel (px, py), stereographic projection."""
    size = int(cgrid["size"])
    c = (size - 1) * 0.5
    cap = math.tan(math.radians((90.0 - float(cgrid["edge_lat"])) * 0.5))
    lon = np.radians(float(cgrid["lonMin"]) + np.asarray(x) * float(cgrid["dx"]))
    lat = float(cgrid["latMin"]) + np.asarray(y) * float(cgrid["dy"])
    rho = np.tan(np.radians(np.clip(90.0 - lat, 0.0, 180.0)) * 0.5) / cap
    return c + c * rho * np.sin(lon), c + c * rho * np.cos(lon)


def polar_projection_meta(size: int, edge_lat: float, max_lat: float) -> dict:
    """Serialisable description of the disc published in ``latest.json``."""
    return {
        "type": "polar_stereographic",
        "center_lat": 90.0,
        "central_lon": 0.0,
        "lon_zero": "bottom",
        "lon_positive": "east",
        "edge_lat": float(edge_lat),
        "max_lat": float(max_lat),
        "size": int(size),
        "radius_px": (int(size) - 1) * 0.5,
        "formula": "rho = tan((90-lat)/2) / tan((90-edge_lat)/2)",
        "client_orientation": "Greenwich at bottom, east to the right "
                              "(fmPolarDrawOverlays canvas)",
    }


def projection_parts(value):
    """Return ``(projection_string, projection_meta)`` for a manifest value.

    The published shape (>= 1.2.4.30) is the STRING ``"stereographic"`` under
    ``projection`` plus the geometry under ``projection_meta``. The dict that
    the first disc builds wrote under ``projection`` is still accepted and
    split here, so ``--index-only`` heals such a branch instead of republishing
    an object the client silently ignores.
    """
    if isinstance(value, str):
        text = value.strip().lower()
        return (text or None), None
    if isinstance(value, dict):
        kind = str(value.get("type") or "").strip().lower()
        name = POLAR_PROJECTION if kind == "polar_stereographic" else None
        return name, dict(value)
    return None, None


def consensus_projection(entries):
    """``(projection, projection_meta)`` if EVERY entry declares the same value.

    This is the ``09f8158b`` rule, applied to the two lists the client covers as
    a block:

    * the DOMAINS of a region -- the region-level key is applied by
      ``fmRegionProjection`` to every domain of that region;
    * the RUNS of a domain -- ``domains.<dominio>.projection`` is used for EVERY
      run when the frame itself does not carry one, so a disc published from the
      newest run would paint the older Mercator strip as a disc.

    An entry that declares NOTHING does not agree: a missing value means
    ``"mercator"`` (the client default) and extending it to the other entries
    would be an invention. That is why an all-Mercator list produces no key at
    all -- the default is already ``mercator``. Only STRINGS count: an object
    (shape of the first disc builds) is not a value the client accepts.
    """
    values = set()
    metas = {}
    for entry in entries or ():
        if not isinstance(entry, dict):
            return None, None
        value = entry.get("projection")
        if not isinstance(value, str) or not value:
            return None, None
        values.add(value)
        meta = entry.get("projection_meta")
        if value not in metas and isinstance(meta, dict):
            metas[value] = meta
    if len(values) != 1:
        return None, None
    value = next(iter(values))
    return value, metas.get(value)


def build_polar_resampler(grid: dict, size: int,
                          edge_lat: float = NORTH_DISC_EDGE_LAT):
    """Return ``f(arr) -> (disc_field, mask)`` for the polar stereo disc.

    ``arr`` is the cropped source frame ``(ny, nx)`` (row 0 = south) on the
    domain grid; the output is ``(size, size)`` float32 with NaN outside the
    data coverage. Bilinear weights are precomputed once (the disc does not
    move); a pixel is emitted only when its four source corners are finite,
    the same NaN policy as the Mercator upsampler. ``categorical=True`` samples
    the NEAREST source pixel instead (a code must not be averaged). Longitudes
    are wrapped on global grids so the 180 deg seam is sampled from the
    opposite edge.
    """
    ny = int(grid["ny"])
    nx = int(grid["nx"])
    dxg = float(grid["dx"])
    dyg = float(grid["dy"])
    lat0 = float(grid["latMin"])
    lon0 = float(grid["lonMin"])
    lat_n = lat0 + (ny - 1) * dyg
    lon_e = lon0 + (nx - 1) * dxg

    geo = polar_disc_geometry(size, edge_lat)
    lat = geo["lat"]
    lon = geo["lon"]
    mask = geo["mask"]
    row = (lat - lat0) / dyg
    col = (lon - lon0) / dxg
    if (lon_e - lon0) > 359.0:
        # Global grid: 1440 columns (-180..179.75) need mod(nx); a 1441-column
        # grid (-180..180, duplicated endpoint) needs mod(nx-1).
        period = float(nx) if (nx - 1) * dxg < 360.0 - 1e-9 else float(nx - 1)
        col = np.mod(col, period)
    else:
        period = None
    row_ok = (row >= 0.0) & (row <= ny - 1 + 1e-9)
    if period is not None:
        # After mod() the column is ALWAYS inside [0, period): the historical
        # ``col <= nx-1`` test would reject the last cell strip (lon 179.75..
        # 180) and draw a transparent seam at the antimeridian.
        col_ok = np.ones(col.shape, dtype=bool)
    else:
        col_ok = (col >= 0.0) & (col <= nx - 1 + 1e-9)
    valid = mask & row_ok & col_ok
    r0 = np.clip(np.floor(row).astype(np.int64), 0, ny - 1)
    c0 = np.clip(np.floor(col).astype(np.int64), 0, nx - 1)
    r1 = np.clip(r0 + 1, 0, ny - 1)
    if period is not None:
        # The 180 deg seam must interpolate between the last and the first
        # column; clipping c1 to nx-1 would publish a visible 1-px seam.
        c1 = np.mod(c0 + 1, int(period)).astype(np.int64)
    else:
        c1 = np.clip(c0 + 1, 0, nx - 1)
    wr = (row - r0).astype(np.float32)
    wc = (col - c0).astype(np.float32)
    # Nearest sample for the CATEGORICAL path: a code is copied, never mixed.
    nr = np.clip(np.floor(row + 0.5).astype(np.int64), 0, ny - 1)
    if period is not None:
        nc = np.mod(np.floor(col + 0.5).astype(np.int64),
                    int(period)).astype(np.int64)
    else:
        nc = np.clip(np.floor(col + 0.5).astype(np.int64), 0, nx - 1)
    idx_nn = (nr * nx + nc).ravel()
    idx00 = (r0 * nx + c0).ravel()
    idx01 = (r0 * nx + c1).ravel()
    idx10 = (r1 * nx + c0).ravel()
    idx11 = (r1 * nx + c1).ravel()
    w00 = ((1.0 - wr) * (1.0 - wc)).ravel().astype(np.float32)
    w01 = ((1.0 - wr) * wc).ravel().astype(np.float32)
    w10 = (wr * (1.0 - wc)).ravel().astype(np.float32)
    w11 = (wr * wc).ravel().astype(np.float32)
    inside = valid.ravel()
    out_shape = (int(geo["size"]), int(geo["size"]))

    def resample_polar(arr, categorical: bool = False):
        a = np.asarray(arr)
        if a.shape[0] != ny or a.shape[1] != nx:
            raise ValueError("shape %r != grid (ny=%d, nx=%d)"
                             % (tuple(a.shape), ny, nx))
        flat = np.ascontiguousarray(a, dtype=np.float32).ravel()
        if categorical:
            # NEAREST-NEIGHBOUR (see build_row_resampler): the disc has the same
            # "code averaged into a nonexistent value" failure as the strip.
            vnn = flat[idx_nn]
            ok = inside & np.isfinite(vnn)
            out = np.where(ok, vnn, np.float32(np.nan))
            return out.astype(np.float32).reshape(out_shape), mask
        v00 = flat[idx00]
        v01 = flat[idx01]
        v10 = flat[idx10]
        v11 = flat[idx11]
        ok = (inside & np.isfinite(v00) & np.isfinite(v01)
              & np.isfinite(v10) & np.isfinite(v11))
        out = v00 * w00 + v01 * w01 + v10 * w10 + v11 * w11
        out = np.where(ok, out, np.float32(np.nan)).astype(np.float32)
        return out.reshape(out_shape), mask

    return resample_polar


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
    # --- livello 10 hPa (~30-32 km; scale in color_scales.json) -------------
    # Scelte INGEGNERISTICHE dichiarate, non tarature: 4 degC separano le
    # isoterme della bassa stratosfera (T fra -95 e -20 degC), 80 m il
    # geopotenziale (Z10 fra ~29500 e ~33700 m), 5 m/s il getto (0..120 m/s).
    "temperature_10hPa": 4.0,
    "geopotential_height_10hPa": 80.0,
    "wind_speed_10hPa": 5.0,
}
CONTOUR_VARS = frozenset(CONTOUR_INTERVALS.keys())

# --------------------------------------------------------------------------- #
# CAMPI CATEGORICI (nessuna interpolazione: NEAREST-NEIGHBOUR)
#
# Un CODICE non e' una grandezza: la media di 3 e 45 e' 24, un codice WMO che
# nessun modello ha emesso. Il lookup a breakpoint dipinto con quella media
# finisce nella banda "non definito" (148,148,148) e produce le righe GRIGIE fra
# due zone di colore. MISURATO il 2026-10-26 sul frame reale dwd_icon_d2
# 2026-10-26T06Z: weather_code con la sola interpolazione dei valori aveva il
# 9,98 % di pixel non interi in `europa` (x1) e il 19,80 % in `italia` (x3), e
# 63.395 pixel grigi generati da un valore interpolato in Italia.
#
# PERCHE' UN ELENCO QUI E NON IN color_scales.json: il JSON NON dichiara la
# categoricita'. Tutte e 35 le scale hanno ``type: "breakpoint"``, compresa
# weather_code (45 bande discrete 0..99): il tipo descrive il LOOKUP, non la
# natura del campo. Quindi la lista e' esplicita qui, dichiarata, e una nuova
# variabile categorica si aggiunge con UNA riga. Chi aggiunge un nome deve
# verificare che la sua scala sia fatta di codici discreti: interpolarlo
# inventerebbe valori.
# --------------------------------------------------------------------------- #
CATEGORICAL_VARS = frozenset({
    "weather_code",   # codice WMO 0..99 (weather_code.light: 45 bande)
})


def is_categorical(var: str) -> bool:
    """True when ``var`` holds CODES: its resampling must be nearest-neighbour."""
    return var in CATEGORICAL_VARS

# Variabili di VELOCITA' del vento -> variabile di DIREZIONE (gradi, "da cui
# soffia") letta dallo STESSO frame .om. Le direzioni NON hanno color scale
# (quindi non generano un WebP proprio): servono solo alle frecce.
ARROW_VARS = {
    "wind_speed_10m": "wind_direction_10m",
    "wind_speed_850hPa": "wind_direction_850hPa",
    "wind_speed_300hPa": "wind_direction_300hPa",
    "wind_speed_10hPa": "wind_direction_10hPa",
}

MAX_CONTOUR_LEVELS = 64           # cap sui livelli per variabile
LABEL_MIN_SPACING_PX = 110.0      # spaziatura minima fra le etichette
LABEL_FONT_PX = 12                # altezza del carattere (px di output), storico
LABEL_MIN_POLYLINE_PX = 60.0      # sotto questa lunghezza il livello non e' etichettato

# --- leggibilita' delle etichette a schermo (1.2.4.26) ---------------------
# Pannello mappa del client: #leaflet-map { height: 560px } e larghezza desktop
# misurata 1008-1382 px (fattore di visualizzazione 0.35-0.48 sulla striscia
# north di 2879 px). Si usa il caso PEGGIORE (0.35) per dimensionare il font.
PANEL_HEIGHT_PX = 560.0           # altezza CSS del pannello mappa
PANEL_WIDTH_PX_MIN = 1008.0       # 0.35 * 2879 (fit factor misurato, caso peggiore)
MIN_SCREEN_DIGIT_PX = 7.0         # altezza minima delle cifre a schermo
DIGIT_PROBE = "0123456789"        # testo di calibrazione (stesse cifre delle etichette)
LABEL_FONT_MAX_PX = 48            # tetto di sicurezza della ricerca del font
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
POLAR_OVERLAY_SUPERSAMPLE = 1.5   # disco 1000^2: 1.5x basta e costa 2,25 MP

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
    if cgrid.get("map") == "identity":
        # Polar disc: the sampled field is already in output pixels with a
        # uniform (stereographic) spacing on BOTH axes -- no Mercator remap.
        return (np.arange(ny, dtype=np.float64),
                np.arange(nx, dtype=np.float64), ny, nx)
    if cgrid.get("map") == "polar":
        # Disc isolines: SOURCE grid, projected vertex by vertex; only the
        # output size comes from the disc.
        size = int(cgrid["size"])
        return (np.arange(ny, dtype=np.float64),
                np.arange(nx, dtype=np.float64), size, size)
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


@functools.lru_cache(maxsize=128)
def _font_digit_height(px: int) -> int:
    """Ink height (px) of the digit probe at font size ``px`` (measured, cached)."""
    px = max(1, int(px))
    bb = _font(px).getbbox(DIGIT_PROBE)
    if not bb:
        return px
    return max(1, int(bb[3] - bb[1]))


def label_font_px(out_w: int, out_h: int, forced: int = 0, region: str | None = None) -> int:
    """Font size (px) that keeps the digits >= ``MIN_SCREEN_DIGIT_PX`` on screen.

    The client shows the WebP inside a 560-px-tall map pane with Leaflet
    ``fitBounds``: the displayed scale is ``min(panel_w/out_w, panel_h/out_h)``.
    For each rendered image the factor is computed from its OWN size, using the
    worst-case pane width (``PANEL_WIDTH_PX_MIN``); then the smallest font whose
    MEASURED digit height reaches ``MIN_SCREEN_DIGIT_PX / factor`` is chosen.
    A forced positive value (``--label-font-px``) bypasses the computation and
    reproduces the historical output when it is 12.

    Only the ``north`` region is auto-scaled: the measured factors of
    ``europa`` (0.90) and ``italia`` (1.10) already keep 12 px above the target,
    and the ``domain`` region is explicitly kept at the historical 12 px (it
    must stay byte-identical; its factor varies with the model grid).  When
    ``region`` is None the formula is applied to any size (used by tests).

    This is the inverse of the formula quoted in the module docstring
    (7 px / 0.35 = 20 px of digits, i.e. ~26 px with the nominal 9/12 ratio);
    the search uses the real font metrics of the host instead of an assumed
    ratio, so it returns 29 px with Pillow 12.3 (18 px digits at 26 px, 20 px
    only at 29 px).
    """
    try:
        forced = int(forced or 0)
    except (TypeError, ValueError):
        forced = 0
    if forced > 0:
        return forced
    if region is not None and region != NORTH_REGION:
        return LABEL_FONT_PX
    out_w = int(out_w)
    out_h = int(out_h)
    if out_w <= 0 or out_h <= 0:
        return LABEL_FONT_PX
    factor = min(float(PANEL_WIDTH_PX_MIN) / float(out_w),
                 float(PANEL_HEIGHT_PX) / float(out_h))
    if not (factor > 0.0):
        return LABEL_FONT_PX
    need = float(MIN_SCREEN_DIGIT_PX) / factor
    if need <= float(_font_digit_height(LABEL_FONT_PX)):
        return LABEL_FONT_PX
    for px in range(LABEL_FONT_PX + 1, LABEL_FONT_MAX_PX + 1):
        if float(_font_digit_height(px)) >= need - 1e-9:
            return px
    return LABEL_FONT_MAX_PX


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


def _label_bbox_px(font, text, ss):
    """Text bbox in OUTPUT pixels (the font is built at ``font_px * ss``)."""
    bb = font.getbbox(text)
    if not bb:
        return (0.0, 0.0, 0.0, 0.0)
    return (bb[0] / ss, bb[1] / ss, bb[2] / ss, bb[3] / ss)


def _label_fits(x, y, bb, W, H, clip_circle=None):
    """True when the text bbox drawn at ``(x, y)`` stays inside the raster.

    ``bb`` is in output pixels and already includes the glyph bearings; the
    anchor is the Pillow default top-left anchor (``la``). ``clip_circle`` is
    ``(cx, cy, radius)`` in output pixels for the polar disc.
    """
    x0 = x + bb[0]
    x1 = x + bb[2]
    y0 = y + bb[1]
    y1 = y + bb[3]
    if x0 < 0.0 or y0 < 0.0 or x1 > float(W) or y1 > float(H):
        return False
    if clip_circle is not None:
        cx, cy, r = clip_circle
        for px, py in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            if math.hypot(px - cx, py - cy) > r:
                return False
    return True


def _draw_contours(draw, sub, cgrid, scale, variant, interval, ss=1.0, placed=None,
                   force_labels=False, font_px=None, clip_circle=None):
    """Draw the isolines of ``sub`` (and their VALUE labels) on ``draw``.

    ``draw`` is an ImageDraw on the SUPERSAMPLED overlay: every coordinate is
    multiplied by ``ss``. Returns ``{"levels": [...], "labels": [...]}`` with the
    levels actually drawn and the ``(level, x, y)`` anchors of the labels (in
    OUTPUT pixel coordinates).

    ``font_px`` is the per-region label size (``None`` = historical 12 px);
    ``clip_circle`` is ``(cx, cy, radius)`` in OUTPUT pixels for the polar disc.
    When the font is enlarged the anchor is constrained so the text stays
    inside the image; at 12 px (and without a disc) the historical placement is
    left untouched (byte-identical output).
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
    font_out = max(1, int(font_px or LABEL_FONT_PX))
    font = _font(max(1, int(round(font_out * ss))))
    enforce_bounds = clip_circle is not None or font_out > LABEL_FONT_PX
    width = max(1, int(round(CONTOUR_LINE_WIDTH * ss)))
    drawn_levels = []
    drawn_labels = []
    for level in levels:
        segs = contour_segments(z, level)
        if len(segs) < 2:
            continue
        if cgrid.get("map") == "polar":
            px0, py0 = _project_polar(cgrid, segs[:, 0], segs[:, 1])
            px1, py1 = _project_polar(cgrid, segs[:, 2], segs[:, 3])
        else:
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
        if enforce_bounds and chosen:
            bb = _label_bbox_px(font, text, ss)
            inside = [c for c in chosen
                      if _label_fits(c[0], c[1], bb, W, H, clip_circle)]
            if not inside:
                # No anchor of the "spacing" subset fits: fall back to ANY
                # anchor of the level that fits (still ON its own isoline) and
                # drop the label only when the whole line runs outside the
                # raster/disc.
                fit_any = [c for c in candidates
                           if _label_fits(c[0], c[1], bb, W, H, clip_circle)]
                if fit_any:
                    inside = [max(fit_any,
                                  key=lambda c: _min_distance(placed, c))]
            chosen = inside
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


def _draw_wind_arrows(draw, speed_px, dir_px, cgrid, scale, speed_ref, ss=1.0,
                      polar=False):
    """Draw the wind arrows on ``draw`` (supersampled overlay).

    ``speed_px``/``dir_px`` are the resampled fields in OUTPUT pixel space
    (same shape as the rendered raster); ``dir_px`` is the meteorological
    direction "from". The arrows are sampled on a coarse lattice (every
    ``ARROW_STEP_PX`` output pixels, at least 3 data cells). Returns the number
    of arrows drawn.

    ``polar`` rotates the flow into the north-polar stereographic frame used by
    the disc: north points towards the pole (radially inward) and east runs
    counter-clockwise, so the pixel flow is ``(sin(lon - dir), cos(lon - dir))``
    with Greenwich at the BOTTOM (same convention as the client polar canvas).
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
    if polar:
        cx = (W - 1) * 0.5
        cy = (H - 1) * 0.5
        lam = np.degrees(np.arctan2(col_px[None, :] - cx, row_px[:, None] - cy))
        ang = np.radians(lam - dd)
        ux, uy = np.sin(ang), np.cos(ang)
    else:
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


def mask_rgba_to_disc(rgba, mask):
    """Zero every pixel OUTSIDE a hard circular mask (alpha cut at rho = 1).

    Used twice on the polar path: once on the colored raster and once AFTER the
    isoline/label overlay has been alpha-composited. The overlay is drawn on the
    full square with no circular clip, so without the second pass its pixels
    survive up to a few px beyond the rim (measured on the 1.000 px disc: 224 px
    for pressure_msl, 576 for temperature_850hPa, 2.237 for wind_speed_850hPa,
    up to rho = 1.0118). A consumer that does not clip (the client does, a plain
    <img> does not) would see an alpha ring. Pixels inside the mask are returned
    byte-identical.
    """
    out = np.array(rgba, dtype=np.uint8, copy=True)
    out[~np.asarray(mask, dtype=bool)] = 0
    return out


def _apply_overlay(base_rgba, draw_fn, ss=None):
    """Composite the overlay on the base raster (the base is NEVER resampled).

    The overlay is drawn on a separate RGBA layer (supersampled), resized with
    LANCZOS to the base size and alpha-composited: the pixels where the layer
    is completely transparent stay byte-identical to the base raster.
    ``ss`` overrides the automatic supersampling factor (the polar disc uses
    1.5x: its 1000x1000 raster with a 2x layer would cost 4 MP per variable,
    more than the 2879x521 strip at 1.5x).
    """
    arr = np.asarray(base_rgba)
    h = int(arr.shape[0])
    w = int(arr.shape[1])
    if ss is None:
        ss = _overlay_scale(h, w)
    else:
        ss = max(1.0, float(ss))
    layer = _make_overlay(h, w, ss, draw_fn)
    base = Image.fromarray(arr, "RGBA")
    return np.asarray(Image.alpha_composite(base, layer))


def _overlay_drawer(sub, cgrid, scale, variant, interval, placed,
                    force_labels=False,
                    speed_out=None, dir_out=None, speed_ref=0.0,
                    font_px=None, clip_circle=None, polar=False,
                    arrow_grid=None, arrow_scale=None):
    """Build the draw callback of the overlay (isolines + wind arrows).

    ``sub`` is the data-grid field (for the isolines), ``speed_out``/
    ``dir_out`` are the OUTPUT-raster fields (for the arrows). The callback
    receives the ImageDraw of the supersampled layer and the supersampling
    factor. ``font_px``/``clip_circle`` are forwarded to the label drawing;
    ``polar`` switches the arrow rotation to the polar-stereographic frame.
    ``arrow_grid``/``arrow_scale`` decouple the arrow sampling grid from the
    isoline grid (the disc contours use the SOURCE grid, the arrows use the
    1000x1000 sampled raster).
    """
    def _draw(draw, ss):
        if interval is not None:
            _draw_contours(draw, sub, cgrid, scale, variant, interval,
                           ss=ss, placed=placed, force_labels=force_labels,
                           font_px=font_px, clip_circle=clip_circle)
        if speed_out is not None and dir_out is not None:
            _draw_wind_arrows(draw, speed_out, dir_out,
                              arrow_grid if arrow_grid is not None else cgrid,
                              arrow_scale if arrow_scale is not None else scale,
                              speed_ref, ss=ss, polar=polar)
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
# Incremental render: the list of paths already published on the branch
#
# The workflow already has the branch state cheaply (``git fetch --depth=1
# --filter=blob:none`` + ``git ls-tree -r``: measured 3.6 s / 2.5 MiB for 118.587
# paths, no WebP blob downloaded). It hands that list over as a FILE, one path per
# line, because the renderer runs on the shard, where the published frames are
# NOT on disk -- an ``os.listdir`` here would see the delta and nothing else.
# --------------------------------------------------------------------------- #
def normalize_branch_path(path) -> str:
    """``data\\fm2-rendered/x`` -> ``data/fm2-rendered/x`` (no ``./``, no leading /)."""
    s = str(path or "").strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s.lstrip("/")


def existing_key_from_path(path):
    """``((region, domain, run_id, var), file)`` from one published WebP path.

    The published layout is
    ``<prefix>/<region>/<domain>/runs/<run_id>/<var>/<file>.webp`` and the key is
    built from the TAIL, so the branch-root prefix the caller prepends
    (``data/fm2-rendered`` in CI, an absolute checkout locally) is irrelevant.
    Returns ``None`` for a line that does not have that shape.
    """
    parts = normalize_branch_path(path).split("/")
    if len(parts) < 6 or parts[-4] != EXISTING_LIST_RUNS_COMPONENT:
        return None
    region, domain, run_id, var, name = (parts[-6], parts[-5], parts[-3],
                                         parts[-2], parts[-1])
    if not (region and domain and run_id and var and name):
        return None
    return (region, domain, run_id, var), name


def load_existing_list(path: str):
    """Read a published-path list: ``({(region, domain, run_id, var): {file}}, stats)``.

    Blank lines and ``#`` comments are skipped. Lines that do not parse as a
    published WebP path are counted in ``stats["ignored"]`` and dropped: they can
    protect nothing from the prune, so the caller prints the count instead of
    pretending the list was understood.
    """
    table: dict = {}
    stats = {"lines": 0, "kept": 0, "ignored": 0, "sample": []}
    with open(path, "r", encoding="utf-8-sig") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            stats["lines"] += 1
            parsed = existing_key_from_path(line)
            if parsed is None:
                stats["ignored"] += 1
                if len(stats["sample"]) < 5:
                    stats["sample"].append(line)
                continue
            key, name = parsed
            table.setdefault(key, set()).add(name)
            stats["kept"] += 1
    return table, stats


def valid_time_from_webp_name(name: str):
    """``2026-10-14T1600.webp`` -> ``2026-10-14T16:00Z`` (inverse of the writer)."""
    stem = str(name or "")
    if stem.lower().endswith(".webp"):
        stem = stem[:-5]
    m = _VALID_STEM_RE.match(stem)
    if not m:
        return None
    return "%s-%s-%sT%s:%sZ" % m.groups()


def frames_from_names(names) -> list:
    """Manifest frame records rebuilt from file NAMES alone (``bytes`` unknown).

    Used when the shard has no per-variable manifest: the branch list is the only
    source of "which frames are published", so the file name gives the
    ``valid_time`` and ``bytes`` stays ``None`` -- the size is not in the list and
    a zero would be a lie. The client builds the WebP URLs from ``dir`` +
    ``valid_times`` and never reads ``bytes``.
    """
    recs = []
    for name in sorted(names):
        vt = valid_time_from_webp_name(name)
        if vt is None:
            continue
        recs.append({"file": name, "valid_time": vt, "bytes": None})
    return recs


def merge_frame_records(seed_records, fresh_records):
    """UNION of the already published records and the ones just rendered.

    Returns ``(records, keep_names)``. Keyed by ``file`` and a freshly rendered
    record WINS (its ``bytes`` is measured, not the ``None`` of a record rebuilt
    from a file name). ``records`` is sorted by ``valid_time`` and excludes the
    entries without a string time (the client reads ``valid_times``); their file
    names stay in ``keep_names`` so an unparseable name is never pruned.
    """
    merged = {}
    for rec in list(seed_records or ()):
        if isinstance(rec, dict) and rec.get("file"):
            merged[rec["file"]] = rec
    for rec in list(fresh_records or ()):
        if isinstance(rec, dict) and rec.get("file"):
            merged[rec["file"]] = rec
    records = [r for r in merged.values() if isinstance(r.get("valid_time"), str)]
    records.sort(key=lambda r: r["valid_time"])
    return records, set(merged)


def var_publish_state(vdir: str, known_files, palette_sha: str,
                      declared_palette: str | None = None,
                      expected_version: str = WEBP_VERSION) -> dict:
    """What of one published ``<region>/<domain>/runs/<run>/<var>`` dir is reusable.

    ``known_files`` are the names the branch list says are published in that
    directory; ``vdir`` is where they would live. Returns
    ``{"files": set, "frames": [rec], "reuse": bool, "reason": str}`` where

      * ``files``  = every name we must NOT prune (list union manifest), i.e. the
        seed of the prune keep-set;
      * ``frames`` = the manifest records to carry over (from ``latest.json`` when
        it exists, rebuilt from the file names otherwise), so the union manifest
        always covers every file we keep;
      * ``reuse``  = True ONLY when the on-disk manifest proves the pixels were
        produced by THIS ``WEBP_VERSION`` and THIS palette. A missing manifest, a
        version change and a palette change all mean "render the whole variable":
        the list alone cannot prove which colours the published pixels have.
    """
    files = set(known_files or ())
    state = {"files": files, "frames": [], "reuse": False, "reason": "no_manifest"}
    mpath = os.path.join(vdir, "latest.json")
    manifest = None
    if os.path.isfile(mpath):
        try:
            manifest = read_manifest(mpath)
        except Exception as exc:
            print("  ! %s: manifest illeggibile (%s)" % (mpath, exc), file=sys.stderr)
            manifest = None
    if isinstance(manifest, dict):
        if manifest.get("version") != expected_version:
            state["reason"] = "version_changed"
        else:
            proof = manifest.get("palette_sha256") or declared_palette
            if not proof:
                state["reason"] = "palette_unknown"
            elif proof != palette_sha:
                state["reason"] = "palette_changed"
            else:
                state["reuse"] = True
                state["reason"] = "manifest"
    elif declared_palette and files:
        # Nessun manifest (shard senza checkout): l'unica prova disponibile e'
        # l'asserzione esplicita dell'operatore sulla palette gia' pubblicata.
        state["reuse"] = declared_palette == palette_sha
        state["reason"] = "declared" if state["reuse"] else "palette_changed"
    frames = []
    if isinstance(manifest, dict):
        for rec in (manifest.get("frames") or []):
            if isinstance(rec, dict) and rec.get("file"):
                frames.append(rec)
                files.add(rec["file"])
    # Elenco e manifest devono raccontare la stessa cosa: un file elencato ma
    # assente dal manifest non puo' sparire dal manifest (resterebbe pubblicato
    # senza entry) ne' essere potato (e' pubblicato). Si ricostruisce la entry.
    if frames:
        known_in_frames = {rec["file"] for rec in frames}
        frames.extend(frames_from_names(files - known_in_frames))
    else:
        frames = frames_from_names(files)
    state["frames"] = frames
    return state


def prune_published_dir(vdir: str, keep: set, incremental: bool, known: set,
                        label: str = "") -> int:
    """``prune_dir`` with the incremental guard rail; returns the removed count.

    With ``--existing-list`` an EMPTY known set for a directory that is not empty
    on disk is the signature of the trap (a list that says "nothing published"
    while the artifact holds WebP because the ENTRY does not match the layout).
    Deleting a whole directory of published images is never the intent of an
    incremental render, so in that case the prune is SKIPPED and reported.
    """
    if incremental and not known:
        try:
            present = [n for n in os.listdir(vdir) if n.lower().endswith(".webp")]
        except OSError:
            present = []
        if present:
            print("  ! prune saltato per %s: nessun path pubblicato nell'elenco ma "
                  "%d WebP presenti (elenco non corrispondente?)"
                  % (label or vdir, len(present)), file=sys.stderr)
            return 0
    removed = prune_dir(vdir, keep)
    if incremental and removed:
        print("  [prune] %s: %d WebP rimossi (%d tenuti)"
              % (label or vdir, removed, len(keep)))
    return removed


# --------------------------------------------------------------------------- #
# Parallel render workers
#
# The heavy part of a frame is PER (frame, variable): the .om array read, the
# Mercator row resampling, the palette lookup, the (optional) isoline/arrow
# overlay and the WebP encode. All of it scales with PIXELS, so threads would
# contend on the GIL for numpy/python work and on libwebp for the encode:
# separate PROCESSES are the only way to use the 4 vCPUs of the runner.
#
# Unit of work = one (frame, variable) across ALL regions: the frame array is
# read once in the worker (OmFileReader is not picklable), then every region is
# rendered from that same array. The parent keeps only metadata, so the task
# order cannot change the output: workers write the deterministic
# ``<region>/<domain>/<var>/<VALID>.webp`` names and return sizes.
# --------------------------------------------------------------------------- #
DEFAULT_MAX_WORKERS = 4


def default_workers() -> int:
    """Default worker count: min(4, cpu_count) -- the runner has 4 vCPU."""
    return max(1, min(DEFAULT_MAX_WORKERS, os.cpu_count() or 1))


def _crop_spec(crops: dict) -> dict:
    """Picklable crop descriptors (the resampler closure is rebuilt per worker)."""
    spec = {}
    for rname, crop in crops.items():
        spec[rname] = {
            "row0": crop["row0"],
            "row1": crop["row1"],
            "col0": crop["col0"],
            "col1": crop["col1"],
            "grid": crop["grid"],
            "scale": crop["scale"],
            "font_px": int(crop.get("font_px") or 0),
        }
        if crop.get("kind") == "polar":
            spec[rname].update({
                "kind": "polar",
                "disc_size": int(crop["disc_size"]),
                "edge_lat": float(crop["edge_lat"]),
                "max_lat": float(crop["max_lat"]),
            })
    return spec


def _finalise_crop(item: dict) -> dict:
    """Build the unpicklable parts of a crop (resampler + overlay geometry)."""
    if item.get("kind") == "polar":
        size = int(item["disc_size"])
        item["resample"] = build_polar_resampler(item["grid"], size,
                                                 item["edge_lat"])
        # Isolines: SOURCE grid projected vertex by vertex (same cost as the
        # strip). Arrows: 1000x1000 sampled raster with an identity grid.
        item["overlay_grid"] = polar_contour_grid(item["grid"], size,
                                                  item["edge_lat"])
        item["arrow_grid"] = polar_overlay_grid(size)
        item["center"] = (size - 1) * 0.5
        item["radius"] = item["center"]
        # `projection` MUST be the string the client accepts; the geometry is
        # published separately in `projection_meta`.
        item["projection"] = POLAR_PROJECTION
        item["projection_meta"] = polar_projection_meta(size, item["edge_lat"],
                                                        item["max_lat"])
    else:
        item["resample"] = build_row_resampler(item["grid"], item["scale"])
    return item


def _crops_from_spec(spec: dict) -> dict:
    crops = {}
    for rname, crop in spec.items():
        crops[rname] = _finalise_crop(dict(crop))
    return crops


_WORKER_STATE: dict = {}


def _worker_state_init(scales, out_root, domain, run_id, crops, quality,
                       lossless, method):
    """Populate the per-process render state (pool initializer or serial path)."""
    _WORKER_STATE.clear()
    _WORKER_STATE.update({
        "scales": scales,
        "out_root": out_root,
        "domain": domain,
        "run_id": run_id,
        "crops": crops,
        "quality": quality,
        "lossless": lossless,
        "method": method,
        "lookups": {},
        "variants": {},
    })


def _worker_pid(_ignored=None):
    """Pool probe: forces spawn + initializer and returns the worker pid."""
    return os.getpid()


def _worker_state_init_spec(scales, out_root, domain, run_id, crop_spec,
                            quality, lossless, method):
    """Pool initializer: rebuild the (unpicklable) resamplers in the worker."""
    _worker_state_init(scales, out_root, domain, run_id,
                       _crops_from_spec(crop_spec), quality, lossless, method)


def _render_var_task(payload) -> dict:
    """Render every region of one (frame, variable); return write metadata.

    Runs inside a worker process (or in-process when ``--workers 1``). Only
    whole-frame array reads are done here; a read failure is RETURNED (the
    parent prints the same ``! <valid>/<var> read failed`` line the serial
    loop printed) while write/encode errors propagate and abort the domain.
    """
    frame_path, var, valid_time, base_name = payload
    st = _WORKER_STATE
    scales = st["scales"]

    if var not in st["lookups"]:
        st["lookups"][var] = make_lookup(scales, var, "light")
    lk = st["lookups"][var]
    if lk is None:
        return {"var": var, "valid_time": valid_time, "skip": "no_scale"}

    if var not in st["variants"]:
        st["variants"][var] = _scale_variant(scales, var, "light")
    variant = st["variants"][var]
    unit = variant.get("unit") if variant else None

    interval = CONTOUR_INTERVALS.get(var)
    dir_name = ARROW_VARS.get(var)
    speed_ref = _speed_reference(variant) if dir_name else 0.0
    # A CODE (weather_code) is resampled NEAREST-NEIGHBOUR on every axis: the
    # bilinear/2-tap paths are for magnitudes. See CATEGORICAL_VARS.
    categorical = is_categorical(var)
    force_labels = var in {"pressure_msl", "surface_pressure",
                           "geopotential_height_500hPa",
                           "geopotential_height_850hPa",
                           "geopotential_height_1000hPa"}

    try:
        reader = OmFileReader(frame_path)
    except Exception as exc:
        return {"var": var, "valid_time": valid_time,
                "error": "cannot open %s: %s" % (frame_path, exc)}
    try:
        try:
            arr = read_frame_array(reader, var)
        except Exception as exc:
            return {"var": var, "valid_time": valid_time,
                    "error": "%s/%s read failed: %s" % (valid_time, var, exc)}

        # Wind arrows: the direction array has no color scale of its own; it
        # is read explicitly from the SAME frame (one read per direction var,
        # exactly like the per-frame dir_cache of the serial loop).
        dir_arr = None
        dir_error = None
        if dir_name:
            try:
                dir_arr = _unwrap_direction(read_frame_array(reader, dir_name))
            except Exception as exc:
                dir_error = "%s/%s read failed: %s" % (valid_time, dir_name, exc)
    finally:
        try:
            reader.close()
        except Exception:
            pass

    files = []
    for rname, crop in st["crops"].items():
        sub = arr[crop["row0"]:crop["row1"], crop["col0"]:crop["col1"]]
        polar = crop.get("kind") == "polar"
        mask = None
        if polar:
            arr_out, mask = crop["resample"](sub, categorical=categorical)
            overlay_grid = crop["overlay_grid"]
            overlay_scale = 1
            clip_circle = (crop["center"], crop["center"], crop["radius"])
        else:
            arr_out = crop["resample"](sub, categorical=categorical)
            overlay_grid = crop["grid"]
            overlay_scale = crop["scale"]
            clip_circle = None
        rgba = lk(arr_out)
        if mask is not None:
            # Polar disc: HARD circular alpha mask (0 outside the 20N circle).
            rgba = mask_rgba_to_disc(rgba, mask)

        # Isolines (with values) and wind arrows are drawn on a separate RGBA
        # layer and alpha-composited: the base raster is NEVER resampled.
        if interval is not None or dir_arr is not None:
            dir_out = None
            if dir_arr is not None:
                dsub = dir_arr[crop["row0"]:crop["row1"],
                               crop["col0"]:crop["col1"]]
                # The direction is DEGREES (a magnitude): always continuous.
                if polar:
                    dir_out = crop["resample"](dsub)[0]
                else:
                    dir_out = crop["resample"](dsub)
            rgba = _apply_overlay(rgba, _overlay_drawer(
                sub, overlay_grid, overlay_scale,
                variant, interval, [], force_labels=force_labels,
                speed_out=arr_out if dir_out is not None else None,
                dir_out=dir_out, speed_ref=speed_ref,
                font_px=crop.get("font_px"), clip_circle=clip_circle,
                polar=polar, arrow_grid=crop.get("arrow_grid"),
                arrow_scale=1 if polar else None),
                ss=POLAR_OVERLAY_SUPERSAMPLE if polar else None)
            if mask is not None:
                # The overlay (isolines/labels/arrows) is composited on the full
                # square: reapply the disc mask so alpha stays EXACTLY 0 outside
                # the 20N circle. Inside the circle the composite is untouched.
                rgba = mask_rgba_to_disc(rgba, mask)

        vdir = _theme_var_dir(st["out_root"], rname, st["domain"], var, "light",
                              st["run_id"])
        os.makedirs(vdir, exist_ok=True)
        _prune_legacy_theme_dirs(st["out_root"], rname, st["domain"], var)
        wpath = os.path.join(vdir, base_name + ".webp")
        size = save_webp(rgba, wpath, st["quality"], st["lossless"],
                         st["method"])
        files.append({"region": rname, "file": base_name + ".webp",
                      "bytes": size})

    return {"var": var, "valid_time": valid_time, "unit": unit,
            "files": files, "dir_error": dir_error}


def _run_var_tasks(executor, payloads: list):
    """Run one frame's per-variable tasks; return ``(results, executor)``.

    ``executor`` is ``None`` on the serial path. A dead pool (e.g. the runner's
    OOM killer) is reported once and the frame is retried SERIALLY: file names
    are deterministic, so the retry overwrites the same WebPs and the final
    output is identical.
    """
    if executor is None:
        return [_render_var_task(p) for p in payloads], None
    futures = [executor.submit(_render_var_task, p) for p in payloads]
    try:
        return [f.result() for f in futures], executor
    except concurrent.futures.process.BrokenProcessPool as exc:
        print("  ! pool di render morto (%s): degrado a 1 worker" % exc,
              file=sys.stderr)
        executor.shutdown(wait=False, cancel_futures=True)
        return [_render_var_task(p) for p in payloads], None


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
    forced_font = int(getattr(args, "label_font_px", 0) or 0)
    north_projection = str(getattr(args, "north_projection", "strip") or "strip")
    try:
        disc_size = int(getattr(args, "north_disc_size", 1000) or 1000)
    except (TypeError, ValueError):
        disc_size = 1000
    disc_size = max(64, min(disc_size, 4000))
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
        if rname == NORTH_REGION and north_projection == "disc":
            max_lat = (float(cgrid["latMin"])
                       + (int(cgrid["ny"]) - 1) * float(cgrid["dy"]))
            if max_lat < 89.9:
                print("  ! north disc: il dominio arriva a %.3fN, il polo "
                      "(90N) resta trasparente" % max_lat, file=sys.stderr)
            crops[rname] = _finalise_crop({
                "kind": "polar",
                "row0": row0, "row1": row1, "col0": col0, "col1": col1,
                "grid": cgrid,
                "scale": 1,
                "disc_size": disc_size,
                "edge_lat": NORTH_DISC_EDGE_LAT,
                "max_lat": min(90.0, max_lat),
                "font_px": label_font_px(disc_size, disc_size, forced_font,
                                         region=rname),
            })
            continue
        scale = resolve_upsample(args, cgrid["nx"], cgrid["ny"])
        out_w = (int(cgrid["nx"]) - 1) * scale + 1
        out_h = (int(cgrid["ny"]) - 1) * scale + 1
        crops[rname] = _finalise_crop({
            "row0": row0, "row1": row1, "col0": col0, "col1": col1,
            "grid": cgrid,
            "scale": scale,
            "font_px": label_font_px(out_w, out_h, forced_font, region=rname),
        })
    if not crops:
        return 0, 0, "no region intersection", {}, 0, 0

    def _crop_label(name):
        crop = crops[name]
        if crop.get("kind") == "polar":
            return "%s disc %d" % (name, crop["disc_size"])
        return "%s x%d" % (name, crop["scale"])

    print("  [upsample] %s"
          % ", ".join(_crop_label(r) for r in crops))

    # Worker pool (processes). The state is initialised in the parent too so
    # the serial path and the BrokenProcessPool fallback can run the EXACT
    # same task function; the initializer builds it once per worker process.
    workers = max(1, int(getattr(args, "workers", 1) or 1))
    _worker_state_init(scales, out_root, domain, run_id, crops,
                       args.quality, args.lossless, args.method)
    executor = None
    if workers > 1:
        try:
            mp_ctx = multiprocessing.get_context("spawn")
            executor = concurrent.futures.ProcessPoolExecutor(
                max_workers=workers,
                mp_context=mp_ctx,
                initializer=_worker_state_init_spec,
                initargs=(scales, out_root, domain, run_id, _crop_spec(crops),
                          args.quality, args.lossless, args.method),
            )
            # Probe NOW: a host that forbids process creation must degrade to
            # serial here, not blow up at the first frame with work half-done.
            pids = list(executor.map(_worker_pid, range(workers), chunksize=1))
            print("  [workers] %d processi (pid %s)"
                  % (workers, ",".join(str(p) for p in sorted(set(pids)))))
        except Exception as exc:
            print("  ! workers=%d non disponibile (%s): degrado a 1 worker"
                  % (workers, exc), file=sys.stderr)
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
            executor = None
            workers = 1
    if executor is None:
        print("  [workers] 1 (seriale)")

    # Decide variable set: CLI filter, else manifest var_order print-safe.
    var_order = [v for v in (manifest.get("var_order") or []) if isinstance(v, str)]
    requested = args.vars if args.vars else var_order

    # Fingerprint of the palette the pixels are computed with. It goes into EVERY
    # per-variable manifest (so a colour change can never be silent again) and it
    # is the value the incremental path compares before skipping a variable.
    try:
        palette_sha = palette_sha256()
    except OSError as exc:
        print("  ! palette_sha256 non calcolabile (%s): skip incrementale "
              "disabilitato" % exc, file=sys.stderr)
        palette_sha = None

    rendered = 0
    skipped = 0
    already = 0
    frames_done = 0
    bytes_written = 0
    themes = ["light"]
    # rname -> var -> theme -> [frame records]
    var_frames = {rname: {} for rname in crops}
    var_meta = {}                                # var -> unit

    # Incremental state: what the branch list says is already published for THIS
    # (region, var) of THIS run_id, plus the manifest records to carry over.
    # Without --existing-list (or with an empty one) both stay empty and this
    # block does nothing: the render path is the historical one.
    existing_map = getattr(args, "existing_map", None) or {}
    incremental = bool(existing_map)
    declared_palette = _clean_palette_sha(getattr(args, "existing_palette_sha", None))
    var_state: dict = {}
    if incremental:
        reuse_dirs = 0
        for rname in crops:
            if requested:
                vars_here = requested
            else:
                vars_here = sorted({k[3] for k in existing_map
                                    if k[0] == rname and k[1] == domain
                                    and k[2] == run_id})
            for var in vars_here:
                if _scale_variant(scales, var, "light") is None:
                    continue  # nessuna scala: la variabile non e' mai resa
                stt = var_publish_state(
                    _theme_var_dir(out_root, rname, domain, var, "light", run_id),
                    existing_map.get((rname, domain, run_id, var)),
                    palette_sha, declared_palette)
                if not stt["files"] and not stt["frames"]:
                    continue
                var_state[(rname, var)] = stt
                if stt["reuse"]:
                    reuse_dirs += 1
                    if var not in var_meta:
                        variant = _scale_variant(scales, var, "light")
                        if variant is not None:
                            var_meta[var] = variant.get("unit")
                    # Il manifest per-variabile e' l'UNIONE: qui si semina con i
                    # frame gia' pubblicati, poi il render vi aggiunge (e
                    # sovrascrive, a parita' di file) i frame nuovi.
                    var_frames[rname].setdefault(var, {}).setdefault(
                        "light", []).extend(stt["frames"])
        reasons: dict = {}
        for stt in var_state.values():
            if not stt["reuse"]:
                reasons[stt["reason"]] = reasons.get(stt["reason"], 0) + 1
        print("  [incremental] %d/%d directory (regione,var) riusabili, %d file "
              "elencati%s"
              % (reuse_dirs, len(var_state),
                 sum(len(v) for v in existing_map.values()),
                 ("; non riusabili: " + ", ".join("%s=%d" % kv
                                                  for kv in sorted(reasons.items())))
                 if reasons else ""))

    _scale_cache: dict = {}

    def _has_scale(var):
        """True when the palette can color this variable at all (else no file)."""
        if var not in _scale_cache:
            _scale_cache[var] = _scale_variant(scales, var, "light") is not None
        return _scale_cache[var]

    def _published_everywhere(var, base_name):
        """True when EVERY region of this run already has ``<base_name>.webp``.

        The unit of work is one (frame, variable) rendered across ALL regions, so
        the task can be skipped only if every region has it. Half-published is
        rendered again (deterministic bytes, same output, no risk).
        """
        name = base_name + ".webp"
        for _rname in crops:
            stt = var_state.get((_rname, var))
            if not stt or not stt.get("reuse") or name not in stt.get("files", ()):
                return False
        return True

    seen_frames = set()
    watch_complete = not bool(args.watch_manifest)
    try:
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

            # Enumerate the frame's children ONCE in the parent (metadata
            # only): the worker tasks read the arrays themselves because
            # OmFileReader is not picklable.
            try:
                reader = OmFileReader(frame_path)
            except Exception as exc:
                print("  ! cannot open %s: %s" % (frame_path, exc), file=sys.stderr)
                skipped += 1
                continue
            try:
                present = frame_array_names(reader)
            except Exception as exc:
                print("  ! cannot read %s: %s" % (frame_path, exc), file=sys.stderr)
                skipped += 1
                continue
            finally:
                try:
                    reader.close()
                except Exception:
                    pass

            names = [v for v in requested if v in present] if requested else present
            if args.vars:
                missing = [v for v in requested if v not in present]
                if missing:
                    print("  ~ %s: vars absent in frame: %s"
                          % (valid_time, ",".join(missing)), file=sys.stderr)

            base_name = valid_time_to_filename(valid_time)
            todo = names
            if incremental:
                # Gia' pubblicato = tutte le regioni hanno il file per questa
                # variabile: il manifest e' gia' seminato da var_frames, l'array
                # non viene letto e non si scrive nulla. Una variabile SENZA
                # scala non produce alcun file, quindi non puo' impedire lo skip
                # (ne' essere resa): non conta nel decidere se il frame e' finito.
                todo = [v for v in names
                        if not _published_everywhere(v, base_name)]
                if not any(_has_scale(v) for v in todo):
                    already += 1
                    continue
            payloads = [(frame_path, var, valid_time, base_name)
                        for var in todo]
            # One frame at a time (arrival order preserved in --watch-manifest
            # mode); the variables of THAT frame are rendered in parallel.
            results, executor = _run_var_tasks(executor, payloads)
            for res in results:
                var = res["var"]
                if res.get("skip") == "no_scale":
                    print("  ~ no color scale for %r (skip)" % var,
                          file=sys.stderr)
                    continue
                if res.get("error"):
                    print("  ! %s" % res["error"], file=sys.stderr)
                    continue
                if res.get("dir_error"):
                    print("  ! %s" % res["dir_error"], file=sys.stderr)
                if var not in var_meta:
                    var_meta[var] = res.get("unit")
                for rec in res["files"]:
                    rendered += 1
                    bytes_written += rec["bytes"]
                    var_frames[rec["region"]].setdefault(var, {}).setdefault(
                        "light", []).append({
                            "file": rec["file"],
                            "valid_time": valid_time,
                            "bytes": rec["bytes"],
                        })
            frames_done += 1

        if watch_complete:
            break
        time.sleep(0.25)
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
            executor = None

    if not any(var_frames[r] for r in var_frames):
        if incremental and not skipped:
            # Nulla pubblicato e nulla da pubblicare: il dominio ha fatto il suo
            # lavoro (exit 0) ma non c'e' nessun manifest da riscrivere.
            return rendered, skipped, "up-to-date", {}, frames_done, bytes_written
        return rendered, skipped, "nothing rendered", {}, frames_done, bytes_written

    generated_at = _utcnow_iso()

    contrib = {}
    for rname, frames_by_var in var_frames.items():
        if not frames_by_var:
            continue
        cgrid = crops[rname]["grid"]
        cbbox = cropped_bbox(cgrid)
        projection = crops[rname].get("projection")
        projection_meta = crops[rname].get("projection_meta")
        if projection:
            # Polar disc: the image covers the FULL circle. The resampler wraps
            # the 180 deg seam (``np.mod`` on the column), so the geographic
            # extent is -180..180, NOT the last grid column (179.75 on a 0.25
            # deg grid). Publishing 179.75 would describe a 359.75 deg disc and
            # disagree with the region index bbox.
            cbbox = dict(cbbox)
            cbbox["lon_max"] = 180.0
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
            stt = var_state.get((rname, var)) or {}
            known = set(stt.get("files") or ())
            for theme, recs in recs_by_theme.items():
                # UNIONE: i frame gia' elencati dal branch + quelli resi adesso.
                # Riscrivere solo il delta cancellerebbe i valid_times vecchi dal
                # manifest, che e' esattamente il percorso che il client legge.
                merged_recs, keep = merge_frame_records(stt.get("frames"), recs)
                keep |= known
                vdir = _theme_var_dir(out_root, rname, domain, var, theme, run_id)
                prune_published_dir(vdir, keep, incremental, known,
                                    label="%s/%s/%s/%s" % (rname, domain, var, theme))
                shutil.rmtree(os.path.join(vdir, "dark"), ignore_errors=True)
                vmanifest = {
                    "version": WEBP_VERSION,
                    "palette_sha256": palette_sha,
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
                    "frames": merged_recs,
                    "valid_times": [r["valid_time"] for r in merged_recs],
                }
                if projection:
                    vmanifest["projection"] = projection
                if projection_meta:
                    vmanifest["projection_meta"] = projection_meta
                write_json(os.path.join(vdir, "latest.json"), vmanifest)
                sub = "%s/runs/%s/%s" % (domain, run_id, var)
                entry["themes"][theme] = {
                    "count": len(merged_recs),
                    "valid_times": [r["valid_time"] for r in merged_recs],
                    "dir": sub,
                    "manifest": "%s/latest.json" % sub,
                }
            variables[var] = entry
        run_rec = {
            "id": run_id,
            "reference_time": reference_time,
            "generated_at": generated_at,
            "expected_frames": expected_frames,
            "variables": variables,
        }
        if projection:
            run_rec["projection"] = projection
        if projection_meta:
            run_rec["projection_meta"] = projection_meta
        contrib[rname] = {
            "bbox": cbbox,
            "bounds": bounds,
            "grid": grid_summary,
            "reference_time": reference_time,
            "current_run": run_id,
            "runs": [run_rec],
            "variables": variables,
        }
        # Polar disc: the geographic bbox is NOT a Mercator image extent. The
        # client must switch to the dedicated polar path when this key is
        # present (the default strip path never writes it). The value is the
        # STRING 'stereographic' (see POLAR_PROJECTION); the geometry is in
        # `projection_meta`.
        #
        # This index lists ONE run (the one just rendered) and the multi-run
        # index is rebuilt by --index-only, which applies the same rule to the
        # runs it finds on the branch. The domain-level key goes through the
        # same `consensus_projection` used there, so the two writers cannot
        # diverge: a domain whose indexed runs disagree publishes no key.
        dom_projection, dom_projection_meta = consensus_projection([run_rec])
        if dom_projection:
            contrib[rname]["projection"] = dom_projection
        if dom_projection_meta:
            contrib[rname]["projection_meta"] = dom_projection_meta

    if incremental:
        print("  [incremental] %s: %d frame gia' pubblicati (saltati), %d frame "
              "resi, %d WebP scritti" % (domain, already, frames_done, rendered))
        if not rendered:
            return rendered, skipped, "up-to-date", contrib, frames_done, bytes_written
    return rendered, skipped, None, contrib, frames_done, bytes_written


def write_region_index(out_root: str, region_name: str, region_bbox, domains: dict,
                       themes, projection=None, projection_meta=None) -> None:
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
    # The region-level `projection` is only a FALLBACK for the client when the
    # per-domain entry has none -- but `fmRegionProjection` applies it to EVERY
    # domain of the region. Publishing it while some domains are still Mercator
    # strips makes the client draw a 2879x521 strip as if it were a disc
    # (measured in production on 2026-10-26: dwd_icon -> projection
    # 'stereographic' with source 2879x521). So it is written ONLY when every
    # indexed domain declares the SAME projection: `consensus_projection` is the
    # `09f8158b` rule, the same one used for the runs of a domain below.
    agreed, agreed_meta = consensus_projection((domains or {}).values())
    if not projection or agreed != projection:
        projection = None
        projection_meta = None
    elif projection_meta is None:
        projection_meta = agreed_meta
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
    if projection:
        index["projection"] = projection
    if projection_meta:
        index["projection_meta"] = projection_meta
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
    bbox = bounds = grid = projection = projection_meta = None
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
            # `projection` is the STRING the client reads (>= 1.2.4.30); the
            # geometry is under `projection_meta`. A legacy object written under
            # `projection` by the first disc builds is SPLIT here, so an
            # --index-only pass heals it instead of republishing the object.
            if projection is None:
                projection, legacy_meta = projection_parts(vman.get("projection"))
                projection_meta = (projection_meta or vman.get("projection_meta")
                                   or legacy_meta)
            elif projection_meta is None:
                projection_meta = vman.get("projection_meta")
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
    rec = {
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
    if projection:
        rec["projection"] = projection
    if projection_meta:
        rec["projection_meta"] = projection_meta
    return rec


def rebuild_index(in_dir: str, out_dir: str, domain_list, region_names, themes,
                  region_bboxes=None) -> int:
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
        region_bbox = (region_bboxes or REGIONS)[rname]
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
                legacy_projection = None
                legacy_projection_meta = None
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
                    if legacy_projection is None:
                        legacy_projection, legacy_vm_meta = projection_parts(
                            vm.get("projection"))
                        legacy_projection_meta = (legacy_projection_meta
                                                  or vm.get("projection_meta")
                                                  or legacy_vm_meta)
                    elif legacy_projection_meta is None:
                        legacy_projection_meta = vm.get("projection_meta")
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
                    legacy_run = {
                        "id": "legacy-" + _run_id(legacy_ref),
                        "reference_time": legacy_ref,
                        "generated_at": legacy_generated,
                        "expected_frames": legacy_expected or legacy_actual,
                        "frames_available": legacy_actual,
                        "completion_pct": round(100.0 * min(legacy_actual, legacy_expected) / legacy_expected, 1) if legacy_expected else 100.0,
                        "variables": legacy_vars,
                        "bbox": legacy_bbox, "bounds": legacy_bounds, "grid": legacy_grid,
                    }
                    if legacy_projection:
                        legacy_run["projection"] = legacy_projection
                    if legacy_projection_meta:
                        legacy_run["projection_meta"] = legacy_projection_meta
                    runs.append(legacy_run)
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
            # `projection` at DOMAIN level is a fallback the client applies to
            # EVERY run of the domain (and to the frames of the timeline that do
            # not carry their own). Runs of one domain can disagree -- measured
            # in production on 2026-10-26 on ecmwf_ifs025/north: the newest run
            # is the 1000x1000 stereographic disc while the previous one is the
            # 2879x521 Mercator strip. So the key is written ONLY when ALL the
            # indexed runs declare the SAME projection (`09f8158b` rule via
            # `consensus_projection`); otherwise it is omitted and each run
            # keeps its own `projection` in `runs[]`.
            dom_projection, dom_projection_meta = consensus_projection(runs)
            if dom_projection:
                index_domains[domain]["projection"] = dom_projection
            if dom_projection_meta:
                index_domains[domain]["projection_meta"] = dom_projection_meta

        if not index_domains:
            print("[index] %s: NESSUN dominio valido -> index.json NON scritto"
                  % rname)
            continue
        theme_list = [t for t in themes if t in region_themes]
        region_projection = None
        region_projection_meta = None
        for dom_entry in index_domains.values():
            if dom_entry.get("projection"):
                region_projection = dom_entry["projection"]
                region_projection_meta = dom_entry.get("projection_meta")
                break
        if (rname == NORTH_REGION
                and region_projection == POLAR_PROJECTION):
            # The region really reaches the Pole when a disc domain is indexed:
            # REGIONS['north'] is the Mercator strip (85.0511 N) and would
            # contradict the manifests. Keeps --index-only self-consistent
            # WITHOUT passing --north-projection to the index workflow.
            region_bbox = (NORTH_DISC_EDGE_LAT, -180.0, 90.0, 180.0)
        write_region_index(out_dir, rname, region_bbox, index_domains, theme_list,
                           projection=region_projection,
                           projection_meta=region_projection_meta)
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
    ap.add_argument("--workers", type=int, default=0,
                    help="render worker PROCESSES: 0=auto (min(%d, cpu_count)), "
                         "1=serial/historical. One worker renders one "
                         "(frame, variable) across all regions."
                         % DEFAULT_MAX_WORKERS)
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
    ap.add_argument("--existing-list", default=None,
                    help="file with ONE published path per line, relative to the branch "
                         "root (data/fm2-rendered/<region>/<domain>/runs/<run_id>/"
                         "<var>/<VALID>.webp): the (frame, variable) units already "
                         "published for that run are NOT re-rendered. Absent or empty "
                         "file = full render (historical behaviour). The skip also "
                         "requires the per-variable latest.json (palette proof) or "
                         "--existing-palette-sha")
    ap.add_argument("--existing-palette-sha", default=None,
                    help="sha256 of the palette the files in --existing-list were "
                         "rendered with, for a shard that has no per-variable manifest "
                         "(default: read from the manifest; with neither, the skip is "
                         "disabled and everything is rendered again)")
    ap.add_argument("--label-font-px", type=int, default=0,
                    help="force ONE label font size in output pixels (0 = per-region "
                         "auto: the north strip is enlarged so the digits stay >= "
                         "%.0f px in the client pane; 12 = historical bytes)"
                         % MIN_SCREEN_DIGIT_PX)
    ap.add_argument("--north-projection", choices=("strip", "disc"), default="strip",
                    help="projection of the 'north' region: 'strip' = historical Web "
                         "Mercator strip (default, byte-identical to the pre-1.2.4.26 "
                         "output), 'disc' = north-polar stereographic disc with a 20N "
                         "circular mask, published with \"projection\": "
                         "\"stereographic\" (string, what the client reads) plus the "
                         "geometry in projection_meta")
    ap.add_argument("--north-disc-size", type=int, default=1000,
                    help="side in px of the north polar disc (default 1000; clamped "
                         "to 64..4000)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    args.domains = _split_csv(args.domains)
    args.vars = _split_csv(args.vars)

    # Incremental render: the published-path list is read ONCE and handed to every
    # domain as a {(region, domain, run_id, var): {file}} table. Cost measured on
    # a list of 124.992 paths (11,8 MiB of text, the size `git ls-tree -r` prints
    # for the real branch): 0,133 s for the whole parse.
    args.existing_map = {}
    if args.existing_list:
        t0 = time.perf_counter()
        try:
            args.existing_map, ex_stats = load_existing_list(args.existing_list)
        except OSError as exc:
            # FATAL, not a silent fallback: the operator asked for incremental and
            # silently rendering everything would cost the full render (30+ h/day).
            print("FATAL: --existing-list illeggibile (%s): %s"
                  % (args.existing_list, exc), file=sys.stderr)
            return 2
        n_dir = len(args.existing_map)
        n_file = sum(len(v) for v in args.existing_map.values())
        print("[incremental] elenco esistente: %d path in %d directory letti in %.3f s"
              % (n_file, n_dir, time.perf_counter() - t0))
        if ex_stats["ignored"]:
            print("[incremental] ATTENZIONE: %d righe su %d non sono path pubblicati "
                  "e sono state ignorate (es. %s)"
                  % (ex_stats["ignored"], ex_stats["lines"],
                     "; ".join(ex_stats["sample"][:3])), file=sys.stderr)
        if not n_dir:
            print("[incremental] elenco vuoto: nessun frame risulta pubblicato, "
                  "render completo")

    # 0 = auto (4 on the 4-vCPU runner, 1 on a single-core host); hard cap 32
    # so a typo cannot spawn hundreds of processes. --index-only ignores it.
    if args.workers is None or int(args.workers) <= 0:
        args.workers = default_workers()
    args.workers = max(1, min(int(args.workers), 32))

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

    # The polar disc spans the real data up to 90 N (the engine bbox reaches the
    # Pole); the Mercator strip stops at 85.0511 N by definition. The same bbox
    # is used by the renderer, the region index and --index-only.
    region_bboxes = dict(regions)
    if args.north_projection == "disc" and NORTH_REGION in region_bboxes:
        region_bboxes[NORTH_REGION] = (NORTH_DISC_EDGE_LAT, -180.0, 90.0, 180.0)

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
        written = rebuild_index(in_dir, out_dir, args.domains, region_names, themes,
                                region_bboxes=region_bboxes)
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
    up_to_date = False
    index_domains = {rname: {} for rname in regions}

    for ddir in domains:
        domain = os.path.basename(os.path.normpath(ddir))
        print("[domain] %s" % domain)
        t0 = time.perf_counter()
        try:
            ok, skipped, err, contrib, nframes, nbytes = render_domain(
                ddir, out_dir, scales, args, region_bboxes)
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
        elif err == "up-to-date":
            # Incrementale: nessun frame nuovo, ma i manifest sono stati
            # riscritti in unione e contrib e' valido -> il dominio ha lavorato.
            any_domain_ok = True
            up_to_date = True
            print("  up-to-date (0 frame nuovi: WebP gia' pubblicati)")
        else:
            print("  ok=0 skipped=%d (%s)" % (skipped, err or "empty"))
        for rname, entry in contrib.items():
            index_domains[rname][domain] = entry

    if total_ok > 0 or up_to_date:
        for rname in regions:
            region_projection = None
            region_projection_meta = None
            for dom_entry in index_domains[rname].values():
                if dom_entry.get("projection"):
                    region_projection = dom_entry["projection"]
                    region_projection_meta = dom_entry.get("projection_meta")
                    break
            write_region_index(out_dir, rname, region_bboxes[rname],
                               index_domains[rname], args.themes,
                               projection=region_projection,
                               projection_meta=region_projection_meta)

        # Publish the exact color scales the renderer used so the client legend and
        # the raster agree (single source of truth in the cache root).
        copy_color_scales(out_dir)

    print("[done] frames written: %d -> %s" % (total_ok, out_dir))
    return 0 if any_domain_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
