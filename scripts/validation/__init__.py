# -*- coding: utf-8 -*-
"""MeteoRisk Light — Validation (banco di validazione riutilizzabile).

Pacchetto AUTONOMO: nessuna dipendenza da ``scripts/radar_engine`` (unico
requisito: numpy). Serve a rispondere con numeri difendibili alla domanda
"quanto e' bravo l'algoritmo di rilevamento convettivo?".

Moduli:
  supercell_validation.py  banco di validazione event-based (contingenza 2x2,
                           POD/FAR/CSI/HSS/bias, bootstrap, stratificazione,
                           classificazione dei MISS, CLI, adattatori di input)

La documentazione d'uso (schema della verita', regola di matching, formule,
limiti dichiarati) vive in ``scripts/validation/README.md``.
"""

VALIDATION_TOOLKIT_VERSION = "1.0.0"

__all__ = ["supercell_validation"]
