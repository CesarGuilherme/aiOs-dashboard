"""Overview and Skills tabs for /hx — the two chart-bearing pages.

Split out of hx_tabs.py to keep both files under the project's ~400-line cap.
Shared filter helpers (_one, _tabs, ranges) live in hx_views.
"""
from __future__ import annotations

from .db import (
    daily_token_breakdown, model_breakdown, overview_totals, project_summary,
    recent_sessions, skill_breakdown, tool_token_breakdown,
)
from .fx import brl
from .hx_views import (
    _RANGES, _RANGE_DAYS, chart, compact, e, i, kpi as _kpi, one as _one,
    model_short, short, since as _since, source_badge, tabs as _tabs, table, ts,
)
from .memory import get_brain
from .pricing import cost_for, get_plan
from .skills import cached_catalog


# ---------------------------------------------------------------- overview




def overview(ctx: dict) -> str:
    qs, db, pricing, rate = ctx["qs"], ctx["db"], ctx["pricing"], ctx["rate"]
    rng = _one(qs, "range", "30d")
    if rng not in _RANGE_DAYS:
        rng = "30d"
    src = _one(qs, "source", "all")
    if src not in ("all", "claude", "grok"):
        src = "all"
    since = _since(rng)

    totals = overview_totals(db, since, None, source=src)
    projs = project_summary(db, since, None, source=src)
    sess = recent_sessions(db, limit=10, since=since, source=src)
    tools = tool_token_breakdown(db, since, None, source=src)
    daily = daily_token_breakdown(db, since, None, source=src)
    by_model = model_breakdown(db, since, None, source=src)

    cost_usd = 0.0
    for m in by_model:
        c = cost_for(m["model"], m, pricing)
        if c["usd"] is not None:
            cost_usd += c["usd"]
    cache_create = (totals.get("cache_create_5m_tokens") or 0) + (totals.get("cache_create_1h_tokens") or 0)

    # --- header: range tabs + source chips (plain links, real URLs)
    def url(range_key=None, source=None):
        parts = []
        r = range_key or rng
        s = source if source is not None else src
        if r != "30d":
            parts.append("range=" + r)
        if s != "all":
            parts.append("source=" + s)
        return "/hx/overview" + ("?" + "&".join(parts) if parts else "")

    range_tabs = "".join(
        '<a href="{u}" hx-get="{u}" hx-target="#app" hx-push-url="true" class="{cls}">{label}</a>'.format(
            u=url(range_key=k), label=e(label), cls="active" if k == rng else "")
        for k, label in _RANGES.items
    )

    def chip(key, label, cls, meta=""):
        active = src == key
        # Clicking the active source clears the filter, like the SPA's toggle.
        target = "all" if (active and key != "all") else key
        u = url(source=target)
        return (
            '<a href="{u}" hx-get="{u}" hx-target="#app" hx-push-url="true" '
            'class="source-chip badge {cls}{act}">{label}{meta}</a>'
        ).format(u=u, cls=cls, act=" active" if active else "", label=e(label),
                 meta=f" · {meta}" if meta else "")

    chips = chip("all", "all", "all-src") + "".join(
        chip(s["source"], s["source"], source_badge(s["source"]) or "sonnet",
             f'{i(s["sessions"])} sess · {compact(s["billable_tokens"])} tok')
        for s in (totals.get("by_source") or [])
    )
    hint = "" if src == "all" else (
        f'<span class="muted filter-hint" style="font-size:12px">showing <b>{e(src)}</b> only</span>')

    days = _RANGE_DAYS[rng]
    meta = ("last %d days" % days) if days else "all time"
    meta += " · USD→BRL " + f"{rate:.2f}".replace(".", ",")

    plan = get_plan(db)
    plan_info = (pricing.get("plans") or {}).get(plan) or {}
    plan_sub = (
        f'<div class="sub">pay {e(brl(plan_info["monthly"], rate))}/mo on {e(plan_info["label"])}</div>'
        if plan != "api" and plan_info.get("monthly") else ""
    )

    kpis = (
        _kpi(0, "Sessions", i(totals["sessions"]), i(totals["sessions"]))
        + _kpi(1, "Turns", i(totals["turns"]), i(totals["turns"]))
        + _kpi(2, "Input", compact(totals["input_tokens"]), i(totals["input_tokens"]) + " tokens")
        + _kpi(3, "Output", compact(totals["output_tokens"]), i(totals["output_tokens"]) + " tokens")
        + _kpi(4, "Cache read", compact(totals["cache_read_tokens"]), i(totals["cache_read_tokens"]) + " tokens")
        + _kpi(5, "Cache create", compact(cache_create), i(cache_create) + " tokens")
        + _kpi(6, "Est. cost", brl(cost_usd, rate), brl(cost_usd, rate), cls="cost", extra=plan_sub)
    )

    # --- charts
    days_x = [d["day"] for d in daily]
    ch_billable = chart("ch-daily-billable", "stacked", {
        "categories": days_x,
        "series": [
            {"name": "input", "values": [d["input_tokens"] for d in daily], "color": "#27E0FF"},
            {"name": "output", "values": [d["output_tokens"] for d in daily], "color": "#8B7CFF"},
            {"name": "cache create", "values": [d["cache_create_tokens"] for d in daily], "color": "#FFB53D"},
        ],
    })
    ch_cache = chart("ch-daily-cache", "stacked", {
        "categories": days_x,
        "series": [{"name": "cache read", "values": [d["cache_read_tokens"] for d in daily],
                    "color": "#2FE6B8"}],
    })
    model_data = [
        {"name": model_short(m["model"]) or "unknown",
         "value": (m.get("input_tokens") or 0) + (m.get("output_tokens") or 0)
                  + (m.get("cache_create_5m_tokens") or 0) + (m.get("cache_create_1h_tokens") or 0)}
        for m in by_model
    ]
    ch_model = chart("ch-model", "donut", [d for d in model_data if d["value"] > 0], 300)

    top_p = projs[:8]
    ch_projects = chart("ch-projects", "grouped", {
        "categories": [short(p.get("project_name") or p["project_slug"], 20) for p in top_p],
        "series": [
            {"name": "input", "values": [p.get("input_tokens") or 0 for p in top_p], "color": "#27E0FF"},
            {"name": "output", "values": [p.get("output_tokens") or 0 for p in top_p], "color": "#8B7CFF"},
        ],
    }, 320)
    top_t = tools[:8]
    ch_tools = chart("ch-tools", "bar", {
        "categories": [t["tool_name"] for t in top_t],
        "values": [t["calls"] for t in top_t],
        "color": "#8B7CFF",
    }, 320)

    recent = "".join(
        '<tr><td class="mono">{when}</td>'
        '<td><span class="badge {sb}">{src}</span></td>'
        "<td>{link}</td><td class=\"num\">{tok}</td></tr>".format(
            when=e(ts(s["started"])), sb=source_badge(s.get("source")),
            src=e(s.get("source") or "claude"),
            link='<a href="/hx/sessions/{sid}" hx-get="/hx/sessions/{sid}" hx-target="#app" '
                 'hx-push-url="true">{name}</a>'.format(
                     sid=e(s["session_id"]), name=e(s.get("project_name") or s["project_slug"])),
            tok=compact(s["tokens"]),
        )
        for s in sess
    ) or '<tr><td colspan="4" class="muted">no sessions in this range</td></tr>'

    return f"""
<div class="flex" style="margin-bottom:14px">
  <h2 style="margin:0;font-size:16px;letter-spacing:-0.01em">Overview</h2>
  <span class="muted" style="font-size:12px">{e(meta)}</span>
  <div class="spacer"></div>
  <div class="range-tabs" role="tablist">{range_tabs}</div>
</div>
<div class="flex source-chips" style="gap:8px;margin:-4px 0 14px;flex-wrap:wrap;align-items:center">
  {chips}{hint}
</div>

<div class="row cols-7">{kpis}</div>

<div class="card" id="knowledge-card" style="margin-top:16px">
  <div class="flex">
    <h3 style="margin:0">Knowledge</h3>
    <span class="muted" style="font-size:12px">your second brain at a glance</span>
    <div class="spacer"></div>
    <a href="/hx/brain" hx-get="/hx/brain" hx-target="#app" hx-push-url="true"
       style="font-size:12px">Open Brain →</a>
  </div>
  <div id="knowledge-stats" class="muted" style="margin-top:8px;font-size:13px"
       hx-get="/hx/knowledge" hx-trigger="load" hx-swap="innerHTML">loading…</div>
</div>

<details class="card glossary" style="margin-top:16px">
  <summary><h3 style="display:inline-block;margin:0">What do these numbers mean?</h3>
    <span class="muted" style="font-size:12px">— click to expand</span></summary>
  <dl>
    <dt>Session</dt><dd>One agent run — Claude Code JSONL under <code>~/.claude/projects/</code>,
      or a Grok session under <code>~/.grok/sessions/</code>.</dd>
    <dt>Turn</dt><dd>One message you sent.</dd>
    <dt>Input tokens</dt><dd>New context this turn. Grok: uncached input from <code>turn_completed.usage</code> when present, else reconstructed.</dd>
    <dt>Output tokens</dt><dd>Agent reply text. Grok: from usage when present, else estimated from length.</dd>
    <dt>Cache read</dt><dd>Tokens re-used from cache (~10× cheaper). Claude + Grok (<code>cachedReadTokens</code>).</dd>
    <dt>Cache create</dt><dd>Writing into the cache (Claude only — Grok does not report create buckets).</dd>
    <dt>Est. cost</dt><dd>Shown in <strong>R$</strong> (USD rates ×
      <code>~/.brain/.usd_brl</code>). API-equivalent, not subscription math.</dd>
    <dt>Billable tokens</dt><dd>Input + Output + Cache create.</dd>
    <dt>Agent chips</dt><dd>Click <b>claude</b> or <b>grok</b> to filter this page to that agent.
      Click again or <b>all</b> to clear.</dd>
  </dl>
</details>

<div class="row cols-2" style="margin-top:16px">
  <div class="card">
    <h3>Your daily work</h3>
    <p class="muted" style="margin:-4px 0 10px;font-size:12px">Tokens you paid for: what you sent
      (<b>input</b>), what the agent wrote (<b>output</b>), and what got stored for re-use
      (<b>cache create</b>).</p>
    {ch_billable}
  </div>
  <div class="card">
    <h3>Daily cache reads</h3>
    <p class="muted" style="margin:-4px 0 10px;font-size:12px"><b>Cache reads</b> are cheap re-uses
      of things already seen. They cost ~10× less than regular input — high numbers here are good.</p>
    {ch_cache}
  </div>
</div>

<div class="row cols-2" style="margin-top:16px">
  <div class="card"><h3>Tokens by project</h3>{ch_projects}</div>
  <div class="card"><h3>Token usage by model</h3>{ch_model}</div>
</div>

<div class="row cols-2" style="margin-top:16px">
  <div class="card"><h3>Top tools (by call count)</h3>{ch_tools}</div>
  <div class="card">
    <h3 style="display:flex;align-items:center"><span>Recent sessions</span>
      <span class="spacer"></span>
      <a href="/hx/sessions" hx-get="/hx/sessions" hx-target="#app" hx-push-url="true"
         style="font-weight:400;font-size:12px">all →</a></h3>
    {table([("started", False), ("agent", False), ("project", False), ("tokens", True)], recent)}
  </div>
</div>
"""


def knowledge(ctx: dict) -> str:
    """Fragment for the Overview knowledge strip — loaded after paint because
    get_brain() walks the memory dirs on disk."""
    try:
        b = get_brain(ctx["projects_dir"], ctx["db"], ctx["pricing"])
    except Exception:
        return '<span class="muted">brain data unavailable</span>'
    projs = b.get("projects") or []
    memories = sum(len(p.get("entries") or []) for p in projs)
    stats = [
        (memories, "memories", False),
        (len(projs), "projects", False),
        (len(b.get("links") or []), "wikilinks", False),
        (len(b.get("suggestions") or []), "suggested", True),
        (len((b.get("effectiveness") or {}).get("prune_candidates") or []), "injected but never used", True),
    ]
    return "".join(
        '<span style="margin-right:18px"><b style="font-size:16px{c}">{v}</b> {label}</span>'.format(
            c=";color:#FFB454" if warn and v else "", v=i(v), label=e(label))
        for v, label, warn in stats
    )


# ---------------------------------------------------------------- skills

def skills(ctx: dict) -> str:
    rng = _one(ctx["qs"], "range", "30d")
    if rng not in _RANGE_DAYS:
        rng = "30d"
    rows = skill_breakdown(ctx["db"], _since(rng), None)
    catalog = cached_catalog()
    for r in rows:
        info = catalog.get(r["skill"])
        r["tokens_per_call"] = info["tokens"] if info else None
    total = sum(r["invocations"] for r in rows)

    days = _RANGE_DAYS[rng]
    tabs = _tabs("/hx/skills", _RANGES, rng)
    top = rows[:12]
    ch = chart("ch-skills", "bar", {
        "categories": [short(r["skill"], 26) for r in top],
        "values": [r["invocations"] for r in top],
        "color": "#2FE6B8",
    }, 320)

    body = "".join(
        '<tr><td><span class="badge">{skill}</span></td>'
        '<td class="num">{inv}</td><td class="num">{tpc}</td>'
        '<td class="num">{sess}</td><td class="mono">{last}</td></tr>'.format(
            skill=e(r["skill"]), inv=i(r["invocations"]),
            tpc='<span class="muted">—</span>' if r["tokens_per_call"] is None else i(r["tokens_per_call"]),
            sess=i(r["sessions"]), last=e(ts(r["last_used"])),
        )
        for r in rows
    ) or '<tr><td colspan="5" class="muted">no skills invoked in this range</td></tr>'

    return (
        '<div class="flex" style="margin-bottom:14px">'
        '<h2 style="margin:0;font-size:16px;letter-spacing:-0.01em">Skills</h2>'
        f'<span class="muted" style="font-size:12px">{"last %d days" % days if days else "all time"}</span>'
        f'<div class="spacer"></div>{tabs}</div>'
        '<div class="row cols-2">'
        f'{_kpi(0, "Unique skills used", i(len(rows)), i(len(rows)))}'
        f'{_kpi(1, "Total invocations", i(total), i(total))}</div>'
        f'<div class="card" style="margin-top:16px"><h3>Top skills (by invocations)</h3>{ch}</div>'
        '<div class="card" style="margin-top:16px"><h3>All skills</h3>'
        '<p class="muted" style="margin:-4px 0 14px;font-size:12px">"Tokens per call" is the size of '
        "the skill's <code>SKILL.md</code> — what the agent loads into context on each invocation. "
        "Claude: Skill tool. Grok: reads of SKILL.md.</p>"
        + table(
            [("skill", False), ("invocations", True), ("tokens per call", True),
             ("sessions", True), ("last used", False)],
            body,
        )
        + "</div>"
    )


