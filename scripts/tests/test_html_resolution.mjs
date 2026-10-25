// TEST — RISOLUZIONE DINAMICA DEL FILE DELL'APP (resolve_app_html.mjs)
//
// Copre il criterio condiviso da server.js (route '/') e build.js (sorgente della
// build): fra piu' copie dell'app nella root si sceglie la versione PIU' ALTA con
// confronto NUMERICO per componenti. Il caso che distingue un confronto numerico da
// uno alfabetico e' 1.2.4.9 vs 1.2.4.21: alfabeticamente vince "9", numericamente
// vince "21". Il test usa una cartella temporanea con file finti, quindi non
// dipende da quali html sono presenti nel repo.
//
// Esegue lo STESSO codice usato da server.js/build.js (import del modulo), non una
// sua reimplementazione: un test che ricopia la logica non dimostrerebbe nulla.

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  APP_HTML_PATTERN,
  compareAppVersions,
  listAppHtmlFiles,
  parseAppVersion,
  resolveAppHtml,
} from '../../resolve_app_html.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.join(HERE, '..', '..');

let checks = 0;
let failures = 0;

function ok(name, condition, detail) {
  checks += 1;
  if (condition) {
    console.log('PASS ' + name);
  } else {
    failures += 1;
    console.error('FAIL ' + name + (detail === undefined ? '' : ' :: ' + detail));
  }
}

function writeFake(dir, name, body) {
  fs.writeFileSync(path.join(dir, name), body, 'utf8');
}

const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mri-html-resolution-'));
const emptyDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mri-html-empty-'));

try {
  console.log('cartella temporanea: ' + tempDir);

  // Le tre copie dell'app piu' un file che NON corrisponde al pattern.
  writeFake(tempDir, 'mri-light-1.2.4.9.html', '<html>9</html>');
  writeFake(tempDir, 'mri-light-1.2.4.16.html', '<html>16</html>');
  writeFake(tempDir, 'mri-light-1.2.4.21.html', '<html>21</html>');
  writeFake(tempDir, 'note.html', '<html>note</html>');

  // Esca: nomi che somigliano al pattern ma non lo sono (ancoraggio ^...$).
  writeFake(tempDir, 'index.html', '<html>stale</html>');
  writeFake(tempDir, 'mri-light-1.2.4.21.html.bak', '<html>backup</html>');
  writeFake(tempDir, 'xmri-light-1.2.4.99.html', '<html>prefisso</html>');
  // Nota: su filesystem case-insensitive (Windows) l'esca maiuscola deve avere una
  // versione diversa, altrimenti scriverebbe lo stesso file di mri-light-1.2.4.21.html.
  writeFake(tempDir, 'mri-light-1.2.4.99.HTML', '<html>maiuscolo</html>');
  writeFake(tempDir, 'mri-light-latest.html', '<html>non numerico</html>');

  const resolved = resolveAppHtml(tempDir);

  ok('1: la cartella con piu\' copie risolve un percorso', typeof resolved === 'string', String(resolved));
  ok(
    '2: sceglie la versione piu\' alta (mri-light-1.2.4.21.html)',
    resolved !== null && path.basename(resolved) === 'mri-light-1.2.4.21.html',
    resolved === null ? 'null' : path.basename(resolved)
  );
  ok(
    '3: NON sceglie mri-light-1.2.4.9.html (trappola del confronto alfabetico)',
    resolved !== null && path.basename(resolved) !== 'mri-light-1.2.4.9.html',
    resolved === null ? 'null' : path.basename(resolved)
  );
  ok(
    '4: NON sceglie mri-light-1.2.4.16.html',
    resolved !== null && path.basename(resolved) !== 'mri-light-1.2.4.16.html',
    resolved === null ? 'null' : path.basename(resolved)
  );
  ok(
    '5: il file risolto esiste e ha il contenuto della 1.2.4.21',
    resolved !== null && fs.existsSync(resolved) && fs.readFileSync(resolved, 'utf8') === '<html>21</html>',
    resolved === null ? 'null' : fs.existsSync(resolved) ? fs.readFileSync(resolved, 'utf8') : 'assente'
  );

  const listed = listAppHtmlFiles(tempDir);
  ok('6: solo i 3 file che corrispondono al pattern sono candidati', listed.length === 3, 'n=' + listed.length);
  ok(
    '7: l\'ordine dei candidati e\' decrescente per versione',
    listed.map((entry) => entry.name).join(',') ===
      'mri-light-1.2.4.21.html,mri-light-1.2.4.16.html,mri-light-1.2.4.9.html',
    listed.map((entry) => entry.name).join(',')
  );

  ok('8: cartella senza candidati -> null (nessun crash, nessun ENOENT)', resolveAppHtml(emptyDir) === null,
    String(resolveAppHtml(emptyDir)));
  ok('9: cartella senza candidati -> lista vuota', listAppHtmlFiles(emptyDir).length === 0);

  ok('10: parseAppVersion ricava [1,2,4,21]', JSON.stringify(parseAppVersion('mri-light-1.2.4.21.html')) === '[1,2,4,21]',
    JSON.stringify(parseAppVersion('mri-light-1.2.4.21.html')));
  ok('11: parseAppVersion su un nome estraneo -> null', parseAppVersion('note.html') === null,
    String(parseAppVersion('note.html')));
  ok('12: compareAppVersions(1.2.4.21, 1.2.4.9) > 0', compareAppVersions([1, 2, 4, 21], [1, 2, 4, 9]) > 0);
  ok('13: compareAppVersions(1.2.4.9, 1.2.4.16) < 0', compareAppVersions([1, 2, 4, 9], [1, 2, 4, 16]) < 0);
  ok('14: compareAppVersions uguali -> 0', compareAppVersions([1, 2, 4], [1, 2, 4, 0]) === 0);
  ok(
    '15: il pattern e\' esattamente ^mri-light-\\d+(\\.\\d+)+\\.html$',
    APP_HTML_PATTERN.source === '^mri-light-\\d+(\\.\\d+)+\\.html$',
    APP_HTML_PATTERN.source
  );

  // Controllo sul repo reale: il file risolto deve esistere ed essere il piu' alto fra
  // quelli presenti (dopo la pulizia di main ce n'e' uno solo; con piu' copie vince la
  // versione piu' alta). Se la root non contiene html il controllo non si applica.
  const realCandidates = listAppHtmlFiles(REPO_ROOT);
  if (realCandidates.length === 0) {
    console.log('SKIP 16: nessun mri-light-*.html nella root del repo, controllo reale non applicabile');
  } else {
    const realResolved = resolveAppHtml(REPO_ROOT);
    ok(
      '16: nel repo reale risolve ' + realCandidates[0].name,
      realResolved === path.join(REPO_ROOT, realCandidates[0].name) && fs.existsSync(realResolved),
      String(realResolved)
    );
    ok(
      '17: nel repo reale non c\'e\' una versione piu\' alta di quella risolta',
      realCandidates.every((entry) => compareAppVersions(entry.version, realCandidates[0].version) <= 0),
      realCandidates.map((entry) => entry.name).join(',')
    );
  }
} finally {
  fs.rmSync(tempDir, { recursive: true, force: true });
  fs.rmSync(emptyDir, { recursive: true, force: true });
}

console.log('');
console.log('controlli: ' + checks + ', falliti: ' + failures);
if (failures > 0) {
  console.error('RISULTATO: FAIL');
  process.exit(1);
}
console.log('RISULTATO: PASS');
