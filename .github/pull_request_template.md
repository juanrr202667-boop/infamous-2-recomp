## Summary

<!-- What problem does this change solve? One bounded cause per PR. -->

## Scope

- [ ] Runtime behavior
- [ ] Build / toolchain
- [ ] Tests or fixtures
- [ ] Documentation
- [ ] CI / repository infrastructure

## Evidence

<!-- Paste sanitized test commands and result counts. State skips and excluded tests. -->

```text
command:
result:
```

## Checks

- [ ] `./scripts/setup.ps1 -CheckOnly`
- [ ] `./scripts/build.ps1 -Target tests`
- [ ] `./scripts/test.ps1 -Suite all`
- [ ] `python scripts/check_forbidden_files.py --tracked`
- [ ] `python scripts/patch_stack.py --static`
- [ ] Regression gate reviewed (if outcomes changed)

## Proprietary material

- [ ] No EBOOT/ELF, ISO, PKG, save, firmware, key, extracted game asset, GPU capture, or memory dump is included.
- [ ] No personal path or secret is present in logs, screenshots, or code.
- [ ] Any raster screenshot has runtime provenance, limits and copyright attribution; it does not claim gameplay from a still image.
- [ ] Synthetic fixtures are used where the defect can be reduced.

## Claims and limitations

<!-- What does this prove? What remains untested? Do not claim gameplay from a build or fixture. -->

## Risks

<!-- Known risks, rollback plan, and any behavior that was preserved intentionally. -->

## Related issues

<!-- Closes #... only when the acceptance criteria are actually met. -->
