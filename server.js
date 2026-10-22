import express from 'express';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import fs from 'node:fs';

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

// Explicit root route
app.get('/', (req, res) => {
  const targetHtml = join(__dirname, 'index.html');
  if (fs.existsSync(targetHtml)) {
    res.sendFile(targetHtml);
  } else {
    res.sendFile(join(__dirname, 'mri-light-1.2.4.14.html'));
  }
});

// Serve static files from root directory
app.use(express.static(__dirname, {
  extensions: ['html', 'htm', 'json', 'geojson']
}));

app.listen(PORT, '0.0.0.0', () => {
  console.log(`MeteoRisk Light dev server listening on http://0.0.0.0:${PORT}`);
});
