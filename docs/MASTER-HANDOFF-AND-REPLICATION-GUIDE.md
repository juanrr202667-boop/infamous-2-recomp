# Technical handoff and replication guide

This is an experimental native Windows port of *inFAMOUS 2* (BCUS98125). It uses PS3Recomp pinned to `5244db92669b54831c6d3ebb7a96d58c4dbfa6df` plus an ordered local patch series. **The game is not playable.** Read [public status](public-status.md) before interpreting any run or capture.

The original maintainer is no longer actively developing the port. This repository is a handoff for contributors who want to continue from the documented state. The steps below are intended to make the next investigation concrete without implying that the game already runs correctly.

## What the repository contains

| Path | Role |
| --- | --- |
| `src/port/` | Handwritten native runner, integration, and instrumentation |
| `src/diagnostics/` | Local diagnostic interface |
| `scripts/rebuild_ppu.py` | PPU generation driver and authoritative `PATCH_STACK` |
| `scripts/rebuild_spu*.py` | Local SPU image preparation and lifting |
| `scripts/patches/` | SDK changes against the pinned upstream revision |
| `scripts/patches/manifest.json` | Patch order, hashes, and touched paths |
| `scripts/probe_menu.py` | Bounded local run and capture entry point |
| `scripts/check_*.py` | Publication, documentation, and patch checks |
| `tests/` | Synthetic native, Python, and optional WARP GPU fixtures |
| `docs/` | Build, architecture, status, and testing guides |

The upstream SDK clone, game data, generated game code, binaries, logs, dumps, and captures remain local. A contributor needs their own legitimate game inputs for the full port. The public synthetic tests require no game files.

## Current technical state

A local November 7 run recorded title/menu input automation, opening of campaign dock resources, D3D12 draw calls, and selected partial 3D images in the display backbuffer. The current manifest names 91 patches; its final entries include SPURS workload argument handling, sequential multi-lane dispatch, and analog stick injection. The local runs support only bounded conclusions. They do not prove normal gameplay, a responsive physical controller, correct graphics, stable FPS, or clean audio. See [public status](public-status.md) for observed facts and limits.

The SPU raw module `pm_wwsjob.bin` is loaded at local-store base `0xA00`; its wrapper must use base and entry `0xA00`. Rebuild through `scripts/rebuild_spu.py`. Do not edit generated code or rebase absolute operands to mask a runtime defect.

RSX captures require comparing the display backbuffer and relevant render targets, including smaller surfaces and their epochs. An intermediate image may be stale or belong to a different instant. A colorful target alone does not establish presentation or gameplay.

## Reproduce the public checks

Use Windows, Python 3.11+, Git, CMake, Ninja, clang-cl, Visual Studio C++ Build Tools, and the Windows SDK. Run from a fresh checkout:

```powershell
./scripts/setup.ps1 -CheckOnly
./scripts/setup.ps1
python scripts/generate_patch_manifest.py --check
python scripts/patch_stack.py --static
./scripts/build.ps1 -Target tests
./scripts/test.ps1 -Regression -IncludeGpu
```

`setup.ps1` fetches the pinned public SDK into `tools/ps3recomp` and applies local patches; inspect any existing local changes before repeating setup. For compiler-backed Python fixtures, set `I2_CLANG_CL` to a working compiler and `TMP`/`TEMP` to a writable local directory. Inspect JUnit/log totals for passes, failures, and skips. A green gate with skipped required fixtures is insufficient.

For the game-dependent build, see [building](building.md). The generated PPU/SPU code and local VFS are deliberately not published. The public repository cannot reproduce a native game run on its own.

## Next bounded investigation

The immediate question is whether scripted tutorial input can advance the dock sequence to a state with observable camera and character movement. A reference from another port suggests right-stick look prompts, but that reference uses a different game release and platform, so treat its timing and controls as a hypothesis.

1. Pin the candidate executable hash, game edition, patch manifest, input schedule, and run limit.
2. Record a control with the current input sequence.
3. Change one right-stick direction or timing interval, then compare with the control at the same observed phase.
4. Record accepted input, camera state, character coordinates if available, frame timing, and display backbuffer. Keep capture epochs and intermediate targets separate.
5. If movement is absent, identify the next blocked state from logs and code before repeating a long run.

A recorded input pulse proves delivery to the port, not acceptance by the game. A build or synthetic fixture proves only its tested contract. Do not describe the port as playable until a native run demonstrates responsive movement, camera control, and progression.

## Publication and attribution

Publish only handwritten source, synthetic fixtures, and patches you have the right to distribute. Preserve [credits](../CREDITS.md), the [license](../LICENSE), and [third-party notices](../THIRD_PARTY_NOTICES.md). Do not publish game assets, translated game executable sources, firmware, keys, saves, logs with personal paths, memory/GPU dumps, or raw captures. Before any push, stage only intended files and run the checks in [contributing](../CONTRIBUTING.md). These checks reduce mistakes; they cannot guarantee the absence of a copyright complaint.
