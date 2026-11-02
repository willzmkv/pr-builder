# tests/test_maestro_bridge.py
"""maestro_bridge against a fake pr-maestro Unix socket server.

Wire contract follows the pr-maestro plan (it owns this request/response):
  request : {"type":"build_diagnosis","game_id","stderr","build_log_path"}
  response: {"type":"diagnosis_result","game_id","diagnosis","patch","confidence"}
            {"type":"error","message"}
"""
import asyncio
import json
from pathlib import Path

import pytest

from pr_builder.maestro_bridge import (
    MAESTRO_SOCK,
    DiagnosisResult,
    FilePatch,
    apply_file_patch,
    diagnose_build_failure,
)


async def _start_fake_maestro(path: Path, response: bytes | None, received: list[dict]):
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = await reader.readline()
        if line:
            received.append(json.loads(line))
        if response is not None:
            writer.write(response)
            await writer.drain()
        else:
            await reader.read()  # never answer; wait for the client to hang up
        writer.close()

    return await asyncio.start_unix_server(handler, path=str(path))


def _line(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode()


def test_default_socket_path_matches_pr_maestro():
    assert MAESTRO_SOCK == "/run/project-r/maestro.sock"


async def test_diagnose_sends_build_diagnosis_and_parses_patch(sock_dir: Path):
    sock = sock_dir / "maestro.sock"
    received: list[dict] = []
    response = _line({
        "type": "diagnosis_result",
        "game_id": "sm64-us",
        "diagnosis": "Missing audiofile link flags",
        "patch": {"file": "Makefile", "line": 42, "before": "-laudiofile",
                  "after": "$(shell pkg-config --libs audiofile)"},
        "confidence": 0.87,
    })
    server = await _start_fake_maestro(sock, response, received)
    async with server:
        result = await diagnose_build_failure(
            "sm64-us", "ld: cannot find -laudiofile", "/tmp/sm64-us.log", sock_path=str(sock)
        )

    assert received == [{
        "type": "build_diagnosis",
        "game_id": "sm64-us",
        "stderr": "ld: cannot find -laudiofile",
        "build_log_path": "/tmp/sm64-us.log",
    }]
    assert isinstance(result, DiagnosisResult)
    assert result.explanation == "Missing audiofile link flags"
    assert result.confidence == pytest.approx(0.87)
    assert result.patch == FilePatch(
        file="Makefile", line=42, before="-laudiofile",
        after="$(shell pkg-config --libs audiofile)",
    )


async def test_diagnose_null_patch(sock_dir: Path):
    sock = sock_dir / "maestro.sock"
    response = _line({"type": "diagnosis_result", "game_id": "sm64-us",
                      "diagnosis": None, "patch": None, "confidence": 0})
    server = await _start_fake_maestro(sock, response, [])
    async with server:
        result = await diagnose_build_failure("sm64-us", "err", sock_path=str(sock))

    assert result is not None
    assert result.patch is None
    assert result.explanation == ""
    assert result.confidence == 0.0


async def test_diagnose_malformed_patch_is_dropped(sock_dir: Path):
    sock = sock_dir / "maestro.sock"
    response = _line({"type": "diagnosis_result", "game_id": "x", "diagnosis": "d",
                      "patch": {"file": "Makefile"}, "confidence": 0.5})
    server = await _start_fake_maestro(sock, response, [])
    async with server:
        result = await diagnose_build_failure("x", "err", sock_path=str(sock))

    assert result is not None
    assert result.patch is None


async def test_diagnose_error_response_returns_none(sock_dir: Path):
    sock = sock_dir / "maestro.sock"
    response = _line({"type": "error", "message": "Unknown request type"})
    server = await _start_fake_maestro(sock, response, [])
    async with server:
        result = await diagnose_build_failure("sm64-us", "err", sock_path=str(sock))
    assert result is None


async def test_diagnose_malformed_json_returns_none(sock_dir: Path):
    sock = sock_dir / "maestro.sock"
    server = await _start_fake_maestro(sock, b"not json\n", [])
    async with server:
        result = await diagnose_build_failure("sm64-us", "err", sock_path=str(sock))
    assert result is None


async def test_diagnose_socket_missing_returns_none(sock_dir: Path):
    result = await diagnose_build_failure("sm64-us", "err", sock_path=str(sock_dir / "nope.sock"))
    assert result is None


async def test_diagnose_timeout_returns_none(sock_dir: Path):
    sock = sock_dir / "maestro.sock"
    server = await _start_fake_maestro(sock, None, [])
    async with server:
        result = await diagnose_build_failure(
            "sm64-us", "err", sock_path=str(sock), response_timeout=0.2
        )
    assert result is None


async def test_diagnose_uses_env_socket_path(sock_dir: Path, monkeypatch):
    sock = sock_dir / "env.sock"
    monkeypatch.setenv("MAESTRO_SOCK_PATH", str(sock))
    received: list[dict] = []
    response = _line({"type": "diagnosis_result", "game_id": "x", "diagnosis": "d",
                      "patch": None, "confidence": 0.1})
    server = await _start_fake_maestro(sock, response, received)
    async with server:
        result = await diagnose_build_failure("x", "err")
    assert result is not None
    assert received[0]["type"] == "build_diagnosis"


# ── apply_file_patch ─────────────────────────────────────────────────────────

def test_apply_file_patch_at_line(tmp_path: Path):
    mk = tmp_path / "Makefile"
    mk.write_text("CC=gcc\nLIBS=-laudiofile -lm\nall:\n\t$(CC) $(LIBS)\n")
    ok = apply_file_patch(FilePatch("Makefile", 2, "-laudiofile", "-laudiofile -lpthread"), tmp_path)
    assert ok is True
    assert mk.read_text() == "CC=gcc\nLIBS=-laudiofile -lpthread -lm\nall:\n\t$(CC) $(LIBS)\n"


def test_apply_file_patch_falls_back_to_first_occurrence(tmp_path: Path):
    src = tmp_path / "src" / "main.c"
    src.parent.mkdir()
    src.write_text("int a;\nint b;\n")
    ok = apply_file_patch(FilePatch("src/main.c", 99, "int b;", "long b;"), tmp_path)
    assert ok is True
    assert src.read_text() == "int a;\nlong b;\n"


def test_apply_file_patch_before_not_found(tmp_path: Path):
    (tmp_path / "Makefile").write_text("all:\n")
    assert apply_file_patch(FilePatch("Makefile", 1, "nothing", "x"), tmp_path) is False
    assert (tmp_path / "Makefile").read_text() == "all:\n"


def test_apply_file_patch_rejects_path_escape(tmp_path: Path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("keep me")
    assert apply_file_patch(FilePatch("../secret.txt", 1, "keep", "lose"), src_dir) is False
    assert apply_file_patch(FilePatch(str(outside), 1, "keep", "lose"), src_dir) is False
    assert outside.read_text() == "keep me"


def test_apply_file_patch_missing_file(tmp_path: Path):
    assert apply_file_patch(FilePatch("nope.c", 1, "a", "b"), tmp_path) is False
