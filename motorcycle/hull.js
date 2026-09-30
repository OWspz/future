// 3D 凸包（增量式 / incremental convex hull）
// 輸入: [[x,y,z],...]  輸出: { vertices:[[x,y,z],...], polygons:[[i,j,k,...],...] }
// 多邊形頂點順序為逆時針（從外側看），共面的三角形會被合併成一個多邊形。
(function (root) {
  const EPS = 1e-7;
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const len = (a) => Math.sqrt(dot(a, a));

  function dedupe(points) {
    const seen = new Map(), out = [];
    for (const p of points) {
      const k = p.map((v) => Math.round(v / 1e-6)).join(',');
      if (!seen.has(k)) { seen.set(k, 1); out.push(p); }
    }
    return out;
  }

  function makeFace(pts, a, b, c) {
    const n = cross(sub(pts[b], pts[a]), sub(pts[c], pts[a]));
    const l = len(n);
    const nn = [n[0] / l, n[1] / l, n[2] / l];
    return { v: [a, b, c], n: nn, d: dot(nn, pts[a]) };
  }

  function convexHull(input) {
    const pts = dedupe(input);
    if (pts.length < 4) throw new Error('need >= 4 points');

    // --- 找初始四面體 ---
    let i0 = 0, i1 = -1, i2 = -1, i3 = -1, best = 0;
    for (let i = 1; i < pts.length; i++) { const d = len(sub(pts[i], pts[i0])); if (d > best) { best = d; i1 = i; } }
    best = 0;
    const dir = sub(pts[i1], pts[i0]);
    for (let i = 0; i < pts.length; i++) {
      const d = len(cross(dir, sub(pts[i], pts[i0])));
      if (d > best) { best = d; i2 = i; }
    }
    if (best < EPS) throw new Error('points are collinear');
    const pn = cross(dir, sub(pts[i2], pts[i0]));
    best = 0;
    for (let i = 0; i < pts.length; i++) {
      const d = Math.abs(dot(pn, sub(pts[i], pts[i0]))) / len(pn);
      if (d > best) { best = d; i3 = i; }
    }
    if (best < EPS) throw new Error('points are coplanar');

    let faces = [];
    const tri = (a, b, c) => faces.push(makeFace(pts, a, b, c));
    if (dot(pn, sub(pts[i3], pts[i0])) > 0) { tri(i0, i2, i1); tri(i0, i1, i3); tri(i1, i2, i3); tri(i2, i0, i3); }
    else { tri(i0, i1, i2); tri(i0, i3, i1); tri(i1, i3, i2); tri(i2, i3, i0); }

    // --- 逐點加入 ---
    for (let p = 0; p < pts.length; p++) {
      if (p === i0 || p === i1 || p === i2 || p === i3) continue;
      const visible = new Set();
      for (const f of faces) if (dot(f.n, pts[p]) - f.d > EPS) visible.add(f);
      if (!visible.size) continue; // 在凸包內部或共面

      // 地平線邊：可見面中、其反向邊不屬於任何可見面的邊
      const edges = new Set();
      for (const f of visible) for (let k = 0; k < 3; k++) edges.add(f.v[k] + '>' + f.v[(k + 1) % 3]);
      const next = [];
      for (const f of faces) if (!visible.has(f)) next.push(f);
      faces = next;
      for (const f of visible) for (let k = 0; k < 3; k++) {
        const a = f.v[k], b = f.v[(k + 1) % 3];
        if (!edges.has(b + '>' + a)) faces.push(makeFace(pts, a, b, p));
      }
    }

    // --- 合併共面三角形為多邊形 ---
    const groups = [];
    for (const f of faces) {
      let g = groups.find((g) => dot(g.n, f.n) > 1 - 1e-9 && Math.abs(g.d - f.d) < 1e-6);
      if (!g) groups.push((g = { n: f.n, d: f.d, faces: [] }));
      g.faces.push(f);
    }
    const polygons = [];
    for (const g of groups) {
      const directed = new Map(); // a -> b
      const all = new Set();
      for (const f of g.faces) for (let k = 0; k < 3; k++) all.add(f.v[k] + '>' + f.v[(k + 1) % 3]);
      for (const f of g.faces) for (let k = 0; k < 3; k++) {
        const a = f.v[k], b = f.v[(k + 1) % 3];
        if (!all.has(b + '>' + a)) directed.set(a, b);
      }
      let start = directed.keys().next().value, loop = [start], cur = directed.get(start);
      while (cur !== start && loop.length <= directed.size) { loop.push(cur); cur = directed.get(cur); }
      // 去除共線頂點
      const clean = loop.filter((v, i) => {
        const a = pts[loop[(i + loop.length - 1) % loop.length]], b = pts[v], c = pts[loop[(i + 1) % loop.length]];
        return len(cross(sub(b, a), sub(c, b))) > 1e-9;
      });
      if (clean.length >= 3) polygons.push(clean);
    }

    // --- 重新編號頂點 ---
    const remap = new Map(), vertices = [];
    for (const poly of polygons) for (const v of poly) if (!remap.has(v)) { remap.set(v, vertices.length); vertices.push(pts[v]); }
    return { vertices, polygons: polygons.map((poly) => poly.map((v) => remap.get(v))) };
  }

  function volume(h) {
    let vol = 0;
    for (const poly of h.polygons) for (let k = 1; k < poly.length - 1; k++)
      vol += dot(h.vertices[poly[0]], cross(h.vertices[poly[k]], h.vertices[poly[k + 1]])) / 6;
    return vol;
  }

  const api = { convexHull, volume };
  if (typeof module !== 'undefined') module.exports = api; else root.Hull = api;
})(this);
