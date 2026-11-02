# tests/test_server.py
"""Pipeline tests for server.py. Every external effect (pacman, git, builds,
emulators, pr-maestro, pr-meta) is mocked at the pr_builder.server boundary."""
import asyncio
import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pr_builder.builder import BuildResult
from pr_builder.maestro_bridge import DiagnosisResult, FilePatch
from pr_builder.server import SOCKET_PATH, Server

SM64_HASH = "20b854b239203baf6c8a71a3e7a166cd"
SM64_TITLE = "Super Mario 64 (US)"


def media_event(**overrides) -> dict:
    event = {
        "type": "media_inserted",
        "platform": "n64",
        "device": "/dev/sr0",
        "mount_point": "/mnt/disc",
        "rom_hash": SM64_HASH,
    }
    event.update(overrides)
    return event


@pytest.fixture()
def server(tmp_path, sock_dir, decomp_registry_path, emu_forks_path) -> Server:
    return Server(
        socket_path=str(sock_dir / "pr-builder.sock"),
        games_root=tmp_path / "games",
        decomp_registry=decomp_registry_path,
        emu_forks_registry=emu_forks_path,
        cache_root=tmp_path / "cache",
    )


@pytest.fixture()
def mocks():
    """Patch every side-effecting collaborator imported by server.py."""
    fakes = {
        "ensure_deps": AsyncMock(return_value=None),
        "_clone_if_needed": AsyncMock(return_value=None),
        "run_build": AsyncMock(),
        "diagnose_build_failure": AsyncMock(return_value=None),
        "apply_file_patch": MagicMock(return_value=False),
        "launch_retroarch": AsyncMock(return_value=None),
        "launch_standalone": AsyncMock(return_value=None),
        "notify_game_installed": AsyncMock(return_value=True),
        "notify_badge_update": AsyncMock(return_value=True),
    }
    with ExitStack() as stack:
        for name, fake in fakes.items():
            stack.enter_context(patch(f"pr_builder.server.{name}", new=fake))
        yield fakes


def fake_binary(tmp_path: Path) -> Path:
    p = tmp_path / "cache" / "sm64-us" / "build" / "us_pc" / "sm64.us.f3dex2e"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x7fELF")
    return p


def test_default_socket_path_matches_pr_mediad():
    assert SOCKET_PATH == "/run/project-r/pr-builder.sock"


def test_default_registry_paths_point_at_bundled_files():
    """Regression: the plan's _REPO_ROOT went one directory too far up."""
    from pr_builder.server import DECOMP_REGISTRY_PATH, EMU_FORKS_REGISTRY_PATH
    from pr_builder.registry import Registry

    reg = Registry(DECOMP_REGISTRY_PATH, EMU_FORKS_REGISTRY_PATH)
    assert reg.lookup_decomp(SM64_HASH).id == "sm64-us"
    assert reg.lookup_emu_fork("shadps4", "CUSA00562").stability == "stable"


async def test_unknown_event_type_is_ignored(server, mocks):
    await server._handle_event({"type": "media_removed", "device": "/dev/sr0"})
    mocks["run_build"].assert_not_called()
    mocks["launch_retroarch"].assert_not_called()
    mocks["launch_standalone"].assert_not_called()


async def test_unknown_hash_launches_retroarch_default_core(server, mocks):
    await server._handle_event(media_event(rom_hash="deadbeefdeadbeefdeadbeefdeadbeef"))
    mocks["run_build"].assert_not_called()
    mocks["launch_retroarch"].assert_awaited_once_with("mupen64plus_next", Path("/mnt/disc"))


async def test_empty_hash_from_pr_mediad_goes_to_emulator(server, mocks):
    """pr-mediad v0.1 sends rom_hash="" (MD5 not computed yet)."""
    await server._handle_event(media_event(rom_hash=""))
    mocks["ensure_deps"].assert_not_called()
    mocks["launch_retroarch"].assert_awaited_once()


async def test_gc_platform_uses_standalone_dolphin(server, mocks):
    await server._handle_event(media_event(platform="gc", rom_hash=""))
    mocks["launch_standalone"].assert_awaited_once_with("gc", Path("/mnt/disc"))


async def test_known_hash_builds_installs_native_and_notifies_meta(server, mocks, tmp_path):
    binary = fake_binary(tmp_path)

    async def build(entry, src_dir, progress):
        for pct in (0, 10, 10, 14):  # duplicate pct must not be re-sent
            await progress("BUILDING", pct)
        return BuildResult(success=True, binary_path=binary)

    mocks["run_build"].side_effect = build
    await server._handle_event(media_event())

    mocks["ensure_deps"].assert_awaited_once_with(["python", "audiofile", "sdl2"])
    game_dir = tmp_path / "games" / "n64" / SM64_TITLE
    assert (game_dir / "sm64.us.f3dex2e").read_bytes() == b"\x7fELF"
    assert json.loads((game_dir / "pr-meta.json").read_text())["badge"] == "NATIVE"

    progress = [c.kwargs["progress"] for c in mocks["notify_badge_update"].await_args_list]
    assert progress == [0, 10, 14]
    for c in mocks["notify_badge_update"].await_args_list:
        assert c.args[:4] == ("n64", SM64_TITLE, game_dir, "BUILDING")

    mocks["notify_game_installed"].assert_awaited_once_with(
        "n64", SM64_TITLE, game_dir, "NATIVE", None, "./sm64.us.f3dex2e", SM64_HASH
    )
    mocks["launch_retroarch"].assert_not_called()
    mocks["diagnose_build_failure"].assert_not_called()


async def test_progress_notifications_stop_after_pr_meta_unavailable(server, mocks, tmp_path):
    binary = fake_binary(tmp_path)
    mocks["notify_badge_update"].return_value = False

    async def build(entry, src_dir, progress):
        for pct in (0, 10, 20, 30):
            await progress("BUILDING", pct)
        return BuildResult(success=True, binary_path=binary)

    mocks["run_build"].side_effect = build
    await server._handle_event(media_event())
    assert mocks["notify_badge_update"].await_count == 1


async def test_build_failure_applies_maestro_patch_and_retries(server, mocks, tmp_path):
    binary = fake_binary(tmp_path)
    mocks["run_build"].side_effect = [
        BuildResult(success=False, stderr="ld: cannot find -laudiofile"),
        BuildResult(success=True, binary_path=binary),
    ]
    fix = FilePatch("Makefile", 42, "-laudiofile", "$(shell pkg-config --libs audiofile)")
    mocks["diagnose_build_failure"].return_value = DiagnosisResult(
        patch=fix, explanation="link flags", confidence=0.87
    )
    mocks["apply_file_patch"].return_value = True
    await server._handle_event(media_event())

    project_id, stderr, log_path = mocks["diagnose_build_failure"].await_args.args
    assert (project_id, stderr) == ("sm64-us", "ld: cannot find -laudiofile")
    assert Path(log_path).read_text() == "ld: cannot find -laudiofile"
    mocks["apply_file_patch"].assert_called_once_with(fix, tmp_path / "cache" / "sm64-us")
    assert mocks["run_build"].await_count == 2
    assert mocks["notify_game_installed"].await_args.args[3] == "NATIVE"
    mocks["launch_retroarch"].assert_not_called()


async def test_build_failure_without_maestro_falls_back_to_emu(server, mocks, tmp_path):
    mocks["run_build"].return_value = BuildResult(success=False, stderr="boom")
    mocks["diagnose_build_failure"].return_value = None  # pr-maestro down

    await server._handle_event(media_event())

    assert mocks["run_build"].await_count == 1
    game_dir = tmp_path / "games" / "n64" / SM64_TITLE
    meta = json.loads((game_dir / "pr-meta.json").read_text())
    assert (meta["badge"], meta["core"]) == ("EMU", "mupen64plus_next")
    mocks["notify_game_installed"].assert_awaited_once_with(
        "n64", SM64_TITLE, game_dir, "EMU", "mupen64plus_next", "/dev/sr0", SM64_HASH
    )
    mocks["launch_retroarch"].assert_awaited_once_with("mupen64plus_next", Path("/mnt/disc"))


async def test_patch_that_cannot_be_applied_is_not_retried(server, mocks):
    mocks["run_build"].return_value = BuildResult(success=False, stderr="boom")
    mocks["diagnose_build_failure"].return_value = DiagnosisResult(
        patch=FilePatch("Makefile", 1, "x", "y"), explanation="", confidence=0.3
    )
    await server._handle_event(media_event())  # apply_file_patch mocked → False
    assert mocks["run_build"].await_count == 1
    mocks["launch_retroarch"].assert_awaited_once()


async def test_deps_failure_falls_back_without_building(server, mocks):
    mocks["ensure_deps"].side_effect = RuntimeError("pacman failed (exit 1)")
    await server._handle_event(media_event())
    mocks["run_build"].assert_not_called()
    assert mocks["notify_game_installed"].await_args.args[3] == "EMU"
    mocks["launch_retroarch"].assert_awaited_once_with("mupen64plus_next", Path("/mnt/disc"))


async def test_clone_failure_falls_back_without_building(server, mocks):
    mocks["_clone_if_needed"].side_effect = RuntimeError("git clone failed")
    await server._handle_event(media_event())
    mocks["run_build"].assert_not_called()
    mocks["launch_retroarch"].assert_awaited_once()


async def test_missing_emulator_binary_does_not_crash(server, mocks):
    mocks["launch_retroarch"].side_effect = FileNotFoundError("retroarch")
    await server._handle_event(media_event(rom_hash=""))  # must not raise


async def test_ps4_stable_fork_launches_shadps4(server, mocks, tmp_path):
    disc = tmp_path / "disc"
    (disc / "PS4GAME").mkdir(parents=True)
    (disc / "PS4GAME" / "PARAM.SFO").write_bytes(b"\x00PSF\x01\x01TITLE_ID\x00CUSA00562\x00")
    await server._handle_event(
        media_event(platform="ps4", rom_hash="", mount_point=str(disc))
    )
    mocks["launch_standalone"].assert_awaited_once_with("ps4", disc)


async def test_socket_server_accepts_ndjson_from_pr_mediad(server, mocks):
    """End-to-end over the Unix socket: malformed line is survived, valid event runs."""
    launched = asyncio.Event()
    mocks["launch_retroarch"].side_effect = lambda *a, **k: launched.set()

    task = asyncio.create_task(server.start())
    try:
        for _ in range(100):
            if Path(server._socket_path).exists():
                break
            await asyncio.sleep(0.01)

        _, w = await asyncio.open_unix_connection(server._socket_path)
        w.write(b"not json\n")
        await w.drain()
        w.close()

        _, w = await asyncio.open_unix_connection(server._socket_path)
        w.write((json.dumps(media_event(rom_hash="")) + "\n").encode())
        await w.drain()
        w.close()

        await asyncio.wait_for(launched.wait(), timeout=2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    mocks["launch_retroarch"].assert_called_once_with("mupen64plus_next", Path("/mnt/disc"))
    assert not Path(server._socket_path).exists()
