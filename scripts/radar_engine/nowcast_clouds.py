#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nuvole IR come CONTESTO del nowcast (2026-11-27).

Perche' esiste
--------------
Il nowcast parte dall'eco radar: una nube che cresce ma non piove ancora non
compare. Il prodotto DPC `IR_108` (GeoTIFF Float32, °C, 1200x1400 a 1 km,
stessa griglia TM della VMI/SRI) vede quella nube. Qui si costruisce un
INDICE DI NUVOLOSITA' a 3 classi e una WebP con una scala SEPARATA da quella
della pioggia (grigio-blu, mai verde/giallo/rosso/ciano).

Onesta'
-------
`role: "context"`: una nube alta e fredda puo' non piovere. Il campo NON e'
una previsione di pioggia e non viene mai sommato all'intensita' prevista.
Se IR_108 non e' disponibile il campo e' null con motivo; il nowcast resta.

Fonte misurata (2026-11-27):
- DPC IR_108, canale IR 10.8 um (MSG SEVIRI), 1 km, °C;
- slot 15:45Z disponibile alle 15:53Z -> latenza 8,1 min (misurata, non
  dichiarata dal fornitore);
- l'alternativa EUMETSAT (mtg_fd:ir105_hrfi) e' un WebP COLORATO, non un
  campo °C: non consente le soglie -20/-40 °C ed e' stata scartata per
  questo strato (manifest 15:25Z, generato 15:45Z: 28 min di ritardo).
"""

import numpy as np

SCHEMA = "meteorisk.nowcast_clouds/1"
SOURCE = "DPC_IR_108"
CHANNEL = "IR 10.8 um (MSG SEVIRI)"
RESOLUTION_M = 1000
ROLE = "context"
THRESHOLDS_C = (-20.0, -32.0, -44.0)
CLASSES = (
    {"id": 1, "min_c": -32.0, "max_c": -20.0, "label": "nube alta",
     "rgb": (170, 190, 210), "alpha": 90},
    {"id": 2, "min_c": -44.0, "max_c": -32.0, "label": "nube fredda",
     "rgb": (135, 160, 190), "alpha": 120},
    {"id": 3, "min_c": -90.0, "max_c": -44.0,
     "label": "nube molto fredda (convettiva)",
     "rgb": (100, 125, 165), "alpha": 150},
)
OPACITY = 0.70
NOTE = (
    "Nuvole IR (contesto): una nube alta e fredda puo' non piovere. "
    "Non e' una previsione di pioggia e non modifica l'intensita' prevista."
)
IMAGE_NAME = "nowcast_clouds.webp"


def classify(temp_c, valid, thresholds=THRESHOLDS_C):
    """°C -> classi 0/1/2/3 (0 = nessuna nube, fuori soglia o non valido)."""
    t = np.asarray(temp_c, np.float32)
    v = np.asarray(valid, bool) & np.isfinite(t)
    c = np.zeros(t.shape, np.uint8)
    c[v & (t < thresholds[0])] = 1
    c[v & (t < thresholds[1])] = 2
    c[v & (t < thresholds[2])] = 3
    return c


def palette_payload():
    return [dict(x) for x in CLASSES]


def _info_base():
    return {
        "schema": SCHEMA, "available": False, "null_reason": None,
        "source": SOURCE, "channel": CHANNEL, "resolution_m": RESOLUTION_M,
        "role": ROLE, "not_a_rain_forecast": True, "note": NOTE,
        "thresholds_c": list(THRESHOLDS_C), "classes": palette_payload(),
        "image": None, "image_crs": "EPSG:3857", "opacity": OPACITY,
        "under_rain": True, "slot": None, "latency_s": None,
        "latency_min": None, "published_pixels": None,
    }


def _from_bundle(bundle, slot_ms):
    entries = (getattr(bundle, "dpc_bundle", None) or {}).get("entries") or {}
    for _slot, by in entries.items():
        for key in ("ir108", "ir_108", "IR_108"):
            e = (by or {}).get(key)
            rd = (e or {}).get("raster")
            if rd is not None and int(rd.time_ms) == int(slot_ms):
                return rd
    return None


def build(bundle, config, clock, latest_ts, sri_rd, warnings=None):
    """Legge IR_108, costruisce le classi di nuvole e il blocco `clouds`.

    Ritorna {"classes", "valid", "rd", "info"} oppure {"info": ...} con
    `available:false` e motivo. Non solleva: il nowcast resta.
    """
    from . import fetch as fetch_mod
    warnings = warnings if warnings is not None else []
    info = _info_base()
    try:
        rd = _from_bundle(bundle, latest_ts)
        if rd is None:
            cfg = dict(config or {})
            frames, fwarn, _ts = fetch_mod.fetch_frames(
                cfg, product="IR_108", max_frames=1,
                anchor_ms=int(latest_ts))
            warnings.extend(w for w in (fwarn or []) if w)
            if not frames:
                info["null_reason"] = "ir108_unavailable"
                return {"info": info}
            rd = frames[-1]
        if int(rd.time_ms) != int(latest_ts):
            info["null_reason"] = "ir108_slot_mismatch"
            info["slot"] = _iso(int(rd.time_ms))
            return {"info": info}
        temp = np.asarray(rd.data, np.float32)
        valid = np.asarray(getattr(rd, "valid_mask", np.isfinite(temp)),
                           bool)
        if temp.shape != (int(sri_rd.rows), int(sri_rd.cols)):
            info["null_reason"] = "ir108_grid_mismatch"
            info["slot"] = _iso(int(rd.time_ms))
            return {"info": info}
        classes = classify(temp, valid)
        if not bool((classes > 0).any()):
            info["null_reason"] = "no_cloud_below_threshold"
            info["slot"] = _iso(int(rd.time_ms))
            return {"info": info}
        latency_s = max(0.0, (clock.now_ms() - int(rd.time_ms)) / 1000.0)
        info.update({
            "available": True, "null_reason": None,
            "slot": _iso(int(rd.time_ms)),
            "latency_s": round(latency_s, 1),
            "latency_min": round(latency_s / 60.0, 1),
            "image": IMAGE_NAME,
            "cloud_pixels": int((classes > 0).sum()),
            "cold_pixels": int((classes >= 3).sum()),
        })
        return {"classes": classes, "valid": valid, "rd": rd, "info": info}
    except Exception as exc:
        warnings.append("clouds: build failed ({}:{})".format(
            exc.__class__.__name__, exc))
        info["null_reason"] = "build_error:{}".format(
            exc.__class__.__name__)
        return {"info": info}


def write_webp(path, classes, quality=80):
    """Classi 0/1/2/3 -> WebP RGBA con la scala grigio-blu (mai pioggia)."""
    from PIL import Image
    c = np.asarray(classes, np.uint8)
    out = np.zeros(c.shape + (4,), np.uint8)
    for cls in CLASSES:
        m = c == int(cls["id"])
        if not bool(m.any()):
            continue
        out[m, 0], out[m, 1], out[m, 2] = cls["rgb"]
        out[m, 3] = int(cls["alpha"])
    img = Image.fromarray(np.ascontiguousarray(out), "RGBA")
    # lossless: le classi e gli RGB devono restare esatti (sono 4 colori
    # piatti, il file resta piccolo). Con lossy l'alpha veniva alterato.
    img.save(path, "WEBP", lossless=True, method=6)
    return int(__import__("os").path.getsize(path))


def payload(state):
    info = dict((state or {}).get("info") or _info_base())
    return info


def disabled_info(reason="disabled_by_config"):
    info = _info_base()
    info["null_reason"] = reason
    return info


def _iso(ms):
    import datetime as _dt
    return _dt.datetime.fromtimestamp(int(ms) / 1000.0,
                                      _dt.timezone.utc).strftime(
                                          "%Y-%m-%dT%H:%M:%SZ")
