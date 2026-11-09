"""Apply the pinned lifter correction, regenerate PPU code, and build locally."""
from pathlib import Path
import argparse
import filecmp
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"
REVISION = "5244db92669b54831c6d3ebb7a96d58c4dbfa6df"
ELF_HASH = "bd4569d249e901449f2e0f6e3a289387af48cb02f637ba8ef950c13c5eb8c2a0"
SWITCH_CASES = (0xB4224, 0xB422C, 0xB4254, 0xB42C8, 0xB42D4, 0xB42E4)


def apply_patch_stack(repo, patches, apply=True):
    """Recognize a verified stack prefix before modifying the toolkit.

    Later patches may change the context of earlier patches. Replay them in
    isolation rather than testing each reverse patch against the final tree.
    Changes outside the stack are preserved; divergent affected files abort.

    With ``apply=False`` the function only verifies that the current tree is a
    prefix of the stack and returns that prefix length without writing.
    """
    repo = Path(repo)
    patches = [Path(p).resolve() for p in patches]
    files = sorted({relative for patch in patches for relative in re.findall(
        r"^(?:--- a/|\+\+\+ b/)(.+)$", patch.read_text(encoding="utf-8"), re.M)})

    def contents(directory):
        return {relative: (directory / relative).read_bytes().replace(b"\r\n", b"\n")
                if (directory / relative).is_file() else None for relative in files}

    active = contents(repo)
    prefix = None
    with tempfile.TemporaryDirectory(prefix="i2-patch-replay-") as temp:
        stage = Path(temp)
        subprocess.run(["git", "-C", str(stage), "init"], check=True, capture_output=True)
        for relative in files:
            original = subprocess.run(["git", "-C", str(repo), "show", "HEAD:" + relative],
                                      capture_output=True)
            if original.returncode == 0:
                destination = stage / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(original.stdout)
        if contents(stage) == active:
            prefix = 0
        for count, patch in enumerate(patches, 1):
            subprocess.run(["git", "-C", str(stage), "apply", "--check", str(patch)],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", str(stage), "apply", str(patch)],
                           check=True, capture_output=True)
            if contents(stage) == active:
                prefix = count
    if prefix is None:
        raise ValueError("Toolkit files diverge from every verified patch prefix; preserve and review local changes")
    if apply:
        apply_command = ["git", "-C", str(repo), "apply"]
        for patch in patches[prefix:]:
            subprocess.run(apply_command + ["--check", str(patch)], check=True)
            subprocess.run(apply_command + [str(patch)], check=True)
        print(f"Patch stack verified: {len(patches)} patches ({prefix} already applied)")
    return prefix


def validate_lift(directory):
    """Check the diagnosed dispatcher before replacing the active lift."""
    units = sorted(directory.glob("ppu_recomp_*.cpp"))
    if not (directory / "ppu_recomp.h").is_file() or not units:
        raise ValueError("Incomplete PPU lift")
    for unit in units:
        # The upstream emitter uses the Windows locale for comments. The
        # validation tokens are ASCII; replacing non-ASCII comment bytes does
        # not rewrite or change the source that the compiler receives.
        content = unit.read_text(encoding="ascii", errors="replace")
        match = re.search(r"^void func_000B4068\(ppu_context\* ctx\) \{", content, re.M)
        if not match:
            continue
        next_function = re.search(r"^void func_", content[match.end():], re.M)
        end = match.end() + next_function.start() if next_function else len(content)
        body = content[match.end():end]
        if all(f"case 0x{address:08X}u:" in body for address in SWITCH_CASES):
            return units
        break
    raise ValueError("The B41D4 switch is still missing one or more case targets")


PATCH_STACK = (
    'ppu-tail-return.patch',
    'fs-directory-entries.patch',
    'spurs-event-routing.patch',
    'spu-interpreter-handoff.patch',
    'spu-microstep-absolute.patch',
    'd3d12-hidden-capture.patch',
    'd3d12-fp-export.patch',
    'spu-receive-event.patch',
    'spu-thread-event-routing.patch',
    'spu-interpreter-mpyui.patch',
    'spu-zero-dma.patch',
    'spu-interpreter-rothmi.patch',
    'spu-orbi-decode.patch',
    'spu-policy-data-abi.patch',
    'spu-interpreter-rotmah.patch',
    'spu-xorbi-decode.patch',
    'audio-device-stats.patch',
    'audio-port-stats.patch',
    'rsx-shader-bind-stats.patch',
    'rsx-immediate-vertices.patch',
    'texture-plane-capture.patch',
    'd3d12-guest-screen-texture.patch',
    'spu-hot-trace-gates.patch',
    'd3d12-sixteen-texture-units.patch',
    'spu-task-signal-wake.patch',
    'ppu-vmx-exp-log-estimates.patch',
    'gcm-flip-context-ownership.patch',
    'gcm-small-context-flip.patch',
    'gcm-self-jump-park.patch',
    'd3d12-mrt-display-composite.patch',
    'd3d12-frame-count-deferred-release.patch',
    'audio-port-level-downmix-headroom.patch',
    'cellpad-script-buffer-expansion.patch',
    'audio-amplitude-stats.patch',
    'audio-initial-level-contract.patch',
    'd3d12-completed-flip-present.patch',
    'rsx-capture-after-file.patch',
    'd3d12-offscreen-capacity.patch',
    'd3d12-small-render-targets.patch',
    'd3d12-stable-vertex-cache.patch',
    'd3d12-surface-metadata.patch',
    'd3d12-color-clear-flags.patch',
    'native-sdk-local-baseline.patch',
    'd3d12-temporal-replay.patch',
    'd3d12-guest-target-seed.patch',
    'd3d12-exact-view-feedback.patch',
    'd3d12-display-surface-coherence.patch',
    'd3d12-no-color-targets.patch',
    'd3d12-fragment-constant-snapshots.patch',
    'd3d12-depth-clear-handling.patch',
    'rsx-fp-color-input-clamp.patch',
    'rsx-fp-pack-unpack.patch',
    'rsx-fp-predicated-write.patch',
    'rsx-fp-bx2-texture.patch',
    'spu-overlay-lookup.patch',
    'rsx-vertex-cmp-fetch.patch',
    'rsx-nv3089-transfer-probe.patch',
    'spu-owned-span-no-fallback.patch',
    'rsx-nv3089-source-origin-12-4.patch',
    'rsx-nv3089-first-row-target.patch',
    'spu-context-owned-mfc.patch',
    'd3d12-nv3089-guest-writeback.patch',
    'rsx-capture-rearm.patch',
    'ppu-syscall-snapshot.patch',
    'ppu-hle-snapshot.patch',
    'ppu-sampler-direct-only.patch',
    'ppu-wws-wait-observer.patch',
    'ppu-sampler-rip-buckets.patch',
    'ppu-light-callsite-observer.patch',
    'ppu-light-loop-observer.patch',
    'ppu-light-wait-observer.patch',
    'spurs-pm-quantum-observer.patch',
    'spu-interp-ied-trace.patch',
    'spu-smc-frequency.patch',
    'antigravity-keyboard.patch',
    'antigravity-pso-cache.patch',
    'rsx-capture-presented-only.patch',
    'rsx-vs-compile-trace.patch',
    'rsx-vs-working-set.patch',
    'ppu-sampler-clear-windows.patch',
    'frame-timeline.patch',
    'ftrace-alternating-sleeps.patch',
    'spurs-attribute-abi.patch',
    'd3d12-fragment-capture-state.patch',
    'd3d12-fragment-program-snapshots.patch',
    'd3d12-present-selection-capture.patch',
    'gcm-fifo-profile.patch',
    'spu-dispatch-owner-contract.patch',
    'spurs-workload-stack-argument.patch',
    'spurs-multilane-sequential-dispatch.patch',
    'cellpad-stick-injection.patch',
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reuse-generated", action="store_true",
        help="validate and adopt the local staged lift after a failed build")
    args = parser.parse_args()
    revision = subprocess.check_output(["git", "-C", str(SDK), "rev-parse", "HEAD"], text=True).strip()
    if revision != REVISION:
        raise SystemExit("Review the toolkit revision before applying this patch.")
    names = PATCH_STACK
    apply_patch_stack(SDK, [ROOT / "scripts/patches" / name for name in names])
    elf = ROOT / "build/game/EBOOT.ELF"
    if hashlib.sha256(elf.read_bytes()).hexdigest() != ELF_HASH:
        raise SystemExit("ELF differs from the locally validated BCUS98125 image.")
    generated = ROOT / "build/game/recomp-next"
    generated.mkdir(parents=True, exist_ok=True)
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    if not args.reuse_generated:
        # Clear only the previous staged generated units. The active lift is
        # untouched until generation and validation both succeed. Without this,
        # an older higher-numbered chunk could survive a smaller fresh lift.
        for path in generated.iterdir():
            if path.name == "ppu_recomp.h" or re.fullmatch(r"ppu_recomp_\d+\.cpp", path.name):
                if path.is_file():
                    path.unlink()
        with (logs / "ppu-lift.log").open("w", encoding="utf-8") as log:
            subprocess.run([sys.executable, "-u", str(SDK / "tools/ppu_lifter.py"), str(elf),
                "--output", str(generated), "--code-end", "0x6EB250", "--jobs", "4",
                "--functions", str(ROOT / "build/game/loader/EBOOT.functions.json"),
                "--hle-stubs", str(ROOT / "build/game/loader/EBOOT.imports.json")],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    current = ROOT / "build/game/recomp"
    units = validate_lift(generated)
    emitted = {path.name for path in units}
    stale = {path.name for path in current.glob("ppu_recomp_*.cpp")} - emitted
    if stale:
        raise ValueError(f"Review stale generated units before adoption: {sorted(stale)}")
    # Only adopt files emitted by the lifter. Preserve separately generated HLE
    # tables and do not rewrite identical units, which avoids needless builds.
    for path in [generated / "ppu_recomp.h", *units]:
        destination = current / path.name
        if not destination.exists() or not filecmp.cmp(path, destination, shallow=False):
            shutil.copy2(path, destination)
    with (logs / "ppu-build.log").open("w", encoding="utf-8") as log:
        subprocess.run(["cmake", "--build", str(ROOT / "build/infamous2-clang2"),
            "--target", "Infamous2Native", "-j4"], cwd=ROOT,
            stdout=log, stderr=subprocess.STDOUT, check=True)
    print("PPU regenerated and linked. Native boot verification is still required.")


if __name__ == "__main__":
    main()
