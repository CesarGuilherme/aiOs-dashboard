"""Brain: live reader for Claude Code project memory dirs + knowledge suggestions.

Reads ~/.claude/projects/*/memory/*.md straight off disk on every request — the
files are the source of truth (auto-loaded into Claude's context each session),
so there is nothing to sync or invalidate. Suggestions reuse the tips engine's
SQLite data to spot knowledge Claude keeps re-deriving instead of remembering.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from .db import connect, daily_token_breakdown
from .memory_parsing import (
    _read_mem_dir,
    get_coverage,
    GLOBAL_MEM_DIR,
    GLOBAL_SLUG,
    SPECIAL_FILES,
    LEARNING_HEAD_RE,
    _parse_frontmatter,
    _iso,
)
from .naming import (
    best_project_name,
    get_labels,
)
from .pricing import cost_for
from .tips import _is_dismissed, _key

# Sentinel planted in the auto-learn extraction prompt (~/.claude/hooks/
# auto-learn-prompt.md). Lets memory_roi isolate the background passes' own
# token cost so net ROI is honest rather than rosy.
EXTRACTION_SENTINEL = "MEMORY_EXTRACTION_PASS"

# Soft-edge tuning: stopwords to ignore, term length floor, overlap threshold.
_STOPWORDS = frozenset("""
the a an and or but for nor so yet of to in on at by with from into onto over under
is are was were be been being do does did has have had will would can could should
this that these those it its it's as not no if then than when while which who whom
you your we our they their he she his her them us me my i id ie eg etc via per vs
read instead file files use used using one two new old via more most less via also
""".split())


# Parsing functions moved to memory_parsing.py


# Name functions are now in token_dashboard/naming.py
# get_labels and _encode_cwd (and best_project_name) are re-exported/imported via naming


def knowledge_suggestions(db_path: str, covered_by_slug: dict, labels: dict) -> List[dict]:
    """Tips-style active loop: targets Claude keeps re-reading but never memorized.

    covered_by_slug maps project_slug -> lowercase blob of that project's memory
    text; a target already mentioned there needs no new memory.
    """
    since = (datetime.utcnow() - timedelta(days=30)).isoformat()
    sql = """
      SELECT project_slug, target, COUNT(*) AS n, COUNT(DISTINCT session_id) AS sessions
        FROM tool_calls
       WHERE tool_name IN ('Read','Grep') AND timestamp >= ?
         AND target IS NOT NULL AND target != ''
       GROUP BY project_slug, target
       HAVING n >= 8 AND sessions >= 3
       ORDER BY n DESC LIMIT 30
    """
    out: List[dict] = []
    try:
        with connect(db_path) as c:
            rows = [dict(r) for r in c.execute(sql, (since,))]
    except Exception:
        return out
    for row in rows:
        slug, target = row["project_slug"], row["target"]
        base = Path(target).name
        if "/memory/" in target or base in SPECIAL_FILES:
            continue
        if base.lower() in covered_by_slug.get(slug, ""):
            continue
        key = _key("memory", f"{slug}:{target}")
        if _is_dismissed(db_path, key):
            continue
        label = labels.get(slug) or (re.split(r"-+", slug.strip("-")) or [slug])[-1]
        out.append({
            "key": key,
            "project": label,
            "title": f"{base} read {row['n']}× across {row['sessions']} sessions",
            "body": f"Claude re-read {target} {row['n']} times in {label} over the last 30 days "
                    "but has no memory about it. One saved summary auto-loads in every future session.",
            "prompt": f"Save a memory about {base} (what it does and the facts you keep re-reading "
                      f"it for) AND add a ~5-line contract for it to this project's CLAUDE.md, "
                      f"so future sessions don't re-derive it. File: {target}",
        })
        if len(out) >= 10:
            break
    return out


def memory_effectiveness(db_path: str, min_injections: int = 3) -> dict:
    """Did injected memories actually earn their place in context?

    Joins the read-path hooks' `memory_injections` log with auto-learn's
    `memory_usage` scoring. Only `mode='rank'` injections count: those were
    surfaced *because the ranker judged them relevant to the prompt*, so a
    rank-injected memory that's never referenced is a real miss. Baseline (global
    tier) injections are unconditional — they load every session by design — so
    counting them would falsely flag always-on memories as dead weight.

    Returns per-name stats (rank-injected sessions, sessions where it was
    referenced, hit rate) plus prune candidates: rank-injected into several
    sessions but used in none. Best-effort — empties if the tables don't exist
    yet (fresh install, before the hooks have run).
    """
    empty = {"by_name": {}, "prune_candidates": []}
    out = {"by_name": {}, "prune_candidates": []}
    try:
        with connect(db_path) as c:
            used_by = {r["memory_name"]: r["used"] for r in c.execute(
                "SELECT memory_name, COUNT(DISTINCT session_id) AS used "
                "FROM memory_usage WHERE used = 1 GROUP BY memory_name"
            )}
            rows = list(c.execute(
                "SELECT memory_name, COUNT(DISTINCT session_id) AS injected, MAX(ts) AS last_ts "
                "FROM memory_injections WHERE mode = 'rank' GROUP BY memory_name"
            ))
    except Exception:
        return empty
    for r in rows:
        name = r["memory_name"]
        injected = r["injected"] or 0
        used = used_by.get(name, 0)
        out["by_name"][name] = {
            "injected": injected,
            "used": used,
            "hit_rate": round(used / injected, 3) if injected else 0.0,
            "last": _iso(r["last_ts"]) if r["last_ts"] else "",
        }
        if injected >= min_injections and used == 0:
            out["prune_candidates"].append({
                "name": name,
                "injected": injected,
                "last": _iso(r["last_ts"]) if r["last_ts"] else "",
            })
    out["prune_candidates"].sort(key=lambda x: x["injected"], reverse=True)
    return out


# _read_mem_dir moved to memory_parsing.py


def get_brain(projects_dir: str, db_path: str, pricing: Optional[dict] = None) -> dict:
    root = Path(projects_dir)
    projects: List[dict] = []
    all_entries: List[dict] = []
    covered_by_slug: dict = {}

    mem_dirs = sorted(root.glob("*/memory")) if root.is_dir() else []
    slugs = [d.parent.name for d in mem_dirs]
    labels = get_labels(db_path, slugs)

    # Project dirs plus the global tier (rendered as a pseudo-project "Global").
    targets: List[Tuple[Path, str]] = [(d, d.parent.name) for d in mem_dirs]
    if GLOBAL_MEM_DIR.is_dir():
        targets.append((GLOBAL_MEM_DIR, GLOBAL_SLUG))

    for mem_dir, slug in targets:
        entries, learnings, covered_blob = _read_mem_dir(mem_dir, slug)
        covered_by_slug[slug] = covered_blob
        label = "Global" if slug == GLOBAL_SLUG else labels.get(slug, slug)
        projects.append({
            "slug": slug,
            "label": label,
            "entries": entries,
            "learnings": learnings,
        })
        all_entries.extend(entries)

    projects.sort(key=lambda p: max((e["mtime"] for e in p["entries"]), default=""), reverse=True)

    by_name = {}
    for e in all_entries:
        by_name.setdefault(e["name"], e["id"])
    links = []
    seen = set()
    for e in all_entries:
        for slug_name in e["links"]:
            target_id = by_name.get(slug_name)
            if not target_id or target_id == e["id"]:
                continue
            pair = tuple(sorted((e["id"], target_id)))
            if pair in seen:
                continue
            seen.add(pair)
            links.append({"source": e["id"], "target": target_id, "kind": "explicit"})

    # Soft edges: connect memories that share distinctive vocabulary even when
    # nobody hand-wrote a [[link]]. Rendered faint so explicit links stay primary.
    links.extend(_soft_links(all_entries, seen))

    # Effectiveness: tag each entry with its rank hit-rate, and resolve prune
    # candidates back to a slug+file so the existing quarantine UI can act on them.
    effectiveness = memory_effectiveness(db_path)
    entry_by_name: dict = {}
    for e in all_entries:
        entry_by_name.setdefault(e["name"], e)
        e["usage"] = effectiveness["by_name"].get(e["name"])
    label_by_slug = {p["slug"]: p["label"] for p in projects}
    for pc in effectiveness["prune_candidates"]:
        ent = entry_by_name.get(pc["name"])
        if ent:
            slug = ent["id"].split("::", 1)[0]
            pc.update({"slug": slug, "file": ent["file"],
                       "label": label_by_slug.get(slug, slug), "type": ent["type"]})

    return {
        "projects": projects,
        "links": links,
        "timeline": _learning_timeline(projects),
        "roi": memory_roi(db_path, covered_by_slug, pricing),
        "suggestions": knowledge_suggestions(db_path, covered_by_slug, labels),
        "effectiveness": effectiveness,
    }


def _significant_terms(text: str) -> Counter:
    """Lowercase alpha tokens, stopwords/short words dropped, term-frequency counted."""
    toks = re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", (text or "").lower())
    return Counter(t for t in toks if t not in _STOPWORDS)


def _soft_links(all_entries: List[dict], explicit_pairs: set,
                threshold: float = 0.12, max_per_node: int = 3) -> List[dict]:
    """Keyword-overlap edges between memories (zero-dependency, stdlib only).

    Cosine-like overlap over each memory's distinctive terms (name + description
    + body). Skips pairs that already have an explicit [[link]], caps edges per
    node so the graph stays legible, and tags every edge kind="soft".
    """
    vecs = []
    for e in all_entries:
        blob = f"{e['name']} {e['name'].replace('-', ' ')} {e.get('description','')} {e.get('body','')}"
        tf = _significant_terms(blob)
        norm = sum(v * v for v in tf.values()) ** 0.5
        vecs.append((e["id"], tf, norm))

    scored = []
    for i in range(len(vecs)):
        id_i, tf_i, norm_i = vecs[i]
        if norm_i == 0:
            continue
        for j in range(i + 1, len(vecs)):
            id_j, tf_j, norm_j = vecs[j]
            if norm_j == 0:
                continue
            pair = tuple(sorted((id_i, id_j)))
            if pair in explicit_pairs:
                continue
            common = tf_i.keys() & tf_j.keys()
            if not common:
                continue
            dot = sum(tf_i[t] * tf_j[t] for t in common)
            sim = dot / (norm_i * norm_j)
            if sim >= threshold:
                scored.append((sim, id_i, id_j))

    scored.sort(reverse=True)
    degree: Counter = Counter()
    out, used = [], set()
    for sim, a, b in scored:
        if (a, b) in used or degree[a] >= max_per_node or degree[b] >= max_per_node:
            continue
        used.add((a, b))
        degree[a] += 1
        degree[b] += 1
        out.append({"source": a, "target": b, "kind": "soft", "weight": round(sim, 3)})
    return out


def _learning_timeline(projects: List[dict]) -> List[dict]:
    """Per-day counts of memories added (auto vs user) and learnings journaled."""
    days: dict = {}

    def bucket(day: str) -> dict:
        return days.setdefault(day, {"day": day, "auto": 0, "user": 0, "learnings": 0})

    for p in projects:
        for e in p["entries"]:
            day = (e.get("mtime") or "")[:10]
            if not day:
                continue
            bucket(day)["auto" if e.get("source") == "auto" else "user"] += 1
        for l in p["learnings"]:
            day = (l.get("date") or "")[:10]
            if day:
                bucket(day)["learnings"] += 1
    return [days[d] for d in sorted(days)]


def memory_roi(db_path: str, covered_by_slug: dict, pricing: Optional[dict] = None) -> dict:
    """Quantify the brain's payoff: re-reads a memory could replace vs. the
    token cost of the automatic extraction passes that built it.

    Saved side is framed as an *estimate / avoided-re-read potential*: tokens
    spent re-reading files that a memory already covers, over the last 30 days.
    Cost side is real: actual tokens burned by the background extraction passes
    (identified by the EXTRACTION_SENTINEL planted in their prompt).
    """
    since = (datetime.utcnow() - timedelta(days=30)).isoformat()
    out = {
        "memorized_targets": 0,
        "reread_count": 0,
        "saved_tokens_est": 0,
        "saved_usd_est": None,
        "extraction_sessions": 0,
        "extraction_tokens": 0,
        "extraction_usd": None,
        "net_tokens_est": 0,
        "net_usd_est": None,
        "cache_trend": [],
    }

    # --- Saved side: re-reads of already-memorized files (Read/Grep joined to
    # their _tool_result for the byte cost). Count only files a memory covers.
    reread_sql = """
      SELECT r.project_slug AS slug, r.target AS target,
             COUNT(*) AS reads,
             COALESCE(SUM(res.result_tokens), 0) AS tokens
        FROM tool_calls r
        JOIN tool_calls res
          ON res.tool_use_id = r.tool_use_id AND res.tool_name = '_tool_result'
       WHERE r.tool_name IN ('Read', 'Grep') AND r.timestamp >= ?
         AND r.target IS NOT NULL AND r.target != ''
       GROUP BY r.project_slug, r.target
    """
    memorized = set()
    try:
        with connect(db_path) as c:
            for row in c.execute(reread_sql, (since,)):
                base = Path(row["target"]).name.lower()
                if not base:
                    continue
                if base in covered_by_slug.get(row["slug"], ""):
                    memorized.add((row["slug"], row["target"]))
                    out["reread_count"] += row["reads"]
                    out["saved_tokens_est"] += row["tokens"] or 0
    except Exception:
        pass
    out["memorized_targets"] = len(memorized)

    # --- Cost side: tokens spent by the extraction passes themselves.
    extraction_models: dict = {}
    try:
        with connect(db_path) as c:
            sids = [r["session_id"] for r in c.execute(
                "SELECT DISTINCT session_id FROM messages WHERE prompt_text LIKE ?",
                (f"%{EXTRACTION_SENTINEL}%",),
            )]
            out["extraction_sessions"] = len(sids)
            for sid in sids:
                for m in c.execute("""
                    SELECT COALESCE(model,'unknown') AS model,
                           COALESCE(SUM(input_tokens),0)           AS input_tokens,
                           COALESCE(SUM(output_tokens),0)          AS output_tokens,
                           COALESCE(SUM(cache_read_tokens),0)      AS cache_read_tokens,
                           COALESCE(SUM(cache_create_5m_tokens),0) AS cache_create_5m_tokens,
                           COALESCE(SUM(cache_create_1h_tokens),0) AS cache_create_1h_tokens
                      FROM messages WHERE session_id=? AND type='assistant'
                     GROUP BY model
                """, (sid,)):
                    md = dict(m)
                    out["extraction_tokens"] += (
                        md["input_tokens"] + md["output_tokens"]
                        + md["cache_create_5m_tokens"] + md["cache_create_1h_tokens"]
                    )
                    agg = extraction_models.setdefault(md["model"], {
                        "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
                        "cache_create_5m_tokens": 0, "cache_create_1h_tokens": 0,
                    })
                    for k in agg:
                        agg[k] += md[k]
    except Exception:
        pass

    # --- Dollar framing (rough for saved, real for extraction).
    if pricing:
        extr_usd = 0.0
        for model, usage in extraction_models.items():
            c = cost_for(model, usage, pricing)
            if c["usd"]:
                extr_usd += c["usd"]
        out["extraction_usd"] = round(extr_usd, 4)
        # Rough upper bound: saved re-read tokens valued as fresh Sonnet input.
        saved = cost_for("claude-sonnet-4-6", {
            "input_tokens": out["saved_tokens_est"], "output_tokens": 0,
            "cache_read_tokens": 0, "cache_create_5m_tokens": 0, "cache_create_1h_tokens": 0,
        }, pricing)
        out["saved_usd_est"] = saved["usd"]
        if out["saved_usd_est"] is not None and out["extraction_usd"] is not None:
            out["net_usd_est"] = round(out["saved_usd_est"] - out["extraction_usd"], 4)

    out["net_tokens_est"] = out["saved_tokens_est"] - out["extraction_tokens"]

    # --- Cache-hit-rate trend (cheaper context reuse over time).
    for d in daily_token_breakdown(db_path, since, None):
        denom = (d["input_tokens"] or 0) + (d["cache_read_tokens"] or 0)
        out["cache_trend"].append({
            "day": d["day"],
            "hit_rate": round((d["cache_read_tokens"] or 0) / denom, 4) if denom else 0.0,
        })
    return out


# --- Mutations: the dashboard's only file-writes, narrowly scoped to memory dirs.

def _resolve_memory_file(projects_dir: str, slug: str, filename: str) -> Optional[Path]:
    """Validate slug+filename point at a real memory .md, with no traversal."""
    if not slug or not filename or filename in SPECIAL_FILES:
        return None
    if "/" in filename or "\\" in filename or ".." in slug or "/" in slug or "\\" in slug:
        return None
    if not filename.endswith(".md"):
        return None
    mem_dir = (Path(projects_dir) / slug / "memory").resolve()
    target = (mem_dir / filename).resolve()
    if mem_dir not in target.parents or not target.is_file():
        return None
    return target


def _strip_index_pointer(mem_dir: Path, filename: str) -> None:
    """Drop the MEMORY.md pointer line that links to `filename`, if present."""
    index = mem_dir / "MEMORY.md"
    if not index.is_file():
        return
    keep = [ln for ln in index.read_text(encoding="utf-8").splitlines()
            if f"]({filename})" not in ln]
    index.write_text("\n".join(keep) + "\n", encoding="utf-8")


def quarantine_memory(projects_dir: str, slug: str, filename: str) -> dict:
    """Move a memory file into memory/.trash/ and remove its index pointer.

    Reversible by design (the file isn't deleted), so a wrong auto-memory can be
    corrected from the dashboard without losing anything.
    """
    target = _resolve_memory_file(projects_dir, slug, filename)
    if target is None:
        return {"ok": False, "error": "not found"}
    mem_dir = target.parent
    trash = mem_dir / ".trash"
    trash.mkdir(exist_ok=True)
    dest = trash / f"{datetime.utcnow():%Y%m%dT%H%M%S}_{filename}"
    target.rename(dest)
    _strip_index_pointer(mem_dir, filename)
    return {"ok": True, "trashed": dest.name}


def promote_memory(projects_dir: str, slug: str, filename: str) -> dict:
    """Mark an auto memory as confirmed (source: user) so it stops being badged."""
    target = _resolve_memory_file(projects_dir, slug, filename)
    if target is None:
        return {"ok": False, "error": "not found"}
    text = target.read_text(encoding="utf-8")
    if re.search(r"^\s*source:\s*\w+\s*$", text, flags=re.MULTILINE):
        text = re.sub(r"^(\s*source:\s*)\w+\s*$", r"\1user", text, count=1, flags=re.MULTILINE)
    else:
        # Insert under metadata: if present, else leave untouched.
        text = re.sub(r"(^\s*metadata:\s*$)", r"\1\n  source: user", text, count=1, flags=re.MULTILINE)
    target.write_text(text, encoding="utf-8")
    return {"ok": True}
