# -*- coding: utf-8 -*-
"""Rigenera la fixture del layer criticita' DPC da un bollettino reale.

Uso (i file originali NON sono nel repo: si scaricano dal DPC):

    python scripts/tests/hydro/gen_fixture.py \
        --index  /percorso/20261113_1434.json \
        --topo   /percorso/20261113_1434_today.json \
        --out    scripts/tests/hydro/fixtures

Tiene 7 zone (vedi fixtures/README.md), rimappa gli archi TopoJSON riferiti e
riscrive i link dell'indice su `fixtures/topo.json`. Nessun dato inventato:
proprieta' e geometrie sono quelle del bollettino, solo in sottoinsieme.
"""
import argparse
import json
import os

# zone scelte: coprono "nessuna", arancione idraulico+idrogeologico, giallo
# temporali e un MultiPolygon
WANT = [
    "Bacini Tordino Vomano",
    "Serchio-Garfagnana-Lima",
    "Bisenzio e Ombrone Pt",
    "Chiascio - Topino",
    "Marsica",
    "Litoranea",
    "Piana Sele e Alto Cilento",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", required=True)
    ap.add_argument("--topo", required=True)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    topo = json.load(open(args.topo, encoding="utf-8"))
    obj = list(topo["objects"].values())[0]
    by_name = {}
    for g in obj["geometries"]:
        by_name.setdefault(g["properties"]["Nome zona"], g)
    picked = [by_name[w] for w in WANT if w in by_name]
    missing = [w for w in WANT if w not in by_name]
    if missing:
        raise SystemExit("zone assenti nel bollettino: %s" % missing)
    print("zone scelte: %s" % [g["properties"]["Nome zona"] for g in picked])

    used = set()

    def collect(ids):
        for i in ids:
            used.add(i if i >= 0 else -i - 1)

    for g in picked:
        if g["type"] == "Polygon":
            for ring in g["arcs"]:
                collect(ring)
        else:
            for poly in g["arcs"]:
                for ring in poly:
                    collect(ring)
    order = sorted(used)
    remap = {old: new for new, old in enumerate(order)}

    def re(ids):
        return [remap[i] if i >= 0 else -remap[-i - 1] - 1 for i in ids]

    new_geoms = []
    for g in picked:
        ng = {"type": g["type"], "properties": g["properties"]}
        if g["type"] == "Polygon":
            ng["arcs"] = [re(r) for r in g["arcs"]]
        else:
            ng["arcs"] = [[re(r) for r in poly] for poly in g["arcs"]]
        new_geoms.append(ng)

    stamp = os.path.basename(args.topo).replace("_today.json", "")
    new_topo = {
        "type": "Topology",
        "arcs": [topo["arcs"][i] for i in order],
        "transform": topo["transform"],
        "objects": {stamp + "_today": {"type": "GeometryCollection", "geometries": new_geoms}},
    }
    p = os.path.join(args.out, "topo.json")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(new_topo, f, ensure_ascii=False, separators=(",", ":"))
    print("topo.json %d byte, archi %d" % (os.path.getsize(p), len(new_topo["arcs"])))

    idx = json.load(open(args.index, encoding="utf-8"))
    red = {
        "type": idx["type"],
        "date": idx["date"],
        "name": idx["name"],
        "global_legend": idx["global_legend"],
        "style": idx["style"],
        "today": {"topo_json": "fixtures/topo.json", "html_descrition": ""},
        "tomorrow": {"topo_json": "fixtures/topo.json", "html_descrition": ""},
    }
    p = os.path.join(args.out, "index.json")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(red, f, ensure_ascii=False, indent=1)
    print("index.json %d byte" % os.path.getsize(p))


if __name__ == "__main__":
    main()
