"""Generate the reviewed manifest for the pinned PS3Recomp patch stack.

The manifest records facts that can be derived from the repository: execution
order, SHA-256, touched files and line counts. It deliberately does not invent
historical motivations. Fields that the repository does not document are
marked "not documented" and must be filled only after evidence is found.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import rebuild_ppu  # noqa: E402

SUBSYSTEMS = (
    ("ppu-", "PPU"),
    ("spu-", "SPU"),
    ("spurs-", "SPU/SPURS"),
    ("rsx-", "RSX"),
    ("d3d12-", "RSX/D3D12"),
    ("gcm-", "GCM"),
    ("audio-", "audio"),
    ("fs-", "filesystem"),
    ("vfs-", "VFS"),
    ("cellpad-", "input"),
    ("catalog-", "catalog"),
    ("texture-", "RSX"),
    ("native-sdk-", "SDK baseline"),
)

TESTS = {
    "ppu-tail-return": ["tests/test_ppu_tail_jump_table.py"],
    "fs-directory-entries": ["fs_entries_*"],
    "spurs-event-routing": ["spurs_explicit_events*"],
    "spu-interpreter-handoff": ["spu_handoff_*"],
    "spu-smc-frequency": ["tests/native/smc_frequency/test_frequency.py"],
    "rsx-capture-presented-only": ["tests/test_rsx_capture_trigger.py"],
    "rsx-vs-compile-trace": ["tests/test_d3d12_vertex_cache.py"],
    "rsx-vs-working-set": ["tests/test_d3d12_vertex_cache.py"],
    "ppu-sampler-clear-windows": ["tests/test_sampler_report_windows.py"],
    "frame-timeline": ["tests/test_ftrace_contract.py"],
    "ftrace-alternating-sleeps": ["tests/test_ftrace_contract.py"],
    "spurs-attribute-abi": ["tests/native/spurs_abi/probe.cpp"],
    "spurs-workload-stack-argument": ["tests/native/spurs_abi/probe.cpp"],
    "spu-microstep-absolute": ["spu_handoff_absolute_*"],
    "spu-receive-event": ["spu_receive_*"],
    "spu-thread-event-routing": ["spu_thread_event_*"],
    "spu-interpreter-mpyui": ["spu_multiply_*"],
    "spu-zero-dma": ["spu_dma_*"],
    "spu-interpreter-rothmi": ["spu_shift_*"],
    "spu-orbi-decode": ["spu_orbi_*"],
    "spu-policy-data-abi": ["spu_policy_argument_*"],
    "spu-interpreter-rotmah": ["spu_rotmah_*"],
    "spu-xorbi-decode": ["spu_xorbi_*"],
    "spu-task-signal-wake": ["spu_task_wake_*"],
    "spu-overlay-lookup": ["spu_overlay_lookup_*"],
    "spu-owned-span-no-fallback": ["spu_overlay_lookup_owner-unlifted"],
    "audio-device-stats": ["audio_*", "tests/test_audio_*"],
    "audio-port-stats": ["audio_*", "tests/test_audio_*"],
    "audio-amplitude-stats": ["audio_amplitude_*", "tests/test_audio_*"],
    "audio-initial-level-contract": ["audio_port_initial_level", "tests/test_audio_*"],
    "audio-port-level-downmix-headroom": ["audio_port_initial_level", "tests/test_audio_downmix.py"],
    "rsx-shader-bind-stats": ["tests/test_rsx_fp_sampler_mask.py", "tests/test_rsx_surface_dma.py"],
    "rsx-immediate-vertices": ["rsx_immediate_*"],
    "texture-plane-capture": ["tests/test_rsx_capture_trigger.py"],
    "d3d12-guest-screen-texture": ["rsx_mrt_display_*", "tests/test_d3d12_display_bridge.py"],
    "spu-hot-trace-gates": ["spu_hot_trace_*"],
    "d3d12-sixteen-texture-units": ["rsx_guest_texture_*", "tests/test_rsx_fp_sampler_mask.py"],
    "ppu-vmx-exp-log-estimates": ["tests/test_ppu_vmx_estimates.py"],
    "gcm-flip-context-ownership": ["gcm_flip_wrap_*"],
    "gcm-small-context-flip": ["gcm_flip_wrap_*"],
    "gcm-self-jump-park": ["gcm_flip_wrap_*"],
    "d3d12-mrt-display-composite": ["rsx_mrt_display_*"],
    "d3d12-frame-count-deferred-release": ["tests/test_d3d12_present_gate.py"],
    "cellpad-script-buffer-expansion": ["tests/test_skip_and_auto_transition.py"],
    "d3d12-completed-flip-present": ["tests/test_d3d12_present_gate.py"],
    "rsx-capture-after-file": ["tests/test_rsx_capture_trigger.py"],
    "d3d12-offscreen-capacity": ["tests/test_d3d12_offscreen_capacity.py"],
    "d3d12-small-render-targets": ["tests/test_d3d12_small_targets.py"],
    "d3d12-stable-vertex-cache": ["tests/test_d3d12_vertex_cache.py"],
    "d3d12-surface-metadata": ["tests/test_d3d12_surface_metadata.py"],
    "d3d12-color-clear-flags": ["tests/test_d3d12_clear_color_flags.py"],
    "d3d12-temporal-replay": ["tests/test_d3d12_temporal_replay.py"],
    "d3d12-guest-target-seed": ["tests/test_d3d12_display_clear.py"],
    "d3d12-exact-view-feedback": ["tests/test_d3d12_feedback.py"],
    "d3d12-display-surface-coherence": ["tests/test_d3d12_display_coherence.py"],
    "d3d12-no-color-targets": ["tests/test_d3d12_no_color_target.py", "rsx_d3d12_no_color_*"],
    "d3d12-fragment-constant-snapshots": ["tests/test_d3d12_fragment_constants.py"],
    "d3d12-fragment-capture-state": ["tests/test_d3d12_fp_capture.py", "tests/test_d3d12_fallback_state.py"],
    "d3d12-fragment-program-snapshots": ["tests/test_d3d12_fragment_constants.py", "tests/test_d3d12_fp_capture.py", "tests/test_d3d12_fallback_state.py"],
    "d3d12-present-selection-capture": ["tests/test_rsx_capture_trigger.py", "tests/test_d3d12_display_bridge.py", "tests/test_d3d12_temporal_replay.py"],
    "gcm-fifo-profile": ["tests/test_gcm_fifo_profile.py", "tests/test_native_sprint_safety.py", "tests/test_ftrace_contract.py"],
    "spu-dispatch-owner-contract": ["tests/test_spu_dispatch_ownership.py", "spu_overlay_lookup_*"],
    "d3d12-depth-clear-handling": ["tests/test_d3d12_depth_clear.py"],
    "rsx-fp-color-input-clamp": ["tests/test_rsx_fp_pack_unpack.py"],
    "rsx-fp-pack-unpack": ["tests/test_rsx_fp_pack_unpack.py", "fp_pack_opcodes"],
    "rsx-fp-predicated-write": ["tests/test_rsx_fp_predicated_write.py"],
    "rsx-fp-bx2-texture": ["tests/test_rsx_fp_bx2_texture.py"],
    "rsx-vertex-cmp-fetch": ["rsx_vertex_cmp_fetch"],
    "rsx-nv3089-transfer-probe": ["nv3089_probe_*"],
    "rsx-nv3089-source-origin-12-4": ["nv3089_probe_origin"],
    "rsx-nv3089-first-row-target": ["nv3089_probe_target", "nv3089_probe_*"],
    "spu-context-owned-mfc": ["spu_mfc_isolation_*"],
    "d3d12-nv3089-guest-writeback": ["nv3089_probe_writeback", "nv3089_probe_*"],
}


def subsystem(name: str) -> str:
    for prefix, label in SUBSYSTEMS:
        if name.startswith(prefix):
            return label
    return "unclassified"


def patch_files(text: str) -> list[str]:
    return sorted({
        match.group(1)
        for match in re.finditer(r"^\+\+\+ b/(.+)$", text, re.MULTILINE)
        if match.group(1) != "/dev/null"
    })


def line_counts(text: str) -> tuple[int, int]:
    added = removed = 0
    for line in text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def build_manifest(patches_dir: Path) -> dict:
    entries = []
    missing = []
    for order, name in enumerate(rebuild_ppu.PATCH_STACK, 1):
        path = patches_dir / name
        if not path.is_file():
            missing.append(name)
            continue
        data = path.read_bytes()
        text = data.decode("utf-8", errors="replace")
        added, removed = line_counts(text)
        entries.append({
            "order": order,
            "name": name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "series_file": f"scripts/patches/{name}",
            "subsystem": subsystem(name),
            "files": patch_files(text),
            "lines_added": added,
            "lines_removed": removed,
            "objective": "not documented in the repository; inferred scope only",
            "original_problem": "not documented in the repository",
            "dependencies": "apply in the order listed in scripts/rebuild_ppu.py",
            "associated_tests": TESTS.get(name.removesuffix(".patch"), []),
            "upstream_candidate": "unknown",
            "verification": "sha256 and order only; runtime effect is covered only where a test is listed",
        })
    if missing:
        raise SystemExit("Missing patch files: " + ", ".join(missing))
    return {
        "schema": 1,
        "sdk_revision": rebuild_ppu.REVISION,
        "order_source": "scripts/rebuild_ppu.py:PATCH_STACK",
        "generated_by": "scripts/generate_patch_manifest.py",
        "generated_at": "2026-10-20",
        "patch_count": len(entries),
        "disclaimer": (
            "The repository does not record a historical objective for every patch. "
            "Fields marked 'not documented' are unknown, not guessed. Test associations "
            "are derived from patch names and fixture groups."
        ),
        "patches": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patches", type=Path, default=ROOT / "scripts" / "patches")
    parser.add_argument("--out", type=Path, default=ROOT / "scripts" / "patches" / "manifest.json")
    parser.add_argument("--check", action="store_true",
                        help="fail if the existing manifest differs from generated output")
    args = parser.parse_args()
    manifest = build_manifest(args.patches)
    rendered = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not args.out.is_file() or args.out.read_text(encoding="utf-8") != rendered:
            print("Patch manifest is stale; run python scripts/generate_patch_manifest.py")
            return 1
        print(f"Patch manifest is current: {manifest['patch_count']} patches")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered, encoding="utf-8")
    print(f"Wrote {args.out} ({manifest['patch_count']} patches)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
