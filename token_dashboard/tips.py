"""Rule-based tips engine — produces actionable suggestions from SQLite."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import List, Optional

from .db import connect


def _iso_days_ago(today_iso: str, n: int) -> str:
    d = datetime.fromisoformat(today_iso.replace("Z", ""))
    return (d - timedelta(days=n)).isoformat()


def _key(category: str, scope: str) -> str:
    return f"{category}:{scope}"


def _is_dismissed(db_path, key: str) -> bool:
    with connect(db_path) as c:
        r = c.execute("SELECT dismissed_at FROM dismissed_tips WHERE tip_key=?", (key,)).fetchone()
    if not r:
        return False
    return (time.time() - r["dismissed_at"]) < 14 * 86400


def dismiss_tip(db_path, key: str) -> None:
    with connect(db_path) as c:
        c.execute(
            "INSERT OR REPLACE INTO dismissed_tips (tip_key, dismissed_at) VALUES (?, ?)",
            (key, time.time()),
        )
        c.commit()


def cache_discipline_tips(db_path, today_iso: Optional[str] = None) -> List[dict]:
    today_iso = today_iso or datetime.utcnow().isoformat()
    since = _iso_days_ago(today_iso, 7)
    sql = """
      SELECT project_slug,
             SUM(cache_read_tokens) AS cr,
             SUM(input_tokens + cache_create_5m_tokens + cache_create_1h_tokens) AS rebuild
        FROM messages
       WHERE type='assistant' AND timestamp >= ?
       GROUP BY project_slug
       HAVING (cr + rebuild) > 100000
    """
    out = []
    with connect(db_path) as c:
        for row in c.execute(sql, (since,)):
            total = (row["cr"] or 0) + (row["rebuild"] or 0)
            hit = (row["cr"] or 0) / total if total else 0
            if hit < 0.40:
                key = _key("cache", row["project_slug"])
                if _is_dismissed(db_path, key):
                    continue
                out.append({
                    "key": key,
                    "category": "cache",
                    "title": f"Low cache hit rate in {row['project_slug']}",
                    "body": f"Cache hit rate is {hit*100:.0f}% over the last 7 days. Sessions that restart context frequently rebuild cache. Consider longer-lived sessions or fewer context resets.",
                    "scope": row["project_slug"],
                })
    return out


def knowledge_tips(db_path, projects_dir: str) -> List[dict]:
    """Files Claude keeps re-reading without a memory — delegated to the Brain
    page's engine (memory.knowledge_suggestions) so both pages share one metric,
    one coverage check, and one dismissal key. Function-level import: memory.py
    imports from this module at top level.
    """
    from .memory import get_coverage, get_labels, knowledge_suggestions
    covered = get_coverage(projects_dir)
    labels = get_labels(db_path, list(covered))
    return [{
        "key": s["key"], "category": "memory",
        "project": s["project"],
        "title": s["title"], "body": s["body"],
        "prompt": s["prompt"], "scope": s["key"],
    } for s in knowledge_suggestions(db_path, covered, labels)]


def failing_command_tips(db_path, projects_dir: str, today_iso: Optional[str] = None) -> List[dict]:
    """Tool calls that keep erroring — pure token waste, nothing surfaced it before.

    is_error lives on _tool_result rows; the join via tool_use_id attributes
    each failure back to the command/file that produced it.
    """
    today_iso = today_iso or datetime.utcnow().isoformat()
    since = _iso_days_ago(today_iso, 30)
    sql = """
      SELECT u.project_slug, u.tool_name, u.target,
             COUNT(*) AS n, COUNT(DISTINCT u.session_id) AS sessions
        FROM tool_calls r
        JOIN tool_calls u ON u.tool_use_id = r.tool_use_id
                         AND u.tool_name != '_tool_result'
       WHERE r.tool_name = '_tool_result' AND r.is_error = 1
         AND r.timestamp >= ? AND u.target IS NOT NULL AND u.target != ''
       GROUP BY u.project_slug, u.tool_name, u.target
      HAVING n >= 5 AND sessions >= 2
       ORDER BY n DESC LIMIT 10
    """
    with connect(db_path) as c:
        rows = [dict(r) for r in c.execute(sql, (since,))]
    if not rows:
        return []
    from .memory import get_labels
    labels = get_labels(db_path, sorted({r["project_slug"] for r in rows}))
    out = []
    for row in rows:
        target = row["target"]
        short = target if len(target) <= 60 else target[:59] + "…"
        key = _key("repeat-fail", f"{row['project_slug']}:{row['tool_name']}:{target[:120]}")
        if _is_dismissed(db_path, key):
            continue
        label = labels.get(row["project_slug"], row["project_slug"])
        out.append({
            "key": key, "category": "repeat-fail",
            "project": label,
            "title": f"{row['tool_name']} `{short}` failed {row['n']}× across {row['sessions']} sessions",
            "body": f"This {row['tool_name']} call errored {row['n']} times in {label} over the last 30 days "
                    "— tokens spent producing nothing. Fix the root cause once, or memorize the workaround.",
            "prompt": f"This keeps failing across sessions: {row['tool_name']} `{target}`. Investigate why, "
                      "then either fix the root cause or save a memory documenting the failure and the "
                      "working alternative so future sessions don't retry it.",
            "scope": key,
        })
    return out


def memory_hygiene_tips(db_path, projects_dir: str, today_iso: Optional[str] = None) -> List[dict]:
    """Keep the memory system itself token-efficient.

    Two rules: a LEARNINGS.md journal past the distillation threshold, and an
    active project (≥5 sessions/30d) with no memory at all.
    """
    from pathlib import Path
    from .memory import LEARNING_HEAD_RE, SPECIAL_FILES, get_labels

    today_iso = today_iso or datetime.utcnow().isoformat()
    since = _iso_days_ago(today_iso, 30)
    root = Path(projects_dir)
    out: List[dict] = []

    with connect(db_path) as c:
        active = {r["project_slug"]: r["s"] for r in c.execute(
            """SELECT project_slug, COUNT(DISTINCT session_id) AS s
                 FROM messages WHERE timestamp >= ?
                GROUP BY project_slug HAVING s >= 5""", (since,))}
    labels = get_labels(db_path, sorted(set(active) | {d.parent.name for d in root.glob("*/memory")}))

    for mem_dir in (root.glob("*/memory") if root.is_dir() else []):
        slug = mem_dir.parent.name
        learnings = mem_dir / "LEARNINGS.md"
        if not learnings.is_file():
            continue
        try:
            entries = sum(1 for ln in learnings.read_text(encoding="utf-8", errors="replace").splitlines()
                          if LEARNING_HEAD_RE.match(ln))
        except OSError:
            continue
        if entries <= 30:
            continue
        key = _key("memory-distill", slug)
        if _is_dismissed(db_path, key):
            continue
        label = labels.get(slug, slug)
        out.append({
            "key": key, "category": "memory",
            "project": label,
            "title": f"LEARNINGS.md has {entries} entries — time to distill",
            "body": f"The {label} learnings journal passed the ~30-entry threshold. Old entries loaded "
                    "every session are a growing token sink; distilled atomic memories load on demand.",
            "prompt": "LEARNINGS.md in this project's memory dir passed 30 entries. Distill the oldest "
                      "entries into atomic memory files (one durable fact each, indexed in MEMORY.md) "
                      "and trim the journal, per the /learn convention.",
            "scope": slug,
        })

    for slug, sessions in active.items():
        mem_dir = root / slug / "memory"
        has_memories = mem_dir.is_dir() and any(
            f.name not in SPECIAL_FILES for f in mem_dir.glob("*.md"))
        if has_memories:
            continue
        key = _key("memory-missing", slug)
        if _is_dismissed(db_path, key):
            continue
        label = labels.get(slug, slug)
        out.append({
            "key": key, "category": "memory",
            "project": label,
            "title": f"{label} has {sessions} sessions this month but no memory",
            "body": "Every session in this project starts from zero — decisions and gotchas get "
                    "re-derived each time. One session spent saving memories pays back immediately.",
            "prompt": "This project has no saved memories yet. Save memory files for its durable, "
                      "non-derivable facts (key decisions, gotchas, contracts), index them in "
                      "MEMORY.md, and start a LEARNINGS.md journal per the /learn convention.",
            "scope": slug,
        })
    return out


def repeated_target_tips(db_path, today_iso: Optional[str] = None) -> List[dict]:
    today_iso = today_iso or datetime.utcnow().isoformat()
    since = _iso_days_ago(today_iso, 7)
    out = []
    with connect(db_path) as c:
        # Repeated Bash commands (high repetition threshold)
        for row in c.execute("""
          SELECT target, COUNT(*) AS n
            FROM tool_calls
           WHERE tool_name='Bash' AND timestamp >= ?
           GROUP BY target HAVING n > 15
           ORDER BY n DESC LIMIT 10
        """, (since,)):
            key = _key("repeat-bash", row["target"] or "?")
            if _is_dismissed(db_path, key):
                continue
            out.append({
                "key": key, "category": "repeat-bash",
                "title": f"`{row['target']}` ran {row['n']} times",
                "body": f"This bash command ran {row['n']} times in the past 7 days. Consider a watch flag or shell alias.",
                "scope": row["target"],
            })

        # Repeated file targets (Claude + Grok tools) — common waste pattern
        from .tool_aliases import FILE_TOOLS, sql_in
        for row in c.execute(f"""
          SELECT target, COUNT(*) AS n
            FROM tool_calls
           WHERE tool_name {sql_in(FILE_TOOLS)}
             AND timestamp >= ?
           GROUP BY target HAVING n > 10
           ORDER BY n DESC LIMIT 10
        """, (since,)):
            key = _key("repeat-file", row["target"] or "?")
            if _is_dismissed(db_path, key):
                continue
            out.append({
                "key": key, "category": "repeat-file",
                "title": f"`{row['target']}` accessed {row['n']} times",
                "body": f"This file/pattern was touched {row['n']} times in the past 7 days. Consider a Second Brain memory or CLAUDE.md/AGENTS.md summary.",
                "scope": row["target"],
            })
    return out


def right_size_tips(db_path, today_iso: Optional[str] = None) -> List[dict]:
    today_iso = today_iso or datetime.utcnow().isoformat()
    since = _iso_days_ago(today_iso, 7)
    sql = """
      SELECT COUNT(*) AS n,
             SUM(input_tokens+cache_create_5m_tokens+cache_create_1h_tokens) AS in_tok,
             SUM(output_tokens) AS out_tok
        FROM messages
       WHERE type='assistant' AND model LIKE '%opus%'
         AND output_tokens < 500 AND is_sidechain = 0
         AND timestamp >= ?
    """
    with connect(db_path) as c:
        row = c.execute(sql, (since,)).fetchone()
    if not row or (row["n"] or 0) < 10:
        return []
    api_opus   = ((row["in_tok"] or 0) * 15 + (row["out_tok"] or 0) * 75) / 1_000_000
    api_sonnet = ((row["in_tok"] or 0) *  3 + (row["out_tok"] or 0) * 15) / 1_000_000
    savings = api_opus - api_sonnet
    if savings < 1.0:
        return []
    key = _key("right-size", "opus-short-turns-7d")
    if _is_dismissed(db_path, key):
        return []
    return [{
        "key": key, "category": "right-size",
        "title": f"{row['n']} short Opus turns might fit on Sonnet",
        "body": f"Opus turns under 500 output tokens cost ~${api_opus:.2f} in the last 7 days. Sonnet would have cost ~${api_sonnet:.2f} (savings ~${savings:.2f}).",
        "scope": "opus-short-turns-7d",
    }]


def outlier_tips(db_path, today_iso: Optional[str] = None) -> List[dict]:
    today_iso = today_iso or datetime.utcnow().isoformat()
    since = _iso_days_ago(today_iso, 7)
    out = []
    with connect(db_path) as c:
        big = c.execute("""
          SELECT COUNT(*) AS n, AVG(result_tokens) AS avg_t
            FROM tool_calls
           WHERE tool_name='_tool_result' AND result_tokens > 50000 AND timestamp >= ?
        """, (since,)).fetchone()
        if big and (big["n"] or 0) >= 5:
            key = _key("tool-bloat", "result-50k+")
            if not _is_dismissed(db_path, key):
                out.append({
                    "key": key, "category": "tool-bloat",
                    "title": f"{big['n']} tool results over 50k tokens this week",
                    "body": f"Average size is {int(big['avg_t']):,} tokens. Pipe long Bash output to head/tail and ask for narrower file reads.",
                    "scope": "result-50k+",
                })
        for row in c.execute("""
          SELECT agent_id, COUNT(*) AS n,
                 AVG(input_tokens+output_tokens) AS mean_t,
                 MAX(input_tokens+output_tokens) AS max_t
            FROM messages
           WHERE is_sidechain=1 AND agent_id IS NOT NULL AND timestamp >= ?
           GROUP BY agent_id HAVING n >= 10
        """, (since,)):
            if (row["max_t"] or 0) > 6 * (row["mean_t"] or 1) and (row["max_t"] or 0) > 50_000:
                key = _key("subagent-outlier", row["agent_id"])
                if _is_dismissed(db_path, key):
                    continue
                out.append({
                    "key": key, "category": "subagent-outlier",
                    "title": f"Subagent {row['agent_id']} has cost outliers",
                    "body": f"Largest invocation used {int(row['max_t']):,} tokens vs mean {int(row['mean_t']):,}. Worth checking what those did differently.",
                    "scope": row["agent_id"],
                })
    return out


def all_tips(db_path, projects_dir: str, today_iso: Optional[str] = None) -> List[dict]:
    return [
        *knowledge_tips(db_path, projects_dir),
        *failing_command_tips(db_path, projects_dir, today_iso),
        *memory_hygiene_tips(db_path, projects_dir, today_iso),
        *cache_discipline_tips(db_path, today_iso),
        *repeated_target_tips(db_path, today_iso),
        *right_size_tips(db_path, today_iso),
        *outlier_tips(db_path, today_iso),
    ]
