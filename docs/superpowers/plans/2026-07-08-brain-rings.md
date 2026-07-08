# Brain Rings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Brain tab's 3D memory galaxy with a concentric-ring "second brain" visualization of the four agentic layers (Applications / Routines / Memory / Skills), live-scanned from `~/.claude`.

**Architecture:** New stdlib scanner `token_dashboard/workspace.py` behind `GET /api/workspace`; new canvas-2D renderer `web/rings.js` with deterministic polar layout; `web/routes/brain.js` reworked to mount it with a floating control panel (search / labels / spin). The old three.js galaxy stack is deleted.

**Tech Stack:** Python 3 stdlib, vanilla JS, Canvas 2D. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-08-brain-rings-design.md`

## Global Constraints

- Stdlib only — no `pip install`, no new JS libraries.
- Fully local; missing paths yield empty lists, never errors.
- SQLite parameter binding for any SQL (none expected here).
- Files stay under ~400 lines / one responsibility.
- "Done" = ran live, output inspected, numbers reported (live-verify rule).

---

### Task 1: Workspace scanner + `/api/workspace`

**Files:**
- Create: `token_dashboard/workspace.py`
- Create: `tests/test_workspace.py`
- Modify: `token_dashboard/server.py` (add import + GET route next to `/api/brain`, line ~173)

**Interfaces:**
- Produces: `scan_workspace(claude_dir: Path) -> dict` returning
  `{"applications": [{"name", "scope"}], "routines": [{"name"}], "skills": [{"name", "source"}]}`.
  Server route: `GET /api/workspace` → that dict as JSON, using `Path.home() / ".claude"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workspace.py
import json
import tempfile
import unittest
from pathlib import Path

from token_dashboard.workspace import scan_workspace


def _build_fake_claude(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / ".claude.json").write_text(json.dumps({
        "mcpServers": {"svelte": {}, "n8n": {}},
        "projects": {
            "/Users/x/dev/proj-a": {"mcpServers": {"svelte": {}, "playwright": {}}},
            "/Users/x/dev/proj-b": {},
        },
    }))
    claude = root / ".claude"
    (claude / "scheduled-tasks" / "daily-log").mkdir(parents=True)
    (claude / "scheduled-tasks" / "weekly-report.md").write_text("x")
    (claude / "skills" / "watch").mkdir(parents=True)
    (claude / "skills" / "learn").mkdir()
    plug = claude / "plugins" / "cache" / "mkt" / "superpowers" / "6.0.3" / "skills"
    (plug / "brainstorming").mkdir(parents=True)
    (plug / "watch").mkdir()   # duplicate of user skill — user wins
    return claude


class WorkspaceScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.claude = _build_fake_claude(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def test_applications_dedup_global_wins(self):
        apps = scan_workspace(self.claude)["applications"]
        by_name = {a["name"]: a for a in apps}
        self.assertEqual(by_name["svelte"]["scope"], "global")
        self.assertEqual(by_name["n8n"]["scope"], "global")
        self.assertEqual(by_name["playwright"]["scope"], "proj-a")
        self.assertEqual(len(apps), 3)

    def test_routines_dirs_and_md_files(self):
        names = {r["name"] for r in scan_workspace(self.claude)["routines"]}
        self.assertEqual(names, {"daily-log", "weekly-report"})

    def test_skills_dedup_user_wins(self):
        skills = scan_workspace(self.claude)["skills"]
        by_name = {s["name"]: s for s in skills}
        self.assertEqual(by_name["watch"]["source"], "user")
        self.assertEqual(by_name["learn"]["source"], "user")
        self.assertEqual(by_name["brainstorming"]["source"], "plugin")
        self.assertEqual(len(skills), 3)

    def test_missing_everything_yields_empty_lists(self):
        empty = Path(self.tmp.name) / "nope" / ".claude"
        self.assertEqual(scan_workspace(empty),
                         {"applications": [], "routines": [], "skills": []})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_workspace -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'token_dashboard.workspace'`

- [ ] **Step 3: Write the implementation**

```python
# token_dashboard/workspace.py
"""Scan ~/.claude for the agentic layers the Brain rings visualize.

Applications = MCP servers in ~/.claude.json; Routines = scheduled tasks;
Skills = user skills + plugin-cache skills. Every missing path yields an
empty list — the endpoint must never 500 on a bare machine.
"""
import json
from pathlib import Path


def _applications(claude_dir: Path) -> list:
    cfg_path = claude_dir.parent / ".claude.json"
    try:
        cfg = json.loads(cfg_path.read_text())
    except (OSError, ValueError):
        return []
    seen = {}
    for proj_path, proj in (cfg.get("projects") or {}).items():
        for name in (proj.get("mcpServers") or {}):
            seen[name] = {"name": name, "scope": Path(proj_path).name}
    for name in (cfg.get("mcpServers") or {}):
        seen[name] = {"name": name, "scope": "global"}   # global wins
    return sorted(seen.values(), key=lambda a: a["name"])


def _routines(claude_dir: Path) -> list:
    root = claude_dir / "scheduled-tasks"
    if not root.is_dir():
        return []
    out = []
    for child in sorted(root.iterdir()):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            out.append({"name": child.name})
        elif child.suffix == ".md":
            out.append({"name": child.stem})
    return out


def _skills(claude_dir: Path) -> list:
    seen = {}
    for skill_dir in claude_dir.glob("plugins/cache/*/*/*/skills/*"):
        if skill_dir.is_dir():
            seen[skill_dir.name] = {"name": skill_dir.name, "source": "plugin"}
    user_root = claude_dir / "skills"
    if user_root.is_dir():
        for child in sorted(user_root.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                seen[child.name] = {"name": child.name, "source": "user"}  # user wins
    return sorted(seen.values(), key=lambda s: s["name"])


def scan_workspace(claude_dir: Path) -> dict:
    return {
        "applications": _applications(claude_dir),
        "routines": _routines(claude_dir),
        "skills": _skills(claude_dir),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_workspace -v`
Expected: 4 tests PASS

- [ ] **Step 5: Wire the endpoint into `server.py`**

Add to the imports at the top of `token_dashboard/server.py` (near line 21):

```python
from .workspace import scan_workspace
```

Add inside `do_GET`, next to the `/api/brain` branch (~line 173):

```python
            if path == "/api/workspace":
                return _send_json(self, scan_workspace(Path.home() / ".claude"))
```

`Path` is already imported in server.py; if not, add `from pathlib import Path`.

- [ ] **Step 6: Run the full suite**

Run: `python3 -m unittest discover tests`
Expected: all tests pass (68 existing + 4 new)

- [ ] **Step 7: Commit**

```bash
git add token_dashboard/workspace.py tests/test_workspace.py token_dashboard/server.py
git commit -m "feat: /api/workspace scanner for applications/routines/skills"
```

---

### Task 2: `web/rings.js` canvas renderer

**Files:**
- Create: `web/rings.js`

**Interfaces:**
- Consumes: nothing from Task 1 directly (data is passed in by the caller).
- Produces: `ringsCanvas(el, { nodes, links, onNodeClick, onNodeHover })` →
  handle `{ __teardown(), setFilter(query|null), setLabels(bool), setSpin(0..1), focus(nodeId) }`.
  Node shape: `{ id, name, layer: 'skills'|'memory'|'routines'|'applications', group, size, meta }`
  (`meta` is an arbitrary object echoed to `onNodeHover`; memory node `size` = 1 + link count, others 1).
  Link shape: `{ source, target }` (node ids). Unknown ids are skipped silently.

- [ ] **Step 1: Write the renderer**

No JS test harness exists in this repo; this task is verified live in Task 4. Create:

```javascript
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
```

- [ ] **Step 2: Syntax check**

Run: `node --check web/rings.js`
Expected: no output (exit 0). If `node` is unavailable, skip — Task 4's live run covers it.

- [ ] **Step 3: Commit**

```bash
git add web/rings.js
git commit -m "feat: rings.js concentric-ring canvas renderer"
```

---

### Task 3: Brain tab rework — mount rings + control panel

**Files:**
- Modify: `web/routes/brain.js` (replace the "Memory galaxy" card markup, lines ~95-120, and the mounting block, lines ~177-239)
- Modify: `web/style.css` (add `.rings*` panel styles; keep `.galaxy-info` styles — still used for CSS reuse or delete if unreferenced after edit)

**Interfaces:**
- Consumes: `ringsCanvas(el, { nodes, links, onNodeClick, onNodeHover })` from Task 2 (handle: `setFilter`, `setLabels`, `setSpin`, `focus`, `spin`, `labels`, `__teardown`); `GET /api/workspace` from Task 1.
- Produces: nothing downstream.

- [ ] **Step 1: Update imports and data fetch in `brain.js`**

Replace lines 1-3:

```javascript
import { api, fmt } from '/web/app.js';
import { lineChart, stackedBarChart } from '/web/charts.js';
import { ringsCanvas } from '/web/rings.js';
```

Replace line 9 (`const brain = await api('/api/brain');`) with a parallel fetch where a
failed workspace scan degrades to memory-only rings:

```javascript
  const [brain, workspace] = await Promise.all([
    api('/api/brain'),
    api('/api/workspace').catch(() => ({ applications: [], routines: [], skills: [] })),
  ]);
```

- [ ] **Step 2: Replace the galaxy card markup**

The rings card becomes the FIRST card (before Memory ROI). In the first `root.innerHTML = \`` block, prepend this card and delete the entire old "Memory galaxy" card (the one with `graph-head`, `#brain-view`, `.galaxy` / HUD / stamp markup) from the second `innerHTML +=` block:

```javascript
    <div class="card rings-card">
      <h2>Second brain</h2>
      <div class="rings-wrap">
        <div id="rings-canvas"></div>
        <div class="rings-panel">
          <input id="rings-search" type="search" placeholder="Search nodes… ( / )" autocomplete="off">
          <label class="rings-row"><input id="rings-labels" type="checkbox"> Node names</label>
          <label class="rings-row">Ring spin
            <input id="rings-spin" type="range" min="0" max="1" step="0.05">
          </label>
          <div id="rings-detail" class="rings-detail muted">hover a node</div>
        </div>
      </div>
    </div>
```

- [ ] **Step 3: Replace the mounting block**

Delete the whole `if (allEntries.length) { … }` galaxy-mounting block (old lines ~177-239, including `forceGraph`, the 2D/3D `mount()` switcher, HUD wiring) and the `let galaxyTeardown` declaration at the top. Replace with:

```javascript
  // --- rings: memory nodes from /api/brain + agentic layers from /api/workspace
  const nodes = [
    ...allEntries.map(e => ({
      id: e.id, name: e.name, layer: 'memory', group: e.projectLabel,
      size: 1 + e.links.length,
      meta: { project: e.projectLabel, links: e.links.length, mtime: e.mtime, mem: true },
    })),
    ...workspace.skills.map(s => ({
      id: 'skill::' + s.name, name: s.name, layer: 'skills', group: s.source,
      size: 1, meta: { source: s.source },
    })),
    ...workspace.routines.map(r => ({
      id: 'routine::' + r.name, name: r.name, layer: 'routines', group: '',
      size: 1, meta: {},
    })),
    ...workspace.applications.map(a => ({
      id: 'app::' + a.name, name: a.name, layer: 'applications', group: a.scope,
      size: 1, meta: { scope: a.scope },
    })),
  ];
  // memory wikilinks + best-effort skill→routine links (routine name contains skill name)
  const links = [
    ...brain.links,
    ...workspace.routines.flatMap(r =>
      workspace.skills
        .filter(s => r.name.toLowerCase().includes(s.name.toLowerCase()))
        .map(s => ({ source: 'skill::' + s.name, target: 'routine::' + r.name }))),
  ];

  const detail = root.querySelector('#rings-detail');
  const showDetail = n => {
    if (!n) { detail.className = 'rings-detail muted'; detail.textContent = 'hover a node'; return; }
    detail.className = 'rings-detail';
    detail.innerHTML = `
      <div class="gi-name">${fmt.htmlSafe(n.name)}</div>
      <div class="gi-meta">${fmt.htmlSafe(n.layer)}${n.meta.project ? ' · ' + fmt.htmlSafe(n.meta.project) : ''}${n.meta.scope ? ' · ' + fmt.htmlSafe(n.meta.scope) : ''}${n.meta.source ? ' · ' + fmt.htmlSafe(n.meta.source) : ''}${n.meta.links != null ? ` · ${n.meta.links} link${n.meta.links === 1 ? '' : 's'}` : ''}</div>
      ${n.meta.mtime ? `<div class="gi-meta mono">${fmt.ts(n.meta.mtime)}</div>` : ''}`;
  };
  const rings = ringsCanvas(root.querySelector('#rings-canvas'), {
    nodes, links,
    onNodeHover: showDetail,
    onNodeClick: n => {
      if (!n.meta.mem) return;
      const elx = root.querySelector(`details[data-mem="${CSS.escape(n.id)}"]`);
      if (elx) { elx.open = true; elx.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    },
  });

  // panel wiring
  const search = root.querySelector('#rings-search');
  search.addEventListener('input', () => rings.setFilter(search.value.trim() || null));
  search.addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    const q = search.value.trim().toLowerCase();
    const best = q && nodes.find(n => n.name.toLowerCase().includes(q));
    if (best) rings.focus(best.id);
  });
  const onSlash = e => {
    if (e.key === '/' && document.activeElement !== search && !/INPUT|TEXTAREA/.test(document.activeElement?.tagName)) {
      e.preventDefault(); search.focus();
    }
  };
  document.addEventListener('keydown', onSlash);
  const labelsBox = root.querySelector('#rings-labels');
  labelsBox.checked = rings.labels;
  labelsBox.addEventListener('change', () => rings.setLabels(labelsBox.checked));
  const spinSlider = root.querySelector('#rings-spin');
  spinSlider.value = rings.spin;
  spinSlider.addEventListener('input', () => rings.setSpin(spinSlider.value));
```

And change the final return of the route (was `return () => galaxyTeardown?.();`) to:

```javascript
  return () => { rings.__teardown(); document.removeEventListener('keydown', onSlash); };
```

- [ ] **Step 4: Add panel CSS to `style.css`**

Append (reusing existing `--panel`/`--border`/`--muted` vars and `.gi-*` classes):

```css
/* Second-brain rings */
.rings-card { padding-bottom: 12px; }
.rings-wrap { position: relative; height: 85vh; min-height: 480px; background: radial-gradient(ellipse at center, #0d0b14 0%, #070609 80%); border-radius: 10px; overflow: hidden; }
.rings-wrap #rings-canvas { position: absolute; inset: 0; }
.rings-panel { position: absolute; top: 14px; right: 14px; width: 230px; background: var(--panel, rgba(16,14,24,0.92)); border: 1px solid var(--border); border-radius: 10px; padding: 12px; display: flex; flex-direction: column; gap: 10px; backdrop-filter: blur(6px); }
.rings-panel input[type="search"] { width: 100%; background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; font-size: 12px; }
.rings-row { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); }
.rings-row input[type="range"] { flex: 1; }
.rings-detail { border-top: 1px solid var(--border); padding-top: 10px; font-size: 12px; min-height: 44px; }
```

- [ ] **Step 5: Commit**

```bash
git add web/routes/brain.js web/style.css
git commit -m "feat: brain tab mounts second-brain rings with control panel"
```

---

### Task 4: Delete the galaxy stack + live verification

**Files:**
- Delete: `web/galaxy.js`, `web/3d-force-graph.min.js`, `web/three.core.min.js`, `web/three.module.js`, `web/three-spritetext.min.js`, `web/three-spritetext.module.js`
- Modify: `web/charts.js` (delete the `forceGraph` export, lines ~134+ — only brain.js used it)
- Modify: `web/style.css` (delete `.graph-head`, `.galaxy`, `.galaxy-hud`, `.galaxy-stamp` blocks; KEEP `.galaxy-info .gi-name` / `.gi-meta` — the rings detail panel reuses them, but rename the selectors to `.rings-detail .gi-name` / `.rings-detail .gi-meta`)

**Interfaces:**
- Consumes: everything from Tasks 1-3 complete.
- Produces: nothing — cleanup + verification gate.

- [ ] **Step 1: Delete files and dead code**

```bash
git rm web/galaxy.js web/3d-force-graph.min.js web/three.core.min.js \
       web/three.module.js web/three-spritetext.min.js web/three-spritetext.module.js
```

In `web/charts.js`, delete the entire `export function forceGraph(...)` block.
In `web/style.css`, delete the `.graph-head`, `.galaxy { … }`, `.galaxy-hud`, `.galaxy-stamp` rule blocks and change `.galaxy-info .gi-name` → `.rings-detail .gi-name`, `.galaxy-info .gi-meta` → `.rings-detail .gi-meta`; delete the remaining `.galaxy-info { … }` block.

- [ ] **Step 2: Verify no dangling references**

Run: `grep -rn "galaxy\|forceGraph\|3d-force-graph\|three\." web/ --include="*.js" --include="*.html" --include="*.css" | grep -v rings`
Expected: no matches (or only unrelated hits like `three.` inside minified vendor files that were deleted — output must be empty).

- [ ] **Step 3: Full test suite**

Run: `python3 -m unittest discover tests`
Expected: all pass.

- [ ] **Step 4: Live verify (required — live-verify rule)**

```bash
python3 cli.py dashboard --no-open &
sleep 2
curl -s http://127.0.0.1:8080/api/workspace | python3 -m json.tool | head -40
```

Expected: real MCP names (e.g. `svelte`), routines, and skills (e.g. `watch`, `learn`) with sensible scopes/sources. Report the actual counts.

Then open `http://127.0.0.1:8080/#/brain` (use the webapp-testing skill / Playwright if available, otherwise ask the user to eyeball) and confirm:
- four labeled rings render with nodes; slow spin by default
- `/` focuses search; typing dims non-matches; Enter zooms to a match
- hovering shows node detail in the panel; clicking a memory node opens its entry below
- spin slider to 0 freezes; labels checkbox toggles names; settings survive reload
- no console errors

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: replace 3D galaxy with second-brain rings (-2MB vendor js)"
```
