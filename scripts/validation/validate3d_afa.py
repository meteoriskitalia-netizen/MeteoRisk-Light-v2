#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Etichetta INDIPENDENTE per la validazione 3D: EUMETSAT MTG LI AFA.

Layer WMS anonimo `mtg_fd:li_afa` (Accumulated Flash Area), archivio
2025-05-30 -> oggi, passo 5 min. Il decoder PNG e la semantica del layer NON
sono riscritti qui: si importano da `radar_engine.phase2.lightning_afa` e
`lightning` (unica fonte di verita' del progetto).

SEMANTICA — dichiarata, non embellita:
  * AFA e' l'AREA coperta da emissione ottica di scariche accumulate, non un
    conteggio di fulmini e NON e' un'etichetta di "organizzazione" ne' di
    "conferma" di nulla. E' un proxy indipendente di convezione ELETTRIFICATA.
  * `value=True`: almeno un pixel AFA attivo entro `radius_km` dal centroide
    della cella in almeno uno slot della finestra.
  * `value=False`: tutti gli slot della finestra sono stati serviti
    correttamente e nessuno ha pixel attivi entro il raggio.
  * `value=None`: finestra incompleta — con `null_reason` esplicito.
  * Nessuna parola "confermato": il campo si chiama `label_afa`.

TOLLERANZA ±30 min (scelta, giustificata):
  1. l'eco radar e l'emissione ottica non sono simultanei: il lampo puo'
     precedere o seguire il massimo di riflettivita' di alcuni minuti;
  2. il layer AFA accumula su 30 s per granulo, ma la disponibilita' reale
     dei granuli non e' continua (nearestValue puo' servire uno slot vicino);
  3. una finestra di ±5 min (3 slot) darebbe falsi "nessun lampo" quando lo
     slot esatto e' assente. ±30 min (13 slot) e' abbastanza ampia da
     assorbire questo, ma abbastanza stretta da non accoppiare temporali
     distinti (gli eventi sono separati da >=3 ore, vedi validate3d_events).
Il costo e' dichiarato nel piano di raccolta; la cache per slot evita di
scaricare due volte lo stesso granulo per celle diverse.

Nessun dato simulato: tutti i valori derivano da risposte HTTP reali.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request

UTC = _dt.timezone.utc
AFA_PERIOD_MS = 5 * 60 * 1000
DEFAULT_WINDOW_MIN = 30
DEFAULT_RADIUS_KM = 25.0
DEFAULT_TOLERANCE_MIN = 5.0  # scarto massimo ammesso tra richiesto e servito
DEFAULT_THROTTLE_S = 3.0

SOURCE = ("EUMETSAT EUMETView WMS mtg_fd:li_afa (MTG-I Lightning Imager, "
          "Accumulated Flash Area)")
REFERENCE = ("AFA = area con scariche accumulate; proxy indipendente di "
             "convezione elettrificata, NON etichetta di organizzazione e NON "
             "conteggio di fulmini (legenda senza valori numerici)")
WINDOW_JUSTIFICATION = (
    "finestra +/-30 min (13 slot da 5 min): assorbe il disallineamento "
    "radar-ottico, l'accumulo 30 s del granulo e l'eventuale nearestValue; "
    "piu' stretta di un evento (gap minimo 3 ore)")

_LAST_HTTP_MONO = None


def slot_floor_ms(ts_ms, period_ms=AFA_PERIOD_MS):
    """Ultimo multiplo di 5 min <= ts_ms (UTC)."""
    p = int(period_ms)
    return (int(ts_ms) // p) * p


def window_slots(ts_ms, window_min=DEFAULT_WINDOW_MIN,
                 period_ms=AFA_PERIOD_MS):
    """Slot AFA da considerare: [t-window, t+window], multipli di 5 min."""
    centre = slot_floor_ms(ts_ms, period_ms)
    n = int(round(float(window_min) * 60000.0 / float(period_ms)))
    return [centre + k * int(period_ms) for k in range(-n, n + 1)]


def _iso(ms):
    return _dt.datetime.fromtimestamp(int(ms) / 1000.0, UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _ms(iso):
    s = str(iso).replace("Z", "+00:00")
    return int(_dt.datetime.fromisoformat(s).timestamp() * 1000)


def _modules():
    """Import LAZY dei decoder ufficiali (unica fonte di verita')."""
    from radar_engine.phase2 import lightning_afa as afa
    from radar_engine.phase2 import lightning as ltg
    return afa, ltg


def fetch_slot(ts_ms, *, cache_dir=None, throttle_s=DEFAULT_THROTTLE_S,
               timeout_s=30, bbox=None, grid=1300):
    """Scarica (o rilegge dalla cache) UNO slot AFA e lo decodifica.

    Ritorna dict con `status` ("ok"|"slot_missing"|"error"), `frame`
    (decoded, solo se ok), `served_iso`, `served_offset_min`, `sha256`,
    `null_reason`. Non solleva: un guasto e' un valore null con motivo.
    """
    global _LAST_HTTP_MONO
    afa, _ltg = _modules()
    slot = slot_floor_ms(ts_ms)
    requested_iso = afa.epoch_to_iso(slot)
    out = {
        "requested_iso": requested_iso, "served_iso": None,
        "served_offset_min": None, "status": "error", "frame": None,
        "sha256": None, "bytes": 0, "from_cache": False,
        "null_reason": None, "http_status": None,
    }
    bb = tuple(bbox) if bbox else afa.AFA_BBOX
    sidecar_path = png_path = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        safe = requested_iso.replace(":", "-")
        png_path = os.path.join(cache_dir, safe + ".png")
        sidecar_path = os.path.join(cache_dir, safe + ".json")
        if os.path.isfile(png_path) and os.path.isfile(sidecar_path):
            try:
                with open(sidecar_path, "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
                if meta.get("status") == "ok" and meta.get("sha256"):
                    with open(png_path, "rb") as fh:
                        content = fh.read()
                    if hashlib.sha256(content).hexdigest() == meta["sha256"]:
                        out.update({
                            "status": "ok", "frame": afa.decode_afa_png(
                                content, bbox=bb, width=grid, height=grid),
                            "served_iso": meta.get("served_iso"),
                            "served_offset_min": meta.get("served_offset_min"),
                            "sha256": meta["sha256"], "bytes": len(content),
                            "from_cache": True, "http_status": 200,
                        })
                        return out
            except Exception:
                pass  # cache corrotta: si riscarica, nessun valore inventato

    url = afa.build_afa_getmap_url(requested_iso, bbox=bb,
                                   width=grid, height=grid)
    try:
        if _LAST_HTTP_MONO is not None and throttle_s > 0:
            wait = float(throttle_s) - (time.monotonic() - _LAST_HTTP_MONO)
            if wait > 0:
                time.sleep(wait)
        req = urllib.request.Request(url, headers={
            "User-Agent": "opencode-meteorisk-validation/1.0",
            "Accept": "image/png"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            content = resp.read()
            status = resp.status
            warning = resp.headers.get("Warning")
        _LAST_HTTP_MONO = time.monotonic()
        out["http_status"] = status
        out["bytes"] = len(content)
    except Exception as exc:
        out["null_reason"] = "afa_http_error:{}".format(
            exc.__class__.__name__)
        return out

    code = afa._service_exception_code(content)
    if code:
        out.update({"status": "slot_missing",
                    "null_reason": "afa_service_exception:{}".format(code)})
        return out
    served = afa.parse_served_iso(warning) or requested_iso
    offset_min = abs(_ms(served) - slot) / 60000.0
    try:
        frame = afa.decode_afa_png(content, bbox=bb, width=grid, height=grid)
    except Exception as exc:
        out["null_reason"] = "afa_decode_error:{}".format(
            exc.__class__.__name__)
        return out
    sha = hashlib.sha256(content).hexdigest()
    out.update({"status": "ok", "frame": frame, "served_iso": served,
                "served_offset_min": round(offset_min, 3), "sha256": sha})
    if png_path and sidecar_path:
        try:
            with open(png_path, "wb") as fh:
                fh.write(content)
            with open(sidecar_path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"requested_iso": requested_iso,
                           "served_iso": served,
                           "served_offset_min": out["served_offset_min"],
                           "sha256": sha, "bytes": len(content),
                           "status": "ok"}, fh, indent=2)
        except OSError:
            pass  # cache best-effort: il dato in memoria resta valido
    return out


def label_cell(lat, lon, ts_ms, *, window_min=DEFAULT_WINDOW_MIN,
               radius_km=DEFAULT_RADIUS_KM, tolerance_min=DEFAULT_TOLERANCE_MIN,
               cache_dir=None, throttle_s=DEFAULT_THROTTLE_S,
               fetcher=None, period_ms=AFA_PERIOD_MS, grid=1300):
    """Etichetta AFA per UNA cella. `fetcher` e' injection per i TEST.

    Ritorna sempre `label_afa` con value True/False/None e i motivi. Non
    scrive nulla fuori da `cache_dir` (che e' una cache, non un dato grezzo
    versionato).
    """
    afa, _ltg = _modules()
    slots = window_slots(ts_ms, window_min=window_min, period_ms=period_ms)
    getter = fetcher or (lambda s: fetch_slot(
        s, cache_dir=cache_dir, throttle_s=throttle_s, grid=grid))
    slots_out = []
    max_off = 0.0
    any_active = False
    n_active_pixels = 0
    for s in slots:
        rec = getter(s)
        status = rec.get("status")
        off = rec.get("served_offset_min") or 0.0
        max_off = max(max_off, float(off))
        usable = (status == "ok" and rec.get("frame") is not None
                  and float(off) <= float(tolerance_min))
        active = False
        if usable:
            try:
                c = afa.count_within(rec["frame"], float(lon), float(lat),
                                     float(radius_km))
                active = bool(c.get("active"))
                n_active_pixels = max(n_active_pixels,
                                      int(c.get("active_pixels") or 0))
            except Exception:
                usable = False
        if active:
            any_active = True
        slots_out.append({
            "requested_iso": rec.get("requested_iso") or _iso(s),
            "status": status, "usable": bool(usable),
            "active": active, "served_iso": rec.get("served_iso"),
            "served_offset_min": rec.get("served_offset_min"),
            "null_reason": rec.get("null_reason"),
        })
    n_ok = sum(1 for s in slots_out if s["usable"])
    if any_active:
        value, reason = True, None
    elif n_ok == len(slots_out):
        value, reason = False, None
    else:
        value, reason = None, "afa_window_incomplete:{}/{}".format(
            n_ok, len(slots_out))
    return {
        "label_afa": value,
        "label_null_reason": reason,
        "source": SOURCE,
        "reference": REFERENCE,
        "method": "any AFA active pixel within radius in +/-window",
        "window_min": float(window_min),
        "tolerance_min": float(tolerance_min),
        "radius_km": float(radius_km),
        "n_slots": len(slots_out),
        "n_usable_slots": n_ok,
        "max_served_offset_min": round(max_off, 3),
        "max_active_pixels": n_active_pixels,
        "window_justification": WINDOW_JUSTIFICATION,
        "slots": slots_out,
        "semantics": ("proxy di convezione elettrificata; NON etichetta di "
                      "organizzazione, NON 'confermato'"),
    }
