# src/pr_builder/launcher.py
"""RetroArch and standalone emulator launch."""
from __future__ import annotations

import asyncio
from pathlib import Path

# Retroarch core library directory on Arch/CachyOS
_RA_CORE_DIR = "/usr/lib/libretro"

# Map platform name → standalone emulator executable
STANDALONE_EMULATORS: dict[str, list[str]] = {
    "ps2":      ["pcsx2", "--no-gui"],
    "ps3":      ["rpcs3", "--no-gui"],
    "ps4":      ["shadps4"],
    "ps5":      ["kytyps5"],
    "gamecube": ["dolphin-emu", "--batch"],
    "gc":       ["dolphin-emu", "--batch"],  # pr-mediad's platform name for GameCube
    "wii":      ["dolphin-emu", "--batch"],
    "xbox":     ["xemu", "-dvd_path"],
    "xbox360":  ["xenia-canary"],
    "switch":   ["eden", "-g"],
}

# Default libretro core per platform, for platforms without a standalone
# emulator. Platform names and cores mirror pr-mediad's platform_db.c table.
RETROARCH_CORES: dict[str, str] = {
    "ps1":       "duckstation",
    "n64":       "mupen64plus_next",
    "saturn":    "mednafen_saturn",
    "dreamcast": "flycast",
    "nes":       "mesen",
    "snes":      "bsnes",
    "gb":        "mgba",
    "gbc":       "mgba",
    "gba":       "mgba",
    "nds":       "melonds",
    "megadrive": "genesis_plus_gx",
    "atari2600": "stella2014",
}


async def launch_retroarch(core: str, rom_path: Path) -> None:
    """Launch RetroArch with the given libretro core and ROM.

    core: bare core name, e.g. "mupen64plus_next" — the function appends
          _libretro.so and prepends the core directory path.
    """
    core_lib = f"{_RA_CORE_DIR}/{core}_libretro.so"
    cmd = ["retroarch", "-L", core_lib, "--", str(rom_path)]
    proc = await asyncio.create_subprocess_exec(*cmd)
    await proc.wait()


async def launch_standalone(platform: str, rom_path: Path) -> None:
    """Launch the platform-appropriate standalone emulator.

    Raises ValueError if the platform has no configured standalone emulator.
    """
    if platform not in STANDALONE_EMULATORS:
        raise ValueError(
            f"No standalone emulator configured for platform: {platform!r}. "
            f"Known platforms: {sorted(STANDALONE_EMULATORS)}"
        )
    base_cmd = STANDALONE_EMULATORS[platform]
    cmd = base_cmd + [str(rom_path)]
    proc = await asyncio.create_subprocess_exec(*cmd)
    await proc.wait()
