#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — history.py (archivio rolling 2h)

Archivio rolling DERIVATO a finestra di 25 slot da 5 minuti (2 ore), mantenuto
in data/radar/history/ (FUORI dai 4 file derivati committati su main):

    index.json            as_of + window_slots + slot ordinati (cronologico,
                          il piu' recente in fondo)
    slots/<SLOT>.json     payload dello scan: tracks (punto all'ultimo frame
                          per track viva) + record SOI su on_latest_frame

Pattern replicato dalla finestra rolling satellite (satellite_engine.py /
branch satellite-cache):
    - slot con floor a 5 min UTC, nome 'YYYY-MM-DDTHH-MM-00Z';
    - merge IDEMPOTENTE: slot gia' presente e valido -> saltato (nessuna
      riscrittura), slot assente/corrotto -> riscritto;
    - prune della finestra agli ultimi N slot + rimozione file orfani;
    - scrittura ATOMICA (tmp nella stessa dir + json.loads + os.replace).

L'archivio e' BEST-EFFORT: un suo errore non cambia mai l'rc del run
(main.py logga un warning e l'output derivato resta comunque scritto).
"""

import datetime as _dt
import hashlib
import json
import math
import os
import tempfile

from . import models

WINDOW_SLOTS = 25       # 25 slot * 5 min = 2 h (stessa finestra satellite)
SLOT_STEP_S = 300       # passo slot: 5 min
INDEX_NAME = "index.json"
SLOTS_DIR = "slots"
# 1.2.4.26 — CELLE PER FRAME (fetch incrementale). Sotto-directory separata da
# `slots/` (che contiene tracce+strutture dello scan): qui vive una voce per
# OGNI frame della finestra di tracking, non solo per l'ultimo.
# Perche' serve: il motore riscarica 6 frame VMI a ogni run ma solo 2 sono
# nuovi (l'ancora avanza di 10 minuti); le celle dei 4 frame gia' processati
# sono deterministiche e si possono riusare. MISURATO il 2026-10-24 sullo slot
# 12:40Z: celle ricostruite dall'archivio IDENTICHE campo per campo (0
# differenze su 183 celle) e stessi conteggi (183 celle, 33 track, 111 storm
# object, 8 strutture). Costo dell'archivio: 72,8 kB per 6 slot (73 kB/MB non
# compresso), ~300 kB per 25 slot (2 h).
CELLS_DIR = "cells"
# Campi della DetectedCell che l'archivio deve conservare: sono quelli letti da
# tracking.py (area_km2, max_dbz, mean_dbz, centroid_lonlat, cell_id,
# timestamp_ms), aggregation.py (area_km2, centroid_lonlat, solidity,
# compactness, max_dbz) e dai punti di TrackPoint (tutti). I float sono scritti
# a piena precisione: json usa repr(), che per i float Python fa round-trip
# esatto, quindi la cella ricostruita e' identica bit per bit (nessun
# arrotondamento che possa spostare una soglia).
CELL_FIELDS = ("cell_id", "timestamp_ms", "timestamp_iso", "area_km2",
               "centroid_raster", "centroid_lonlat", "bbox", "max_dbz",
               "mean_dbz", "p90_dbz", "eccentricity", "solidity",
               "compactness", "pixel_count")


def _parse_iso(value):
    """datetime UTC da ISO-8601 ('...Z' o con offset) o da datetime."""
    if isinstance(value, _dt.datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            dt = _dt.datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("iso_non_valida:{}".format(value)) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)


def slot_name(iso):
    """Nome slot dello scan: floor a 5 min UTC ('YYYY-MM-DDTHH-MM-00Z')."""
    epoch = int(_parse_iso(iso).timestamp())
    epoch -= epoch % SLOT_STEP_S
    slot = _dt.datetime.fromtimestamp(epoch, tz=_dt.timezone.utc)
    return slot.strftime("%Y-%m-%dT%H-%M-%SZ")


def _index_empty():
    return {"as_of": None, "window_slots": WINDOW_SLOTS, "next_uid": 1,
            "slots": []}


def read_index(root):
    """index.json dell'archivio; default vuoto se assente/corrotto."""
    try:
        with open(os.path.join(root, INDEX_NAME), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return _index_empty()
    if not isinstance(data, dict) or not isinstance(data.get("slots"), list):
        return _index_empty()
    try:
        window = int(data.get("window_slots") or WINDOW_SLOTS)
    except (TypeError, ValueError):
        window = WINDOW_SLOTS
    try:
        next_uid = int(data.get("next_uid") or 1)
    except (TypeError, ValueError):
        next_uid = 1
    if next_uid < 1:
        next_uid = 1
    slots = sorted({str(s) for s in data["slots"] if isinstance(s, str)})
    return {"as_of": data.get("as_of"), "window_slots": window,
            "next_uid": next_uid, "slots": slots}


def read_slot(root, slot):
    """Payload di slots/<slot>.json; None se assente/corrotto."""
    path = os.path.join(root, SLOTS_DIR, "{}.json".format(slot))
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _atomic_write_text(path, text):
    """Scrive text in modo atomico (tmp nella stessa dir + os.replace)."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    json.loads(text)  # validazione JSON PRIMA della replace
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".history-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise models.OutputError("atomic_write_failed:{}".format(exc)) from exc


def _write_index(root, slots, window_slots, next_uid=None):
    """Riscrive index.json con slot ordinati (no-op se gia' aggiornato).

    next_uid=None preserva il contatore corrente (fallback 1 se assente)."""
    slots = sorted({str(s) for s in slots})
    window_slots = int(window_slots)
    current = read_index(root)
    if next_uid is None:
        next_uid = current["next_uid"]
    try:
        next_uid = int(next_uid)
    except (TypeError, ValueError):
        next_uid = current["next_uid"]
    if next_uid < 1:
        next_uid = 1
    if (os.path.exists(os.path.join(root, INDEX_NAME))
            and current["slots"] == slots
            and current["window_slots"] == window_slots
            and current["next_uid"] == next_uid):
        return current
    payload = {
        "as_of": models.utcnow_iso(),
        "window_slots": window_slots,
        "next_uid": next_uid,
        "slots": slots,
    }
    _atomic_write_text(os.path.join(root, INDEX_NAME),
                       json.dumps(payload, ensure_ascii=False))
    return payload


def merge_slot(root, slot_payload, next_uid=None):
    """Scrive/sovrascrive slots/<slot>.json (atomico) e aggiorna l'index."""
    slot = str(slot_payload["slot"])
    _atomic_write_text(os.path.join(root, SLOTS_DIR, slot + ".json"),
                       json.dumps(_safe(slot_payload), ensure_ascii=False))
    index = read_index(root)
    _write_index(root, list(index["slots"]) + [slot], index["window_slots"],
                 next_uid)


def prune_slots(root, keep=WINDOW_SLOTS):
    """Taglia la finestra agli ultimi `keep` slot e rimuove i file orfani."""
    keep = max(int(keep), 0)
    index = read_index(root)
    slots_dir = os.path.join(root, SLOTS_DIR)
    try:
        names = sorted(n for n in os.listdir(slots_dir)
                       if n.endswith(".json"))
    except OSError:
        names = []
    kept = sorted(index["slots"])[-keep:] if keep else []
    kept_set = set(kept)
    for name in names:
        if name[:-len(".json")] not in kept_set:
            try:
                os.unlink(os.path.join(slots_dir, name))
            except OSError:
                pass  # file gia' sparito: la pulizia prosegue
    if not names and not index["slots"]:
        return  # nessun archivio: non creare la directory
    _write_index(root, kept, keep)


def _round(value, ndigits):
    """float arrotondato; None se assente/non numerico/non finito."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return round(out, ndigits) if math.isfinite(out) else None


def _as_int(value):
    """int arrotondato; None se assente/non numerico/non finito."""
    out = _round(value, 6)
    return None if out is None else int(round(out))


def _safe(value):
    """Conversione ricorsiva in tipi JSON-safe (numpy -> nativi)."""
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "tolist"):
        return _safe(value.tolist())
    if hasattr(value, "item"):
        return _safe(value.item())
    return str(value)


def haversine_km(lon1, lat1, lon2, lat2):
    """Distanza haversine pura in km tra due punti lon/lat (gradi)."""
    radius = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2.0) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2)
    return 2.0 * radius * math.asin(min(1.0, math.sqrt(a)))


def _lonlat(payload):
    """(lon, lat) finiti da un dict con chiave 'lonlat', None se assenti."""
    coord = payload.get("lonlat") if isinstance(payload, dict) else None
    if not isinstance(coord, (list, tuple)) or len(coord) != 2:
        return None
    try:
        lon = float(coord[0])
        lat = float(coord[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lon) and math.isfinite(lat)):
        return None
    return lon, lat


def _dt_minutes(new_ms, prev_ms):
    """Minuti assoluti tra due timestamp; 5.0 se assenti/non finiti."""
    try:
        new = float(new_ms)
        prev = float(prev_ms)
    except (TypeError, ValueError):
        return 5.0
    if not (math.isfinite(new) and math.isfinite(prev)):
        return 5.0
    return abs(new - prev) / 60000.0


def _previous_slot(slots, current):
    """Ultimo slot dell'index strettamente precedente a current (o None)."""
    earlier = [s for s in slots if s < current]
    return earlier[-1] if earlier else None


def _apply_uids(payload, prev_payload, next_uid):
    """Assegna uid stabili alle track e track_uid ai record SOI.

    Associa ogni track nuova alla track piu' vicina (greedy nearest-first)
    dello slot precedente con lo stesso track_type e uid presente, entro una
    soglia di continuita' dipendente dal passo temporale. Ritorna il nuovo
    next_uid (avanzato solo per le nuove identita')."""
    tracks = payload.get("tracks") or []
    prev_tracks = (prev_payload or {}).get("tracks") or []
    dt_min = _dt_minutes(payload.get("radar_timestamp_ms"),
                         (prev_payload or {}).get("radar_timestamp_ms"))
    max_km = min(120.0, 20.0 + 3.0 * dt_min)

    candidates = []
    for new_idx, new_track in enumerate(tracks):
        new_coord = _lonlat(new_track)
        if new_coord is None:
            continue
        for prev_idx, prev_track in enumerate(prev_tracks):
            if prev_track.get("track_type") != new_track.get("track_type"):
                continue
            prev_uid = prev_track.get("uid")
            if prev_uid is None:
                continue
            prev_coord = _lonlat(prev_track)
            if prev_coord is None:
                continue
            dist = haversine_km(new_coord[0], new_coord[1],
                                prev_coord[0], prev_coord[1])
            if dist <= max_km:
                candidates.append((dist, new_idx, prev_idx, prev_uid))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))

    assigned = {}
    used_new = set()
    used_prev = set()
    for _dist, new_idx, prev_idx, prev_uid in candidates:
        if new_idx in used_new or prev_idx in used_prev:
            continue
        assigned[new_idx] = prev_uid
        used_new.add(new_idx)
        used_prev.add(prev_idx)

    for new_idx, new_track in enumerate(tracks):
        if new_idx in assigned:
            new_track["uid"] = assigned[new_idx]
        else:
            new_track["uid"] = next_uid
            next_uid += 1

    uid_by_key = {}
    for track in tracks:
        key = (track.get("track_type"), track.get("track_id"))
        if track.get("track_id") is None or track.get("uid") is None:
            continue
        uid_by_key.setdefault(key, track["uid"])
    for rec in payload.get("structures") or []:
        key = (rec.get("track_type"), rec.get("track_id"))
        rec["track_uid"] = uid_by_key.get(key)
    return next_uid


def _track_entry(track, latest_frame_index, track_type, uid=None):
    """Voce track sul frame piu' recente; None se non viva a quel frame."""
    if latest_frame_index is None:
        return None
    point = None
    for p in reversed(getattr(track, "points", None) or []):
        if p.frame_index == latest_frame_index:
            point = p
            break
    if point is None:
        return None
    motion = getattr(track, "motion", None) or {}
    return {
        "uid": _as_int(uid),
        "track_id": _as_int(getattr(track, "track_id", None)),
        "track_type": track_type,
        "status": getattr(track, "status", None),
        "lonlat": [_round(point.lonlat[0], 5), _round(point.lonlat[1], 5)],
        "area_km2": _round(point.area_km2, 2),
        "max_dbz": _round(point.max_dbz, 2),
        "mean_dbz": _round(point.mean_dbz, 2),
        "velocity_kmh": _round(motion.get("velocity_kmh"), 2),
        "direction_toward_deg": _round(motion.get("direction_toward_deg"), 1),
    }


def _structure_entry(candidate):
    """Voce del descrittore SOI sul frame corrente (intensity/motion dal bundle)."""
    position = candidate.get("position") or []
    if len(position) == 2:
        position = [_round(position[0], 5), _round(position[1], 5)]
    else:
        position = None
    return {
        "structure_id": candidate.get("structure_id"),
        "track_id": _as_int(candidate.get("track_id")),
        "track_type": candidate.get("track_type"),
        "track_uid": None,
        "soi": _as_int(candidate.get("soi")),
        "level": candidate.get("level"),
        "phase": candidate.get("phase"),
        "position": position,
        "intensity": candidate.get("intensity") or {},
        "motion": candidate.get("motion") or {},
    }


def build_slot_payload(bundle_like):
    """Payload dello scan corrente per slots/<SLOT>.json (None se assente).

    tracks: una voce per track VIVA all'ultimo frame (punto del frame piu'
    recente + velocity/direction dal motion radice); structures: soli record
    con on_latest_frame=True. I campi mancanti restano None/omessi (nessun
    dato inventato)."""
    ts_iso = getattr(bundle_like, "radar_timestamp_iso", None)
    if not ts_iso:
        return None
    cells_by_frame = getattr(bundle_like, "cells_by_frame", None) or []
    latest = len(cells_by_frame) - 1 if cells_by_frame else None
    if latest is None:
        pts = [p.frame_index
               for trk in (getattr(bundle_like, "tracks", None) or [])
               for p in (getattr(trk, "points", None) or [])]
        latest = max(pts) if pts else None
    tracks = []
    for trk in getattr(bundle_like, "tracks", None) or []:
        entry = _track_entry(trk, latest, "cell")
        if entry is not None:
            tracks.append(entry)
    for trk in getattr(bundle_like, "storm_tracks", None) or []:
        entry = _track_entry(trk, latest, "storm_object")
        if entry is not None:
            tracks.append(entry)
    structures = [_structure_entry(c)
                  for c in (getattr(bundle_like, "convective_structures",
                                     None) or [])
                  if c.get("on_latest_frame")]
    return _safe({
        "slot": slot_name(ts_iso),
        "radar_timestamp": ts_iso,
        "radar_timestamp_ms": getattr(bundle_like, "radar_timestamp_ms", None),
        "tracks": tracks,
        "structures": structures,
    })


def _slot_valid(payload, slot):
    """True se lo slot e' un payload leggibile e coerente con lo slot."""
    return (isinstance(payload, dict) and payload.get("slot") == slot
            and isinstance(payload.get("radar_timestamp"), str)
            and isinstance(payload.get("tracks"), list)
            and isinstance(payload.get("structures"), list))


def build_history(bundle_like, out_root, window_slots=WINDOW_SLOTS):
    """Archivio rolling: merge dello slot corrente + prune della finestra.

    IDEMPOTENTE (come satellite_engine): uno slot gia' presente e valido viene
    saltato senza riscrittura; slot assente/corrotto -> riscritto. Ritorna il
    nuovo index oppure None se non c'e' scan da archiviare."""
    payload = build_slot_payload(bundle_like)
    if payload is None:
        return None
    root = os.path.abspath(out_root)
    slot = payload["slot"]
    if _slot_valid(read_slot(root, slot), slot):
        index = read_index(root)
        if slot not in index["slots"] or index["window_slots"] != int(window_slots):
            _write_index(root, list(index["slots"]) + [slot], window_slots,
                         index["next_uid"])
    else:
        index = read_index(root)
        prev_slot = _previous_slot(index["slots"], slot)
        prev_payload = read_slot(root, prev_slot) if prev_slot else None
        next_uid = _apply_uids(payload, prev_payload, index["next_uid"])
        merge_slot(root, payload, next_uid)
    prune_slots(root, keep=window_slots)
    return read_index(root)


# ---------------------------------------------------------------------------
# 1.2.4.26 — CELLE PER FRAME (fetch incrementale)
# ---------------------------------------------------------------------------
def cells_signature(config):
    """Firma della configurazione che determina le CELLE (1.2.4.26).

    Se `detect` (soglia dBZ, aree, morfologia, connettivita'), i nodata del
    preprocess o il prodotto cambiano, le celle archiviate NON valgono piu':
    riusarle produrrebbe una sequenza che il codice attuale non genererebbe.
    La firma viaggia nel file e `read_cells` la confronta: mismatch -> None ->
    il frame viene riscaricato e ridetectato."""
    payload = {
        "product": (config.get("source") or {}).get("product_type"),
        "detect": (config.get("detect") or {}),
        "nodata": sorted((config.get("preprocess") or {})
                         .get("nodata_values") or []),
    }
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _cells_path(root, slot):
    return os.path.join(root, CELLS_DIR, "{}.json".format(slot))


def _cell_to_payload(cell):
    """Dict JSON-safe di una DetectedCell (float a piena precisione)."""
    out = {}
    for name in CELL_FIELDS:
        value = getattr(cell, name, None)
        if isinstance(value, tuple):
            value = list(value)
        out[name] = value
    return out


def _cell_from_payload(payload):
    """DetectedCell da payload; None se il record e' incompleto.

    Nessun valore inventato: un campo mancante rende la cella NON
    ricostruibile e il chiamante ricade sul download del frame."""
    if not isinstance(payload, dict):
        return None
    for name in ("cell_id", "timestamp_ms", "timestamp_iso", "area_km2",
                 "centroid_raster", "centroid_lonlat", "bbox", "max_dbz"):
        if payload.get(name) is None:
            return None
    try:
        return models.DetectedCell(
            cell_id=str(payload["cell_id"]),
            timestamp_ms=int(payload["timestamp_ms"]),
            timestamp_iso=str(payload["timestamp_iso"]),
            area_km2=float(payload["area_km2"]),
            centroid_raster=tuple(float(v) for v in payload["centroid_raster"]),
            centroid_lonlat=tuple(float(v) for v in payload["centroid_lonlat"]),
            bbox_raster=tuple(int(v) for v in payload["bbox"]),
            max_dbz=float(payload["max_dbz"]),
            mean_dbz=float(payload.get("mean_dbz") or 0.0),
            p90_dbz=float(payload.get("p90_dbz") or 0.0),
            eccentricity=float(payload.get("eccentricity") or 0.0),
            solidity=float(payload.get("solidity") or 0.0),
            compactness=float(payload.get("compactness") or 0.0),
            pixel_count=int(payload.get("pixel_count") or 0),
            frame_index=None,
        )
    except (TypeError, ValueError, KeyError):
        return None


def read_cells(root, slot_iso, signature=None):
    """Celle archiviate per lo slot; None se assenti/corrotte/non coerenti.

    `signature`: se presente, il file deve dichiarare la STESSA firma di
    configurazione (`cells_signature`), altrimenti le celle sono state prodotte
    con soglie/nodata diversi e non sono riusabili."""
    try:
        slot = slot_name(slot_iso)
    except ValueError:
        return None
    try:
        with open(_cells_path(root, slot), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("slot") != slot:
        return None
    if signature is not None and data.get("signature") != signature:
        return None
    rows = data.get("cells")
    if not isinstance(rows, list):
        return None
    cells = [_cell_from_payload(row) for row in rows]
    if any(c is None for c in cells):
        return None
    return cells


def write_cells(root, slot_iso, cells, signature=None):
    """Scrive (atomico, idempotente) le celle di UN frame dell'archivio.

    Ritorna il path scritto, o None se lo slot non e' valido. Il contenuto
    identico NON viene riscritto: `cells` sono deterministiche per slot, quindi
    il confronto testuale e' sufficiente e rende l'operazione idempotente."""
    slot = slot_name(slot_iso)
    payload = {"slot": slot, "radar_timestamp": slot,
               "signature": signature,
               "cells": [_cell_to_payload(c) for c in (cells or [])]}
    text = json.dumps(_safe(payload), ensure_ascii=False, separators=(",", ":"))
    path = _cells_path(root, slot)
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read() == text:
                return path
    except OSError:
        pass
    _atomic_write_text(path, text)
    return path


def prune_cells(root, keep):
    """Tiene solo gli ultimi `keep` slot dell'archivio celle (per nome slot).

    Nessun indice separato: il nome del file E' lo slot (ordinabile
    lessicograficamente), quindi l'archivio non puo' divergere da un indice."""
    keep = max(int(keep), 0)
    cdir = os.path.join(root, CELLS_DIR)
    try:
        names = sorted(n[:-len(".json")] for n in os.listdir(cdir)
                       if n.endswith(".json"))
    except OSError:
        return []
    kept = names[-keep:] if keep else []
    for name in names:
        if name not in set(kept):
            try:
                os.unlink(os.path.join(cdir, name + ".json"))
            except OSError:
                pass  # file gia' sparito: la pulizia prosegue
    return kept


def build_cells_archive(bundle_like, out_root, window_slots=WINDOW_SLOTS,
                        signature=None):
    """Archivia le celle di OGNI frame della finestra (best-effort).

    Serve a `fetch.fetch_window`: al run successivo i frame gia' processati si
    ricostruiscono da qui invece di essere riscaricati. `frame_slots_iso` e
    `cells_by_frame` devono essere allineati (li costruisce main._run sulla
    stessa timeline). Ritorna il numero di slot scritti."""
    cells_by_frame = getattr(bundle_like, "cells_by_frame", None) or []
    slots = getattr(bundle_like, "frame_slots_iso", None) or []
    if not cells_by_frame or len(slots) != len(cells_by_frame):
        return 0
    root = os.path.abspath(out_root)
    written = 0
    for slot_iso, cells in zip(slots, cells_by_frame):
        try:
            write_cells(root, slot_iso, cells, signature=signature)
            written += 1
        except (OSError, ValueError, models.OutputError):
            continue
    prune_cells(root, keep=window_slots)
    return written

