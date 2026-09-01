# Known Limitations

None of these are blockers — the dashboard still gives you useful information. They're the rough edges you'll notice if you look hard.

## Grok tokens: real usage when present, estimate otherwise

Claude JSONL carries Anthropic `input_tokens` / `output_tokens` / cache fields per message. Grok `updates.jsonl` emits a `turn_completed` event with `usage` on most modern turns:

- `inputTokens` / `outputTokens` / `cachedReadTokens` (and optional `costUsdTicks`)
- Scanner maps **uncached** input = `inputTokens − cachedReadTokens`, cache read = `cachedReadTokens`, so `cost_for` does not double-bill cache
- **`cache_create_*` stays 0** — Grok does not report cache-write (5m/1h) buckets

Older turns without `usage` still fall back to reconstruction:

- **context growth** from `params._meta.totalTokens` → `input_tokens`
- **output** ≈ `chars//4` of agent text (+ half weight for thoughts)
- **cache_*** = 0

Costs use those counts against rates in `pricing.json`. Optional server-stamped `costUsdTicks` is not yet preferred over the rate table.

## Costs display in BRL

Internal pricing is USD/1M tokens. The UI multiplies by the rate in `~/.brain/.usd_brl` (env `USD_BRL` override; fallback 5.40) and formats as `R$ 9,49`.

## Second Brain memory is shared; injection effectiveness is Claude-only

Durable knowledge lives under `~/.brain/projects/` + `~/.brain/global` for **all** agents. The `memory_injections` / `memory_usage` tables (Brain “effectiveness”) and ROI extraction-cost (the `MEMORY_EXTRACTION_PASS` sentinel) are still filled only by Claude Code hooks. Grok has no equivalent auto-learn pass.

## Skills token counts are partial

The Skills route counts Claude `Skill` tool invocations **and** Grok `read_file`/`Read` of a `SKILL.md`. **tokens-per-call** comes from catalog files under `~/.claude/{skills,scheduled-tasks,plugins}` and `~/.grok/{skills,bundled/skills,installed-plugins}`. Project-local `.claude/skills/` / `.grok/skills/` and Task-dispatched skills still show counts (when invoked) but may leave the token column blank.

## Cost for Pro / Max / Max-20x users is shown as API-equivalent, not subscription value

The Settings route lets you select your pricing plan, but the Overview cost number is always the API-equivalent (what the same usage would have cost on pay-per-token rates). If you're on Pro you pay a flat $20/month regardless of how much of that API-equivalent number you rack up. We don't do "subscription ROI" math yet — Anthropic doesn't publish per-plan rate limits as public JSON, and faking it would be worse than not doing it.

## Cowork sessions are invisible

If you use Claude's Cowork mode (server-side sessions, not local `claude` CLI), those sessions don't write JSONL to `~/.claude/projects/` and the dashboard can't see them.

## Non-standard model names get tier-fallback pricing

If a transcript references a model ID not in `pricing.json` (e.g. a future snapshot that isn't in our table yet), cost is estimated from the tier substring (`opus` / `sonnet` / `haiku`) in the name. The UI marks these as `estimated: true`. If the model name contains none of those substrings, cost is reported as null.

## First scan can be slow

The first `python3 cli.py scan` on a heavy user's machine can read tens of MB across hundreds of JSONLs. Subsequent scans are incremental (mtime + byte-offset tracking in the `files` table), so they're fast.

## Running two dashboards against the same DB

Both will fight over the SQLite file and you'll see inconsistent numbers and occasional `database is locked` errors. Only run one at a time. If you want to view the dashboard from a second device, use `HOST=0.0.0.0` on the one running machine and point the second device's browser at it.
