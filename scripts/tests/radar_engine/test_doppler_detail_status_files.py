#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A3 — i dettagli Doppler 1.3 DEVONO esistere anche senza scan VRADH.

Questo e' il test di accettazione della patch MINIMALE consegnata separatamente
per `radar_engine/main.py::_write_1_3_detail` (main.py e' in mano ad un altro
agente e NON e' stato modificato in questa consegna).

Su main.py NON patchato il test FALLISCE: e' la dimostrazione del difetto
(`if not obj: continue` -> nessun file scritto), non un errore del test.
Con la patch applicata i due file esistono sempre e dichiarano lo stato.
"""
import json

from radar_engine import main as rmain

DETAILS = ("doppler_analysis.json", "mesocyclone_tracks.json")
NULL_REASON = "no_opera_vradh_data"


class _EmptyBundle:
    """Bundle senza alcun payload Doppler (nessuno scan VRADH disponibile)."""

    generated_at_iso = "2026-11-16T22:00:00Z"
    # intenzionalmente: nessun attributo doppler_analysis / mesocyclone_tracks


class _RealBundle:
    generated_at_iso = "2026-11-16T22:00:00Z"
    doppler_analysis = {"status": "ok", "couplets": [{"id": "c1"}]}
    mesocyclone_tracks = {"status": "ok", "tracks": [{"id": "t1"}]}


def test_a3_detail_files_exist_and_declare_null_reason(tmp_path):
    rmain._write_1_3_detail(_EmptyBundle(), str(tmp_path))
    for name in DETAILS:
        path = tmp_path / name
        assert path.exists(), (
            "{} non scritto: l'assenza di VRADH non e' dichiarata".format(name))
        raw = path.read_text(encoding="utf-8")
        assert "\r" not in raw
        doc = json.loads(raw)
        assert doc.get("status") == "unavailable", (name, doc)
        assert doc.get("available") is False, (name, doc)
        assert doc.get("null_reason") == NULL_REASON, (name, doc)
        assert doc.get("detail") == name, (name, doc)
        assert doc.get("generated_at_utc") == _EmptyBundle.generated_at_iso


def test_a3_real_payload_is_written_verbatim(tmp_path):
    """Il ripiego NON deve sovrascrivere un payload Doppler reale."""
    rmain._write_1_3_detail(_RealBundle(), str(tmp_path))
    for name, want in zip(DETAILS, (_RealBundle.doppler_analysis,
                                    _RealBundle.mesocyclone_tracks)):
        doc = json.loads((tmp_path / name).read_text(encoding="utf-8"))
        assert doc == want, (name, doc)


def test_a3_status_payload_is_serializable_and_atomic(tmp_path):
    rmain._write_1_3_detail(_EmptyBundle(), str(tmp_path))
    leftovers = [p.name for p in tmp_path.iterdir()
                 if p.name.startswith(".") and p.name.endswith(".tmp")]
    assert leftovers == [], "file temporanei non ripuliti: {}".format(leftovers)
    for name in DETAILS:
        json.loads((tmp_path / name).read_text(encoding="utf-8"),
                   parse_constant=lambda c: (_ for _ in ()).throw(
                       ValueError("costante non JSON: " + c)))
