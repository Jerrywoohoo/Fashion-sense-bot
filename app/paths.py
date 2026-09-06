"""Filesystem paths used across the application.

Kept in one place so ``bot.py`` (startup) and ``handlers.py`` (runtime) agree
on where things live without importing from each other.
"""
from __future__ import annotations

import os
from pathlib import Path

_default_data_dir = "../data" if Path("../data").exists() else "data"
DATA_DIR = Path(os.getenv("DATA_DIR", _default_data_dir))
IMAGES_DIR = DATA_DIR / "images"


def ensure_data_directories() -> None:
    """Create ``data/`` and ``data/images/`` if they don't already exist."""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)


def resolve_image_path(raw_path: str | Path | None) -> Path | None:
    """Resolve an image path regardless of whether it was stored relative to root or new-model."""
    if not raw_path:
        return None
    p = Path(raw_path)
    if p.is_file():
        return p
    # Look directly in IMAGES_DIR by filename
    in_images = IMAGES_DIR / p.name
    if in_images.is_file():
        return in_images
    # Check possible relative directory locations
    alt_paths = [
        Path("../data/images") / p.name,
        Path("data/images") / p.name,
        Path("../../data/images") / p.name,
    ]
    for alt in alt_paths:
        if alt.is_file():
            return alt
    return None
