#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Meteorisk Radar Engine — multiscale.py

Segmentazione multi-soglia AGGIUNTIVA (layer opzionale, DISABILITATO di default).

Motivo (misurato, vedi REPORT.md): `detect.py` usa una soglia singola a 20 dBZ.
Sul crop CAPPI/VMI reale 64x64 (slot 2026-11-15T16:15:00Z) un SINGOLO eco
connesso a 20 dBZ contiene 23 plateau di massimi locali (12 a 30 dBZ): la
segmentazione attuale fonde nuclei distinti in una sola cella.

Questo modulo NON modifica `detect.py`, `config['detect']` o i derivati
pubblicati: produce descrittori separati, richiamabili solo su richiesta.
Nessuna dipendenza nuova: numpy + scipy.ndimage (gia' presenti).

Contratto dei 4 stati del dominio (coerente con preprocess.py:23-42):
  * valid_echo     - pixel valido con eco (dBZ >= soglia inviluppo);
  * valid_no_echo  - pixel valido senza eco (valore < soglia, oppure -9998
                     "in copertura senza eco");
  * nodata         - -9999 / NaN / nodata dichiarato (fuori copertura);
  * insufficient_coverage - VERDETTO di finestra, non una quarta sentinella:
                     frazione osservata (eco + no-echo) < coverage_min_fraction.
Nessuna operazione morfologica: gli inviluppi sono componenti connesse calcolate
sul solo dato valido, quindi NON possono attraversare lacune di copertura.

Taratura: tutti i numeri in DEFAULT_THRESHOLDS sono MISURATI su 1 frame reale
(64x64) e documentati in REPORT.md; dove la scelta e' una convenzione di
progetto e' dichiarata "SCELTA INGEGNERISTICA" nel reference del campo.
"""

import math

import numpy as np
import scipy.ndimage as ndi

SOURCE = "radar_engine.multiscale"

# Sentinel "in copertura senza eco" della famiglia echo (preprocess.py:41).
_NO_ECHO_SENTINEL = -9998.0
# watershed_ift accetta SOLO input uint8/uint16 (scipy 1.18.1, verificato):
# codifica il costo 0..100 dBZ in 0..65500, barriera 65535 = fuori inviluppo.
_COST_MAX_DBZ = 100.0
_COST_SCALE = 655.0
_COST_BARRIER = 65535

# Valori MISURATI sul crop reale (1 frame, 982 px validi, 1 km/px).
DEFAULT_THRESHOLDS = {
    "enabled": False,                 # SCELTA INGEGNERISTICA: default OFF
    "envelope_dbz": 20.0,             # = config['detect']['dbz_threshold']
    "peak_levels_dbz": [20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 50.0],
    "marker_dbz": 30.0,               # plateau "nucleo" per i marker watershed
    "prominence_min_dbz": 5.0,        # p75 prominenza misurata (23 plateau)
    "min_sep_km": 5.0,                # ~p10 distanza NN tra picchi prominenti
    "min_nucleus_area_km2": 4.0,      # = min_area_px volume_publisher
    "coverage_min_fraction": 0.60,    # inviluppo reale osservato al 67,8%
    "peak_filter_px": 3,
}

REFERENCE = {
    "enabled": "SCELTA INGEGNERISTICA: layer additivo disabilitato di default; "
               "nessun derivato di produzione lo richiama.",
    "domain": "preprocess.py:23-42 (famiglia echo: -9999 fuori copertura, "
              "-9998 in copertura senza eco). Copertura insufficiente e' un "
              "verdetto di conteggio, non una sentinella.",
    "n_nuclei": "Inviluppo low-threshold + plateau (>=" + str(DEFAULT_THRESHOLDS["marker_dbz"]) +
                " dBZ) con prominenza >= 5 dB (p75 misurato) e separazione >= "
                "5 km (p10 misurato); watershed_ift solo se >=2. Nessuna morfologia.",
    "dbz_max": "Massimo sui soli pixel validi dell'inviluppo (fix 1.2.4.64).",
    "prominence": "Caduta fino al punto di fusione con un picchio piu' alto "
                  "(flooding a soglie decrescenti). p50=1.5, p75=5.0, p90=12.4 dB misurati.",
    "min_dist_nuclei_km": "Distanza minima fra centroide dei nuclei superstiti "
                          "(1 km/px, griglia DPC). NN prom>=5: p10=4.74, med=6.88 km.",
    "envelope_area_km2": "Pixel dell'inviluppo 20 dBZ connesso 8-conn su dato valido; "
                         "nessun closingThrough NoData. Inviluppo reale = 609 km2.",
    "envelope_bbox_px": "Bounding box (r0,c0,r1,c1) dell'inviluppo: verifica che "
                        "nessun oggetto attraversi una lacuna di copertura.",
    "nuclei_area_km2": "Somma delle aree delle regioni watershed sopravvissute al "
                       "gate area minima (o area del plateau per un solo nucleo).",
    "linearity": "1 - lambda2/lambda1 degli autovalori della covarianza dei pixel "
                 "(0 cerchio, 1 segmento). Misurato sull'inviluppo reale: 0.885.",
    "orientation_deg": "Angolo dell'asse principale (PCA, 0-180 gradi). "
                       "Misurato sull'inviluppo reale: 169.7 gradi.",
    "completeness": "Frazione di pixel osservati nel bounding box dell'oggetto. "
                    "Soglia 0.60 = SCELTA INGEGNERISTICA (inviluppo reale 0.678).",
    "threshold_scan": "Conteggio plateau per soglia (20/25/30/35/40/45/50). "
                      "Sul crop reale: 23 a 20, 12 a 30, 0 a 45/50 dBZ.",
    "split_by_watershed": "True se l'oggetto conteneva >=2 picchi prominenti ed e' "
                          "stato diviso con scipy.ndimage.watershed_ift (input uint16).",
}


def field(value, *, null_reason=None, source=None, reference=None, **extra):
    """Campo con value/source/reference; None senza motivo e' un errore
    (stesso contratto di advanced.field / severe_stack.field)."""
    if isinstance(value, float) and not math.isfinite(value):
        value, null_reason = None, null_reason or "not_finite"
    if value is None and not null_reason:
        raise ValueError("field_null_without_reason")
    out = {"value": value, "null_reason": null_reason,
           "source": source or SOURCE, "reference": reference}
    out.update(extra)
    return out


def thresholds_from_config(cfg=None):
    """Unisce DEFAULT_THRESHOLDS con il blocco CONFIG['multiscale'].

    Solo valori finiti e (per le soglie) non negativi; `enabled` e' bool.
    Nessuna dipendenza da config globale: il blocco puo' essere passato
    esplicitamente (utile nei test e per override JSON)."""
    out = dict(DEFAULT_THRESHOLDS)
    if isinstance(cfg, dict):
        for key in out:
            if key not in cfg:
                continue
            val = cfg[key]
            if key == "enabled":
                out[key] = bool(val)
            elif key == "peak_levels_dbz":
                vals = [float(v) for v in val
                        if isinstance(v, (int, float)) and math.isfinite(float(v))
                        and float(v) >= 0]
                if vals:
                    out[key] = sorted(set(vals))
            else:
                try:
                    f = float(val)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(f) and f >= 0:
                    out[key] = f
    return out


def classify_domain(data, *, nodata_values=(-9999.0, -9998.0),
                    envelope_dbz=20.0, coverage_min_fraction=0.60):
    """Separazione dei 4 stati e costruzione dell'array dBZ numerico.

    Ritorna (Z, numeric, domain, report). `Z` contiene -inf dove non c'e'
    dato numerico, cosi' maximum_filter/ndi.label NON attraversano lacune.
    """
    arr = np.asarray(data, dtype=float)
    finite = np.isfinite(arr)
    is_out = ~finite
    is_noecho = np.zeros(arr.shape, dtype=bool)
    for v in nodata_values:
        eq = finite & (arr == float(v))
        if float(v) == _NO_ECHO_SENTINEL:
            is_noecho |= eq
        else:
            is_out |= eq
    numeric = finite & ~is_noecho & ~is_out
    domain = numeric | is_noecho
    Z = np.where(numeric, arr, -np.inf)
    echo = numeric & (arr >= envelope_dbz)

    observed = float(domain.mean()) if domain.size else 0.0
    status = ("insufficient_coverage" if observed < coverage_min_fraction
              else "ok")
    report = {
        "valid_echo_px": field(int(echo.sum()), reference=REFERENCE["domain"]),
        "valid_no_echo_px": field(int((numeric & (arr < envelope_dbz)).sum() +
                                      is_noecho.sum()), reference=REFERENCE["domain"]),
        "nodata_px": field(int(is_out.sum()), reference=REFERENCE["domain"]),
        "observed_fraction": field(round(observed, 6), reference=REFERENCE["domain"]),
        "coverage_status": field(status, reference=REFERENCE["domain"]),
    }
    return Z, numeric, domain, report


def _plateaus(Z, threshold, filt):
    """Mask dei plateau di massimi locali >= threshold (8-connessi)."""
    mx = ndi.maximum_filter(Z, size=filt, mode="nearest")
    return np.isfinite(Z) & (Z == mx) & (Z >= threshold)


def _peak_list(Z, peak_mask):
    """Lista di (valore, riga, col, mask locale) per ogni plateau etichettato."""
    lab, n = ndi.label(peak_mask, structure=np.ones((3, 3), bool))
    objs = ndi.find_objects(lab)
    out = []
    for i in range(1, n + 1):
        slc = objs[i - 1]
        m = lab[slc] == i
        rr, cc = np.nonzero(m)
        rr = rr + slc[0].start
        cc = cc + slc[1].start
        out.append({"val": float(Z[rr, cc].max()),
                    "r": float(rr.mean()), "c": float(cc.mean()),
                    "npx": int(m.sum()), "id": i})
    return out


def _prominence(Z, peaks, comp_mask, low):
    """Prominenza di ogni plateau: caduta fino alla fusione con un picchio
    piu' alto (soglie decrescenti). Nessuna interpolazione inventata."""
    if not peaks:
        return {}
    vals = np.unique(Z[comp_mask & np.isfinite(Z) & (Z >= low)])
    vals = sorted((float(x) for x in vals), reverse=True)
    seeds = {p["id"]: (int(round(p["r"])), int(round(p["c"]))) for p in peaks}
    prom = {}
    for t in vals:
        lab, _ = ndi.label((Z >= t) & comp_mask, structure=np.ones((3, 3), bool))
        groups = {}
        for pid, (r, c) in seeds.items():
            if 0 <= r < lab.shape[0] and 0 <= c < lab.shape[1]:
                l = int(lab[r, c])
                if l > 0:
                    groups.setdefault(l, []).append(pid)
        for ids in groups.values():
            if len(ids) < 2:
                continue
            ids.sort(key=lambda k: next(p["val"] for p in peaks if p["id"] == k))
            for pid in ids[:-1]:
                if pid not in prom:
                    pv = next(p["val"] for p in peaks if p["id"] == pid)
                    prom[pid] = max(0.0, pv - t)
    for p in peaks:
        if p["id"] not in prom:
            prom[p["id"]] = max(0.0, p["val"] - low)
    return prom


def _dedup(peaks, prom, min_dbz, min_sep):
    """Mantiene i picchi con prominenza sufficiente, poi greedy per distanza
    minima (ordine per valore decrescente -> deterministica)."""
    ok = [p for p in peaks if prom.get(p["id"], 0.0) >= min_dbz]
    ok.sort(key=lambda p: (-p["val"], p["r"], p["c"]))
    kept = []
    for p in ok:
        if all(math.hypot(p["r"] - q["r"], p["c"] - q["c"]) >= min_sep
               for q in kept):
            kept.append(p)
    return kept


def _pca(mask):
    """(linearita', orientamento_deg) dai momenti 2d dei pixel della maschera."""
    rr, cc = np.nonzero(mask)
    if rr.size < 3:
        return None, None
    pts = np.column_stack((cc.astype(float), rr.astype(float)))
    cov = np.cov((pts - pts.mean(axis=0)).T)
    eig, vec = np.linalg.eigh(cov)
    lam1, lam2 = max(float(eig[1]), 1e-12), max(float(eig[0]), 1e-12)
    lin = 1.0 - lam2 / lam1
    ang = float(np.degrees(np.arctan2(vec[1, 1], vec[0, 1])) % 180.0)
    return lin, ang


def segment_multiscale(data, pixel_area_km2, *, cfg=None,
                       nodata_values=(-9999.0, -9998.0), seed=None):
    """Segmenta un singolo frame. `seed` e' accettato per il contratto di
    riproducibilita': NON viene usata alcuna sorgente casuale, quindi due
    chiamate con lo stesso seed (e con qualunque seed) danno output identico.

    Con cfg['enabled']=False (default di produzione) restituisce uno stub
    senza oggetti: nessun derivato viene toccato."""
    th = thresholds_from_config(cfg)
    if not th["enabled"]:
        return {"enabled": False, "thresholds": th, "objects": [],
                "domain": None,
                "note": "layer disabilitato: richiamare con cfg['multiscale']['enabled']=True"}

    low = th["envelope_dbz"]
    Z, numeric, domain, dom_report = classify_domain(
        data, nodata_values=nodata_values, envelope_dbz=low,
        coverage_min_fraction=th["coverage_min_fraction"])
    conn = np.ones((3, 3), bool)
    env_lab, n_env = ndi.label((Z >= low), structure=conn)
    if n_env == 0:
        return {"enabled": True, "thresholds": th, "objects": [],
                "domain": dom_report, "note": "nessun eco sopra soglia"}

    filt = int(th["peak_filter_px"])
    peaks_all = _peak_list(Z, _plateaus(Z, low, filt))
    peaks_by_id = {p["id"]: p for p in peaks_all}
    env_objs = ndi.find_objects(env_lab)
    objects = []

    for idx in range(1, n_env + 1):
        slc = env_objs[idx - 1]
        comp = np.zeros(Z.shape, dtype=bool)
        comp[slc] = env_lab[slc] == idx
        comp_vals = Z[comp]
        if comp_vals.size == 0:
            continue

        # scan per soglia (tutte le soglie misurate, plateau within component)
        scan = []
        for t in th["peak_levels_dbz"]:
            pl = _peak_list(Z, _plateaus(Z, t, filt))
            n_in = sum(1 for p in pl if comp[int(round(p["r"])), int(round(p["c"]))])
            scan.append(field(n_in, reference=REFERENCE["threshold_scan"], dbz=t))

        # picchi dell'inviluppo dentro questo componente
        comp_peaks = []
        for p in peaks_all:
            if comp[int(round(p["r"])), int(round(p["c"]))]:
                q = dict(p)
                comp_peaks.append(q)
        prom = _prominence(Z, comp_peaks, comp, low)
        kept = _dedup(comp_peaks, prom, th["prominence_min_dbz"], th["min_sep_km"])

        # watershed SOLO se >=2 picchi prominenti
        split = len(kept) >= 2
        core_area = 0.0
        marker_pm = _plateaus(Z, th["marker_dbz"], filt)
        marker_lab, _ = ndi.label(marker_pm, structure=conn)
        used_labels = set()
        if split:
            markers = np.zeros(Z.shape, dtype=np.int32)
            for j, p in enumerate(kept, start=1):
                r, c = int(round(p["r"])), int(round(p["c"]))
                l = int(marker_lab[r, c])
                # due picchi possono condividere lo stesso plateau a 30 dBZ:
                # in quel caso il secondo marker e' il suo solo pixel di picco
                if l > 0 and l not in used_labels:
                    markers[marker_lab == l] = j
                    used_labels.add(l)
                else:
                    markers[r, c] = j
            cost = np.full(Z.shape, _COST_BARRIER, dtype=np.uint16)
            cost[comp] = np.clip(
                (_COST_MAX_DBZ - Z[comp]) * _COST_SCALE, 0, _COST_BARRIER - 1
            ).astype(np.uint16)
            ws = ndi.watershed_ift(cost, markers, structure=conn)
            # maschera all'inviluppo: nessun nucleo puo' attraversare un gap
            ws = np.where(comp, ws, 0)
            sizes = ndi.sum(np.ones_like(comp, int), ws, range(1, len(kept) + 1))
            good = [j for j in range(1, len(kept) + 1)
                    if sizes[j - 1] * pixel_area_km2 >= th["min_nucleus_area_km2"]]
            n_nuclei = len(good)
            core_area = float(sum(sizes[j - 1] for j in good) * pixel_area_km2)
        else:
            n_nuclei = len(kept)
            if kept:
                # un solo nucleo: area = core >= marker_dbZ dell'inviluppo
                a = float(((Z >= th["marker_dbz"]) & comp).sum() * pixel_area_km2)
                if a < th["min_nucleus_area_km2"]:
                    n_nuclei, a = 0, 0.0
                core_area = a

        prom_vals = [prom.get(p["id"], 0.0) for p in kept]
        dists = [math.hypot(p["r"] - q["r"], p["c"] - q["c"])
                 for i, p in enumerate(kept) for q in kept[i + 1:]]
        lin, ang = _pca(comp)

        # completezza: frazione osservata nel bbox dell'oggetto
        rr, cc = np.nonzero(comp)
        obs = float(domain[rr.min():rr.max() + 1,
                           cc.min():cc.max() + 1].mean())
        if obs >= th["coverage_min_fraction"]:
            compl = field(round(obs, 6), reference=REFERENCE["completeness"])
        else:
            compl = field(None, null_reason="insufficient_coverage",
                          reference=REFERENCE["completeness"],
                          observed_fraction=round(obs, 6))

        objects.append({
            "n_nuclei": field(n_nuclei, reference=REFERENCE["n_nuclei"]),
            "dbz_max": field(round(float(np.max(Z[comp])), 3),
                             reference=REFERENCE["dbz_max"]),
            "prominence": (field(round(max(prom_vals), 3),
                                 reference=REFERENCE["prominence"])
                           if prom_vals else
                           field(None, null_reason="no_prominent_peak",
                                 reference=REFERENCE["prominence"])),
            "min_dist_nuclei_km": (field(round(min(dists), 3),
                                         reference=REFERENCE["min_dist_nuclei_km"])
                                   if len(kept) >= 2 else
                                   field(None, null_reason="fewer_than_two_nuclei",
                                         reference=REFERENCE["min_dist_nuclei_km"])),
            "envelope_area_km2": field(round(float(comp.sum() * pixel_area_km2), 3),
                                       reference=REFERENCE["envelope_area_km2"]),
            "envelope_bbox_px": field((int(rr.min()), int(cc.min()),
                                       int(rr.max()), int(cc.max())),
                                      reference=REFERENCE["envelope_bbox_px"]),
            "nuclei_area_km2": field(round(core_area, 3),
                                     reference=REFERENCE["nuclei_area_km2"]),
            "linearity": (field(round(lin, 6), reference=REFERENCE["linearity"])
                          if lin is not None else
                          field(None, null_reason="too_few_pixels",
                                reference=REFERENCE["linearity"])),
            "orientation_deg": (field(round(ang, 3),
                                      reference=REFERENCE["orientation_deg"])
                                if ang is not None else
                                field(None, null_reason="too_few_pixels",
                                      reference=REFERENCE["orientation_deg"])),
            "completeness": compl,
            "threshold_scan": scan,
            "split_by_watershed": field(split,
                                        reference=REFERENCE["split_by_watershed"]),
        })

    objects.sort(key=lambda o: o["envelope_area_km2"]["value"], reverse=True)
    return {"enabled": True, "thresholds": th, "objects": objects,
            "domain": dom_report, "note": None}
