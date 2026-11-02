# tests/test_library.py
import json
import pytest
from pathlib import Path

from pr_builder.library import install_native, write_emu_meta, GameMeta


def test_install_native_creates_directory(tmp_path: Path):
    """install_native copies binary and writes pr-meta.json with badge=NATIVE."""
    games_root = tmp_path / "games"
    fake_binary = tmp_path / "sm64.us.f3dex2e"
    fake_binary.write_bytes(b"\x7fELF fake binary")

    meta = install_native(
        games_root=games_root,
        platform="n64",
        title="Super Mario 64",
        binary_src=fake_binary,
    )

    game_dir = games_root / "n64" / "Super Mario 64"
    assert game_dir.is_dir()
    installed_binary = game_dir / "sm64.us.f3dex2e"
    assert installed_binary.exists()
    assert installed_binary.read_bytes() == b"\x7fELF fake binary"


def test_install_native_writes_meta_json(tmp_path: Path):
    """install_native writes valid pr-meta.json with badge=NATIVE."""
    games_root = tmp_path / "games"
    fake_binary = tmp_path / "game.bin"
    fake_binary.write_bytes(b"bin")

    install_native(
        games_root=games_root,
        platform="n64",
        title="Super Mario 64",
        binary_src=fake_binary,
    )

    meta_path = games_root / "n64" / "Super Mario 64" / "pr-meta.json"
    assert meta_path.exists()
    data = json.loads(meta_path.read_text())
    assert data["badge"] == "NATIVE"
    assert data["platform"] == "n64"
    assert data["title"] == "Super Mario 64"
    assert data["core"] is None
    assert data["binary"].endswith("game.bin")


def test_install_native_returns_game_meta(tmp_path: Path):
    games_root = tmp_path / "games"
    fake_binary = tmp_path / "game.bin"
    fake_binary.write_bytes(b"bin")

    result = install_native(
        games_root=games_root,
        platform="n64",
        title="Super Mario 64",
        binary_src=fake_binary,
    )

    assert isinstance(result, GameMeta)
    assert result.badge == "NATIVE"
    assert result.platform == "n64"


def test_write_emu_meta_creates_emu_entry(tmp_path: Path):
    """write_emu_meta writes pr-meta.json with badge=EMU and core name."""
    games_root = tmp_path / "games"

    meta = write_emu_meta(
        games_root=games_root,
        platform="ps1",
        title="Crash Bandicoot",
        core="duckstation",
        rom_path=Path("/mnt/disc/crash.cue"),
    )

    meta_path = games_root / "ps1" / "Crash Bandicoot" / "pr-meta.json"
    data = json.loads(meta_path.read_text())
    assert data["badge"] == "EMU"
    assert data["core"] == "duckstation"
    assert data["platform"] == "ps1"
    assert meta.badge == "EMU"


def test_install_native_idempotent(tmp_path: Path):
    """Calling install_native twice on the same title overwrites without error."""
    games_root = tmp_path / "games"
    fake_binary = tmp_path / "game.bin"
    fake_binary.write_bytes(b"v1")

    install_native(games_root, "n64", "Mario", fake_binary)

    fake_binary.write_bytes(b"v2")
    install_native(games_root, "n64", "Mario", fake_binary)

    installed = games_root / "n64" / "Mario" / "game.bin"
    assert installed.read_bytes() == b"v2"
