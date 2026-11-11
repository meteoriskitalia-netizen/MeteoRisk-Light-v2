#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 2 (morphology, vertical structure, environment NWP, lightning, overshooting top)

Package Fase 2 sperimentale: estende il descrittore radar Fase 1 (convective_structure) con
layer ADDITIVI che usano prodotti aggiuntivi (VIL/ETM/POH/CAPPI, satellite WV/IR,
fulmini WMS) e ambiente NWP (Open-Meteo). Nessun layer della Fase 1 viene
modificato: l'indice v1 resta invariato, l'indice v2 (aggregate.aggregate_ssi_v2)
combinia i pesi espliciti documentati nel modulo aggregate.

Moduli:
  hook              - detection uncino (hook echo) da campo riflettivita'
  vertical_structure- strutture verticali (VIL, echo top, POH, overhang)
  environment       - indici ambientali NWP (SCP/STP/SHIP, SRH, CAPE/CIN)
  lightning         - vecchio feed Blitz v2 DPC (LEGACY, 403 su 12/12 slot:
                      resta come sonda singola/fallback dichiarato)
  lightning_afa     - fulmini da EUMETSAT MTG-I AFA (mtg_fd:li_afa): area con
                      scariche accumulate nel raggio del candidato, NON un
                      conteggio di scariche (sorgente primaria dal 2026-11-11)
  overshoot         - overshooting top da BTD WV-IR (WV non disponibile DPC)
  satellite_ot      - overshooting top da IR_108 DPC (proxy IRW-texture)
  aggregate         - indice v2 = combinazione pesata dei layer Fase 2

Tutte le funzioni di calcolo sono PURE (nessuna I/O, nessuno stato condiviso,
determinismo): l'I/O vive solo nei wrapper fetch_* e solleva eccezioni locali
allineabili a radar_engine.models.SourceError a integrazione.

EXPERIMENTAL: soglie e pesi non calibrati su dataset reali (v. report fase A1).

B2 — COMPONENTI PER CELLÀ (0.4.0): hook/struttura/ambiente/fulmini sono
calcolati SULLA FINESTRA LOCALE di ciascun candidato (footprint ±km) e non piu'
su una griglia globale condivisa; aggregate.aggregate_ssi_v2 ESCLUDE i
componenti None e RINORMALIZZA i pesi sui presenti (somma 1.00).
"""

PHASE2_VERSION = "0.6.0"   # OT da IR_108 (satellite_ot) attivo per candidato
