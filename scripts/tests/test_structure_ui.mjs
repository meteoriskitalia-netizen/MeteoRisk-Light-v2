// 1.3.0.87 — test FUNZIONALE delle due aggiunte client della struttura
// convettiva: classe morfologica visibile e contorno MultiPolygon di nuclei.
// Estrae le funzioni vere dall'HTML (nessuna copia) e le esegue in un sandbox.
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const html = fs.readFileSync(path.join(ROOT, 'mri-light-1.3.0.87.html'), 'utf8');

function extract(startMarker, endMarker) {
  const i = html.indexOf(startMarker);
  if (i < 0) throw new Error('marcatore iniziale assente: ' + startMarker);
  const j = html.indexOf(endMarker, i);
  if (j < 0) throw new Error('marcatore finale assente: ' + endMarker);
  return html.slice(i, j);
}

const labels = extract('var SC_MORPH_LABELS = {', 'function scMorphologyClass(c)');
const morph = extract('function scMorphologyClass(c)', 'function scCompass(');
const rings = extract('function scBuildOutlineRings(record)', '// 1.3.0.87 — il contorno pubblicato');
const polys = extract('function scBuildOutlinePolygons(record)', 'function scBuildForecastCone(');

const sandbox = {
  console,
  scConeRingLonLat: function (ring) {
    if (!Array.isArray(ring) || ring.length < 4) return null;
    const out = [];
    for (const p of ring) {
      if (!Array.isArray(p) || p.length < 2) return null;
      const lon = Number(p[0]), lat = Number(p[1]);
      if (!isFinite(lon) || !isFinite(lat)) return null;
      out.push([lat, lon]);
    }
    return out;
  },
};
vm.createContext(sandbox);
vm.runInContext(labels + morph + rings + polys, sandbox);

let failures = 0;
function check(name, cond, detail) {
  if (cond) {
    console.log('  [PASS] ' + name);
  } else {
    failures += 1;
    console.log('  [FAIL] ' + name + (detail ? ' -- ' + detail : ''));
  }
}

const c1 = sandbox.scMorphologyClass({
  morphology_class: {
    value: 'MULTICELL', raw_value: 'SINGLE_CELL',
    hysteresis: { frames: 3, stable_frames: 2 },
  },
});
check('classe Multicella mostrata', /Multicella/.test(c1.text), c1.text);
check('stabilita mostrata', /stabile 2 frame/.test(c1.text), c1.text);
check('valore grezzo nel title', /SINGLE_CELL|Cella singola/.test(c1.title), c1.title);

const c2 = sandbox.scMorphologyClass({
  morphology_class: { value: null, null_reason: 'shape_2d_unavailable' },
});
check('classe nulla dichiarata', /non determinata/.test(c2.text), c2.text);

const c3 = sandbox.scMorphologyClass({});
check('campo assente dichiarato', /n\/d/.test(c3.text), c3.text);

const square = [[9, 45], [9.1, 45], [9.1, 45.1], [9, 45.1], [9, 45]];
const poly = sandbox.scBuildOutlinePolygons({
  outline: { type: 'Polygon', coordinates: [square] },
});
check('Polygon -> 1 poligono', Array.isArray(poly) && poly.length === 1);

const multi = sandbox.scBuildOutlinePolygons({
  outline: {
    type: 'MultiPolygon',
    coordinates: [[square], [[[10, 46], [10.1, 46], [10.1, 46.1], [10, 46.1], [10, 46]]]],
  },
});
check('MultiPolygon -> 2 poligoni', Array.isArray(multi) && multi.length === 2,
      JSON.stringify(multi && multi.length));
check('anelli convertiti [lat,lon]', multi && multi[0] && multi[0][0][0][0] === 45);

const bad = sandbox.scBuildOutlinePolygons({ outline: { type: 'MultiPolygon', coordinates: [] } });
check('MultiPolygon vuoto -> null', bad === null);

if (failures) {
  console.log('RESULT: FAIL (' + failures + ')');
  process.exit(1);
}
console.log('RESULT: PASS');
