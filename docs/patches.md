# PS3Recomp patch stack

The port pins PS3Recomp to `5244db92669b54831c6d3ebb7a96d58c4dbfa6df` and carries
**91 ordered patches** in `scripts/patches/`. The authoritative order is
`scripts/rebuild_ppu.py:PATCH_STACK`; `scripts/patches/manifest.json` records the
same order with SHA-256 hashes for every file. `scripts/patch_stack.py` verifies
the manifest and replays the series against a checkout without modifying it.
Patch 90 is the SPURS multi-lane sequential lockstep dispatch solving concurrency
deadlocks, and Patch 91 is the cellPad analog stick programmatic injection.

## What is and is not documented

The repository records the patch **name, order, touched files, hash and line
counts**. It does **not** record a reliable historical objective or original
bug report for every patch. The manifest therefore marks those fields as
`not documented` instead of inventing a story. Test associations are derived
from fixture groups and patch names; they show where coverage is expected, not
proof that a given test exercises every changed line.

## Ordered inventory

| # | Patch | Subsystem | Files | Associated fixture group |
| ---: | --- | --- | ---: | --- |
| 1 | `ppu-tail-return.patch` | PPU | 1 | `tests/test_ppu_tail_jump_table.py` |
| 2 | `fs-directory-entries.patch` | filesystem | 1 | `fs_entries_*` |
| 3 | `spurs-event-routing.patch` | SPU/SPURS | 1 | `spurs_explicit_events*` |
| 4 | `spu-interpreter-handoff.patch` | SPU | 1 | `spu_handoff_*` |
| 5 | `spu-microstep-absolute.patch` | SPU | 1 | `spu_handoff_absolute_*` |
| 6 | `d3d12-hidden-capture.patch` | RSX/D3D12 | 1 | — |
| 7 | `d3d12-fp-export.patch` | RSX/D3D12 | 1 | — |
| 8 | `spu-receive-event.patch` | SPU | 6 | `spu_receive_*` |
| 9 | `spu-thread-event-routing.patch` | SPU | 1 | `spu_thread_event_*` |
| 10 | `spu-interpreter-mpyui.patch` | SPU | 1 | `spu_multiply_*` |
| 11 | `spu-zero-dma.patch` | SPU | 1 | `spu_dma_*` |
| 12 | `spu-interpreter-rothmi.patch` | SPU | 1 | `spu_shift_*` |
| 13 | `spu-orbi-decode.patch` | SPU | 3 | `spu_orbi_*` |
| 14 | `spu-policy-data-abi.patch` | SPU | 1 | `spu_policy_argument_*` |
| 15 | `spu-interpreter-rotmah.patch` | SPU | 1 | `spu_rotmah_*` |
| 16 | `spu-xorbi-decode.patch` | SPU | 3 | `spu_xorbi_*` |
| 17 | `audio-device-stats.patch` | audio | 1 | `audio_*`, `tests/test_audio_*` |
| 18 | `audio-port-stats.patch` | audio | 1 | `audio_*`, `tests/test_audio_*` |
| 19 | `rsx-shader-bind-stats.patch` | RSX | 1 | `tests/test_rsx_fp_sampler_mask.py` |
| 20 | `rsx-immediate-vertices.patch` | RSX | 3 | `rsx_immediate_*` |
| 21 | `texture-plane-capture.patch` | RSX | 1 | `tests/test_rsx_capture_trigger.py` |
| 22 | `d3d12-guest-screen-texture.patch` | RSX/D3D12 | 1 | `rsx_mrt_display_*` |
| 23 | `spu-hot-trace-gates.patch` | SPU | 4 | `spu_hot_trace_*` |
| 24 | `d3d12-sixteen-texture-units.patch` | RSX/D3D12 | 2 | `rsx_guest_texture_*` |
| 25 | `spu-task-signal-wake.patch` | SPU | 2 | `spu_task_wake_*` |
| 26 | `ppu-vmx-exp-log-estimates.patch` | PPU | 1 | `tests/test_ppu_vmx_estimates.py` |
| 27 | `gcm-flip-context-ownership.patch` | GCM | 2 | `gcm_flip_wrap_*` |
| 28 | `gcm-small-context-flip.patch` | GCM | 1 | `gcm_flip_wrap_*` |
| 29 | `gcm-self-jump-park.patch` | GCM | 1 | `gcm_flip_wrap_*` |
| 30 | `d3d12-mrt-display-composite.patch` | RSX/D3D12 | 3 | `rsx_mrt_display_*` |
| 31 | `d3d12-frame-count-deferred-release.patch` | RSX/D3D12 | 1 | `tests/test_d3d12_present_gate.py` |
| 32 | `audio-port-level-downmix-headroom.patch` | audio | 1 | `audio_port_initial_level` |
| 33 | `cellpad-script-buffer-expansion.patch` | input | 1 | `tests/test_skip_and_auto_transition.py` |
| 34 | `audio-amplitude-stats.patch` | audio | 1 | `audio_amplitude_*` |
| 35 | `audio-initial-level-contract.patch` | audio | 1 | `audio_port_initial_level` |
| 36 | `d3d12-completed-flip-present.patch` | RSX/D3D12 | 1 | `tests/test_d3d12_present_gate.py` |
| 37 | `rsx-capture-after-file.patch` | RSX | 1 | `tests/test_rsx_capture_trigger.py` |
| 38 | `d3d12-offscreen-capacity.patch` | RSX/D3D12 | 2 | `tests/test_d3d12_offscreen_capacity.py` |
| 39 | `d3d12-small-render-targets.patch` | RSX/D3D12 | 1 | `tests/test_d3d12_small_targets.py` |
| 40 | `d3d12-stable-vertex-cache.patch` | RSX/D3D12 | 1 | `tests/test_d3d12_vertex_cache.py` |
| 41 | `d3d12-surface-metadata.patch` | RSX/D3D12 | 2 | `tests/test_d3d12_surface_metadata.py` |
| 42 | `d3d12-color-clear-flags.patch` | RSX/D3D12 | 1 | `tests/test_d3d12_clear_color_flags.py` |
| 43 | `native-sdk-local-baseline.patch` | SDK baseline | 28 | — |
| 44 | `d3d12-temporal-replay.patch` | RSX/D3D12 | 3 | `tests/test_d3d12_temporal_replay.py` |
| 45 | `d3d12-guest-target-seed.patch` | RSX/D3D12 | 1 | `tests/test_d3d12_display_clear.py` |
| 46 | `d3d12-exact-view-feedback.patch` | RSX/D3D12 | 4 | `tests/test_d3d12_feedback.py` |
| 47 | `d3d12-display-surface-coherence.patch` | RSX/D3D12 | 6 | `tests/test_d3d12_display_coherence.py` |
| 48 | `d3d12-no-color-targets.patch` | RSX/D3D12 | 4 | `tests/test_d3d12_no_color_target.py` |
| 49 | `d3d12-fragment-constant-snapshots.patch` | RSX/D3D12 | 3 | `tests/test_d3d12_fragment_constants.py` |
| 50 | `d3d12-depth-clear-handling.patch` | RSX/D3D12 | 2 | `tests/test_d3d12_depth_clear.py` |
| 51 | `rsx-depth-format-alias.patch` | RSX/D3D12 | 1 | — |
| 52 | `vfs-overlapped-aio-read.patch` | VFS | 2 | `tests/test_vfs_overlapped_aio.py` |
| 53 | `catalog-fastpath-mmap.patch` | VFS | 1 | `tests/test_fast_catalog_integrity.py` |
| 54 | `spu-wws-image-coherency.patch` | SPU | 1 | `tests/test_spu_wws_modules.py` |
| 55 | `spu-dispatch-owner-contract.patch` | SPU | 1 | `tests/test_spu_dispatch_ownership.py` |
| 56 | `spu-task-signal-routing.patch` | SPU | 1 | `spu_task_wake_*` |
| 57 | `spu-overlay-lookup.patch` | SPU | 2 | `spu_overlay_lookup_*` |
| 58 | `spu-owned-span-no-fallback.patch` | SPU | 1 | `spu_overlay_lookup_*` |
| 59 | `d3d12-nv3089-guest-writeback.patch` | RSX/D3D12 | 1 | `nv3089_*` |
| 60 | `d3d12-surface-dma-metadata.patch` | RSX/D3D12 | 2 | `tests/test_rsx_surface_dma.py` |
| 61 | `d3d12-surface-dma-manifest.patch` | RSX/D3D12 | 2 | `tests/test_rsx_surface_dma.py` |
| 62 | `d3d12-linear-view-copy.patch` | RSX/D3D12 | 2 | `tests/test_d3d12_linear_copy.py` |
| 63 | `ppu-hle-snapshot.patch` | PPU | 1 | `hle_snapshot_*` |
| 64 | `ppu-light-loop-observer.patch` | PPU | 1 | `tests/test_light_loop_lift.py` |
| 65 | `ppu-light-wait-observer.patch` | PPU | 1 | `tests/test_light_wait_lift.py` |
| 66 | `ppu-light-callsite-observer.patch` | PPU | 1 | `tests/test_light_callsite_lift.py` |
| 67 | `spu-mfc-isolation-barrier.patch` | SPU | 2 | `tests/native/spu_mfc_isolation_probe.cpp` |
| 68 | `spu-frequency-profile.patch` | SPU | 1 | `tests/native/smc_frequency/` |
| 69 | `antigravity-keyboard.patch` | Input | 1 | `scripts/patch_cellpad_keyboard.py` |
| 70 | `antigravity-pso-cache.patch` | RSX/D3D12 | 1 | — |
| 71 | `test_d3d12.patch` | RSX/D3D12 | 1 | `tests/test_d3d12_depth_test_gate.py` |
| 72 | `spu-plugin-staging.patch` | SPU | 1 | `tests/test_spu_plugins.py` |
| 73 | `spu-wws-staging.patch` | SPU | 1 | `tests/test_spu_wws_modules.py` |
| 74 | `rsx-fp-bx2-texture.patch` | RSX | 1 | `tests/test_rsx_fp_bx2_texture.py` |
| 75 | `rsx-fp-predicated-write.patch` | RSX | 1 | `tests/test_rsx_fp_predicated_write.py` |
| 76 | `rsx-fp-pack-unpack.patch` | RSX | 1 | `tests/test_rsx_fp_pack_unpack.py` |
| 77 | `rsx-fp-sampler-mask.patch` | RSX | 1 | `tests/test_rsx_fp_sampler_mask.py` |
| 78 | `rsx-linear-catalog.patch` | RSX | 1 | `tests/test_rsx_linear_catalog.py` |
| 79 | `rsx-linear-coherence.patch` | RSX | 1 | `tests/test_rsx_linear_coherence.py` |
| 80 | `rsx-linear-metadata.patch` | RSX | 1 | `tests/test_rsx_linear_metadata.py` |
| 81 | `rsx-linear-view.patch` | RSX | 1 | `tests/test_rsx_linear_view.py` |
| 82 | `rsx-vs-compile-trace.patch` | RSX | 1 | — |
| 83 | `rsx-vs-working-set.patch` | RSX | 1 | — |
| 84 | `ppu-sampler-clear-windows.patch` | PPU | 1 | `tests/test_sampler_report_windows.py` |
| 85 | `frame-timeline.patch` | Runner | 1 | `tests/native/frame_clock_probe.cpp` |
| 86 | `ftrace-alternating-sleeps.patch` | Kernel | 1 | `tests/test_ftrace_contract.py` |
| 87 | `spurs-attribute-abi.patch` | SPURS | 1 | `tests/native/spurs_abi/` |
| 88 | `d3d12-fragment-capture-state.patch` | RSX/D3D12 | 1 | `tests/test_d3d12_fp_capture.py` |
| 89 | `spurs-workload-stack-argument.patch` | PPU/SPURS | 2 | `tests/native/spurs_abi/probe.cpp` |
| 90 | `spurs-multilane-sequential-dispatch.patch` | SPURS | 1 | `tests/test_skip_and_auto_transition.py` |
| 91 | `cellpad-stick-injection.patch` | Input | 2 | `tests/test_skip_and_auto_transition.py` |

## Verification

```powershell
python scripts/generate_patch_manifest.py --check
python scripts/patch_stack.py --static
python scripts/patch_stack.py --check --json
```

`--static` verifies names, order and hashes without an SDK checkout. `--check`
replays the series in a temporary tree and reports the applied prefix and the
first failing patch. `--apply` writes only the missing suffix after that check.

## SPURS multi-lane dispatch (patch 90)

Patch 90 (`spurs-multilane-sequential-dispatch.patch`) replaces per-quantum `CreateThread`
churn in `spurs_pm_worker` with sequential multi-lane dispatch on the kernel thread.
When `maxContention=5` is requested, virtual SPUs 0..4 advance
sequentially. Local 300s, 1200s, and 1800s witness runs recorded no deadlock.
This does not establish deadlock freedom for all workloads.

## Analog stick injection and timed holds (patch 91)

Patch 91 (`cellpad-stick-injection.patch`) introduces `cellPad_inject_stick` and
a timed hold scheduler supporting up to 32 holds in `PAD_STICK`, enabling scripted
scripted input for local diagnostics. Game acceptance of tutorial input and
controllable movement remain unverified.
