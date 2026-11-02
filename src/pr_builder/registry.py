# src/pr_builder/registry.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class DecompEntry:
    id: str
    name: str
    platform: str
    repo: str
    build_type: str  # "make" | "cmake"
    build_args: list[str]
    asset_extractor: Optional[str]
    install_binary: str
    deps: list[str]
    retroarch_fallback: str


@dataclass(frozen=True)
class EmuForkEntry:
    title: str
    fork_repo: str
    fork_reason: str
    stability: str  # "stable" | "experimental"


class Registry:
    """Wraps decomp_registry.json and emu_forks_registry.json lookups."""

    def __init__(self, decomp_path: Path, emu_forks_path: Path) -> None:
        if not decomp_path.exists():
            raise FileNotFoundError(f"decomp registry not found: {decomp_path}")
        if not emu_forks_path.exists():
            raise FileNotFoundError(f"emu forks registry not found: {emu_forks_path}")

        raw_decomp = json.loads(decomp_path.read_text())
        raw_forks = json.loads(emu_forks_path.read_text())

        self._decomp: dict[str, DecompEntry] = {
            rom_hash: DecompEntry(
                id=data["id"],
                name=data["name"],
                platform=data["platform"],
                repo=data["repo"],
                build_type=data["build_type"],
                build_args=data.get("build_args", []),
                asset_extractor=data.get("asset_extractor"),
                install_binary=data["install_binary"],
                deps=data.get("deps", []),
                retroarch_fallback=data.get("retroarch_fallback", ""),
            )
            for rom_hash, data in raw_decomp.get("games", {}).items()
        }

        self._forks: dict[str, dict[str, EmuForkEntry]] = {
            emu: {
                game_id: EmuForkEntry(
                    title=entry["title"],
                    fork_repo=entry["fork_repo"],
                    fork_reason=entry["fork_reason"],
                    stability=entry["stability"],
                )
                for game_id, entry in games.items()
            }
            for emu, games in raw_forks.items()
        }

    def lookup_decomp(self, rom_hash: str) -> Optional[DecompEntry]:
        """Return DecompEntry for the given ROM MD5 hash, or None."""
        return self._decomp.get(rom_hash)

    def lookup_emu_fork(self, emulator: str, game_id: str) -> Optional[EmuForkEntry]:
        """Return EmuForkEntry for (emulator, game_id), or None."""
        return self._forks.get(emulator, {}).get(game_id)
