#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Campionamento volumetrico COMPATTO ispirato a Iride (solo il campionamento).

Modulo volutamente indipendente: NON importa config.py, NON importa
volume_publisher.py e NON tocca main.py. Lavora su array CAPPI gia' in memoria.

Cio' che e' stato preso da Iride (backend/dpc_volume.py) e' ESCLUSIVAMENTE la
rappresentazione compatta:

  * SIDE = 32 bin orizzontali per lato (cubo per cella = 32 x 32 x 10 uint8);
  * codifica uint8: 0 = missing/fuori copertura, 1 = in copertura senza eco,
    2..246 = dBZ quantizzato con  code = (dBZ + 32) * 2 + 2
            (passo 0,5 dBZ, intervallo -32 .. +90 dBZ);
  * reduction per bin = MASSIMO VALIDO (max pooling); precedenza:
    dBZ valido > "no echo" (1) > "missing" (0);
  * nessuna interpolazione, nessuna distance transform, nessun marching
    squares lato server (queste restano, invariate, al volume pieno).

Cio' che NON e' stato preso: la semantica. In particolare NON viene adottato
``floor = cube.max(axis=0)`` come misura fisica: e' un artefatto visivo del
renderer WebGL2 di Iride, non un echo top ne' una quota fisica. I parametri
scientifici restano quelli del volume pieno (stesse formule, stesso
null_reason, stessi 5 threshold): il cubo e' una RAPPRESENTAZIONE compatta
dello stesso campo, con area di bin dichiarata (``bin_km``) usata per riportare
CELL_VOLUME a conteggio di voxel 1 km^2.

La finestra di campionamento e' ESATTAMENTE il sotto-volume usato oggi dal
publisher (bbox della cella sulla VMI + pad), clippato alla griglia e poi
imbottito di "missing" fino a un quadrato di lato ``side * pool`` dov''e'
``pool = ceil(max(altezza, larghezza) / side)``. Con ``pool == 1`` (celle
contenute in 32 km) il cubo e' una coppia 1:1 dei voxel del sotto-volume e i
parametri sono BIT-IDENTICI a quelli della griglia piena (a parte la quantizzazione
a 0,5 dBZ, che e' la risoluzione nativa dei prodotti DPC). Con ``pool > 1`` il
max pooling e' dichiarato e l'area di bin e' ``pool**2`` km^2.
"""
from __future__ import annotations

import base64
import math

import numpy as np

SIDE_DEFAULT = 32

# Codifica Iride, identica al backend dpc_volume.py
ENC_MISSING = 0            # -9999 (fuori copertura) oppure NaN
ENC_NO_ECHO = 1            # -9998 (in copertura, senza eco)
ENC_DBZ_FIRST = 2          # -32.0 dBZ
ENC_DBZ_LAST = 246         # +90.0 dBZ
DBZ_MIN = -32.0
DBZ_MAX = 90.0
DBZ_STEP = 0.5

SAMPLING = "maximum valid reflectivity within horizontal bins"
REDUCTION = ("max over valid codes in each horizontal pool x pool bin; "
             "precedence: valid dBZ > no-echo(1) > missing(0)")
REF_IRIDE = ("campionamento compatto ispirato a Iride (dpc_volume.py): "
             "SIDE=32, uint8 0/1/2..246, code=(dBZ+32)*2+2, max valido per bin")
REF_NOT_FLOOR = ("NON si adotta floor=cube.max(axis=0) come misura fisica: "
                 "artefatto visivo del renderer WebGL2 di Iride, non echo top")


def encode_dbz(dbz):
    """dBZ -> codice uint8 Iride. Valori fuori intervallo vengono saturi.

    Verifica: -32 -> 2, 15 -> 96, +90 -> 246.
    """
    a = np.asarray(dbz, dtype=np.float64)
    code = np.rint((a - DBZ_MIN) / DBZ_STEP) + ENC_DBZ_FIRST
    code = np.clip(code, ENC_DBZ_FIRST, ENC_DBZ_LAST)
    return code.astype(np.uint8)


def decode_code(code):
    """codice uint8 Iride -> dBZ float32; 0 e 1 diventano NaN."""
    c = np.asarray(code, dtype=np.int16)
    out = np.full(c.shape, np.nan, dtype=np.float32)
    ok = c >= ENC_DBZ_FIRST
    out[ok] = ((c[ok].astype(np.float64) - ENC_DBZ_FIRST) * DBZ_STEP + DBZ_MIN).astype(np.float32)
    return out


def encode_cube_to_b64(cube):
    """Cubo (10, S, S) uint8 -> stringa base64 (row-major, C order)."""
    a = np.ascontiguousarray(cube, dtype=np.uint8)
    return base64.b64encode(a.tobytes()).decode("ascii")


def decode_b64_to_cube(text, side, n_levels=10):
    """Inverso di encode_cube_to_b64."""
    raw = base64.b64decode(text)
    return np.frombuffer(raw, dtype=np.uint8).reshape(n_levels, side, side)


def _level_array(cappi, level):
    """RasterData -> ndarray 2D da un dizionario {quota: RasterData}."""
    if not isinstance(cappi, dict):
        return None
    rd = cappi.get(level)
    if rd is None:
        rd = cappi.get(float(level))
    if rd is None:
        return None
    arr = getattr(rd, "data", rd)
    try:
        return np.asarray(arr, dtype=np.float32)
    except (TypeError, ValueError):
        return None


def _build_codes(window_levels, side, pool):
    """(10, win, win) float32 con sentinelle -> (10, side, side) uint8."""
    win = int(side) * int(pool)
    codes = np.full((10, win, win), ENC_MISSING, dtype=np.uint8)
    a = np.asarray(window_levels, dtype=np.float32)
    # in-coverage senza eco: -9998
    codes[a == -9998.0] = ENC_NO_ECHO
    # dBZ valido: massimo della codifica (monotona) = massimo dBZ valido
    valid = np.isfinite(a) & (a >= DBZ_MIN) & (a <= DBZ_MAX)
    codes[valid] = encode_dbz(a[valid])
    if int(pool) > 1:
        codes = codes.reshape(10, side, pool, side, pool).max(axis=(2, 4))
    return codes.astype(np.uint8)


def build_compact_cube(cappi=None, volume=None, *,
                       r0, r1, c0, c1, side=SIDE_DEFAULT, pixel_km=1.0):
    """Costruisce il cubo compatto per UNA cella.

    Parametri
    ---------
    cappi   : dict {1..10: RasterData} con i CAPPI GREZZI (sentinelle -9999/-9998
              intatte). Se fornito, e' la sorgente preferita e distingue 0 da 1.
    volume  : ndarray (10, H, W) float32 gia' mascherato a NaN (forma alternativa
              per chi ha solo il volume in memoria: in questo caso -9999 e -9998
              sono entrambi confluiti in NaN e codificati 0 -- dichiarato).
    r0,r1,c0,c1 : finestra [r0,r1) x [c0,c1) in coordinate griglia piena,
              gia' clippata (stesso _subvolume del publisher, pad incluso).
    side    : lato del cubo (32 di default; 64 per rimisurare).
    pixel_km: passo della griglia DPC (1 km).

    Ritorna un dict con `cube` (10, side, side) uint8 e i metadati geometrici.
    """
    side = int(side)
    if side <= 0:
        raise ValueError("side deve essere positivo")
    h, w = int(r1 - r0), int(c1 - c0)
    if h <= 0 or w <= 0:
        raise ValueError("finestra vuota")
    pool = max(1, int(math.ceil(max(h, w) / float(side))))
    win = side * pool
    pr = (win - h) // 2
    pc = (win - w) // 2

    vol_arr = np.asarray(volume, dtype=np.float32) if volume is not None else None
    if vol_arr is not None and (vol_arr.ndim != 3 or vol_arr.shape[0] != 10):
        vol_arr = None

    levels = np.full((10, win, win), np.nan, dtype=np.float32)
    for k in range(1, 11):
        arr = _level_array(cappi, k) if cappi is not None else None
        if arr is None and vol_arr is not None:
            arr = vol_arr[k - 1]
        if arr is None or arr.ndim != 2:
            continue
        # la griglia di riferimento e' quella del volume (o del primo CAPPI)
        if r1 > arr.shape[0] or c1 > arr.shape[1]:
            continue
        try:
            levels[k - 1, pr:pr + h, pc:pc + w] = arr[r0:r1, c0:c1]
        except (ValueError, IndexError):
            continue

    cube = _build_codes(levels, side, pool)
    return {
        "cube": cube,
        "side": side,
        "pool": pool,
        "win": win,
        "offset_rc": (pr, pc),
        "window_full_rc": (int(r0), int(r1), int(c0), int(c1)),
        "sub_shape_hw": (h, w),
        "bin_km": float(pixel_km) * float(pool),
        "sampling": SAMPLING,
        "reduction": REDUCTION,
        "reference": REF_IRIDE + "; " + REF_NOT_FLOOR,
    }


def cell_mask_in_cube(labeled, label_id, meta):
    """Maschera (side, side) dei bin che contengono la cella `label_id`."""
    side = int(meta["side"])
    pool = int(meta["pool"])
    win = int(meta["win"])
    pr, pc = meta["offset_rc"]
    r0, r1, c0, c1 = meta["window_full_rc"]
    h, w = int(r1 - r0), int(c1 - c0)
    if labeled is None:
        return np.zeros((side, side), dtype=bool)
    region = np.asarray(labeled[r0:r1, c0:c1]) == int(label_id)
    full = np.zeros((win, win), dtype=bool)
    full[pr:pr + h, pc:pc + w] = region
    if pool == 1:
        return full
    return full.reshape(side, pool, side, pool).any(axis=(1, 3))
