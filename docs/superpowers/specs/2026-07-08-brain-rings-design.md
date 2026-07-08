# Brain tab: Second Brain ring visualization

**Date:** 2026-07-08
**Status:** approved design
**Replaces:** the 3D memory galaxy (`web/galaxy.js`) and its 2D/3D toggle in the Brain tab.

Inspired by the RoboNuggets "second brain" layout (YouTube VoKiKvgpk78): concentric ring
bands for the four agentic layers, with a floating control panel (search, spin, labels).

## Scope

- **In:** ring visualization of the agentic layers (Applications / Routines / Memory /
  Skills), live-scanned from `~/.claude`; search; spin + labels controls; node detail on
  hover/click; deletion of the old 3D galaxy stack.
- **Out (deliberately):** workspace file scanning, Force/Circle/Hex alternative layouts,
  Departments/Folders view modes, link-spring/size sliders, expand/collapse, "open file
  on device", retrieval scoring (brain.js from the video). Add a layout-tab switcher only
  when a second layout is actually requested.

## Backend

New module `token_dashboard/workspace.py`, endpoint `GET /api/workspace` wired into
`server.py`. Stdlib-only scanning; every missing path yields an empty list, never an
error.

Returns:

```json
{
  "applications": [{"name": "svelte", "scope": "global"}],
  "routines":     [{"name": "daily-log"}],
  "skills":       [{"name": "watch", "source": "plugin"}]
}
```

- **applications** — MCP server names from `~/.claude.json`: top-level `mcpServers` keys
  (`scope: "global"`) plus each `projects.<path>.mcpServers` key (`scope: <project
  basename>`). Dedup by name, global wins.
- **routines** — one entry per child (dir or `.md` file) of `~/.claude/scheduled-tasks/`.
- **skills** — dirs under `~/.claude/skills/` (`source: "user"`) and skill dirs matching
  `~/.claude/plugins/cache/*/*/*/skills/*` (`source: "plugin"`). Dedup by name, user wins.

Memory nodes are NOT served here — the frontend already gets them from `/api/brain`.

## Frontend

### `web/rings.js` (new; `web/galaxy.js` deleted)

Canvas-2D renderer, exported as `ringsCanvas(el, opts)` returning a handle with
`__teardown()` (same contract the router expects today).

- **Bands, inside → out:** Skills (orange), Memory (purple), Routines (yellow),
  Applications (blue). Ring-band arc labels always visible.
- **Placement is deterministic:** polar coordinates; within each band, nodes grouped by
  category (memory by project, skills by source), even angular spacing per group, small
  hash-based jitter in radius for an organic look. No force simulation.
- **Links:** memory `[[wikilinks]]` (from `/api/brain`) as curved lines; skill→routine
  link when a routine name contains a skill name (cheap string match, best-effort).
- **Interaction:** wheel zoom, drag pan, hover highlights node + shows detail in the
  panel, click on a memory node opens its `<details>` entry below (existing behavior).
- **Spin:** slow default rotation; slider 0–max, 0 freezes and stops the rAF loop when
  nothing else animates.
- **Node sizing:** memory nodes sized by link count (as today); other layers fixed size.

### Control panel (in `brain.js` markup, floating top-right over the canvas)

- **Search** input, `/` focuses it. Typing dims non-matching nodes (match on node name,
  case-insensitive); Enter pans/zooms to the best match.
- **Node names** checkbox — toggles per-node labels.
- **Ring spin** slider.
- **Detail area** — hovered/clicked node: name, layer, project/scope/source, link count,
  mtime (memories only).
- Settings (spin, labels) persist to `localStorage` under `td.rings.*`.

### `brain.js` rework

- Viz card first, ~85vh tall; the 3D/2D toggle and galaxy HUD/stamp markup go away
  (panel replaces them). ROI, timeline, suggestions, prune, per-project memory lists
  stay, below the viz.
- Fetches `/api/brain` and `/api/workspace` in parallel; a failed `/api/workspace`
  renders memory-only rings rather than breaking the tab.

### Deletions

`web/galaxy.js`, `web/3d-force-graph.min.js`, `web/three.core.min.js`,
`web/three.module.js`, `web/three-spritetext.min.js`, `web/three-spritetext.module.js`
(~2 MB). `charts.js#forceGraph` stays only if still referenced elsewhere; delete if not.

## Testing & verification

- Unit test `tests/test_workspace.py`: scanner pointed at a temp fake `~/.claude` tree —
  asserts MCP/global + project dedup, routines listing, user + plugin skills dedup, and
  empty results for missing paths.
- Live verify (required): `python3 cli.py dashboard --no-open`, `curl /api/workspace`
  shows real MCPs/routines/skills with counts reported, Brain tab eyeballed: rings render,
  search dims/zooms, click opens memory entry, no console errors.
