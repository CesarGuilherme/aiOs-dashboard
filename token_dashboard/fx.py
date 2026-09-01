"""USD→BRL rate for display. File lives next to the Brain (`~/.brain/.usd_brl`)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Union

DEFAULT_USD_BRL = 5.40
RATE_FILE = Path.home() / ".brain" / ".usd_brl"


def usd_brl_rate(path: Union[str, Path, None] = None) -> float:
    p = Path(path) if path else RATE_FILE
    env = os.environ.get("USD_BRL")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    try:
        raw = p.read_text(encoding="utf-8").strip()
        if raw:
            return float(raw)
    except (OSError, ValueError):
        pass
    return DEFAULT_USD_BRL


def usd_to_brl(usd: float | None, rate: float | None = None) -> float | None:
    if usd is None:
        return None
    r = rate if rate is not None else usd_brl_rate()
    return round(float(usd) * r, 6)


def brl(usd: float | None, rate: float | None = None, digits: int = 2) -> str:
    """Display string for a USD amount, in BRL. Mirrors web/app.js `_brl` exactly —
    same comma decimal separator, same absence of thousands separators — so the two
    frontends can be diffed side by side without cosmetic noise."""
    v = usd_to_brl(usd, rate)
    if v is None:
        return "—"
    return "R$ " + f"{v:.{digits}f}".replace(".", ",")
