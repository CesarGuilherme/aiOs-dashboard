"""htmx frontend (v2) served under /hx — shell, formatters and request dispatch.

The SPA under `web/` stays untouched at `/`. This module is the server-rendered
counterpart: one URL per tab, full page on a plain GET, fragment only when htmx
asks (`HX-Request: true`). HTML is built with f-strings + html.escape — no
template engine, per the project's stdlib-only rule.

Every interpolated value must go through `e()` or a numeric formatter. Prompt
text, memory bodies, project names and tip bodies are all user data.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timedelta, timezone

from .fx import brl, usd_brl_rate

TABS = ("overview", "brain", "prompts", "sessions", "projects", "skills", "tips", "settings")


# ---------------------------------------------------------------- formatters
# Ports of web/app.js:22-45. The SPA stays the behavioural source of truth; when
# the two drift, match app.js rather than "improving" this side.

def e(s) -> str:
    """Escape any value for HTML text or a quoted attribute."""
    return html.escape("" if s is None else str(s), quote=True)


def i(n) -> str:
    """fmt.int — thousands separators."""
    try:
        return f"{int(n or 0):,}"
    except (TypeError, ValueError):
        return "0"


def compact(n) -> str:
    """fmt.compact — Intl compact notation, max 1 fraction digit (1.2K, 3.4M)."""
    try:
        v = float(n or 0)
    except (TypeError, ValueError):
        return "0"
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= limit:
            s = f"{v / limit:.1f}".rstrip("0").rstrip(".")
            return s + suffix
    return f"{v:.0f}"


def pct(n) -> str:
    if n is None:
        return "—"
    return f"{float(n) * 100:.0f}%"


def short(s, n: int = 80) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def ts(t) -> str:
    return ("" if t is None else str(t))[:16].replace("T", " ")


def model_class(m) -> str:
    s = (m or "").lower()
    for key in ("fable", "mythos", "opus", "sonnet", "haiku", "grok"):
        if key in s:
            return "fable" if key == "mythos" else key
    return ""


def model_short(m) -> str:
    return (m or "").replace("claude-", "")


def source_badge(s) -> str:
    return "grok" if s == "grok" else ("sonnet" if s == "claude" else "")


def attr_json(obj) -> str:
    """JSON payload for a data-* attribute (chart/rings islands read it back)."""
    return html.escape(json.dumps(obj, default=str), quote=True)


# ---------------------------------------------------------------- transport

def send_html(handler, body: str, status: int = 200) -> None:
    raw = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


def card(title: str, body: str, sub: str = "") -> str:
    head = f"<h2>{e(title)}</h2>" if title else ""
    subline = f'<p class="muted" style="margin:-8px 0 14px">{e(sub)}</p>' if sub else ""
    return f'<div class="card">{head}{subline}{body}</div>'


def chart(el_id: str, kind: str, opt, height: int = 260) -> str:
    """A chart island: an empty sized div carrying its ECharts payload.

    web/hx-charts.js mounts these after each htmx swap using the SPA's own
    wrappers — server-rendered SVG would mean reimplementing tooltips, resize
    and the donut legend in Python, and still needing JS for the first two.
    """
    return (
        f'<div id="{e(el_id)}" data-chart="{e(kind)}" data-opt="{attr_json(opt)}" '
        f'style="height:{int(height)}px"></div>'
    )


def table(headers, rows_html: str) -> str:
    """headers: iterable of (label, is_numeric)."""
    head = "".join(
        '<th class="num">%s</th>' % e(label) if num else "<th>%s</th>" % e(label)
        for label, num in headers
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{rows_html}</tbody></table>"


# ---------------------------------------------------------------- filters
# Shared by the tab modules. Filters are plain links, so they are real,
# bookmarkable URLs instead of the SPA's location.hash parsing.


class Opts:
    def __init__(self, param, items):
        self.param, self.items = param, items


_RANGES = Opts("range", [("7d", "7d"), ("30d", "30d"), ("90d", "90d"), ("all", "All")])
_RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90, "all": None}
_KPI_ACCENTS = ("--accent", "--accent-2", "--warn", "--good")


def one(qs: dict, key: str, default: str) -> str:
    return qs.get(key, [default])[0]


def since(range_key: str):
    days = _RANGE_DAYS.get(range_key)
    if not days:
        return None
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def tabs(base: str, options: Opts, current: str) -> str:
    btns = "".join(
        '<a href="{base}?{k}={v}" hx-get="{base}?{k}={v}" hx-target="#app" '
        'hx-push-url="true" class="{cls}">{label}</a>'.format(
            base=base, k=options.param, v=e(key), label=e(label),
            cls="active" if key == current else "",
        )
        for key, label in options.items
    )
    return f'<div class="range-tabs" role="tablist">{btns}</div>'


def kpi(idx: int, label: str, value: str, full: str, cls: str = "", extra: str = "") -> str:
    """KPI tile. Cycles the theme's accent tokens so each tile glows differently."""
    accent = _KPI_ACCENTS[idx % len(_KPI_ACCENTS)]
    return (
        f'<div class="card kpi {cls}" style="--hud-accent:var({accent})">'
        f'<div class="label">{e(label)}</div>'
        f'<div class="value" title="{e(full)}">{e(value)}</div>{extra}</div>'
    )


# ---------------------------------------------------------------- shell

# ponytail: the SSE trigger re-fetches the whole tab, so a 30s scan rebuilds every
# chart on Overview (the SPA patches in place — see overview.js patchLiveData).
# Upgrade path if the flicker bites: give Overview a `?frag=kpis` fragment and
# point the scan trigger at #kpi-row only.
_SHELL_JS = """
import { mountHudBackground, addHudCorners } from '/web/hud-background.js';
import { disposeAll } from '/web/charts.js';

mountHudBackground({ variant: 'subtle' });

function decorate(root) {
  root.querySelectorAll('.card').forEach(c => addHudCorners(c, { accent: 'cyan', size: 12, inset: 0 }));
}
function setActive() {
  // "/" is Overview's other name, so it must match the /hx/overview link.
  const here = location.pathname === '/' ? '/hx/overview' : location.pathname;
  document.querySelectorAll('header.topbar nav a').forEach(a =>
    a.classList.toggle('active', a.getAttribute('href') === here));
}

// Mount whatever islands the fragment declares. Runs on the initial full page
// load too — that path has no swap event, so hooking only htmx:afterSwap would
// leave every chart blank until the first navigation.
async function hydrate(root) {
  if (root.querySelector('[data-chart]')) (await import('/web/hx-charts.js')).mountCharts(root);
  if (root.querySelector('#rings-data')) (await import('/web/hx-brain.js')).mountBrain(root);
  decorate(root);
}
hydrate(document);

// Charts and the Brain rings are JS islands living inside the swapped fragment.
// Tear them down BEFORE the swap or every navigation leaks an ECharts instance,
// a window resize listener and (on Brain) a requestAnimationFrame loop.
document.body.addEventListener('htmx:beforeSwap', e => {
  // Only a swap of the whole tab replaces the islands. Partial swaps inside the
  // page (the Knowledge strip's hx-trigger="load", a dismissed tip) must NOT
  // dispose them — doing so wipes the charts that just mounted.
  if (e.detail.target.id !== 'app') return;
  disposeAll();
  try { window.__hxTeardown?.(); } catch {}
  window.__hxTeardown = null;
});
document.body.addEventListener('htmx:afterSwap', e => {
  setActive();
  hydrate(e.detail.target);
});

// Browser back/forward restores #app from htmx's history cache. That path fires
// neither beforeSwap nor afterSwap, so without this the rings rAF loop survives
// the navigation and the restored charts are dead markup (cached SVG, no live
// ECharts instance behind it).
document.body.addEventListener('htmx:historyRestore', () => {
  disposeAll();
  try { window.__hxTeardown?.(); } catch {}
  window.__hxTeardown = null;
  setActive();
  hydrate(document.getElementById('app'));
});

// Live scan updates. Same 1500ms debounce and same skip-on-Brain rule as the SPA
// (app.js:196-201) — the rings graph must not reset while you are reading it.
let scanTimer;
try {
  new EventSource('/api/stream').onmessage = ev => {
    let evt; try { evt = JSON.parse(ev.data); } catch { return; }
    if (evt.type !== 'scan' || location.pathname.startsWith('/hx/brain')) return;
    clearTimeout(scanTimer);
    scanTimer = setTimeout(() => htmx.ajax('GET', location.pathname + location.search,
                                           { target: '#app', swap: 'innerHTML' }), 1500);
  };
} catch {}

window.addEventListener('keydown', ev => {
  if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 'b') {
    ev.preventDefault();
    document.body.classList.toggle('privacy-on');
  }
});
"""


def shell(active: str, body: str, plan: str = "api") -> str:
    nav = "".join(
        '<a href="/hx/{t}" hx-get="/hx/{t}" hx-target="#app" hx-push-url="true"{cls}>{t}</a>'.format(
            t=t, cls=' class="active"' if t == active else ""
        )
        for t in TABS
    )
    return f"""<!doctype html>
<html lang="en" class="hud-theme">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI-Dashboard — htmx</title>
<link rel="icon" type="image/svg+xml" href="/web/favicon.svg">
<link rel="stylesheet" href="/web/style.css">
<script src="/web/echarts.min.js"></script>
<script src="/web/htmx.min.js"></script>
</head>
<body>
<header class="topbar">
  <div class="topbar-inner">
    <div class="brand font-hud">AI-DASHBOARD<span class="muted"> /hx</span></div>
    <nav>{nav}</nav>
    <div class="spacer"></div>
    <span class="pill" id="plan-pill">{e(plan)}</span>
    <a class="pill muted" href="/spa" title="the original vanilla-JS dashboard">spa</a>
    <span class="pill muted" title="Cmd/Ctrl+B blurs sensitive text">⌘B blur</span>
  </div>
</header>
<main id="app" hx-history-elt>{body}</main>
<script type="module">{_SHELL_JS}</script>
</body>
</html>
"""


# ---------------------------------------------------------------- dispatch

def handle(handler, path: str, qs: dict, db_path: str, projects_dir: str, pricing: dict) -> None:
    """GET /hx and /hx/*. Full page on a plain request, fragment when htmx asks."""
    from . import hx_tabs

    # "/" is the same page as "/hx"; "/hx/prompts" -> "prompts".
    tab = (path[4:] if path.startswith("/hx") else "").strip("/") or "overview"
    sub = ""
    if "/" in tab:
        tab, sub = tab.split("/", 1)

    # Sub-tab fragments loaded lazily by the page itself (hx-trigger="load"),
    # so a slow disk scan never blocks the first paint.
    if tab in hx_tabs.FRAGMENTS:
        rate = usd_brl_rate()
        return send_html(handler, hx_tabs.FRAGMENTS[tab](
            dict(db=db_path, projects_dir=projects_dir, pricing=pricing, rate=rate, qs=qs, sub=sub)
        ))

    if tab not in TABS:
        return send_html(handler, card("Not found", f"<p>No such tab: <code>{e(tab)}</code></p>"), 404)

    from .pricing import get_plan
    rate = usd_brl_rate()
    ctx = dict(db=db_path, projects_dir=projects_dir, pricing=pricing, rate=rate, qs=qs, sub=sub)
    body = hx_tabs.render(tab, ctx)

    if handler.headers.get("HX-Request") == "true":
        return send_html(handler, body)
    return send_html(handler, shell(tab, body, plan=get_plan(db_path)))


def handle_post(handler, path: str, form: dict, db_path: str, projects_dir: str) -> None:
    """POST /hx/*. Bodies are form-encoded (htmx default), already parsed into `form`.

    Responses are HTML fragments swapped straight into the page — an empty body
    deletes the target, which is how a dismissed tip removes its own row.
    """
    from .memory import promote_memory, quarantine_memory
    from .pricing import set_plan
    from .tips import dismiss_tip

    if path == "/hx/tips/dismiss":
        dismiss_tip(db_path, form.get("key", ""))
        return send_html(handler, "")

    if path == "/hx/plan":
        plan = form.get("plan", "api")
        set_plan(db_path, plan)
        # Out-of-band swap keeps the topbar pill in sync without a full reload.
        return send_html(
            handler,
            f'<span id="plan-pill" class="pill" hx-swap-oob="true">{e(plan)}</span>'
            '<span style="color:var(--good)">Saved.</span>',
        )

    if path in ("/hx/brain/remove", "/hx/brain/keep"):
        remove = path.endswith("remove")
        fn = quarantine_memory if remove else promote_memory
        res = fn(projects_dir, form.get("slug", ""), form.get("file", ""))
        if not res.get("ok"):
            return send_html(handler, f'<span class="muted">failed: {e(res.get("error"))}</span>', 404)
        # remove swaps the whole row away; keep only replaces its own button.
        return send_html(handler, "" if remove else '<span class="muted">kept ✓</span>')

    return send_html(handler, card("Not found", f"<p><code>{e(path)}</code></p>"), 404)
