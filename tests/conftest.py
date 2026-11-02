# tests/conftest.py
import json
import pytest
from pathlib import Path


SAMPLE_DECOMP_REGISTRY = {
    "games": {
        "20b854b239203baf6c8a71a3e7a166cd": {
            "id": "sm64-us",
            "name": "Super Mario 64 (US)",
            "platform": "n64",
            "repo": "https://github.com/sm64pc/sm64ex.git",
            "build_type": "make",
            "build_args": ["VERSION=us", "-j4"],
            "asset_extractor": "extract_assets.py",
            "install_binary": "build/us_pc/sm64.us.f3dex2e",
            "deps": ["python", "audiofile", "sdl2"],
            "retroarch_fallback": "mupen64plus_next",
        }
    }
}

SAMPLE_EMU_FORKS_REGISTRY = {
    "shadps4": {
        "CUSA00562": {
            "title": "Bloodborne",
            "fork_repo": "https://github.com/somedev/shadps4-bloodborne.git",
            "fork_reason": "60fps patches",
            "stability": "stable",
        },
        "CUSA99999": {
            "title": "FakeGame",
            "fork_repo": "https://github.com/dev/shadps4-fake.git",
            "fork_reason": "test fork",
            "stability": "experimental",
        },
    },
    "kytyps5": {
        "PPSA01284": {
            "title": "Demon's Souls Remake",
            "fork_repo": "https://github.com/somedev/kyty-demons-souls.git",
            "fork_reason": "translation patches",
            "stability": "experimental",
        }
    },
}


@pytest.fixture()
def decomp_registry_path(tmp_path: Path) -> Path:
    p = tmp_path / "decomp_registry.json"
    p.write_text(json.dumps(SAMPLE_DECOMP_REGISTRY))
    return p


@pytest.fixture()
def emu_forks_path(tmp_path: Path) -> Path:
    p = tmp_path / "emu_forks_registry.json"
    p.write_text(json.dumps(SAMPLE_EMU_FORKS_REGISTRY))
    return p
