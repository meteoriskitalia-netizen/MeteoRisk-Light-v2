import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

console.log('Building MeteoRisk Light...');

const sourceFile = path.join(__dirname, 'mri-light-1.2.4.14.html');
const destFile = path.join(__dirname, 'index.html');

if (fs.existsSync(sourceFile)) {
  fs.copyFileSync(sourceFile, destFile);
  console.log('Synchronized index.html with mri-light-1.2.4.14.html');
}

console.log('Build completed successfully.');
