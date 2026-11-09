# Public status — 9 November 2026

**Not playable.** The project has a locally built native Windows runner. Native diagnostic runs recorded menu input, loading of campaign dock resources, D3D12 draws, and partial 3D backbuffer images. They have not established controllable Cole movement, correct presentation throughout the sequence, usable performance, or reliable audio.

## Recorded observations

The local development log in the maintainer's private workspace records three November 7 witness sessions with the 91-patch SDK stack. One 900-second session recorded menu automation, resource opens including `h_jbi_cityfalls.bsub` and `h_jbi_bus.bsub`, 40,009 D3D12 draws, and 20 RSX capture sets. A later 1,800-second session recorded a frame counter reaching 2,277 and 84,009 draws. Another 1,200-second session produced 80 RSX capture sets. These are bounded observations from one local environment, not independent reproductions on a clean checkout.

The capture summary reports a display backbuffer with many colors in selected frames. Intermediate render targets can belong to different epochs; their contents do not prove what appeared in the window or that gameplay worked. A frame counter and elapsed wall time do not by themselves establish stable FPS. Input injection was recorded, but character movement and camera response remain unverified.

The local log reports no SPURS deadlock during those bounded runs after sequential multi-lane dispatch. This supports the change in the observed workloads, not a general claim that all deadlocks are solved. Audio behavior also needs a new controlled assessment; earlier sessions recorded crackling, and later qualitative reports alone do not establish clean output.

The raw runs, game assets, captures, and generated game code are intentionally absent from this repository. Their absence limits what a new contributor can independently verify from the public package. Synthetic fixtures and WARP GPU tests validate narrower contracts; they do not prove gameplay. On 9 November 2026, the complete 91-patch series reapplied to a clean checkout of the pinned SDK revision in the maintainer's local environment. The public regression gate then passed with 204 native tests, including six WARP fixtures, and 515 Python tests; 18 Python tests were skipped for documented optional, environment, or game-local conditions. After the synthetic frequency fixture was included in the standard build, a local rerun passed 204 native and 516 Python tests with 17 Python skips; the symlink permission test passed on that host. The static manifest, documentation links, and forbidden-file scan also passed.

## Current unknowns

1. Which scripted input or state is required to reach controllable character movement in the dock sequence.
2. Which rendering defects remain in presented frames, including depth and material behavior.
3. Whether audio and performance are usable under a comparable, repeatable native run.
4. Whether the full 91-patch series and port build reproduce from clean, legitimate local inputs on another machine; the full port build was not part of the patch replay check.

See the [handoff](MASTER-HANDOFF-AND-REPLICATION-GUIDE.md) for the next investigation, [patches](patches.md) for the reproducible series, and [regression testing](regression-testing.md) for synthetic checks. The project has no playable release or completion percentage.
