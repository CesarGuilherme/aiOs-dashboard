"""Scan Grok CLI sessions (~/.grok/sessions) into the dashboard SQLite schema.

Layout (see ~/.grok/docs/user-guide/17-sessions.md):
  ~/.grok/sessions/<url-encoded-cwd>/<session-id>/updates.jsonl
  + summary.json (model, cwd, title)

Token accounting (prefer real usage when present):
  - `turn_completed.usage` carries inputTokens / outputTokens / cachedReadTokens
    (and optional costUsdTicks). Map to dashboard columns with uncached input
    = inputTokens - cachedReadTokens so cost_for does not double-bill cache.
  - Fallback (older turns without usage): context growth from
    params._meta.totalTokens as input; chars//4 as output; cache_* = 0.
  - cache_create_* is always 0 (Grok does not emit cache-write buckets).
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import unquote

from .db import connect
from .naming import _encode_slug
from .scanner import INSERT_MSG, INSERT_TOOL

SOURCE = "grok"

# rawInput field priority for a human-readable target
_TARGET_KEYS = (
    "file_path", "path", "target_directory", "target_file", "command",
    "pattern", "query", "url", "prompt", "image",
)

_TOOL_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_KIND_TO_TOOL = {
    "read": "read_file",
    "edit": "search_replace",
    "execute": "run_terminal_command",
    "search": "search_tool",
}


def _canonical_tool_name(title: Any, kind: Any = None) -> str:
    """Prefer snake_case tool ids; never store human titles like `Read /path`."""
    t = (title or "").strip() if isinstance(title, str) else ""
    if t and len(t) <= 80 and _TOOL_IDENT.match(t):
        return t
    k = (kind or "").strip().lower() if isinstance(kind, str) else ""
    if k in _KIND_TO_TOOL:
        return _KIND_TO_TOOL[k]
    first = t.split()[0] if t else ""
    if first and len(first) <= 80 and _TOOL_IDENT.match(first):
        return first
    return "unknown"


def _as_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _tokens_from_usage(usage: dict) -> Tuple[int, int, int]:
    """Map Grok turn_completed.usage → (input, output, cache_read).

    Grok reports full prompt size in inputTokens with cache hits in
    cachedReadTokens (totalTokens ≈ input + output). Store uncached input
    separately so pricing matches Anthropic-style cost_for.
    """
    full_in = _as_int(usage.get("inputTokens"))
    cache_read = max(0, _as_int(usage.get("cachedReadTokens")))
    output = max(0, _as_int(usage.get("outputTokens")))
    if cache_read and full_in >= cache_read:
        uncached = full_in - cache_read
    else:
        uncached = max(0, full_in)
    return uncached, output, cache_read


def _iso_from_ts(ts: Any) -> str:
    """Grok timestamps are unix seconds (sometimes ms)."""
    if ts is None:
        return datetime.now(timezone.utc).isoformat()
    try:
        n = float(ts)
    except (TypeError, ValueError):
        return str(ts)
    if n > 1e12:  # ms
        n /= 1000.0
    return datetime.fromtimestamp(n, tz=timezone.utc).isoformat()


def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if content.get("type") == "text":
            return content.get("text") or ""
        if "text" in content:
            return str(content.get("text") or "")
        return ""
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text") or "")
                elif b.get("type") == "content" and isinstance(b.get("content"), dict):
                    parts.append(_text_from_content(b["content"]))
                elif "text" in b:
                    parts.append(str(b.get("text") or ""))
        return "".join(parts)
    return ""


def _tool_target(raw_input: Any) -> Optional[str]:
    if not isinstance(raw_input, dict):
        return None
    for k in _TARGET_KEYS:
        v = raw_input.get(k)
        if isinstance(v, str) and v.strip():
            return v[:500]
    # first string value fallback
    for v in raw_input.values():
        if isinstance(v, str) and v.strip() and len(v) < 500:
            return v[:500]
    return None


def _load_summary(session_dir: Path) -> dict:
    p = session_dir / "summary.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _project_slug_for_session(session_dir: Path, summary: dict) -> Tuple[str, Optional[str]]:
    cwd = (summary.get("info") or {}).get("cwd") or summary.get("cwd")
    if not cwd:
        # parent dir name is URL-encoded cwd
        try:
            cwd = unquote(session_dir.parent.name)
        except Exception:
            cwd = session_dir.parent.name
    if cwd and (cwd.startswith("/") or (len(cwd) > 2 and cwd[1] == ":")):
        slug = _encode_slug(cwd)
    else:
        # fixture / bare name
        slug = _encode_slug(cwd) if cwd else session_dir.parent.name
    return slug, cwd if isinstance(cwd, str) else None


def parse_updates(
    path: Path,
    session_id: str,
    project_slug: str,
    cwd: Optional[str],
    default_model: Optional[str],
) -> Tuple[List[dict], List[dict]]:
    """Parse a full updates.jsonl into message rows + tool_call rows."""
    # promptId -> turn state
    turns: Dict[str, dict] = {}
    order: List[str] = []  # promptIds in first-seen order
    last_total = 0
    orphan_tools: List[dict] = []  # tools before any promptId
    # Grok user_message_chunk almost never carries promptId; usage lives on the
    # next agent/turn_completed event that does. Hold text until then.
    pending_user: List[Tuple[str, str]] = []

    def turn(pid: str) -> dict:
        if pid not in turns:
            turns[pid] = {
                "user_text": [],
                "assistant_text": [],
                "thought_text": [],
                "tools": [],  # {id, name, target, result_chars, is_error, ts}
                "model": default_model,
                "max_tokens": 0,
                "stop_reason": None,
                "user_ts": None,
                "assistant_ts": None,
                "agent_id": None,
                "is_sidechain": 0,
                "usage": None,  # turn_completed.usage dict when present
            }
            order.append(pid)
        return turns[pid]

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            params = rec.get("params") or {}
            update = params.get("update") or {}
            meta = params.get("_meta") or {}
            umeta = update.get("_meta") or {}
            su = update.get("sessionUpdate")
            ts = _iso_from_ts(rec.get("timestamp") or meta.get("agentTimestampMs"))
            pid = meta.get("promptId") or update.get("prompt_id")
            model = umeta.get("modelId") or default_model
            total = meta.get("totalTokens")
            if pid and pending_user:
                tpend = turn(pid)
                for text, uts in pending_user:
                    if text:
                        tpend["user_text"].append(text)
                    tpend["user_ts"] = tpend["user_ts"] or uts
                pending_user.clear()
            if total is not None and pid:
                t = turn(pid)
                try:
                    t["max_tokens"] = max(t["max_tokens"], int(total))
                except (TypeError, ValueError):
                    pass
            if model and pid:
                turn(pid)["model"] = model

            if su == "user_message_chunk":
                text = _text_from_content(update.get("content"))
                if pid:
                    t = turn(pid)
                    t["user_text"].append(text)
                    t["user_ts"] = t["user_ts"] or ts
                    if model:
                        t["model"] = model
                else:
                    pending_user.append((text, ts))
            elif su == "agent_message_chunk":
                if not pid:
                    continue
                t = turn(pid)
                t["assistant_text"].append(_text_from_content(update.get("content")))
                t["assistant_ts"] = ts
            elif su == "agent_thought_chunk":
                if not pid:
                    continue
                t = turn(pid)
                t["thought_text"].append(_text_from_content(update.get("content")))
                t["assistant_ts"] = t["assistant_ts"] or ts
            elif su == "tool_call":
                tool = {
                    "id": update.get("toolCallId") or meta.get("eventId"),
                    "name": _canonical_tool_name(update.get("title"), update.get("kind")),
                    "target": _tool_target(update.get("rawInput")),
                    "result_chars": 0,
                    "is_error": 0,
                    "ts": ts,
                }
                if pid:
                    turn(pid)["tools"].append(tool)
                else:
                    orphan_tools.append(tool)
            elif su == "tool_call_update":
                tid = update.get("toolCallId")
                body = _text_from_content(update.get("content"))
                status = (update.get("status") or "").lower()
                is_err = 1 if status in ("failed", "error") else 0
                target = _tool_target(update.get("rawInput"))
                canon = _canonical_tool_name(update.get("title"), update.get("kind"))
                # find tool in turns
                found = False
                for t in turns.values():
                    for tool in t["tools"]:
                        if tool["id"] == tid:
                            if body:
                                tool["result_chars"] = max(tool["result_chars"], len(body))
                            if target and not tool["target"]:
                                tool["target"] = target
                            if is_err:
                                tool["is_error"] = 1
                            if tool["name"] in ("unknown", "Other") and canon != "unknown":
                                tool["name"] = canon
                            found = True
                            break
                    if found:
                        break
                if not found and tid:
                    orphan_tools.append({
                        "id": tid,
                        "name": canon,
                        "target": target,
                        "result_chars": len(body),
                        "is_error": is_err,
                        "ts": ts,
                    })
            elif su == "turn_completed":
                if pid:
                    t = turn(pid)
                    t["stop_reason"] = update.get("stop_reason") or "end_turn"
                    usage = update.get("usage")
                    if isinstance(usage, dict) and usage:
                        t["usage"] = usage
                        # Prefer model id from modelUsage when stream omitted modelId
                        mu = usage.get("modelUsage")
                        if isinstance(mu, dict) and mu and not t.get("model"):
                            t["model"] = next(iter(mu.keys()), None)
            elif su == "subagent_spawned":
                if pid:
                    turn(pid)["is_sidechain"] = 1
                    turn(pid)["agent_id"] = (
                        update.get("agentId") or update.get("sessionId") or meta.get("eventId")
                    )

    if pending_user:
        pid_flush = order[-1] if order else "_pending"
        tpend = turn(pid_flush)
        for text, uts in pending_user:
            if text:
                tpend["user_text"].append(text)
            tpend["user_ts"] = tpend["user_ts"] or uts
        pending_user.clear()

    # Attach orphans to last turn if any
    if orphan_tools and order:
        turns[order[-1]]["tools"].extend(orphan_tools)

    messages: List[dict] = []
    tools_out: List[dict] = []
    prev_max = 0

    for pid in order:
        t = turns[pid]
        user_text = "".join(t["user_text"]).strip()
        asst_text = "".join(t["assistant_text"]).strip()
        thought = "".join(t["thought_text"])
        # skip empty synthetic noise turns with no content and no tools
        if not user_text and not asst_text and not t["tools"]:
            continue

        user_uuid = f"grok:{session_id}:{pid}:user"
        asst_uuid = f"grok:{session_id}:{pid}:assistant"
        user_ts = t["user_ts"] or t["assistant_ts"] or datetime.now(timezone.utc).isoformat()
        asst_ts = t["assistant_ts"] or user_ts

        max_tok = int(t["max_tokens"] or 0)
        usage = t.get("usage")
        if isinstance(usage, dict) and usage:
            input_tokens, output_tokens, cache_read_tokens = _tokens_from_usage(usage)
            # keep context watermark for any later turns that lack usage
            tot = _as_int(usage.get("totalTokens"))
            if tot:
                prev_max = max(prev_max, tot)
            elif max_tok:
                prev_max = max(prev_max, max_tok)
        else:
            # context growth as input estimate; chars//4 for output; no cache
            input_tokens = max(0, max_tok - prev_max) if max_tok else 0
            if max_tok:
                prev_max = max_tok
            out_chars = len(asst_text) + len(thought) // 2
            output_tokens = max(0, out_chars // 4)
            cache_read_tokens = 0

        if user_text or t["tools"] or asst_text:
            messages.append({
                "uuid": user_uuid,
                "parent_uuid": None,
                "session_id": session_id,
                "project_slug": project_slug,
                "cwd": cwd,
                "git_branch": None,
                "cc_version": None,
                "entrypoint": "grok",
                "type": "user",
                "is_sidechain": t["is_sidechain"],
                "agent_id": t["agent_id"],
                "timestamp": user_ts,
                "model": None,
                "stop_reason": None,
                "prompt_id": pid,
                "message_id": f"{pid}:user",
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_create_5m_tokens": 0,
                "cache_create_1h_tokens": 0,
                "prompt_text": user_text or None,
                "prompt_chars": len(user_text) if user_text else None,
                "tool_calls_json": None,
                "source": SOURCE,
            })

        tool_summary = []
        for tool in t["tools"]:
            tool_summary.append({"name": tool["name"], "target": tool["target"]})
            tools_out.append({
                "message_uuid": asst_uuid,
                "session_id": session_id,
                "project_slug": project_slug,
                "tool_name": tool["name"],
                "target": tool["target"],
                "tool_use_id": tool["id"],
                "result_tokens": None,
                "is_error": 0,
                "timestamp": tool["ts"] or asst_ts,
                "source": SOURCE,
            })
            # synthetic result row for ROI joins
            if tool["result_chars"] or tool["is_error"]:
                tools_out.append({
                    "message_uuid": asst_uuid,
                    "session_id": session_id,
                    "project_slug": project_slug,
                    "tool_name": "_tool_result",
                    "target": tool["id"],
                    "tool_use_id": tool["id"],
                    "result_tokens": tool["result_chars"] // 4,
                    "is_error": tool["is_error"],
                    "timestamp": tool["ts"] or asst_ts,
                    "source": SOURCE,
                })

        messages.append({
            "uuid": asst_uuid,
            "parent_uuid": user_uuid if (user_text or t["tools"]) else None,
            "session_id": session_id,
            "project_slug": project_slug,
            "cwd": cwd,
            "git_branch": None,
            "cc_version": None,
            "entrypoint": "grok",
            "type": "assistant",
            "is_sidechain": t["is_sidechain"],
            "agent_id": t["agent_id"],
            "timestamp": asst_ts,
            "model": t["model"] or default_model,
            "stop_reason": t["stop_reason"],
            "prompt_id": pid,
            "message_id": f"{pid}:assistant",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_create_5m_tokens": 0,
            "cache_create_1h_tokens": 0,
            "prompt_text": None,
            "prompt_chars": None,
            "tool_calls_json": json.dumps(tool_summary) if tool_summary else None,
            "source": SOURCE,
        })

    return messages, tools_out


def scan_grok_session(session_dir: Path, conn) -> dict:
    """Full reparse of one session dir into DB. Returns counts."""
    updates = session_dir / "updates.jsonl"
    if not updates.is_file():
        return {"messages": 0, "tools": 0, "files": 0}
    try:
        stat = updates.stat()
    except OSError:
        return {"messages": 0, "tools": 0, "files": 0}

    path_s = str(updates)
    row = conn.execute(
        "SELECT mtime, bytes_read FROM files WHERE path=?", (path_s,)
    ).fetchone()
    if row and row["mtime"] == stat.st_mtime and row["bytes_read"] == stat.st_size:
        return {"messages": 0, "tools": 0, "files": 0}

    session_id = session_dir.name
    summary = _load_summary(session_dir)
    slug, cwd = _project_slug_for_session(session_dir, summary)
    model = summary.get("current_model_id") or (summary.get("info") or {}).get("model")

    # Drop prior grok rows for this session so full reparse is idempotent
    old = [r[0] for r in conn.execute(
        "SELECT uuid FROM messages WHERE session_id=? AND source=?",
        (session_id, SOURCE),
    )]
    if old:
        ph = ",".join("?" * len(old))
        conn.execute(f"DELETE FROM tool_calls WHERE message_uuid IN ({ph})", old)
        conn.execute(
            "DELETE FROM messages WHERE session_id=? AND source=?",
            (session_id, SOURCE),
        )

    msgs, tools = parse_updates(updates, session_id, slug, cwd, model)
    for m in msgs:
        conn.execute(INSERT_MSG, m)
    for t in tools:
        conn.execute(INSERT_TOOL, t)

    conn.execute(
        "INSERT OR REPLACE INTO files (path, mtime, bytes_read, scanned_at, source) VALUES (?, ?, ?, ?, ?)",
        (path_s, stat.st_mtime, stat.st_size, time.time(), SOURCE),
    )
    return {"messages": len(msgs), "tools": len(tools), "files": 1}


def scan_grok_dir(sessions_root: Union[str, Path], db_path: Union[str, Path]) -> dict:
    root = Path(sessions_root)
    totals = {"messages": 0, "tools": 0, "files": 0}
    if not root.is_dir():
        return totals
    with connect(db_path) as conn:
        # sessions are .../<encoded-cwd>/<session-id>/updates.jsonl
        for updates in root.rglob("updates.jsonl"):
            session_dir = updates.parent
            # skip non-session (e.g. nested junk)
            if not session_dir.name or session_dir.name.startswith("."):
                continue
            try:
                sub = scan_grok_session(session_dir, conn)
            except Exception:
                continue
            totals["messages"] += sub["messages"]
            totals["tools"] += sub["tools"]
            totals["files"] += sub["files"]
            conn.commit()
    return totals
