"""Pull Grok text-token rates from the public xAI pricing page into pricing.json.

Uses the below-200k row only. cost_for prices a model's summed tokens, so the
long-context rate cannot be applied per request. Cache-create columns mirror
input: Grok does not emit cache-create buckets.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Optional, Union
from urllib import error, request

from .db import connect
from .pricing import load_pricing

PRICING_URL = "https://docs.x.ai/developers/pricing.md"
_SECTION = "### Text API Pricing"
_ROW = re.compile(
    r"^\|\s*(?P<label>[^|]+?)\s*\|"
    r"\s*[^|$]+\|\s*\$(?P<input>[0-9.]+)\s*\|"
    r"\s*\$(?P<cache>[0-9.]+)\s*\|"
    r"\s*\$(?P<output>[0-9.]+)\s*\|"
)
_FLAGSHIP = re.compile(r"^grok-4\.(\d+)$")
# Sessions record this id; the pricing page lists grok-build-0.1.
_BUILD_ALIAS = {"grok-build": "grok-build-0.1"}


def fetch_pricing_md(url: str = PRICING_URL, timeout: float = 5.0) -> str:
    req = request.Request(url, headers={"User-Agent": "token-dashboard"})
    with request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def parse_text_api_table(md: str) -> dict:
    """model id -> short-context {input, output, cache_read}."""
    start = md.find(_SECTION)
    if start < 0:
        return {}
    rest = md[start + len(_SECTION):]
    end = rest.find("\n### ")
    body = rest if end < 0 else rest[:end]
    grouped: dict[str, list[tuple[str, float, float, float]]] = {}
    for line in body.splitlines():
        m = _ROW.match(line.strip())
        if not m:
            continue
        label = m.group("label").strip()
        model_id = label.split(" (", 1)[0].strip()
        if not model_id.startswith("grok"):
            continue
        grouped.setdefault(model_id, []).append((
            label.lower(),
            float(m.group("input")),
            float(m.group("output")),
            float(m.group("cache")),
        ))
    out = {}
    for model_id, rows in grouped.items():
        short = [r for r in rows if "< 200k" in r[0] or "below 200k" in r[0]]
        pick = short[0] if short else min(rows, key=lambda r: r[1])
        _, inp, output, cache = pick
        out[model_id] = {"input": inp, "output": output, "cache_read": cache}
    return out


def _numbers(spec: dict) -> dict:
    inp = spec["input"]
    return {
        "input": inp,
        "output": spec["output"],
        "cache_read": spec["cache_read"],
        "cache_create_5m": inp,
        "cache_create_1h": inp,
    }


def _model_row(spec: dict) -> dict:
    row = {"tier": "grok"}
    row.update(_numbers(spec))
    return row


_RATE_KEYS = ("input", "output", "cache_read", "cache_create_5m", "cache_create_1h")


def _same_numbers(old: dict, new: dict) -> bool:
    return bool(old) and all(float(old.get(k, -1)) == float(new[k]) for k in _RATE_KEYS)


def _flagship_id(parsed: dict) -> Optional[str]:
    best_id, best_n = None, -1
    for model_id in parsed:
        m = _FLAGSHIP.fullmatch(model_id)
        if m and int(m.group(1)) > best_n:
            best_n = int(m.group(1))
            best_id = model_id
    return best_id


def merge_grok_pricing(pricing: dict, parsed: dict, seen: set[str]) -> list[str]:
    """Upsert official Grok rows. Returns ids/labels that changed. Does not delete."""
    if not parsed:
        return []
    models = pricing.setdefault("models", {})
    changed: list[str] = []
    for model_id, spec in parsed.items():
        row = _model_row(spec)
        old = models.get(model_id)
        if not (_same_numbers(old, row) and old.get("tier") == "grok"):
            models[model_id] = row
            changed.append(model_id)
    for alias, official in _BUILD_ALIAS.items():
        if alias in models and official in parsed:
            row = _model_row(parsed[official])
            old = models.get(alias)
            if not (_same_numbers(old, row) and old.get("tier") == "grok"):
                models[alias] = row
                changed.append(alias)
    flagship = _flagship_id(parsed)
    if flagship:
        row = _numbers(parsed[flagship])
        fallback = pricing.setdefault("tier_fallback", {})
        if not _same_numbers(fallback.get("grok"), row):
            fallback["grok"] = row
            changed.append("tier_fallback.grok")
    page = pricing.setdefault("pricing_page_models", [])
    known_page = set(page)
    for model_id in parsed:
        if model_id in seen and model_id not in known_page and model_id in models:
            page.append(model_id)
            known_page.add(model_id)
            changed.append(model_id)
    return changed


def missing_grok_models(db_path: Union[str, Path], pricing: dict) -> set[str]:
    known = set((pricing.get("models") or {}))
    with connect(db_path) as c:
        rows = c.execute(
            "SELECT DISTINCT model FROM messages WHERE model LIKE 'grok%'"
        ).fetchall()
    return {r["model"] for r in rows if r["model"] not in known}


def _seen_models(db_path: Optional[Union[str, Path]]) -> set[str]:
    if not db_path:
        return set()
    with connect(db_path) as c:
        rows = c.execute(
            "SELECT DISTINCT model FROM messages WHERE model IS NOT NULL"
        ).fetchall()
    return {r["model"] for r in rows}


def sync_pricing(
    path: Union[str, Path],
    db_path: Optional[Union[str, Path]] = None,
    fetch: Callable[[], str] = fetch_pricing_md,
) -> str:
    """Refresh path from the pricing page. Keeps the file when the fetch fails."""
    path = Path(path)
    try:
        parsed = parse_text_api_table(fetch())
    except (error.URLError, TimeoutError, OSError, ValueError):
        return "pricing: kept file (offline)"
    if not parsed:
        return "pricing: kept file (unparsed)"
    pricing = load_pricing(path)
    changed = merge_grok_pricing(pricing, parsed, _seen_models(db_path))
    if not changed:
        return "pricing: unchanged"
    # Unique, stable order — a seen id can also be a rate update.
    labels = list(dict.fromkeys(changed))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(pricing, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return "pricing: updated " + ", ".join(labels)
