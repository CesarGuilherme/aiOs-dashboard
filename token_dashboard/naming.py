"""Centralized project/slug/cwd name handling and pretty-printing.

Extracted to eliminate duplication between db.py, memory.py and tips.py.
All project name resolution should go through here.
"""

from __future__ import annotations

import re
from typing import List, Optional


def _encode_slug(path: str) -> str:
    """Claude Code's project-slug encoding: each of `:`, `\\`, `/`, space → one `-`."""
    return re.sub(r"[:\\/ ]", "-", path)


def _walk_to_root(cwd: str, slug: str) -> Optional[str]:
    """If any ancestor of cwd encodes to slug, return that ancestor's basename."""
    if not cwd or not slug:
        return None
    trimmed = cwd.rstrip("/\\")
    sep = "\\" if "\\" in trimmed else "/"
    parts = trimmed.split(sep)
    for i in range(len(parts), 0, -1):
        if _encode_slug(sep.join(parts[:i])) == slug:
            name = parts[i - 1]
            if name:
                return name
    return None


def project_name_for(cwd: Optional[str], fallback_slug: str) -> str:
    """Pretty project name from a single cwd + slug (best-effort).

    For the multi-cwd case, prefer `best_project_name`.
    """
    name = _walk_to_root(cwd or "", fallback_slug or "")
    if name:
        return name
    if cwd:
        trimmed = cwd.rstrip("/\\")
        sep = "\\" if "\\" in trimmed else "/"
        tail = trimmed.split(sep)[-1]
        if tail:
            return tail
    if fallback_slug:
        parts = [p for p in re.split(r"-+", fallback_slug) if p]
        if parts:
            return parts[-1]
    return fallback_slug or ""


def best_project_name(cwds, slug: str) -> str:
    """Pick a pretty name from a list of cwds.

    Prefer a cwd whose walk-up matches `slug` (a true descendant of the project
    root). If none match, fall back to `project_name_for` on the first cwd,
    then to the slug's last segment.
    """
    cwds = [c for c in (cwds or []) if c]
    for cwd in cwds:
        name = _walk_to_root(cwd, slug)
        if name:
            return name
    return project_name_for(cwds[0] if cwds else None, slug)


def _encode_cwd(cwd: str) -> str:
    """Claude Code's project-dir encoding: every non-alphanumeric char -> '-'.

    The slugs the Brain derives from dir names use this encoding, but the
    scanner's stored `messages.project_slug` can differ.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", cwd or "")


def get_labels(db_path: str, slugs: List[str]) -> dict:
    """Pretty names from the transcript db's cwd records; slug tail as fallback.

    Matches cwds to slugs by re-encoding the cwd (not by stored project_slug),
    so projects whose on-disk path contains characters the scanner encoded
    differently still get a real name.
    """
    labels = {s: (re.split(r"-+", s.strip("-")) or [s])[-1] for s in slugs}
    slugset = set(slugs)
    try:
        from .db import connect
        with connect(db_path) as c:
            cwds_by_slug: dict = {}
            for r in c.execute("SELECT DISTINCT cwd FROM messages WHERE cwd IS NOT NULL"):
                enc = _encode_cwd(r["cwd"])
                if enc in slugset:
                    cwds_by_slug.setdefault(enc, []).append(r["cwd"])
            for slug, cwds in cwds_by_slug.items():
                if cwds:
                    labels[slug] = best_project_name(cwds, slug)
    except Exception:
        pass
    return labels
