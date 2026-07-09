// rings.js — "second brain" renderer v3, matched to the reference frames:
// honeycomb lattice, hex app badges with glyphs, gold orbit routines, folder
// orbs with count labels + Saturn rings that explode into subfolders on click,
// and hover highlighting that dims everything not connected to the node.
// Scene is baked to an offscreen bitmap; per-frame blit with rotate/zoom/pan.
// Expanding a folder re-runs layout + bake (cheap, user-triggered).

const PAL = ['#C9B8F0', '#EEB0DF', '#9FE3D4', '#EFE3A0', '#A8C8F5', '#F5C09A', '#B8E89E', '#F5A8B8'];
const LAYER = {
  skills:       { color: '#E8944A', label: 'SKILLS' },
  memory:       { color: '#B48CFF', label: 'MEMORY' },
  routines:     { color: '#D9B944', label: 'ROUTINES' },
  applications: { color: '#8FB4E3', label: 'APPLICATIONS' },
};
const R_SKILLS = [130, 168, 206], R_HUB = 300, R_ITEMS0 = 350, R_ROUT = 700, R_APPS = 860;
const WORLD = 980;
const BAKE_PX = 3600;
const hash = s => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) | 0; return (h >>> 0) / 4294967295; };
const folderR = count => Math.min(28, 9 + 5 * Math.log10(1 + count));

export function ringsCanvas(el, { nodes, links, onNodeClick, onNodeHover }) {
  const canvas = document.createElement('canvas');
  canvas.style.cssText = 'width:100%;height:100%;display:block;cursor:grab';
  el.appendChild(canvas);
  const ctx = canvas.getContext('2d');

  const expanded = new Set();          // folder ids currently exploded
  let all = [], byId = new Map(), edges = [], adj = new Map(), hubs = [], center = null;
  const grid = new Map();
  const cell = 48, gKey = (x, y) => `${Math.floor(x / cell)},${Math.floor(y / cell)}`;

  // ---- layout: deterministic polar placement -------------------------------
  const fileNode = (f, group, color) => ({
    id: 'file::' + f.path, name: f.name, layer: 'file', group,
    size: f.size, meta: { ...f }, _color: color,
  });

  function layout() {
    all = []; hubs = [];
    center = { id: 'center', name: 'CLAUDE.MD', layer: 'center', group: '', meta: {}, x: 0, y: 0, r: 30, _color: '#E8944A' };
    all.push(center);
    const place = (n, a, rad, r, color) => {
      n.x = Math.cos(a) * rad; n.y = Math.sin(a) * rad; n.r = r;
      n._color = color;
      all.push(n);
    };

    const skills = nodes.filter(n => n.layer === 'skills');
    let si = 0;
    for (const row of R_SKILLS) {
      const cap = Math.floor((2 * Math.PI * row) / 30);
      for (let k = 0; k < cap && si < skills.length; k++, si++)
        place(skills[si], (k / cap) * 2 * Math.PI + row, row, 7, LAYER.skills.color);
    }
    for (; si < skills.length; si++)
      place(skills[si], hash(skills[si].id) * 2 * Math.PI, R_SKILLS[2] + 34, 7, LAYER.skills.color);

    const ring = (list, rad, r, color) => list.forEach((n, i) =>
      place(n, (i / Math.max(1, list.length)) * 2 * Math.PI + 0.4, rad, r, color));
    ring(nodes.filter(n => n.layer === 'routines'), R_ROUT, 8, LAYER.routines.color);
    ring(nodes.filter(n => n.layer === 'applications'), R_APPS, 26, LAYER.applications.color);

    // departments: memories + folder orbs (+ exploded children), packed in wedges
    const mem = nodes.filter(n => n.layer === 'memory');
    const folders = nodes.filter(n => n.layer === 'folder');
    const depts = [...new Set([...mem, ...folders].map(n => n.group))].sort();
    const weight = d => Math.sqrt(
      folders.filter(f => f.group === d).reduce((s, f) => s + Math.sqrt(f.count || 1), 0) +
      3 * mem.filter(m => m.group === d).length + 1);
    const totW = depts.reduce((s, d) => s + weight(d), 0) || 1;
    const GAP = 0.05;
    let a0 = -Math.PI / 2;
    for (const [di, d] of depts.entries()) {
      const span = (weight(d) / totW) * 2 * Math.PI - GAP;
      const color = PAL[di % PAL.length];
      const hub = { id: 'hub::' + d, name: d, layer: 'hub', group: d, meta: { dept: d } };
      place(hub, a0 + span / 2, R_HUB, 15, color);
      hubs.push(hub);

      // visible items: memories, then folders (expanded ones bring children)
      const items = [];
      for (const m of mem.filter(m => m.group === d))
        items.push({ n: m, r: 6 + Math.min(6, m.size || 1) });
      for (const f of folders.filter(f => f.group === d)) {
        items.push({ n: f, r: folderR(f.count || 1) });
        if (expanded.has(f.id)) {
          for (const kid of f.kids || []) {
            items.push({ n: kid, r: folderR(kid.count || 1) * 0.8 });
            if (expanded.has(kid.id))
              for (const lf of kid.leaves || []) items.push({ n: fileNode(lf, d, color), r: 3 });
          }
          for (const lf of f.leaves || []) items.push({ n: fileNode(lf, d, color), r: 3 });
        }
      }
      // variable-width row packing: angle advance per item ∝ its diameter
      let rad = R_ITEMS0, aCur = 0, rowMax = 0;
      for (const it of items) {
        const need = (it.r * 2 + 12) / rad;
        if (aCur + need > span) {
          rad = Math.min(rad + rowMax * 2 + 14, WORLD - 60);   // ponytail: rows past the cap stack at WORLD-60
          aCur = 0; rowMax = 0;
        }
        place(it.n, a0 + aCur + need / 2, rad + (hash(it.n.id) - 0.5) * 6, it.r, color);
        aCur += need; rowMax = Math.max(rowMax, it.r);
      }
      a0 += span + GAP;
    }

    byId = new Map(all.map(n => [n.id, n]));
    edges = links.map(l => [byId.get(l.source), byId.get(l.target)]).filter(([a, b]) => a && b);
    for (const h of hubs) edges.push([center, h]);
    for (const f of nodes.filter(n => n.layer === 'folder')) {
      const fn = byId.get(f.id), hn = byId.get('hub::' + f.group);
      if (fn && hn) edges.push([hn, fn]);
      if (expanded.has(f.id))
        for (const kid of f.kids || []) { const kn = byId.get(kid.id); if (kn) edges.push([fn, kn]); }
    }
    adj = new Map();
    for (const [a, b] of edges) {
      (adj.get(a) || adj.set(a, new Set()).get(a)).add(b);
      (adj.get(b) || adj.set(b, new Set()).get(b)).add(a);
    }
    grid.clear();
    for (const n of all) {
      const k = gKey(n.x, n.y);
      (grid.get(k) || grid.set(k, []).get(k)).push(n);
    }
  }

  // ---- node painting --------------------------------------------------------
  function hexPath(c, x, y, r) {
    c.beginPath();
    for (let i = 0; i < 6; i++) {
      const a = (i / 6) * 2 * Math.PI - Math.PI / 2;
      c[i ? 'lineTo' : 'moveTo'](x + Math.cos(a) * r, y + Math.sin(a) * r);
    }
    c.closePath();
  }

  function drawNode(c, n) {
    const col = n._color;
    c.shadowColor = col;
    if (n.layer === 'applications') {
      c.shadowBlur = 18;
      hexPath(c, n.x, n.y, n.r);
      c.fillStyle = 'rgba(30,42,66,0.75)'; c.fill();
      c.shadowBlur = 0;
      c.strokeStyle = col; c.lineWidth = 1.6; c.stroke();
      c.fillStyle = '#F2F6FF'; c.font = `700 ${Math.round(n.r * 0.9)}px system-ui`;
      c.textAlign = 'center'; c.textBaseline = 'middle';
      c.fillText((n.name[0] || '?').toUpperCase(), n.x, n.y + 1);
      c.textBaseline = 'alphabetic';
    } else if (n.layer === 'routines') {
      c.shadowBlur = 22; c.fillStyle = col;
      c.beginPath(); c.arc(n.x, n.y, n.r * 0.55, 0, 2 * Math.PI); c.fill();
      c.shadowBlur = 0; c.strokeStyle = col; c.lineWidth = 1.3;
      c.beginPath(); c.arc(n.x, n.y, n.r * 2, 0, 2 * Math.PI); c.stroke();
      const sa = hash(n.id) * 2 * Math.PI;   // tiny satellite on the orbit
      c.beginPath(); c.arc(n.x + Math.cos(sa) * n.r * 2, n.y + Math.sin(sa) * n.r * 2, 2.4, 0, 2 * Math.PI); c.fill();
    } else if (n.layer === 'skills') {
      c.shadowBlur = 10; c.fillStyle = col;
      c.beginPath();
      for (let i = 0; i < 8; i++) {
        const a = (i / 8) * 2 * Math.PI, rr = i % 2 ? n.r * 0.36 : n.r;
        c[i ? 'lineTo' : 'moveTo'](n.x + Math.cos(a) * rr, n.y + Math.sin(a) * rr);
      }
      c.closePath(); c.fill();
    } else if (n.layer === 'hub') {
      c.shadowBlur = 26; c.fillStyle = col;
      c.beginPath(); c.arc(n.x, n.y, n.r, 0, 2 * Math.PI); c.fill();
      c.shadowBlur = 0; c.fillStyle = 'rgba(10,10,16,0.85)';
      c.font = `700 ${Math.round(n.r)}px system-ui`; c.textAlign = 'center'; c.textBaseline = 'middle';
      c.fillText((n.name[0] || '').toUpperCase(), n.x, n.y + 1);
      c.textBaseline = 'alphabetic';
    } else if (n.layer === 'center') {
      const g = c.createRadialGradient(n.x, n.y, 0, n.x, n.y, 120);
      g.addColorStop(0, 'rgba(232,148,74,0.30)'); g.addColorStop(1, 'rgba(232,148,74,0)');
      c.fillStyle = g; c.beginPath(); c.arc(n.x, n.y, 120, 0, 2 * Math.PI); c.fill();
      c.shadowBlur = 0; c.fillStyle = '#141018';
      c.beginPath(); c.arc(n.x, n.y, n.r, 0, 2 * Math.PI); c.fill();
      c.strokeStyle = '#E8944A'; c.lineWidth = 2; c.stroke();
      c.font = `${Math.round(n.r * 1.1)}px system-ui`; c.textAlign = 'center'; c.textBaseline = 'middle';
      c.fillText('🤖', n.x, n.y + 2);
      c.textBaseline = 'alphabetic';
    } else if (n.layer === 'folder') {
      const g = c.createRadialGradient(n.x - n.r * 0.35, n.y - n.r * 0.35, n.r * 0.1, n.x, n.y, n.r);
      g.addColorStop(0, '#FFFFFF'); g.addColorStop(0.25, col); g.addColorStop(1, col + '99');
      c.shadowBlur = n.r * 1.4; c.fillStyle = g;
      c.beginPath(); c.arc(n.x, n.y, n.r, 0, 2 * Math.PI); c.fill();
      c.shadowBlur = 0;
      if ((n.count || 0) >= 200) {   // Saturn ring on heavy folders
        c.strokeStyle = 'rgba(255,255,255,0.4)'; c.lineWidth = 1.1;
        c.beginPath(); c.ellipse(n.x, n.y, n.r * 1.7, n.r * 0.55, -0.5, 0, 2 * Math.PI); c.stroke();
      }
      if (n.r >= 11) {
        c.fillStyle = 'rgba(12,10,18,0.9)';
        c.font = `700 ${Math.max(9, Math.round(n.r * 0.62))}px system-ui`;
        c.textAlign = 'center'; c.textBaseline = 'middle';
        c.fillText(String(n.count || ''), n.x, n.y + 1);
        c.textBaseline = 'alphabetic';
      }
      if (expanded.has(n.id)) {      // exploded marker: hollow ring
        c.strokeStyle = 'rgba(255,255,255,0.7)'; c.lineWidth = 1.4;
        c.beginPath(); c.arc(n.x, n.y, n.r + 4, 0, 2 * Math.PI); c.stroke();
      }
    } else {                          // memory orb / file dot
      c.shadowBlur = n.layer === 'memory' ? 14 : 6;
      c.fillStyle = col;
      c.globalAlpha = n.layer === 'file' ? 0.9 : 1;
      c.beginPath(); c.arc(n.x, n.y, n.r, 0, 2 * Math.PI); c.fill();
      c.globalAlpha = 1;
    }
    c.shadowBlur = 0;
  }

  // ---- state ----------------------------------------------------------------
  let spin = +(localStorage.getItem('td.rings.spin') ?? 0.12);
  let labels = localStorage.getItem('td.rings.labels') !== '0';
  let filter = null, hovered = null, dead = false, raf = null, last = performance.now();
  let rot = 0, zoom = 0.9, panX = 0, panY = 0, fly = null;

  const off = document.createElement('canvas');
  off.width = off.height = BAKE_PX;
  const os = BAKE_PX / (2 * WORLD);

  function bake() {
    const c = off.getContext('2d');
    c.setTransform(os, 0, 0, os, BAKE_PX / 2, BAKE_PX / 2);
    c.clearRect(-WORLD, -WORLD, 2 * WORLD, 2 * WORLD);
    // purple memory-zone vignette (the reference's soft disc behind the arcs)
    const vg = c.createRadialGradient(0, 0, R_SKILLS[2], 0, 0, R_ROUT - 30);
    vg.addColorStop(0, 'rgba(44,28,80,0.10)');
    vg.addColorStop(0.55, 'rgba(52,34,96,0.32)');
    vg.addColorStop(1, 'rgba(30,20,55,0)');
    c.fillStyle = vg; c.beginPath(); c.arc(0, 0, R_ROUT - 20, 0, 2 * Math.PI); c.fill();
    // guide rings
    for (const [rad, col] of [[R_SKILLS[1], LAYER.skills.color], [R_ROUT, LAYER.routines.color], [R_APPS, LAYER.applications.color]]) {
      c.beginPath(); c.arc(0, 0, rad, 0, 2 * Math.PI);
      c.strokeStyle = col + '55'; c.lineWidth = 1.4; c.stroke();
    }
    // links (faint web)
    c.lineWidth = 0.7;
    for (const [a, b] of edges) {
      const dim = filter && !(a.name.toLowerCase().includes(filter) || b.name.toLowerCase().includes(filter));
      c.strokeStyle = dim ? 'rgba(180,170,220,0.02)' : 'rgba(180,170,220,0.10)';
      c.beginPath(); c.moveTo(a.x, a.y);
      c.quadraticCurveTo((a.x + b.x) / 2 * 0.6, (a.y + b.y) / 2 * 0.6, b.x, b.y);
      c.stroke();
    }
    for (const n of all) {
      c.globalAlpha = filter && !n.name.toLowerCase().includes(filter) ? 0.10 : 1;
      drawNode(c, n);
    }
    c.globalAlpha = 1;
    // hub / hero labels (rotate slowly with the scene — acceptable at this spin)
    c.fillStyle = 'rgba(240,236,250,0.92)'; c.font = '700 15px system-ui';
    c.textAlign = 'center';
    try { c.letterSpacing = '2px'; } catch {}
    for (const h of hubs) c.fillText(h.name.toUpperCase(), h.x, h.y + h.r + 22);
    c.fillText('CLAUDE.MD', 0, center.r + 26);
    if (labels) {
      c.font = '600 11px system-ui'; c.fillStyle = 'rgba(225,220,245,0.75)';
      for (const n of all)
        if (n.layer === 'applications' || n.layer === 'routines')
          c.fillText(n.name, n.x, n.y + n.r * (n.layer === 'routines' ? 2 : 1) + 14);
    }
    try { c.letterSpacing = '0px'; } catch {}
  }
  let bakeTimer = null;
  const rebake = () => { clearTimeout(bakeTimer); bakeTimer = setTimeout(() => { bake(); draw(); }, 120); };
  const rebuild = () => { layout(); bake(); draw(); };

  // honeycomb lattice tile (screen-space, doesn't rotate)
  const tile = document.createElement('canvas'); tile.width = 84; tile.height = 48;
  { const t = tile.getContext('2d'); t.strokeStyle = 'rgba(150,145,185,0.07)'; t.lineWidth = 1;
    const hp = (cx, cy) => { hexPath(t, cx, cy, 16); t.stroke(); };
    hp(14, 0); hp(14, 48); hp(56, 24); hp(98, 0); hp(98, 48); hp(-28, 24); }
  let pattern = null;

  const W = () => el.clientWidth, H = () => el.clientHeight;
  const screenScale = () => Math.min(W(), H()) / (2 * WORLD) * zoom * 2.0;
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

  function draw() {
    const w = W(), h = H(), s = screenScale();
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!pattern) pattern = ctx.createPattern(tile, 'repeat');
    ctx.fillStyle = pattern; ctx.fillRect(0, 0, w, h);
    const blit = () => {
      ctx.translate(w / 2 + panX, h / 2 + panY);
      ctx.rotate(rot);
      ctx.scale(s / os, s / os);
    };
    ctx.save(); blit();
    ctx.drawImage(off, -BAKE_PX / 2, -BAKE_PX / 2);
    ctx.restore();

    // hover highlight: dim the world, relight the node + its neighbourhood
    if (hovered) {
      ctx.fillStyle = 'rgba(6,6,10,0.72)';
      ctx.fillRect(0, 0, w, h);
      const near = adj.get(hovered) || new Set();
      ctx.save(); blit();
      ctx.lineWidth = 1.2 * (os / s);
      for (const nb of near) {
        ctx.strokeStyle = (hovered._color || '#fff') + 'CC';
        ctx.beginPath(); ctx.moveTo(hovered.x, hovered.y); ctx.lineTo(nb.x, nb.y); ctx.stroke();
        drawNode(ctx, nb);
      }
      drawNode(ctx, hovered);
      ctx.restore();
    }

    // upright band labels at 12 o'clock
    ctx.textAlign = 'center'; ctx.font = '700 14px system-ui';
    try { ctx.letterSpacing = '3px'; } catch {}
    for (const [rad, L] of [[R_APPS, LAYER.applications], [R_ROUT, LAYER.routines], [R_ITEMS0 - 60, LAYER.memory], [R_SKILLS[2], LAYER.skills]]) {
      ctx.fillStyle = L.color;
      ctx.fillText(L.label, w / 2 + panX, h / 2 + panY - rad * s - 8);
    }
    try { ctx.letterSpacing = '0px'; } catch {}
    if (hovered) {
      const [x, y] = toScreen(hovered);
      ctx.fillStyle = '#fff'; ctx.font = '600 12px system-ui';
      ctx.fillText(hovered.name, x, y - hovered.r * s - 8);
    }
  }

  const hit = (mx, my) => {
    const [wx, wy] = toWorld(mx, my);
    let best = null, bd = (14 / screenScale()) ** 2;
    for (let gx = -1; gx <= 1; gx++) for (let gy = -1; gy <= 1; gy++)
      for (const n of grid.get(gKey(wx + gx * cell, wy + gy * cell)) || []) {
        const d = (n.x - wx) ** 2 + (n.y - wy) ** 2;
        if (d < Math.max(bd, (n.r * 1.2) ** 2)) { bd = d; best = n; }
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
    if (dragging && !moved && hovered) {
      if (hovered.layer === 'folder') {           // explode / collapse
        expanded.has(hovered.id) ? expanded.delete(hovered.id) : expanded.add(hovered.id);
        const keep = hovered.id;
        rebuild();
        hovered = byId.get(keep) || null;
      }
      onNodeClick?.(hovered);
    }
    dragging = false; canvas.style.cursor = 'grab';
  };
  const onWheel = e => { e.preventDefault(); zoom = Math.min(12, Math.max(0.3, zoom * (e.deltaY < 0 ? 1.1 : 0.9))); draw(); };
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
  rebuild(); ensureLoop();

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
    expand(id) { if (byId.get(id) && !expanded.has(id)) { expanded.add(id); rebuild(); } },
    flyTo(id) {
      const n = byId.get(id);
      if (!n) return;
      const s0 = Math.min(W(), H()) / (2 * WORLD) * 2.0;
      const z1 = 3;
      const x = n.x * Math.cos(rot) - n.y * Math.sin(rot);
      const y = n.x * Math.sin(rot) + n.y * Math.cos(rot);
      fly = { t: 0, z0: zoom, z1, x0: panX, y0: panY, x1: -x * s0 * z1, y1: -y * s0 * z1 };
      hovered = n; onNodeHover?.(n);
      ensureLoop();
    },
  };
}
