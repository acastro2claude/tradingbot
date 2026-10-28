"""Load research/settings.toml."""

from __future__ import annotations

import tomllib
from functools import lru_cache
from pathlib import Path

SETTINGS_PATH = Path(__file__).with_name("settings.toml")


@lru_cache(maxsize=None)
def load_settings(path: str | None = None) -> dict:
    with open(path or SETTINGS_PATH, "rb") as f:
        return tomllib.load(f)


def ticks(points: float, settings: dict | None = None) -> int:
    """Convert a distance in points to whole ticks."""
    s = settings or load_settings()
    return int(round(points / s["market"]["tick_size"]))
