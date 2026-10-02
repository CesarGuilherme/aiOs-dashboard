"""Read side of the Second Brain: ~/.brain/brain.db, the derived index of ~/.brain/nodes.

The Brain's source is a Markdown tree (domain → project → topic → memory) that
`~/.brain/scripts/brain.py rebuild` compiles into brain.db. The dashboard only
reads that DB; the tables it relies on are the contract:

  nodes(id, parent_id, kind, type, name, summary, body, file, workspaces,
        status, source, date, mtime, depth, size)
      kind ∈ domain|project|topic|memory; type 'learning' = a LEARNINGS.md entry;
      workspaces = JSON list of repo paths (project hubs only); size = descendants
  edges(src, dst, kind, why)      kind ∈ link|same-solution|reuses|depends-on|supersedes|related

Parent/child structure comes from nodes.parent_id, not from edges.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

GLOBAL_SLUG = "global"
SPECIAL_FILES = {"MEMORY.md", "LEARNINGS.md", "AUDIT.md", "README.md", "CLAUDE.md", "INDEX.md"}


def brain_root() -> Path:
    return Path(os.environ.get("BRAIN_DIR", Path.home() / ".brain")).expanduser()


def memory_projects_dir(fallback: str) -> str:
    """The Brain's node tree (`~/.brain/nodes`), else the caller's path (tests)."""
    p = brain_root() / "nodes"
    return str(p) if p.is_dir() else fallback


def brain_db_for(nodes_dir: str) -> Path:
    """brain.db sits next to the nodes/ tree it indexes."""
    return Path(nodes_dir).parent / "brain.db"


def _iso(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


def _parse_frontmatter(text: str) -> Tuple[dict, str]:
    """Flat `key: value` frontmatter; nested legacy `metadata:` keys are flattened."""
    meta: dict = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return meta, text
    body_start = len(lines)
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body_start = i + 1
            break
        key, sep, value = line.partition(":")
        if sep and value.strip() and key.strip() not in meta:
            meta[key.strip()] = value.strip().strip("\"'")
    return meta, "\n".join(lines[body_start:]).strip()


def _stale(nodes_dir: Path, db: Path) -> bool:
    """Same rule as brain.py `stale`: any .md or folder under nodes/ newer than brain.db
    (hand edits and cp/mv/rm never reach the PostToolUse hook)."""
    if not nodes_dir.is_dir():
        return False
    if not db.is_file():
        return True
    built = db.stat().st_mtime
    for root, dirs, files in os.walk(nodes_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if os.stat(root).st_mtime > built:
            return True
        if any(f.endswith(".md") and os.stat(os.path.join(root, f)).st_mtime > built for f in files):
            return True
    return False


def rebuild_brain(nodes_dir: str) -> None:
    """Run `brain.py rebuild` for this tree; best-effort (tests have no brain.py)."""
    root = Path(nodes_dir).parent
    script = root / "scripts" / "brain.py"
    if not script.is_file():
        script = brain_root() / "scripts" / "brain.py"
    if script.is_file():
        subprocess.run([sys.executable, str(script), "rebuild"], env={**os.environ, "BRAIN_DIR": str(root)},
                       capture_output=True, timeout=60, check=False)


def load_tree(db: Path) -> Tuple[List[dict], List[dict]]:
    """(nodes, edges) from brain.db, rebuilt first if the tree changed under it.

    Empty when the Brain has never been built.
    """
    if _stale(Path(db).parent / "nodes", Path(db)):
        rebuild_brain(str(Path(db).parent / "nodes"))
    if not Path(db).is_file():
        return [], []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        nodes = [dict(r) for r in con.execute("SELECT * FROM nodes")]
        edges = [dict(r) for r in con.execute("SELECT * FROM edges")]
    finally:
        con.close()
    for n in nodes:
        n["workspaces"] = json.loads(n.get("workspaces") or "[]")
    return nodes, edges


def owner_of(node: dict, by_id: dict) -> Optional[dict]:
    """Nearest project ancestor (or self); else the top domain (e.g. global)."""
    cur, top = node, node
    while cur:
        if cur["kind"] == "project":
            return cur
        top = cur
        cur = by_id.get(cur["parent_id"]) if cur.get("parent_id") else None
    return top


def coverage_from_tree(nodes: List[dict]) -> dict:
    """project_slug (Claude-encoded cwd) -> lowercase blob of that project's memory text.

    A hub is reachable under every workspace it lists; the global domain under
    GLOBAL_SLUG. This is what "is this file already memorized?" looks up.
    """
    from .naming import _encode_cwd  # local: keeps this module import-light

    by_id = {n["id"]: n for n in nodes}
    blobs: dict = {}
    for n in nodes:
        owner = owner_of(n, by_id)
        if owner is None:
            continue
        text = f"{n['name']}\n{n.get('summary') or ''}\n{n.get('body') or ''}".lower()
        blobs.setdefault(owner["id"], []).append(text)
    out: dict = {}
    for owner_id, parts in blobs.items():
        owner = by_id[owner_id]
        blob = "\n".join(parts)
        keys = [_encode_cwd(w) for w in owner.get("workspaces") or []]
        if owner["kind"] == "domain" and owner["id"] == GLOBAL_SLUG:
            keys.append(GLOBAL_SLUG)
        for k in keys:
            out[k] = (out.get(k, "") + "\n" + blob).strip()
    return out


def get_coverage(projects_dir: str) -> dict:
    """Coverage map for callers (tips.py) that only need "already memorized?"."""
    nodes, _ = load_tree(brain_db_for(projects_dir))
    return coverage_from_tree(nodes)
