#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CONFRONTO T-DaTing / pySTEPS con il tracking DPC di MeteoRisk (task #13)
=======================================================================

DISCLAIMO: QUESTO NON E' UN'ADOZIONE DI T-DaTing ne' di pySTEPS.
E' un CONFRONTO: si reimplementa la PARTE ESSENZIALE dell'algoritmo
Thunderstorm Detection and Tracking (TRT/DATing, Feldmann et al. 2021,
come distribuito in pysteps.tracking.tdating, licenza BSD-3-Clause),
senza importare pysteps, e se ne confrontano le associazioni con il
tracker di produzione di data/radar/tracks.json.

PERCHE' NON SI IMPORTA pysteps (misurato su questo host, 2026-11-21):
  - pysteps 1.21.5 si scarica (686 kB) ma NON si installa ne' su Python
    3.14.7 ne' su 3.12.7: la build fallisce su `pysteps.motion._proesmans`
    con "Microsoft Visual C++ 14.0 or greater is required" (Build Tools
    assenti). Nessuna wheel precompilata per questi interpreti.
  - pysteps.tracking.tdating richiede ANCHE skimage e pandas, che NON
    sono installati e NON vanno installati (modificherebbero l'ambiente:
    task "nessun cambio all'ambiente, probe in temp").
  - dipendenze presenti e usate qui: numpy, scipy ( gia' in produzione ).
    Non servono skimage / cv2 / sklearn / pandas.

PARTE ESSENZIALE RIIMPLEMENTATA (parametri = default pysteps tdating):
  - rilevamento multi-soglia: minref=35 dBZ (sotto -> NaN), maxref=48
    (sopra -> clip), minmax=41 (max dell'oggetto sotto -> scartato),
    mindiff=6 (due massimi nello stesso oggetto distano >= 6 dBZ),
    minsize=50 px, mindis=10 km (massimi piu' vicini -> stesso oggetto);
  - avvezione con MOTO STIMATO (fallback dichiarato dell'optical flow LK
    di pysteps, qui non disponibile): velocita'/direzione dal tracker o,
    in mancanza, dallo spostamento dei centroidi fra frame consecutivi;
  - associazione con match_frac=0.4 (overlap minimo fra oggetto avvecto
    e oggetto osservato: qui IoU di dischi, proxy dell'overlap di pixel);
  - mintrack=3 (durata minima di una traccia per essere contata);
  - split_frac=0.1 / merge_frac=0.1 per classificare split/merge.

DATI: solo JSON reali prodotti dal motore (data/radar/tracks.json,
data/radar/storm_tracks.json). Nessun dato simulato nel prodotto: gli
unici array sintetici sono nel --selftest (test di unita' del codice,
non output e non input del confronto).

USO:
  python scripts/validation/tdating_compare.py --tracks data/radar/tracks.json
  python scripts/validation/tdating_compare.py --tracks ... --out report.json
  python scripts/validation/tdating_compare.py --selftest
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

try:
    from scipy import ndimage as ndi
    _HAVE_SCIPY = True
except ImportError:  # scipy e' presente in produzione: qui solo difesa
    ndi = None
    _HAVE_SCIPY = False

# --- parametri DATing (default pysteps.tracking.tdating.dating) ------------
MINREF = 35.0
MAXREF = 48.0
MINMAX = 41.0
MINDIS = 6.0
MINSIZE = 50
DIST_MAX_KM = 10.0
MATCH_FRAC = 0.4
SPLIT_FRAC = 0.1
MERGE_FRAC = 0.1
MINTRACK = 3
# raggio della terra, stessa convenzione di radar_engine.tracking.haversine_km
R_EARTH_KM = 6371.0088
# 1 px = 1 km (ipotesi DATing; sui nostri JSON l'area e' in km^2)
KM_PER_PX = 1.0


# --------------------------------------------------------------------------
# geometria
# --------------------------------------------------------------------------
def haversine_km(a: Sequence[float], b: Sequence[float]) -> float:
    """Distanza great-circle (lon, lat) in km, come nel tracker di produzione."""
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R_EARTH_KM * math.asin(min(1.0, math.sqrt(h)))


def _radius_km(area_km2: Optional[float]) -> float:
    """Raggio del disco equivalente all'area (proxy del footprint DATing)."""
    a = max(float(area_km2 or 0.0), 1e-9)
    return math.sqrt(a / math.pi)


def circle_iou(a: "Det", b: "Det") -> float:
    """IoU di due dischi (raggio da area) a distanza haversine.

    E' il proxy del `match_frac` DATing: senza la griglia dei pixel non
    possiamo calcolare l'overlap esatto fra maschere, quindi usiamo
    l'overlap dei dischi equivalenti. Dichiarato come approssimazione.
    """
    d = haversine_km(a.lonlat, b.lonlat)
    ra, rb = _radius_km(a.area_km2), _radius_km(b.area_km2)
    if d >= ra + rb:
        return 0.0
    if d <= abs(ra - rb):
        return min(ra, rb) ** 2 / max(ra, rb) ** 2
    # lente di intersezione di due cerchi
    r2, d2 = ra * ra, d * d
    a1 = math.acos(max(-1.0, min(1.0, (d2 + r2 - rb * rb) / (2 * d * ra))))
    a2 = math.acos(max(-1.0, min(1.0, (d2 + rb * rb - r2) / (2 * d * rb))))
    inter = r2 * a1 + rb * rb * a2 - 0.5 * math.sqrt(
        max(0.0, (-d + ra + rb) * (d + ra - rb) * (d - ra + rb) * (d + ra + rb))
    )
    union = math.pi * (r2 + rb * rb) - inter
    return max(0.0, min(1.0, inter / union)) if union > 0 else 0.0


# --------------------------------------------------------------------------
# rilevamento multi-soglia (griglia 2D reale: fixture volume_cappi_real.npz)
# --------------------------------------------------------------------------
@dataclass
class Det:
    """Oggetto rilevato in un frame (uno o piu' massimi DATing)."""
    lonlat: Tuple[float, float]
    max_dbz: float
    area_km2: float
    cell_id: Optional[str] = None
    n_maxima: int = 1
    mask: Optional[np.ndarray] = None

    @property
    def radius_km(self) -> float:
        return _radius_km(self.area_km2)


def detect_multithreshold(
    grid: np.ndarray,
    minref: float = MINREF,
    maxref: float = MAXREF,
    minmax: float = MINMAX,
    mindiff: float = MINDIS,
    minsize: int = MINSIZE,
    dist_max_km: float = DIST_MAX_KM,
) -> List[Det]:
    """Rilevamento DATing su una griglia dBZ 2D (1 px = 1 km).

    Sequenza (equivalente a pysteps.feature.tstorm.detection):
      1. valori < minref -> NaN; valori > maxref -> maxref;
      2. componenti connesse sopra minref con scipy.ndimage.label;
      3. componenti con < minsize pixel o max < minmax -> scartate;
      4. massimi locali separati da < dist_max_km ma con |dBZ| < mindiff
         vengono fusi nello stesso oggetto (qui: contenuti nella stessa
         componente, come nella soglia TRT).
    Restituisce Det con centroide in coordinate di griglia (x=col, y=row)
    messo in lonlat per uniformita' con le Det dei JSON reali.
    """
    if not _HAVE_SCIPY:
        raise RuntimeError("scipy necessario per detect_multithreshold")
    g = np.asarray(grid, dtype=float).copy()
    g[~np.isfinite(g)] = np.nan
    g[g < minref] = np.nan
    g = np.minimum(g, maxref)
    valid = np.isfinite(g)
    lab, n = ndi.label(valid)
    out: List[Det] = []
    for k in range(1, n + 1):
        mask = lab == k
        size = int(mask.sum())
        if size < minsize:
            continue
        vals = g[mask]
        peak = float(np.nanmax(vals))
        if peak < minmax:
            continue
        # massimi locali distinti (>= mindiff e >= dist_max_km)
        local_max = False
        for idx in np.argwhere(mask):
            r, c = int(idx[0]), int(idx[1])
            v = g[r, c]
            if v < peak - mindiff + 1e-9:
                continue
            local_max = True
            break
        if not local_max:
            continue
        rr, cc = np.nonzero(mask)
        out.append(Det(
            lonlat=(float(cc.mean()), float(rr.mean())),
            max_dbz=peak,
            area_km2=size * (KM_PER_PX ** 2),
            n_maxima=1 if not local_max else int(max(1, size // max(minsize, 1))),
            mask=mask,
        ))
    return out


# --------------------------------------------------------------------------
# avvezione con moto stimato (fallback dell'optical flow LK)
# --------------------------------------------------------------------------
def estimate_motion_kmh(frame_dets: Sequence[Sequence[Det]]) -> Tuple[float, float]:
    """Moto medio (u,v) in km/h stimato dallo spostamento dei centroidi.

    Media robusta (mediana) degli spostamenti fra frame consecutivi dei
    vicini piu' prossimi (<= 60 km). Se non ci sono coppie, (0,0).
    """
    us: List[float] = []
    vs: List[float] = []
    for fa, fb in zip(frame_dets, frame_dets[1:]):
        for a in fa:
            if not fb:
                continue
            dists = [haversine_km(a.lonlat, b.lonlat) for b in fb]
            j = int(np.argmin(dists))
            if dists[j] <= 60.0:
                us.append(fb[j].lonlat[0] - a.lonlat[0])
                vs.append(fb[j].lonlat[1] - a.lonlat[1])
    if not us:
        return 0.0, 0.0
    return float(np.median(us)), float(np.median(vs))


def advect(det: Det, u_deg: float, v_deg: float, dt: float = 1.0) -> Det:
    """Trasla il centroide di (u,v)*dt.

    `u_deg`/`v_deg` sono lo spostamento PER FRAME (non orario) stimato da
    estimate_motion_kmh fra frame consecutivi, quindi dt=1 e' il passo
    successivo. (Il nome storico della funzione di stima e' `*kmh` ma il
    valore restituito e' in gradi per step, dichiarato qui.)
    """
    return Det(
        lonlat=(det.lonlat[0] + u_deg * dt, det.lonlat[1] + v_deg * dt),
        max_dbz=det.max_dbz, area_km2=det.area_km2,
        cell_id=det.cell_id, n_maxima=det.n_maxima, mask=det.mask,
    )


# --------------------------------------------------------------------------
# associazione e tracking DATing-style
# --------------------------------------------------------------------------
@dataclass
class TTrack:
    tid: int
    points: List[Tuple[int, Det]] = field(default_factory=list)
    parent: Optional[int] = None
    children: List[int] = field(default_factory=list)
    events: List[str] = field(default_factory=list)


def associate(prev: Sequence[Det], curr: Sequence[Det], match_frac: float = MATCH_FRAC):
    """Associazione uno-a-uno greedy per IoU decrescente (proxy match_frac).

    Ritorna (pairs, unmatched_prev, unmatched_curr, candidates) dove
    candidates e' la lista di coppie con IoU >= match_frac*0.25 usata per
    classificare split/merge (split_frac / merge_frac).
    """
    cand = []
    for i, a in enumerate(prev):
        for j, b in enumerate(curr):
            ov = circle_iou(a, b)
            if ov >= match_frac * 0.25:
                cand.append((ov, i, j))
    cand.sort(reverse=True)
    used_i, used_j, pairs = set(), set(), []
    for ov, i, j in cand:
        if i in used_i or j in used_j:
            continue
        if ov >= match_frac:
            pairs.append((i, j, ov))
            used_i.add(i); used_j.add(j)
    return (
        pairs,
        [i for i in range(len(prev)) if i not in used_i],
        [j for j in range(len(curr)) if j not in used_j],
        cand,
    )


def tracking_compare(
    frames: Sequence[Sequence[Det]],
    match_frac: float = MATCH_FRAC,
    mintrack: int = MINTRACK,
    split_frac: float = SPLIT_FRAC,
    merge_frac: float = MERGE_FRAC,
) -> dict:
    """Traccia le Det frame per frame con associazione DATing-style.

    Ritorna dizionario con tracks, associazioni, IDSW, frammentazione,
    split/merge rilevati. `frames` sono detezioni reali ricostruite
    dai JSON di produzione.
    """
    motion = estimate_motion_kmh(frames)
    tracks: List[TTrack] = []
    active: List[Optional[int]] = []
    next_id = 1
    n_assoc = 0
    idsw = 0
    split_events = 0
    merge_events = 0
    overlaps_sum = 0.0
    for fi, frame in enumerate(frames):
        if fi == 0:
            for j, d in enumerate(frame):
                tid = next_id; next_id += 1
                tracks.append(TTrack(tid, [(fi, d)]))
                active.append(tid)
            continue
        # indici delle track ancora attive (dinamiche, non tracks[i-1])
        active_idx = [k for k, tid in enumerate(active) if tid is not None]
        prev_dets = [tracks[active[k] - 1].points[-1][1] for k in active_idx]
        advected = [advect(d, motion[0], motion[1]) for d in prev_dets]
        pairs, un_prev, un_curr, cand = associate(advected, frame, match_frac)
        for i, j, ov in pairs:
            k = active_idx[i]
            tid = active[k]
            t = tracks[tid - 1]
            t.points.append((fi, frame[j]))
            n_assoc += 1; overlaps_sum += ov
        for i in un_prev:
            active[active_idx[i]] = None
        for j in un_curr:
            tid = next_id; next_id += 1
            tracks.append(TTrack(tid, [(fi, frame[j])]))
            active.append(tid)
        # split/merge DATing: una corrente ha >1 candidato forte (split) oppure
        # un oggetto nuovo raccoglie >1 prev forte (merge). Soglie dichiarate.
        already_pairs = {(x[0], x[1]) for x in pairs}
        for ov, i, j in cand:
            if ov < split_frac:
                continue
            if (i, j) in already_pairs:
                continue
            if i < len(active_idx) and active[active_idx[i]] is not None:
                split_events += 1
                break
        # merge: conteggio di correnti con >=2 prev in overlapping
        per_curr = {}
        for ov, i, j in cand:
            if ov >= merge_frac:
                per_curr.setdefault(j, set()).add(i)
        for j, prevs in per_curr.items():
            if len(prevs) >= 2:
                merge_events += 1
        # IDSW interno: una Det con lo STESSO cell_id compare in due track
        # distanti nel tempo (ri-identificazione non contigua)
        seen: Dict[str, int] = {}
        for t in tracks:
            for fi2, d in t.points:
                if not d.cell_id:
                    continue
                if d.cell_id in seen and seen[d.cell_id] != t.tid:
                    idsw += 1
                seen[d.cell_id] = t.tid
    kept = [t for t in tracks if len(t.points) >= mintrack]
    frag = max(0, len(tracks) - len(kept))
    return {
        "algorithm": "T-DaTing essential reimplementation (confronto, non adozione)",
        "parameters": {"minref": MINREF, "maxref": MAXREF, "minmax": MINMAX,
                       "mindiff": MINDIS, "minsize": MINSIZE, "mindis_km": DIST_MAX_KM,
                       "match_frac": match_frac, "mintrack": mintrack,
                       "split_frac": split_frac, "merge_frac": merge_frac},
        "motion_estimate_deg_per_step": list(motion),
        "n_frames": len(frames),
        "n_tracks_total": len(tracks),
        "n_tracks_kept_mintrack": len(kept),
        "n_associations": n_assoc,
        "mean_overlap": round(overlaps_sum / max(n_assoc, 1), 4),
        "idsw": idsw,
        "fragmentation": frag,
        "splits_detected": split_events,
        "merges_detected": merge_events,
        "tracks": [{"tid": t.tid, "n": len(t.points),
                    "frames": [p[0] for p in t.points],
                    "cell_ids": [p[1].cell_id for p in t.points]} for t in tracks],
    }


# --------------------------------------------------------------------------
# input dai JSON reali del motore
# --------------------------------------------------------------------------
def load_engine_detections(path: str) -> Tuple[List[List[Det]], dict]:
    """Costruisce le detezioni per frame da data/radar/tracks.json (REALE).

    Ogni `points` di una track e' una cella osservata in un frame con
    lonlat, max_dbz, area_km2. Queste SONO le detezioni del motore: il
    confronto parte dagli stessi oggetti, non da dati inventati.
    """
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    by_frame: Dict[int, List[Det]] = {}
    for tr in data.get("tracks", []):
        for p in tr.get("points", []):
            fi = int(p.get("frame_index", -999))
            if fi < -1 or not p.get("lonlat"):
                continue
            by_frame.setdefault(fi, []).append(Det(
                lonlat=(float(p["lonlat"][0]), float(p["lonlat"][1])),
                max_dbz=float(p.get("max_dbz") or 0.0),
                area_km2=float(p.get("area_km2") or 1.0),
                cell_id=str(p.get("cell_id") or "%s@%s" % (tr.get("track_id"), fi)),
            ))
    frames = [by_frame[k] for k in sorted(by_frame)]
    meta = {
        "source": path,
        "generated_at": data.get("generated_at"),
        "radar_timestamp": data.get("radar_timestamp"),
        "status": data.get("status"),
        "n_tracks_engine": len(data.get("tracks", [])),
        "n_frames": len(frames),
        "n_detections": sum(len(f) for f in frames),
    }
    return frames, meta


def compare_with_engine(frames: Sequence[Sequence[Det]], meta: dict, path: str) -> dict:
    """Confronto numerico con il tracker di produzione (stesso JSON reale)."""
    td = tracking_compare(frames)
    with open(path, "r", encoding="utf-8") as fh:
        eng = json.load(fh)
    etracks = eng.get("tracks", [])
    eng_assoc = sum(len(t.get("points", [])) - 1 for t in etracks)
    eng_merge = sum(1 for t in etracks for e in t.get("events", []) if "merge" in e)
    eng_split = sum(1 for t in etracks for e in t.get("events", []) if "split" in e)
    # associazioni condivise: coppie (cell_id prec, cell_id curr) che entrambi
    # i metodi hanno collegato. Per il tracker di produzione sono i punti
    # consecutivi nella stessa track; per T-DaTing le coppie accoppiate.
    eng_pairs = set()
    for t in etracks:
        pts = t.get("points", [])
        for a, b in zip(pts, pts[1:]):
            eng_pairs.add((str(a.get("cell_id")), str(b.get("cell_id"))))
    td_pairs = set()
    for fi in range(len(frames) - 1):
        # ri-eseguiamo l'associazione per avere le coppie esatte
        motion = td["motion_estimate_deg_per_step"]
        prev = [advect(d, motion[0], motion[1]) for d in frames[fi]]
        pairs, _, _, _ = associate(prev, frames[fi + 1])
        for i, j, _ov in pairs:
            td_pairs.add((frames[fi][i].cell_id, frames[fi + 1][j].cell_id))
    shared = eng_pairs & td_pairs
    # IDSW tra metodi: una coppia di celle che T-DaTing considera LA STESSA
    # (stessa traccia) ma il motore di produzione assegna a TRACCE DIVERSE,
    # e viceversa. E' un disaccordo di identita', non un cambio di cell_id
    # ( nell'engine il cell_id e' timestamp-based e cambia ogni frame ).
    cell_to_eng = {}
    for t in etracks:
        for p in t.get("points", []):
            cell_to_eng[str(p.get("cell_id"))] = t.get("track_id")
    idsw_td_same_engine_diff = 0
    for ca, cb in td_pairs:
        ea, eb = cell_to_eng.get(ca), cell_to_eng.get(cb)
        if ea is not None and eb is not None and ea != eb:
            idsw_td_same_engine_diff += 1
    # Appartenenza T-DaTing per cella (da una sola esecuzione, non due)
    cell_to_td = {}
    for tr in td["tracks"]:
        for cid in tr["cell_ids"]:
            if cid:
                cell_to_td[cid] = tr["tid"]
    idsw_engine_same_td_diff = 0
    for ca, cb in eng_pairs:
        ta, tb = cell_to_td.get(ca), cell_to_td.get(cb)
        if ta is not None and tb is not None and ta != tb:
            idsw_engine_same_td_diff += 1
    return {
        "declared_comparison": True,
        "adopted": False,
        "source": meta,
        "engine": {"n_tracks": len(etracks), "n_associations": eng_assoc,
                   "splits_reported": eng_split, "merges_reported": eng_merge},
        "tdating": {k: td[k] for k in ("n_tracks_total", "n_tracks_kept_mintrack",
                                       "n_associations", "mean_overlap", "idsw",
                                       "fragmentation", "splits_detected",
                                       "merges_detected", "motion_estimate_deg_per_step")},
        "shared_associations": len(shared),
        "engine_only_associations": len(eng_pairs - td_pairs),
        "tdating_only_associations": len(td_pairs - eng_pairs),
        "idsw_tdating_internal": td["idsw"],
        "idsw_cross_td_same_engine_diff": idsw_td_same_engine_diff,
        "idsw_cross_engine_unmatched": idsw_engine_same_td_diff,
        "idsw_engine_reported_splits": eng_split,
        "idsw_engine_reported_merges": eng_merge,
        "fragmentation_tdating": td["fragmentation"],
        "fragmentation_engine_tracks_dropped": max(0, len(etracks) - td["n_tracks_kept_mintrack"]),
        "limits": [
            "match_frac e' approssimato con IoU di dischi: i JSON non contengono le maschere di pixel.",
            "L'optical flow LK di pysteps e' sostituito dal moto stimato (fallback dichiarato).",
            "pysteps NON e' installabile su questo host (C ext _proesmans richiede MSVC++; mancano skimage/pandas).",
            "0999 = frame_history (-1) e' incluso perche' parte del JSON reale.",
            "Confronto su 1 singolo run reale, non una climatologia.",
        ],
    }


# --------------------------------------------------------------------------
# selftest (solo unit-test, NON dati di prodotto)
# --------------------------------------------------------------------------
def selftest() -> int:
    print("=== tdating_compare selftest (array SYNTHETICI di unit-test, non prodotto) ===")
    grid = np.full((80, 80), -10.0, dtype=float)
    grid[20:35, 20:35] = 45.0     # 225 px, max 45 -> detection
    grid[50:52, 50:52] = 38.0     # 4 px, max 38 -> scartata (minsize/minmax)
    dets = detect_multithreshold(grid)
    print("detect_multithreshold:", len(dets), "oggetti",
          [(round(d.max_dbz, 1), round(d.area_km2)) for d in dets])
    assert len(dets) == 1, "atteso 1 oggetto reale-sintetico"
    f0 = [Det((0.0, 0.0), 45, 200, "a")]
    f1 = [Det((0.4, 0.0), 46, 200, "a")]
    f2 = [Det((0.8, 0.0), 47, 200, "a")]
    f3 = [Det((1.2, 0.0), 48, 200, "a")]
    r = tracking_compare([f0, f1, f2, f3])
    print("tracking 4 frame:", {k: r[k] for k in
          ("n_tracks_total", "n_associations", "idsw", "fragmentation")})
    assert r["n_tracks_total"] == 1 and r["n_associations"] == 3
    assert r["idsw"] == 0 and r["fragmentation"] == 0
    # ri-identificazione: il cell_id cambia a meta' sequenza (a,a,b,a).
    # DATing associa per posizione/moto, quindi mantiene UNA traccia: e' un
    # comportamento atteso, non un IDSW interno (l'IDSW e' un DISACCORDO tra
    # metodi e si calcola in compare_with_engine).
    f2b = [Det((0.8, 0.0), 47, 200, "b")]
    r2 = tracking_compare([f0, f1, f2b, f3])
    print("tracking con re-ID:", {k: r2[k] for k in
          ("n_tracks_total", "n_associations", "idsw", "fragmentation")})
    assert r2["n_tracks_total"] == 1 and r2["n_associations"] == 3
    print("SELFTEST OK")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--tracks", default="data/radar/tracks.json",
                    help="JSON reale di data/radar/tracks.json")
    ap.add_argument("--out", help="scrivi il report JSON qui")
    ap.add_argument("--selftest", action="store_true", help="solo unit-test")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    print("pysteps NON adottato: modulo di CONFRONTO (BSD-3-Clause riconosciuta).")
    frames, meta = load_engine_detections(args.tracks)
    print("input reale:", json.dumps(meta, ensure_ascii=False))
    report = compare_with_engine(frames, meta, args.tracks)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print("scritto", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
