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


def _smoothstep(edge0, edge1, x):
    t = np.clip((np.asarray(x, np.float32) - float(edge0)) /
                (float(edge1) - float(edge0)), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def rgba_from_classes(classes):
    """Classi discrete -> RGBA (la resa VECCHIA, tenuta per confronto/test)."""
    c = np.asarray(classes, np.uint8)
    out = np.zeros(c.shape + (4,), np.uint8)
    for cls in CLASSES:
        m = c == int(cls["id"])
        if not bool(m.any()):
            continue
        out[m, 0], out[m, 1], out[m, 2] = cls["rgb"]
        out[m, 3] = int(cls["alpha"])
    return out


def rgba_from_temp(temp_c, valid, blend_c=2.0, blur_px=0.7):
    """°C -> RGBA con TRANSIZIONI MORBIDE fra le classi.

    Il bordo netto di una classificazione a soglie produce gradini visibili
    (e il nearest li rende blocchi). Qui:
      - l'alpha sale con smoothstep su 2 °C attorno a -20 °C;
      - il colore passa da classe 1 a 2 a 3 con smoothstep su 2 °C attorno
        a -32 e -44 °C;
      - un blur leggero (default 0,7 px) viene applicato SOLO a questa
        immagine (premultiplied, niente aloni), non alla pioggia.
    """
    t = np.asarray(temp_c, np.float32)
    v = np.asarray(valid, bool) & np.isfinite(t)
    cold = -t
    h = max(float(blend_c), 0.1) / 2.0
    w0 = _smoothstep(20.0 - h, 20.0 + h, cold)
    w2 = _smoothstep(32.0 - h, 32.0 + h, cold)
    w3 = _smoothstep(44.0 - h, 44.0 + h, cold)
    c1 = np.array(CLASSES[0]["rgb"], np.float32)
    c2 = np.array(CLASSES[1]["rgb"], np.float32)
    c3 = np.array(CLASSES[2]["rgb"], np.float32)
    # COLORE A 3 CLASSI (palette separata, peso contenuto): la sfumatura
    # vera sta nell'ALPHA, che e' cio' che fa vedere il bordo morbido. Con il
    # colore interpolato su 256 livelli il WebP passava da 6 kB a 63 kB.
    rgb = np.empty(t.shape + (3,), np.float32)
    rgb[:, :] = c1
    rgb[w2 > 0.5] = c2
    rgb[w3 > 0.5] = c3
    alpha = 90.0 * w0 + 30.0 * w2 + 30.0 * w3
    alpha = np.clip(alpha, 0.0, 150.0)
    alpha[~v] = 0.0
    rgb[~v] = 0.0
    if float(blur_px) > 0:
        from scipy.ndimage import gaussian_filter
        alpha = np.clip(gaussian_filter(alpha.astype(np.float32),
                                        float(blur_px)), 0.0, 150.0)
    # 10 livelli di alpha (passo 16): con 150 livelli il canale alpha del
    # WebP faceva 66 kB; a 10 livelli il bordo resta sfumato a opacita' bassa
    # e il file torna piccolo.
    alpha = np.round(alpha / 16.0) * 16.0
    out = np.zeros(rgb.shape[:2] + (4,), np.uint8)
    out[:, :, :3] = np.clip(rgb + 0.5, 0, 255).astype(np.uint8)
    out[:, :, 3] = np.clip(alpha + 0.5, 0, 255).astype(np.uint8)
    return out


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
        # RESA MORBIDA (2026-11-27): scala continua, resampling bilineare,
        # blur leggero solo sul canale nuvole. Dichiarati nel payload.
        "resampling": "bilinear", "blend_c": 2.0, "blur_px": 0.7,
        "downsample": 2, "image_size": None,
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
        _cfg = dict(config or {})
        _blend = float(_cfg.get("blend_c")
                       if _cfg.get("blend_c") is not None else 2.0)
        _blur = float(_cfg.get("blur_px")
                      if _cfg.get("blur_px") is not None else 0.7)
        _down = int(_cfg.get("downsample")
                    if _cfg.get("downsample") is not None else 2)
        info.update({
            "available": True, "null_reason": None,
            "slot": _iso(int(rd.time_ms)),
            "latency_s": round(latency_s, 1),
            "latency_min": round(latency_s / 60.0, 1),
            "image": IMAGE_NAME,
            "cloud_pixels": int((classes > 0).sum()),
            "cold_pixels": int((classes >= 3).sum()),
            "resampling": "bilinear",
            "blend_c": round(_blend, 2),
            "blur_px": round(_blur, 2),
            "downsample": max(1, _down),
        })
        return {"classes": classes, "temp": temp, "valid": valid, "rd": rd,
                "info": info}
    except Exception as exc:
        warnings.append("clouds: build failed ({}:{})".format(
            exc.__class__.__name__, exc))
        info["null_reason"] = "build_error:{}".format(
            exc.__class__.__name__)
        return {"info": info}


def write_webp(path, data, quality=80):
    """Classi 0/1/2/3 oppure RGBA -> WebP RGBA lossless.

    Le classi discrete servono ai test e al confronto "prima"; la produzione
    passa un RGBA gia' sfumato (`rgba_from_temp`).
    """
    from PIL import Image
    arr = np.asarray(data)
    out = rgba_from_classes(arr) if arr.ndim == 2 else arr.astype(np.uint8)
    img = Image.fromarray(np.ascontiguousarray(out), "RGBA")
    # lossless: le classi/gli RGB devono restare esatti (4 colori piatti o
    # sfumatura controllata; il file resta piccolo). Con lossy l'alpha
    # veniva alterato.
    img.save(path, "WEBP", lossless=True, method=6)
    return int(__import__("os").path.getsize(path))


def write_rgba_webp(path, rgba, quality=82, downsample=1):
    """RGBA sfumato -> WebP lossless, con downsample opzionale.

    Con il colore interpolato su 256 livelli il lossless esplodeva (63-78
    kB contro 6 kB). Con 3 colori + alpha quantizzato a 16 livelli e
    downsample 2 il file e' 18 kB (misurato 2026-11-27) e resta senza
    perdita. Il client stira il layer (~3 km) con interpolazione.
    """
    from PIL import Image
    arr = np.asarray(rgba, np.uint8)
    img = Image.fromarray(np.ascontiguousarray(arr), "RGBA")
    d = max(1, int(downsample))
    if d > 1:
        img = img.resize((max(1, img.width // d), max(1, img.height // d)),
                         Image.BOX)
    # 10 livelli di alpha + 3 colori: il lossless ora comprime bene e non
    # altera il bordo. Se cresce, si torna a lossy (dichiarato nel report).
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
