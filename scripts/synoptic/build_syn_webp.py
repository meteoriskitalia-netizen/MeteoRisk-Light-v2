#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Orchestratore del prodotto WebP sinottico SPERIMENTALE (1.3.0.18).

Due modalita':

1. VALIDA + INDICE (default, usata dal workflow):
   ``python -m scripts.synoptic.build_syn_webp --source data/syn_webp --out-dir out``
   Legge i 6 WebP reali consegnati e i loro sidecar, verifica sha256/dimensioni/
   dichiarazioni di onesta', costruisce ``index.json`` con la didascalia completa
   e copia SOLO i 7 file autorizzati (6 WebP + index.json) nella cartella di
   pubblicazione. Nessun dato inventato: se un controllo fallisce lo script
   esce con errore e NON pubblica nulla.

2. RENDER (produzione da .om reali):
   ``python -m scripts.synoptic.build_syn_webp --render --manifest URL \
       --frames URL1,URL2,... --out-dir out``
   Usa ``syn_webp_render.py`` (riutilizzo della pipeline esistente) e i frame
   .om dei branch fm2-*, letti via HTTP Range senza token. Il set renderizzato
   deve comunque passare gli stessi controlli prima di essere pubblicato.

Il prodotto e' SEMPRE dichiarato SPERIMENTALE: il motore fronti e'
``development_only`` e non esiste verita' indipendente (nessun POD/FAR/CSI).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

SCHEMA_VERSION = 1
EXPERIMENTAL_WARNING = "SPERIMENTALE — non validato, nessuna accuratezza misurata"
SOURCE_LABEL = "Open-Meteo .om / CC BY 4.0"
LICENSE = "CC BY 4.0 (attribuzione richiesta)"
ATTRIBUTION = "Open-Meteo — https://open-meteo.com/ (CC BY 4.0)"
PUBLICATION_STATUS = "development_only"
WEBP_SIZE = (962, 482)
N_EXPECTED = 6


class ValidationError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _webp_size(path: Path):
    """Dimensioni reali del WebP (VP8X/VP8/VP8L), senza dipendenze esterne."""
    b = path.read_bytes()
    if b[:4] != b"RIFF" or b[8:12] != b"WEBP":
        raise ValidationError("non e' un WebP: %s" % path.name)
    kind = b[12:16]
    if kind == b"VP8X":
        w = 1 + int.from_bytes(b[24:27], "little")
        h = 1 + int.from_bytes(b[27:30], "little")
    elif kind == b"VP8 ":
        w = int.from_bytes(b[26:28], "little") & 0x3FFF
        h = int.from_bytes(b[28:30], "little") & 0x3FFF
    elif kind == b"VP8L":
        bits = int.from_bytes(b[21:25], "little")
        w = (bits & 0x3FFF) + 1
        h = ((bits >> 14) & 0x3FFF) + 1
    else:
        raise ValidationError("chunk WebP sconosciuto %r: %s" % (kind, path.name))
    return w, h


def load_measurements(source: Path) -> dict:
    p = source / "MEASUREMENTS.json"
    if not p.is_file():
        raise ValidationError("MEASUREMENTS.json assente in %s" % source)
    return json.loads(p.read_text(encoding="utf-8"))


def validate_set(source: Path) -> tuple[list, dict]:
    """Verifica i 6 WebP e i sidecar. Ritorna (records, measurements)."""
    meas = load_measurements(source)
    records = meas.get("records") or []
    if len(records) != N_EXPECTED:
        raise ValidationError("attesi %d WebP, trovati %d" % (N_EXPECTED, len(records)))
    out = []
    for rec in records:
        name = rec["file"]
        wp = source / name
        side = source / rec["sidecar"]
        if not wp.is_file() or not side.is_file():
            raise ValidationError("file mancante: %s" % name)
        got = _sha256(wp)
        if got != rec["webp_sha256"]:
            raise ValidationError("sha256 diverso per %s: %s != %s" %
                                  (name, got, rec["webp_sha256"]))
        if wp.stat().st_size != rec["webp_bytes"]:
            raise ValidationError("byte diversi per %s" % name)
        size = _webp_size(wp)
        if size != WEBP_SIZE:
            raise ValidationError("dimensioni %s: %r != %r" % (name, size, WEBP_SIZE))
        sd = json.loads(side.read_text(encoding="utf-8"))
        if str(sd.get("publication_status", "")).find("development_only") < 0:
            raise ValidationError("sidecar senza development_only: %s" % name)
        if sd.get("blocks", {}).get("drawn") is not False:
            raise ValidationError("blocchi disegnati (TM90 non verificabile): %s" % name)
        src_s = str(sd.get("source", "")).lower()
        if "open-meteo" not in src_s or ".om" not in src_s:
            raise ValidationError("provenienza .om assente: %s" % name)
        if "CC BY 4.0" not in str(sd.get("license", "")):
            raise ValidationError("licenza CC BY 4.0 assente: %s" % name)
        ftypes = rec.get("front_types") or {}
        if ftypes.get("occluded", 0) or ftypes.get("frontogenesis", 0) or ftypes.get("frontolysis", 0):
            raise ValidationError("simbologia non convenzionale inattesa: %s" % name)
        out.append({
            "file": "data/syn_webp/" + name,
            "run": rec["run_reference_time"],
            "valid_time": rec["valid_time"],
            "lead_hours": int(rec["lead_hours"]),
            "caption": ("Run %s · valid time %s · lead +%dh · %s" %
                        (rec["run_reference_time"], rec["valid_time"],
                         int(rec["lead_hours"]), SOURCE_LABEL)),
            "source": SOURCE_LABEL,
            "license": LICENSE,
            "attribution": ATTRIBUTION,
            "fronts": int(rec["n_fronts"]),
            "pressure_centers": int(rec["n_pressure_centers"]),
            "blocks_drawn": False,
            "webp_bytes": int(rec["webp_bytes"]),
            "webp_sha256": got,
            "experimental": True,
            "experimental_warning": EXPERIMENTAL_WARNING,
            "publication_status": PUBLICATION_STATUS,
        })
    return out, meas


def build_index(source: Path) -> dict:
    frames, meas = validate_set(source)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "synoptic_webp_experimental",
        "generated_at": dt.datetime.now(dt.timezone.utc)
                        .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "status": "ok",
        "experimental": True,
        "experimental_warning": EXPERIMENTAL_WARNING,
        "publication_status": PUBLICATION_STATUS,
        "validation": "NONE — nessun POD/FAR/CSI, nessuna verita' indipendente",
        "source": SOURCE_LABEL,
        "license": LICENSE,
        "attribution": ATTRIBUTION,
        "run_reference_time": meas.get("run_reference_time"),
        "region": meas.get("region"),
        "frames": frames,
        "not_implemented": [{
            "id": "atmospheric_blocks",
            "status": "NON DISEGNATI",
            "reason": ("Un blocco richiede persistenza >=5 giorni e firma dinamica a "
                       "500 hPa: non verificabile da un singolo valid time. Il "
                       "precedente layer TM90 e' stato rimosso (geometria non "
                       "verificabile)."),
        }],
    }


# Path autorizzati: SOLO il prodotto WebP sinottico. Nessun altro file del
# checkout puo' finire sul branch cache (dimensione costante, 7 file).
ALLOWED = lambda f: (f.startswith("data/syn_webp/") and
                     (f.endswith(".webp") or f.endswith("/index.json")))


def publish(source: Path, out_dir: Path) -> int:
    idx = build_index(source)
    out_dir.mkdir(parents=True, exist_ok=True)
    # cartella di output ripulita: dimensione costante, nessun file residuo
    for f in out_dir.rglob("*"):
        if f.is_file():
            f.unlink()
    copied = []
    for rec in idx["frames"]:
        rel = rec["file"]
        if not ALLOWED(rel):
            raise ValidationError("path non autorizzato: %s" % rel)
        dst = out_dir / Path(rel).name
        shutil.copyfile(source / Path(rel).name, dst)
        copied.append(dst.name)
    idxf = out_dir / "index.json"
    if not ALLOWED("data/syn_webp/index.json"):
        raise ValidationError("index.json non autorizzato")
    idxf.write_text(json.dumps(idx, ensure_ascii=False, separators=(",", ":")) + "\n",
                    encoding="utf-8", newline="\n")
    print("pubblicati %d WebP + index.json in %s" % (len(copied), out_dir))
    print(" WARN: %s" % EXPERIMENTAL_WARNING)
    return len(copied)


def render_set(manifest: str, frames: list[str], out_dir: Path, domain: str) -> int:
    """Produzione reale da .om (locali o HTTP Range). Richiede le dipendenze
    gia' usate dal prototipo: numpy, Pillow, scipy, matplotlib, omfiles."""
    import syn_webp_render as rwr
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for i, frame in enumerate(frames):
        sidecar = out_dir / ("rendered_%02d.sidecar.json" % i)
        webp = out_dir / ("rendered_%02d.webp" % i)
        rc = rwr.main(["--frame", frame, "--manifest", manifest,
                       "--domain", domain, "--region", "syn", "--scale", "2",
                       "--out", str(webp), "--sidecar", str(sidecar)])
        if rc != 0:
            raise ValidationError("render fallito per %s" % frame)
        n += 1
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default="data/syn_webp",
                    help="cartella con i 6 WebP reali + sidecar + MEASUREMENTS.json")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--render", action="store_true",
                    help="renderizza da .om reali invece di validare il set consegnato")
    ap.add_argument("--manifest", help="latest.json (URL o locale), solo con --render")
    ap.add_argument("--frames", help="lista di .om separati da virgole, solo con --render")
    ap.add_argument("--domain", default="ecmwf_ifs025")
    args = ap.parse_args(argv)
    try:
        if args.render:
            if not args.manifest or not args.frames:
                raise ValidationError("--render richiede --manifest e --frames")
            n = render_set(args.manifest, [x.strip() for x in args.frames.split(",") if x.strip()],
                           Path(args.out_dir), args.domain)
            print("renderizzati %d frame in %s (NON pubblicati:Validare prima)" %
                  (n, args.out_dir))
        else:
            publish(Path(args.source), Path(args.out_dir))
    except (ValidationError, OSError, ValueError, json.JSONDecodeError) as exc:
        print("FATAL: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
