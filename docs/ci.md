# CI and result interpretation

CI proves that the **public, synthetic** surface still builds and tests. It does
not run the game, has no game files, and cannot validate gameplay.

## Jobs

| Job | Runner | What it does |
| --- | --- | --- |
| `repository-checks` | Ubuntu | Forbidden-file scan, patch manifest/order/hash, workflow YAML and PowerShell syntax |
| `python` | Ubuntu | `pytest tests`, with Windows/native fixtures skipped by design |
| `windows-tests` | Windows | Fresh pinned SDK clone, 91-patch apply, synthetic CMake build, CTest |
| `regression` | Windows | CTest + pytest JUnit comparison with the approved baseline |

All workflows use `permissions: contents: read`. They do not upload game
binaries, captures, logs with personal paths, or secrets.

## Reproducibility rules

- The SDK is cloned at the pinned commit from `scripts/rebuild_ppu.py`. CI fails
  if the checkout or patch series differs.
- `scripts/patch_stack.py --static` checks the manifest; `--apply` applies only
  the missing suffix after a dry-run.
- A missing game file is never replaced with a fake one. Jobs that need it are
  not enabled on public runners.
- `gpu-optional` CTest fixtures use WARP, not a physical GPU. Jobs may exclude
  them on headless runners; excluded tests are reported as excluded, not passed.
- The baseline is only valid for the recorded patch hash and environment. A
  patch change requires a reviewed baseline update.

## Reading a run

1. Confirm the **patch manifest** job was green. A green build with a different
   patch series is not comparable.
2. Read the CTest counts. `100% tests passed` means all *selected* tests passed;
   check the label filter for what was excluded.
3. Read the pytest summary. `passed` and `skipped` are separate; a skip is not a
   pass.
4. Check the regression report artifact:
   - `failed`: a previously passing test now fails.
   - `skipped_regressions`: a previously passing test is now skipped.
   - `missing`: a baseline test disappeared.
   - `new` / `new_skips`: informational until the baseline is reviewed.
5. Do not use CI as evidence of menu, input, rendering, audio, or gameplay.

## Local equivalent

```powershell
./scripts/setup.ps1 -CheckOnly
./scripts/build.ps1 -Target tests
./scripts/test.ps1 -Suite all
./scripts/test.ps1 -Regression
```

If CI differs from a local run, include the OS, compiler, CMake, patch prefix,
and the exact command. Strip personal paths before posting logs.
