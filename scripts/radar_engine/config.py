#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Meteorisk Radar Engine — config.py

Configurazione centralizzata del motore. Le soglie e i pesi sono espliciti e
marcati come EXPERIMENTAL DEFAULTS (non calibrati su dataset reali): vanno
validati retrospettivamente prima di qualunque uso operativo.

Il file può essere sovrascritto parzialmente con un JSON (opzione --config /
variabile RADAR_ENGINE_CONFIG): l'override è un merge RICORSIVO (i dict si
fondono a ogni profondità; scalari/liste sostituiti) sui blocchi (source,
preprocess, detect, tracking, storm, scoring, convective_structure, phase2,
output).
"""

import json
import os
import warnings as warnings_mod
from pathlib import Path

# FONTE UNICA del gate di continuita' degli storm object: la stessa tupla che
# usa il layer celle (forecast.SPEED_RANGE_KMH[1] = 110 km/h). Importarla qui
# evita una seconda copia del valore che potrebbe divergere.
from .forecast import SPEED_RANGE_KMH as _SPEED_RANGE_KMH

# ---------------------------------------------------------------------------
# Defaults — EXPERIMENTAL (valori iniziali di progetto, nessuna calibrazione)
# ---------------------------------------------------------------------------
EXPERIMENTAL = "EXPERIMENTAL DEFAULT — non calibrato su dati reali"

CONFIG = {
    "source": {
        # Unico punto a conoscenza degli endpoint DPC (v. source_dpc.py).
        "api_base": "https://radar-api.protezionecivile.it",
        "origin_header": "https://radar.protezionecivile.it",
        "product_type": "VMI",
        "history_frames": 6,        # finestra candidata: T-25m..T0 (VMI PT5M)
        # 1.2.4.26 — FETCH INCREMENTALE: i frame gia' processati dal run
        # precedente si ricostruiscono dalle CELLE archiviate in
        # data/radar/history/cells/ (branch radar-history) invece di essere
        # riscaricati. MISURATO (slot reale 2026-10-24T12:40Z): 6 frame = 3,09
        # MB, dei quali solo 2 nuovi (1,03 MB); celle ricostruite IDENTICHE
        # campo per campo (0 differenze su 183 celle) e stessi conteggi
        # (183 celle, 33 track, 111 storm object, 8 strutture).
        # `phase2_lookback_frames` = quanti slot recenti restano raster VERI:
        # `_hook_for_candidate` legge le ultime 3 griglie continue e con celle
        # ricostruite darebbe un valore diverso. Con 3: 3 frame scaricati, 3
        # riusati. `enabled=false` = comportamento storico (6 download).
        # Archivio assente -> scarica tutto comunque (nessun errore).
        "incremental_fetch": {
            "enabled": True,
            "phase2_lookback_frames": 3,
        },
        # 1.2.4.20 — cadenza del prodotto, usata SOLO dal replay (--as-of):
        # in modalita' live il passo arriva da `findLastProductByType`.
        # 300 s = cadenza PT5M misurata; nessun istante inventato.
        "period_s": 300,
        "http_timeout_s": 30,
        "download_timeout_s": 60,
        "max_download_retries": 2,
        # 1.2.4.26 — download PARALLELI. Misurato il 2026-10-25 sul motore
        # reale: 6 VMI 50,15 s -> 14,12 s (3,55x), 10 CAPPI 69,97 s -> 9,75 s
        # (7,17x), zero HTTP 429 a 6 connessioni. 1 = percorso seriale storico
        # (leva di rollback senza toccare il codice). Tetto duro nel modulo:
        # fetch.FETCH_WORKERS_MAX = 8.
        "fetch_workers": 6,
        "cache_dir": None,          # None -> scripts/radar_engine/temp
        "keep_raw_frames": False,   # esclude sempre il raw dal repo (gitignore)
    },
    # -----------------------------------------------------------------
    # 1.2.4.27 — MOSAICO MULTI-SORGENTE: OPERA CIRRUS (EUMETNET) come
    # SECONDA fonte accanto alla VMI Radar-DPC. Merge con provenienza per
    # pixel, nessun blend (v. mosaic.py). `enabled=false` = percorso storico
    # a sola sorgente DPC, byte-identico alla 1.2.4.26.
    # COSTO MISURATO il 2026-11-10 su 6 frame (slot reali 00Z..12Z):
    # vedi docs/OPERA_MOSAIC.md — 5 richieste Range e ~771 kB per frame,
    # ~1,6 s di apertura+lettura, ~0,15 s di riproiezione.
    "opera": {
        "enabled": True,
        "s3_base": "https://s3.waw3-1.cloudferro.com/openradar-24h/",
        # 2026-11-13 — ENDPOINT DI BACKUP, DOCUMENTATO DAL PRODUTTORE.
        # MISURATO: dal 12/11 il primario risponde 200 con 0 chiavi (l'intero
        # bucket ha 2 oggetti di ottobre); il backup dichiarato da EUMETNET
        # ("ORD Backup") ha 197 DBZH.tiff/13-11 a cadenza 5 min. Si prova il
        # primario e, se elenca 0 chiavi, il backup: nessun merge fra i due.
        "s3_fallback_bases": [
            "https://object-store.os-api.cci1.ecmwf.int/openradar-24h-backup/",
        ],
        "product": "DBZH",
        "extension": "tiff",
        # Finestra letta dal COG (Italia + Adriatico + Tirreno): 1.294x1.486
        # px su 3.800x4.400 invece del file intero.
        "bbox_wgs84": [6.0, 35.0, 20.0, 48.0],
        # Priorita' STATICA: numero piu' basso = vince. DPC dove ha un eco,
        # OPERA altrove.
        "priority": {"dpc": 10, "opera": 20},
        # Freschezza: oltre questa eta' il vincitore viene sostituito
        # dall'altro se fresco (substituted=true + motivo).
        "max_age_min": 20,
        # Tolleranza fra slot VMI e slot composito (entrambi a 5 minuti).
        "slot_tolerance_s": 300,
        "lookback_days": 2,
        "timeout_s": 60,
        # Fascia di confine fra sorgenti esclusa da detect/tracking.
        "seam_width_px": 2,
        # Clamp fisico dBZ (OPERA dichiara -31,5..95,5).
        "clamp_dbz": [-32.0, 95.0],
        # Lettura COG via /vsicurl/ (Range). False = download integrale.
        "use_vsicurl": True,
        # Misura dei byte Range (CPL_CURL_VERBOSE su fd 2). Costo ~0.
        # ATTENZIONE: su Windows la cattura in-process non funziona (CRT
        # diverso) e la misura richiede un SOTTOPROCESSO che RIPETE la
        # lettura: ~3,4 s per frame. In produzione OFF (default).
        "measure_bytes": False,
        # Dump opzionale degli array di provenienza (.npz, ~15 MB/frame):
        # None = non scritto (il dettaglio aggregato va in latest.json).
        "provenance_out": None,
    },
    # -----------------------------------------------------------------
    # 1.2.4.84 - DOPPLER FRANCESE (OPERA FR): shear azimutale osservato.
    # Il prodotto DPC VMI e' un mosaico 2D a singola riflettivita' e NON
    # contiene velocita' radiale: l'unico Doppler disponibile per l'Italia
    # centro-settentrionale e' quello dei due radar francesi che coprono la
    # Corsica e il Tirreno (frale = Aleria, fraja = Ajaccio).
    # GEOMETRIA MISURATA il 2026-11-11 sui file OPERA SCAN:
    #   360 raggi x 267 bin, rscale 960 m, rstart 0 -> 256.32 km,
    #   gain +0.5, offset -60, nodata 255, undetect 254, how/NI = 62.0555 m/s
    #   (il PVOL nativo MÃ©tÃ©o-France/iRadar e' 360x256 @ 1000 m, gain -0.5,
    #   offset 60: codifica speculare, stesso valore fisico).
    # COPERTURA: Roma e' a 247.9 km da Aleria (dentro i 250 km) ma la 0.6
    # gradi a 248 km e' a 6.2 km di altezza: il Doppler c'e' solo se la
    # precipitazione arriva a quella quota (misurato: 13/243 volumi del
    # 2026-11-11 con almeno un gate valido nella finestra di Roma).
    # COSTO: 4 file per sito (~250 kB) + 2 per la finestra di copertura,
    # ~1.5-3 s per run (misurato); nessun download se il bucket non ha il
    # giorno corrente (lookback_days).
    "doppler": {
        "enabled": True,
        "sites": ["frale", "fraja"],
        "country": "FR",
        # Gate fisici. `max_range_km` 250 (Roma a 247.9): il taglio e' sulla
        # DISTANZA, l'altezza del fascio e' un gate separato a 8 km.
        # `min_range_km` 30: sotto, 1 grado di azimuth vale meno del gate
        # radiale (0.52 km a 30 km contro 0.96 km) e lo "shear azimutale"
        # misura il rumore lungo il raggio, non la struttura azimutale
        # (misurato: |shear| p99 8-10 m/s/km a 30-100 km contro 1-3 a
        # 100-250 km).
        "max_range_km": 250.0,
        "min_range_km": 30.0,
        "max_beam_height_km": 8.0,
        "high_beam_height_km": 3.0,     # confidenza high sotto 3 km
        "medium_beam_height_km": 6.0,   # medium sotto 6, low sopra
        # SCELTA INGEGNERISTICA (nessuna calibrazione): 4 m/s/km su 2 gradi
        # a 100 km = 14 m/s di differenza fra raggi adiacenti.
        "shear_threshold_ms_km": 4.0,
        "shear_window_rays": 1,         # 1 -> 3 raggi (i-1, i, i+1)
        "persistence_scans": 2,
        "persistence_factor": 0.5,
        "vertical_tolerance_rays": 2,
        "vertical_tolerance_gates": 2,
        "vertical_factor": 0.5,
        # Ingestione: 2 elevazioni per lo shear (0.6 + 1.01 per frale,
        # 0.4 + 0.8 per fraja), 4 per la copertura.
        "max_elevations_shear": 2,
        "max_elevations_coverage": 3,
        "lookback_days": 2,
        "timeout_s": 60,
        # Accoppiamento struttura <-> candidato.
        "match_radius_km": 15.0,
        "match_window_min": 5.0,
        # Copertura misurata (non un cerchio): merge dei range massimi per
        # azimuth nella finestra.
        "coverage_out": "data/radar/doppler_coverage.geojson",
        "coverage_window_min": 30.0,
        "coverage_max_volumes": 3,
        "coverage_min_range_km": 5.0,
    },
    # Collimazione DPC <-> OPERA (harness, non UI): disattivata in produzione,
    # si esegue dal workflow radar-collimation.yml o da CLI.
    "collimation": {
        "enabled": False,
        "out": "data/radar/collimation.json",
        "presence_threshold_dbz": 20.0,
        "ring_edges_km": [0.0, 50.0, 100.0, 150.0, 200.0, None],
        "sites_file": None,
    },
    "preprocess": {
        # DATA VALIDITY (nodata/espliciti) — distinta dalla soglia meteorologica.
        "nodata_values": [-9999.0, -9998.0],
        "declared_nodata_from_tiff": True,   # aggiunge ds.nodata del GeoTIFF
        # Plausibility check geografica (FAIL SAFE): fuori bbox Italia -> errore.
        "geo_plausible_bbox": {"lon": [4.0, 21.0], "lat": [34.0, 48.0]},
    },
    "detect": {
        # METEOROLOGICAL THRESHOLD — separato dalla validità del dato.
        "dbz_threshold": 20.0,             # EXPERIMENTAL
        "min_area_km2": 30.0,              # EXPERIMENTAL (celle piccole scartate)
        "max_area_km2": 40000.0,           # EXPERIMENTAL (cap anti-mosaico)
        "morph_radius_px": 1,              # EXPERIMENTAL (SE quadrato 2r+1)
        "connectivity_8": True,            # labelling 8-connesso (np.ones(3,3))
    },
    "tracking": {
        # Cost function combinata (Hungarian / linear_sum_assignment):
        #   cost = w_distance*d[km] + w_area*Δarea_norm + w_intensity*ΔdBZ
        #          + w_overlap*(1 - IoU_circle)
        "w_distance": 1.0,                 # EXPERIMENTAL
        "w_area": 6.0,                     # EXPERIMENTAL (Δarea normalizzata 0-1)
        "w_intensity": 0.25,               # EXPERIMENTAL (ΔdBZ)
        "w_overlap": 8.0,                  # EXPERIMENTAL (1-IoU in 0-1)
        "max_assignment_cost": 400.0,      # EXPERIMENTAL (sopra soglia -> birth)
        "min_frames_track": 2,             # track minima (2+ frame)
        "full_confidence_frames": 5,       # >=5 frame -> tracking_confidence=full
        # ---- Fase 1.6 (STABILIZATION) — EXPERIMENTAL ----
        "cost_formula": "legacy",          # 'legacy' (Fase 1) | 'improved' (1.6)
        "w_prediction": 1.0,               # peso errore predizione (solo improved)
        "distance_ref_km": 100.0,          # normalizzazione distanza (0-1)
        "prediction_ref_km": 50.0,         # normalizzazione errore predizione
        "intensity_ref_dbz_cost": 35.0,    # normalizzazione ΔdBZ (0-1)
        "memory_frames": 3,                # media recente area/intensità (matching)
        "max_storm_speed_kmh": None,       # GATE fisico (NON hardcodato; None=off
                                           #  = default di produzione, vedi Fase1.6)
        "prediction_gate_km": None,        # None -> = step gate da max_storm_speed
        "diagnostics": False,              # per-match diag (Mai nel dataset pubbl.)
        # ---- Fase 1.8 (M1) — GRACE/COASTING — EXPERIMENTAL ----
        "max_coast_frames": 0,             # 0=OFF: frame senza celle -> morte
                                           # immediata; N>0: sopravvive N frame
                                           # consecutivi privi di celle.
    },
    "storm": {
        # Fase 1.7 — LAYER MULTISCALA (CELL -> STORM OBJECT -> STORM SYSTEM).
        # Il CellTracker NON viene sostituito: resta il layer locale. Sotto il
        # tracker, lo storm object è l'unità di identità principale.
        "enabled": True,               # abilita aggregazione + storm tracking
        "aggregation": {
            # Metodo di raggruppamento celle in storm object (EXPERIMENTAL):
            #   'distance_cc'     - componenti connesse su distanza centroide
            #   'dbscan'          - DBSCAN di riferimento (cell centroids)
            #   'dilation_overlap'- (DEFAULT) unione se gap-cerchio <= merge_gap
            #                       OPPURE cerchi dilatati di dilate_km si sovrapp.
            "method": "dilation_overlap",
            "merge_gap_km": 5.0,       # gap (d - r1 - r2) per l'unione (>=0)
            "dilate_km": 8.0,          # raggio di dilatazione per l'overlap test
            "dbscan_eps_km": 15.0,     # riferimento: eps DBSCAN (centroids)
            "dbscan_min_cells": 2,     # riferimento: min_samples DBSCAN
            "min_cells": 1,            # storm object con >= n celle
            "hull": True,              # incluso convex hull centroids nei props
            "density_radius_km": 25.0, # raggio per la local cell density
        },
        "tracking": {
            # Cost function NORMALIZZATA (0-1 per termine), pesi configurabili:
            #   cost = w_distance * d/distance_ref + w_iou * (1-IoU_circle)
            #        + w_area * |ΔA|/max(A) + w_intensity * |ΔdBZ|/int_ref
            #        + w_cells * min(1, |Δcount|/cell_count_ref)
            # PRIORITÀ: geometry overlap (w_iou) > centroid continuity
            # (w_distance) > motion continuity (predizione, NON Kalman).
            "w_distance": 1.0,
            "w_iou": 6.0,
            "w_area": 2.0,
            "w_intensity": 0.5,
            "w_cells": 1.0,
            "cell_count_ref": 10.0,
            "distance_ref_km": 100.0,
            "intensity_ref_dbz": 35.0,
            "w_prediction": 1.0,       # errore predizione (come tracking 1.6)
            "prediction_ref_km": 50.0,
            "max_assignment_cost": 400.0,
            "min_frames_track": 2,
            "full_confidence_frames": 5,
            # GATE DI CONTINUITA' degli storm object (2026-11-11). Stesso
            # valore/semantica del gate delle CELLE (forecast.SPEED_RANGE_KMH[1]
            # = 110 km/h): se la velocita' implicita fra due associazioni supera
            # la soglia, l'associazione viene RIFIUTATA e nasce un nuovo storm
            # object. Misurato sul run 2026-11-10T22:55:36Z prima del gate:
            # 20 storm track su 37 con almeno un segmento >110 km/h
            # (41 segmenti, massimo 12.703 km/h) e 5 delle 7 strutture
            # pubblicate costruite su quei salti.
            "max_storm_speed_kmh": _SPEED_RANGE_KMH[1],
            "prediction_gate_km": None,
            "diagnostics": False,
            "max_coast_frames": 0,          # 0=OFF (M1 grace/coasting, v. tracking)
        },
        "ambiguity": {
            # metrica DIAGNOSTICA di qualità dell'identificazione (non meteo):
            # competing candidates + cell density locale + merge/split + cost sep.
            "density_radius_km": 25.0,
            "gate_radius_km": 50.0,       # candidati competitor entro il raggio
            "density_medium": 5,          # celle entro raggio -> soglie
            "density_high": 10,
            "competing_high": 3,          # n candidati finiti -> soglia high
            "cost_sep_high": 0.12,        # sep_ratio sotto -> high
            "cost_sep_medium": 0.30,      # sotto -> medium
        },
        "motion": {
            # Risoluzione motion_confidence (low/medium/high): age, osservazioni,
            # continuità geometrica, coerenza spostamento, ambiguità residua.
            "min_frames_clock": 3,       # frame per motion (oltre il minimo 2)
            "geom_iou_high": 0.35,       # media IoU consecutiva
            "geom_iou_medium": 0.20,
            "cv_speed_high": 0.40,       # CV velocità tra segmenti
            "turn_high_deg": 15.0,       # std circolare heading tra segmenti
            "max_validated_velocity_kmh": 200.0,  # GATE di VALIDAZIONE cinematica
                                 # (Fase 1.9, Parte B): rifiuta la velocity_kmh
                                 # pubblicata se il robust median è impossibile
                                 # (>200 km/h). CALIBRATO su 25 casi reali
                                 # (PHASE19 report): p99 velocità vera ~181 km/h,
                                 # tracking invariato (continuità 0.585). NON
                                 # tocca il matching (max_storm_speed_kmh resta
                                 # il solo step-gate Fase 1.6, default None).
        },
    },
    "scoring": {
        # Organization Score (0-100), pesi in somma 1.0. EXPERIMENTAL.
        "weights": {
            "persistence": 0.20,
            "intensity": 0.20,
            "intensity_consistency": 0.15,
            "spatial_coherence": 0.15,
            "motion_consistency": 0.15,
            "growth_sustained": 0.15,
        },
        # Scale di riferimento per la normalizzazione (valori fisici plausibili).
        "intensity_ref_dbz": [20.0, 55.0], # floor/ceiling normalizzazione dBZ
        "persistence_ref_frames": 5,       # track "piena" a 5 frame (T0..T-20)
        "bands": [
            (0.0, 30.0, "Weak Convective Cell"),
            (31.0, 55.0, "Convective Cell"),
            (56.0, 75.0, "Organized Convective Cell"),
            (76.0, 100.0, "Highly Organized Convective Cell"),
        ],
    },
    "convective_structure": {
        # 1.2.4.17 — DESCRITTORE di struttura convettiva (blocco ex-SSI).
        # Layer ADDITIVO: stessi dati Fase 1 (celle+track+Organization Score),
        # nessuna richiesta rete aggiuntiva. Nessun Doppler nel prodotto VMI:
        # la rotazione NON e' misurabile (rotation_measurable=false in output) e
        # nessun campo dichiara mesocicloni o diagnosi che il dato non contiene.
        "enabled": True,
        "weights": {
            "intensity_core": 0.25,
            "organization": 0.30,
            "core_morphology": 0.10,
            "persistence": 0.15,
            "growth_sustained": 0.20,
        },
        "intensity_core_dbz": 45.0,   # GATE: core convettivo reale per il record
        "intensity_ref_dbz": [35.0, 55.0],
        "persistence_ref_frames": 5,
        "compactness_ref": 8.0,
        "candidate_soi": 65.0,        # SOI minimo per emettere un record
        "birth_frames": 3,            # candidati con <=n frame -> phase 'birth'
        "min_frames": 2,
        # Livelli DESCRITTIVI (0-49 non e' un'assenza: e' SOI sotto 50).
        "bands": [
            (0.0, 49.0, "unorganized"),
            (50.0, 64.0, "organized"),
            (65.0, 79.0, "strong"),
            (80.0, 100.0, "intense"),
        ],
    },
    "phase2": {
        # Fase 2 (EXPERIMENTAL): layer additivi A1 -> indice v2 del descrittore.
        # Policy: ogni sotto-layer e' OPZIONALE; errore/dato assente ->
        # warning + sub-score None (mai crash del run, stessa politica dei
        # layer storm/descrittore della Fase 1). Nessun dato inventato.
        "enabled": True,
        "hook": {
            # Hook echo morfologico da griglia VMI (nessun Doppler):
            # soglia dBZ del core + persistenza sulle ultime N griglie.
            # B2: calcolo sul FOOTPRINT locale di ogni candidato.
            "dbz_threshold": 45.0,     # = hook.HOOK_DBZ_THRESHOLD (A1)
            "history_frames": 3,       # frame per hook.filter_persistence
            "footprint_radius_km": 45.0,  # finestra ±km attorno al candidato
        },
        "structure": {
            # Prodotti DPC addizionali (griglie devono allinearsi alla VMI).
            # None = product type non configurato -> descrittore ASSENTE
            # (membership 0, mai un valore fabbricato).
            # B2: ritaglio PER CANDIDATO della finestra ±local_radius_km
            # (ETM metri -> km, POH frazione -> % convertiti al ritaglio).
            "product_vil": "VIL",      # prodotto DPC verificato (A0)
            "product_etm": "ETM",      # prodotto DPC verificato (A0)
            "product_poh": "POH",      # prodotto DPC verificato (A0)
            # 2026-11-12 — SRI: Quantitative Precipitation Estimate (mm/h).
            # Product type DPC "SRI" (findLastProductByType?type=SRI -> 200,
            # PT5M; file servito SRI/<DD-MM-YYYY-HH-MM>.tif). La sonda del
            # 2026-11-12 misura 0.01..72.43 mm/h, 0.0 = assenza valida e
            # -9999 = fuori copertura (NESSUN -9998). Etichetta di fonte
            # richiesta: "SRI_adj" (radar + pluviometri); l'API espone solo il
            # product type SRI (SRI_adj/SRI_ADJ/SRIadj -> 404, misurato).
            "product_sri": "SRI",
            # 2026-11-12 — BUNDLE ALLINEATO: VIL/ETM/POH/SRI chiesti allo
            # STESSO productDate di OGNI frame (non piu' "l'ultimo prodotto").
            # Costo misurato sullo slot 2026-11-12T15:15Z: VIL 430 kB,
            # ETM 745 kB, POH 335 kB, SRI 1.168 kB = 2,68 MB/slot; 6 slot =
            # ~16 MB/run (rete, nessun file committato). `retain_non_reference`
            # tiene in memoria solo POH/SRI su tutti gli slot (servono a
            # feature e accumulo); VIL/ETM sono usati solo sul frame di
            # riferimento e vengono rilasciati dopo il QC.
            "bundle": {
                "enabled": True,
                "products": ["vil", "etm", "poh", "sri"],
                "stale_after_s": 300,      # una cadenza PT5M
                "accum_step_s": 300,       # passo dell'accumulo stimato
                "poh_threshold": 0.5,      # POH e' una FRAZIONE 0-1
                "retain_non_reference": ["poh", "sri"],
            },
            "product_low": "CAPPI_2",  # CAPPI 2 km: verificato live (findLastProductByType)
            "product_high": "CAPPI_6", # CAPPI 6 km: verificato live -> overhang attivo
            "local_radius_km": 45.0,   # finestra ±km attorno al candidato
            # 1.2.4.18 — MESH (Witt et al. 1998) e OVERHANG hanno bisogno del
            # PROFILO dBZ(H): la sonda CAPPI del 2026-10-23 ha verificato che
            # esistono ESATTAMENTE 10 livelli (CAPPI_1..CAPPI_10 = 1000..10000 m
            # slm, passo 1000 m, griglia 1200x1400 identica alla VMI). Qui la
            # mappa quota_km -> product type: il fetcher li chiede TUTTI allo
            # STESSO productDate della VMI (fetch.fetch_cappi_set).
            "cappi_products": {
                "1.0": "CAPPI_1", "2.0": "CAPPI_2", "3.0": "CAPPI_3",
                "4.0": "CAPPI_4", "5.0": "CAPPI_5", "6.0": "CAPPI_6",
                "7.0": "CAPPI_7", "8.0": "CAPPI_8", "9.0": "CAPPI_9",
                "10.0": "CAPPI_10",
            },
            # Interruttore ESPLICITO del profilo completo: OFF -> si scaricano
            # solo product_low/product_high (2 livelli -> MESH null con
            # motivo) e il costo per run torna quello della 1.2.4.17.
            "mesh_levels_enabled": True,
            "mesh_min_levels": 4,      # gate piu' restrittivo di Witt (>=2)
            "mesh_dh_m": 1000.0,       # passo verticale REALE dei CAPPI DPC
            # Costo dichiarato: 10 file = 5,00-6,31 MB per run (misurato),
            # cioe' ~4-5x il volume VMI+VIL+ETM. 1.2.4.26: download in
            # parallelo (source.fetch_workers, misurato 69,97 s -> 9,75 s);
            # con il gate hail_gate il set completo si scarica solo nei run
            # plausibili.
            "cappi_fetch": {
                "period_s": 300,           # cadenza misurata PT5M
                # 2026-11-12 — INTERRUTTORE del retry (0 = nessun retry).
                # NON e' piu' il numero di tentativi: con il polling il numero
                # di round lo decide `ceil(retry_wait_s / poll_interval_s)`,
                # cioe' 12 con i valori qui sotto. Il tetto resta `retry_wait_s`.
                "retry_attempts": 1,
                # Ritardo di pubblicazione MISURATO: VMI +6m53s, CAPPI +9m03s
                # sullo stesso slot => ~2m10s fra i due. 120 s di attesa UNICA
                # (condivisa dai 10 livelli) coprono quel divario; senza di
                # essa lo slot allineato alla VMI sarebbe spesso assente.
                "retry_wait_s": 120.0,
                # 2026-11-12 — GRANULARITA' DELL'ATTESA (fetch.CAPPI_POLL_INTERVAL_S).
                # Il tetto resta `retry_wait_s`; cambia solo che si riprova ogni
                # `poll_interval_s` e si esce al PRIMO successo, invece di
                # dormire 120 s e riprovare una volta sola. MISURA di produzione
                # (40 run del 2026-11-12, data/radar/latest.json):
                # `cappi_fetch.seconds` = 121,456 / 121,985 / 122,294 s in 3 run
                # su 40 (motore 148,5-159,5 s) contro 1,05-2,01 s negli altri 37
                # (motore 21,3-43,3 s). Con 10 s il caso "prodotto arrivato
                # pochi secondi dopo il primo 500" costa <=10 s invece di 120.
                # 0 o negativo = attesa unica storica (rollback senza codice).
                "poll_interval_s": 10.0,
                "allow_previous_slot": True,  # fallback t-5min se resta 5xx
            },
            "overhang": {
                # SCELTA INGEGNERISTICA: dBZ(6 km) >= 45 e dBZ(2 km) < 45
                # (il probe misura 381 colonne cosi' e 583 con dBZ6>=50).
                "low_km": 2.0,
                "high_km": 6.0,
                "core_dbz": 45.0,
                "strong_core_dbz": 50.0,
            },
            # 1.2.4.26 — GATE DI PLAUSIBILITA' GRANDINE prima dei 10 CAPPI.
            # Il criterio ESISTEVA gia' nel prodotto: VIL density >= 3.5 g/m3
            # (Amburn & Wolf 1997, severe_stack.VIL_DENSITY_THRESHOLD_G_M3).
            # MISURA sul registro data/state/severe_predictions.jsonl del
            # 2026-10-25 (177 run con candidati, 23-25/10): 26 run (14,7%)
            # avevano almeno una cella sopra soglia; 151 (85,3%) no. Quindi il
            # set completo da 10 livelli serve in ~1 run su 7: negli altri si
            # scaricano solo CAPPI_2/CAPPI_6 (2 livelli: overhang e struttura
            # restano, MESH esce null con "cappi_levels<4").
            # CONSERVATIVO: se VIL o ETM mancano il gate NON e' valutabile e si
            # scarica il set completo (l'assenza del dato non e' evidenza di
            # assenza di grandine). Il criterio e' GLOBALE sulla griglia, non
            # per cella: il massimo globale e' >= quello per candidato, quindi
            # il gate non scarta mai un run che il criterio per cella terrebbe
            # (la misura 14,7% e' un LIMITE INFERIORE di selettivita').
            "hail_gate": {
                "enabled": True,
                "vil_density_threshold_g_m3": 3.5,
                # Livelli scaricati quando il gate dice "non plausibile":
                # "low_high" = product_low + product_high (2 CAPPI).
                "levels_when_implausible": "low_high",
            },
        },
        "environment": {
            # Open-Meteo sul punto di OGNI candidato (urllib diretto,
            # nessuna dipendenza extra): SCP/STP/SHIP + env_score 0-100.
            # B2: cache per bucket a cache_grid_deg gradi -> i candidati
            # nello stesso bucket condividono UNA sola richiesta.
            "enabled": True,
            "timeout_s": 30,           # = environment.ENV_HTTP_TIMEOUT_S
            "cache_grid_deg": 0.1,     # bucket = (round(lat,1), round(lon,1))
        },
        "lightning": {
            # ATTENZIONE (2026-11-11): il vecchio feed Blitz v2 DPC e' MORTO
            # (403 su 12/12 slot). La sorgente primaria e' il blocco "afa"
            # qui sotto; le chiavi window_slots / max_backoff_steps /
            # max_failed_attempts / max_unexpected_failures /
            # publication_grace_steps / max_consecutive_failures restano solo
            # come documentazione storica e per config esterne: main.py non le
            # legge piu' (il vecchio feed fa UNA sonda, senza backoff).
            # Blitz v2 S3 DPC: finestra del rate in slot da 5 minuti.
            # B2: conteggio spaziale PER CANDIDATO entro radius_km (haversine)
            # con fetch dello slot in cache condiviso fra i candidati.
            "window_slots": 4,         # = lightning.LIGHTNING_TREND_WINDOW
            "radius_km": 30.0,         # = lightning.LIGHTNING_RADIUS_KM
            "min_strikes": 1,          # rate < min_strikes -> componente None
            # 1.2.4.17 — E: il vecchio valore 36 non arrivava mai al modulo
            # (LTG_MAX_BACKOFF_STEPS = 12) e un run senza dati produceva 37
            # richieste HTTP 403 (STUDIO_SUPERCELLE_RADAR sez. 6.4). Ora il
            # valore e' ALLINEATO al modulo (12) e main.py applica comunque il
            # minimo fra config e costante del modulo: un tentativo per slot.
            "max_backoff_steps": 12,       # = lightning.LTG_MAX_BACKOFF_STEPS
            # 1.2.4.26 — TETTO DEI TENTATIVI FALLITI PER RUN (12), il valore
            # della 1.2.4.17. MISURA del 2026-10-25 sulle 72h precedenti
            # (435 run, 267 con candidati, fonte data/radar/latest.json a ogni
            # commit): 58 run con almeno un dato fulmini; 43 di quelle 58
            # avevano >=3 fallimenti INIZIALI (con un tetto a 3 avrebbero perso
            # il layer, 74%); mediana 10 richieste per run con dato (~6 slot
            # in 403 prima del primo pubblicato); nessuna run con dato ha avuto
            # bisogno di k>=9, ma il k=9 e' il caso MISURATO il 2026-10-07 e
            # con 12 richieste non si perde. Il conteggio si azzera a ogni slot
            # trovato: la finestra di trend (4 slot) non viene ridotta.
            # Costo: 12 richieste/run solo quando NESSUNO slot e' pubblicato
            # (403, risposta piccola); con la sorgente viva si ferma prima.
            "max_failed_attempts": 12,
            # Tetto SEPARATO sui fallimenti che NON sono "non ancora
            # pubblicato" (rete, 5xx, payload non decodificabile): 3, perche'
            # ritentare 12 volte una sorgente rotta non produce dati. Un 403
            # non incrementa questo contatore.
            "max_unexpected_failures": 3,
            # Stop dopo N errori CONSECUTIVI: superseded da max_failed_attempts
            # (resta come documentazione storica, non piu' letto da main.py).
            "publication_grace_steps": 9,  # = lightning.LTG_PUBLICATION_DELAY_STEPS
            "max_consecutive_failures": 3, # stop dopo 3 errori consecutivi
            # 2026-11-11 - SORGENTE PRIMARIA: AFA di MTG-I (EUMETSAT EUMETView
            # WMS, layer mtg_fd:li_afa). HTTP 200 senza token, archivio dal
            # 2025-05-30, passo 5 minuti, griglia 0,01 gradi. Il vecchio feed
            # DPC risponde 403 su 12/12 slot: resta SOLO come sonda singola
            # (legacy_fallback) quando l'AFA non e' disponibile.
            # MISURA: 1 richiesta per run (28 KB, decodifica 0,3 s) contro le
            # 12 richieste 403 del vecchio percorso.
            "afa": {
                "enabled": True,
                "wms_url": "https://view.eumetsat.int/geoserver/wms",
                "layer": "mtg_fd:li_afa",
                "bbox": [6.0, 35.0, 19.0, 48.0],   # Italia (lon/lat, CRS:84)
                "grid_px": 1300,                   # 0,01 gradi
                "max_backoff_steps": 3,            # 15 min indietro
                "throttle_s": 3.0,                 # >= 3 s fra richieste
                "timeout_s": 30,
                "cache_dir": "data/cache/lightning_afa",
                "legacy_fallback": True,           # vecchio feed: 1 sonda
            },
        },
        "severe_stack": {
            # 1.2.4.17 — S1 (ambiente) + S2 (grandine) + S3 (overshooting top)
            # e registro delle predizioni per la futura validazione POD/FAR.
            # 1.2.4.18 — aggiunge S2b (overhang da CAPPI_2/CAPPI_6) e S1b
            # (Bunkers RM/LM come DESCRITTORE del moto atteso, non rotazione).
            "enabled": True,
            "predictions_enabled": True,
            "predictions_retention_days": 30,
            "overhang_enabled": True,
            # Bunkers: 0-6 km con offset 7.5 m/s. Se il profilo vento non
            # arriva a 6 km il campo e' null con motivo (nessuna estrapolazione).
            "bunkers_top_m": 6000.0,
            "bunkers_deviation_ms": 7.5,
            # La tabella W(Z)/E(Z) di Witt e la costante 2.54 sono state
            # TRASCRITTE dal paper (eq. 1-3 p.288, eq. 6 p.293). Se un domani
            # la verifica decadesse, portare questa a false produce
            # mesh null con null_reason "witt_table_unavailable" invece di un
            # numero non verificato.
            "mesh_table_verified": True,
            "mesh_dh_m": 1000.0,
        },
        "ot": {
            # IR_108 DPC (MSG SEVIRI 10.8 um, GeoTIFF Float32 in °C, PT5M,
            # STESSA griglia TM della VMI): overshooting top via proxy
            # IRW-texture (Bedka et al. 2010). Il BTD WV-IR di overshoot.py NON
            # e' calcolabile (il canale WV 6.2 non e' tra i prodotti DPC): si
            # usa SOLO l'IR 10.8. dn_to_kelvin non serve piu' (IR_108 e' gia'
            # in °C, non un PNG grayscale non tarato). Soglie EXPERIMENTAL.
            "product_ir108": "IR_108",   # prodotto DPC (docs/RADAR_SOURCE_VERIFICATION.md)
            "footprint_radius_km": 45.0, # finestra ±km attorno al candidato
            "ctt_threshold_c": -58.0,    # = satellite_ot.IR_CTT_THRESHOLD_C (215 K)
            "anvil_threshold_c": -48.0,  # = satellite_ot.IR_ANVIL_THRESHOLD_C (225 K)
            "anomaly_threshold_c": 6.5,  # = satellite_ot.IR_ANOMALY_THRESHOLD_C
            "ring_radius_px": 8,         # raggio di campionamento ~8 km a 1 km/px
            "ring_directions": 16,       # 16 direzioni (Bedka 2010)
            "min_anvil_samples": 5,      # >= 5/16 campioni d'anvillo validi
            "erosion_kernel": 3,         # erosione 3x3 (rimuove pixel isolati)
            # Legacy BTD WV-IR (overshoot.py): mantenuto per retro-compatibilita'
            # della config, non piu' usato dal pipeline OT (canale WV assente).
            "btd_threshold_k": 12.0,     # = overshoot.OT_BTD_THRESHOLD_K
            "ir_threshold_k": 215.0,     # = overshoot.OT_IR_THRESHOLD_K
        },
        "aggregate": {
            # Pesi indice v2: SOMMA ESATTAMENTE 1.00 (aggregate.DEFAULT_WEIGHTS).
            # Somma > 1.0 -> ValueError -> warning + nessun soi_v2 (v. A2-2).
            # B2: i componenti None per candidato vengono ESCLUSI e i pesi
            # RINORMALIZZATI sui presenti (somma 1.0); tutti None -> soi_v2 None.
            # 2026-11-12: un 0.0 FINITO e' un valore MISURATO e conta con il
            # suo peso (non viene escluso). NaN/inf/non numerici sono ASSENTI
            # (prima diventavano 0.0 e contavano: dato mancante -> zero finto).
            "weights": {
                "base": 0.60,          # SOI Fase 1 (convective_structure) — base
                "hook": 0.15,
                "structure": 0.10,
                "env": 0.08,
                "ot": 0.04,
                "lightning": 0.03,
            },
            # MISURA del 2026-11-12 (34 record, 15 run osservati, 14 con almeno
            # una struttura) — perche' soi_v2 sta ~20.7 punti sotto soi
            # (media; 17-25):
            #   hook 0.0 su 34/34: il descrittore e' stato VALUTATO (non null)
            #     e il gate morfologico (core >= 25 px E bay_wrap >= 150 gradi)
            #     non e' scattato. Quale dei due sotto-gate fallisca NON e'
            #     attribuibile dai record pubblicati (hook_features non c'e').
            #   ot 0.0 su 34/34 (ctt_min_c max -38.5 C contro soglia -58 C:
            #     nessun overshooting top, novembre),
            #   lightning null su 34/34 (AFA 504/timeout, feed legacy 403):
            #     ESCLUSO dalla rinormalizzazione (questo e' il caso "assente"),
            #   structure 0.0-5.8 (m_poh e m_overhang = 0 su 34/34, m_vil > 0
            #     su 1/34: contribuisce solo ETM), env 2.2-42.1.
            # Il 19% di peso di hook+ot resta quindi una costante negativa:
            # NON si compensa abbassando i pesi (sarebbe una toppa): la
            # correzione e' nei descrittori. I pesi usati per record sono
            # pubblicati in aggregate_ssi_v2()['weights_used'].
            # Livelli DESCRITTIVI di soi_v2 (0-100): stesse parole dei livelli
            # SOI ma soglie DIVERSE (SOI: 50/65/80) e valori NON comparabili.
            # = aggregate.DEFAULT_LEVELS_V2 (il valore EFFETTIVO: main.py non
            # passa cfg a aggregate_ssi_v2). Un test verifica la coincidenza.
            "levels_v2": [
                [0.0, 49.0, "unorganized"],
                [50.0, 69.0, "organized"],
                [70.0, 84.0, "strong"],
                [85.0, 100.0, "intense"],
            ],
        },
    },
    "output": {
        "out_dir": "data/radar",
        "files": ["latest.json", "storms.geojson", "tracks.json",
                  "storm_objects.geojson", "storm_tracks.json",
                  "convective_structures.json"],
        # 1.2.4.17 — registro delle PREDIZIONI (JSONL, append, rotazione 30 gg).
        # Percorso relativo alla radice del repo: data/state/, non data/radar/.
        "predictions_file": "data/state/severe_predictions.jsonl",
        "predictions_retention_days": 30,
    },
}


def _deep_merge(base, overrides):
    """Merge ricorsivo: i dict si fondono a ogni profondità, gli altri tipi
    (scalari, liste, tuple) vengono SOSTITUITI."""
    out = dict(base)
    for key, value in (overrides or {}).items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def merge_config(base, overrides):
    """Merge ricorsivo: overrides (dict) si fonde su base a ogni profondità.

    R3: la precedente versione 1-livello sostituiva un intero sotto-blocco non
    nominato (es. override di 'storm.tracking' perdeva 'aggregation',
    'ambiguity'; idem per i sotto-blocchi di 'phase2')."""
    return _deep_merge(base, overrides)


def load_config(path=None):
    """Carica la configurazione con eventuale override JSON (merge ricorsivo).

    1.2.4.17: il blocco `supercell` e' stato rinominato `convective_structure`.
    Un override JSON che usa ancora la chiave storica viene RIMAPPATO (con
    DeprecationWarning): nessuna configurazione silenziosamente ignorata."""
    cfg = merge_config(CONFIG, {})
    path = path or os.environ.get("RADAR_ENGINE_CONFIG")
    if path:
        with open(path, "r", encoding="utf-8") as fh:
            overrides = json.load(fh)
        if isinstance(overrides, dict) and "supercell" in overrides:
            # nome storico, non usare in output (solo chiave JSON legacy)
            warnings_mod.warn(
                "config key 'supercell' is deprecated: use "
                "'convective_structure' (1.2.4.17)", DeprecationWarning,
                stacklevel=2)
            legacy = overrides.pop("supercell")
            conv = overrides.setdefault("convective_structure", {})
            if isinstance(legacy, dict) and isinstance(conv, dict):
                overrides["convective_structure"] = merge_config(legacy, conv)
            else:                                    # pragma: no cover
                overrides["convective_structure"] = legacy
        cfg = merge_config(CONFIG, overrides)
    if cfg["source"].get("cache_dir") is None:
        cfg["source"]["cache_dir"] = str(
            Path(__file__).resolve().parent / "temp"
        )
    return cfg