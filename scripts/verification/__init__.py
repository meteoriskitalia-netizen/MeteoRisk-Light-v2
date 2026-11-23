# -*- coding: utf-8 -*-
"""Verifica previsioni-vs-osservazioni della mappa dei rischi.

Moduli:
  * ``risk_forecast_capture`` — JOB A: fotografa il livello previsto 0-4 per
    (provincia, pericolo) e ne registra solo i cambi (delta).
  * ``risk_verification`` — JOB B: accoppia previsione e osservazioni sulla
    fascia ``ALL`` (giorno intero) e produce esiti 2x2 + skill.

Nessuna dipendenza oltre alla stdlib. Le metriche (Wilson, POD, FAR, CSI)
importano ``scripts/validation/validate3d_metrics.py``, gia' testato nel repo.
"""
