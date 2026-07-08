// galaxy.js — Memory Galaxy renderer (pure 2D Canvas, no WebGL).
//
// Why Canvas and not three.js: the vendored 3d-force-graph is a UMD bundle with
// its own three instance, so custom glow sprites built from our ESM three never
// rendered. This draws the whole scene by hand — manual 3D rotation + perspective
// projection, additive ("screen") radial-gradient glows — which is both robust
// and gives the soft, luminous look we're after. Technique mirrors galax.html.

// Per-category star colours as "r, g, b" strings. Index 0 leads white-violet so
// the densest project reads as the bright purple core; accents follow.
const GAL_PALETTE = [
  '238, 222, 255', // white-violet
  '194, 132, 255', // violet
  '135, 225, 255', // ice blue
  '255, 190, 115', // amber
  '255, 104, 150', // rose
  '72, 224, 205', // teal
  '108, 146, 255', // blue
];

// 3D force-directed layout (Fruchterman–Reingold) so linked memories cluster.
// Mutates each node with x/y/z, then flattens toward a galactic disc.
function layout3D(nodes, edges) {
  const n = nodes.length;
  for (const nd of nodes) {
    nd.x = (Math.random() - 0.5) * 360;
    nd.y = (Math.random() - 0.5) * 220;
    nd.z = (Math.random() - 0.5) * 300;
  }
  if (!n) return;
  const k = 210;                                  // ideal edge length (larger = more spread)
  const iters = n > 350 ? 220 : 340;
  let temp = 115;
  const dx = new Float64Array(n), dy = new Float64Array(n), dz = new Float64Array(n);
  for (let it = 0; it < iters; it++) {
    dx.fill(0); dy.fill(0); dz.fill(0);
    for (let i = 0; i < n; i++) {                 // repulsion (all pairs)
      for (let j = i + 1; j < n; j++) {
        let ax = nodes[i].x - nodes[j].x, ay = nodes[i].y - nodes[j].y, az = nodes[i].z - nodes[j].z;
        const d2 = ax * ax + ay * ay + az * az + 0.01, d = Math.sqrt(d2), f = (k * k) / d2;
        ax = ax / d * f; ay = ay / d * f; az = az / d * f;
        dx[i] += ax; dy[i] += ay; dz[i] += az;
        dx[j] -= ax; dy[j] -= ay; dz[j] -= az;
      }
    }
    for (const [a, b] of edges) {                 // attraction (along links)
      let ax = nodes[a].x - nodes[b].x, ay = nodes[a].y - nodes[b].y, az = nodes[a].z - nodes[b].z;
      const d = Math.sqrt(ax * ax + ay * ay + az * az) + 0.01, f = (d * d) / k;
      ax = ax / d * f; ay = ay / d * f; az = az / d * f;
      dx[a] -= ax; dy[a] -= ay; dz[a] -= az;
      dx[b] += ax; dy[b] += ay; dz[b] += az;
    }
    for (let i = 0; i < n; i++) {                 // displace (temp-capped) + gentle centering
      const d = Math.sqrt(dx[i] * dx[i] + dy[i] * dy[i] + dz[i] * dz[i]) + 0.01, m = Math.min(d, temp);
      nodes[i].x += dx[i] / d * m - nodes[i].x * 0.006;
      nodes[i].y += dy[i] / d * m - nodes[i].y * 0.006;
      nodes[i].z += dz[i] / d * m - nodes[i].z * 0.006;
    }
    temp *= 0.985;
  }
  // Normalise to a consistent radius, then squash into a disc for the galaxy feel.
  let max = 1;
  for (const nd of nodes) max = Math.max(max, Math.hypot(nd.x, nd.y, nd.z));
  const s = 760 / max;
  for (const nd of nodes) { nd.x *= s * 1.42; nd.y *= s * 0.48; nd.z *= s * 0.72; }
}

// Faint, non-interactive background dust for depth — rotates with the galaxy.
function makeDust(count = 420) {
  const dust = [];
  for (let i = 0; i < count; i++) {
    const r = 170 + Math.random() * 720;
    const theta = Math.random() * Math.PI * 2, phi = Math.acos(2 * Math.random() - 1);
    dust.push({
      x: r * Math.sin(phi) * Math.cos(theta),
      y: r * Math.cos(phi) * 0.48,
      z: r * Math.sin(phi) * Math.sin(theta),
      a: 0.08 + Math.random() * 0.42,
      size: 0.45 + Math.random() * 1.25,
    });
  }
  return dust;
}

// Persisted across re-mounts so a background data refresh (SSE) is seamless:
// the camera keeps the user's zoom/rotation, star positions stay put, and the
// dust field doesn't reshuffle. camState also holds the default framing.
const camState = {
  angleX: -0.28, angleY: -0.1, targetAngleX: -0.28, targetAngleY: -0.1,
  zoom: 4.18, targetZoom: 4.18, autoRotate: true,   // zoom = how close (bigger = closer)
};
const LAYOUT_VERSION = 'real-nodes-wide-v3';
const posCache = new Map();   // node id → {x, y, z}
let dustCache = null;

// Mount the galaxy into `el`. Contract mirrors the old forceGraph3D:
//   { nodes, links, onNodeClick, onNodeHover }
// nodes: { id, name, category, recency (0..1), project, mtime, ... }
// links: { source, target, kind } where source/target are node ids.
export function galaxyCanvas(el, { nodes: rawNodes, links: rawLinks, onNodeClick, onNodeHover }) {
  const canvas = document.createElement('canvas');
  canvas.style.cssText = 'display:block;width:100%;height:100%;cursor:grab';
  el.appendChild(canvas);
  const ctx = canvas.getContext('2d');

  // Build internal node objects + resolve links to index pairs / references.
  const nodes = rawNodes.map(n => ({
    ref: n, id: n.id, name: n.name,
    color: GAL_PALETTE[(((n.category ?? 0) % GAL_PALETTE.length) + GAL_PALETTE.length) % GAL_PALETTE.length],
    recency: Number.isFinite(n.recency) ? Math.max(0, Math.min(1, n.recency)) : 0.5,
    connections: 0, radius: 3, pulse: Math.random() * Math.PI,
    x: 0, y: 0, z: 0, projX: 0, projY: 0, projScale: 0,
  }));
  const byId = new Map(nodes.map((n, i) => [n.id, i]));
  const edges = [];
  const links = [];
  for (const l of rawLinks) {
    const a = byId.get(l.source), b = byId.get(l.target);
    if (a == null || b == null) continue;
    edges.push([a, b]);
    nodes[a].connections++; nodes[b].connections++;
    links.push({ s: nodes[a], t: nodes[b], soft: l.kind === 'soft' });
  }
  for (const n of nodes) {
    n.radius = 4.6 + Math.min(n.connections, 18) * 0.52 + n.recency * 3.1;
  }
  // Label only the most-connected stars so the field stays legible (others on hover).
  const labelCap = Math.max(8, Math.min(18, Math.round(nodes.length * 0.12)));
  const labelled = new Set([...nodes].sort((a, b) => b.connections - a.connections).slice(0, labelCap));

  // Reuse cached positions when the node set is unchanged so a refresh doesn't
  // teleport stars; relayout (and refresh the cache) only when new nodes appear.
  const layoutSig = `${LAYOUT_VERSION}:${nodes.length}:${links.length}`;
  if (posCache.sig !== layoutSig || nodes.some(n => !posCache.has(n.id))) {
    layout3D(nodes, edges);
    posCache.clear();
    posCache.sig = layoutSig;
    for (const n of nodes) posCache.set(n.id, { x: n.x, y: n.y, z: n.z });
  } else {
    for (const n of nodes) { const p = posCache.get(n.id); n.x = p.x; n.y = p.y; n.z = p.z; }
  }
  const dust = (dustCache ||= makeDust(260));

  // ── camera state (restored from the persisted camState) ─────────────────────
  const FOV = 350;
  let angleX = camState.angleX, angleY = camState.angleY;
  let targetAngleX = camState.targetAngleX, targetAngleY = camState.targetAngleY;
  let zoom = camState.zoom, targetZoom = camState.targetZoom;
  let autoRotate = camState.autoRotate;
  const saveCam = () => Object.assign(camState,
    { angleX, angleY, targetAngleX, targetAngleY, zoom, targetZoom, autoRotate });
  let isDragging = false, movedFar = false;
  let prev = { x: 0, y: 0 };
  let hovered = null;
  let W = 0, H = 0;

  const dpr = () => Math.min(window.devicePixelRatio || 1, 2);
  const resize = () => {
    W = el.clientWidth; H = el.clientHeight;
    const r = dpr();
    canvas.width = Math.round(W * r); canvas.height = Math.round(H * r);
  };
  resize();
  const ro = new ResizeObserver(resize);
  ro.observe(el);

  // ── interaction ───────────────────────────────────────────────────────────
  const localXY = e => {
    const r = canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };
  const pick = (mx, my) => {
    let found = null, front = -Infinity;
    for (const n of nodes) {
      if (n.projScale <= 0) continue;
      const hit = n.projScale * n.radius + 12;
      if (Math.hypot(n.projX - mx, n.projY - my) < hit && n.projScale > front) {
        front = n.projScale; found = n;
      }
    }
    return found;
  };

  const onDown = e => {
    isDragging = true; movedFar = false;
    prev = { x: e.clientX, y: e.clientY };
  };
  const onMove = e => {
    if (isDragging) {
      const dx = e.clientX - prev.x, dy = e.clientY - prev.y;
      if (Math.abs(dx) + Math.abs(dy) > 4) movedFar = true;
      targetAngleY += dx * 0.005;
      targetAngleX = Math.max(-1.4, Math.min(1.4, targetAngleX + dy * 0.005));
      prev = { x: e.clientX, y: e.clientY };
      return;
    }
    const { x, y } = localXY(e);
    const next = pick(x, y);
    if (next !== hovered) {
      hovered = next;
      canvas.style.cursor = next ? 'pointer' : 'grab';
      onNodeHover?.(next ? next.ref : null);
    }
  };
  const onUp = e => {
    if (isDragging && !movedFar) {
      const { x, y } = localXY(e);
      const n = pick(x, y);
      if (n) onNodeClick?.(n.ref);
    }
    isDragging = false;
  };
  const onWheel = e => {
    e.preventDefault();
    targetZoom *= e.deltaY < 0 ? 1.1 : 1 / 1.1;
    targetZoom = Math.max(0.5, Math.min(targetZoom, 5.0));
  };
  const onDbl = () => { autoRotate = !autoRotate; };

  canvas.addEventListener('mousedown', onDown);
  canvas.addEventListener('wheel', onWheel, { passive: false });
  canvas.addEventListener('dblclick', onDbl);
  window.addEventListener('mousemove', onMove);
  window.addEventListener('mouseup', onUp);

  // ── render loop ───────────────────────────────────────────────────────────
  let raf = 0;
  const render = () => {
    angleX += (targetAngleX - angleX) * 0.08;
    angleY += (targetAngleY - angleY) * 0.08;
    zoom += (targetZoom - zoom) * 0.08;
    if (!isDragging && autoRotate) targetAngleY += 0.0012;

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.setTransform(dpr(), 0, 0, dpr(), 0, 0);

    const cx = W / 2, cy = H / 2 + 10;
    const cosX = Math.cos(angleX), sinX = Math.sin(angleX);
    const cosY = Math.cos(angleY), sinY = Math.sin(angleY);
    const project = p => {
      const x1 = p.x * cosY - p.z * sinY;
      const z1 = p.z * cosY + p.x * sinY;
      const y2 = p.y * cosX - z1 * sinX;
      const z2 = z1 * cosX + p.y * sinX;
      const fz = z2 - 450 / zoom;
      const scale = FOV / (FOV - fz);
      return { px: cx + x1 * scale, py: cy + y2 * scale, scale };
    };

    ctx.globalCompositeOperation = 'source-over';

    // Canvas-side atmosphere keeps screenshots consistent even if CSS is disabled.
    const bg = ctx.createRadialGradient(W * 0.5, H * 0.52, 0, W * 0.5, H * 0.52, Math.max(W, H) * 0.8);
    bg.addColorStop(0, 'rgba(42, 17, 72, 0.92)');
    bg.addColorStop(0.42, 'rgba(17, 8, 38, 0.96)');
    bg.addColorStop(1, 'rgba(5, 3, 18, 1)');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, W, H);

    ctx.globalCompositeOperation = 'screen';
    const nebulae = [
      [0.22, 0.46, 210, '125, 73, 214', 0.18],
      [0.50, 0.84, 260, '73, 24, 110', 0.16],
      [0.72, 0.34, 180, '92, 65, 170', 0.13],
    ];
    for (const [x, y, r, rgb, a] of nebulae) {
      const g = ctx.createRadialGradient(W * x, H * y, 0, W * x, H * y, r);
      g.addColorStop(0, `rgba(${rgb}, ${a})`);
      g.addColorStop(0.58, `rgba(${rgb}, ${a * 0.32})`);
      g.addColorStop(1, `rgba(${rgb}, 0)`);
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(W * x, H * y, r, 0, Math.PI * 2);
      ctx.fill();
    }

    // Background dust.
    for (const d of dust) {
      const { px, py, scale } = project(d);
      if (scale <= 0) continue;
      ctx.fillStyle = `rgba(220, 213, 255, ${d.a * Math.min(scale, 1.25) * 0.62})`;
      ctx.beginPath();
      ctx.arc(px, py, Math.max(scale * d.size, 0.35), 0, Math.PI * 2);
      ctx.fill();
    }

    // Project stars, then painter's-algorithm sort (far → near).
    for (const n of nodes) {
      const { px, py, scale } = project(n);
      n.projX = px; n.projY = py; n.projScale = scale;
    }
    nodes.sort((a, b) => a.projScale - b.projScale);

    // Real memory links.
    for (const l of links) {
      if (l.s.projScale <= 0 || l.t.projScale <= 0) continue;
      const mid = (l.s.projScale + l.t.projScale) / 2;
      let alpha = (l.soft ? 0.14 : 0.22) * mid, weight = (l.soft ? 0.62 : 0.95) * mid;
      if (hovered && (hovered === l.s || hovered === l.t)) { alpha = 0.72; weight = 1.75; }
      ctx.strokeStyle = `rgba(177, 151, 246, ${Math.min(alpha, 0.82)})`;
      ctx.lineWidth = weight;
      ctx.beginPath();
      ctx.moveTo(l.s.projX, l.s.projY);
      ctx.lineTo(l.t.projX, l.t.projY);
      ctx.stroke();
    }

    // Stars: soft glow + bright core + optional label.
    for (const n of nodes) {
      if (!(n.projScale > 0) || !Number.isFinite(n.projX) || !Number.isFinite(n.projY)) continue;
      n.pulse += 0.03;
      const base = n.radius * n.projScale * (1 + Math.sin(n.pulse) * 0.08);
      const isHover = hovered === n;
      const glowR = base * (n.connections * 0.72 + 5.9 + n.recency * 3.2) * (isHover ? 1.45 : 1);
      const op = isHover ? 0.9
        : Math.min(0.13 * n.projScale * (n.connections * 0.32 + 1.35) * (0.72 + 0.58 * n.recency), 0.52);

      if (n.connections >= 6) {
        const halo = ctx.createRadialGradient(n.projX, n.projY, 0, n.projX, n.projY, glowR * 1.18);
        halo.addColorStop(0, `rgba(${n.color}, ${op * 0.14})`);
        halo.addColorStop(0.34, `rgba(${n.color}, ${op * 0.08})`);
        halo.addColorStop(1, `rgba(${n.color}, 0)`);
        ctx.fillStyle = halo;
        ctx.beginPath();
        ctx.arc(n.projX, n.projY, glowR * 1.18, 0, Math.PI * 2);
        ctx.fill();
      }

      const g = ctx.createRadialGradient(n.projX, n.projY, base * 0.1, n.projX, n.projY, glowR);
      g.addColorStop(0, `rgba(${n.color}, ${op})`);
      g.addColorStop(0.2, `rgba(${n.color}, ${op * 0.42})`);
      g.addColorStop(0.6, `rgba(${n.color}, ${op * 0.08})`);
      g.addColorStop(1, `rgba(${n.color}, 0)`);
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(n.projX, n.projY, glowR, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = isHover ? '#fff'
        : `rgba(${n.color}, ${Math.min(0.72 * n.projScale + 0.34 + 0.28 * n.recency, 1)})`;
      ctx.beginPath();
      ctx.arc(n.projX, n.projY, Math.max(base * 0.78, 2.4), 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = `rgba(255, 255, 255, ${isHover ? 0.98 : 0.86})`;
      ctx.beginPath();
      ctx.arc(n.projX - base * 0.13, n.projY - base * 0.16, Math.max(base * 0.28, 1.0), 0, Math.PI * 2);
      ctx.fill();

      if ((labelled.has(n) && n.projScale > 0.95) || isHover) {
        const ta = isHover ? 1 : Math.min((n.projScale - 0.9) * 2.5, 0.9);
        ctx.fillStyle = isHover ? '#fff' : `rgba(235, 230, 250, ${ta})`;
        ctx.font = `${isHover ? 700 : 600} ${Math.max(11, 10.8 * n.projScale)}px var(--sans, sans-serif)`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.shadowColor = '#05030d';
        ctx.shadowBlur = 7;
        ctx.lineWidth = 3;
        ctx.strokeStyle = 'rgba(5, 3, 13, 0.78)';
        ctx.strokeText(n.name, n.projX, n.projY + base + 6);
        ctx.fillText(n.name, n.projX, n.projY + base + 6);
        ctx.shadowBlur = 0;
      }
    }

    ctx.globalCompositeOperation = 'source-over';
    const vignette = ctx.createRadialGradient(W * 0.5, H * 0.48, Math.min(W, H) * 0.18, W * 0.5, H * 0.5, Math.max(W, H) * 0.62);
    vignette.addColorStop(0, 'rgba(0, 0, 0, 0)');
    vignette.addColorStop(0.68, 'rgba(0, 0, 0, 0.18)');
    vignette.addColorStop(1, 'rgba(0, 0, 0, 0.58)');
    ctx.fillStyle = vignette;
    ctx.fillRect(0, 0, W, H);

    saveCam();   // keep the persisted camera in sync so a re-mount resumes here
    raf = requestAnimationFrame(render);
  };
  raf = requestAnimationFrame(render);

  return {
    __teardown() {
      saveCam();
      cancelAnimationFrame(raf);
      ro.disconnect();
      canvas.removeEventListener('mousedown', onDown);
      canvas.removeEventListener('wheel', onWheel);
      canvas.removeEventListener('dblclick', onDbl);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      canvas.remove();
    },
  };
}
