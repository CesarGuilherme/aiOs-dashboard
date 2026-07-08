"""Parsing helpers for Claude memory files (MEMORY.md, *.md in memory dirs, LEARNINGS.md).

Extracted from memory.py to keep the main file focused and under size limits.
These are pure (or near-pure) functions that turn text on disk into structured data.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

LINK_RE = re.compile(r"\[\[([^\]\n]+)\]\]")
LEARNING_HEAD_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*[—–-]*\s*(.*)$")

SPECIAL_FILES = {"MEMORY.md", "LEARNINGS.md", "AUDIT.md"}

# The global memory tier (~/.claude/memory/global/) loads in every Claude session
# via the read-path hooks. It lives outside ~/.claude/projects/, so surface it
# here as a pseudo-project ("Global") rather than letting it stay invisible.
GLOBAL_MEM_DIR = Path.home() / ".claude" / "memory" / "global"
GLOBAL_SLUG = "global"


def _iso(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


def _parse_frontmatter(text: str) -> Tuple[dict, str]:
    """Parse the fixed memory-file frontmatter (name/description/metadata.type).

    Hand-rolled on purpose: the format is a closed convention, not arbitrary
    YAML, and this keeps the image dependency-free.
    """
    meta: dict = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return meta, text
    body_start = len(lines)
    in_metadata = False
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body_start = i + 1
            break
        if not line.strip() or ":" not in line:
            continue
        indented = line[0] in " \t"
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not indented:
            in_metadata = key == "metadata"
            if key in ("name", "description") and value:
                meta[key] = value
        elif in_metadata and key in ("type", "source", "origin_session") and value:
            meta[key] = value
    return meta, "\n".join(lines[body_start:]).strip()


def _parse_learnings(text: str) -> List[dict]:
    """Split a LEARNINGS.md journal into dated entries (newest kept first)."""
    entries: List[dict] = []
    current: Optional[dict] = None
    for line in text.splitlines():
        m = LEARNING_HEAD_RE.match(line)
        if m:
            if current:
                current["body"] = current["body"].strip()
                entries.append(current)
            current = {"date": m.group(1), "title": m.group(2).strip(), "body": ""}
        elif current is not None:
            current["body"] += line + "\n"
    if current:
        current["body"] = current["body"].strip()
        entries.append(current)
    return entries


def get_coverage(projects_dir: str) -> dict:
    """project_slug -> lowercase blob of that project's memory text.

    Standalone version of what get_brain builds inline, for callers (tips.py)
    that only need to know whether a file is already memorized.
    """
    root = Path(projects_dir)
    out: dict = {}
    if not root.is_dir():
        return out
    for mem_dir in root.glob("*/memory"):
        texts = []
        for f in mem_dir.glob("*.md"):
            try:
                texts.append(f.read_text(encoding="utf-8", errors="replace").lower())
            except OSError:
                continue
        out[mem_dir.parent.name] = "\n".join(texts)
    return out


def _read_mem_dir(mem_dir: Path, slug: str) -> Tuple[List[dict], List[dict], str]:
    """Parse one memory dir into (entries, learnings, lowercase coverage blob).

    Shared by the project dirs and the global tier so both render identically.
    Report files (AUDIT.md) are skipped entirely — they're not memories and
    would otherwise pollute the graph and the ROI coverage match.
    """
    entries: List[dict] = []
    learnings: List[dict] = []
    covered: List[str] = []
    for f in sorted(mem_dir.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if f.name == "AUDIT.md":
            continue
        covered.append(text.lower())
        if f.name == "MEMORY.md":
            continue
        if f.name == "LEARNINGS.md":
            learnings = _parse_learnings(text)
            continue
        meta, body = _parse_frontmatter(text)
        name = meta.get("name") or f.stem
        entries.append({
            "id": f"{slug}::{name}",
            "name": name,
            "description": meta.get("description", ""),
            "type": meta.get("type", "project"),
            "source": meta.get("source", "user"),
            "origin_session": meta.get("origin_session", ""),
            "body": body,
            "file": f.name,
            "mtime": _iso(f.stat().st_mtime),
            "links": sorted(set(LINK_RE.findall(text))),
        })
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries, learnings, "\n".join(covered)
