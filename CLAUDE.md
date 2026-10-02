# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project overview

**AI-Dashboard** (formerly Token Dashboard) — a local multi-agent dashboard for Claude Code + Grok CLI usage, costs (UI in **R$**), and the shared Second Brain. Reads Claude JSONL under `~/.claude/projects/` and Grok `updates.jsonl` under `~/.grok/sessions/`; durable memory remains the Claude-path Brain store used by all agents.

Inspired by [phuryn/claude-usage](https://github.com/phuryn/claude-usage) but diverges in UI (vanilla JS + ECharts, JARVIS HUD theme, hash router, SSE soft refresh) and scope (expensive-prompt drill-down, skills view, tips engine, Second brain graph, streaming-snapshot dedup). See `docs/inspiration.md` for the original's feature set and known limitations.

### Session contract (do not re-derive)
- **What it is:** local stdlib-only CLI + SQLite cache + vanilla `web/` SPA (hash router, no build). Serves on `127.0.0.1:8181`; UI costs in **R$** via `~/.brain/.usd_brl`.
- **Data plane:** `scan_all` every 30s → Claude JSONL + Grok sessions → `~/.brain/token-dashboard.db`; APIs under `/api/*`; Brain from `~/.brain/brain.db` (derived index of the `~/.brain/nodes` tree; schema contract in `memory_parsing.py`).
- **Live UI:** SSE `/api/stream` may emit `scan`; client **soft-refreshes** only (Overview `export refresh`); full remount is **navigation-only**; Brain never auto-refreshes. `/api/stream` fans out to **one queue per connected client** (`_publish` / `_subscribe`) — a single shared queue handed each event to exactly one client.
- **Dedup:** assistant billing key is `(session_id, message_id)`, not top-level `uuid` (streaming snapshots).
- **Touch carefully:** `web/app.js` router/SSE, `web/charts.js` instance reuse, `web/rings.js` teardown, `token_dashboard/server.py` `_scan_loop` — do not “fix” live updates by remounting tabs.

## Status

Working codebase. 158 Python unit tests (`python3 -m unittest discover tests`). Eight UI tabs wired up (Overview, Brain, Prompts, Sessions, Projects, Skills, Tips, Settings). Runs on macOS, Windows, and Linux.

**The default UI is now the server-rendered htmx frontend, served at `/`.** The vanilla SPA is untouched and still fully wired at **`/spa`** (linked from the topbar). To switch back, flip the two branches at the top of `do_GET` in `server.py`.

## The htmx frontend (default, at `/`)

A full-parity server-rendered UI. Both frontends run in the same process: htmx at `/` (canonical tab URLs under `/hx/…`), SPA at `/spa`.

- **Python:** `hx_views.py` (shell, formatters, GET/POST dispatch) · `hx_tabs.py` (projects/sessions/prompts/tips/settings) · `hx_overview.py` (overview + skills, the chart-bearing pages) · `hx_brain.py` (Brain). HTML is f-strings + `html.escape` — **no template engine**, per the stdlib-only rule. Every interpolated value goes through `e()`.
- **Server changes are two:** a route branch after `server.py:111`, and `/hx/*` claiming the POST body **before** `json.loads` (htmx posts form-encoded).
- **One URL, two representations:** plain GET → full page; `HX-Request: true` → fragment only. Nav is per-tab `hx-get` targeting `#app` (never `hx-boost`, which would re-run the shell scripts and stack a second `EventSource`).
- **Assets live in `web/`** so the existing `_serve_static` serves them: `htmx.min.js` (vendored, like `echarts.min.js`), `hx-charts.js`, `hx-brain.js`. `style.css`, `charts.js`, `rings.js`, `hud-background.js` are reused unmodified.
- **Islands:** charts and the Brain graph cannot be server-rendered. The server emits `data-chart`/`data-opt` divs and a `<script type="application/json" id="rings-data">`; the shell hydrates them on initial load **and** on `htmx:afterSwap`.
- **Three teardown rules, all load-bearing.** (1) `htmx:beforeSwap` disposes charts and calls `window.__hxTeardown` **only when `e.detail.target.id === 'app'`** — a partial swap (the Knowledge strip's `hx-trigger="load"`, a dismissed tip) must not wipe the islands. (2) `hx-brain.js` must set `window.__hxTeardown`, or the rings rAF loop runs forever on a detached canvas. (3) **Browser back/forward** restores `#app` (`hx-history-elt`) from htmx's cache and fires *neither* swap event — `htmx:historyRestore` has to tear down and re-hydrate by hand, and the island mounters clear their container first because the cached markup contains dead SVG/canvas.
- **Known regression vs the SPA:** the SSE scan re-fetches the whole tab, so Overview rebuilds its five charts instead of patching in place like `overview.js` `patchLiveData`. Marked with a `ponytail:` comment in `hx_views.py`; upgrade path is a `#kpi-row`-only fragment.
- **Where htmx pays off:** the five table tabs, and real bookmarkable URLs replacing ~80 lines of `location.hash` parsing. **Where it's a tax:** Brain (~90% JS island) and Overview (chart options serialized into attributes).

## Architecture

- `cli.py` → `scan_all` → Claude `scanner.py` + Grok `grok_scanner.py` → `~/.brain/token-dashboard.db` (SQLite, `source` column)
- `token_dashboard/server.py` exposes JSON APIs (`/api/*`) + SSE stream (`/api/stream`) + static frontend (`web/`)
- Brain extras: `memory_parsing.py` (reads `brain.db`) + `memory.py` (projects/graph/ROI/suggestions; keep/remove move notes to `nodes/.trash` and rerun `brain.py rebuild`) → `/api/brain`; `workspace.py` → `/api/workspace` + `POST /api/open`
- `web/` is vanilla JS, no build step — hash router + ECharts + `rings.js` + HUD background; costs via `fmt.usd` → BRL (`~/.brain/.usd_brl`)

### `token_dashboard/server.py` (HTTP process)

Stdlib-only `ThreadingHTTPServer` + handler factory (`build_handler` closes over `db_path` / `projects_dir` / `grok_sessions_dir`) — static `web/`, JSON `/api/*`, SSE `/api/stream`. Daemon `_scan_loop` runs `scan_all` every **30s** and puts on global `EVENTS` only when `n["messages"] > 0`; stream clients get `data: {type:scan…}` or `: ping` keepalives — client **soft-refreshes**, never full remount. Pricing hot-reloads from `pricing.json` mtime; costs computed in-handler via `cost_for` + FX. POSTs are tiny JSON only (`MAX_POST_BYTES`); `/api/open` is gated (`Content-Type`, `allowed_open_path`, exists). Do not change SSE semantics to force remount. Durable notes: `~/.claude/projects/-Volumes-SSD-CESAR-Developer-aiOs-dashboard/memory/server_py_contract.md`.

### `web/rings.js` (Brain tab graph)

Hand-rolled 2D-canvas 3D **synapse graph** for the Brain tab — no SVG, no charting lib, unrelated to `web/charts.js`/ECharts. Renders the Brain **tree**: node radius by kind (domain > project hub > topic > note) + descendant count, hubs glow with a ring. Layout = hierarchical seed (domains on a sphere, children fanned out from their parent) + a short Fruchterman-Reingold pass (`parent` springs short, `link` long, `soft` not laid out), then fit so the p90 node reaches 1.6·WORLD; typed links (`same-solution`, `reuses`, `depends-on`, `supersedes`, `related`) are colored by kind (`EDGE_RGB`) and are the only edges that fire pulses; note labels only at zoom ≥ 1.8. Payload is `brain.graph` from `/api/brain`; a flat legacy payload (no `kind`) still renders the old way. Then it just rotates; color is a **rainbow hue per project/group**, deliberately independent of the app's `--accent` HUD palette — don't reharmonize it with chart colors. Shift+drag orbits (plain drag pans) — ctrl+drag was tried and rejected because ctrl+click opens Safari's context menu. Callers must invoke the returned `__teardown()` on unmount to stop the rAF loop/interval/ResizeObserver. SSE soft-refresh is skipped on the Brain tab so the graph does not reset mid-view. Full remount happens only on hash navigation; Overview implements optional `export async function refresh(root)` for in-place live updates.

### `web/routes/brain.js` (Brain tab route)

`#/brain` page: fetches `/api/brain` + `/api/workspace` (workspace optional). **Canvas = `brain.graph`** (whole tree + typed links; detail panel lists typed links first, with their `why`); skills/routines/apps stay in the sidebar lists. Wires `ringsCanvas`, search (`/`), labels/spin, ROI KPIs, timeline chart, suggestions (dismiss → `/api/tips/dismiss`), prune + auto keep/remove (`/api/brain/keep|remove` → full reload). Default export **returns teardown** (`rings.__teardown` + slash keydown). **No `export refresh`** — SSE deliberately skips this tab. Durable notes: `…/memory/brain_js_contract.md`.

### `web/routes/overview.js` (Overview tab)

Main KPIs/charts. Hash query: `range` (`7d|30d|90d|all`, default **30d**) + `source` (`all|claude|grok`); chip/range clicks rewrite hash (remount). Parallel bundle: overview/projects/sessions/tools/daily/by-model; costs via `usd_brl_rate` → R$. **`export async function refresh(root)`** is the SSE soft path: no-ops without `#ch-daily-billable`, then `patchLiveData` (KPIs, chips meta, sessions, charts) — never full remount on scan. Knowledge strip loads `/api/brain` async after paint. Durable notes: `…/memory/overview_js_contract.md`.

## Data source

Claude Code writes one JSONL file per session to `~/.claude/projects/<project-slug>/<session-id>.jsonl`. Each line is a message record; usage fields live at `message.usage` and model identifier at `message.model`. The scanner is incremental — it tracks each file's mtime and byte offset in the `files` table and only reads new bytes on subsequent scans.

Grok sessions: `~/.grok/sessions/<…>/updates.jsonl` via `grok_scanner.py` (token split reconstructed; see `docs/KNOWN_LIMITATIONS.md`).

## Conventions

- **Fully local.** No telemetry, no remote calls for user data. Tests run offline.
- **Stdlib only.** No `pip install`. If a new feature needs a third-party library, argue for it first — we're willing to pay ergonomics cost to keep install friction at zero.
- **SQLite parameter binding always.** Any f-string in a SQL statement must interpolate only internal, caller-controlled values (column names, placeholder lists). User-reachable values go through `?`.
- **Small files with clear responsibilities.** If a file grows past ~400 lines or accretes three distinct concerns, split it.
- **Streaming-snapshot dedup.** When adding scanner logic that joins the `messages` table, remember `(session_id, message_id)` is the dedup key, not `uuid`. See `scanner._evict_prior_snapshots` and the migration note in `db._migrate_add_message_id`.

## Customizing

Env vars: `PORT` (default 8181), `HOST` (default 127.0.0.1), `CLAUDE_PROJECTS_DIR`, `GROK_SESSIONS_DIR`, `TOKEN_DASHBOARD_DB`, `USD_BRL`. Pricing lives in `pricing.json`. See README.md § Environment variables for details.

## Known limitations

See `docs/KNOWN_LIMITATIONS.md`. Current summary: Skills counts Claude `Skill` plus Grok `SKILL.md` reads; `tokens_per_call` comes from `~/.claude/{skills,scheduled-tasks,plugins}` and `~/.grok/{skills,bundled/skills,installed-plugins}`. Project-local and Task-dispatched skills may show counts but blank token counts. Grok cache **read** comes from `turn_completed.usage.cachedReadTokens` when present; cache **create** stays 0 (Grok does not emit create buckets). Brain effectiveness / ROI extraction cost remain Claude-hook-only.

## Verifying changes

```bash
python3 -m unittest discover tests        # all tests
python3 cli.py dashboard --no-open        # start the server
curl http://127.0.0.1:8181/api/overview   # sanity-check an endpoint
```
