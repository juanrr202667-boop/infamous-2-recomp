# Progress

The current [public status](docs/public-status.md) is the canonical summary. The 7 November 2026 local sessions reached the campaign dock resource load and produced partial 3D backbuffer captures. The latest local SDK patch manifest lists 91 ordered patches. Neither observation establishes playable gameplay or a portable full build.

## Verified from the public tree

- The 91 ordered patches reapplied to a clean copy of pinned PS3Recomp revision `5244db92669b54831c6d3ebb7a96d58c4dbfa6df` in the maintainer's local environment on 9 November 2026. Contributors should repeat the check on their own machines.
- The public regression gate passed locally on 9 November 2026: 204 native tests passed, including six WARP fixtures; 515 Python tests passed and 18 were skipped for documented conditions.
- The native port still requires separately supplied game inputs and generated code.

## Next milestone

Reproduce the campaign transition with a fixed executable hash and inputs, then measure whether tutorial input produces camera and character movement. Record the presented backbuffer, relevant intermediate surfaces, audio evidence, and timing separately. See the [handoff](docs/MASTER-HANDOFF-AND-REPLICATION-GUIDE.md).
