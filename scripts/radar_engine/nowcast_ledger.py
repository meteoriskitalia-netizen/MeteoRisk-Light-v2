#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — nowcast_ledger.py (novembre 2026)

LEDGER COMPATTO DELLA VERIFICA DEL NOWCAST (schema `meteorisk.nowcast_ledger/1`).

Perche' esiste
--------------
`data/radar/nowcast_verification.json` conserva 7 giorni di righe (CSI/POD/FAR
per slot, lead, soglia) e viene riscritto: e' una serie OPERATIVA, non un
archivio. Per una validazione statistica servono anni di coppie
(previsto, osservato), ma salvare i raster costerebbe ~20 MB per slot
(~5,8 GB/giorno). Il ledger conserva invece un RIASSUNTO FEDELE e compatto:

- **istogramma congiunto 8x8** di (previsto, osservato) sui pixel del dominio,
  con bordi `HIST_EDGES = [0, 0.1, 0.5, 1, 2, 5, 10, 20, inf]` mm/h;
- **contatori** hits/misses/false_alarms/correct_negatives per (lead, soglia);
- **FSS** (fraction skill score, Roberts & Lean 2008) per (lead, soglia,
  intorno 9/25/49 px).

Perche' l'istogramma e le soglie
--------------------------------
CSI/POD/FAR a una soglia T si ricavano dall'istogramma congiunto SOLO se T e'
un bordo esatto. Con bordi diversi da 0,5/2/5 lo scarto misurato sul CSI e'
0,29-0,65: un numero che sembra una verifica e non lo e'. Per questo
`HIST_EDGES` contiene 0,5, 2 e 5 come bordi, e per una soglia NON di bordo
`contingency_from_hist()` restituisce un INTERVALLO (min/max sui vertici
ammissibili della massa del bin che contiene T), non un valore puntuale.

Il FSS non e' ricostruibile dall'istogramma (dipende dalla disposizione
spaziale, non solo dalla distribuzione congiunta): viene calcolato al momento
dell'ingestione e archiviato.

Formato del record (little-endian, 1102 byte, nessun padding)
-------------------------------------------------------------
::

    header   28 B  <4sHHQQHH
                   magic "NLGR" | version u16 | flags u16
                   slot_ms u64 | generated_ms u64 | grid_w u16 | grid_h u16
    lead     54 B  3 x <HIIII (18 B): lead_min u16 | valid_s u32
                   domain_pixels u32 | n_obs_rain u32 | flags u32
    counters 144 B 3 lead x 3 soglie x 4 u32 (hits, misses, fa, cn)
    FSS      108 B 3 lead x 3 soglie x 3 intorni float32 (NaN = non definito)
    hist     768 B 3 lead x 64 bin u32 (riga = bin previsto, colonna = osservato)

`flags` dell'header: bit0 = licenza CC BY-SA 4.0, bit1 = fonte Radar-DPC.
`flags` del lead: bit0 = lead presente nell'archivio, bit1 = lead calcolato
(se bit1 = 0 i contatori/FSS/istogramma di quel lead sono zero e non
significano "nessun evento").

Archivio
--------
Un file per giorno UTC, compresso (zstd se `zstandard` e' installato,
altrimenti gzip), sotto il root passato:

    <root>/YYYY/MM/DD.bin.zst     (oppure DD.bin.gz)

Nessuna potatura: il ledger e' l'archivio lungo. Misurato: 27,3 MB/anno con
zstd, 34,4 MB/anno con gzip alla cadenza di 288 slot/giorno.

Il modulo fa SOLO filesystem: non esegue git. La pubblicazione sul branch
ORPHAN `nowcast-ledger` (commit senza parent + `--force-with-lease`) e' nello
step del workflow `radar-engine.yml`.

Attribuzione
------------
I dati di partenza sono il prodotto SRI del Radar-DPC (Dipartimento della
Protezione Civile), licenza CC BY-SA 4.0. Il ledger e' un'opera derivata:
`LICENCE` e i flag dell'header lo dichiarano, `ensure_docs()` scrive
README.md e LICENCE.md alla radice del branch.
"""

import argparse
import datetime as _dt
import json
import os
import struct
import sys
import tempfile
import time

import numpy as np

SCHEMA = "meteorisk.nowcast_ledger/1"
MAGIC = b"NLGR"
VERSION = 1

HEADER_BYTES = 28
LEAD_BYTES = 54
COUNTER_BYTES = 144
FSS_BYTES = 108
HIST_BYTES = 768
RECORD_BYTES = (HEADER_BYTES + LEAD_BYTES + COUNTER_BYTES + FSS_BYTES
                + HIST_BYTES)                       # 1102

LEADS = (15, 30, 60)
THRESHOLDS = (0.5, 2.0, 5.0)
NEIGHBORHOODS = (9, 25, 49)

# Bordi OBBLIGATORI: 0.5, 2 e 5 devono essere bordi esatti, altrimenti il CSI
# ricostruito dall'istogramma sbaglia di 0,29-0,65 (misurato).
HIST_EDGES = (0.0, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, float("inf"))
HIST_BINS = 8
_FINITE_EDGES = np.asarray(HIST_EDGES[:-1], dtype=np.float64)

FLAG_LICENSE_CC_BY_SA = 0x0001
FLAG_SOURCE_RADAR_DPC = 0x0002
HEADER_FLAGS_DEFAULT = FLAG_LICENSE_CC_BY_SA | FLAG_SOURCE_RADAR_DPC

LEAD_FLAG_PRESENT = 0x0001
LEAD_FLAG_COMPUTED = 0x0002

LICENSE_NAME = "CC BY-SA 4.0"
SOURCE_NAME = "Radar-DPC (Dipartimento della Protezione Civile)"
SCHEMA_NAME = SCHEMA

_HEADER = struct.Struct("<4sHHQQHH")
_LEAD = struct.Struct("<HIIII")
_COUNTERS = struct.Struct("<" + "I" * (len(LEADS) * len(THRESHOLDS) * 4))
_FSS = struct.Struct("<" + "f" * (len(LEADS) * len(THRESHOLDS)
                                  * len(NEIGHBORHOODS)))
_HIST = struct.Struct("<" + "I" * (len(LEADS) * HIST_BINS * HIST_BINS))

assert _HEADER.size == HEADER_BYTES
assert _LEAD.size == 18 and _LEAD.size * len(LEADS) == LEAD_BYTES
assert _COUNTERS.size == COUNTER_BYTES
assert _FSS.size == FSS_BYTES
assert _HIST.size == HIST_BYTES

_ISO = "%Y-%m-%dT%H:%M:%SZ"
_UTC = _dt.timezone.utc


# ---------------------------------------------------------------------------
# utilita' di tempo
# ---------------------------------------------------------------------------
def iso_from_ms(ms):
    return _dt.datetime.fromtimestamp(int(ms) / 1000.0, _UTC).strftime(_ISO)


def parse_iso_ms(value):
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    txt = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
    dt = _dt.datetime.fromisoformat(txt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return int(dt.timestamp() * 1000)


def day_of_slot_ms(slot_ms):
    return _dt.datetime.fromtimestamp(int(slot_ms) / 1000.0, _UTC).date()


# ---------------------------------------------------------------------------
# istogramma congiunto e contingenza
# ---------------------------------------------------------------------------
def bin_index(values):
    """Indice di bin 0..7 per ogni valore (mm/h), bordi HIST_EDGES."""
    v = np.asarray(values, dtype=np.float64)
    idx = np.searchsorted(_FINITE_EDGES, v, side="right") - 1
    return np.clip(idx, 0, HIST_BINS - 1).astype(np.intp)


def joint_histogram(pred, obs, domain):
    """8x8 uint32: riga = bin del previsto, colonna = bin dell'osservato."""
    ok = (np.isfinite(pred) & np.isfinite(obs)
          & np.asarray(domain, dtype=bool))
    flat = (bin_index(np.asarray(pred)[ok]) * HIST_BINS
            + bin_index(np.asarray(obs)[ok]))
    counts = np.bincount(flat, minlength=HIST_BINS * HIST_BINS)
    return counts.astype(np.uint32).reshape(HIST_BINS, HIST_BINS)


def threshold_edge_index(threshold):
    """Indice del bordo se `threshold` e' un bordo esatto, altrimenti None."""
    t = float(threshold)
    for i, edge in enumerate(HIST_EDGES):
        if np.isfinite(edge) and float(edge) == t:
            return i
    return None


def scores_from_counts(counts):
    """CSI/POD/FAR dalla quaterna; None se il denominatore e' 0.

    Stessa formula di `nowcast_field.scores` (non importato: il ledger resta
    autonomo e testabile senza il motore)."""
    hits, misses, fa, cn = [int(x) for x in counts]
    pod = (hits / float(hits + misses)) if (hits + misses) else None
    far = (fa / float(hits + fa)) if (hits + fa) else None
    csi = (hits / float(hits + misses + fa)) if (hits + misses + fa) else None
    return {"csi": csi, "pod": pod, "far": far,
            "hits": hits, "misses": misses, "false_alarms": fa,
            "correct_negatives": cn}


def _counts_with_cut(hist, pred_cut, obs_cut):
    """Contingenza esatta assumendo: previsto >= T sse bin >= pred_cut."""
    h = np.asarray(hist, dtype=np.int64)
    hits = int(h[pred_cut:, obs_cut:].sum())
    misses = int(h[:pred_cut, obs_cut:].sum())
    fa = int(h[pred_cut:, :obs_cut].sum())
    cn = int(h[:pred_cut, :obs_cut].sum())
    return (hits, misses, fa, cn)


def contingency_from_hist(hist, threshold):
    """Contingenza (e punteggi) ricostruita dall'istogramma congiunto.

    - soglia su un BORDO: conteggi esatti (`exact: true`);
    - soglia DENTRO un bin: intervallo [min, max] sui 4 vertici ammissibili
      (massa del bin tutta sotto o tutta sopra, per previsto e osservato);
      `counts` resta None e `scores` resta None: nessun numero finto.
    """
    t = float(threshold)
    if t < 0:
        raise ValueError("threshold_negative:%r" % (threshold,))
    edge = threshold_edge_index(t)
    if edge is not None:
        counts = _counts_with_cut(hist, edge, edge)
        return {
            "threshold_mmh": t,
            "exact": True,
            "bin": edge - 1 if edge > 0 else None,
            "counts": counts,
            "scores": scores_from_counts(counts),
            "bounds": None,
            "score_bounds": None,
        }

    # bin che contiene T: _FINITE_EDGES[k] <= T < _FINITE_EDGES[k+1]
    k = int(np.searchsorted(_FINITE_EDGES, t, side="right")) - 1
    k = max(0, min(k, HIST_BINS - 1))
    corners = [c for c in (
        _counts_with_cut(hist, k, k),
        _counts_with_cut(hist, k, k + 1),
        _counts_with_cut(hist, k + 1, k),
        _counts_with_cut(hist, k + 1, k + 1),
    )]
    bounds = {
        name: [min(c[i] for c in corners), max(c[i] for c in corners)]
        for i, name in enumerate(("hits", "misses", "false_alarms",
                                  "correct_negatives"))
    }
    score_corners = [scores_from_counts(c) for c in corners]

    def _bound(key):
        vals = [s[key] for s in score_corners if s[key] is not None]
        return [min(vals), max(vals)] if vals else None

    return {
        "threshold_mmh": t,
        "exact": False,
        "bin": k,
        "bin_range_mmh": [float(HIST_EDGES[k]), float(HIST_EDGES[k + 1])],
        "counts": None,
        "scores": None,
        "bounds": bounds,
        "score_bounds": {"csi": _bound("csi"), "pod": _bound("pod"),
                         "far": _bound("far")},
    }


def sweep_thresholds(hist, thresholds=None):
    """Una voce per ogni soglia (default: i 7 bordi finiti dell'istogramma)."""
    if thresholds is None:
        thresholds = HIST_EDGES[1:-1]
    return [contingency_from_hist(hist, t) for t in thresholds]


# ---------------------------------------------------------------------------
# FSS (Roberts & Lean 2008) su raster, intorni quadrati
# ---------------------------------------------------------------------------
def _box_mean_numpy(values, side):
    """Fallback senza scipy: media su finestra side x side centrata."""
    a = np.asarray(values, dtype=np.float64)
    h = int(side) // 2
    p = np.pad(a, h, mode="constant", constant_values=0.0)
    c = np.cumsum(np.cumsum(p, axis=0), axis=1)
    c = np.pad(c, ((1, 0), (1, 0)), mode="constant", constant_values=0.0)
    s = (c[side:, side:] - c[:-side, side:]
         - c[side:, :-side] + c[:-side, :-side])
    return s / float(int(side) * int(side))


def _box_mean(values, side):
    """Media su finestra side x side: `uniform_filter` (scipy, stessa
    definizione del backtest della cascata) con fallback numpy."""
    try:
        from scipy import ndimage
        return ndimage.uniform_filter(np.asarray(values, dtype=np.float32),
                                      size=int(side), mode="constant",
                                      cval=0.0)
    except ImportError:
        return _box_mean_numpy(values, side)


def _fractions_from_den(binary, valid, side, den):
    """Frazione sopra soglia dato il conteggio dei pixel validi nell'intorno."""
    s2 = float(int(side) * int(side))
    num = _box_mean(np.asarray(binary, dtype=np.float32)
                    * np.asarray(valid, dtype=np.float32), side) * s2
    ok = den >= s2 - 1e-3
    frac = np.zeros_like(num)
    np.divide(num, den, out=frac, where=den > 0.5)
    return frac, ok


def neighbourhood_fractions(binary, valid, side):
    """Frazione sopra soglia in ogni intorno; `ok` = finestra piena e dentro
    il dominio (nessun intorno troncato), come il backtest della cascata."""
    s2 = float(int(side) * int(side))
    den = _box_mean(np.asarray(valid, dtype=np.float32), side) * s2
    return _fractions_from_den(binary, valid, side, den)


def _fss(fp, fo, ok):
    if not bool(ok.any()):
        return None
    d = (fp - fo)[ok]
    mse = float(np.mean(d * d))
    ref = float(np.mean((fp[ok] ** 2) + (fo[ok] ** 2)))
    if ref <= 0.0:
        return None
    return 1.0 - mse / ref


def fss_from_raster(pred_binary, obs_binary, valid, side):
    """FSS su maschere binarie gia' sogliate. None se non definito."""
    s2 = float(int(side) * int(side))
    den = _box_mean(np.asarray(valid, dtype=np.float32), side) * s2
    fp, okp = _fractions_from_den(pred_binary, valid, side, den)
    fo, oko = _fractions_from_den(obs_binary, valid, side, den)
    return _fss(fp, fo, okp & oko)


def fss_block(pred, obs, domain):
    """{soglia: {intorno: fss}} per le 3 soglie x 3 intorni.

    `den` (pixel validi nell'intorno) dipende solo da (dominio, intorno): si
    calcola 3 volte per lead, non 9, e `ok` e' lo stesso per tutte le soglie.
    """
    v = np.asarray(domain, dtype=np.float32)
    pred = np.asarray(pred)
    obs = np.asarray(obs)
    den_by_side = {}
    ok_by_side = {}
    for side in NEIGHBORHOODS:
        s2 = float(int(side) * int(side))
        den = _box_mean(v, side) * s2
        den_by_side[side] = den
        ok_by_side[side] = den >= s2 - 1e-3
    out = {}
    for t in THRESHOLDS:
        pb = pred >= t
        ob = obs >= t
        out[t] = {}
        for side in NEIGHBORHOODS:
            den = den_by_side[side]
            fp, okp = _fractions_from_den(pb, v, side, den)
            fo, oko = _fractions_from_den(ob, v, side, den)
            out[t][side] = _fss(fp, fo, okp & oko)
    return out


# ---------------------------------------------------------------------------
# record: costruzione, pack, unpack
# ---------------------------------------------------------------------------
def empty_record(slot_ms, generated_ms=None, grid_w=0, grid_h=0):
    record = {
        "magic": MAGIC.decode("ascii"),
        "version": VERSION,
        "flags": HEADER_FLAGS_DEFAULT,
        "slot_ms": int(slot_ms),
        "generated_ms": int(generated_ms if generated_ms is not None
                            else round(time.time() * 1000)),
        "grid_w": int(grid_w),
        "grid_h": int(grid_h),
        "leads": {},
        "counters": {},
        "fss": {},
        "hist": {},
    }
    for lead in LEADS:
        record["leads"][lead] = {"lead_min": lead, "valid_s": 0,
                                 "domain_pixels": 0, "n_obs_rain": 0,
                                 "flags": 0, "present": False,
                                 "computed": False}
        record["counters"][lead] = {t: (0, 0, 0, 0) for t in THRESHOLDS}
        record["fss"][lead] = {t: {s: float("nan") for s in NEIGHBORHOODS}
                               for t in THRESHOLDS}
        record["hist"][lead] = np.zeros((HIST_BINS, HIST_BINS),
                                        dtype=np.uint32)
    return record


def add_lead(record, lead, pred, obs, domain, valid_ms,
             min_rain_pixels=0):
    """Calcola e inserisce un lead nel record (istogramma + contatori + FSS)."""
    lead = int(lead)
    if lead not in LEADS:
        raise ValueError("lead_not_in_ledger:%r" % (lead,))
    hist = joint_histogram(pred, obs, domain)
    ok = (np.isfinite(pred) & np.isfinite(obs)
          & np.asarray(domain, dtype=bool))
    n_obs_rain = int(np.sum(np.asarray(obs)[ok] >= 0.5))
    header = record["leads"][lead]
    header.update({
        "lead_min": lead,
        "valid_s": int(int(valid_ms) // 1000),
        "domain_pixels": int(ok.sum()),
        "n_obs_rain": n_obs_rain,
        "flags": LEAD_FLAG_PRESENT | LEAD_FLAG_COMPUTED,
        "present": True,
        "computed": True,
    })
    record["hist"][lead] = hist
    for t in THRESHOLDS:
        record["counters"][lead][t] = _counts_with_cut(
            hist, threshold_edge_index(t), threshold_edge_index(t))
    record["fss"][lead] = fss_block(pred, obs, domain)
    return record


def build_record(slot_ms, preds, obss, domains, valid_ms,
                 generated_ms=None, grid=None):
    """Record completo da {lead: array}. Un lead assente resta non presente."""
    first = next(iter(preds.values()), None)
    if grid is None:
        if first is None:
            grid = (0, 0)
        else:
            h, w = np.asarray(first).shape[:2]
            grid = (int(w), int(h))
    record = empty_record(slot_ms, generated_ms=generated_ms,
                          grid_w=grid[0], grid_h=grid[1])
    for lead in LEADS:
        if lead not in preds:
            continue
        add_lead(record, lead, preds[lead], obss[lead], domains[lead],
                 valid_ms[lead])
    return record


def pack(record):
    """record -> 1102 byte little-endian."""
    header = _HEADER.pack(MAGIC, VERSION, int(record["flags"]),
                          int(record["slot_ms"]), int(record["generated_ms"]),
                          int(record["grid_w"]), int(record["grid_h"]))
    leads = b""
    counters = []
    fss = []
    hists = []
    for lead in LEADS:
        h = record["leads"][lead]
        leads += _LEAD.pack(int(h["lead_min"]), int(h["valid_s"]),
                            int(h["domain_pixels"]), int(h["n_obs_rain"]),
                            int(h["flags"]))
        for t in THRESHOLDS:
            counters.extend(int(x) for x in record["counters"][lead][t])
        for t in THRESHOLDS:
            for side in NEIGHBORHOODS:
                v = record["fss"][lead][t][side]
                fss.append(float("nan") if v is None else float(v))
        hist = np.asarray(record["hist"][lead], dtype=np.uint32)
        if hist.shape != (HIST_BINS, HIST_BINS):
            raise ValueError("hist_shape:%r" % (hist.shape,))
        hists.extend(int(x) for x in hist.ravel())
    blob = (header + leads + _COUNTERS.pack(*counters)
            + _FSS.pack(*fss) + _HIST.pack(*hists))
    if len(blob) != RECORD_BYTES:
        raise AssertionError("record_bytes:%d" % len(blob))
    return blob


def unpack(blob):
    """1102 byte -> record (istogrammi np.ndarray uint32)."""
    blob = bytes(blob)
    if len(blob) != RECORD_BYTES:
        raise ValueError("record_size:%d_expected_%d" % (len(blob),
                                                         RECORD_BYTES))
    off = 0
    magic, version, flags, slot_ms, gen_ms, gw, gh = _HEADER.unpack_from(
        blob, off)
    off += HEADER_BYTES
    if magic != MAGIC:
        raise ValueError("bad_magic:%r" % (magic,))
    if version != VERSION:
        raise ValueError("bad_version:%d" % version)
    record = {
        "magic": "NLGR", "version": version, "flags": flags,
        "slot_ms": int(slot_ms), "generated_ms": int(gen_ms),
        "grid_w": int(gw), "grid_h": int(gh),
        "license": (LICENSE_NAME if flags & FLAG_LICENSE_CC_BY_SA else None),
        "source": (SOURCE_NAME if flags & FLAG_SOURCE_RADAR_DPC else None),
        "leads": {}, "counters": {}, "fss": {}, "hist": {},
    }
    lead_headers = []
    for _i in range(len(LEADS)):
        lead_headers.append(_LEAD.unpack_from(blob, off))
        off += _LEAD.size
    counters = _COUNTERS.unpack_from(blob, off)
    off += COUNTER_BYTES
    fss_vals = _FSS.unpack_from(blob, off)
    off += FSS_BYTES
    hist_vals = _HIST.unpack_from(blob, off)
    off += HIST_BYTES
    for i, (lead_min, valid_s, dom_px, obs_rain, lflags) in enumerate(
            lead_headers):
        lead = int(lead_min)
        if lead != LEADS[i]:
            raise ValueError("lead_order:%d_at_%d" % (lead, i))
        record["leads"][lead] = {
            "lead_min": lead, "valid_s": int(valid_s),
            "domain_pixels": int(dom_px), "n_obs_rain": int(obs_rain),
            "flags": int(lflags),
            "present": bool(lflags & LEAD_FLAG_PRESENT),
            "computed": bool(lflags & LEAD_FLAG_COMPUTED),
        }
        record["counters"][lead] = {}
        for j, t in enumerate(THRESHOLDS):
            k = (i * len(THRESHOLDS) + j) * 4
            record["counters"][lead][t] = tuple(
                int(x) for x in counters[k:k + 4])
        record["fss"][lead] = {}
        for j, t in enumerate(THRESHOLDS):
            record["fss"][lead][t] = {}
            for m, side in enumerate(NEIGHBORHOODS):
                idx = ((i * len(THRESHOLDS) + j) * len(NEIGHBORHOODS)) + m
                record["fss"][lead][t][side] = float(fss_vals[idx])
        base = i * HIST_BINS * HIST_BINS
        record["hist"][lead] = np.asarray(
            hist_vals[base:base + HIST_BINS * HIST_BINS],
            dtype=np.uint32).reshape(HIST_BINS, HIST_BINS)
    return record


def record_summary(record):
    """Riassunto leggibile (per report e test), senza raster."""
    return {
        "schema": SCHEMA,
        "slot": iso_from_ms(record["slot_ms"]),
        "generated": iso_from_ms(record["generated_ms"]),
        "grid": [record["grid_w"], record["grid_h"]],
        "license": record.get("license", LICENSE_NAME),
        "source": record.get("source", SOURCE_NAME),
        "leads": {
            lead: {
                "present": record["leads"][lead]["present"],
                "computed": record["leads"][lead]["computed"],
                "valid_time": (iso_from_ms(record["leads"][lead]["valid_s"]
                                           * 1000)
                               if record["leads"][lead]["valid_s"] else None),
                "domain_pixels": record["leads"][lead]["domain_pixels"],
                "n_obs_rain": record["leads"][lead]["n_obs_rain"],
                "counters": {("%.1f" % t): list(record["counters"][lead][t])
                             for t in THRESHOLDS},
            } for lead in LEADS},
    }


# ---------------------------------------------------------------------------
# compressione, I/O giornaliero
# ---------------------------------------------------------------------------
def _codec():
    try:
        import zstandard  # noqa: F401
        return "zst"
    except ImportError:
        return "gz"


def compress(blob, codec=None):
    codec = codec or _codec()
    if codec == "zst":
        import zstandard
        return zstandard.ZstdCompressor(level=9, write_checksum=True).compress(
            bytes(blob)), "zst"
    import gzip
    return gzip.compress(bytes(blob), compresslevel=9, mtime=0), "gz"


def decompress(data):
    data = bytes(data)
    if data[:4] == b"\x28\xb5\x2f\xfd":
        import zstandard
        return zstandard.ZstdDecompressor().decompress(data)
    if data[:2] == b"\x1f\x8b":
        import gzip
        return gzip.decompress(data)
    raise ValueError("unknown_compression")


def day_path(root, day, codec=None):
    d = day if isinstance(day, _dt.date) else _dt.date.fromisoformat(str(day))
    return os.path.join(root, "%04d" % d.year, "%02d" % d.month,
                        "%02d.bin.%s" % (d.day, codec or _codec()))


def find_day_path(root, day):
    d = day if isinstance(day, _dt.date) else _dt.date.fromisoformat(str(day))
    for codec in ("zst", "gz"):
        p = day_path(root, d, codec)
        if os.path.isfile(p):
            return p, codec
    return None, None


def _atomic_write_bytes(path, data):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".ledger-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_day(root, day):
    """Lista di record del giorno (vuota se il file non esiste)."""
    path, _codec_name = find_day_path(root, day)
    if path is None:
        return []
    raw = decompress(open(path, "rb").read())
    if len(raw) % RECORD_BYTES:
        raise ValueError("day_truncated:%s:%d" % (path, len(raw)))
    out = []
    for off in range(0, len(raw), RECORD_BYTES):
        out.append(unpack(raw[off:off + RECORD_BYTES]))
    return out


def iter_records(root):
    """Tutti i record dell'archivio, in ordine di giorno e slot."""
    if not os.path.isdir(root):
        return
    days = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith((".bin.zst", ".bin.gz")):
                continue
            days.append(os.path.join(dirpath, name))
    for path in sorted(days):
        raw = decompress(open(path, "rb").read())
        if len(raw) % RECORD_BYTES:
            continue
        for off in range(0, len(raw), RECORD_BYTES):
            try:
                rec = unpack(raw[off:off + RECORD_BYTES])
            except ValueError:
                continue
            rec["_path"] = path
            yield rec


def merge_record(old, new):
    """Fonde un record nuovo in uno vecchio a livello di LEAD.

    Un lead presente nel nuovo sostituisce header/contatori/FSS/istogramma;
    un lead assente NON azzera quello vecchio (uno slot verificato in due run
    non perde il primo lead)."""
    for lead in LEADS:
        if not new["leads"][lead]["present"]:
            continue
        old["leads"][lead] = dict(new["leads"][lead])
        old["counters"][lead] = {t: tuple(new["counters"][lead][t])
                                 for t in THRESHOLDS}
        old["fss"][lead] = {t: dict(new["fss"][lead][t])
                            for t in THRESHOLDS}
        old["hist"][lead] = np.array(new["hist"][lead], dtype=np.uint32,
                                     copy=True)
    old["generated_ms"] = new["generated_ms"]
    old["flags"] = new["flags"]
    if new["grid_w"] and new["grid_h"]:
        old["grid_w"], old["grid_h"] = new["grid_w"], new["grid_h"]
    return old


def append_record(root, record):
    """Aggiunge (o fonde) un record nel file del giorno. Ritorna misure.

    Il CODEC del giorno e' quello del file gia' esistente: un giorno non puo'
    avere sia `.bin.zst` sia `.bin.gz` (due file per lo stesso giorno
    renderebbero invisibile uno dei due a `read_day`)."""
    t0 = time.time()
    day = day_of_slot_ms(record["slot_ms"])
    existing_path, existing_codec = find_day_path(root, day)
    existing = read_day(root, day)
    by_slot = {int(r["slot_ms"]): r for r in existing}
    slot = int(record["slot_ms"])
    if slot in by_slot:
        by_slot[slot] = merge_record(by_slot[slot], record)
        merged = True
    else:
        by_slot[slot] = record
        merged = False
    raw = b"".join(pack(by_slot[k]) for k in sorted(by_slot))
    codec = existing_codec or _codec()
    blob, _codec_used = compress(raw, codec)
    path = existing_path or day_path(root, day, codec)
    _atomic_write_bytes(path, blob)
    return {
        "path": path,
        "day": day.isoformat(),
        "records": len(by_slot),
        "raw_bytes": len(raw),
        "compressed_bytes": len(blob),
        "merged": merged,
        "seconds": round(time.time() - t0, 3),
    }


def append_slot(root, slot_ms, preds, obss, domains, valid_ms,
                generated_ms=None, grid=None):
    """Costruisce e appende il record di uno slot. `preds` = {lead: array}."""
    record = build_record(slot_ms, preds, obss, domains, valid_ms,
                          generated_ms=generated_ms, grid=grid)
    return append_record(root, record)


def archive_bytes(root):
    total = 0
    if not os.path.isdir(root):
        return 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.endswith((".bin.zst", ".bin.gz")):
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    pass
    return total


# ---------------------------------------------------------------------------
# documenti del branch
# ---------------------------------------------------------------------------
README = """# nowcast-ledger — ledger compatto della verifica del nowcast

Un file **per giorno UTC**, compresso, con record binari da **1102 byte**
(schema `meteorisk.nowcast_ledger/1`):

    nowcast_ledger/YYYY/MM/DD.bin.zst

Ogni record contiene, per lo slot sorgente del nowcast e per i 3 lead
ufficiali (15/30/60 min):

- **istogramma congiunto 8x8** di (previsto, osservato) in mm/h, con bordi
  `0, 0.1, 0.5, 1, 2, 5, 10, 20, inf`: le soglie 0,5/2/5 sono bordi esatti,
  quindi CSI/POD/FAR si possono RICALCOLARE per qualunque soglia futura che
  cada su un bordo; per una soglia interna a un bin il modulo restituisce un
  intervallo, non un numero inventato;
- **contatori** hits/misses/false alarms/correct negatives (3 lead x 3 soglie);
- **FSS** (Roberts & Lean 2008) per 3 lead x 3 soglie x 3 intorni
  (9/25/49 px), calcolato al momento dell'ingestione: non e' ricostruibile
  dall'istogramma.

Nessuna potatura: il ledger e' l'archivio lungo (~27 MB/anno con zstd).
Il branch `nowcast-archive` (finestra 7 giorni) resta separato e invariato.

**Attribuzione.** I dati di partenza sono il prodotto **SRI del Radar-DPC**
(Dipartimento della Protezione Civile), licenza **CC BY-SA 4.0**. Questo
ledger e' un'opera derivata: va attribuito alla fonte e condiviso con la
stessa licenza. Fonte: <https://radar-api.protezionecivile.it>

Scritto da: job `radar-engine`, step "Pubblica ledger verifica
(branch nowcast-ledger)". Pubblicato con commit ORPHAN senza parent e
`--force-with-lease` (mai `--force` nudo).
"""

LICENCE = """# Licenza e attribuzione

I dati di partenza sono il prodotto **SRI (Surface Rainfall Intensity)** del
**Radar-DPC**, pubblicato dalla Protezione Civile italiana con licenza
**CC BY-SA 4.0**.

Questo archivio contiene statistiche derivate (istogrammi congiunti,
contatori di contingenza, FSS): resta un'opera derivata e va attribuito alla
fonte e condiviso con la stessa licenza (share-alike).

Fonte: <https://radar-api.protezionecivile.it> — Radar-DPC, Dipartimento della
Protezione Civile.
"""


def ensure_docs(root):
    os.makedirs(root, exist_ok=True)
    for name, text in (("README.md", README), ("LICENCE.md", LICENCE)):
        path = os.path.join(root, name)
        try:
            with open(path, encoding="utf-8") as fh:
                if fh.read() == text:
                    continue
        except OSError:
            pass
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


# ---------------------------------------------------------------------------
# CLI minimale (ispezione del branch, non serve al run operativo)
# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Ledger compatto della verifica del nowcast")
    ap.add_argument("--root", required=True)
    ap.add_argument("--inspect-day", default=None)
    ap.add_argument("--json", default=None,
                    help="scrive il riassunto (JSON) nel path indicato")
    args = ap.parse_args(argv)

    if args.inspect_day:
        records = read_day(args.root, args.inspect_day)
        summary = [record_summary(r) for r in records]
    else:
        summary = [record_summary(r) for r in iter_records(args.root)]
    text = json.dumps({"schema": SCHEMA, "records": len(summary),
                       "compressed_bytes": archive_bytes(args.root),
                       "items": summary}, ensure_ascii=False, indent=1)
    if args.json:
        with open(args.json, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    print("[INFO] ledger: %d record, %d B compressi, %d B/record"
          % (len(summary), archive_bytes(args.root), RECORD_BYTES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
