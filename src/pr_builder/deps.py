# src/pr_builder/deps.py
"""Pacman-based dependency resolution for decomp project toolchains."""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from typing import Sequence


async def ensure_deps(packages: Sequence[str]) -> None:
    """Install missing packages via pacman -S --needed --noconfirm.

    Skips packages already installed (pacman --needed is idempotent).
    Raises RuntimeError on pacman failure, including when sudo/pacman
    are not available on the host.
    """
    if not packages:
        return

    missing = [p for p in packages if not _is_installed(p)]
    if not missing:
        return

    # -n: never prompt for a password — a daemon has no TTY and would hang.
    cmd = ["sudo", "-n", "pacman", "-S", "--needed", "--noconfirm"] + list(missing)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:  # sudo/pacman binary missing
        raise RuntimeError(f"cannot run pacman: {exc}") from exc

    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"pacman failed (exit {proc.returncode}): {stderr.decode()}")


def _is_installed(package: str) -> bool:
    """Return True if `package` is already installed via pacman -Q."""
    return shutil.which("pacman") is not None and _pacman_query(package)


def _pacman_query(package: str) -> bool:
    result = subprocess.run(
        ["pacman", "-Q", package],
        capture_output=True,
    )
    return result.returncode == 0
