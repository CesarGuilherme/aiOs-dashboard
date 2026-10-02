"""Rule-based tips engine — waste/cost/behavior. Brain owns “save a memory”."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from .db import connect
from .fx import brl
from .pricing import cost_for, load_pricing
from .tool_aliases import BASH_TOOLS, POLL_TOOLS, READ_TOOLS, sql_in

_PRICING_JSON = Path(__file__).resolve().parent.parent / "pricing.json"

# Protocol / always-on paths — reading these is how agents are supposed to work.
_NOISE_NAMES = frozenset({
    "INDEX.md", "MEMORY.md", "LEARNINGS.md", "AUDIT.md",
    "SKILL.md", "CLAUDE.md", "AGENTS.md",
    "TaskOutput", "TodoWrite", "ExitPlanMode", "AskUserQuestion",
})
_NOISE_TOOLS = frozenset({
    "ExitPlanMode", "exit_plan_mode",
    "AskUserQuestion", "ask_user_question",
    "TodoWrite", "todo_write",
})


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


def _is_noise_target(target: str) -> bool:
    if not target or not str(target).strip():
        return True
    target = str(target)
    base = Path(target).name
    if base in _NOISE_NAMES or target in _NOISE_NAMES:
        return True
    t = target.replace("\\", "/").lower()
    if "/memory/" in t or "scratchpad" in t:
        return True
    if "/tmp/claude-" in t:
        return True
    return False


def _is_noise_tool(name: str) -> bool:
    return (name or "") in _NOISE_TOOLS


def _short(target: str, n: int = 60) -> str:
    return target if len(target) <= n else target[: n - 1] + "…"


def _memory_blobs(projects_dir: str) -> tuple[dict, str]:
    """Per-slug coverage plus the global-domain blob (empty if no Brain)."""
    if not projects_dir:
        return {}, ""
    from .memory_parsing import GLOBAL_SLUG, get_coverage
    covered = get_coverage(projects_dir)
    return covered, covered.get(GLOBAL_SLUG, "")


def _in_memory(slug: str, needles, covered: dict, global_blob: str) -> bool:
    hay = ((covered.get(slug) or "") + "\n" + global_blob).lower()
    if not hay.strip():
        return False
    for n in needles:
        if not n:
            continue
        s = str(n).lower()
        if len(s) >= 4 and s in hay:
            return True
        base = Path(str(n)).name.lower()
        if base != s and len(base) >= 4 and base in hay:
            return True
    return False


def _pricing() -> dict:
    return load_pricing(_PRICING_JSON)


def _usd(model: str, in_tok: int, out_tok: int, pricing: dict) -> float:
    usage = {
        "input_tokens": in_tok or 0, "output_tokens": out_tok or 0,
        "cache_read_tokens": 0, "cache_create_5m_tokens": 0, "cache_create_1h_tokens": 0,
    }
    return cost_for(model, usage, pricing).get("usd") or 0.0


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


def failing_command_tips(db_path, projects_dir: str, today_iso: Optional[str] = None) -> List[dict]:
    """Tool calls that keep erroring — or that keep erroring despite a Brain note."""
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
    from .naming import get_labels
    labels = get_labels(db_path, sorted({r["project_slug"] for r in rows}))
    covered, global_blob = _memory_blobs(projects_dir)
    out = []
    for row in rows:
        if _is_noise_tool(row["tool_name"]) or _is_noise_target(row["target"]):
            continue
        target = row["target"]
        key = _key("repeat-fail", f"{row['project_slug']}:{row['tool_name']}:{target[:120]}")
        if _is_dismissed(db_path, key):
            continue
        label = labels.get(row["project_slug"], row["project_slug"])
        known = _in_memory(
            row["project_slug"],
            (target, Path(target).name, row["tool_name"]),
            covered, global_blob,
        )
        short = _short(target)
        if known:
            out.append({
                "key": key, "category": "ignored-fail",
                "project": label,
                "title": f"Brain already documents {row['tool_name']} `{short}` — still failed {row['n']}×",
                "body": f"{label}: this failure is already in memory, but it still errored "
                        f"{row['n']} times across {row['sessions']} sessions in 30 days. "
                        "Follow the documented workaround instead of retrying.",
                "prompt": f"A Brain memory already covers this failure: {row['tool_name']} `{target}`. "
                          "Don't retry it. Follow the workaround in that memory (INDEX.md first).",
                "scope": key,
            })
        else:
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

    Two rules: a project's LEARNINGS journal past the distillation threshold, and
    an active project (≥5 sessions/30d) whose cwd no Brain hub covers.
    """
    from .memory_parsing import brain_db_for, coverage_from_tree, load_tree, owner_of
    from .naming import _encode_cwd, get_labels

    today_iso = today_iso or datetime.utcnow().isoformat()
    since = _iso_days_ago(today_iso, 30)
    out: List[dict] = []

    with connect(db_path) as c:
        active = {r["project_slug"]: r["s"] for r in c.execute(
            """SELECT project_slug, COUNT(DISTINCT session_id) AS s
                 FROM messages WHERE timestamp >= ?
                GROUP BY project_slug HAVING s >= 5""", (since,))}
    nodes, _ = load_tree(brain_db_for(projects_dir)) if projects_dir else ([], [])
    by_id = {n["id"]: n for n in nodes}
    learnings: dict = {}
    for n in nodes:
        if n["kind"] == "memory" and n.get("type") == "learning":
            owner = owner_of(n, by_id)
            if owner:
                learnings[owner["id"]] = learnings.get(owner["id"], 0) + 1

    for hub_id, entries in sorted(learnings.items()):
        if entries <= 30:
            continue
        key = _key("memory-distill", hub_id)
        if _is_dismissed(db_path, key):
            continue
        label = by_id[hub_id]["name"]
        out.append({
            "key": key, "category": "memory",
            "project": label,
            "title": f"LEARNINGS.md has {entries} entries — time to distill",
            "body": f"The {label} learnings journal passed the ~30-entry threshold. Distilled atomic "
                    "notes in the right topic are found by `brain search`; a long journal is just noise.",
            "prompt": f"The Brain journal ~/.brain/nodes/{hub_id}/LEARNINGS.md passed 30 entries. Promote "
                      "the reusable ones to atomic notes in the right topic (per the /learn skill), add "
                      "cross-project edges where they apply, trim the journal, then `brain lint`.",
            "scope": hub_id,
        })

    covered = coverage_from_tree(nodes)
    labels = get_labels(db_path, sorted(active))
    for slug, sessions in active.items():
        if covered.get(_encode_cwd(slug)):
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
            "prompt": "This project has no hub in the Second Brain yet. Create "
                      "~/.brain/nodes/<domain>/<project>/_project.md (O que é / O que faz / Como faz / "
                      "Stack / Soluções reutilizáveis / Onde está, with workspaces: = this repo) and "
                      "save its durable facts as notes, per the /learn skill.",
            "scope": slug,
        })
    return out


def _hot_reads(db_path, since: str) -> List[dict]:
    sql = f"""
      SELECT project_slug, target, COUNT(*) AS n, COUNT(DISTINCT session_id) AS sessions
        FROM tool_calls
       WHERE tool_name {sql_in(READ_TOOLS)}
         AND timestamp >= ?
         AND target IS NOT NULL AND target != ''
       GROUP BY project_slug, target
      HAVING n > 10 AND sessions >= 3
       ORDER BY n DESC LIMIT 20
    """
    with connect(db_path) as c:
        return [dict(r) for r in c.execute(sql, (since,))]


def ignored_memory_tips(db_path, projects_dir: str, today_iso: Optional[str] = None) -> List[dict]:
    """Hot reads of a file the Brain already covers — agent ignored the memory."""
    today_iso = today_iso or datetime.utcnow().isoformat()
    since = _iso_days_ago(today_iso, 7)
    rows = _hot_reads(db_path, since)
    if not rows:
        return []
    from .naming import get_labels
    covered, global_blob = _memory_blobs(projects_dir)
    labels = get_labels(db_path, sorted({r["project_slug"] for r in rows}))
    out = []
    for row in rows:
        target = row["target"]
        if _is_noise_target(target):
            continue
        base = Path(target).name
        if not _in_memory(row["project_slug"], (base, target), covered, global_blob):
            continue
        key = _key("ignored-memory", f"{row['project_slug']}:{target[:120]}")
        if _is_dismissed(db_path, key):
            continue
        label = labels.get(row["project_slug"], row["project_slug"])
        out.append({
            "key": key, "category": "ignored-memory",
            "project": label,
            "title": f"{base} has a Brain memory — still read {row['n']}× across {row['sessions']} sessions",
            "body": f"Agents keep re-opening {target} in {label} even though the Second Brain "
                    "already covers it. INDEX first, then the memory — don't re-derive the contract from source.",
            "prompt": f"A Brain memory already covers `{base}` ({target}). Don't re-read the source "
                      "for contract facts — open INDEX.md, then that memory. Update the memory if it's "
                      "stale; re-open the file only when editing it.",
            "scope": key,
        })
    return out


def repeat_file_tips(db_path, projects_dir: str = "", today_iso: Optional[str] = None) -> List[dict]:
    """Hot reads that are not yet a Brain memory — hotspot, not a save-memory nudge."""
    today_iso = today_iso or datetime.utcnow().isoformat()
    since = _iso_days_ago(today_iso, 7)
    rows = _hot_reads(db_path, since)
    if not rows:
        return []
    from .naming import get_labels
    covered, global_blob = _memory_blobs(projects_dir)
    labels = get_labels(db_path, sorted({r["project_slug"] for r in rows}))
    out = []
    for row in rows:
        target = row["target"]
        if _is_noise_target(target):
            continue
        base = Path(target).name
        if _in_memory(row["project_slug"], (base, target), covered, global_blob):
            continue
        key = _key("repeat-file", target)
        if _is_dismissed(db_path, key):
            continue
        label = labels.get(row["project_slug"], row["project_slug"])
        out.append({
            "key": key, "category": "repeat-file",
            "project": label,
            "title": f"`{_short(target)}` read {row['n']}× across {row['sessions']} sessions",
            "body": f"Hotspot in {label} over the last 7 days. Prefer a short AGENTS.md/CLAUDE.md "
                    "contract and narrower reads (offset/limit) instead of re-opening the whole file.",
            "prompt": f"`{target}` is a hotspot across sessions. Add a ~5-line contract to this "
                      "project's AGENTS.md/CLAUDE.md (what it does, the gotchas) and prefer offset/limit "
                      "reads. If the facts are durable, Brain will already be suggesting a memory.",
            "scope": target,
        })
    return out


def poll_wait_tips(db_path, today_iso: Optional[str] = None) -> List[dict]:
    """sleep loops and TaskOutput polling — use Monitor/watch instead of an alias."""
    today_iso = today_iso or datetime.utcnow().isoformat()
    since = _iso_days_ago(today_iso, 7)
    with connect(db_path) as c:
        sleep_n = c.execute(f"""
          SELECT COUNT(*) AS n FROM tool_calls
           WHERE tool_name {sql_in(BASH_TOOLS)} AND timestamp >= ?
             AND (target LIKE '%sleep %' OR target LIKE '%sleep\t%')
        """, (since,)).fetchone()["n"]
        poll_n = c.execute(f"""
          SELECT COUNT(*) AS n FROM tool_calls
           WHERE timestamp >= ?
             AND (tool_name {sql_in(POLL_TOOLS)} OR target IN ('TaskOutput','get_command_or_subagent_output'))
        """, (since,)).fetchone()["n"]
    if (sleep_n or 0) < 5 and (poll_n or 0) < 10:
        return []
    key = _key("poll-wait", "7d")
    if _is_dismissed(db_path, key):
        return []
    bits = []
    if sleep_n >= 5:
        bits.append(f"{sleep_n} bash commands with `sleep`")
    if poll_n >= 10:
        bits.append(f"{poll_n} TaskOutput/poll calls")
    return [{
        "key": key, "category": "poll-wait",
        "title": "Polling instead of a watch/Monitor",
        "body": "Last 7 days: " + " and ".join(bits)
                + ". Sleep loops and TaskOutput polling burn turns. Use Monitor/watch (or one blocking wait with a timeout).",
        "prompt": "Replace bash `sleep` poll loops and repeated TaskOutput checks with the Monitor/watch "
                  "tool, or a single blocking wait with a timeout. Don't alias the sleep.",
        "scope": "7d",
    }]


def skill_reread_tips(db_path, today_iso: Optional[str] = None) -> List[dict]:
    """Agents opening the full SKILL.md instead of a stub."""
    today_iso = today_iso or datetime.utcnow().isoformat()
    since = _iso_days_ago(today_iso, 30)
    sql = f"""
      SELECT target, COUNT(*) AS n, COUNT(DISTINCT session_id) AS sessions
        FROM tool_calls
       WHERE tool_name {sql_in(READ_TOOLS)} AND timestamp >= ?
         AND target LIKE '%SKILL.md'
       GROUP BY target
      HAVING n >= 8 AND sessions >= 3
       ORDER BY n DESC LIMIT 10
    """
    with connect(db_path) as c:
        rows = [dict(r) for r in c.execute(sql, (since,))]
    by_skill: dict[str, dict] = {}
    for row in rows:
        name = Path(row["target"]).parent.name or Path(row["target"]).name
        slot = by_skill.setdefault(name, {"n": 0, "sessions": 0, "target": row["target"]})
        slot["n"] += row["n"]
        slot["sessions"] = max(slot["sessions"], row["sessions"])
    out = []
    for name, slot in sorted(by_skill.items(), key=lambda kv: -kv[1]["n"]):
        if slot["n"] < 8 or slot["sessions"] < 3:
            continue
        key = _key("skill-reread", name)
        if _is_dismissed(db_path, key):
            continue
        out.append({
            "key": key, "category": "skill-reread",
            "title": f"`{name}` SKILL.md read {slot['n']}× across {slot['sessions']}+ sessions",
            "body": "Agents keep opening the full skill file. A ~10-line stub in AGENTS.md / global rules "
                    "(when to use it, the one constraint) avoids re-ingesting the whole skill.",
            "prompt": f"SKILL.md for `{name}` is being re-read across sessions ({slot['target']}). "
                      "Add a ~10-line stub to AGENTS.md or the global agent rules: when to invoke it, "
                      "the one hard constraint, and do not open the full SKILL.md unless implementing it.",
            "scope": name,
        })
    return out


def right_size_tips(db_path, today_iso: Optional[str] = None) -> List[dict]:
    today_iso = today_iso or datetime.utcnow().isoformat()
    since = _iso_days_ago(today_iso, 7)
    pricing = _pricing()
    out = []
    specs = (
        ("opus-short-turns-7d", "model LIKE '%opus%'", "claude-opus-5", "claude-sonnet-5",
         "{n} short Opus turns might fit on Sonnet"),
        ("grok-short-turns-7d",
         "model LIKE 'grok-4%' AND model NOT LIKE '%build%' AND model NOT LIKE '%composer%'",
         "grok-4.5", "grok-build",
         "{n} short Grok-4 turns might fit on grok-build"),
    )
    with connect(db_path) as c:
        for scope, where, expensive, cheap, title in specs:
            row = c.execute(f"""
              SELECT COUNT(*) AS n,
                     SUM(input_tokens+cache_create_5m_tokens+cache_create_1h_tokens) AS in_tok,
                     SUM(output_tokens) AS out_tok
                FROM messages
               WHERE type='assistant' AND {where}
                 AND output_tokens < 500 AND is_sidechain = 0
                 AND timestamp >= ?
            """, (since,)).fetchone()
            if not row or (row["n"] or 0) < 10:
                continue
            hi = _usd(expensive, row["in_tok"] or 0, row["out_tok"] or 0, pricing)
            lo = _usd(cheap, row["in_tok"] or 0, row["out_tok"] or 0, pricing)
            savings = hi - lo
            if savings < 1.0:
                continue
            key = _key("right-size", scope)
            if _is_dismissed(db_path, key):
                continue
            out.append({
                "key": key, "category": "right-size",
                "title": title.format(n=row["n"]),
                "body": (f"Turns under 500 output tokens cost ~{brl(hi)} in the last 7 days. "
                         f"{cheap} would have cost ~{brl(lo)} (savings ~{brl(savings)})."),
                "prompt": (f"{row['n']} short turns ran on the expensive model. For sub-500-output turns, "
                           f"prefer {cheap} unless the task actually needs the larger model."),
                "scope": scope,
            })
    return out


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
                    "prompt": "Several tool results exceeded 50k tokens this week. Pipe long shell output "
                              "to head/tail and request offset/limit file reads instead of dumping whole files.",
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
                    "prompt": f"Subagent `{row['agent_id']}` has a {int(row['max_t']):,}-token outlier vs "
                              f"mean {int(row['mean_t']):,}. Inspect that invocation before the next dispatch.",
                    "scope": row["agent_id"],
                })
    return out


def all_tips(db_path, projects_dir: str, today_iso: Optional[str] = None) -> List[dict]:
    return [
        *ignored_memory_tips(db_path, projects_dir, today_iso),
        *failing_command_tips(db_path, projects_dir, today_iso),
        *skill_reread_tips(db_path, today_iso),
        *poll_wait_tips(db_path, today_iso),
        *memory_hygiene_tips(db_path, projects_dir, today_iso),
        *cache_discipline_tips(db_path, today_iso),
        *repeat_file_tips(db_path, projects_dir, today_iso),
        *right_size_tips(db_path, today_iso),
        *outlier_tips(db_path, today_iso),
    ]
