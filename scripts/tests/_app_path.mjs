// RISOLUZIONE VERSION-AGNOSTICA DEL FILE DELL'APP PER I TEST .mjs
//
// PERCHE' ESISTE: 27 test .mjs aprivano `mri-light-1.2.4.101.html` (un nome
// congelato, mai aggiornato dai bump). Quel file non esiste nel repo, quindi i
// test morivano con ENOENT prima di eseguire UN SOLO assert: erano rossi per un
// motivo finto e non misuravano niente. Un test che non apre l'app non sta
// testando l'app.
//
// FONTE DI VERITA': `APP_FILE` nel `VERSION` della root — lo stesso valore che
// `deploy-pages.yml` copia in `_site/index.html` (`grep -m1 '^APP_FILE=' VERSION`).
// Non si usa "il primo html che capita" (l'ordine di `readdirSync` dipende dal
// filesystem e cambia quando si rimuovono le release vecchie).
//
// FALLBACK DICHIARATO: se `APP_FILE` manca o il file non e' su disco si accetta
// l'UNICO `mri-light-<numeri>.html` presente nella root. Con zero o piu' di uno
// si LANCIA: meglio un errore esplicito che misurare un html diverso da quello
// pubblicato.
//
// `appPath()` -> percorso assoluto; `appHtmlName()` -> nome del file;
// `appHtml()` -> contenuto; `appResolution()` -> {name, path, source} per i test
// che vogliono dichiarare da dove arriva il percorso.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { listAppHtmlFiles } from '../../resolve_app_html.mjs';

export const REPO_ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

let cached = null;

export function appResolution() {
  if (cached) return cached;
  const versionPath = path.join(REPO_ROOT, 'VERSION');
  let declared = '';
  if (fs.existsSync(versionPath)) {
    declared = ((fs.readFileSync(versionPath, 'utf8').match(/^APP_FILE=(.+)$/m) || [])[1] || '').trim();
  }
  if (declared) {
    const abs = path.join(REPO_ROOT, declared);
    if (fs.existsSync(abs)) {
      cached = { name: declared, path: abs, source: 'VERSION' };
      return cached;
    }
  }
  const candidates = listAppHtmlFiles(REPO_ROOT);
  if (candidates.length === 1) {
    cached = {
      name: candidates[0].name,
      path: path.join(REPO_ROOT, candidates[0].name),
      source: 'glob'
    };
    return cached;
  }
  throw new Error('html dell\'app non risolvibile: APP_FILE di VERSION = ' +
    JSON.stringify(declared) + ' e ' + candidates.length +
    ' file mri-light-<versione>.html nella root' +
    (candidates.length ? ' (' + candidates.map((c) => c.name).join(', ') + ')' : ''));
}

export function appPath() { return appResolution().path; }
export function appHtmlName() { return appResolution().name; }
export function appHtml() { return fs.readFileSync(appPath(), 'utf8'); }

// Versione dell'app ricavata dal NOME del file risolto (mri-light-1.3.0.61.html
// -> "1.3.0.61"). Serve ai test che devono confrontare APP_VERSION o CACHE_NAME
// senza congelare la release nello script: la versione attesa e' quella del file
// che il deploy pubblica, non una stringa scritta a mano anni prima.
export function appVersion() {
  const m = /^mri-light-(.+)\.html$/.exec(appHtmlName());
  return m ? m[1] : '';
}
