# tests/test_registry.py
import pytest
from pathlib import Path
from pr_builder.registry import Registry, DecompEntry, EmuForkEntry


def test_lookup_known_hash(decomp_registry_path, emu_forks_path):
    reg = Registry(decomp_registry_path, emu_forks_path)
    entry = reg.lookup_decomp("20b854b239203baf6c8a71a3e7a166cd")
    assert entry is not None
    assert isinstance(entry, DecompEntry)
    assert entry.id == "sm64-us"
    assert entry.name == "Super Mario 64 (US)"
    assert entry.platform == "n64"
    assert entry.repo == "https://github.com/sm64pc/sm64ex.git"
    assert entry.build_type == "make"
    assert entry.build_args == ["VERSION=us", "-j4"]
    assert entry.deps == ["python", "audiofile", "sdl2"]
    assert entry.retroarch_fallback == "mupen64plus_next"
    assert entry.install_binary == "build/us_pc/sm64.us.f3dex2e"


def test_lookup_unknown_hash(decomp_registry_path, emu_forks_path):
    reg = Registry(decomp_registry_path, emu_forks_path)
    entry = reg.lookup_decomp("deadbeefdeadbeefdeadbeefdeadbeef")
    assert entry is None


def test_lookup_emu_fork_stable(decomp_registry_path, emu_forks_path):
    reg = Registry(decomp_registry_path, emu_forks_path)
    fork = reg.lookup_emu_fork("shadps4", "CUSA00562")
    assert fork is not None
    assert isinstance(fork, EmuForkEntry)
    assert fork.title == "Bloodborne"
    assert fork.stability == "stable"
    assert "shadps4-bloodborne" in fork.fork_repo


def test_lookup_emu_fork_experimental(decomp_registry_path, emu_forks_path):
    reg = Registry(decomp_registry_path, emu_forks_path)
    fork = reg.lookup_emu_fork("shadps4", "CUSA99999")
    assert fork is not None
    assert fork.stability == "experimental"


def test_lookup_emu_fork_missing(decomp_registry_path, emu_forks_path):
    reg = Registry(decomp_registry_path, emu_forks_path)
    fork = reg.lookup_emu_fork("shadps4", "CUSA00000")
    assert fork is None


def test_lookup_emu_fork_unknown_emulator(decomp_registry_path, emu_forks_path):
    reg = Registry(decomp_registry_path, emu_forks_path)
    fork = reg.lookup_emu_fork("nonexistent_emu", "CUSA00562")
    assert fork is None


def test_registry_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        Registry(tmp_path / "missing.json", tmp_path / "also_missing.json")
