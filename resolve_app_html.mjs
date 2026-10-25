// Risoluzione del file dell'app MeteoRisk Light nella root del progetto.
//
// Nella root storica convivono piu' copie dell'app (mri-light-1.2.4.9.html,
// mri-light-1.2.4.16.html, mri-light-1.2.4.21.html, ...) e il nome cambia a ogni
// release. Nessuno script deve quindi avere un nome di file scritto a mano.
//
// Criterio (lo stesso usato dai test): si considerano solo i file che corrispondono
// a /^mri-light-\d+(\.\d+)+\.html$/ e si sceglie la versione PIU' ALTA con un
// confronto NUMERICO per componenti: 1.2.4.21 > 1.2.4.16 > 1.2.4.9, mentre un
// confronto alfabetico direbbe il contrario ("1.2.4.21" < "1.2.4.9").
//
// Modulo condiviso da server.js, build.js e scripts/tests/test_html_resolution.mjs:
// il test esercita lo stesso codice che gira in produzione, non una sua copia.

import fs from 'node:fs';
import path from 'node:path';

export const APP_HTML_PATTERN = /^mri-light-\d+(\.\d+)+\.html$/;

const APP_HTML_CAPTURE = /^mri-light-((?:\d+)(?:\.\d+)+)\.html$/;

/**
 * Versione numerica del file dell'app, come array di interi.
 * @param {string} fileName nome del file (senza path)
 * @returns {number[]|null} es. [1,2,4,21], oppure null se il nome non corrisponde
 */
export function parseAppVersion(fileName) {
  if (typeof fileName !== 'string') return null;
  const match = APP_HTML_CAPTURE.exec(fileName);
  if (!match) return null;
  return match[1].split('.').map((part) => Number.parseInt(part, 10));
}

/**
 * Confronto numerico per componenti fra due versioni (array di interi).
 * Le componenti mancanti valgono 0, quindi [1,2,4] e [1,2,4,0] sono uguali.
 * @returns {number} <0 se a<b, 0 se uguali, >0 se a>b
 */
export function compareAppVersions(a, b) {
  const length = Math.max(a.length, b.length);
  for (let i = 0; i < length; i += 1) {
    const left = a[i] === undefined ? 0 : a[i];
    const right = b[i] === undefined ? 0 : b[i];
    if (left !== right) return left < right ? -1 : 1;
  }
  return 0;
}

/**
 * File dell'app presenti nella cartella, ordinati dalla versione piu' alta alla piu' bassa.
 * @param {string} dir cartella da scandire
 * @returns {{name: string, version: number[]}[]}
 */
export function listAppHtmlFiles(dir) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  const found = [];
  for (const entry of entries) {
    if (!entry.isFile()) continue;
    const version = parseAppVersion(entry.name);
    if (version === null) continue;
    found.push({ name: entry.name, version });
  }
  found.sort((x, y) => {
    const byVersion = compareAppVersions(y.version, x.version);
    if (byVersion !== 0) return byVersion;
    if (x.name === y.name) return 0;
    return x.name < y.name ? -1 : 1;
  });
  return found;
}

/**
 * Percorso assoluto del file dell'app piu' recente nella cartella.
 * @param {string} dir cartella da scandire (tipicamente __dirname degli script di root)
 * @returns {string|null} null se nessun file corrisponde: il chiamante decide come degradare
 */
export function resolveAppHtml(dir) {
  const files = listAppHtmlFiles(dir);
  if (files.length === 0) return null;
  return path.join(dir, files[0].name);
}
