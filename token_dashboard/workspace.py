"""Scan ~/.claude for the agentic layers the Brain rings visualize.

Applications = MCP servers in ~/.claude.json; Routines = scheduled tasks;
Skills = user skills + plugin-cache skills. Every missing path yields an
empty list — the endpoint must never 500 on a bare machine.
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__"}
MAX_FILES_PER_DEPT = 2000
DEFAULT_ROOTS = "/Volumes/SSD_CESAR/Developer"


def workspace_roots() -> list:
    raw = os.environ.get("WORKSPACE_ROOTS", DEFAULT_ROOTS)
    return [Path(p).expanduser() for p in raw.split(":") if p.strip()]


def _dept_files(dept_dir: Path) -> list:
    out = []
    for dirpath, dirnames, filenames in os.walk(dept_dir):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("."):
                continue
            p = Path(dirpath) / fn
            try:
                st = p.stat()
            except OSError:
                continue
            out.append({
                "name": fn,
                "path": str(p),
                "rel": str(p.relative_to(dept_dir)),
                "dept": dept_dir.name,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                "ext": p.suffix.lstrip(".").lower(),
            })
    out.sort(key=lambda f: f["size"], reverse=True)
    return out[:MAX_FILES_PER_DEPT]


def _files(roots: list) -> list:
    out = []
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                out.extend(_dept_files(child))
    return out


def allowed_open_path(path_str: str, roots: list, claude_dir: Path) -> bool:
    try:
        p = Path(path_str).resolve()
    except OSError:
        return False
    for base in list(roots) + [claude_dir]:
        base = base.resolve()
        if p == base or base in p.parents:
            return True
    return False


def open_on_device(path_str: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", path_str], check=False)
    elif os.name == "nt":
        os.startfile(path_str)  # noqa — windows only
    else:
        subprocess.run(["xdg-open", path_str], check=False)


def _applications(claude_dir: Path) -> list:
    cfg_path = claude_dir.parent / ".claude.json"
    try:
        cfg = json.loads(cfg_path.read_text())
    except (OSError, ValueError):
        return []
    seen = {}
    for proj_path, proj in (cfg.get("projects") or {}).items():
        for name in (proj.get("mcpServers") or {}):
            seen[name] = {"name": name, "scope": Path(proj_path).name}
    for name in (cfg.get("mcpServers") or {}):
        seen[name] = {"name": name, "scope": "global"}   # global wins
    return sorted(seen.values(), key=lambda a: a["name"])


def _routines(claude_dir: Path) -> list:
    root = claude_dir / "scheduled-tasks"
    if not root.is_dir():
        return []
    out = []
    for child in sorted(root.iterdir()):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            out.append({"name": child.name})
        elif child.suffix == ".md":
            out.append({"name": child.stem})
    return out


def _skills(claude_dir: Path) -> list:
    seen = {}
    for skill_dir in claude_dir.glob("plugins/cache/*/*/*/skills/*"):
        if skill_dir.is_dir():
            seen[skill_dir.name] = {"name": skill_dir.name, "source": "plugin"}
    user_root = claude_dir / "skills"
    if user_root.is_dir():
        for child in sorted(user_root.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                seen[child.name] = {"name": child.name, "source": "user"}  # user wins
    return sorted(seen.values(), key=lambda s: s["name"])


def scan_workspace(claude_dir: Path, roots=None) -> dict:
    if roots is None:
        roots = workspace_roots()
    return {
        "applications": _applications(claude_dir),
        "routines": _routines(claude_dir),
        "skills": _skills(claude_dir),
        "files": _files(roots),
    }
