# pr-builder

> Project-R build orchestrator — decompiles and recompiles games to native Linux binaries.

**Language:** Python 3.11+ · **Status:** Pre-alpha · [Implementation Plan](https://github.com/willzmkv/project-r-os/blob/main/ROADMAP.md#pr-builder--build-orchestrator)

## What it does

Receives media events from pr-mediad via Unix socket. Looks up the ROM hash in `decomp_registry.json`. If a decompilation project exists, clones it, installs deps via pacman/yay, and runs the build. On success, installs the native binary and notifies pr-meta. On failure, asks pr-maestro (AI) for a diagnosis and patch.

```
MediaEvent → registry lookup → git clone → build → install native binary
                             → no entry  → write EMU meta → RetroArch
```

## Architecture

- `registry.py` — loads decomp_registry.json + emu_forks_registry.json
- `deps.py` — async pacman/yay dependency installer
- `builder.py` — subprocess build runner (make/cmake), streams progress
- `library.py` — installs native binary, writes pr-meta.json badge
- `maestro_bridge.py` — Unix socket client to pr-maestro for AI diagnosis
- `launcher.py` — launches RetroArch or standalone emulator as fallback
- `server.py` — asyncio Unix socket server at `/run/project-r/pr-builder.sock`

## Run

```bash
pip install -e .
python -m pr_builder.server
```

## Registry

Game entries live in [decomp-registry](https://github.com/willzmkv/decomp-registry).

## License

GPL-3.0
