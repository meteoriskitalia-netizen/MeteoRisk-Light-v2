#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shim HTTP per ``OmFileReader.from_fsspec`` — Range HTTP, zero token.

Perche' esiste
--------------
``omfiles.OmFileReader.from_fsspec(fs_obj, path())`` e' il modo nativo per leggere
un frame .om remoto senza scaricarlo intero. Con ``fsspec.HTTPFileSystem`` pero'
l'adapter installato e' incompatibile con la versione di omfiles presente su questo
host (misurato il 2026-11-18: ``RuntimeError: Python I/O error TypeError:
HTTPFileSystem._cat_file() missing 1 required positional argument: 'url'``).

Questo modulo fornisce l'oggetto minimo che omfiles richiede davvero (misurato
chiamando ``from_fsspec`` con un oggetto tracciante):

    fs.size(path)                         -> int (Content-Length via HEAD)
    fs.cat_file(start=..., end=..., path=...) -> bytes

``end`` e' ESCLUSIVO (semantica slice Python), mentre HTTP Range e' inclusivo:
si richiede quindi ``bytes=start-(end-1)``. Con questa correzione la lettura remota
di ``geopotential_height_500hPa`` da un frame reale da 3.637.696 byte e' riuscita
con **11 richieste Range** e ha restituito l'array (281, 1440) con min 4915.0 /
max 5935.0, identico alla lettura locale dello stesso frame.

Nessuna credenziale, nessun file scritto, nessun dominio nuovo.
"""

from __future__ import annotations

import urllib.error
import urllib.request

UA = "MeteoRisk-Light synoptic producer/1.0 (public Open-Meteo .om, CC BY 4.0)"


class RangeHttpFS:
    """Adapter fsspec-minimo su HTTP Range per public raw.githubusercontent.com."""

    def __init__(self, timeout: float = 60.0) -> None:
        self._timeout = float(timeout)
        self.requests = 0

    @staticmethod
    def _request(url: str, method: str = "GET", range_header: str | None = None):
        req = urllib.request.Request(url, method=method)
        req.add_header("User-Agent", UA)
        if range_header:
            req.add_header("Range", range_header)
        return urllib.request.urlopen(req, timeout=10.0)

    def size(self, path: str) -> int:
        with self._request(path, method="HEAD") as resp:
            value = resp.headers.get("Content-Length")
            if value is None:
                raise OSError(f"HEAD {path}: Content-Length assente")
            return int(value)

    def cat_file(self, *args, **kwargs) -> bytes:
        # omfiles chiama con soli keyword: start, end, path.
        url = args[0] if args else (kwargs.get("url") or kwargs.get("path"))
        start = kwargs.get("start", args[1] if len(args) > 1 else 0)
        end = kwargs.get("end", args[2] if len(args) > 2 else None)
        if url is None:
            raise ValueError("cat_file: path mancante")
        start = int(start)
        if end is None:
            end = self.size(url)
        end = int(end)
        if end <= start:
            return b""
        self.requests += 1
        with self._request(url, range_header=f"bytes={start}-{end - 1}") as resp:
            data = resp.read()
        if len(data) != end - start:
            raise OSError(
                f"Range {start}-{end - 1}: attesi {end - start} byte, ricevuti {len(data)}"
            )
        return data

    # alias difensivo: alcune versioni di omfiles chiamano _cat_file.
    _cat_file = cat_file


def open_frame(spec: str, fs: RangeHttpFS | None = None):
    """Apri un frame .om da URL HTTP(S) o da path locale.

    Ritorna ``(reader, fs)``; ``fs`` e' None nel caso locale.
    """
    from omfiles import OmFileReader

    if spec.startswith("http://") or spec.startswith("https://"):
        fs = fs or RangeHttpFS()
        return OmFileReader.from_fsspec(fs, spec), fs
    return OmFileReader.from_path(spec), None
