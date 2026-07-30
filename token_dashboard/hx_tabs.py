"""Tab renderers for the /hx frontend. Each takes ctx and returns an HTML fragment.

ctx keys: db, projects_dir, pricing, rate (USD→BRL), qs (parsed query string), sub
(the path remainder, e.g. a session id).

Every user-controlled value goes through `e()`. No exceptions — prompt text, tip
bodies, project names and session ids all come from disk.
"""
from __future__ import annotations

import json

from . import hx_brain
from .db import expensive_prompts, project_summary, recent_sessions, session_turns
from .fx import brl
from .hx_views import (
    Opts as _Opts, attr_json, card, e, i, model_class, model_short, one as _one,
    short, source_badge, table, tabs as _tabs, ts,
)
from .pricing import cost_for, get_plan
from .tips import all_tips


def _session_link(sid: str) -> str:
    return (
        '<a href="/hx/sessions/{s}" hx-get="/hx/sessions/{s}" hx-target="#app" '
        'hx-push-url="true" class="mono">{sh}…</a>'
    ).format(s=e(sid), sh=e(sid[:8]))


# ---------------------------------------------------------------- projects

def projects(ctx: dict) -> str:
    rows = project_summary(ctx["db"], None, None, source=_one(ctx["qs"], "source", "all"))
    body = "".join(
        '<tr><td title="{slug}">{name}</td>'
        '<td class="num">{sessions}</td><td class="num">{turns}</td>'
        '<td class="num">{billable}</td><td class="num">{cache}</td></tr>'.format(
            slug=e(r["project_slug"]),
            name=e(r.get("project_name") or r["project_slug"]),
            sessions=i(r["sessions"]),
            turns=i(r["turns"]),
            billable=i(r["billable_tokens"]),
            cache=i(r["cache_read_tokens"]),
        )
        for r in rows
    )
    return card(
        "Projects",
        table(
            [("project", False), ("sessions", True), ("turns", True),
             ("billable tokens", True), ("cache reads", True)],
            body,
        ),
        sub="Sorted by billable token spend. Cache reads are billed cheaper, so high "
            "cache-read columns are good.",
    )


# ---------------------------------------------------------------- sessions

def sessions(ctx: dict) -> str:
    if ctx["sub"]:
        return _session_detail(ctx, ctx["sub"])
    rows = recent_sessions(ctx["db"], limit=100, source=_one(ctx["qs"], "source", "all"))
    body = "".join(
        '<tr><td class="mono">{started}</td>'
        '<td><span class="badge {sbadge}">{src}</span></td>'
        '<td title="{slug}">{name}</td>'
        '<td class="num">{turns}</td><td class="num">{tokens}</td>'
        "<td>{link}</td></tr>".format(
            started=e(ts(s["started"])),
            sbadge=source_badge(s.get("source")),
            src=e(s.get("source") or "claude"),
            slug=e(s["project_slug"]),
            name=e(s.get("project_name") or s["project_slug"]),
            turns=i(s["turns"]),
            tokens=i(s["tokens"]),
            link=_session_link(s["session_id"]),
        )
        for s in rows
    )
    return card(
        "Sessions",
        table(
            [("started", False), ("agent", False), ("project", False),
             ("turns", True), ("tokens", True), ("session", False)],
            body,
        ),
    )


def _session_detail(ctx: dict, sid: str) -> str:
    turns = session_turns(ctx["db"], sid)
    tin = sum(t.get("input_tokens") or 0 for t in turns if t["type"] == "assistant")
    tout = sum(t.get("output_tokens") or 0 for t in turns if t["type"] == "assistant")
    tcache = sum(t.get("cache_read_tokens") or 0 for t in turns if t["type"] == "assistant")
    cwd = next((t["cwd"] for t in turns if t.get("cwd")), "")
    base = cwd.replace("\\", "/").rstrip("/").split("/")[-1] if cwd else ""
    project = base or (turns[0]["project_slug"] if turns else "")
    started = turns[0]["timestamp"] if turns else ""
    ended = turns[-1]["timestamp"] if turns else ""

    rows = []
    for t in turns:
        tools = json.loads(t["tool_calls_json"]) if t.get("tool_calls_json") else []
        summary = (
            short(t["prompt_text"], 110) if t.get("prompt_text")
            else " · ".join(x.get("name", "") for x in tools)
        )
        badge = (
            f'<span class="badge {model_class(t["model"])}">{e(model_short(t["model"]))}</span>'
            if t.get("model") else ""
        )
        side = ' <span class="badge">side</span>' if t.get("is_sidechain") else ""
        rows.append(
            '<tr><td class="mono">{time}</td><td>{type}{side}</td><td>{badge}</td>'
            '<td class="blur-sensitive">{summary}</td>'
            '<td class="num">{tin}</td><td class="num">{tout}</td>'
            '<td class="num">{cache}</td></tr>'.format(
                time=e((t.get("timestamp") or "")[11:19]),
                type=e(t["type"]), side=side, badge=badge, summary=e(summary),
                tin=i(t.get("input_tokens")), tout=i(t.get("output_tokens")),
                cache=i(t.get("cache_read_tokens")),
            )
        )

    header = (
        '<div class="card"><h2 style="display:flex;align-items:center">'
        f"<span>Session {e(sid[:8])}…</span><span class='spacer'></span>"
        '<a href="/hx/sessions" hx-get="/hx/sessions" hx-target="#app" hx-push-url="true" '
        'class="muted">← all sessions</a></h2>'
        '<div class="flex muted" style="font-family:var(--mono);font-size:12px;'
        'flex-wrap:wrap;gap:14px">'
        f"<span>{e(project)}</span><span>{e(ts(started))} → {e(ts(ended))}</span>"
        f"<span>{len(turns)} records</span>"
        f"<span>{i(tin)} in · {i(tout)} out · {i(tcache)} cache rd</span></div></div>"
    )
    detail = (
        '<div class="card" style="margin-top:16px"><h3>Turn-by-turn</h3>'
        + table(
            [("time", False), ("type", False), ("model", False), ("prompt / tools", False),
             ("in", True), ("out", True), ("cache rd", True)],
            "".join(rows),
        )
        + "</div>"
    )
    return header + detail


# ---------------------------------------------------------------- prompts

_SORTS = _Opts("sort", [("tokens", "Most tokens"), ("recent", "Most recent")])


def prompts(ctx: dict) -> str:
    sort = _one(ctx["qs"], "sort", "tokens")
    if sort not in ("tokens", "recent"):
        sort = "tokens"
    rows = expensive_prompts(ctx["db"], limit=100, sort=sort)
    for r in rows:
        r["estimated_cost_usd"] = cost_for(r["model"], {
            "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": r["cache_read_tokens"],
            "cache_create_5m_tokens": 0, "cache_create_1h_tokens": 0,
        }, ctx["pricing"])["usd"]

    body = "".join(
        '<tr><td class="{col1cls}">{col1}</td>'
        # ponytail: <details> inline beats the SPA's click-to-open drawer — no JS,
        # no in-memory row cache, and the full text is in the page for ⌘F.
        '<td class="blur-sensitive"><details><summary>{summary}</summary>'
        "<pre>{full}</pre></details></td>"
        '<td><span class="badge {mcls}">{model}</span></td>'
        '<td class="num">{billable}</td><td class="num">{cache}</td>'
        "<td>{link}</td></tr>".format(
            col1cls="mono" if sort == "recent" else "num mono",
            col1=e(ts(r["timestamp"])) if sort == "recent" else e(brl(r["estimated_cost_usd"], ctx["rate"], 4)),
            summary=e(short(r["prompt_text"], 110)),
            full=e(r["prompt_text"] or ""),
            mcls=model_class(r["model"]), model=e(model_short(r["model"])),
            billable=i(r["billable_tokens"]), cache=i(r["cache_read_tokens"]),
            link=_session_link(r["session_id"]),
        )
        for r in rows
    ) or '<tr><td colspan="6" class="muted">no prompts yet</td></tr>'

    sub = ("Your latest prompts and the assistant turn each one triggered."
           if sort == "recent" else "The prompts that cost the most tokens.")
    head = (
        '<div class="flex" style="margin-bottom:14px">'
        '<h2 style="margin:0;font-size:16px;letter-spacing:-0.01em">Prompts</h2>'
        '<div class="spacer"></div>' + _tabs("/hx/prompts", _SORTS, sort) + "</div>"
    )
    return head + card(
        "",
        table(
            [("when" if sort == "recent" else "cache cost", False), ("prompt", False),
             ("model", False), ("tokens", True), ("cache rd", True), ("session", False)],
            body,
        ),
        sub=sub + " Expand a row to see the full prompt.",
    )


# ---------------------------------------------------------------- tips

_EMPTY_TIPS = ('<p class="muted">No suggestions right now. AI-Dashboard surfaces patterns '
               "weekly — check back after more activity.</p>")


def tips(ctx: dict) -> str:
    rows = all_tips(ctx["db"], ctx["projects_dir"])
    if not rows:
        return f'<div class="card" id="tips-card"><h2>Suggestions</h2>{_EMPTY_TIPS}</div>'
    items = "".join(tip_block(t) for t in rows)
    return (
        '<div class="card" id="tips-card"><h2>Suggestions</h2>'
        '<p class="muted" style="margin:-8px 0 14px">Rule-based pattern detection over the '
        "last 7 days. Dismissed tips re-appear after 14 days.</p>" + items + "</div>"
    )


def tip_block(t: dict) -> str:
    copy = (
        '<button onclick="navigator.clipboard.writeText(this.dataset.copy)" '
        f'data-copy="{e(t["prompt"])}">copy prompt</button>'
        if t.get("prompt") else ""
    )
    return (
        '<div class="tip"><div class="tip-head">'
        f'<span class="badge">{e(t.get("project") or t.get("category"))}</span>'
        f"<strong>{e(t['title'])}</strong><span class='spacer'></span>{copy}"
        f'<button class="ghost" hx-post="/hx/tips/dismiss" '
        f'hx-vals="{attr_json({"key": t["key"]})}" '
        'hx-target="closest .tip" hx-swap="outerHTML">dismiss</button>'
        f'</div><p class="tip-body">{e(t["body"])}</p></div>'
    )


# ---------------------------------------------------------------- settings

def settings(ctx: dict) -> str:
    pricing = ctx["pricing"]
    current = get_plan(ctx["db"])
    models = pricing.get("models", {})
    keys = [k for k in (pricing.get("pricing_page_models") or []) if k in models] or list(models)

    opts = "".join(
        '<option value="{k}"{sel}>{label}</option>'.format(
            k=e(k), sel=" selected" if k == current else "",
            label=e(v["label"] + (f" — ${v['monthly']}/mo" if v.get("monthly") else "")),
        )
        for k, v in pricing.get("plans", {}).items()
    )
    rows = "".join(
        '<tr><td><span class="badge {tier}">{k}</span></td>'
        '<td class="num">${inp:.2f}</td><td class="num">${out:.2f}</td>'
        '<td class="num">${cr:.2f}</td><td class="num">${c5:.2f}</td>'
        '<td class="num">${c1:.2f}</td></tr>'.format(
            tier=e(models[k].get("tier", "")), k=e(k),
            inp=models[k]["input"], out=models[k]["output"], cr=models[k]["cache_read"],
            c5=models[k]["cache_create_5m"], c1=models[k]["cache_create_1h"],
        )
        for k in keys
    )
    return (
        '<div class="card"><h2>Settings</h2>'
        '<h3 style="margin-top:16px">Plan</h3>'
        '<p class="muted" style="margin:0 0 12px">Sets how cost is displayed. API mode shows '
        "pay-per-token rates. Subscription modes show what you actually pay each month.</p>"
        '<form class="flex" hx-post="/hx/plan" hx-target="#plan-msg" hx-swap="innerHTML">'
        f'<select name="plan">{opts}</select>'
        '<button class="primary" type="submit">Save</button>'
        '<span id="plan-msg" class="muted"></span></form>'
        '<hr class="divider"><h3>Pricing table</h3>'
        '<p class="muted" style="margin:0 0 12px">Models available in Claude Code and Grok '
        "Build. Edit <code>pricing.json</code> to change what appears. Reload after editing.</p>"
        + table(
            [("model", False), ("input", True), ("output", True), ("cache read", True),
             ("cache 5m", True), ("cache 1h", True)],
            rows,
        )
        + '<p class="muted" style="margin-top:8px;font-size:11px">Rates per 1M tokens, USD. '
        "Other model IDs still bill via rates / tier_fallback.</p>"
        '<hr class="divider"><h3>Privacy</h3>'
        "<p class='muted'>Press <code>Cmd/Ctrl + B</code> anywhere to blur prompt text and "
        "other sensitive content for screenshots.</p></div>"
    )



from .hx_overview import knowledge, overview, skills


# ---------------------------------------------------------------- registry

RENDERERS = {
    "overview": overview,
    "projects": projects,
    "sessions": sessions,
    "prompts": prompts,
    "skills": skills,
    "tips": tips,
    "settings": settings,
    "brain": hx_brain.render,
}

# Lazily-loaded sub-fragments (not tabs, no shell).
FRAGMENTS = {"knowledge": knowledge}


def render(tab: str, ctx: dict) -> str:
    return RENDERERS[tab](ctx)
