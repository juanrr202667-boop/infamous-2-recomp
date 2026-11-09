# inFAMOUS 2 Recomp

Experimental Windows port of the PlayStation 3 BCUS98125 release, built with a pinned [PS3Recomp](https://github.com/sp00nznet/ps3recomp) revision and a local patch series.

**Status (November 2026): not playable.** Local native runs have reached the campaign dock sequence and produced partial 3D backbuffer images. Controllable character movement, correct rendering, reliable audio, and acceptable performance have not been established. See [observed status](docs/public-status.md) for the evidence and its limits.

The original maintainer has stepped away from active development and published this snapshot so others can continue the work. The technical handoff records the current state, reproducible checks, open questions, and next investigation. There is no planned release date or promise of ongoing maintainer support.

This is an independent research project. It is not affiliated with or endorsed by Sony Interactive Entertainment or Sucker Punch Productions.

## Continue the work

Start with the [technical handoff](docs/MASTER-HANDOFF-AND-REPLICATION-GUIDE.md), then read [getting started](docs/getting-started.md), [the patch stack](docs/patches.md), and [regression testing](docs/regression-testing.md). The handoff identifies the current unknowns and the next bounded investigation.

The repository includes handwritten integration code, build and diagnostic scripts, synthetic tests, and ordered patches for PS3Recomp. The pinned third-party SDK is fetched separately. The game executable, generated translations of game code, assets, firmware, keys, saves, dumps, and a runnable game binary are not distributed.

## Test without the game

On Windows with Python, CMake, Ninja, clang-cl, and the Windows SDK:

```powershell
./scripts/setup.ps1 -CheckOnly
./scripts/setup.ps1
./scripts/build.ps1 -Target tests
./scripts/test.ps1 -Regression -IncludeGpu
```

The WARP fixtures require a suitable graphics environment. Record pass, failure, and skip counts; a skipped fixture is not a pass. The full port also requires the contributor's own legitimately obtained local game files and generated code. See [building](docs/building.md).

## Publication and licensing

Before sharing changes, run the [publication checks](CONTRIBUTING.md). Keep proprietary material and personal paths out of commits and history. Patch files contain context from the MIT-licensed PS3Recomp project; preserve its notices and the other exceptions in [third-party notices](THIRD_PARTY_NOTICES.md). Original contributions are licensed under [GPL-3.0-only](LICENSE), subject to the documented third-party terms.

No repository check can guarantee that a rights holder will not file a complaint. Share only material you have the right to publish.
