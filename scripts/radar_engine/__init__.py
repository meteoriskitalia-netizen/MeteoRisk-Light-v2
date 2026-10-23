#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — Fase 1 (MVP Storm Detection & Tracking).

Motore quantitativo di rilevamento e tracciamento di celle convettive a partire
dal prodotto Radar-DPC VMI (GeoTIFF Float32, dBZ) scaricato via REST API ufficiale
(OPTION_A, Fase 0 — docs/RADAR_SOURCE_VERIFICATION.md).

Sorgente dati: Radar-DPC (Dipartimento della Protezione Civile), licenza
CC-BY-SA 4.0. Il motore pubblica SOLO output derivati (coordinate di celle,
geometrie, metriche, score, classificazioni) e mai immagini/tile/raw radar.

Classificazione Fase 1 (probabilistica, nessuna diagnosi di rotazione):
  - Weak Convective Cell [0-30]
  - Convective Cell [31-55]
  - Organized Convective Cell [56-75]
  - Highly Organized Convective Cell [76-100]
Nessun hook-echo/mesocyclone/rotation in Fase 1 (riferimenti Fase 2+).

Descrittore di struttura convettiva (1.2.4.17; ex indice 0.2.0):
il modulo convective_structure.py combina i descrittori Fase 1 (celle +
tracking + Organization Score) in uno structural_organization_index (SOI)
0-100. Il layer resta ad ESPLICITO GATE di intensità e dichiara
rotation_measurable=false (prodotto VMI a singola riflettività, nessun
Doppler). Output: data/radar/convective_structures.json (additivo).
"""

ENGINE_NAME = "Meteorisk Radar Engine"
ENGINE_VERSION = "0.3.0"
ENGINE_PHASE = 1