# src/pr_builder/meta_bridge.py
"""Outbound notifications to pr-meta.

The wire contract is owned by pr-meta (see its plan, Task 9): pr-meta
listens on /run/pr-meta.sock (env PR_META_SOCKET); each connection
carries exactly one JSON object and is then closed. No response is sent.

  {"event": "game_installed", "platform", "title", "rom_hash",
   "game_dir", "badge", "core", "binary"}
      → full scrape + pr-meta.json + gamelist.xml upsert

  {"event": "badge_update", "platform", "title", "game_dir",
   "badge", "core", "progress"}
      → only <pr_badge>/<pr_core> change (no re-scrape)

Badges: "NATIVE" | "EMU" | "BUILDING" | "BUILD ERROR".
Delivery is best-effort: pr-builder keeps working when pr-meta is down.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("pr-builder.meta")

PR_META_SOCK = "/run/pr-meta.sock"
PR_META_SOCK_ENV = "PR_META_SOCKET"


async def notify_game_installed(
    platform: str,
    title: str,
    game_dir: Path,
    badge: str,
    core: Optional[str],
    binary: Optional[str],
    rom_hash: str = "",
    sock_path: Optional[str] = None,
) -> bool:
    """Tell pr-meta a title landed in the library. Returns False if undelivered."""
    return await _send({
        "event": "game_installed",
        "platform": platform,
        "title": title,
        "rom_hash": rom_hash,
        "game_dir": str(game_dir),
        "badge": badge,
        "core": core,
        "binary": binary or "",
    }, sock_path)


async def notify_badge_update(
    platform: str,
    title: str,
    game_dir: Path,
    badge: str,
    core: Optional[str] = None,
    progress: Optional[int] = None,
    sock_path: Optional[str] = None,
) -> bool:
    """Tell pr-meta to flip a title's badge. Returns False if undelivered."""
    return await _send({
        "event": "badge_update",
        "platform": platform,
        "title": title,
        "game_dir": str(game_dir),
        "badge": badge,
        "core": core,
        "progress": progress,
    }, sock_path)


async def _send(event: dict[str, Any], sock_path: Optional[str], timeout: float = 2.0) -> bool:
    path = sock_path or os.environ.get(PR_META_SOCK_ENV, PR_META_SOCK)
    try:
        _, writer = await asyncio.wait_for(asyncio.open_unix_connection(path), timeout=timeout)
    except (OSError, asyncio.TimeoutError) as exc:
        log.debug("pr-meta unavailable at %s: %s", path, exc)
        return False

    try:
        writer.write((json.dumps(event) + "\n").encode())
        await asyncio.wait_for(writer.drain(), timeout=timeout)
        return True
    except (OSError, asyncio.TimeoutError) as exc:
        log.warning("pr-meta send failed: %r", exc)
        return False
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
