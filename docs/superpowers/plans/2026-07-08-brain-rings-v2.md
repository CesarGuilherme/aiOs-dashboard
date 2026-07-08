# Brain Rings V2 (Visual Parity) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Brain-tab rings to match the reference: dense per-department file arcs, layer-specific node shapes with glow, center CLAUDE.MD node, search dropdown, and a click detail card with Fly to / Copy path / Open on device.

**Architecture:** `/api/workspace` gains a `files` scan of `WORKSPACE_ROOTS` plus a `POST /api/open` endpoint; `web/rings.js` is rewritten around an offscreen-baked scene bitmap (bake once, blit with rotate/zoom/pan every frame — fast at 30k nodes); `web/routes/brain.js` panel gains a search dropdown and detail card.

**Tech Stack:** Python 3 stdlib, vanilla JS, Canvas 2D. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-08-brain-rings-design.md` (§ V2 addendum)

## Global Constraints

- Stdlib only; no new JS libraries; fully local.
- Missing paths → empty lists, never errors. `POST /api/open` rejects paths outside the scanned roots and `~/.claude` with 403.
- File scan skips `.git`, `node_modules`, `venv`, `.venv`, `__pycache__`, and hidden entries; cap 2000 files per department, largest first.
- All user-derived strings rendered into HTML go through `fmt.htmlSafe`.
- Live-verify rule: done = ran live + inspected output + reported numbers.

---

### Task 1: File scan + `POST /api/open`

**Files:**
- Modify: `token_dashboard/workspace.py`
- Modify: `tests/test_workspace.py`
- Modify: `token_dashboard/server.py` (GET route already exists; update it, add POST route)

**Interfaces:**
- Produces: `scan_workspace(claude_dir, roots=None)` — `roots: list[Path]|None`; when None, derived from `WORKSPACE_ROOTS` env (colon-separated, default `/Volumes/SSD_CESAR/Developer`). Result gains `"files": [{"name","path","rel","dept","size","mtime","ext"}]` (`mtime` ISO-8601 seconds, `path` absolute string).
- Produces: `allowed_open_path(path_str, roots, claude_dir) -> bool` and `open_on_device(path_str) -> None` (platform dispatch).
- Server: `GET /api/workspace` now includes `files`; `POST /api/open` body `{"path": "..."}` → 204 on success, 403 if not allowed, 404 if missing.

- [ ] **Step 1: Add failing tests**

Append to `tests/test_workspace.py`:

```python
import os

from token_dashboard.workspace import scan_workspace, allowed_open_path


class FileScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.claude = Path(self.tmp.name) / ".claude"   # empty — files only
        self.dev = Path(self.tmp.name) / "Developer"
        proj = self.dev / "proj-a"
        (proj / "src").mkdir(parents=True)
        (proj / "src" / "big.py").write_text("x" * 500)
        (proj / "small.md").write_text("y")
        (proj / ".hidden").write_text("z")
        (proj / "node_modules" / "dep").mkdir(parents=True)
        (proj / "node_modules" / "dep" / "index.js").write_text("no")
        (self.dev / "proj-b").mkdir()
        (self.dev / "proj-b" / "only.txt").write_text("t")
        (self.dev / ".DS_Store").write_text("")   # hidden root entry ignored

    def tearDown(self):
        self.tmp.cleanup()

    def test_files_scanned_with_fields_and_skips(self):
        files = scan_workspace(self.claude, roots=[self.dev])["files"]
        rels = {f["rel"] for f in files}
        self.assertEqual(rels, {"src/big.py", "small.md", "only.txt"})
        big = next(f for f in files if f["name"] == "big.py")
        self.assertEqual(big["dept"], "proj-a")
        self.assertEqual(big["ext"], "py")
        self.assertEqual(big["size"], 500)
        self.assertTrue(Path(big["path"]).is_absolute())
        self.assertIn("T", big["mtime"])   # ISO timestamp

    def test_cap_largest_first(self):
        proj = self.dev / "proj-c"
        proj.mkdir()
        for i in range(30):
            (proj / f"f{i:02}.txt").write_text("x" * (i + 1))
        from token_dashboard import workspace
        old = workspace.MAX_FILES_PER_DEPT
        workspace.MAX_FILES_PER_DEPT = 10
        try:
            files = [f for f in scan_workspace(self.claude, roots=[self.dev])["files"]
                     if f["dept"] == "proj-c"]
        finally:
            workspace.MAX_FILES_PER_DEPT = old
        self.assertEqual(len(files), 10)
        self.assertEqual(min(f["size"] for f in files), 21)   # kept the 10 largest

    def test_missing_root_yields_empty(self):
        files = scan_workspace(self.claude, roots=[Path(self.tmp.name) / "nope"])["files"]
        self.assertEqual(files, [])

    def test_allowed_open_path(self):
        inside = self.dev / "proj-a" / "small.md"
        self.assertTrue(allowed_open_path(str(inside), [self.dev], self.claude))
        self.assertFalse(allowed_open_path("/etc/passwd", [self.dev], self.claude))
        sneaky = str(self.dev / ".." / "outside.txt")
        self.assertFalse(allowed_open_path(sneaky, [self.dev], self.claude))
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest tests.test_workspace -v`
Expected: FAIL — `ImportError: cannot import name 'allowed_open_path'`

- [ ] **Step 3: Implement in `workspace.py`**

Add to `token_dashboard/workspace.py`:

```python
import os
import subprocess
import sys
from datetime import datetime

SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__"}
MAX_FILES_PER_DEPT = 2000
DEFAULT_ROOTS = "/Volumes/SSD_CESAR/Developer"


def workspace_roots() -> list:
    raw = os.environ.get("WORKSPACE_ROOTS", DEFAULT_ROOTS)
    return [Path(p).expanduser() for p in raw.split(":") if p.strip()]


def _dept_files(dept_dir: Path) -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(dept_dir):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("."):
                continue
            p = Path(dirpath) / fn
            try:
                st = p.stat()
            except OSError:
                continue
            out.append({
                "name": fn,
                "path": str(p),
                "rel": str(p.relative_to(dept_dir)),
                "dept": dept_dir.name,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                "ext": p.suffix.lstrip(".").lower(),
            })
    out.sort(key=lambda f: f["size"], reverse=True)
    return out[:MAX_FILES_PER_DEPT]


def _files(roots: list) -> list:
    out = []
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                out.extend(_dept_files(child))
    return out


def allowed_open_path(path_str: str, roots: list, claude_dir: Path) -> bool:
    try:
        p = Path(path_str).resolve()
    except OSError:
        return False
    for base in list(roots) + [claude_dir]:
        base = base.resolve()
        if p == base or base in p.parents:
            return True
    return False


def open_on_device(path_str: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", path_str], check=False)
    elif os.name == "nt":
        os.startfile(path_str)  # noqa — windows only
    else:
        subprocess.run(["xdg-open", path_str], check=False)
```

Change `scan_workspace` to:

```python
def scan_workspace(claude_dir: Path, roots=None) -> dict:
    if roots is None:
        roots = workspace_roots()
    return {
        "applications": _applications(claude_dir),
        "routines": _routines(claude_dir),
        "skills": _skills(claude_dir),
        "files": _files(roots),
    }
```

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest tests.test_workspace -v`
Expected: all pass (existing 4 + new 4). NOTE: the existing `test_missing_everything_yields_empty_lists` asserts an exact dict — update its expectation to include `"files": []` and pass `roots=[]` there so the default root on the real machine doesn't leak in:

```python
    def test_missing_everything_yields_empty_lists(self):
        empty = Path(self.tmp.name) / "nope" / ".claude"
        self.assertEqual(scan_workspace(empty, roots=[]),
                         {"applications": [], "routines": [], "skills": [], "files": []})
```

- [ ] **Step 5: Wire server routes**

In `token_dashboard/server.py`, update the import:

```python
from .workspace import scan_workspace, workspace_roots, allowed_open_path, open_on_device
```

The existing GET branch stays as-is (scan_workspace now includes files). Add to `do_POST`, next to the other POST routes (~line 217):

```python
            if url.path == "/api/open":
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                target = str(body.get("path", ""))
                claude_dir = Path.home() / ".claude"
                if not allowed_open_path(target, workspace_roots(), claude_dir):
                    self.send_response(403); self.end_headers(); return
                if not Path(target).exists():
                    self.send_response(404); self.end_headers(); return
                open_on_device(target)
                self.send_response(204); self.end_headers(); return
```

Match the surrounding code's existing helpers for reading the POST body if a helper already exists — reuse it instead of the inline read.

- [ ] **Step 6: Full suite + commit**

Run: `python3 -m unittest discover tests`
Expected: all pass.

```bash
git add token_dashboard/workspace.py tests/test_workspace.py token_dashboard/server.py
git commit -m "feat: workspace file scan + POST /api/open"
```

---

### Task 2: rings.js v2 — baked-scene renderer

**Files:**
- Rewrite: `web/rings.js` (replace entire file)

**Interfaces:**
- Consumes: node objects built by brain.js (Task 3):
  `{ id, name, layer: 'center'|'skills'|'memory'|'routines'|'applications'|'file', group, size, meta }`
  (`group` = department/project name for memory+file; `size` = bytes for files, 1+links for memory).
  Links `{source, target}` refer to node ids; unknown ids skipped.
- Produces: `ringsCanvas(el, { nodes, links, onNodeClick, onNodeHover })` → handle
  `{ __teardown(), setFilter(q|null), setLabels(bool), setSpin(0..1), get spin, get labels, flyTo(id) }`.
  The caller does NOT pass a center node — rings.js creates it (`id:'center'`, label `CLAUDE.MD`).

- [ ] **Step 1: Write the renderer** (verbatim):

```javascript
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
    while (i < items.length && rad < R_ROUT - 40) {
      const cap = Math.max(1, Math.floor((span * rad) / 22));
      for (let k = 0; k < cap && i < items.length; k++, i++) {
        const it = items[i];
        on(it.n, a0 + ((k + 0.5) / cap) * span, rad + (hash(it.n.id) - 0.5) * 8, it.r, color);
      }
      rad += 24;
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
```

- [ ] **Step 2: Syntax check**

Run: `node --check web/rings.js`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add web/rings.js
git commit -m "feat: rings v2 — baked-scene renderer with arcs, shapes, glow, fly-to"
```

---

### Task 3: Panel v2 — search dropdown + detail card

**Files:**
- Modify: `web/routes/brain.js`
- Modify: `web/style.css`

**Interfaces:**
- Consumes: Task 1's `files` array in `/api/workspace`; Task 2's handle (`flyTo` replaces v1's `focus`).
- Produces: nothing downstream.

- [ ] **Step 1: Extend node building in `brain.js`**

In the node-building block, add file nodes and keep the rest (memory/skills/routines/applications) as-is:

```javascript
    ...(workspace.files || []).map(f => ({
      id: 'file::' + f.path, name: f.name, layer: 'file', group: f.dept,
      size: f.size,
      meta: { path: f.path, rel: f.rel, dept: f.dept, size: f.size, mtime: f.mtime, ext: f.ext },
    })),
```

Memory nodes keep `meta.mem = true` and gain `meta.name = e.name` if not already present. The `.catch` fallback object gains `files: []`.

- [ ] **Step 2: Replace the panel markup**

Replace the `.rings-panel` markup with:

```javascript
        <div class="rings-panel">
          <input id="rings-search" type="search" placeholder="Search nodes… ( / )" autocomplete="off">
          <div id="rings-results" class="rings-results" hidden></div>
          <label class="rings-row"><input id="rings-labels" type="checkbox"> Node names</label>
          <label class="rings-row">Ring spin
            <input id="rings-spin" type="range" min="0" max="1" step="0.05">
          </label>
          <div id="rings-detail" class="rings-detail muted">click a node</div>
        </div>
```

- [ ] **Step 3: Search dropdown + detail card wiring**

Replace the old search/detail wiring with:

```javascript
  const detail = root.querySelector('#rings-detail');
  const fmtSize = b => b == null ? '' : b > 1048576 ? (b / 1048576).toFixed(1) + ' MB' : b > 1024 ? (b / 1024).toFixed(0) + ' KB' : b + ' B';
  const fmtAge = iso => { if (!iso) return ''; const d = Math.floor((Date.now() - Date.parse(iso)) / 86400000); return d <= 0 ? 'today' : d + 'd ago'; };
  const showDetail = n => {
    if (!n) { detail.className = 'rings-detail muted'; detail.textContent = 'click a node'; return; }
    const m = n.meta || {};
    const badges = [m.dept || m.project, n.layer].filter(Boolean)
      .map(b => `<span class="badge">${fmt.htmlSafe(String(b))}</span>`).join(' ');
    const linked = (brain.links || []).filter(l => l.source === n.id || l.target === n.id)
      .map(l => l.source === n.id ? l.target : l.source).slice(0, 8);
    detail.className = 'rings-detail';
    detail.innerHTML = `
      <div class="gi-name">${fmt.htmlSafe(n.name)}</div>
      <div style="margin:4px 0">${badges}</div>
      <div class="gi-meta">${[fmtSize(m.size), fmtAge(m.mtime), m.ext].filter(Boolean).join(' · ')}</div>
      ${m.path || m.rel ? `<div class="gi-meta mono" style="word-break:break-all">${fmt.htmlSafe(m.rel || m.path)}</div>` : ''}
      <div class="rings-actions">
        <button data-fly="${fmt.htmlSafe(n.id)}">Fly to</button>
        ${m.path ? `<button data-copy-path="${fmt.htmlSafe(m.path)}">Copy path</button>
        <button data-open="${fmt.htmlSafe(m.path)}">Open on device</button>` : ''}
      </div>
      ${linked.length ? `<div class="gi-meta" style="margin-top:6px">CONNECTIONS</div>
        ${linked.map(id => `<div class="gi-meta">• ${fmt.htmlSafe(String(id).split('::').pop())}</div>`).join('')}` : ''}`;
    detail.querySelector('[data-fly]')?.addEventListener('click', () => rings.flyTo(n.id));
    detail.querySelector('[data-copy-path]')?.addEventListener('click', async ev => {
      await navigator.clipboard.writeText(ev.target.dataset.copyPath); ev.target.textContent = 'copied ✓';
    });
    detail.querySelector('[data-open]')?.addEventListener('click', () =>
      fetch('/api/open', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: n.meta.path }) }));
  };
  const rings = ringsCanvas(root.querySelector('#rings-canvas'), {
    nodes, links,
    onNodeHover: () => {},          // hover shows the on-canvas label only
    onNodeClick: n => {
      showDetail(n);
      if (n.meta?.mem) {
        const elx = root.querySelector(`details[data-mem="${CSS.escape(n.id)}"]`);
        if (elx) { elx.open = true; elx.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
      }
    },
  });

  // search dropdown
  const search = root.querySelector('#rings-search');
  const results = root.querySelector('#rings-results');
  const renderResults = q => {
    if (!q) { results.hidden = true; results.innerHTML = ''; return; }
    const ql = q.toLowerCase();
    const hits = nodes.filter(n => n.name.toLowerCase().includes(ql)).slice(0, 12);
    results.hidden = hits.length === 0;
    results.innerHTML = hits.map(n => `
      <div class="rings-result" data-id="${fmt.htmlSafe(n.id)}">
        <span class="dot" style="background:${n.layer === 'skills' ? '#E8944A' : n.layer === 'routines' ? '#D9B944' : n.layer === 'applications' ? '#7FA8E8' : '#B48CFF'}"></span>
        <span class="rn">${fmt.htmlSafe(n.name)}</span>
        <span class="rp">${fmt.htmlSafe(n.meta?.rel || n.meta?.project || n.group || '')}</span>
      </div>`).join('');
    results.querySelectorAll('.rings-result').forEach(row =>
      row.addEventListener('click', () => {
        const n = nodes.find(x => x.id === row.dataset.id);
        results.hidden = true;
        if (n) { rings.flyTo(n.id); showDetail(n); }
      }));
  };
  search.addEventListener('input', () => { rings.setFilter(search.value.trim() || null); renderResults(search.value.trim()); });
  search.addEventListener('keydown', e => {
    if (e.key === 'Escape') { results.hidden = true; search.blur(); }
    if (e.key === 'Enter') results.querySelector('.rings-result')?.click();
  });
  const onSlash = e => {
    if (e.key === '/' && document.activeElement !== search && !/INPUT|TEXTAREA/.test(document.activeElement?.tagName)) {
      e.preventDefault(); search.focus();
    }
  };
  document.addEventListener('keydown', onSlash);
```

Keep the labels-checkbox and spin-slider wiring unchanged. Update the search placeholder count if desired: `search.placeholder = \`Search ${nodes.length.toLocaleString()} nodes… ( / )\`;`

- [ ] **Step 4: CSS additions**

Append to `web/style.css`:

```css
.rings-results { max-height: 320px; overflow-y: auto; border: 1px solid var(--border); border-radius: 8px; background: var(--bg); }
.rings-result { display: flex; align-items: center; gap: 8px; padding: 6px 8px; cursor: pointer; font-size: 12px; }
.rings-result:hover { background: var(--panel-2, rgba(255,255,255,0.05)); }
.rings-result .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.rings-result .rn { color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rings-result .rp { color: var(--muted); font-size: 10px; margin-left: auto; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 45%; }
.rings-actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
.rings-actions button { font-size: 11px; }
```

- [ ] **Step 5: Syntax check + commit**

Run: `node --check web/routes/brain.js`
Expected: exit 0.

```bash
git add web/routes/brain.js web/style.css
git commit -m "feat: brain panel v2 — search dropdown, detail card, file nodes"
```

---

### Task 4: Live verification

**Files:** none new — verification gate.

- [ ] **Step 1: Full test suite**

Run: `python3 -m unittest discover tests`
Expected: all pass.

- [ ] **Step 2: Live API check**

Start `python3 cli.py dashboard --no-open` (PORT=8123 if 8080 busy). Then:

```bash
curl -s http://127.0.0.1:8123/api/workspace | python3 -c "import json,sys; d=json.load(sys.stdin); print({k: len(v) for k,v in d.items()}); import collections; print(collections.Counter(f['dept'] for f in d['files']).most_common(8))"
curl -s -X POST http://127.0.0.1:8123/api/open -d '{"path":"/etc/passwd"}' -o /dev/null -w '%{http_code}\n'
```

Expected: files count > 0 with per-department counts; the /etc/passwd open attempt returns **403**. Report the real numbers.

- [ ] **Step 3: Browser check**

If Playwright works, screenshot `#/brain`; otherwise report static checks and flag the eyeball for the user. Kill the server after.

- [ ] **Step 4: Commit anything outstanding**

```bash
git add -A && git commit -m "chore: rings v2 verification" --allow-empty
```
