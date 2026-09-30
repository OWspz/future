const { convexHull, volume } = require('./hull.js');
const assert = require('assert');
const cube = [];
for (const x of [0, 1]) for (const y of [0, 1]) for (const z of [0, 1]) cube.push([x, y, z]);
let h = convexHull(cube);
assert.strictEqual(h.vertices.length, 8); assert.strictEqual(h.polygons.length, 6);
assert(Math.abs(volume(h) - 1) < 1e-9);
// 內部點 + 重複點不應影響結果
h = convexHull(cube.concat([[.5, .5, .5], [0, 0, 0], [.2, .9, .5]]));
assert.strictEqual(h.vertices.length, 8); assert(Math.abs(volume(h) - 1) < 1e-9);
// 隨機點：所有輸入點都必須在凸包內
const rnd = Array.from({ length: 200 }, () => [Math.random(), Math.random(), Math.random()].map((v) => v * 2 - 1));
h = convexHull(rnd);
for (const p of rnd) for (const poly of h.polygons) {
  const [a, b, c] = poly.map((i) => h.vertices[i]);
  const u = [b[0]-a[0], b[1]-a[1], b[2]-a[2]], w = [c[0]-a[0], c[1]-a[1], c[2]-a[2]];
  const nn = [u[1]*w[2]-u[2]*w[1], u[2]*w[0]-u[0]*w[2], u[0]*w[1]-u[1]*w[0]];
  assert(nn[0]*(p[0]-a[0]) + nn[1]*(p[1]-a[1]) + nn[2]*(p[2]-a[2]) < 1e-6, 'point outside hull');
}
console.log('hull ok:', h.vertices.length, 'verts', h.polygons.length, 'faces');
