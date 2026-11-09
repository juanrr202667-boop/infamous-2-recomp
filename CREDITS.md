# Credits and references

This is an independent, experimental static recompilation project for inFAMOUS 2 on Windows. Credits acknowledge the origins and tools used; they do not imply participation or endorsement by the mentioned projects.

## Contributors

- **juans-collab** — Project creator, architecture, runtime integration, and port development.
- **OpenAI Codex / ChatGPT** — Automated code auditing, test fixture implementation, regression verification, and technical documentation assistance.

## Incorporated technical foundation

- **[PS3Recomp](https://github.com/sp00nznet/ps3recomp)**, by Ned Heller / sp00nznet and contributors: PPU/SPU recompilation toolchain, runtime, HLE interfaces, and RSX backend. The local base is pinned to commit `5244db92669b54831c6d3ebb7a96d58c4dbfa6df`, with local modifications reproduced by `scripts/rebuild_ppu.py` and `scripts/patches/`.
- Integration files in `src/port/`, including input, configuration, and build templates, originate from PS3Recomp. Patch files also contain context snippets of original code. The MIT license and copyright notice are preserved in [src/port/PS3Recomp-LICENSE](src/port/PS3Recomp-LICENSE).
- **FFmpeg / PPSSPP:** the SDK embeds a separate ATRAC3/ATRAC3+ decoder under LGPL-2.1-or-later. This exception must not be confused with the MIT license of the rest of the SDK. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Development and diagnostic tools

Local development history records use of the following tools. They are not redistributed with this project:

- [Python](https://www.python.org/), [LLVM/Clang](https://llvm.org/), [CMake/CTest](https://cmake.org/), and [Ninja](https://ninja-build.org/): automation, code translation, compilation, and testing.
- [pytest](https://docs.pytest.org/), [NumPy](https://numpy.org/), and [Pillow](https://python-pillow.org/): automated tests and local image/audio analysis.
- [Graphify](https://github.com/Graphify-Labs/graphify): local AST navigation for handwritten port code. Graph extraction does not validate runtime behavior; no game assets are uploaded.
- [RTK](https://github.com/rtk-ai/rtk): local command-output filtering, OSS edition 1.0.1 (Apache-2.0). Complete diagnostic artifacts remain available locally.
- [FFmpeg](https://ffmpeg.org/): reference decoder for local audio and video comparisons. This tool usage is distinct from the decoder embedded in the SDK.
- [RenderDoc](https://renderdoc.org/) and [DCC-MCP RenderDoc](https://github.com/dcc-mcp/dcc-mcp-renderdoc): local capture and inspection of D3D12 commands and resources. Real capture and replay are recorded in local diagnostics; this does not validate the accuracy of all rendering passes.
- [Ghidra](https://github.com/NationalSecurityAgency/ghidra) and [GhidraMCP](https://github.com/LaurieWired/GhidraMCP): local environment installed, with integration verified on a sample PowerPC binary. This does not represent complete reverse engineering of the game executable.
- [Microsoft PIX](https://devblogs.microsoft.com/pix/): installed, with basic availability confirmed. No PIX trace of the game is verified in examined documentation.

Tool versions, detailed evidence, and installation limits are documented in local governance records. The published summary is available in [docs/public-status.md](docs/public-status.md).

## Consulted technical references

- [Infamous-mod-manager](https://github.com/mentally-ill-kid/Infamous-mod-manager): static reference for PSARC/PACK file structures; the application was not executed in this project.
- [NoahBPeterson/ps3recomp](https://github.com/NoahBPeterson/ps3recomp): toolkit fork pinned by the erikomis reference at `770660a`. Its firmware LLE architecture is under static review; this does not replace our pinned SDK.
- [libPSARC](https://github.com/GaticusHax/libPSARC) and [PSL1GHT](https://github.com/ps3dev/PSL1GHT): public documentation on formats and commands.
- [RPCS3](https://github.com/RPCS3/rpcs3): static consultation of documentation, issues, pull requests, and RSX hardware contracts. It is not used to run tests, nor as this port's backend or runtime.
- **[erikomis/infamous2-recomp](https://github.com/erikomis/infamous2-recomp)**, by Erik Martins (erikomis): another independent static recompilation port of inFAMOUS 2 (release `NPUA80638`) to macOS/arm64 with Metal, built on the inFAMOUS 1 fork of ps3recomp and firmware LLE. Consulted as a **static reference** for its toolkit fixes, its list of upstream PS3Recomp pull requests, and its diagnostic recipes. The repository is MIT-licensed with additional provenance paragraphs. One small fix was adopted on 20/10/2026 — the image-ownership order in `spu_lifted_lookup` (our patch 55) — reimplemented locally and covered by our own RED/GREEN fixture. No other code from it has been incorporated, and none of its announced results are taken as evidence of execution for this project (different SKU and platform).
- [Whompay](https://www.youtube.com/watch?v=Z1dv128z5kM): independent external video demonstration of another title in the series. It does not provide code, documentation, or evidence applicable to this project.
- Public documentation on Direct3D 12, Microsoft Learn, Khronos Group, and PS3/Cell technical architecture specifications.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for full license texts and copyright notices.
