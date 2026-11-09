# Contributing guidelines

This project is currently not playable. We welcome small, verifiable fixes in RSX/D3D12, depth handling, shaders, SPU protocols, and audio. Review the [current status](docs/public-status.md) before opening an issue or PR.

## Content policy and licensing

- Do not submit ROMs, DLC, save files, EBOOT/ELF binaries, firmware, encryption keys, game assets, decompiled game executable sources, memory dumps, or GPU traces.
- Use synthetic inputs and minimal reproduction cases free of proprietary materials.
- Preserve authorship and third-party licenses; distinguish incorporated code from consulted technical references.
- Original contributions are accepted under GPL-3.0-only. Only submit work that you have the right to distribute under these terms, with third-party exceptions explicitly declared.

## Before you start

Run the public checks that do not need the game:

```powershell
./scripts/setup.ps1 -CheckOnly
./scripts/build.ps1 -Target tests
./scripts/test.ps1 -Suite all
python scripts/check_forbidden_files.py --tracked
python scripts/patch_stack.py --static
```

Use a separate build directory for a second toolchain. Do not reset a dirty SDK
checkout; `scripts/patch_stack.py --check --json` reports divergence without
writing. See [getting started](docs/getting-started.md) and [CI](docs/ci.md).

## Investigating and fixing defects

1. Separate observed facts from hypotheses and unknowns.
2. Reproduce defects against production implementations. For graphics, use pixel readbacks when relevant.
3. Fix one bounded root cause at a time; avoid changing multiple subsystems simultaneously to force frames to render.
4. Record test verifications, skips, and diagnostic limits. A passing build does not prove gameplay.
5. SDK modifications require patches against the pinned commit and reproducible patch stack application. Do not update git clones automatically.
6. Do not edit generated PPU/SPU code to hide defects. Fix code generators, configuration, runtime routines, or integration adapters according to the cause.

RPCS3 may be consulted as a static reference for code, NIDs, or formats. Do not run it as this project's test suite, backend, or runtime.

## Before publishing changes

Keep [public status](docs/public-status.md) current and date older evidence. Do not call the port playable without a native run demonstrating responsive input, controllable movement, camera control, and progression. Raster captures require native-run provenance, limitations, and rights attribution.

Run `./scripts/test.ps1 -Regression -IncludeGpu` when checking the GPU-inclusive baseline. Inspect passed, failed, and skipped totals; excluded tests do not count as passes. Compiler-backed Python fixtures need `I2_CLANG_CL` and writable `TMP`/`TEMP` directories.

Stage only intended files. Run `python scripts/check_publication_manifest.py --update`, stage `PUBLICATION_MANIFEST.json`, then run the verifier without `--update`. Check patch manifest and order, scan for forbidden files, validate documentation links, and review the full diff and third-party notices before pushing.

## Reporting issues

Provide commit revision, operating system, GPU and driver version when relevant, exact command lines, effective environment variables, and the first observed error. Strip personal information and secrets. Do not attach proprietary game assets; provide minimal synthetic repros and technical summaries.

## Concurrent work

Declare files under your ownership and use separate build trees. Run only one GPU operation at a time. The test candidate and its inputs must remain immutable during witness runs. Disclose AI assistance tools used; they do not replace human review or licensing authorization.
