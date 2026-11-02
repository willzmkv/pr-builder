# src/pr_builder/builder.py
"""Subprocess build execution with asyncio progress streaming."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from pr_builder.registry import DecompEntry

# Callback type: receives badge name ("BUILDING") and integer percentage 0-100.
ProgressCallback = Callable[[str, int], Awaitable[None]]


@dataclass
class BuildResult:
    success: bool
    binary_path: Path | None = None
    stderr: str = ""


async def run_build(
    entry: DecompEntry,
    src_dir: Path,
    progress: ProgressCallback,
) -> BuildResult:
    """Build an already-cloned decomp project in `src_dir` and return the result.

    Streams stdout lines and maps them to progress percentage estimates.
    Collects stderr for failure reporting.
    """
    await progress("BUILDING", 0)

    if entry.build_type == "make":
        return await _run_make(entry, src_dir, progress)
    elif entry.build_type == "cmake":
        return await _run_cmake(entry, src_dir, progress)
    else:
        return BuildResult(
            success=False,
            stderr=f"Unknown build_type: {entry.build_type}",
        )


async def _run_make(
    entry: DecompEntry,
    src_dir: Path,
    progress: ProgressCallback,
) -> BuildResult:
    cmd = ["make"] + entry.build_args
    return await _exec_build(cmd, src_dir, entry, progress, start_pct=10, end_pct=90)


async def _run_cmake(
    entry: DecompEntry,
    src_dir: Path,
    progress: ProgressCallback,
) -> BuildResult:
    build_dir = src_dir / "build"
    build_dir.mkdir(exist_ok=True)

    # Configure
    configure_cmd = ["cmake", ".."] + entry.build_args
    cfg_result = await _exec_build(
        configure_cmd, build_dir, entry, progress, start_pct=5, end_pct=30,
        expect_binary=False,
    )
    if not cfg_result.success:
        return cfg_result

    # Build
    build_cmd = ["cmake", "--build", ".", "--parallel"]
    return await _exec_build(
        build_cmd, build_dir, entry, progress, start_pct=30, end_pct=90
    )


async def _exec_build(
    cmd: list[str],
    cwd: Path,
    entry: DecompEntry,
    progress: ProgressCallback,
    start_pct: int,
    end_pct: int,
    expect_binary: bool = True,
) -> BuildResult:
    stderr_lines: list[str] = []
    stdout_lines: list[str] = []

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:  # make/cmake not installed
        return BuildResult(success=False, stderr=f"cannot run {cmd[0]}: {exc}")

    # Read stdout for progress heartbeat, stderr for error capture
    async def read_stdout() -> None:
        pct = start_pct
        step = max(1, (end_pct - start_pct) // 20)
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            stdout_lines.append(line.decode(errors="replace").rstrip())
            pct = min(pct + step, end_pct)
            await progress("BUILDING", pct)

    async def read_stderr() -> None:
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            stderr_lines.append(line.decode(errors="replace").rstrip())

    await asyncio.gather(read_stdout(), read_stderr())
    await proc.wait()

    if proc.returncode != 0:
        return BuildResult(
            success=False,
            stderr="\n".join(stderr_lines),
        )

    if not expect_binary:
        return BuildResult(success=True)

    binary = cwd.parent / entry.install_binary if entry.build_type == "cmake" else cwd / entry.install_binary
    if not binary.is_file():
        return BuildResult(
            success=False,
            stderr=f"build exited 0 but install_binary not found: {binary}",
        )
    return BuildResult(
        success=True,
        binary_path=binary,
        stderr="",
    )
