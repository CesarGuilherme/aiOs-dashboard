// rings.js — organic brain-synapse renderer, arc-reactor style (see
// hud-background.js for the source of the rotation/projection/glow idiom).
// The Brain is a tree (domain → project hub → topic → memory) plus typed
// cross-project links. Nodes are neuron somas sized by kind and descendant
// count — hubs are big and bright — laid out by a one-shot 3D force-directed
// relaxation where `parent` springs are short (clusters form around hubs) and
// keyword `soft` springs are long and weak. Typed links are colored by kind and
// fire a traveling pulse + arrival flash, like action potentials crossing a
// synapse. Everything is drawn fresh each frame (~200 nodes) — no baked bitmap,
// so labels stay upright in screen space and there is no bitmap boundary.

const WORLD = 480;
const FR_ITERS = 300;
const PULSE_MS = 900;
// spring length per edge kind (× k): short parent springs pull a project together
const SPRING = { parent: 0.4, link: 2.0 };   // soft edges are drawn, not laid out
const KIND_R = { domain: 15, project: 9, topic: 5, memory: 3.2 };
const EDGE_RGB = {
  'same-solution': [255, 196, 84], reuses: [80, 220, 255], 'depends-on': [120, 150, 255],
  supersedes: [150, 150, 165], related: [205, 140, 255],
};
const hash = s => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) | 0; return (h >>> 0) / 4294967295; };

export function ringsCanvas(el, { nodes, links, onNodeClick, onNodeHover }) {
  const canvas = document.createElement('canvas');
  canvas.style.cssText = 'width:100%;height:100%;display:block;cursor:grab';
  el.appendChild(canvas);
  const ctx = canvas.getContext('2d');

  let all = [], byId = new Map(), edges = [], adj = new Map(), firing = [];

  // ---- layout: one-shot 3D force-directed (Fruchterman-Reingold-ish) --------
  function layoutForce(nodesIn, linksIn) {
    all = nodesIn.map(n => {
      const theta = hash(n.id) * 2 * Math.PI;
      const phi = Math.acos(hash(n.id + '.p') * 2 - 1);
      const r = WORLD * 0.3 * (0.4 + hash(n.id + '.r'));
      return { ...n,
        x3d: r * Math.sin(phi) * Math.cos(theta),
        y3d: r * Math.sin(phi) * Math.sin(theta),
        z3d: r * Math.cos(phi),
        pulse: hash(n.id + '.f') * Math.PI * 2,
        blob: [...Array(7)].map((_, i) => 0.75 + hash(n.id + '.b' + i) * 0.5),
        x: 0, y: 0, scale: 1 };
    });
    byId = new Map(all.map(n => [n.id, n]));
    edges = linksIn.map(l => [byId.get(l.source), byId.get(l.target), l.kind || 'link', l.why || ''])
      .filter(([a, b]) => a && b);
    firing = edges.filter(e => e[2] !== 'parent' && e[2] !== 'soft');
    if (!firing.length) firing = edges;
    const groups = [...new Set(all.map(n => n.group))].sort();
    groupHue = new Map(groups.map((g, i) => [g, (i / Math.max(1, groups.length)) * 360]));

    adj = new Map();
    for (const [a, b] of edges) {
      (adj.get(a) || adj.set(a, new Set()).get(a)).add(b);
      (adj.get(b) || adj.set(b, new Set()).get(b)).add(a);
    }
    for (const n of all) {
      const deg = adj.get(n) ? adj.get(n).size : 0;
      n.r = !n.kind ? 5 + Math.min(9, 1.6 * deg)                       // legacy flat payload
        : n.kind === 'project' ? KIND_R.project + Math.min(9, Math.sqrt(n.size || 0) * 1.4)
        : (KIND_R[n.kind] ?? 5) + (n.kind === 'memory' ? Math.min(3, deg * 0.35) : 0);
      n.hub = n.kind === 'project' || n.kind === 'domain';
    }

    const tree = seedTree(edges);
    if (all.length > 1) {
      const k = WORLD / Math.cbrt(all.length) * (tree ? 0.35 : 0.55);
      let temp = WORLD * (tree ? 0.012 : 0.06);
      for (let it = 0; it < (tree ? 140 : FR_ITERS); it++) {
        const disp = new Map(all.map(n => [n, [0, 0, 0]]));
        for (let i = 0; i < all.length; i++) {
          for (let j = i + 1; j < all.length; j++) {
            const a = all[i], b = all[j];
            let dx = a.x3d - b.x3d, dy = a.y3d - b.y3d, dz = a.z3d - b.z3d;
            const d = Math.hypot(dx, dy, dz) || 0.01;
            const f = (k * k) * (a.r * b.r / 25) / d / d;
            dx *= f; dy *= f; dz *= f;
            const da = disp.get(a), db = disp.get(b);
            da[0] += dx; da[1] += dy; da[2] += dz;
            db[0] -= dx; db[1] -= dy; db[2] -= dz;
          }
        }
        for (const [a, b, kind] of edges) {
          if (kind === 'soft') continue;
          let dx = a.x3d - b.x3d, dy = a.y3d - b.y3d, dz = a.z3d - b.z3d;
          const d = Math.hypot(dx, dy, dz) || 0.01;
          const f = d / (k * (SPRING[kind] ?? 1));
          dx *= f / d; dy *= f / d; dz *= f / d;
          const da = disp.get(a), db = disp.get(b);
          da[0] -= dx * d; da[1] -= dy * d; da[2] -= dz * d;
          db[0] += dx * d; db[1] += dy * d; db[2] += dz * d;
        }
        for (const n of all) {
          const [dx, dy, dz] = disp.get(n);
          const d = Math.hypot(dx, dy, dz) || 0.01;
          const lim = Math.min(d, temp);
          n.x3d = Math.max(-WORLD, Math.min(WORLD, n.x3d + dx / d * lim));
          n.y3d = Math.max(-WORLD, Math.min(WORLD, n.y3d + dy / d * lim));
          n.z3d = Math.max(-WORLD, Math.min(WORLD, n.z3d + dz / d * lim));
        }
        temp *= 0.985;
      }
      // recenter the cluster on the origin so rotation doesn't wobble
      const cx = all.reduce((s, n) => s + n.x3d, 0) / all.length;
      const cy = all.reduce((s, n) => s + n.y3d, 0) / all.length;
      const cz = all.reduce((s, n) => s + n.z3d, 0) / all.length;
      for (const n of all) { n.x3d -= cx; n.y3d -= cy; n.z3d -= cz; }
      // fit: projection shrinks the world ~0.35× and a sphere shows only ~r/√3 per
      // axis, so the 90th-percentile node goes to 1.6·WORLD to fill the card
      const radii = all.map(n => Math.hypot(n.x3d, n.y3d, n.z3d)).sort((a, b) => a - b);
      const r90 = radii[Math.floor(radii.length * 0.9)] || 1;
      const fit = (WORLD * 1.6) / r90;
      for (const n of all) { n.x3d *= fit; n.y3d *= fit; n.z3d *= fit; }
    }
  }

  // Tree payloads (parent edges) get a hierarchical seed: domains on a sphere,
  // each node's children fanned out on a cap pointing away from its parent, so
  // hubs end up as centers of their own satellite clusters before relaxation.
  function seedTree(edgesIn) {
    const kids = new Map();
    let any = false;
    for (const [a, b, kind] of edgesIn) {
      if (kind !== 'parent') continue;
      any = true;
      (kids.get(a) || kids.set(a, []).get(a)).push(b);
    }
    if (!any) return false;
    const hasParent = new Set([...kids.values()].flat());
    const roots = all.filter(n => !hasParent.has(n));
    const fib = (i, m) => {  // even directions on a unit sphere
      const y = 1 - (2 * (i + 0.5)) / m, r = Math.sqrt(1 - y * y), t = i * 2.399963;
      return [Math.cos(t) * r, y, Math.sin(t) * r];
    };
    const DIST = { domain: WORLD * 0.62, project: WORLD * 0.30, topic: WORLD * 0.12, memory: WORLD * 0.07 };
    const place = (n, pos, out) => {
      [n.x3d, n.y3d, n.z3d] = pos;
      const ch = kids.get(n) || [];
      ch.forEach((c, i) => {
        let [dx, dy, dz] = fib(i, Math.max(ch.length, 2));
        if (out) { dx += out[0] * 1.6; dy += out[1] * 1.6; dz += out[2] * 1.6; }  // fan outward
        const len = Math.hypot(dx, dy, dz) || 1;
        const dir = [dx / len, dy / len, dz / len];
        const d = (DIST[c.kind] ?? WORLD * 0.08) * (c.kind === 'project' ? 0.7 + Math.sqrt(c.size || 1) * 0.08 : 1);
        place(c, [pos[0] + dir[0] * d, pos[1] + dir[1] * d, pos[2] + dir[2] * d], dir);
      });
    };
    roots.forEach((r, i) => {
      const dir = fib(i, Math.max(roots.length, 2));
      const d = r.kind === 'domain' ? DIST.domain : WORLD * 0.2;
      place(r, dir.map(v => v * d), dir);
    });
    return true;
  }

  // rainbow hue per project (group), evenly spread; small per-node jitter
  let groupHue = new Map();
  function hsl2rgb(h, s, l) {
    const f = k => { const kk = (k + h / 30) % 12; return l - s * Math.min(l, 1 - l) * Math.max(-1, Math.min(kk - 3, 9 - kk, 1)); };
    return [f(0) * 255, f(8) * 255, f(4) * 255].map(Math.round);
  }
  function nodeRGB(n) {
    const hue = (groupHue.get(n.group) ?? 270) + (hash(n.id + '.hue') - 0.5) * 18;
    return hsl2rgb((hue + 360) % 360, n.kind === 'domain' ? 0.35 : 0.72, n.hub ? 0.76 : 0.66);
  }

  // ---- state ----------------------------------------------------------------
  let spin = +(localStorage.getItem('td.rings.spin') ?? 0.35);
  let labels = localStorage.getItem('td.rings.labels') !== '0';
  let filter = null, hovered = null, dead = false, raf = null;
  let zoom = 0.9, panX = 0, panY = 0, fly = null;
  let pulses = [];
  // arc-reactor drift: rotation speeds ease toward re-randomized targets
  let rotX = 0.0016, rotY = 0.0024, rotZ = -0.0009;
  let tRotX = rotX, tRotY = rotY, tRotZ = rotZ, nextSpinShift = 0;

  const W = () => el.clientWidth, H = () => el.clientHeight;

  function rotateAll(ax, ay, az) {
    const cosX = Math.cos(ax), sinX = Math.sin(ax);
    const cosY = Math.cos(ay), sinY = Math.sin(ay);
    const cosZ = Math.cos(az), sinZ = Math.sin(az);
    for (const n of all) {
      const y = n.y3d * cosX - n.z3d * sinX;
      const z = n.z3d * cosX + n.y3d * sinX;
      const x = n.x3d * cosY - z * sinY;
      const z2 = z * cosY + n.x3d * sinY;
      n.x3d = x * cosZ - y * sinZ;
      n.y3d = y * cosZ + x * sinZ;
      n.z3d = z2;
    }
  }

  function updateSpin(now) {
    if (now > nextSpinShift) {
      tRotX = (Math.random() - 0.5) * 0.011;
      tRotY = (Math.random() - 0.5) * 0.011;
      tRotZ = (Math.random() - 0.5) * 0.005;
      nextSpinShift = now + 1500 + Math.random() * 2600;
    }
    rotX += (tRotX - rotX) * 0.018;
    rotY += (tRotY - rotY) * 0.018;
    rotZ += (tRotZ - rotZ) * 0.018;
  }

  // Perspective params + safe depth scale. Without a floor on the denominator,
  // high zoom (or flyTo z=3) + a node with large negative z3d makes
  // (perspective + z*base + cam) ≤ 0 → negative/∞ scale → createRadialGradient
  // throws and the rAF loop dies until full page reload.
  function camParams() {
    const w = W(), h = H();
    const perspective = Math.max(w, h) * 0.72;
    const cameraDistance = Math.max(w, h) * 0.83;
    const base = Math.min(w, h) / (2 * WORLD) * 1.7 * zoom;
    return { w, h, perspective, cameraDistance, base, minDenom: Math.max(perspective * 0.08, 8) };
  }
  function depthScale(z3d, perspective, cameraDistance, base, minDenom) {
    const denom = perspective + z3d * base + cameraDistance;
    return perspective / Math.max(denom, minDenom);
  }

  function projectAll() {
    const { w, h, perspective, cameraDistance, base, minDenom } = camParams();
    let smin = Infinity, smax = -Infinity;
    for (const n of all) {
      const s = depthScale(n.z3d, perspective, cameraDistance, base, minDenom);
      n.scale = s * base;  // always finite/positive; draw paths clamp radii separately
      n.x = w / 2 + panX + n.x3d * n.scale;
      n.y = h / 2 + panY + n.y3d * n.scale;
      n._s = s;
      n.pulse += 0.02;
      if (s < smin) smin = s; if (s > smax) smax = s;
    }
    const range = smax - smin || 1;
    for (const n of all) n.depth = (n._s - smin) / range;  // 0 back … 1 front
  }

  const nodeAlpha = n => {
    const dimmed = filter && !n.name.toLowerCase().includes(filter);
    return (dimmed ? 0.10 : 1) * (0.35 + 0.65 * n.depth);
  };

  function somaPath(c, n, rr) {
    const pts = n.blob.map((m, i) => {
      const a = (i / n.blob.length) * 2 * Math.PI;
      return [n.x + Math.cos(a) * rr * m, n.y + Math.sin(a) * rr * m];
    });
    c.beginPath();
    for (let i = 0; i < pts.length; i++) {
      const [x0, y0] = pts[i], [x1, y1] = pts[(i + 1) % pts.length];
      i ? c.quadraticCurveTo(x0, y0, (x0 + x1) / 2, (y0 + y1) / 2) : c.moveTo((x0 + pts[pts.length - 1][0]) / 2, (y0 + pts[pts.length - 1][1]) / 2);
    }
    c.closePath();
  }

  function drawNode(c, n, glow) {
    const [r, g, b] = nodeRGB(n);
    const flicker = 0.85 + Math.sin(n.pulse) * 0.15;
    // Radii must stay > 0 — createRadialGradient throws on negative/NaN and kills the loop.
    const rr = Math.max(0.5, Math.min(200, n.r * Math.max(0, n.scale) * 1.6 * flicker));
    const gr = c.createRadialGradient(n.x - rr * 0.3, n.y - rr * 0.3, rr * 0.1, n.x, n.y, rr);
    gr.addColorStop(0, `rgba(${Math.min(255, r + 70)},${Math.min(255, g + 70)},${Math.min(255, b + 70)},1)`);
    gr.addColorStop(1, `rgba(${r},${g},${b},0.85)`);
    c.shadowColor = `rgb(${r},${g},${b})`;
    c.shadowBlur = Math.min(80, glow || n.hub ? rr * 2.4 : rr * 0.9);
    somaPath(c, n, rr);
    c.fillStyle = gr; c.fill();
    c.shadowBlur = 0;
    // soft outer aura, additive
    c.fillStyle = `rgba(${r},${g},${b},${glow ? 0.16 : n.hub ? 0.12 : 0.07})`;
    c.beginPath(); c.arc(n.x, n.y, rr * (n.hub ? 3.4 : 2.6), 0, 2 * Math.PI); c.fill();
    if (n.hub) {  // arc-reactor ring around hubs
      c.strokeStyle = `rgba(${Math.min(255, r + 60)},${Math.min(255, g + 60)},${Math.min(255, b + 60)},0.55)`;
      c.lineWidth = 1.2;
      c.beginPath(); c.arc(n.x, n.y, rr * 1.55, 0, 2 * Math.PI); c.stroke();
    }
    if (glow) {
      c.strokeStyle = 'rgba(255,255,255,0.85)'; c.lineWidth = 1.6;
      c.beginPath(); c.arc(n.x, n.y, rr + 4, 0, 2 * Math.PI); c.stroke();
    }
  }

  // edge curve on projected points (shared by edge fill + pulse position)
  function edgeCtrl(a, b) {
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    const wob = (hash(a.id + b.id) - 0.5) * 0.5;
    return [mx - (b.y - a.y) * wob, my + (b.x - a.x) * wob];
  }
  function edgePoint(a, b, t) {
    const [cx, cy] = edgeCtrl(a, b);
    const u = 1 - t;
    return [u * u * a.x + 2 * u * t * cx + t * t * b.x, u * u * a.y + 2 * u * t * cy + t * t * b.y];
  }

  function drawEdge(c, a, b, kind) {
    const vis = Math.min(nodeAlpha(a), nodeAlpha(b)) * (0.4 + (a.depth + b.depth) * 0.3);
    if (kind === 'parent' || kind === 'soft') {   // hierarchy + keyword overlap: hairlines
      const [r, g, bl] = nodeRGB(b);
      const al = (kind === 'parent' ? 0.22 : 0.06) * vis;
      if (al < 0.01) return;
      c.strokeStyle = `rgba(${r},${g},${bl},${al})`; c.lineWidth = kind === 'parent' ? 0.8 : 0.5;
      c.beginPath(); c.moveTo(a.x, a.y); c.lineTo(b.x, b.y); c.stroke();
      return;
    }
    if (EDGE_RGB[kind]) {                          // typed cross-project link
      const [r, g, bl] = EDGE_RGB[kind];
      const [cx, cy] = edgeCtrl(a, b);
      c.strokeStyle = `rgba(${r},${g},${bl},${0.7 * vis})`; c.lineWidth = 1.8;
      c.beginPath(); c.moveTo(a.x, a.y); c.quadraticCurveTo(cx, cy, b.x, b.y); c.stroke();
      return;
    }
    const alpha = 0.16 * vis;
    if (alpha < 0.01) return;
    const [cx, cy] = edgeCtrl(a, b);
    const w0 = Math.max(1.2, a.r * a.scale * 1.6 * 0.2), w1 = Math.max(0.5, b.r * b.scale * 1.6 * 0.1);
    const nx = cy - a.y, ny = a.x - cx;
    const nlen = Math.hypot(nx, ny) || 1;
    c.beginPath();
    c.moveTo(a.x + nx / nlen * w0, a.y + ny / nlen * w0);
    c.quadraticCurveTo(cx, cy, b.x + nx / nlen * w1, b.y + ny / nlen * w1);
    c.lineTo(b.x - nx / nlen * w1, b.y - ny / nlen * w1);
    c.quadraticCurveTo(cx, cy, a.x - nx / nlen * w0, a.y - ny / nlen * w0);
    c.closePath();
    c.fillStyle = `rgba(170,150,230,${alpha})`;
    c.fill();
  }

  function draw(now) {
    const w = W(), h = H();
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const bg = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, Math.max(w, h) * 0.7);
    bg.addColorStop(0, '#0d0b14'); bg.addColorStop(1, '#07060a');
    ctx.fillStyle = bg; ctx.fillRect(0, 0, w, h);

    ctx.save();
    ctx.globalCompositeOperation = 'lighter';

    // central halo (arc-reactor heart)
    const haloR = Math.max(1, Math.min(w, h) * 0.55 * zoom);
    const halo = ctx.createRadialGradient(w / 2 + panX, h / 2 + panY, 0, w / 2 + panX, h / 2 + panY, haloR);
    halo.addColorStop(0, 'rgba(120,90,200,0.10)');
    halo.addColorStop(0.5, 'rgba(90,70,160,0.04)');
    halo.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = halo; ctx.fillRect(0, 0, w, h);

    const sorted = [...all].sort((a, b) => a.depth - b.depth);  // back to front
    for (const [a, b, kind] of edges) drawEdge(ctx, a, b, kind);

    // synapse pulses: traveling glow + arrival flash
    for (const p of pulses) {
      const t = Math.min(1, (now - p.t0) / PULSE_MS);
      const [a, b] = p.edge;
      const [px, py] = edgePoint(a, b, t);
      const [r, g, bch] = EDGE_RGB[p.kind] || nodeRGB(a);
      ctx.shadowColor = `rgb(${r},${g},${bch})`; ctx.shadowBlur = 14;
      ctx.fillStyle = 'rgba(255,255,255,0.95)';
      ctx.beginPath(); ctx.arc(px, py, 2.6 * Math.max(0.6, b.depth), 0, 2 * Math.PI); ctx.fill();
      ctx.shadowBlur = 0;
      if (t > 0.85) {
        const fe = (t - 0.85) / 0.15;
        const rr = Math.max(0.5, Math.min(200, b.r * Math.max(0, b.scale) * 1.6 * (1 + fe * 1.8)));
        const gr = ctx.createRadialGradient(b.x, b.y, 0, b.x, b.y, rr);
        gr.addColorStop(0, `rgba(255,255,255,${0.45 * (1 - fe)})`);
        gr.addColorStop(1, 'rgba(255,255,255,0)');
        ctx.fillStyle = gr;
        ctx.beginPath(); ctx.arc(b.x, b.y, rr, 0, 2 * Math.PI); ctx.fill();
      }
    }
    pulses = pulses.filter(p => now - p.t0 < PULSE_MS);

    for (const n of sorted) {
      ctx.globalAlpha = nodeAlpha(n);
      drawNode(ctx, n, false);
    }
    ctx.globalAlpha = 1;
    ctx.restore();

    // hover: dim the world, relight the node + its neighbourhood
    if (hovered) {
      ctx.fillStyle = 'rgba(6,6,10,0.68)';
      ctx.fillRect(0, 0, w, h);
      ctx.save();
      ctx.globalCompositeOperation = 'lighter';
      const near = adj.get(hovered) || new Set();
      const [r, g, b2] = nodeRGB(hovered);
      ctx.lineWidth = 1.2;
      for (const nb of near) {
        ctx.strokeStyle = `rgba(${r},${g},${b2},0.75)`;
        ctx.beginPath(); ctx.moveTo(hovered.x, hovered.y); ctx.lineTo(nb.x, nb.y); ctx.stroke();
        drawNode(ctx, nb, false);
      }
      drawNode(ctx, hovered, true);
      ctx.restore();
    }

    // labels: screen-space, always upright, faded by depth; hovered on top
    if (labels) {
      ctx.font = '600 10px system-ui'; ctx.textAlign = 'center';
      for (const n of sorted) {
        if (n === hovered) continue;
        if (n.kind === 'memory' && zoom < 1.8) continue;   // ~150 notes: name hubs/topics only
        const a = (n.hub ? 1.6 : 1) * (hovered ? 0.15 : 0.55) * nodeAlpha(n) * Math.max(0, (n.depth - 0.35) * 1.55);
        if (a < 0.05) continue;
        ctx.fillStyle = `rgba(225,220,245,${Math.min(0.8, a)})`;
        ctx.fillText(n.name, n.x, n.y + n.r * n.scale * 1.6 + 12);
      }
    }
    if (hovered) {
      ctx.fillStyle = '#fff'; ctx.font = '600 12px system-ui'; ctx.textAlign = 'center';
      ctx.fillText(hovered.name, hovered.x, hovered.y - hovered.r * hovered.scale * 1.6 - 10);
      const sum = hovered.meta?.summary;
      if (sum) {
        ctx.font = '400 11px system-ui'; ctx.fillStyle = 'rgba(225,220,245,0.85)';
        ctx.fillText(sum.length > 90 ? sum.slice(0, 89) + '…' : sum,
          hovered.x, hovered.y + hovered.r * hovered.scale * 1.6 + 16);
      }
    }
  }

  const hit = (mx, my) => {
    let best = null, bd = 14 * 14;
    for (const n of all) {
      const d = (n.x - mx) ** 2 + (n.y - my) ** 2;
      if (d < Math.max(bd, (n.r * n.scale  * 1.6 * 1.3) ** 2) && d < bd + (n.r * n.scale * 1.6 * 1.3) ** 2) {
        if (!best || d < bd) { bd = d; best = n; }
      }
    }
    return best;
  };

  // ---- synapse-firing scheduler ---------------------------------------------
  const scheduleFiring = () => {
    if (!firing.length || document.hidden) return;
    const n = 1 + Math.floor(Math.random() * 3);
    for (let i = 0; i < n; i++) {
      const [a, b, kind] = firing[Math.floor(Math.random() * firing.length)];
      pulses.push({ edge: Math.random() < 0.5 ? [a, b] : [b, a], kind, t0: performance.now() });
    }
  };
  const pulseInterval = setInterval(scheduleFiring, 500 + Math.random() * 400);

  function tick(now) {
    if (dead) return;
    updateSpin(now);
    if (spin > 0) rotateAll(rotX * spin, rotY * spin, rotZ * spin);
    if (fly) {
      fly.t = Math.min(1, fly.t + 1 / 36);
      const e = 1 - (1 - fly.t) ** 3;
      const n = fly.node;
      // target pan recomputed each frame (node keeps rotating)
      zoom = fly.z0 + (fly.z1 - fly.z0) * e;
      const { perspective, cameraDistance, base, minDenom } = camParams();
      const scale = depthScale(n.z3d, perspective, cameraDistance, base, minDenom) * base;
      panX += (-n.x3d * scale - panX) * 0.14;
      panY += (-n.y3d * scale - panY) * 0.14;
      if (fly.t >= 1) fly = null;
    }
    projectAll();
    draw(now);
    raf = requestAnimationFrame(tick);
  }

  let dragging = false, rotating = false, moved = false, sx = 0, sy = 0;
  const onDown = e => {
    dragging = true; rotating = e.shiftKey;
    moved = false; sx = e.clientX; sy = e.clientY;
    canvas.style.cursor = rotating ? 'move' : 'grabbing';
  };
  const onMove = e => {
    const rect = canvas.getBoundingClientRect();
    if (dragging) {
      const dx = e.clientX - sx, dy = e.clientY - sy;
      if (rotating) rotateAll(dy * 0.005, dx * 0.005, 0);   // shift + drag = orbit
      else { fly = null; panX += dx; panY += dy; }
      if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
      sx = e.clientX; sy = e.clientY; return;
    }
    const hcur = hit(e.clientX - rect.left, e.clientY - rect.top);
    if (hcur !== hovered) { hovered = hcur; canvas.style.cursor = hcur ? 'pointer' : 'grab'; onNodeHover?.(hcur); }
  };
  const onUp = () => {
    if (dragging && !moved && !rotating && hovered) onNodeClick?.(hovered);
    dragging = false; rotating = false; canvas.style.cursor = hovered ? 'pointer' : 'grab';
  };
  const onWheel = e => {
    e.preventDefault();
    fly = null;  // user zoom cancels flyTo so the two don't fight
    zoom = Math.min(8, Math.max(0.3, zoom * (e.deltaY < 0 ? 1.1 : 0.9)));
  };
  canvas.addEventListener('mousedown', onDown);
  canvas.addEventListener('mousemove', onMove);
  window.addEventListener('mouseup', onUp);
  canvas.addEventListener('wheel', onWheel, { passive: false });
  const ro = new ResizeObserver(() => {
    canvas.width = W() * devicePixelRatio; canvas.height = H() * devicePixelRatio;
  });
  ro.observe(el);
  canvas.width = W() * devicePixelRatio; canvas.height = H() * devicePixelRatio;
  layoutForce(nodes, links);
  raf = requestAnimationFrame(tick);

  return {
    __teardown() {
      dead = true;
      if (raf) cancelAnimationFrame(raf);
      clearInterval(pulseInterval);
      ro.disconnect();
      canvas.removeEventListener('mousedown', onDown);
      canvas.removeEventListener('mousemove', onMove);
      canvas.removeEventListener('wheel', onWheel);
      window.removeEventListener('mouseup', onUp);
      canvas.remove();
    },
    setFilter(q) { filter = q ? q.toLowerCase() : null; },
    setLabels(v) { labels = !!v; localStorage.setItem('td.rings.labels', v ? '1' : '0'); },
    setSpin(v) { spin = +v; localStorage.setItem('td.rings.spin', String(v)); },
    get spin() { return spin; },
    get labels() { return labels; },
    get nodes() { return all; },   // read-only view for layout diagnostics
    flyTo(id) {
      const n = byId.get(id);
      if (!n) return;
      fly = { t: 0, z0: zoom, z1: 3, node: n };
      hovered = n; onNodeHover?.(n);
    },
  };
}
