# Architecture

This is a static recompilation port, not an emulator wrapper. The game's PPU and
SPU code is translated ahead of time; PS3 services are provided by the pinned
PS3Recomp runtime and by local adapters.

For the exhaustive technical reference, see [Master Technical Handoff Guide](MASTER-HANDOFF-AND-REPLICATION-GUIDE.md).

## Build-time flow

```text
local EBOOT/ELF (read-only, never committed)
        |
        +-- ppu_lifter.py --> build/game/recomp/ppu_recomp.h + lifted PPU units
        +-- spu_lifter.py --> build/game/spu_lifted/<image>/spu_recomp.c
        |
CMake (src/port) ---> native executable + PS3Recomp runtime + local adapters
```

`PS3RECOMP_DIR` points at the toolkit checkout. `RECOMP_DIR` points at the
generated PPU output. The test-only build does not read either directory's game
inputs: it compiles the runtime and fixtures against
`tests/native/synthetic_recomp/ppu_recomp.h`.

## Runtime subsystems

| Area | Responsibility | Main local files |
| --- | --- | --- |
| Runner | Boot, frame loop, diagnostics, exit policy, menu FSM | `src/port/main.cpp`, `lfqueue.cpp`, `lwcond.cpp` |
| PPU | Lifted guest execution, syscalls, memory, ELFv1 ABI bridge | PS3Recomp `runtime/ppu/`, generated PPU units |
| SPU / SPURS | Interpreter/dispatch, LS (0xA00), MFC isolation, multi-lane SPURS | PS3Recomp `runtime/spu/`, `cellSpurs.c` |
| RSX | Guest command decoding, surface/view planning, MRT composite | PS3Recomp `libs/video/`, local patches |
| D3D12 | Pipeline, resources, presentation, WARP-capable fixtures, 16 textures | PS3Recomp `libs/video/rsx_d3d12_*` |
| Audio | cellAudio mixer, 48kHz WASAPI device output, headroom downmix | PS3Recomp `libs/audio/`, `src/port` |
| Input | cellPad HLE, programmatic stick injection, multi-hold `PAD_STICK` | PS3Recomp `libs/input/` |
| Filesystem/VFS | Guest paths, mmap catalog fastpath, overlapped AIO reads | PS3Recomp `runtime/syscalls/`, `ppu_fs.cpp` |
| Diagnostics | Native warnings, captures, telemetry, local dashboard | `src/port`, `src/diagnostics/` |

## Test seam

The synthetic header provides only game-agnostic types (`ppu_context`,
`func_entry`, `PPU_FENCE`). It contains no lifted function or asset. Fixtures
include production runtime sources directly where practical, so a change in the
SDK is exercised by the same code the port uses. They do not load the game, open
a window, or require a GPU; the D3D12 fixtures use WARP.

This seam is deliberately narrow. A fixture can prove a decoder, a clear rule,
or a queue contract. It cannot prove that a retail frame is correct, that the
interface is readable, or that gameplay progresses.

## Patch boundary

Local changes to PS3Recomp are kept as ordered patches in `scripts/patches/`,
not as raw edits in the ignored clone. The verified series consists of **91 patches**
on `5244db92669b54831c6d3ebb7a96d58c4dbfa6df`.
- **Patch 89** passes the 9th argument (`maxContention=5`) from guest stack `SP+0x70` to SPURS.
- **Patch 90** implements sequential multi-lane lockstep dispatch, eliminating thread creation churn and data races under `maxContention=5`.
- **Patch 91** implements `cellPad_inject_stick` and the multi-hold timed input scheduler.

See [patches](patches.md) for the full 91-patch inventory.

## Failure policy

- A missing game file is a configuration error, not a reason to synthesize one.
- A skipped test is reported as skipped, never as passed.
- A build success is not a gameplay result.
- Diagnostics must not print keys, save data, personal paths, or guest assets.
