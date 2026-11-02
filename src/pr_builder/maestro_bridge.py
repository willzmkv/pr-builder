# src/pr_builder/maestro_bridge.py
"""IPC bridge to pr-maestro for build-failure diagnosis.

The wire contract is owned by pr-maestro (see its plan, Tasks 6-7):
pr-maestro listens on /run/project-r/maestro.sock (env MAESTRO_SOCK_PATH)
and accepts newline-delimited JSON requests, answering each with one
newline-terminated JSON response. It orchestrates Claude, Codex, Gemini
(via Maestro MCP) with OpenCode as fallback.

Request shape:
  {"type": "build_diagnosis",
   "game_id": "sm64-us",
   "stderr": "...",
   "build_log_path": "/home/arcade/.cache/pr-builder/logs/sm64-us.log"}

Response shape:
  {"type": "diagnosis_result", "game_id": "sm64-us",
   "diagnosis": "one sentence root cause" | null,
   "patch": {"file": "Makefile", "line": 42,
             "before": "-laudiofile", "after": "..."} | null,
   "confidence": 0.85}
  OR, on failure inside pr-maestro:
  {"type": "error", "message": "..."}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("pr-builder.maestro")

MAESTRO_SOCK = "/run/project-r/maestro.sock"
MAESTRO_SOCK_ENV = "MAESTRO_SOCK_PATH"

# Responses can carry file contents; allow lines well beyond asyncio's 64 KiB default.
_READ_LIMIT = 4 * 1024 * 1024


@dataclass(frozen=True)
class FilePatch:
    """Single in-file replacement proposed by pr-maestro."""

    file: str  # path relative to the project source dir
    line: Optional[int]  # 1-based line hint; may be wrong or absent
    before: str
    after: str


@dataclass
class DiagnosisResult:
    patch: Optional[FilePatch]  # None if no fix was found
    explanation: str  # pr-maestro "diagnosis" field ("" when null)
    confidence: float  # 0.0 – 1.0


async def diagnose_build_failure(
    project_id: str,
    stderr: str,
    build_log_path: str = "",
    sock_path: Optional[str] = None,
    connect_timeout: float = 5.0,
    response_timeout: float = 120.0,
) -> Optional[DiagnosisResult]:
    """Send a build failure to pr-maestro and return its diagnosis.

    Returns None if pr-maestro is unavailable (socket missing, timeout,
    malformed or error response). The caller should fall back to emulation.
    """
    path = sock_path or os.environ.get(MAESTRO_SOCK_ENV, MAESTRO_SOCK)
    request = json.dumps({
        "type": "build_diagnosis",
        "game_id": project_id,
        "stderr": stderr,
        "build_log_path": build_log_path,
    }) + "\n"

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(path, limit=_READ_LIMIT),
            timeout=connect_timeout,
        )
    except (OSError, asyncio.TimeoutError) as exc:
        log.info("pr-maestro unavailable at %s: %s", path, exc)
        return None

    try:
        writer.write(request.encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.readline(), timeout=response_timeout)
    except (OSError, ValueError, asyncio.TimeoutError) as exc:
        log.warning("pr-maestro request failed: %r", exc)
        raw = b""
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

    if not raw:
        return None

    try:
        data = json.loads(raw.decode())
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("pr-maestro sent malformed JSON")
        return None

    if not isinstance(data, dict) or data.get("type") != "diagnosis_result":
        log.warning("pr-maestro returned %s", data.get("type") if isinstance(data, dict) else data)
        return None

    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    return DiagnosisResult(
        patch=_parse_patch(data.get("patch")),
        explanation=data.get("diagnosis") or "",
        confidence=confidence,
    )


def apply_file_patch(patch: FilePatch, src_dir: Path) -> bool:
    """Apply a pr-maestro FilePatch inside src_dir. Returns True on success.

    The replacement is made on the hinted line when `before` occurs there,
    otherwise at the first occurrence in the file. Paths that resolve
    outside src_dir are refused — the patch comes from an LLM.
    """
    if not patch.before:
        return False

    root = src_dir.resolve()
    target = (root / patch.file).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        log.warning("refusing patch for %r (outside %s or missing)", patch.file, root)
        return False

    with open(target, encoding="utf-8", errors="surrogateescape", newline="") as fh:
        text = fh.read()

    lines = text.splitlines(keepends=True)
    idx = (patch.line - 1) if patch.line is not None else -1
    if "\n" not in patch.before and 0 <= idx < len(lines) and patch.before in lines[idx]:
        lines[idx] = lines[idx].replace(patch.before, patch.after, 1)
        new_text = "".join(lines)
    elif patch.before in text:
        new_text = text.replace(patch.before, patch.after, 1)
    else:
        return False

    with open(target, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
        fh.write(new_text)
    return True


def _parse_patch(raw: Any) -> Optional[FilePatch]:
    if not isinstance(raw, dict):
        return None
    file, before, after = raw.get("file"), raw.get("before"), raw.get("after")
    if not (isinstance(file, str) and isinstance(before, str) and isinstance(after, str)):
        return None
    line = raw.get("line")
    return FilePatch(
        file=file,
        line=line if isinstance(line, int) and not isinstance(line, bool) else None,
        before=before,
        after=after,
    )
