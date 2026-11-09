# Regression testing

The gate compares deterministic fixture outcomes with an approved baseline. It
is intentionally modest: it catches a fixture that regressed, disappeared, or
silently became a skip. It does not compare pixels, audio samples, or gameplay.

## What it covers

| Subsystem | Fixture families |
| --- | --- |
| PPU | VMX estimates, tail jump table, handoff probes |
| SPU | Decode, DMA, channels, tasksets, event routing, hot-trace gates |
| RSX/GCM | Immediate vertices, FIFO flips, flips and context ownership |
| D3D12 | View planning, clears, surface metadata, temporal replay, WARP pipelines |
| Audio | Mixer levels, amplitude statistics, downmix contracts |
| Filesystem/VFS | Directory entries, overlapped reads |
| Python | Analysis, generation, and contract tests |

The D3D12 fixtures use WARP and run without a window. A green gate does not mean
that a retail frame is correct.

The 9 November 2026 public baseline recorded 204 passing CTest fixtures,
including six WARP cases, and 515 passing Python tests. Eighteen Python cases
were skipped: eleven optional GPU cases, two superseded checks, one game-local
ELF check, one symlink permission check, and three native game boot checks.
The skip list is recorded in the JUnit report; none is counted as a pass.

## Baseline policy

- `tests/baselines/regression-gate.json` is versioned and reviewed.
- It records the patch manifest hash. A different patch series fails the gate
  until the baseline is re-approved.
- `--update-baseline` refuses a run with failures. It does not hide a skip: the
  new baseline records `skipped` for that test.
- Review the diff before approving. A baseline update is a claim that the new
  outcome is expected.

## Running

```powershell
./scripts/test.ps1 -Regression
python scripts/regression_gate.py --build-dir build/windows-tests --ctest --pytest
python scripts/regression_gate.py --build-dir build/windows-tests --ctest --pytest --json
```

Add `--include-gpu` to include the WARP D3D12 fixtures. Without it, they are
excluded and absent from both the run and the comparison.

## Adding a fixture

1. Add a deterministic test under `tests/native/` or `tests/`.
2. Give it the appropriate CTest labels (`cpu`, `sdk`, `windows`, optionally
   `gpu-optional`).
3. Run it locally, then update the baseline explicitly.
4. In the PR, state whether the new test proves a parser contract, a runtime
   behavior, or only compilation. Do not call it gameplay coverage.
