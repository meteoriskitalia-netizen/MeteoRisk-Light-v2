import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { resolveAppHtml } from './resolve_app_html.mjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

console.log('Building MeteoRisk Light...');

// Sorgente = il file dell'app piu' recente (stesso criterio di server.js).
// Destinazione = index.html, che non e' piu' committato: la build lo rigenera.
const sourceFile = resolveAppHtml(__dirname);
const destFile = path.join(__dirname, 'index.html');

if (!sourceFile) {
  console.error('FATAL: nessun file mri-light-<versione>.html trovato in ' + __dirname);
  process.exit(1);
}

fs.copyFileSync(sourceFile, destFile);
console.log('Synchronized index.html with ' + path.basename(sourceFile));

console.log('Build completed successfully.');
