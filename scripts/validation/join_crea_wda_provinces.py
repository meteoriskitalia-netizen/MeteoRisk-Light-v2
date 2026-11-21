#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Arricchisce le etichette CREA WDA 'Hail' con il centroide della provincia.

SCELTA DI JOIN (dichiarata):
  Il CSV CREA ha 'CodProv' come INDICE SEQUENZIALE CREA (1=Torino, 2=Vercelli,
  3=Novara, ...), NON il CodProv ISTAT (Torino=101) e NON la SIGLA (TO). Il
  geojson `province_italiane.geojson` ha SOLO SIGLA / DEN_PROV / DEN_UTS: non
  c'e' alcun codice numerico a cui agganciare 'CodProv'. Il join usa quindi il
  NOME provincia: CSV 'Den_prov' -> geojson 'DEN_PROV' (casefold + strip).
  Il codice CREA resta nel record come 'province_code' (provenienza), e ogni
  record arricchito porta 'province_geometry_source'.

ONESTA': il centroide e' quello della geometria provinciale (AREA, non punto
di misura). Il campo 'geo_null_reason' del record originale viene RIMOSSO solo
perche' l'approssimazione e' dichiarata in 'province_geometry_source'. Il valore
non e' la posizione dell'evento: NON va usato per validazione spaziale puntuale.

Uso:
    python scripts/validation/join_crea_wda_provinces.py \
        --in labels.jsonl --geo data/geography/province_italiane.geojson \
        --out labels_geo.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple


def load_geojson(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("type") != "FeatureCollection":
        raise ValueError(f"geojson non e' FeatureCollection: {data.get('type')}")
    return data["features"]


def normalize_name(s: str) -> str:
    return " ".join((s or "").strip().upper().replace("À", "A").replace("È", "E").replace("Ì", "I").replace("Ò", "O").replace("Ù", "U").split())


def build_name_index(features: List[Dict[str, Any]]) -> Dict[str, Tuple[float, float, Dict[str, Any]]]:
    idx: Dict[str, Tuple[float, float, Dict[str, Any]]] = {}
    for ft in features:
        props = ft.get("properties") or {}
        name = normalize_name(props.get("DEN_PROV", ""))
        if not name:
            continue
        centroid = _centroid(ft.get("geometry"))
        if centroid is None:
            continue
        idx[name] = (centroid[0], centroid[1], props)
    return idx


def _centroid(geometry: Optional[Dict[str, Any]]) -> Optional[Tuple[float, float]]:
    """Centroide aritmetico dei vertici (approssimazione dichiarata)."""
    if not geometry:
        return None
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    pts: List[Tuple[float, float]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            if len(node) >= 2 and all(isinstance(x, (int, float)) for x in node[:2]):
                pts.append((float(node[0]), float(node[1])))
            else:
                for child in node:
                    walk(child)

    walk(coords)
    if not pts:
        return None
    lon = sum(p[0] for p in pts) / len(pts)
    lat = sum(p[1] for p in pts) / len(pts)
    # Scarta vertici sfuggiti di mano (geometrie non italiane / accordi di datum)
    if not (-20.0 < lat < 50.0 and -20.0 < lon < 40.0):
        return None
    return (round(lat, 6), round(lon, 6))


def load_records(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"JSONL non valido alla riga {lineno}: {exc}") from exc
    return out


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--in", dest="inp", required=True, help="JSONL in ingresso (non modificato)")
    p.add_argument("--geo", default="data/geography/province_italiane.geojson", help="geojson province")
    p.add_argument("--out", required=True, help="nuovo JSONL di output")
    args = p.parse_args(argv)

    if not os.path.isfile(args.inp):
        print(f"ERRORE: input non trovato: {args.inp}", file=sys.stderr)
        return 2
    if not os.path.isfile(args.geo):
        print(f"ERRORE: geojson non trovato: {args.geo}", file=sys.stderr)
        return 2

    features = load_geojson(args.geo)
    idx = build_name_index(features)
    records = load_records(args.inp)

    enriched: List[Dict[str, Any]] = []
    missing: List[str] = []
    lats: List[float] = []
    lons: List[float] = []
    for rec in records:
        new = dict(rec)
        name = normalize_name(rec.get("province_name", ""))
        hit = idx.get(name)
        if hit is None:
            missing.append(rec.get("province_name", ""))
            new["province_centroid"] = None
            new["province_geometry_source"] = None
            new["geo_null_reason"] = "province_name_not_found_in_geojson"
        else:
            lat, lon, props = hit
            new["province_centroid"] = {"lat": lat, "lon": lon}
            new["province_geometry_source"] = (
                f"{os.path.basename(args.geo)} | join=Den_prov->DEN_PROV (casefold) | "
                f"SIGLA={props.get('SIGLA', '')} | geometry_centroid_of_vertices (AREA proxy, "
                "NON punto evento)"
            )
            # La geometria e' nota: rimuoviamo il vecchio motivo di null grezzo,
            # sostituendolo con la dichiarazione esplicita di approssimazione.
            new.pop("geo_null_reason", None)
            lats.append(lat)
            lons.append(lon)
        enriched.append(new)

    parent = os.path.dirname(os.path.abspath(args.out))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        for rec in enriched:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    ok = len(records) - len(missing)
    print(f"Record in ingresso: {len(records)}")
    print(f"Join OK: {ok}; falliti: {len(missing)}")
    if missing:
        uniq = sorted(set(missing))
        print(f"Province mancanti ({len(uniq)}): {', '.join(uniq)}")
    if lats and lons:
        print(
            "Bounding box nazionale (centroidi): "
            f"lat [{min(lats):.4f}, {max(lats):.4f}] lon [{min(lons):.4f}, {max(lons):.4f}]"
        )
    print(f"Scritto: {args.out} (input NON modificato)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
