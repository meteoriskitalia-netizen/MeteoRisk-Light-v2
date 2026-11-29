#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cloud Type RGB EUMETSAT come layer nuvola (2026-11-29).

Prodotto gia' composto: suolo nero, nuvole colorate per tipo. Niente
fusioni con NWP: alpha = luminanza del prodotto.
"""
import os
import time
import urllib.parse
import urllib.request

import numpy as np

WMS = "https://view.eumetsat.int/geoserver/wms"
LAYER = "mtg_fd:rgb_cloudtype"
IMAGE_NAME = "nowcast_cloudtype.webp"


def build(grid, out_dir, width=512, timeout_s=45):
    info = {
        "source": LAYER, "available": False, "alpha_method": "luminance",
        "role": "context", "note": ("Cloud Type RGB EUMETSAT: suolo nero, "
                                   "nuvole colorate per tipo; overlay, non "
                                   "previsione di pioggia."),
        "image": None, "null_reason": None,
    }
    w = max(256, min(int(width), 1024))
    h = max(1, int(round(w * grid["height"] / grid["width"])))
    b = grid["bounds"]
    params = {
        "service": "WMS", "version": "1.3.0", "request": "GetMap",
        "layers": LAYER, "styles": "", "crs": "EPSG:3857",
        "bbox": "%.3f,%.3f,%.3f,%.3f" % (b[0], b[1], b[2], b[3]),
        "width": str(w), "height": str(h), "format": "image/png",
        "transparent": "false",
    }
    url = WMS + "?" + urllib.parse.urlencode(params)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=int(timeout_s)) as r:
            png = r.read()
    except Exception as exc:
        info["null_reason"] = "wms_error:%s" % exc.__class__.__name__
        info["fetch_s"] = round(time.perf_counter() - t0, 2)
        return info
    info["fetch_s"] = round(time.perf_counter() - t0, 2)
    info["png_bytes"] = len(png)
    info["width"], info["height"] = w, h
    try:
        from io import BytesIO
        from PIL import Image
        img = Image.open(BytesIO(png)).convert("RGB")
        rgb = np.asarray(img, np.float32)
        lum = (0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] +
               0.0722 * rgb[:, :, 2]) / 255.0
        alpha = np.clip(lum, 0.0, 1.0) * 255.0
        out = np.dstack([rgb.astype(np.uint8),
                         alpha.astype(np.uint8)])
        path = os.path.join(out_dir, IMAGE_NAME)
        Image.fromarray(out, "RGBA").save(path, "WEBP", quality=85,
                                          method=6)
        info.update({"available": True, "image": IMAGE_NAME,
                     "output_bytes": os.path.getsize(path),
                     "image_size": [img.width, img.height],
                     "cloud_fraction": round(float((alpha > 8).mean()), 4)})
    except Exception as exc:
        info["null_reason"] = "image_error:%s" % exc.__class__.__name__
    return info
