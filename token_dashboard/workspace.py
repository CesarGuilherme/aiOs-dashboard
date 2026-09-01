"""Scan agent homes for the agentic layers the Brain rings visualize.

Applications = MCP servers in ~/.claude.json plus [mcp_servers.*] in ~/.grok/config.toml;
Routines = Claude scheduled-tasks + Grok ~/.grok/workflows; Skills = Claude + Grok.
Every missing path yields an empty list — the endpoint must never 500.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path


DEFAULT_ROOTS = "/Volumes/SSD_CESAR/Developer"


def workspace_roots() -> list:
    raw = os.environ.get("WORKSPACE_ROOTS", DEFAULT_ROOTS)
    return [Path(p).expanduser() for p in raw.split(":") if p.strip()]


def allowed_open_path(path_str: str, roots: list, *homes: Path) -> bool:
    try:
        p = Path(path_str).resolve()
    except OSError:
        return False
    for base in list(roots) + list(homes):
        if not base:
            continue
        try:
            base = Path(base).resolve()
        except OSError:
            continue
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


_MCP_HDR = re.compile(r"^\[mcp_servers\.([^\]]+)\]")


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


def _grok_applications(grok_dir: Path) -> list:
    cfg = grok_dir / "config.toml"
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return []
    seen = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _MCP_HDR.match(s)
        if not m:
            continue
        name = m.group(1).split(".", 1)[0]
        if name:
            seen[name] = {"name": name, "scope": "grok"}
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


def _grok_routines(grok_dir: Path) -> list:
    out = []
    for root in (grok_dir / "workflows", grok_dir / "bundled" / "workflows"):
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.name.startswith("."):
                continue
            if child.suffix == ".rhai":
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


def _grok_skills(grok_dir: Path) -> list:
    seen = {}
    if not grok_dir.is_dir():
        return []
    for root_name, label in (("skills", "grok-user"), ("bundled/skills", "grok-bundled")):
        root = grok_dir / root_name if root_name != "bundled/skills" else grok_dir / "bundled" / "skills"
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                seen[child.name] = {"name": child.name, "source": label}
    for skill_dir in grok_dir.glob("installed-plugins/*/skills/*"):
        if skill_dir.is_dir():
            seen.setdefault(skill_dir.name, {"name": skill_dir.name, "source": "grok-plugin"})
    return sorted(seen.values(), key=lambda s: s["name"])


def _merge_named(base: list, extra: list, key: str = "name") -> list:
    have = {x[key] for x in base}
    out = list(base)
    for item in extra:
        if item[key] not in have:
            out.append(item)
            have.add(item[key])
    return sorted(out, key=lambda x: x[key])


def scan_workspace(claude_dir: Path, grok_dir: Path | None = None) -> dict:
    skills = _skills(claude_dir)
    apps = _applications(claude_dir)
    routines = _routines(claude_dir)
    if grok_dir is not None:
        skills = _merge_named(skills, _grok_skills(grok_dir))
        apps = _merge_named(apps, _grok_applications(grok_dir))
        routines = _merge_named(routines, _grok_routines(grok_dir))
    return {
        "applications": apps,
        "routines": routines,
        "skills": skills,
    }
