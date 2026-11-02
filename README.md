# pr-builder

> Project-R build orchestrator — decompiles and recompiles games to native Linux binaries.

**Language:** Python 3.11+ · **Status:** Pre-alpha · [Implementation Plan](https://github.com/willzmkv/project-r-os/blob/main/ROADMAP.md#pr-builder--build-orchestrator)

## What it does

Receives media events from pr-mediad via Unix socket. Looks up the ROM hash in `decomp_registry.json`. If a decompilation project exists, it installs deps via pacman, clones the project and runs the build, streaming `BUILDING` progress to pr-meta. On success it installs the native binary and notifies pr-meta (`NATIVE`). On failure it asks pr-maestro (AI) for a diagnosis and patch, applies it and retries once; if the build still fails it records the title as `EMU` and launches an emulator.

```
media_inserted → registry lookup → pacman deps → git clone → build ──ok──→ install → pr-meta NATIVE
                                                                   └fail→ pr-maestro patch → retry ─┘
                                                                                     └fail→ pr-meta EMU → emulator
               → no entry → emulator (stable ShadPS4/KytyPS5 fork → standalone → RetroArch core)
```

## Architecture

- `registry.py` — loads decomp_registry.json + emu_forks_registry.json
- `deps.py` — async pacman dependency installer (`sudo -n pacman -S --needed`)
- `builder.py` — subprocess build runner (make/cmake), streams progress
- `library.py` — installs native binary, writes `~/games/{platform}/{title}/pr-meta.json`
- `maestro_bridge.py` — Unix socket client to pr-maestro; applies its structured patches
- `meta_bridge.py` — Unix socket client to pr-meta (`game_installed`, `badge_update`)
- `launcher.py` — launches RetroArch or a standalone emulator as fallback
- `server.py` — asyncio Unix socket server at `/run/project-r/pr-builder.sock`

## IPC contract

All sockets are `AF_UNIX` stream sockets carrying UTF-8 JSON. Each peer's
plan owns its half of the contract; pr-builder follows them.

### Inbound — pr-mediad → pr-builder (owned by pr-mediad)

- Socket: `/run/project-r/pr-builder.sock` (pr-builder listens; env `PR_BUILDER_SOCKET`)
- Framing: client connects, sends **one newline-terminated JSON line**, disconnects. No reply.

```json
{"type":"media_inserted","platform":"n64","device":"/dev/sr0","mount_point":"/mnt/disc","rom_hash":"20b854b239203baf6c8a71a3e7a166cd"}
```

| field | type | notes |
|---|---|---|
| `type` | string | only `media_inserted` is handled; others are logged and ignored |
| `platform` | string | pr-mediad `esde_name`: `n64`, `ps1`, `ps2`, `gc`, `wii`, `snes`, … |
| `device` | string | block device, e.g. `/dev/sr0` |
| `mount_point` | string | `""` for raw ROM devices |
| `rom_hash` | string | ROM MD5, `""` when unknown (always `""` in pr-mediad v0.1) |

### Outbound — pr-builder → pr-maestro (owned by pr-maestro)

- Socket: `/run/project-r/maestro.sock` (env `MAESTRO_SOCK_PATH`)
- Framing: newline-delimited JSON; one request line, one response line. Timeouts: 5 s connect, 120 s response.

Request:
```json
{"type":"build_diagnosis","game_id":"sm64-us","stderr":"ld: cannot find -laudiofile","build_log_path":"/home/arcade/.cache/pr-builder/logs/sm64-us.log"}
```

Response:
```json
{"type":"diagnosis_result","game_id":"sm64-us","diagnosis":"Missing audiofile link flags",
 "patch":{"file":"Makefile","line":42,"before":"-laudiofile","after":"$(shell pkg-config --libs audiofile)"},
 "confidence":0.87}
```
`diagnosis` and `patch` may be `null`. `{"type":"error","message":"..."}` and any
unreachable/timeout/malformed case are treated as "no diagnosis". The patch is
applied as a single in-file replacement (on `line` if `before` is there, else
first occurrence); paths resolving outside the project's source dir are refused.

### Outbound — pr-builder → pr-meta (owned by pr-meta)

- Socket: `/run/project-r/pr-meta.sock` (env `PR_META_SOCKET`)
- Framing: **one JSON object per connection**, then close. No reply. Best-effort: pr-builder keeps going if pr-meta is down.

Build progress (sent when the percentage changes):
```json
{"event":"badge_update","platform":"n64","title":"Super Mario 64 (US)","game_dir":"/home/arcade/games/n64/Super Mario 64 (US)","badge":"BUILDING","core":null,"progress":42}
```

Title landed in the library (native build or emulator fallback):
```json
{"event":"game_installed","platform":"n64","title":"Super Mario 64 (US)","rom_hash":"20b854b239203baf6c8a71a3e7a166cd","game_dir":"/home/arcade/games/n64/Super Mario 64 (US)","badge":"NATIVE","core":null,"binary":"./sm64.us.f3dex2e"}
```
On emulator fallback: `"badge":"EMU"`, `"core":"<retroarch_fallback>"`, `"binary":"<device>"`.

## Run

```bash
pip install -e .
pr-builder            # or: python -m pr_builder.server
```

| env var | default |
|---|---|
| `PR_BUILDER_SOCKET` | `/run/project-r/pr-builder.sock` |
| `MAESTRO_SOCK_PATH` | `/run/project-r/maestro.sock` |
| `PR_META_SOCKET` | `/run/project-r/pr-meta.sock` |
| `PR_GAMES_ROOT` | `~/games` |
| `PR_BUILDER_DECOMP_REGISTRY` | `<repo>/decomp_registry.json` |
| `PR_BUILDER_EMU_FORKS_REGISTRY` | `<repo>/emu_forks_registry.json` |

Build trees live in `~/.cache/pr-builder/<id>/`, failure logs in `~/.cache/pr-builder/logs/`.
A systemd unit is provided in `pr-builder.service`. The `arcade` user needs a
passwordless sudoers rule for `pacman -S` (pr-builder uses `sudo -n` and falls
back to emulation when it is refused).

## Test

Tests need Linux (Unix sockets) and never touch real pacman, git, builds, emulators or network.

```bash
pip install -e ".[dev]"
pytest -v
```

## Registry

The bundled `decomp_registry.json` / `emu_forks_registry.json` follow the
pr-builder plan's schema (hash-keyed `games` map, `build_type`, `build_args`,
`install_binary`, `retroarch_fallback`). The canonical community registry
lives in [decomp-registry](https://github.com/willzmkv/decomp-registry), which
currently uses a different list-based schema (`rom_hash_md5`, `build_system`,
`output_binary`); it is not loadable by pr-builder yet.

## Known gaps

- Asset extraction (`asset_extractor`) is not run yet; projects that need a base ROM in the tree will fail and fall back to emulation.
- No "Recompile to native? [Yes] [No]" prompt yet; a matching decomp project always builds.
- Stable ShadPS4/KytyPS5 forks are detected and logged, but the stock emulator is launched (forks are not built/installed).

## License

GPL-3.0
