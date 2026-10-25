import express from 'express';
import { fileURLToPath } from 'node:url';
import { dirname } from 'node:path';
import { resolveAppHtml } from './resolve_app_html.mjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const app = express();
const PORT = process.env.PORT || 3000;

// Enable CORS and disable restrictive caching during development
app.use((req, res, next) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  next();
});

// Explicit root route: serve the most recent mri-light-<versione>.html in this folder.
// Nessun nome di file scritto a mano: la release cambia nome a ogni bump.
app.get('/', (req, res) => {
  const targetHtml = resolveAppHtml(__dirname);
  if (!targetHtml) {
    res.status(500).type('text/plain').send(
      'Nessuna app trovata: attesa un file mri-light-<versione>.html (es. mri-light-1.2.4.21.html) nella root del progetto.'
    );
    return;
  }
  res.sendFile(targetHtml);
});

// Serve static files from root directory
app.use(express.static(__dirname, {
  extensions: ['html', 'htm', 'json', 'geojson']
}));

app.listen(PORT, '0.0.0.0', () => {
  console.log(`MeteoRisk Light dev server listening on http://0.0.0.0:${PORT}`);
});
