# Repository work rules

- Treat this as an experimental port. Do not call it playable until a native run demonstrates responsive input, controllable movement, camera control, and progression.
- State observed facts, hypotheses, and unknowns separately. Keep [public status](docs/public-status.md) current and date older evidence.
- Never commit game binaries, generated game code, extracted assets, firmware, keys, saves, audio, GPU traces, or memory dumps. Raster captures need native-run provenance, limitations, and rights attribution.
- Keep PS3Recomp pinned. Represent SDK changes in the ordered patch stack and verify complete replay after changes.
- Use `./scripts/test.ps1 -Regression -IncludeGpu` for the GPU-inclusive baseline. Inspect pass, failure, and skip counts; excluded tests are not passes.
- For compiler-backed Python fixtures, set `I2_CLANG_CL` and writable `TMP`/`TEMP`. A green gate with required fixtures skipped is insufficient.
- Before pushing, stage only intended files. Run `python scripts/check_publication_manifest.py --update`, stage the generated manifest, then run the non-update checker, patch manifest/order checks, forbidden-file scan, and documentation link check. Review the full diff and license notices.
- Do not edit generated PPU/SPU code to hide defects. Do not update third-party clones automatically.
