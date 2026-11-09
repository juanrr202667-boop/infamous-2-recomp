# Development environment setup

The workflow still depends on local setup and paths specific to the original development machine. This page outlines the expected layout; it does not claim a one-command portable build. The public package does not include the inputs required to compile or run the game.

## Prerequisites and tools

Windows x64, D3D12-capable GPU, Git, Python 3, CMake, Ninja, clang-cl/LLVM, and Windows SDK with D3DCompiler. Pytest covers Python tests; compilation fixtures require a configured compiler. GPU fixtures and native boot runs are strictly opt-in. Transitive dependencies follow the SDK instructions and licenses.

Ghidra, RenderDoc, and PIX are optional diagnostic tools. Their installation does not establish game execution.

## Pinned SDK

In the project root:

```powershell
git clone https://github.com/sp00nznet/ps3recomp.git tools/ps3recomp
git -C tools/ps3recomp checkout 5244db92669b54831c6d3ebb7a96d58c4dbfa6df
```

Do not update this clone automatically. `scripts/rebuild_ppu.py` dictates the order of the 91 local patch stages and detects applied prefixes before modifying files. The clone and its binaries are not distributed here.

## Local inputs

You must supply your own legitimately obtained game files. Keep originals strictly read-only; use separate paths for writable VFS and build outputs. Do not upload proprietary assets to GitHub or external services.

The pipeline expects, among other local artifacts:

```text
build/game/EBOOT.ELF
build/game/recomp/ppu_recomp.h
build/game/recomp/*.c or *.cpp
build/game/spu_images/
build/game/spu_lifted/
build/game/vfs/
```

The ELF must match the verified edition and fingerprint. We do not provide keys, decrypted ELFs, game assets, or extraction instructions. Do not disable hash checks to force a different game edition.

## Generation and building

Review scripts before running: they extract data from the local ELF, write to `build/`, and may initiate compilation. Some paths still reflect the original machine; adjust them in your copy.

```powershell
python scripts/rebuild_ppu.py
python scripts/rebuild_spu.py
python scripts/rebuild_spu_light.py
```

These commands require pre-arranged inputs; they do not download or produce game files on their own. The raw SPU module `pm_wwsjob.bin` requires base and entry `0xA00`; do not manually patch generated operands or source files.

With valid code generation and the SDK prepared, CMake accepts explicit directories:

```powershell
$projectRoot = (Get-Location).Path
cmake -S src/port -B build/dev -G Ninja "-DPS3RECOMP_DIR=$projectRoot/tools/ps3recomp" "-DRECOMP_DIR=$projectRoot/build/game/recomp" -DPORT_RUNNER_TESTS=ON
cmake --build build/dev
ctest --test-dir build/dev --output-on-failure
python -m pytest tests -q -ra
```

These are entry points for developers, not a verified sequence on a clean machine for this publication. Fix path and toolchain dependencies without altering guest semantics.

## Native runs and evidence collection

With the executable and VFS prepared, `scripts/probe_menu.py` limits execution time and gathers diagnostic artifacts. See `python scripts/probe_menu.py --help`. Post-intro captures can take several minutes; absence of frames in a short probe does not prove a hang.

Run only one GPU operation at a time. Do not modify the SDK, executable, or input files during a run. Record binary hash, parameters, logs, and termination criteria. Keep process audio muted while audio crackling remains unsolved.

Skip flags, auto-start, fast catalog, and FPS unlocks are diagnostic interventions. Forcing a path does not validate normal menu flow, control, or gameplay.
