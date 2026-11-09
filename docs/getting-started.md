# Getting started

The synthetic test suite does **not** need the game. The full port does, and the
public repository does not distribute any game file.

## Requirements

Windows x64 with:

- Git, Python 3.11+, CMake 3.25+, Ninja
- LLVM/clang-cl (the repository expects `tools/llvm/bin/clang-cl.exe` or
  `clang-cl` on PATH)
- Visual Studio Build Tools with the C++ workload
- Windows 10/11 SDK (for `rc.exe`, `d3dcompiler.lib`, and Windows headers)

Run the checker first:

```powershell
./scripts/setup.ps1 -CheckOnly
```

It reports missing tools and writes `build/setup/environment.json`. Add
`-InstallDependencies` to install public tools through winget where possible;
Visual Studio Build Tools and some SDK components may require administrator
rights and are not installed automatically.

## Prepare the pinned SDK

```powershell
./scripts/setup.ps1
```

This creates `.venv`, installs `requirements-dev.txt`, clones PS3Recomp at the
pinned revision into `tools/ps3recomp`, and verifies the 91-patch series. It
never downloads game files. If the checkout already has local changes, setup
reports the divergence and preserves it.

## Build and test without the game

```powershell
./scripts/build.ps1 -Target tests
./scripts/test.ps1 -Suite all
```

Or use CMake presets directly from a Developer PowerShell:

```powershell
cmake --preset windows-tests
cmake --build --preset windows-tests
ctest --preset windows-tests
python -m pytest tests -q -ra
```

The test build uses `tests/native/synthetic_recomp/ppu_recomp.h`. It must not
read `build/game/recomp/` even if that directory exists.

### Selecting categories

CTest labels are `cpu`, `sdk`, `windows`, `gpu-optional`, and `game-local`.

```powershell
ctest --test-dir build/windows-tests -LE "gpu-optional|game-local"
ctest --test-dir build/windows-tests -L sdk
./scripts/test.ps1 -Suite native -IncludeGpu
```

An excluded test was not run and must not be reported as a pass.

## Full port (local game inputs required)

You must supply your own legitimately obtained game files and keep the
originals read-only. The full build expects a verified local ELF and generated
PPU/SPU output; see [building](building.md). Do not commit or upload those
files.

```powershell
./scripts/build.ps1 -Target port
```

## Regression gate

```powershell
./scripts/test.ps1 -Regression
python scripts/regression_gate.py --build-dir build/windows-tests --ctest --pytest
```

The gate compares fixture outcomes with `tests/baselines/regression-gate.json`.
Update the baseline only with `--update-baseline` after reviewing every change;
it refuses to record a failing run.

## Where to go next

- [Architecture](architecture.md)
- [Patch stack](patches.md)
- [CI and result interpretation](ci.md)
- [Reproducing defects](reproducing-defects.md)
- [Contributing](../CONTRIBUTING.md)
