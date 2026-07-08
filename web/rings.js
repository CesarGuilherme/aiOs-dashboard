// rings.js — concentric-ring "second brain" canvas for the Brain tab.
// Deterministic polar layout, no force simulation: each layer is a ring band,
// nodes evenly spaced within their group, radius jittered by a name hash.

const LAYERS = [
  { key: 'skills',       label: 'SKILLS',       rf: 0.18, color: '#E8A23B' },
  { key: 'memory',       label: 'MEMORY',       rf: 0.46, color: '#B48CFF' },
  { key: 'routines',     label: 'ROUTINES',     rf: 0.68, color: '#E8D44D' },
  { key: 'applications', label: 'APPLICATIONS', rf: 0.86, color: '#4DA6FF' },
];

const hash = s => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) | 0; return (h >>> 0) / 4294967295; };

export function ringsCanvas(el, { nodes, links, onNodeClick, onNodeHover }) {
  const canvas = document.createElement('canvas');
  canvas.style.cssText = 'width:100%;height:100%;display:block;cursor:grab';
  el.appendChild(canvas);
  const ctx = canvas.getContext('2d');

  // --- layout: assign each node a fixed polar position (angle, radius fraction)
  for (const layer of LAYERS) {
    const ns = nodes.filter(n => n.layer === layer.key)
      .sort((a, b) => String(a.group).localeCompare(String(b.group)) || a.name.localeCompare(b.name));
    ns.forEach((n, i) => {
      n._a = (i / Math.max(1, ns.length)) * Math.PI * 2 + hash(layer.key) * Math.PI;
      n._rf = layer.rf + (hash(n.id) - 0.5) * 0.07;   // jitter within the band
      n._color = layer.color;
    });
  }
  const byId = new Map(nodes.map(n => [n.id, n]));
  const edges = links
    .map(l => [byId.get(l.source), byId.get(l.target)])
    .filter(([a, b]) => a && b);

  // --- state
  let spin = +(localStorage.getItem('td.rings.spin') ?? 0.15);
  let labels = localStorage.getItem('td.rings.labels') !== '0';
  let rot = 0, zoom = 1, panX = 0, panY = 0;
  let filter = null, hovered = null, raf = null, last = performance.now(), dead = false;

  const resize = () => {
    const dpr = devicePixelRatio || 1;
    canvas.width = el.clientWidth * dpr;
    canvas.height = el.clientHeight * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw();
  };

  const pos = n => {
    const R = Math.min(el.clientWidth, el.clientHeight) / 2 - 24;
    const a = n._a + rot, r = n._rf * R * zoom;
    return [el.clientWidth / 2 + panX + Math.cos(a) * r,
            el.clientHeight / 2 + panY + Math.sin(a) * r];
  };

  const match = n => !filter || n.name.toLowerCase().includes(filter);

  function draw() {
    const W = el.clientWidth, H = el.clientHeight;
    ctx.clearRect(0, 0, W, H);
    const R = Math.min(W, H) / 2 - 24;
    const cx = W / 2 + panX, cy = H / 2 + panY;
    // ring bands + arc labels
    for (const layer of LAYERS) {
      ctx.beginPath();
      ctx.arc(cx, cy, layer.rf * R * zoom, 0, Math.PI * 2);
      ctx.strokeStyle = layer.color + '44';
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = layer.color + 'CC';
      ctx.font = '600 11px system-ui';
      ctx.textAlign = 'center';
      ctx.fillText(layer.label, cx, cy - layer.rf * R * zoom - 5);
    }
    // links (curved through a point pulled toward the center)
    ctx.lineWidth = 0.6;
    for (const [a, b] of edges) {
      const [x1, y1] = pos(a), [x2, y2] = pos(b);
      const dim = filter && !(match(a) || match(b));
      ctx.strokeStyle = dim ? 'rgba(160,150,200,0.04)' : 'rgba(180,170,220,0.18)';
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.quadraticCurveTo((x1 + x2) / 2 + (cx - (x1 + x2) / 2) * 0.3,
                           (y1 + y2) / 2 + (cy - (y1 + y2) / 2) * 0.3, x2, y2);
      ctx.stroke();
    }
    // nodes
    for (const n of nodes) {
      const [x, y] = pos(n);
      const r = 3 + Math.min(9, (n.size || 1) * 1.5);
      const dim = !match(n);
      ctx.beginPath();
      ctx.arc(x, y, n === hovered ? r + 2 : r, 0, Math.PI * 2);
      ctx.fillStyle = dim ? n._color + '22' : n._color;
      ctx.fill();
      if (labels && !dim && (zoom > 1.4 || n === hovered || filter)) {
        ctx.fillStyle = 'rgba(230,225,245,0.85)';
        ctx.font = '10px system-ui';
        ctx.textAlign = 'left';
        ctx.fillText(n.name, x + r + 3, y + 3);
      }
    }
  }

  function tick(now) {
    if (dead) return;
    if (spin > 0) {
      rot += ((now - last) / 1000) * spin * 0.2;
      draw();
      raf = requestAnimationFrame(tick);
    } else raf = null;
    last = now;
  }
  const ensureLoop = () => { last = performance.now(); if (!raf && spin > 0) raf = requestAnimationFrame(tick); };

  // --- interaction
  const hit = (mx, my) => {
    let best = null, bd = 144;   // 12px pick radius, squared
    for (const n of nodes) {
      const [x, y] = pos(n);
      const d = (x - mx) ** 2 + (y - my) ** 2;
      if (d < bd) { bd = d; best = n; }
    }
    return best;
  };
  let dragging = false, sx = 0, sy = 0, moved = false;
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
  const onUp = e => {
    if (dragging && !moved && hovered) onNodeClick?.(hovered);
    dragging = false; canvas.style.cursor = 'grab';
  };
  const onWheel = e => {
    e.preventDefault();
    zoom = Math.min(6, Math.max(0.4, zoom * (e.deltaY < 0 ? 1.1 : 0.9)));
    draw();
  };
  canvas.addEventListener('mousedown', onDown);
  canvas.addEventListener('mousemove', onMove);
  window.addEventListener('mouseup', onUp);
  canvas.addEventListener('wheel', onWheel, { passive: false });
  const ro = new ResizeObserver(resize);
  ro.observe(el);
  resize();
  ensureLoop();

  return {
    __teardown() {
      dead = true;
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
      window.removeEventListener('mouseup', onUp);
      canvas.remove();
    },
    setFilter(q) { filter = q ? q.toLowerCase() : null; draw(); },
    setLabels(v) { labels = !!v; localStorage.setItem('td.rings.labels', v ? '1' : '0'); draw(); },
    setSpin(v) { spin = +v; localStorage.setItem('td.rings.spin', String(v)); ensureLoop(); if (!spin) draw(); },
    get spin() { return spin; },
    get labels() { return labels; },
    focus(id) {
      const n = byId.get(id);
      if (!n) return;
      zoom = 2.2;
      const R = Math.min(el.clientWidth, el.clientHeight) / 2 - 24;
      const a = n._a + rot, r = n._rf * R * zoom;
      panX = -Math.cos(a) * r; panY = -Math.sin(a) * r;
      hovered = n; onNodeHover?.(n); draw();
    },
  };
}
