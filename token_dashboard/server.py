"""HTTP server: static frontend + JSON endpoints + SSE diff stream."""
from __future__ import annotations

import http.server
import json
import mimetypes
import queue
import threading
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .db import (
    overview_totals, expensive_prompts, project_summary,
    tool_token_breakdown, recent_sessions, session_turns,
    daily_token_breakdown, model_breakdown, skill_breakdown,
    prompts_as_csv, projects_as_csv,
)
from .pricing import load_pricing, cost_for, get_plan, set_plan
from .tips import all_tips, dismiss_tip
from .memory import get_brain, quarantine_memory, promote_memory
from .fx import usd_brl_rate
from .scanner import scan_all
from .skills import cached_catalog
from .workspace import scan_workspace, workspace_roots, allowed_open_path, open_on_device


WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
PRICING_JSON = Path(__file__).resolve().parent.parent / "pricing.json"

EVENTS: "queue.Queue[dict]" = queue.Queue()

MAX_POST_BYTES = 1_000_000  # 1 MB — we only accept tiny JSON bodies (plan, tip key)
MAX_LIMIT = 1000

# Reload pricing.json when the file changes (Settings says "reload the page after editing").
_pricing_mtime: float | None = None
_pricing_data: dict | None = None


def _current_pricing() -> dict:
    global _pricing_mtime, _pricing_data
    try:
        mtime = PRICING_JSON.stat().st_mtime
    except OSError:
        mtime = None
    if _pricing_data is None or mtime != _pricing_mtime:
        _pricing_data = load_pricing(PRICING_JSON)
        _pricing_mtime = mtime
    return _pricing_data


def _send_json(handler, obj, status: int = 200) -> None:
    body = json.dumps(obj, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _send_error(handler, status: int, msg: str) -> None:
    _send_json(handler, {"error": msg}, status=status)


def _clamp_limit(raw, default: int) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(v, MAX_LIMIT))


def _serve_static(handler, rel: str) -> None:
    rel = rel.lstrip("/")
    p = (WEB_ROOT / rel).resolve()
    if not str(p).startswith(str(WEB_ROOT.resolve())) or not p.is_file():
        handler.send_response(404)
        handler.end_headers()
        return
    body = p.read_bytes()
    ctype, _ = mimetypes.guess_type(str(p))
    handler.send_response(200)
    handler.send_header("Content-Type", ctype or "application/octet-stream")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    handler.wfile.write(body)


def build_handler(db_path: str, projects_dir: str, grok_sessions_dir: str | None = None):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_HEAD(self):
            return self.do_GET()

        def do_GET(self):
            url = urlparse(self.path)
            qs = parse_qs(url.query or "")
            path = url.path
            since = qs.get("since", [None])[0]
            until = qs.get("until", [None])[0]
            source = qs.get("source", ["all"])[0]
            pricing = _current_pricing()
            if path in ("/", "/index.html"):
                return _serve_static(self, "index.html")
            if path.startswith("/web/"):
                return _serve_static(self, path[5:])
            if path == "/api/overview":
                totals = overview_totals(db_path, since, until, source=source)
                cost_usd = 0.0
                for m in model_breakdown(db_path, since, until, source=source):
                    c = cost_for(m["model"], m, pricing)
                    if c["usd"] is not None:
                        cost_usd += c["usd"]
                totals["cost_usd"] = round(cost_usd, 4)
                rate = usd_brl_rate()
                totals["usd_brl_rate"] = rate
                totals["cost_brl"] = round(cost_usd * rate, 4)
                return _send_json(self, totals)
            if path == "/api/prompts":
                limit = _clamp_limit(qs.get("limit", ["50"])[0], 50)
                sort = qs.get("sort", ["tokens"])[0]
                rows = expensive_prompts(db_path, limit=limit, sort=sort)
                for r in rows:
                    c = cost_for(r["model"], {
                        "input_tokens": 0, "output_tokens": 0,
                        "cache_read_tokens": r["cache_read_tokens"],
                        "cache_create_5m_tokens": 0, "cache_create_1h_tokens": 0,
                    }, pricing)
                    r["estimated_cost_usd"] = c["usd"]
                return _send_json(self, rows)
            if path == "/api/prompts.csv":
                limit = _clamp_limit(qs.get("limit", ["50"])[0], 50)
                sort = qs.get("sort", ["tokens"])[0]
                rows = expensive_prompts(db_path, limit=limit, sort=sort)
                for r in rows:
                    c = cost_for(r["model"], {
                        "input_tokens": 0, "output_tokens": 0,
                        "cache_read_tokens": r["cache_read_tokens"],
                        "cache_create_5m_tokens": 0, "cache_create_1h_tokens": 0,
                    }, pricing)
                    r["estimated_cost_usd"] = c["usd"]
                csv_text = prompts_as_csv(rows)
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="prompts.csv"')
                self.send_header("Content-Length", str(len(csv_text.encode("utf-8"))))
                self.end_headers()
                self.wfile.write(csv_text.encode("utf-8"))
                return
            if path == "/api/projects.csv":
                rows = project_summary(db_path, since, until)
                csv_text = projects_as_csv(rows)
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="projects.csv"')
                self.send_header("Content-Length", str(len(csv_text.encode("utf-8"))))
                self.end_headers()
                self.wfile.write(csv_text.encode("utf-8"))
                return
            if path == "/api/projects":
                return _send_json(self, project_summary(db_path, since, until, source=source))
            if path == "/api/tools":
                return _send_json(self, tool_token_breakdown(db_path, since, until, source=source))
            if path == "/api/sessions":
                return _send_json(self, recent_sessions(
                    db_path, limit=_clamp_limit(qs.get("limit", ["20"])[0], 20),
                    since=since, until=until, source=source,
                ))
            if path == "/api/daily":
                return _send_json(self, daily_token_breakdown(db_path, since, until, source=source))
            if path == "/api/skills":
                rows = skill_breakdown(db_path, since, until)
                catalog = cached_catalog()
                for r in rows:
                    info = catalog.get(r["skill"])
                    r["tokens_per_call"] = info["tokens"] if info else None
                return _send_json(self, rows)
            if path == "/api/by-model":
                rows = model_breakdown(db_path, since, until, source=source)
                for r in rows:
                    c = cost_for(r["model"], r, pricing)
                    r["cost_usd"] = c["usd"]
                    r["cost_estimated"] = c["estimated"]
                return _send_json(self, rows)
            if path.startswith("/api/sessions/"):
                sid = path.rsplit("/", 1)[1]
                return _send_json(self, session_turns(db_path, sid))
            if path == "/api/tips":
                return _send_json(self, all_tips(db_path, projects_dir))
            if path == "/api/brain":
                return _send_json(self, get_brain(projects_dir, db_path, pricing))
            if path == "/api/workspace":
                return _send_json(self, scan_workspace(Path.home() / ".claude", Path.home() / ".grok"))
            if path == "/api/plan":
                rate = usd_brl_rate()
                return _send_json(self, {
                    "plan": get_plan(db_path),
                    "pricing": pricing,
                    "usd_brl_rate": rate,
                })
            if path == "/api/scan":
                n = scan_all(db_path, projects_dir=projects_dir, grok_sessions_dir=grok_sessions_dir)
                return _send_json(self, n)
            if path == "/api/stream":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                while True:
                    try:
                        evt = EVENTS.get(timeout=15)
                        chunk = f"data: {json.dumps(evt, default=str)}\n\n".encode()
                    except queue.Empty:
                        chunk = b": ping\n\n"
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            url = urlparse(self.path)
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return _send_error(self, 400, "invalid Content-Length")
            if length < 0 or length > MAX_POST_BYTES:
                return _send_error(self, 413, f"body too large (max {MAX_POST_BYTES} bytes)")
            try:
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except json.JSONDecodeError:
                return _send_error(self, 400, "invalid JSON")
            if not isinstance(body, dict):
                return _send_error(self, 400, "body must be a JSON object")
            if url.path == "/api/plan":
                set_plan(db_path, body.get("plan", "api"))
                return _send_json(self, {"ok": True})
            if url.path == "/api/tips/dismiss":
                dismiss_tip(db_path, body.get("key", ""))
                return _send_json(self, {"ok": True})
            if url.path == "/api/brain/remove":
                res = quarantine_memory(projects_dir, body.get("slug", ""), body.get("file", ""))
                return _send_json(self, res, status=200 if res.get("ok") else 404)
            if url.path == "/api/brain/keep":
                res = promote_memory(projects_dir, body.get("slug", ""), body.get("file", ""))
                return _send_json(self, res, status=200 if res.get("ok") else 404)
            if url.path == "/api/open":
                if "application/json" not in (self.headers.get("Content-Type") or ""):
                    self.send_response(403); self.end_headers(); return
                target = str(body.get("path", ""))
                claude_dir = Path.home() / ".claude"
                if not allowed_open_path(target, workspace_roots(), claude_dir):
                    self.send_response(403)
                    self.end_headers()
                    return
                if not Path(target).exists():
                    self.send_response(404)
                    self.end_headers()
                    return
                open_on_device(target)
                self.send_response(204)
                self.end_headers()
                return
            self.send_response(404)
            self.end_headers()

    return H


def _scan_loop(db_path: str, projects_dir: str, grok_sessions_dir: str | None = None, interval: float = 30.0):
    while True:
        try:
            n = scan_all(db_path, projects_dir=projects_dir, grok_sessions_dir=grok_sessions_dir)
            if n["messages"] > 0:
                EVENTS.put({"type": "scan", "n": n, "ts": time.time()})
        except Exception as e:
            EVENTS.put({"type": "error", "message": str(e)})
        time.sleep(interval)


def run(host: str, port: int, db_path: str, projects_dir: str, grok_sessions_dir: str | None = None):
    threading.Thread(
        target=_scan_loop,
        args=(db_path, projects_dir, grok_sessions_dir),
        daemon=True,
    ).start()
    H = build_handler(db_path, projects_dir, grok_sessions_dir=grok_sessions_dir)
    httpd = http.server.ThreadingHTTPServer((host, port), H)
    httpd.serve_forever()
