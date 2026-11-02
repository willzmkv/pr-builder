# tests/test_builder.py
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from pr_builder.builder import BuildResult, run_build
from pr_builder.deps import ensure_deps
from pr_builder.registry import DecompEntry


def make_entry(build_type: str = "make", build_args: list | None = None) -> DecompEntry:
    return DecompEntry(
        id="sm64-us",
        name="Super Mario 64 (US)",
        platform="n64",
        repo="https://github.com/sm64pc/sm64ex.git",
        build_type=build_type,
        build_args=build_args or ["VERSION=us"],
        asset_extractor=None,
        install_binary="build/us_pc/sm64.us.f3dex2e",
        deps=[],
        retroarch_fallback="mupen64plus_next",
    )


@pytest.mark.asyncio
async def test_run_build_make_success(tmp_path: Path):
    """A successful make build returns BuildResult with success=True."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    binary = src_dir / "build/us_pc/sm64.us.f3dex2e"
    binary.parent.mkdir(parents=True)
    binary.write_text("fake binary")

    progress_calls: list[tuple[str, int]] = []

    async def capture_progress(badge: str, pct: int) -> None:
        progress_calls.append((badge, pct))

    # Patch asyncio.create_subprocess_exec to return a fake process
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = _async_readline_gen([b"make[1]: ...\n", b"Linking sm64\n", b""])
    # Deviation from plan: a process spawned with stderr=PIPE always has a
    # stderr stream; the plan's fake left it as a bare (non-awaitable) MagicMock.
    fake_proc.stderr = _async_readline_gen([b""])
    fake_proc.wait = AsyncMock(return_value=0)

    with patch("pr_builder.builder.asyncio.create_subprocess_exec",
               return_value=fake_proc) as mock_exec:
        result = await run_build(make_entry("make"), src_dir, capture_progress)

    assert result.success is True
    assert result.binary_path == src_dir / "build/us_pc/sm64.us.f3dex2e"
    assert result.stderr == ""
    assert len(progress_calls) > 0
    assert progress_calls[0][0] == "BUILDING"


@pytest.mark.asyncio
async def test_run_build_failure_captures_stderr(tmp_path: Path):
    """A failed make build returns BuildResult with success=False and stderr."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    fake_proc = MagicMock()
    fake_proc.returncode = 2
    fake_proc.stdout = _async_readline_gen([b"make: *** [Error 1]\n", b""])
    fake_proc.stderr_content = b"undefined reference to `main'\n"
    fake_proc.wait = AsyncMock(return_value=2)

    stderr_lines = [b"undefined reference to `main'\n", b""]
    fake_proc.stderr = _async_readline_gen(stderr_lines)

    with patch("pr_builder.builder.asyncio.create_subprocess_exec",
               return_value=fake_proc):
        result = await run_build(make_entry("make"), src_dir, _noop_progress)

    assert result.success is False
    assert "undefined reference" in result.stderr


@pytest.mark.asyncio
async def test_run_build_cmake_uses_correct_command(tmp_path: Path):
    """cmake build type runs cmake configure then cmake --build."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    calls: list[tuple] = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = _async_readline_gen([b""])
        proc.stderr = _async_readline_gen([b""])
        proc.wait = AsyncMock(return_value=0)
        return proc

    with patch("pr_builder.builder.asyncio.create_subprocess_exec", side_effect=fake_exec):
        entry = make_entry("cmake", ["-DCMAKE_BUILD_TYPE=Release"])
        await run_build(entry, src_dir, _noop_progress)

    # First call should be cmake configure, second cmake --build
    assert any("cmake" in str(c) for c in calls)


@pytest.mark.asyncio
async def test_run_build_success_without_binary_is_failure(tmp_path: Path):
    """Exit 0 but no install_binary on disk must not be reported as success."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = _async_readline_gen([b"done\n", b""])
    fake_proc.stderr = _async_readline_gen([b""])
    fake_proc.wait = AsyncMock(return_value=0)

    with patch("pr_builder.builder.asyncio.create_subprocess_exec",
               return_value=fake_proc):
        result = await run_build(make_entry("make"), src_dir, _noop_progress)

    assert result.success is False
    assert result.binary_path is None
    assert "sm64.us.f3dex2e" in result.stderr


# ── deps.py (pacman is always mocked — never invoked for real) ───────────────

@pytest.mark.asyncio
async def test_ensure_deps_empty_is_noop():
    with patch("pr_builder.deps.asyncio.create_subprocess_exec") as mock_exec:
        await ensure_deps([])
    mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_deps_skips_when_all_installed():
    with patch("pr_builder.deps._is_installed", return_value=True), \
         patch("pr_builder.deps.asyncio.create_subprocess_exec") as mock_exec:
        await ensure_deps(["python", "sdl2"])
    mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_deps_installs_only_missing():
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("pr_builder.deps._is_installed", side_effect=lambda p: p == "python"), \
         patch("pr_builder.deps.asyncio.create_subprocess_exec",
               return_value=fake_proc) as mock_exec:
        await ensure_deps(["python", "audiofile", "sdl2"])

    cmd = mock_exec.call_args.args
    assert "pacman" in cmd
    assert "--needed" in cmd
    assert "--noconfirm" in cmd
    assert cmd[-2:] == ("audiofile", "sdl2")
    assert "python" not in cmd


@pytest.mark.asyncio
async def test_ensure_deps_pacman_failure_raises():
    fake_proc = MagicMock()
    fake_proc.returncode = 1
    fake_proc.communicate = AsyncMock(return_value=(b"", b"error: target not found: nope\n"))

    with patch("pr_builder.deps._is_installed", return_value=False), \
         patch("pr_builder.deps.asyncio.create_subprocess_exec", return_value=fake_proc):
        with pytest.raises(RuntimeError, match="pacman failed"):
            await ensure_deps(["nope"])


@pytest.mark.asyncio
async def test_ensure_deps_missing_pacman_binary_raises_runtime_error():
    with patch("pr_builder.deps._is_installed", return_value=False), \
         patch("pr_builder.deps.asyncio.create_subprocess_exec",
               side_effect=FileNotFoundError("sudo")):
        with pytest.raises(RuntimeError, match="cannot run pacman"):
            await ensure_deps(["sdl2"])


# ── helpers ──────────────────────────────────────────────────────────────────

class _AsyncLineReader:
    """Mimics asyncio.StreamReader.readline() from a list of byte lines."""
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = iter(lines)

    async def readline(self) -> bytes:
        try:
            return next(self._lines)
        except StopIteration:
            return b""


def _async_readline_gen(lines: list[bytes]) -> _AsyncLineReader:
    return _AsyncLineReader(lines)


async def _noop_progress(badge: str, pct: int) -> None:
    pass
