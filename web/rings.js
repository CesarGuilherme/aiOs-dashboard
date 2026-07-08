// rings.js — "second brain" renderer v2.
// Scene is baked once to an offscreen bitmap (glow included), then every frame the
// bitmap is blitted with rotate/zoom/pan transforms — flat cost at 30k+ nodes.
// World space: center (0,0), radii in abstract units (outer ring ~ 900).

const PAL = ['#B48CFF', '#E08CD5', '#7FD8C9', '#E8D46B', '#8CB4FF', '#F0A87A', '#9BE07F', '#FF9BB0'];
const LAYER = {
  skills:       { color: '#E8944A', label: 'SKILLS' },
  memory:       { color: '#B48CFF', label: 'MEMORY' },
  routines:     { color: '#D9B944', label: 'ROUTINES' },
  applications: { color: '#7FA8E8', label: 'APPLICATIONS' },
};
const R_SKILLS = [120, 160, 200], R_HUB = 290, R_FILES0 = 340, R_ROUT = 700, R_APPS = 860;
const WORLD = 960;             // world half-extent baked
const BAKE_PX = 3600;          // offscreen bitmap edge (px)
const hash = s => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) | 0; return (h >>> 0) / 4294967295; };

function layout(nodes) {
  const center = { id: 'center', name: 'CLAUDE.MD', layer: 'center', group: '', size: 1, meta: {}, x: 0, y: 0, r: 16, color: '#E8944A' };
  const out = [center];
  const on = (n, a, rad, r, color) => { n.x = Math.cos(a) * rad; n.y = Math.sin(a) * rad; n.r = r; n.color = color; out.push(n); };

  // skills — sparkle rows around center
  const skills = nodes.filter(n => n.layer === 'skills');
  let si = 0;
  for (const row of R_SKILLS) {
    const cap = Math.floor((2 * Math.PI * row) / 34);
    for (let k = 0; k < cap && si < skills.length; k++, si++)
      on(skills[si], (k / cap) * 2 * Math.PI + row, row, 7, LAYER.skills.color);
  }
  for (; si < skills.length; si++)   // overflow: extra outer row
    on(skills[si], hash(skills[si].id) * 2 * Math.PI, R_SKILLS[2] + 34, 7, LAYER.skills.color);

  // routines / applications — evenly on their rings
  const ring = (list, rad, r, color) => list.forEach((n, i) =>
    on(n, (i / Math.max(1, list.length)) * 2 * Math.PI + 0.4, rad, r, color));
  ring(nodes.filter(n => n.layer === 'routines'), R_ROUT, 9, LAYER.routines.color);
  ring(nodes.filter(n => n.layer === 'applications'), R_APPS, 15, LAYER.applications.color);

  // departments — wedges of memories (inner rows) + files (arc rows)
  const mem = nodes.filter(n => n.layer === 'memory');
  const files = nodes.filter(n => n.layer === 'file');
  const depts = [...new Set([...mem, ...files].map(n => n.group))].sort();
  const weight = d => Math.sqrt(files.filter(f => f.group === d).length + 3 * mem.filter(m => m.group === d).length + 1);
  const totW = depts.reduce((s, d) => s + weight(d), 0) || 1;
  const GAP = 0.05;
  let a0 = -Math.PI / 2;
  const hubs = [];
  for (const [di, d] of depts.entries()) {
    const span = (weight(d) / totW) * 2 * Math.PI - GAP;
    const color = PAL[di % PAL.length];
    const mid = a0 + span / 2;
    const hub = { id: 'hub::' + d, name: d, layer: 'hub', group: d, size: 1, meta: { dept: d }, };
    on(hub, mid, R_HUB, 12, color);
    hubs.push(hub);
    // memories first (bigger orbs), then files, packed row by row outward
    const items = [
      ...mem.filter(m => m.group === d).map(m => ({ n: m, r: 6 + Math.min(6, m.size) })),
      ...files.filter(f => f.group === d).map(f => ({ n: f, r: Math.max(2.6, Math.min(9, 2 + 1.6 * Math.log10(1 + (f.size || 0) / 1024))) })),
    ];
    let rad = R_FILES0, i = 0;
    // shrink spacing so every item fits inside the band; min factor keeps dots legible
    let sA = 22, sR = 24;
    let est = 0;
    for (let r = R_FILES0; r < R_ROUT - 40; r += sR) est += Math.max(1, Math.floor((span * r) / sA));
    if (items.length > est && est > 0) {
      const f = Math.sqrt(est / items.length);
      sA *= f; sR *= f;
    }
    while (i < items.length) {   // no radius ceiling — every item gets placed
      const cap = Math.max(1, Math.floor((span * rad) / sA));
      for (let k = 0; k < cap && i < items.length; k++, i++) {
        const it = items[i];
        on(it.n, a0 + ((k + 0.5) / cap) * span, rad + (hash(it.n.id) - 0.5) * 8, Math.max(2, Math.min(it.r, sR * 0.45)), color);
      }
      // ponytail: rows past the cap stack at WORLD-60; smarter packing if a dept ever needs it
      rad = Math.min(rad + sR, WORLD - 60);
    }
    a0 += span + GAP;
  }
  return { all: out.concat(), center, hubs };
}

function drawNode(ctx, n) {
  ctx.shadowColor = n.color; ctx.shadowBlur = n.r * 2.2;
  ctx.fillStyle = n.color; ctx.strokeStyle = n.color;
  if (n.layer === 'skills') {                       // 4-point sparkle
    ctx.beginPath();
    for (let i = 0; i < 8; i++) {
      const a = (i / 8) * 2 * Math.PI, rr = i % 2 ? n.r * 0.35 : n.r;
      ctx[i ? 'lineTo' : 'moveTo'](n.x + Math.cos(a) * rr, n.y + Math.sin(a) * rr);
    }
    ctx.closePath(); ctx.fill();
  } else if (n.layer === 'applications') {          // hexagon badge
    ctx.beginPath();
    for (let i = 0; i < 6; i++) {
      const a = (i / 6) * 2 * Math.PI + Math.PI / 6;
      ctx[i ? 'lineTo' : 'moveTo'](n.x + Math.cos(a) * n.r, n.y + Math.sin(a) * n.r);
    }
    ctx.closePath();
    ctx.globalAlpha = 0.25; ctx.fill(); ctx.globalAlpha = 1;
    ctx.lineWidth = 2; ctx.stroke();
  } else if (n.layer === 'routines') {              // circled dot
    ctx.beginPath(); ctx.arc(n.x, n.y, n.r * 0.4, 0, 2 * Math.PI); ctx.fill();
    ctx.lineWidth = 1.4; ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, 2 * Math.PI); ctx.stroke();
  } else {                                          // glowing orb
    ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, 2 * Math.PI); ctx.fill();
  }
}

export function ringsCanvas(el, { nodes, links, onNodeClick, onNodeHover }) {
  const canvas = document.createElement('canvas');
  canvas.style.cssText = 'width:100%;height:100%;display:block;cursor:grab';
  el.appendChild(canvas);
  const ctx = canvas.getContext('2d');

  const { all, center, hubs } = layout(nodes);
  const byId = new Map(all.map(n => [n.id, n]));
  const edges = links.map(l => [byId.get(l.source), byId.get(l.target)]).filter(([a, b]) => a && b);
  hubs.forEach(h => edges.push([center, h]));

  // spatial grid for hit-testing (world coords)
  const grid = new Map();
  const cell = 48, key = (x, y) => `${Math.floor(x / cell)},${Math.floor(y / cell)}`;
  for (const n of all) {
    const k = key(n.x, n.y);
    (grid.get(k) || grid.set(k, []).get(k)).push(n);
  }

  let spin = +(localStorage.getItem('td.rings.spin') ?? 0.12);
  let labels = localStorage.getItem('td.rings.labels') !== '0';
  let filter = null, hovered = null, dead = false, raf = null, last = performance.now();
  let rot = 0, zoom = 0.9, panX = 0, panY = 0, fly = null;

  // --- offscreen bake
  const off = document.createElement('canvas');
  off.width = off.height = BAKE_PX;
  const os = BAKE_PX / (2 * WORLD);
  function bake() {
    const c = off.getContext('2d');
    c.setTransform(os, 0, 0, os, BAKE_PX / 2, BAKE_PX / 2);
    c.clearRect(-WORLD, -WORLD, 2 * WORLD, 2 * WORLD);
    // guide rings
    c.shadowBlur = 0;
    for (const [rad, col] of [[R_ROUT, LAYER.routines.color], [R_APPS, LAYER.applications.color], [R_FILES0 - 60, LAYER.memory.color]]) {
      c.beginPath(); c.arc(0, 0, rad, 0, 2 * Math.PI);
      c.strokeStyle = col + '33'; c.lineWidth = 1.2; c.stroke();
    }
    // links
    c.lineWidth = 0.7;
    for (const [a, b] of edges) {
      const dim = filter && !(a.name.toLowerCase().includes(filter) || b.name.toLowerCase().includes(filter));
      c.strokeStyle = dim ? 'rgba(170,160,210,0.02)' : 'rgba(170,160,210,0.10)';
      c.beginPath(); c.moveTo(a.x, a.y);
      c.quadraticCurveTo((a.x + b.x) / 2 * 0.6, (a.y + b.y) / 2 * 0.6, b.x, b.y);
      c.stroke();
    }
    for (const n of all) {
      c.globalAlpha = filter && !n.name.toLowerCase().includes(filter) ? 0.10 : 1;
      drawNode(c, n);
    }
    c.globalAlpha = 1; c.shadowBlur = 0;
    // hub + center + fixed-node labels (baked; they rotate slowly with the scene)
    c.fillStyle = 'rgba(235,230,250,0.9)'; c.font = '600 15px system-ui'; c.textAlign = 'center';
    for (const h of hubs) c.fillText(h.name.toUpperCase(), h.x, h.y + 30);
    c.fillText('CLAUDE.MD', 0, 34);
    if (labels) {
      c.font = '11px system-ui'; c.fillStyle = 'rgba(220,215,240,0.7)';
      for (const n of all)
        if (['applications', 'routines', 'skills'].includes(n.layer))
          c.fillText(n.name, n.x, n.y + n.r + 12);
    }
  }
  let bakeTimer = null;
  const rebake = () => { clearTimeout(bakeTimer); bakeTimer = setTimeout(() => { bake(); draw(); }, 120); };

  // --- hex-grid background tile
  const tile = document.createElement('canvas'); tile.width = 48; tile.height = 42;
  { const t = tile.getContext('2d'); t.strokeStyle = 'rgba(140,130,180,0.05)'; t.lineWidth = 1;
    const hex = (cx, cy) => { t.beginPath(); for (let i = 0; i < 6; i++) { const a = (i / 6) * 2 * Math.PI + Math.PI / 6; t[i ? 'lineTo' : 'moveTo'](cx + 14 * Math.cos(a), cy + 14 * Math.sin(a)); } t.closePath(); t.stroke(); };
    hex(12, 10); hex(36, 31); }
  let pattern = null;

  const W = () => el.clientWidth, H = () => el.clientHeight;
  const screenScale = () => Math.min(W(), H()) / (2 * WORLD) * zoom * 2.0;

  function draw() {
    const w = W(), h = H(), s = screenScale();
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!pattern) pattern = ctx.createPattern(tile, 'repeat');
    ctx.fillStyle = pattern; ctx.fillRect(0, 0, w, h);
    ctx.translate(w / 2 + panX, h / 2 + panY);
    ctx.rotate(rot);
    ctx.scale(s / os, s / os);
    ctx.drawImage(off, -BAKE_PX / 2, -BAKE_PX / 2);
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    // upright band labels at 12 o'clock
    ctx.textAlign = 'center'; ctx.font = '700 13px system-ui';
    for (const [rad, L] of [[R_APPS, LAYER.applications], [R_ROUT, LAYER.routines], [R_FILES0 - 60, LAYER.memory], [R_SKILLS[2], LAYER.skills]]) {
      ctx.fillStyle = L.color;
      ctx.fillText(L.label, w / 2 + panX, h / 2 + panY - rad * s - 6);
    }
    if (hovered) {   // hovered node label, upright
      const [x, y] = toScreen(hovered);
      ctx.fillStyle = '#fff'; ctx.font = '600 12px system-ui';
      ctx.fillText(hovered.name, x, y - hovered.r * s - 6);
    }
  }

  const toScreen = n => {
    const s = screenScale();
    const x = n.x * Math.cos(rot) - n.y * Math.sin(rot);
    const y = n.x * Math.sin(rot) + n.y * Math.cos(rot);
    return [W() / 2 + panX + x * s, H() / 2 + panY + y * s];
  };
  const toWorld = (mx, my) => {
    const s = screenScale();
    const x = (mx - W() / 2 - panX) / s, y = (my - H() / 2 - panY) / s;
    return [x * Math.cos(-rot) - y * Math.sin(-rot), x * Math.sin(-rot) + y * Math.cos(-rot)];
  };
  const hit = (mx, my) => {
    const [wx, wy] = toWorld(mx, my);
    let best = null, bd = (14 / screenScale()) ** 2;
    for (let gx = -1; gx <= 1; gx++) for (let gy = -1; gy <= 1; gy++)
      for (const n of grid.get(key(wx + gx * cell, wy + gy * cell)) || []) {
        const d = (n.x - wx) ** 2 + (n.y - wy) ** 2;
        if (d < bd) { bd = d; best = n; }
      }
    return best;
  };

  function tick(now) {
    if (dead) return;
    const dt = (now - last) / 1000; last = now;
    let busy = false;
    if (spin > 0) { rot += dt * spin * 0.15; busy = true; }
    if (fly) {
      fly.t = Math.min(1, fly.t + dt / 0.6);
      const e = 1 - (1 - fly.t) ** 3;
      zoom = fly.z0 + (fly.z1 - fly.z0) * e;
      panX = fly.x0 + (fly.x1 - fly.x0) * e;
      panY = fly.y0 + (fly.y1 - fly.y0) * e;
      if (fly.t >= 1) fly = null;
      busy = true;
    }
    if (busy) { draw(); raf = requestAnimationFrame(tick); } else raf = null;
  }
  const ensureLoop = () => { if (!raf && (spin > 0 || fly)) { last = performance.now(); raf = requestAnimationFrame(tick); } };

  let dragging = false, moved = false, sx = 0, sy = 0;
  const onDown = e => { dragging = true; moved = false; sx = e.clientX; sy = e.clientY; canvas.style.cursor = 'grabbing'; };
  const onMove = e => {
    const rect = canvas.getBoundingClientRect();
    if (dragging) {
      panX += e.clientX - sx; panY += e.clientY - sy;
      if (Math.abs(e.clientX - sx) + Math.abs(e.clientY - sy) > 3) moved = true;
      sx = e.clientX; sy = e.clientY; draw(); return;
    }
    const h = hit(e.clientX - rect.left, e.clientY - rect.top);
    if (h !== hovered) { hovered = h; onNodeHover?.(h); draw(); }
  };
  const onUp = () => {
    if (dragging && !moved && hovered) onNodeClick?.(hovered);
    dragging = false; canvas.style.cursor = 'grab';
  };
  const onWheel = e => { e.preventDefault(); zoom = Math.min(10, Math.max(0.3, zoom * (e.deltaY < 0 ? 1.1 : 0.9))); draw(); };
  canvas.addEventListener('mousedown', onDown);
  canvas.addEventListener('mousemove', onMove);
  window.addEventListener('mouseup', onUp);
  canvas.addEventListener('wheel', onWheel, { passive: false });
  const ro = new ResizeObserver(() => {
    canvas.width = W() * devicePixelRatio; canvas.height = H() * devicePixelRatio;
    pattern = null; draw();
  });
  ro.observe(el);
  canvas.width = W() * devicePixelRatio; canvas.height = H() * devicePixelRatio;
  bake(); draw(); ensureLoop();

  return {
    __teardown() {
      dead = true;
      if (raf) cancelAnimationFrame(raf);
      clearTimeout(bakeTimer);
      ro.disconnect();
      canvas.removeEventListener('mousedown', onDown);
      canvas.removeEventListener('mousemove', onMove);
      canvas.removeEventListener('wheel', onWheel);
      window.removeEventListener('mouseup', onUp);
      canvas.remove();
    },
    setFilter(q) { filter = q ? q.toLowerCase() : null; rebake(); },
    setLabels(v) { labels = !!v; localStorage.setItem('td.rings.labels', v ? '1' : '0'); rebake(); },
    setSpin(v) { spin = +v; localStorage.setItem('td.rings.spin', String(v)); ensureLoop(); if (!spin) draw(); },
    get spin() { return spin; },
    get labels() { return labels; },
    flyTo(id) {
      const n = byId.get(id);
      if (!n) return;
      const s0 = Math.min(W(), H()) / (2 * WORLD) * 2.0;   // screenScale at zoom=1
      const z1 = 3;
      const x = n.x * Math.cos(rot) - n.y * Math.sin(rot);
      const y = n.x * Math.sin(rot) + n.y * Math.cos(rot);
      fly = { t: 0, z0: zoom, z1, x0: panX, y0: panY, x1: -x * s0 * z1, y1: -y * s0 * z1 };
      hovered = n; onNodeHover?.(n);
      ensureLoop();
    },
  };
}
