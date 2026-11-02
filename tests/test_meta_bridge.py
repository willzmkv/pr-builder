# tests/test_meta_bridge.py
"""meta_bridge against a fake pr-meta Unix socket server.

Wire contract follows the pr-meta plan (it owns these events): one JSON
object per connection, then close; no response is sent.
"""
import asyncio
import json
from pathlib import Path

from pr_builder.meta_bridge import (
    PR_META_SOCK,
    notify_badge_update,
    notify_game_installed,
)


async def _start_fake_meta(path: Path, received: list[dict]):
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        raw = await reader.read()  # until the client closes: one message per connection
        received.append(json.loads(raw.decode()))
        writer.close()

    return await asyncio.start_unix_server(handler, path=str(path))


async def _wait_for(received: list, n: int) -> None:
    for _ in range(100):
        if len(received) >= n:
            return
        await asyncio.sleep(0.01)


def test_default_socket_path_matches_pr_meta():
    assert PR_META_SOCK == "/run/project-r/pr-meta.sock"


async def test_notify_game_installed_shape(sock_dir: Path):
    sock = sock_dir / "meta.sock"
    received: list[dict] = []
    server = await _start_fake_meta(sock, received)
    async with server:
        ok = await notify_game_installed(
            platform="n64",
            title="Super Mario 64 (US)",
            game_dir=Path("/home/arcade/games/n64/Super Mario 64 (US)"),
            badge="NATIVE",
            core=None,
            binary="./sm64.us.f3dex2e",
            rom_hash="20b854b239203baf6c8a71a3e7a166cd",
            sock_path=str(sock),
        )
        await _wait_for(received, 1)

    assert ok is True
    assert received == [{
        "event": "game_installed",
        "platform": "n64",
        "title": "Super Mario 64 (US)",
        "rom_hash": "20b854b239203baf6c8a71a3e7a166cd",
        "game_dir": "/home/arcade/games/n64/Super Mario 64 (US)",
        "badge": "NATIVE",
        "core": None,
        "binary": "./sm64.us.f3dex2e",
    }]


async def test_notify_badge_update_shape(sock_dir: Path):
    sock = sock_dir / "meta.sock"
    received: list[dict] = []
    server = await _start_fake_meta(sock, received)
    async with server:
        ok = await notify_badge_update(
            platform="n64",
            title="Super Mario 64 (US)",
            game_dir=Path("/g/n64/Super Mario 64 (US)"),
            badge="BUILDING",
            progress=42,
            sock_path=str(sock),
        )
        await _wait_for(received, 1)

    assert ok is True
    assert received == [{
        "event": "badge_update",
        "platform": "n64",
        "title": "Super Mario 64 (US)",
        "game_dir": "/g/n64/Super Mario 64 (US)",
        "badge": "BUILDING",
        "core": None,
        "progress": 42,
    }]


async def test_one_connection_per_message(sock_dir: Path):
    sock = sock_dir / "meta.sock"
    received: list[dict] = []
    server = await _start_fake_meta(sock, received)
    async with server:
        for pct in (10, 20):
            await notify_badge_update("n64", "T", Path("/g/n64/T"), "BUILDING",
                                      progress=pct, sock_path=str(sock))
        await _wait_for(received, 2)
    assert [r["progress"] for r in received] == [10, 20]


async def test_notify_returns_false_when_pr_meta_unavailable(sock_dir: Path):
    ok = await notify_badge_update("n64", "T", Path("/g/n64/T"), "BUILDING",
                                   progress=1, sock_path=str(sock_dir / "missing.sock"))
    assert ok is False


async def test_notify_uses_env_socket_path(sock_dir: Path, monkeypatch):
    sock = sock_dir / "env.sock"
    monkeypatch.setenv("PR_META_SOCKET", str(sock))
    received: list[dict] = []
    server = await _start_fake_meta(sock, received)
    async with server:
        ok = await notify_badge_update("n64", "T", Path("/g/n64/T"), "NATIVE")
        await _wait_for(received, 1)
    assert ok is True
    assert received[0]["badge"] == "NATIVE"
