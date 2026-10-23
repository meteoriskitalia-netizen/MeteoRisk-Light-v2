#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — supercell.py (ALIAS DI COMPATIBILITA', deprecato)

Il modulo e' stato rinominato `convective_structure.py` nella 1.2.4.17: il
dato DPC e' a singola riflettivita', quindi la rotazione non e' misurabile e
nessun campo puo' chiamarsi come una diagnosi che il dato non contiene. Questo
file esiste SOLO per non rompere gli import storici:

    from radar_engine import supercell as sc_mod
    sc_mod.evaluate(bundle, cfg)
    sc_mod.supercell_signature_index(track)

Nessuna logica vive qui: ogni funzione e' quella di convective_structure.
"""

from .convective_structure import (          # noqa: F401
    CONF_BANDS,
    CONF_WEIGHTS,
    DATA_LIMITS_NOTE,
    ROTATION_NULL_REASON,
    classify_level,
    dedup_records,
    derive_confidence,
    evaluate,
    structural_organization_index,
    supercell_signature_index,
)

# Nome storico dell'indice: NON usare nel codice nuovo (e' un alias).
SSI_LEGACY_NOTE = (
    "l'indice 'SSI' della 1.2.4.16 e precedenti si chiama ora "
    "structural_organization_index (soi)"
)
