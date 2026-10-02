"""Brain: reader for the multi-agent Second Brain (~/.brain) + suggestions.

The Brain is a Markdown tree (domain → project → topic → memory) compiled by
~/.brain/scripts/brain.py into brain.db; this module reads that DB on every
request and shapes it for the Brain tab. Suggestions reuse SQLite tool data to
spot knowledge agents keep re-deriving.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from .db import connect, daily_token_breakdown
from .memory_parsing import (
    GLOBAL_SLUG,
    SPECIAL_FILES,
    _iso,
    _parse_frontmatter,
    brain_db_for,
    brain_root,
    coverage_from_tree,
    load_tree,
    owner_of,
    rebuild_brain,
)
from .naming import (
    _encode_cwd,
    best_project_name,
    get_labels,
)
from .pricing import cost_for
from .tips import _is_dismissed, _key
from .tool_aliases import READ_TOOLS, sql_in

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




# Name functions are now in token_dashboard/naming.py
# get_labels and _encode_cwd (and best_project_name) are re-exported/imported via naming


def _coverage_slug(project_slug: str, target: str) -> str:
    """Slug to look coverage up under. Raw `project_slug` is not it.

    Two ways the stored slug misses the Brain dir that actually holds the
    memories: Grok's scanner stores the raw cwd (`SSD_CESAR`) while Brain dirs
    use Claude's encoding (`SSD-CESAR`), and a git worktree gets a slug of its
    own though its memories live with the parent repo. Both make every read look
    uncovered, so every project Grok touches suggests memories that already
    exist. Dismissal keys stay on the raw slug, so old dismissals survive.
    """
    root, sep, _ = target.partition("/.worktrees/")
    return _encode_cwd(root if sep else project_slug)


def _target_vanished(target: str) -> bool:
    """File is gone while its volume is still mounted (deleted worktree, moved file).

    Mount-aware on purpose: /Volumes/SSD-CESAR is often unmounted here, and a
    bare exists() check would then flag every project at once.
    """
    p = Path(target)
    if p.exists():
        return False
    parts = p.parts
    root = Path(*parts[:3]) if len(parts) > 3 and parts[1] == "Volumes" else Path(p.anchor)
    return root.is_dir()


def knowledge_suggestions(db_path: str, covered_by_slug: dict, labels: dict) -> List[dict]:
    """Tips-style active loop: targets agents keep re-reading but never memorized.

    covered_by_slug maps project_slug -> lowercase blob of that project's memory
    text; a target already mentioned there needs no new memory.
    """
    since = (datetime.utcnow() - timedelta(days=30)).isoformat()
    sql = f"""
      SELECT project_slug, target, COUNT(*) AS n, COUNT(DISTINCT session_id) AS sessions
        FROM tool_calls
       WHERE tool_name {sql_in(READ_TOOLS)} AND timestamp >= ?
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
        if _target_vanished(target):
            continue
        if base.lower() in covered_by_slug.get(_coverage_slug(slug, target), ""):
            continue
        key = _key("memory", f"{slug}:{target}")
        if _is_dismissed(db_path, key):
            continue
        label = labels.get(slug) or (re.split(r"-+", slug.strip("-")) or [slug])[-1]
        out.append({
            "key": key,
            "project": label,
            "title": f"{base} read {row['n']}× across {row['sessions']} sessions",
            "body": f"Agents re-read {target} {row['n']} times in {label} over the last 30 days "
                    "but the Second Brain has no memory about it. One saved summary helps every future session.",
            "prompt": f"Save a memory about {base} (what it does and the facts you keep re-reading "
                      f"it for) AND add a ~5-line contract for it to this project's CLAUDE.md/AGENTS.md, "
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




def get_brain(projects_dir: str, db_path: str, pricing: Optional[dict] = None) -> dict:
    """Brain tab payload from brain.db.

    `projects` keeps the per-project list shape the cards render (one per hub,
    plus the global domain); `graph` is the full tree for the canvas: every node
    with its kind/size, `parent` edges for the hierarchy, and the typed
    cross-links (+ faint keyword `soft` edges) on top.
    """
    nodes, edges = load_tree(brain_db_for(projects_dir))
    by_id = {n["id"]: n for n in nodes}
    covered_by_slug = coverage_from_tree(nodes)
    labels = get_labels(db_path, list(covered_by_slug))

    groups: dict = {}
    for n in nodes:
        owner = owner_of(n, by_id)
        n["_owner"] = owner["id"] if owner else n["id"]
        if owner and owner["id"] not in groups and (owner["kind"] == "project" or owner["id"] == GLOBAL_SLUG):
            domain = owner["id"].split("/", 1)[0]
            groups[owner["id"]] = {
                "slug": owner["id"], "label": "Global" if owner["id"] == GLOBAL_SLUG else owner["name"],
                "domain": domain, "summary": owner.get("summary") or "", "size": owner.get("size") or 0,
                "entries": [], "learnings": [],
            }
        if n["kind"] != "memory" or n["_owner"] not in groups:
            continue
        g = groups[n["_owner"]]
        if n["type"] == "learning":
            g["learnings"].append({"date": n.get("date") or "", "title": n["name"], "body": n.get("body") or ""})
            continue
        g["entries"].append({
            "id": n["id"], "name": n["name"], "description": n.get("summary") or "",
            "type": n.get("type") or "reference", "source": n.get("source") or "user",
            "origin_session": "", "body": n.get("body") or "",
            "file": _rel_file(projects_dir, n.get("file") or ""),
            "mtime": _iso(n["mtime"]) if n.get("mtime") else "",
            "links": sorted({e["dst"] for e in edges if e["src"] == n["id"]}),
        })
    projects = sorted(groups.values(), key=lambda p: (p["domain"] != GLOBAL_SLUG, p["domain"], p["label"].lower()))
    for p in projects:
        p["entries"].sort(key=lambda e: e["mtime"], reverse=True)
        p["learnings"].sort(key=lambda l: l["date"], reverse=True)
    all_entries = [e for p in projects for e in p["entries"]]

    cross = [{"source": e["src"], "target": e["dst"], "kind": e["kind"], "why": e.get("why") or ""}
             for e in edges if e["src"] in by_id and e["dst"] in by_id]
    linked = {tuple(sorted((l["source"], l["target"]))) for l in cross}
    soft = _soft_links(all_entries, linked)

    effectiveness = memory_effectiveness(db_path)
    entry_by_name = {}
    for e in all_entries:
        entry_by_name.setdefault(e["name"], e)
        e["usage"] = effectiveness["by_name"].get(e["name"])
    label_by_slug = {p["slug"]: p["label"] for p in projects}
    for pc in effectiveness["prune_candidates"]:
        ent = entry_by_name.get(pc["name"])
        if ent:
            slug = by_id[ent["id"]]["_owner"]
            pc.update({"slug": slug, "file": ent["file"],
                       "label": label_by_slug.get(slug, slug), "type": ent["type"]})

    return {
        "projects": projects,
        "links": cross,
        "graph": _graph(nodes, by_id, cross + soft),
        "timeline": _learning_timeline(projects),
        "roi": memory_roi(db_path, covered_by_slug, pricing),
        "suggestions": knowledge_suggestions(db_path, covered_by_slug, labels),
        "effectiveness": effectiveness,
    }


def _rel_file(nodes_dir: str, path: str) -> str:
    """Absolute node path -> path relative to nodes/ (what the mutation endpoints take)."""
    try:
        return str(Path(path).resolve().relative_to(Path(nodes_dir).resolve()))
    except (ValueError, OSError):
        return Path(path).name


def _graph(nodes: List[dict], by_id: dict, links: List[dict]) -> dict:
    """Canvas payload: every tree node (sized by kind + descendants) and its edges."""
    out_nodes = []
    for n in nodes:
        owner = by_id.get(n["_owner"]) or n
        group = owner["name"] if owner["kind"] == "project" else n["id"].split("/", 1)[0]
        out_nodes.append({
            "id": n["id"], "name": n["name"], "layer": "memory", "kind": n["kind"],
            "type": n.get("type") or "", "group": group, "size": n.get("size") or 0,
            "meta": {"project": group, "kind": n["kind"], "summary": n.get("summary") or "",
                     "mtime": _iso(n["mtime"]) if n.get("mtime") else "",
                     "mem": n["kind"] == "memory" and n.get("type") != "learning", "name": n["name"]},
        })
    parents = [{"source": n["parent_id"], "target": n["id"], "kind": "parent", "why": ""}
               for n in nodes if n.get("parent_id") in by_id]
    return {"nodes": out_nodes, "links": parents + links}


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

    # --- Saved side: re-reads of already-memorized files (read tools joined to
    # their _tool_result for the byte cost). Count only files a memory covers.
    reread_sql = f"""
      SELECT r.project_slug AS slug, r.target AS target,
             COUNT(*) AS reads,
             COALESCE(SUM(res.result_tokens), 0) AS tokens
        FROM tool_calls r
        JOIN tool_calls res
          ON res.tool_use_id = r.tool_use_id AND res.tool_name = '_tool_result'
       WHERE r.tool_name {sql_in(READ_TOOLS)} AND r.timestamp >= ?
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
                if base in covered_by_slug.get(_coverage_slug(row["slug"], row["target"]), ""):
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


# --- Mutations: the dashboard's only file-writes, narrowly scoped to the node tree.

def _resolve_memory_file(projects_dir: str, slug: str, filename: str) -> Optional[Path]:
    """`filename` is a path relative to nodes/; reject traversal, index files, non-notes.

    `slug` (the owning hub) is accepted for API compatibility and must prefix the
    path unless it is the global domain.
    """
    if not filename or not filename.endswith(".md") or Path(filename).name in SPECIAL_FILES:
        return None
    rel = Path(filename)
    if rel.is_absolute() or ".." in rel.parts or Path(filename).name.startswith("_"):
        return None
    if slug and slug != GLOBAL_SLUG and not filename.startswith(slug.rstrip("/") + "/"):
        return None
    root = Path(projects_dir).resolve()
    target = (root / rel).resolve()
    if root not in target.parents or not target.is_file():
        return None
    return target


def quarantine_memory(projects_dir: str, slug: str, filename: str) -> dict:
    """Move a note into nodes/.trash/ (reversible) and rebuild the index."""
    target = _resolve_memory_file(projects_dir, slug, filename)
    if target is None:
        return {"ok": False, "error": "not found"}
    trash = Path(projects_dir) / ".trash"
    trash.mkdir(exist_ok=True)
    dest = trash / f"{datetime.utcnow():%Y%m%dT%H%M%S}_{filename.replace('/', '__')}"
    target.rename(dest)
    rebuild_brain(projects_dir)
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
        text = re.sub(r"^(name:.*)$", r"\1\nsource: user", text, count=1, flags=re.MULTILINE)
    target.write_text(text, encoding="utf-8")
    rebuild_brain(projects_dir)
    return {"ok": True}
