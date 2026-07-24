# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project overview

**AI-Dashboard** (formerly Token Dashboard) — a local multi-agent dashboard for Claude Code + Grok CLI usage, costs (UI in **R$**), and the shared Second Brain. Reads Claude JSONL under `~/.claude/projects/` and Grok `updates.jsonl` under `~/.grok/sessions/`; durable memory remains the Claude-path Brain store used by all agents.

Inspired by [phuryn/claude-usage](https://github.com/phuryn/claude-usage) but diverges in UI (vanilla JS + ECharts, JARVIS HUD theme, hash router, SSE soft refresh) and scope (expensive-prompt drill-down, skills view, tips engine, Second brain graph, streaming-snapshot dedup). See `docs/inspiration.md` for the original's feature set and known limitations.

### Session contract (do not re-derive)
- **What it is:** local stdlib-only CLI + SQLite cache + vanilla `web/` SPA (hash router, no build). Serves on `127.0.0.1:8181`; UI costs in **R$** via `~/.claude/.usd_brl`.
- **Data plane:** `scan_all` every 30s → Claude JSONL + Grok sessions → `~/.claude/token-dashboard.db`; APIs under `/api/*`; Brain from `~/.claude/projects/*/memory/` + global memory.
- **Live UI:** SSE `/api/stream` may emit `scan`; client **soft-refreshes** only (Overview `export refresh`); full remount is **navigation-only**; Brain never auto-refreshes.
- **Dedup:** assistant billing key is `(session_id, message_id)`, not top-level `uuid` (streaming snapshots).
- **Touch carefully:** `web/app.js` router/SSE, `web/charts.js` instance reuse, `web/rings.js` teardown, `token_dashboard/server.py` `_scan_loop` — do not “fix” live updates by remounting tabs.

## Status

Working codebase. 99 Python unit tests (`python3 -m unittest discover tests`). Eight UI tabs wired up (Overview, Brain, Prompts, Sessions, Projects, Skills, Tips, Settings). Runs on macOS, Windows, and Linux.

## Architecture

- `cli.py` → `scan_all` → Claude `scanner.py` + Grok `grok_scanner.py` → `~/.claude/token-dashboard.db` (SQLite, `source` column)
- `token_dashboard/server.py` exposes JSON APIs (`/api/*`) + SSE stream (`/api/stream`) + static frontend (`web/`)
- Brain extras: `memory.py` / `memory_parsing.py` → `/api/brain`; `workspace.py` → `/api/workspace` + `POST /api/open`
- `web/` is vanilla JS, no build step — hash router + ECharts + `rings.js` + HUD background; costs via `fmt.usd` → BRL (`~/.claude/.usd_brl`)

### `token_dashboard/server.py` (HTTP process)

Stdlib-only `ThreadingHTTPServer` + handler factory (`build_handler` closes over `db_path` / `projects_dir` / `grok_sessions_dir`) — static `web/`, JSON `/api/*`, SSE `/api/stream`. Daemon `_scan_loop` runs `scan_all` every **30s** and puts on global `EVENTS` only when `n["messages"] > 0`; stream clients get `data: {type:scan…}` or `: ping` keepalives — client **soft-refreshes**, never full remount. Pricing hot-reloads from `pricing.json` mtime; costs computed in-handler via `cost_for` + FX. POSTs are tiny JSON only (`MAX_POST_BYTES`); `/api/open` is gated (`Content-Type`, `allowed_open_path`, exists). Do not change SSE semantics to force remount. Durable notes: `~/.claude/projects/-Volumes-SSD-CESAR-Developer-aiOs-dashboard/memory/server_py_contract.md`.

### `web/rings.js` (Brain tab graph)

Hand-rolled 2D-canvas 3D **synapse graph** for the Brain tab — no SVG, no charting lib, unrelated to `web/charts.js`/ECharts. One-shot Fruchterman-Reingold 3D layout on mount, then just rotates; color is a **rainbow hue per project/group**, deliberately independent of the app's `--accent` HUD palette — don't reharmonize it with chart colors. Shift+drag orbits (plain drag pans) — ctrl+drag was tried and rejected because ctrl+click opens Safari's context menu. Callers must invoke the returned `__teardown()` on unmount to stop the rAF loop/interval/ResizeObserver. SSE soft-refresh is skipped on the Brain tab so the graph does not reset mid-view. Full remount happens only on hash navigation; Overview implements optional `export async function refresh(root)` for in-place live updates.

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

See `docs/KNOWN_LIMITATIONS.md`. Current summary: Skills `tokens_per_call` is populated only for skills installed under the three scanned roots (`~/.claude/skills/`, `~/.claude/scheduled-tasks/`, `~/.claude/plugins/`); project-local skills and subagent-dispatched skills show invocation counts but blank token counts. Grok cache **read** comes from `turn_completed.usage.cachedReadTokens` when present; cache **create** stays 0 (Grok does not emit create buckets).

## Verifying changes

```bash
python3 -m unittest discover tests        # all tests
python3 cli.py dashboard --no-open        # start the server
curl http://127.0.0.1:8181/api/overview   # sanity-check an endpoint
```
