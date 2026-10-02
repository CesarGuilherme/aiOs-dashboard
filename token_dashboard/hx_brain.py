"""Brain tab for the /hx frontend.

Everything except the graph is server-rendered HTML. The graph itself stays a JS
island: web/rings.js is a 436-line animated canvas with no server-side equivalent,
so the page ships its node/link data in a <script type="application/json"> and
web/hx-brain.js mounts it — and registers the teardown the shell calls on swap.
"""
from __future__ import annotations

import json

from .fx import brl
from .hx_views import attr_json, chart, compact, e, i, pct, short, ts
from .memory import get_brain
from .workspace import scan_workspace
from pathlib import Path

TYPE_CLASS = {"user": "opus", "feedback": "haiku", "project": "sonnet",
              "reference": "", "learning": "haiku"}


def _json_script(obj) -> str:
    """JSON safe to embed in a <script> block. A memory body containing the
    literal '</script>' would otherwise close the tag and inject markup."""
    return json.dumps(obj, default=str).replace("</", "<\\/").replace("<!--", "<\\!--")


def _signed(n, fn=compact) -> str:
    return ("+" if (n or 0) > 0 else "") + fn(n or 0)


def _kpi(label: str, value: str, sub: str = "", color: str = "", big: bool = False) -> str:
    style = f' style="color:{color}"' if color else ""
    cls = "value big" if big else "value"
    subline = f'<div class="sub">{sub}</div>' if sub else ""
    return (
        f'<div class="card kpi"><div class="label">{e(label)}</div>'
        f'<div class="{cls}"{style}>{value}</div>{subline}</div>'
    )


def _tok(v: str) -> str:
    return f'{v}<span style="font-size:13px;color:var(--muted)"> tok</span>'


def _roi_card(roi: dict, rate: float) -> str:
    net = roi.get("net_tokens_est") or 0
    trend = roi.get("cache_trend") or []
    last_cache = trend[-1]["hit_rate"] if trend else None
    net_usd = roi.get("net_usd_est")
    extraction_usd = roi.get("extraction_usd")

    kpis = (
        _kpi("Net (est.)", _tok(_signed(net)),
             sub="rough estimate" if net_usd is None
                 else e(_signed(net_usd, lambda v: brl(v, rate)) + " (rough)"),
             color="var(--good)" if net >= 0 else "var(--bad)", big=True)
        + _kpi("Saved · potential", _tok(compact(roi.get("saved_tokens_est") or 0)),
               sub=f'{i(roi.get("reread_count") or 0)} re-reads · '
                   f'{i(roi.get("memorized_targets") or 0)} memorized files')
        + _kpi("Extraction cost", _tok(compact(roi.get("extraction_tokens") or 0)),
               sub=f'{i(roi.get("extraction_sessions") or 0)} passes'
                   + ("" if extraction_usd is None else " · " + e(brl(extraction_usd, rate))))
        + _kpi("Cache hit · today", "—" if last_cache is None else pct(last_cache),
               sub="context reuse")
    )
    trend_chart = ""
    if len(trend) > 1:
        trend_chart = '<h3 style="margin-top:18px">Cache hit-rate trend</h3>' + chart(
            "roi-cache", "line", {
                "x": [d["day"][5:] for d in trend],
                "series": [{"name": "hit rate %",
                            "data": [round(d["hit_rate"] * 100, 1) for d in trend]}],
            }, 200)
    return (
        '<div class="card" style="margin-top:16px"><h2>Memory ROI</h2>'
        '<p class="muted" style="margin:-8px 0 16px">Does the auto-learning brain pay for itself? '
        "<b>Saved</b> = tokens spent re-reading files a memory already covers (Claude + Grok, last 30d — an "
        "avoided-re-read <em>estimate</em>). <b>Cost</b> = real tokens Claude's background extraction "
        "passes burned (Grok has no equivalent pass). <b>Net</b> is the difference. "
        "Cache hit-rate includes both agents.</p>"
        f'<div class="row cols-4">{kpis}</div>{trend_chart}</div>'
    )


def _suggestions(rows) -> str:
    if not rows:
        return ""
    items = "".join(
        '<div class="tip"><div class="tip-head">'
        f'<span class="badge">{e(s.get("project"))}</span><strong>{e(s["title"])}</strong>'
        '<span class="spacer"></span>'
        f'<button onclick="navigator.clipboard.writeText(this.dataset.copy)" '
        f'data-copy="{e(s.get("prompt"))}">copy prompt</button>'
        f'<button class="ghost" hx-post="/hx/tips/dismiss" '
        f'hx-vals="{attr_json({"key": s["key"]})}" hx-target="closest .tip" '
        'hx-swap="outerHTML">dismiss</button>'
        f'</div><p class="tip-body">{e(s["body"])}</p></div>'
        for s in rows
    )
    return (
        '<div class="card" style="margin-top:16px"><h2>Suggested memories</h2>'
        '<p class="muted" style="margin:-8px 0 14px">Knowledge agents keep re-deriving instead of '
        "remembering — mined from the last 30 days of sessions. Copy the prompt into Claude Code or Grok to "
        "close the loop; once the memory exists the suggestion disappears.</p>"
        f"{items}</div>"
    )


def _brain_button(action: str, slug: str, file: str, label: str, cls: str, title: str) -> str:
    """keep/remove. `remove` swaps the whole row away; `keep` only replaces itself."""
    target, swap = ("closest .tip", "outerHTML") if action == "remove" else ("this", "outerHTML")
    return (
        f'<button class="{cls}" hx-post="/hx/brain/{action}" '
        f'hx-vals="{attr_json({"slug": slug, "file": file})}" '
        f'hx-target="{target}" hx-swap="{swap}" title="{e(title)}">{e(label)}</button>'
    )


def _prune(rows) -> str:
    if not rows:
        return ""
    items = "".join(
        '<div class="tip"><div class="tip-head">'
        f'<span class="badge">{e(pc.get("label") or pc.get("slug") or "")}</span>'
        f'<strong>{e(pc["name"])}</strong>'
        f'<span class="muted">injected {i(pc.get("injected"))}× · used 0'
        + (f' · last {e(ts(pc["last"]))}' if pc.get("last") else "")
        + "</span><span class='spacer'></span>"
        + (_brain_button("remove", pc["slug"], pc["file"], "remove", "ghost danger",
                         "quarantine to memory/.trash and drop the index line")
           if pc.get("slug") and pc.get("slug") != "global" and pc.get("file") else "")
        + "</div></div>"
        for pc in rows
    )
    return (
        '<div class="card" style="margin-top:16px"><h2>Memory effectiveness</h2>'
        '<p class="muted" style="margin:-8px 0 14px">Memories the ranker injected <em>because it '
        "judged them relevant</em> to a prompt, but that were never referenced in the session — "
        "candidates to prune. Always-on global/baseline memories don't count here. Each appears in "
        "≥3 sessions with zero hits. Injection logs are Claude Code hooks only.</p>"
        f"{items}</div>"
    )


def _entry(slug: str, en: dict) -> str:
    usage = en.get("usage") or {}
    usage_badge = ""
    if usage.get("injected"):
        usage_badge = (
            f'<span class="badge {"sonnet" if usage.get("used") else ""}" '
            f'title="rank-injected into {i(usage["injected"])} session(s), '
            f'referenced in {i(usage.get("used"))}">'
            f'{i(usage.get("used"))}/{i(usage["injected"])} used</span>'
        )
    auto = en.get("source") == "auto"
    # ponytail: keep/remove edit the list in place; the rings graph still shows the
    # node until you navigate. Re-rendering the graph on every edit is not worth it.
    actions = (
        _brain_button("keep", slug, en["file"], "keep", "ghost", "confirm — stop badging as auto")
        + _brain_button("remove", slug, en["file"], "remove", "ghost danger",
                        "quarantine to memory/.trash and drop the index line")
    ) if auto else ""
    return (
        f'<details class="tip" data-mem="{e(en["id"])}" style="cursor:pointer">'
        '<summary class="tip-head" style="list-style:none">'
        f'<span class="badge {TYPE_CLASS.get(en.get("type"), "")}">{e(en.get("type"))}</span>'
        + ('<span class="badge auto" title="written automatically by the background extraction '
           'pass">auto</span>' if auto else "")
        + f'<strong>{e(en["name"])}</strong>'
        f'<span class="muted">{e(short(en.get("description"), 90))}</span>'
        f'{usage_badge}<span class="spacer"></span>{actions}'
        f'<span class="muted mono" style="font-size:11px">{e(ts(en.get("mtime")))}</span>'
        '</summary>'
        f'<p class="tip-body" style="white-space:pre-wrap">{e(en.get("body"))}</p></details>'
    )


def _project_card(p: dict) -> str:
    learnings = "".join(
        '<div class="tip"><div class="tip-head">'
        f'<span class="badge haiku">{e(l.get("date"))}</span><strong>{e(l["title"])}</strong></div>'
        + (f'<p class="tip-body">{e(l["body"])}</p>' if l.get("body") else "")
        + "</div>"
        for l in p.get("learnings") or []
    )
    if learnings:
        learnings = ("<h3>Learning curve</h3>" + learnings
                     + ('<hr class="divider">' if p["entries"] else ""))
    entries = "".join(_entry(p["slug"], en) for en in p["entries"])
    empty = ('<p class="muted">Empty memory dir.</p>'
             if not p["entries"] and not p.get("learnings") else "")
    n_learn = len(p.get("learnings") or [])
    meta = f'{len(p["entries"])} memories' + (f" · {n_learn} learnings" if n_learn else "")
    summary = f'<p class="muted" style="margin:-8px 0 14px">{e(p.get("summary"))}</p>' if p.get("summary") else ""
    return (
        '<div class="card" style="margin-top:16px">'
        f'<h2><span class="badge">{e(p.get("domain", ""))}</span> {e(p["label"])} '
        '<span class="muted" style="font-weight:400;font-size:12px">'
        f"· {e(meta)}</span></h2>{summary}{learnings}{entries}{empty}</div>"
    )


def render(ctx: dict) -> str:
    brain = get_brain(ctx["projects_dir"], ctx["db"], ctx["pricing"])
    try:
        workspace = scan_workspace(Path.home() / ".claude", Path.home() / ".grok")
    except Exception:
        workspace = {"applications": [], "routines": [], "skills": []}

    roi = brain.get("roi") or {}
    timeline = brain.get("timeline") or []
    eff = brain.get("effectiveness") or {}

    # Canvas = the whole Brain tree (domains, project hubs, topics, notes) + typed
    # links; skills/routines/apps are agentic-layer context and stay in the sidebar.
    graph = brain.get("graph") or {"nodes": [], "links": []}
    nodes = graph["nodes"]
    rings_data = {"nodes": nodes, "links": graph["links"], "workspace": workspace}

    tl_chart = ""
    if timeline:
        tl_chart = (
            '<div class="card" style="margin-top:16px"><h2>Learning timeline</h2>'
            '<p class="muted" style="margin:-8px 0 14px">Memories added per day — '
            '<span class="badge haiku">auto</span> written by the background pass, '
            '<span class="badge opus">you</span> hand-written, plus journal learnings.</p>'
            + chart("brain-timeline", "stacked", {
                "categories": [d["day"][5:] for d in timeline],
                "series": [
                    {"name": "auto", "values": [d["auto"] for d in timeline], "color": "#2FE6B8"},
                    {"name": "you", "values": [d["user"] for d in timeline], "color": "#8B7CFF"},
                    {"name": "learnings", "values": [d["learnings"] for d in timeline],
                     "color": "#FFB53D"},
                ],
            }, 220)
            + "</div>"
        )

    graph = (
        '<div class="card rings-card"><h2>Second brain</h2><div class="rings-wrap">'
        '<div id="rings-canvas"></div><div class="rings-panel">'
        f'<input id="rings-search" type="search" placeholder="Search {i(len(nodes))} nodes… ( / )"'
        ' autocomplete="off">'
        '<div id="rings-results" class="rings-results" hidden></div>'
        '<label class="rings-row"><input id="rings-labels" type="checkbox"> Node names</label>'
        '<label class="rings-row">Drift<input id="rings-spin" type="range" min="0" max="1" '
        'step="0.05"></label>'
        '<div id="rings-detail" class="rings-detail muted">click a node</div>'
        '<div class="rings-layers" id="rings-layers"></div></div></div>'
        f'<script type="application/json" id="rings-data">{_json_script(rings_data)}</script>'
        "</div>"
    )

    return (
        graph
        + _roi_card(roi, ctx["rate"])
        + tl_chart
        + _suggestions(brain.get("suggestions") or [])
        + _prune(eff.get("prune_candidates") or [])
        + "".join(_project_card(p) for p in brain["projects"])
    )
