# tests/test_launcher.py
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from pathlib import Path

from pr_builder.launcher import (
    RETROARCH_CORES,
    STANDALONE_EMULATORS,
    launch_retroarch,
    launch_standalone,
)


def test_standalone_emulators_map_contains_expected_keys():
    """STANDALONE_EMULATORS must map the platforms defined in the spec."""
    assert "ps2" in STANDALONE_EMULATORS
    assert "ps3" in STANDALONE_EMULATORS
    assert "ps4" in STANDALONE_EMULATORS
    assert "ps5" in STANDALONE_EMULATORS
    assert "gamecube" in STANDALONE_EMULATORS
    assert "wii" in STANDALONE_EMULATORS
    assert "xbox" in STANDALONE_EMULATORS
    assert "xbox360" in STANDALONE_EMULATORS
    assert "switch" in STANDALONE_EMULATORS


@pytest.mark.asyncio
async def test_launch_retroarch_builds_correct_command():
    """launch_retroarch invokes retroarch with -L core and the ROM path."""
    launched: list[tuple] = []

    async def fake_exec(*args, **kwargs):
        launched.append(args)
        proc = MagicMock()
        proc.wait = AsyncMock(return_value=0)
        return proc

    with patch("pr_builder.launcher.asyncio.create_subprocess_exec", side_effect=fake_exec):
        await launch_retroarch(
            core="mupen64plus_next",
            rom_path=Path("/mnt/disc/mario.z64"),
        )

    assert len(launched) == 1
    cmd = launched[0]
    assert "retroarch" in cmd
    assert "-L" in cmd
    assert "mupen64plus_next" in " ".join(str(a) for a in cmd)
    assert "/mnt/disc/mario.z64" in " ".join(str(a) for a in cmd)


@pytest.mark.asyncio
async def test_launch_standalone_ps3():
    """launch_standalone for ps3 invokes rpcs3 with --no-gui and the ROM path."""
    launched: list[tuple] = []

    async def fake_exec(*args, **kwargs):
        launched.append(args)
        proc = MagicMock()
        proc.wait = AsyncMock(return_value=0)
        return proc

    with patch("pr_builder.launcher.asyncio.create_subprocess_exec", side_effect=fake_exec):
        await launch_standalone(
            platform="ps3",
            rom_path=Path("/mnt/disc/game.iso"),
        )

    assert len(launched) == 1
    cmd = launched[0]
    assert "rpcs3" in cmd
    assert "/mnt/disc/game.iso" in cmd


@pytest.mark.asyncio
async def test_launch_standalone_unknown_platform_raises():
    """launch_standalone raises ValueError for an unknown platform."""
    with pytest.raises(ValueError, match="No standalone emulator"):
        await launch_standalone(platform="atari7800", rom_path=Path("/rom.bin"))


@pytest.mark.asyncio
async def test_launch_retroarch_core_path_constructed_correctly():
    """launch_retroarch appends _libretro.so suffix to core name for -L arg."""
    launched: list[tuple] = []

    async def fake_exec(*args, **kwargs):
        launched.append(args)
        proc = MagicMock()
        proc.wait = AsyncMock(return_value=0)
        return proc

    with patch("pr_builder.launcher.asyncio.create_subprocess_exec", side_effect=fake_exec):
        await launch_retroarch(core="bsnes", rom_path=Path("/rom.sfc"))

    cmd_str = " ".join(str(a) for a in launched[0])
    assert "bsnes_libretro.so" in cmd_str


# ── additions: platform names as sent by pr-mediad (it owns them) ────────────

def test_standalone_emulators_accept_pr_mediad_gamecube_name():
    """pr-mediad's platform_db sends GameCube as "gc", not "gamecube"."""
    assert STANDALONE_EMULATORS["gc"] == STANDALONE_EMULATORS["gamecube"]


def test_retroarch_cores_cover_pr_mediad_libretro_platforms():
    """Every pr-mediad platform with a libretro core has a RetroArch default."""
    assert RETROARCH_CORES["n64"] == "mupen64plus_next"
    assert RETROARCH_CORES["ps1"] == "duckstation"
    assert RETROARCH_CORES["snes"] == "bsnes"
    for platform in ("saturn", "dreamcast", "nes", "gb", "gbc", "gba", "nds",
                     "megadrive", "atari2600"):
        assert platform in RETROARCH_CORES
    assert not set(RETROARCH_CORES) & set(STANDALONE_EMULATORS)
