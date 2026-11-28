#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sfondo Geo Colour RGB di giorno per la vista Nowcast (2026-11-28).

Il Geo Colour e' un'immagine OPACA a colori naturali: va sotto la pioggia,
non e' un overlay trasparente. Di notte non e' usabile e si resta con le
nuvole IR. Il bbox e' ESATTAMENTE quello della griglia del nowcast, cosi'
pioggia e sfondo si sovrappongono senza scarti.
"""
import datetime as _dt
import math
import os
import time
import urllib.parse
import urllib.request

import numpy as np

WMS = "https://view.eumetsat.int/geoserver/wms"
LAYER = "mtg_fd:rgb_geocolour"
IMAGE_NAME = "nowcast_background.webp"
SOLAR_ZENITH_DAY_DEG = 85.0


def solar_zenith_deg(ts_ms, lat, lon):
    """Zenit solare approssimato (NOAA), sufficiente per day/night."""
    dt = _dt.datetime.utcfromtimestamp(int(ts_ms) / 1000.0)
    doy = dt.timetuple().tm_yday
    decl = math.radians(23.44) * math.sin(
        2.0 * math.pi * (284 + doy) / 365.0)
    hour = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    ha = math.radians((hour + lon / 15.0 - 12.0) * 15.0)
    la = math.radians(lat)
    cosz = (math.sin(la) * math.sin(decl) +
            math.cos(la) * math.cos(decl) * math.cos(ha))
    return float(math.degrees(math.acos(max(-1.0, min(1.0, cosz)))))


def _grid_center_lonlat(grid):
    from pyproj import Transformer
    b = grid["bounds"]
    x = (b[0] + b[2]) / 2.0
    y = (b[1] + b[3]) / 2.0
    tr = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    return tr.transform(x, y)


def build(config, grid, ts_ms, out_dir):
    """Scarica il Geo Colour di giorno e lo scrive come sfondo opaco."""
    cfg = dict((config or {}).get("nowcast_background") or {})
    info = {
        "enabled": bool(cfg.get("enabled", True)),
        "available": False, "mode": None, "source": LAYER,
        "role": "context", "opaque": True,
        "note": ("Sfondo satellite visibile (Geo Colour), non un layer "
                 "trasparente; la pioggia resta sopra."),
        "image": None, "null_reason": None, "bbox": list(grid["bounds"]),
    }
    if not info["enabled"]:
        info["null_reason"] = "disabled_by_config"
        return info
    lon, lat = _grid_center_lonlat(grid)
    zen = solar_zenith_deg(ts_ms, lat, lon)
    info["solar_zenith_deg"] = round(zen, 2)
    info["center_lonlat"] = [round(lon, 3), round(lat, 3)]
    if zen >= SOLAR_ZENITH_DAY_DEG:
        info["mode"] = "night"
        info["null_reason"] = "night_ir_clouds"
        return info
    info["mode"] = "day"
    width = int(cfg.get("width") or 512)
    width = max(256, min(width, 1024))
    height = max(1, int(round(width * grid["height"] / grid["width"])))
    b = grid["bounds"]
    params = {
        "service": "WMS", "version": "1.3.0", "request": "GetMap",
        "layers": LAYER, "styles": "", "crs": "EPSG:3857",
        "bbox": "%.3f,%.3f,%.3f,%.3f" % (b[0], b[1], b[2], b[3]),
        "width": str(width), "height": str(height),
        "format": "image/png", "transparent": "false",
    }
    url = WMS + "?" + urllib.parse.urlencode(params)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=int(cfg.get("timeout_s")
                                                     or 45)) as resp:
            png = resp.read()
    except Exception as exc:
        info["null_reason"] = "wms_error:%s" % exc.__class__.__name__
        info["fetch_s"] = round(time.perf_counter() - t0, 2)
        return info
    info["fetch_s"] = round(time.perf_counter() - t0, 2)
    info["width"], info["height"] = width, height
    info["png_bytes"] = len(png)
    try:
        from PIL import Image
        tmp = os.path.join(out_dir, IMAGE_NAME + ".tmp.pkl")
        with open(tmp, "wb") as fh:
            fh.write(png)
        img = Image.open(tmp).convert("RGB")
        path = os.path.join(out_dir, IMAGE_NAME)
        img.save(path, "WEBP", quality=85, method=6)
        os.remove(tmp)
        info.update({"available": True, "image": IMAGE_NAME,
                     "output_bytes": os.path.getsize(path),
                     "image_size": [img.width, img.height]})
    except Exception as exc:
        info["null_reason"] = "image_error:%s" % exc.__class__.__name__
    return info
