# src/pr_builder/library.py
"""~/games/ directory management and pr-meta.json writer."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class GameMeta:
    title: str
    platform: str
    badge: str  # "NATIVE" | "EMU" | "BUILDING" | "BUILD ERROR"
    core: Optional[str]
    binary: Optional[str]


def install_native(
    games_root: Path,
    platform: str,
    title: str,
    binary_src: Path,
) -> GameMeta:
    """Copy native binary into ~/games/{platform}/{title}/ and write pr-meta.json.

    Returns the GameMeta that was written.
    """
    game_dir = _ensure_game_dir(games_root, platform, title)
    dest_binary = game_dir / binary_src.name
    shutil.copy2(binary_src, dest_binary)

    meta = GameMeta(
        title=title,
        platform=platform,
        badge="NATIVE",
        core=None,
        binary=f"./{binary_src.name}",
    )
    _write_meta_json(game_dir, meta)
    return meta


def write_emu_meta(
    games_root: Path,
    platform: str,
    title: str,
    core: str,
    rom_path: Path,
) -> GameMeta:
    """Write pr-meta.json with badge=EMU for emulated titles.

    The ROM file is not copied — only metadata is written so ES-DE can
    display the correct badge and know which core to use.
    """
    game_dir = _ensure_game_dir(games_root, platform, title)

    meta = GameMeta(
        title=title,
        platform=platform,
        badge="EMU",
        core=core,
        binary=str(rom_path),
    )
    _write_meta_json(game_dir, meta)
    return meta


def game_dir_for(games_root: Path, platform: str, title: str) -> Path:
    """Return ~/games/{platform}/{title}/ without creating it."""
    return games_root / platform / title


# ── helpers ──────────────────────────────────────────────────────────────────

def _ensure_game_dir(games_root: Path, platform: str, title: str) -> Path:
    game_dir = game_dir_for(games_root, platform, title)
    game_dir.mkdir(parents=True, exist_ok=True)
    return game_dir


def _write_meta_json(game_dir: Path, meta: GameMeta) -> None:
    data = {
        "title": meta.title,
        "platform": meta.platform,
        "badge": meta.badge,
        "core": meta.core,
        "binary": meta.binary,
        "image": None,
        "video": None,
    }
    (game_dir / "pr-meta.json").write_text(json.dumps(data, indent=2))
