# src/pr_builder/server.py
"""asyncio Unix socket server — main entry point for pr-builder.

Listens on /run/project-r/pr-builder.sock for newline-delimited JSON
events from pr-mediad. Dispatches each event through the full pipeline:
  registry lookup → deps → clone → build → install or fallback.

Outbound IPC: pr-maestro (build-failure diagnosis, maestro_bridge) and
pr-meta (library/badge notifications, meta_bridge).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
from pathlib import Path
from typing import Any, Optional

from pr_builder.builder import BuildResult, run_build
from pr_builder.deps import ensure_deps
from pr_builder.launcher import (
    RETROARCH_CORES,
    STANDALONE_EMULATORS,
    launch_retroarch,
    launch_standalone,
)
from pr_builder.library import game_dir_for, install_native, write_emu_meta
from pr_builder.maestro_bridge import apply_file_patch, diagnose_build_failure
from pr_builder.meta_bridge import notify_badge_update, notify_game_installed
from pr_builder.registry import DecompEntry, Registry

log = logging.getLogger("pr-builder")

SOCKET_PATH = "/run/project-r/pr-builder.sock"
GAMES_ROOT = Path.home() / "games"
CACHE_ROOT = Path.home() / ".cache" / "pr-builder"

# Resolved relative to the repo root (editable install); override via env
# for packaged installs (see main()).
_HERE = Path(__file__).parent
_REPO_ROOT = _HERE.parent.parent  # src/pr_builder/ → pr-builder/
DECOMP_REGISTRY_PATH = _REPO_ROOT / "decomp_registry.json"
EMU_FORKS_REGISTRY_PATH = _REPO_ROOT / "emu_forks_registry.json"


class Server:
    def __init__(
        self,
        socket_path: str = SOCKET_PATH,
        games_root: Path = GAMES_ROOT,
        decomp_registry: Path = DECOMP_REGISTRY_PATH,
        emu_forks_registry: Path = EMU_FORKS_REGISTRY_PATH,
        cache_root: Path = CACHE_ROOT,
    ) -> None:
        self._socket_path = socket_path
        self._games_root = games_root
        self._cache_root = cache_root
        self._registry = Registry(decomp_registry, emu_forks_registry)
        self._build_locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        # Remove stale socket
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self._socket_path)

        os.makedirs(os.path.dirname(self._socket_path), exist_ok=True)

        server = await asyncio.start_unix_server(
            self._handle_connection, path=self._socket_path
        )
        log.info("pr-builder listening on %s", self._socket_path)

        try:
            async with server:
                await server.serve_forever()
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(self._socket_path)

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # pr-mediad connects, sends one JSON line, disconnects; no reply is sent.
        # Close our end before running the (long) pipeline so shutdown never
        # waits on an idle connection.
        try:
            raw = await reader.readline()
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
        if not raw:
            return

        try:
            event = json.loads(raw.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning("Malformed event JSON: %s", exc)
            return
        if not isinstance(event, dict):
            log.warning("Event is not a JSON object: %r", event)
            return

        try:
            await self._handle_event(event)
        except Exception:
            log.exception("Pipeline failed for event %r", event)

    async def _handle_event(self, event: dict[str, Any]) -> None:
        if event.get("type") != "media_inserted":
            log.warning("Unknown event type: %s", event.get("type"))
            return

        platform: str = event.get("platform") or ""
        device: str = event.get("device") or ""
        mount_point: str = event.get("mount_point") or ""
        rom_hash: str = event.get("rom_hash") or ""

        log.info("media_inserted: platform=%s device=%s hash=%s", platform, device, rom_hash)

        # 1. Registry lookup
        entry = self._registry.lookup_decomp(rom_hash) if rom_hash else None

        if entry is None:
            log.info("No decomp project for hash=%r — launching emulator", rom_hash)
            await self._launch_best_emulator(platform, device, mount_point)
            return

        lock = self._build_locks.setdefault(entry.id, asyncio.Lock())
        async with lock:
            await self._build_pipeline(
                entry, platform or entry.platform, device, mount_point, rom_hash
            )

    async def _build_pipeline(
        self,
        entry: DecompEntry,
        platform: str,
        device: str,
        mount_point: str,
        rom_hash: str,
    ) -> None:
        game_dir = game_dir_for(self._games_root, platform, entry.name)

        # 2. Dependency resolution
        log.info("Resolving deps: %s", entry.deps)
        try:
            await ensure_deps(entry.deps)
        except RuntimeError as exc:
            log.error("Dep resolution failed: %s", exc)
            await self._fallback_to_emulator(entry, platform, device, mount_point, rom_hash)
            return

        # 3. Clone repo (asset extraction is not implemented yet — see README)
        src_dir = self._cache_root / entry.id
        src_dir.mkdir(parents=True, exist_ok=True)
        try:
            await _clone_if_needed(entry.repo, src_dir)
        except RuntimeError as exc:
            log.error("Clone failed: %s", exc)
            await self._fallback_to_emulator(entry, platform, device, mount_point, rom_hash)
            return

        # 4. Build with progress → BUILDING badge in pr-meta
        progress = self._progress_reporter(platform, entry.name, game_dir)
        result: BuildResult = await run_build(entry, src_dir, progress)

        if not result.success:
            log.warning("Build failed for %s — sending to maestro", entry.id)
            log_path = self._write_build_log(entry.id, result.stderr)
            diagnosis = await diagnose_build_failure(entry.id, result.stderr, str(log_path))

            if diagnosis and diagnosis.patch:
                log.info(
                    "Maestro proposed patch (confidence=%.2f): %s",
                    diagnosis.confidence,
                    diagnosis.explanation,
                )
                # Apply patch and retry — single retry only
                if apply_file_patch(diagnosis.patch, src_dir):
                    result = await run_build(entry, src_dir, progress)
                else:
                    log.warning("Maestro patch for %s could not be applied", diagnosis.patch.file)

            if not result.success:
                log.warning("Build still failing after maestro patch — falling back to emulator")
                await self._fallback_to_emulator(entry, platform, device, mount_point, rom_hash)
                return

        # 5. Install native binary
        assert result.binary_path is not None
        meta = install_native(
            games_root=self._games_root,
            platform=platform,
            title=entry.name,
            binary_src=result.binary_path,
        )
        await notify_game_installed(
            platform, entry.name, game_dir, "NATIVE", None, meta.binary, rom_hash
        )
        log.info("Installed native binary for %s — badge=NATIVE", entry.name)

    async def _fallback_to_emulator(
        self,
        entry: DecompEntry,
        platform: str,
        device: str,
        mount_point: str,
        rom_hash: str,
    ) -> None:
        """Record the title as EMU (pr-meta.json + pr-meta) and launch an emulator."""
        meta = write_emu_meta(
            games_root=self._games_root,
            platform=platform,
            title=entry.name,
            core=entry.retroarch_fallback,
            rom_path=Path(device),
        )
        await notify_game_installed(
            platform,
            entry.name,
            game_dir_for(self._games_root, platform, entry.name),
            "EMU",
            entry.retroarch_fallback or None,
            meta.binary,
            rom_hash,
        )
        await self._launch_best_emulator(
            platform, device, mount_point, core=entry.retroarch_fallback or None
        )

    async def _launch_best_emulator(
        self,
        platform: str,
        device: str,
        mount_point: str,
        core: Optional[str] = None,
    ) -> None:
        """Check emu_forks_registry then launch the best available emulator.

        Order: stable game-specific fork (ps4/ps5) → explicit RetroArch core
        → standalone emulator → platform's default RetroArch core.
        """
        rom_path = Path(mount_point or device)

        try:
            # Check for a game-specific emulator fork (shadps4/kytyps5)
            game_id = _infer_game_id(mount_point)
            for emu in ("shadps4", "kytyps5"):
                if platform not in _emu_to_platforms(emu):
                    continue
                fork = self._registry.lookup_emu_fork(emu, game_id) if game_id else None
                if fork:
                    if fork.stability == "stable":
                        log.info("Using stable fork %s for %s", fork.fork_repo, fork.title)
                        await launch_standalone(platform, rom_path)
                        return
                    else:
                        log.info(
                            "Experimental fork available for %s (%s) — using default emulator",
                            fork.title, emu,
                        )

            # Standard emulator selection
            if core:
                await launch_retroarch(core, rom_path)
            elif platform in STANDALONE_EMULATORS:
                await launch_standalone(platform, rom_path)
            elif platform in RETROARCH_CORES:
                await launch_retroarch(RETROARCH_CORES[platform], rom_path)
            else:
                log.warning("No emulator for platform=%s — doing nothing", platform)
        except OSError as exc:
            log.error("Emulator launch failed for platform=%s: %s", platform, exc)

    def _progress_reporter(self, platform: str, title: str, game_dir: Path):
        """Progress callback: logs every update, forwards changed % to pr-meta.

        Stops forwarding after the first undelivered update so a missing
        pr-meta never slows the build's output pipe.
        """
        state = {"last": None, "meta_ok": True}

        async def progress(badge: str, pct: int) -> None:
            log.info("[%s] %d%%", badge, pct)
            if not state["meta_ok"] or pct == state["last"]:
                return
            state["last"] = pct
            state["meta_ok"] = await notify_badge_update(
                platform, title, game_dir, badge, progress=pct
            )

        return progress

    def _write_build_log(self, project_id: str, stderr: str) -> Path:
        log_dir = self._cache_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{project_id}.log"
        log_path.write_text(stderr)
        return log_path


# ── helpers ──────────────────────────────────────────────────────────────────

async def _clone_if_needed(repo_url: str, dest: Path) -> None:
    """git clone if dest is empty, otherwise skip."""
    if any(dest.iterdir()):
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth=1", repo_url, str(dest),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise RuntimeError(f"cannot run git: {exc}") from exc
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed: {stderr.decode(errors='replace')}")


def _infer_game_id(mount_point: str) -> str | None:
    """Read game ID from PS4/PS5 disc structure (PARAM.SFO)."""
    if not mount_point:
        return None
    for candidate in ("PS4GAME/PARAM.SFO", "PS5GAME/PARAM.SFO"):
        p = Path(mount_point) / candidate
        if p.exists():
            # Simplified: parse TITLE_ID from binary SFO — return first 9-char CUSA/PPSA token
            data = p.read_bytes()
            for marker in (b"CUSA", b"PPSA"):
                idx = data.find(marker)
                if idx != -1:
                    return data[idx:idx + 9].decode(errors="replace")
    return None


def _emu_to_platforms(emu: str) -> set[str]:
    return {"shadps4": {"ps4"}, "kytyps5": {"ps5"}}.get(emu, set())


async def _serve_until_signalled(server: Server) -> None:
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    serve = asyncio.create_task(server.start())
    stopper = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait({serve, stopper}, return_when=asyncio.FIRST_COMPLETED)
    if serve in done:
        stopper.cancel()
        serve.result()  # surface startup errors (e.g. socket dir not writable)
        return

    log.info("pr-builder shutting down")
    serve.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await serve


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    env = os.environ
    server = Server(
        socket_path=env.get("PR_BUILDER_SOCKET", SOCKET_PATH),
        games_root=Path(env.get("PR_GAMES_ROOT", str(GAMES_ROOT))),
        decomp_registry=Path(env.get("PR_BUILDER_DECOMP_REGISTRY", str(DECOMP_REGISTRY_PATH))),
        emu_forks_registry=Path(
            env.get("PR_BUILDER_EMU_FORKS_REGISTRY", str(EMU_FORKS_REGISTRY_PATH))
        ),
    )
    asyncio.run(_serve_until_signalled(server))


if __name__ == "__main__":
    main()
