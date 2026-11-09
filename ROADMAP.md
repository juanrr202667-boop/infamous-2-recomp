# Roadmap

**Current target:** demonstrate controllable movement and camera response in the campaign dock sequence, with a comparable native run and recorded evidence. The port is not playable today.

1. Verify the synthetic regression baseline on a clean checkout. The 91-patch SDK replay passed locally on 9 November 2026; independent reproduction remains open.
2. Reproduce the campaign transition with fixed local inputs, executable hash, duration, and capture settings.
3. Identify the tutorial state that accepts camera and movement input. Test one input change at a time.
4. Compare presented backbuffers with relevant intermediate surfaces and epochs; diagnose remaining graphics defects from observed draws.
5. Reassess audio quality and performance with bounded measurements.
6. Expand coverage to combat, missions, saving, and other areas only after the earlier milestones are demonstrated.

No completion percentage or release date is assigned. See [public status](docs/public-status.md) and the [handoff](docs/MASTER-HANDOFF-AND-REPLICATION-GUIDE.md).
