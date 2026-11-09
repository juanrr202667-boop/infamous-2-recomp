"""Bounded native menu witness with live status, local PCM and event capture.

Every run has a new directory. A reported state-2 store, intro EOF or optional
diagnostic timer arms a capture file consumed by RSX_CAP_AFTER_FILE. No trigger
declares gameplay valid.
The launcher never changes the Windows mixer volume or attaches to other PIDs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import errno
import hashlib
import json
import math
import mmap
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.win_desktop_shim import apply_desktop_shim
apply_desktop_shim()
from scripts.menu_witness import MenuWitness
from scripts.probe_boot import configure_spu_environment, summarize_log
from scripts.capture_phases import CapturePhases


def report(message):
    """Keep a disconnected console pipe from ending the owned child."""
    try:
        print(message, flush=True)
    except OSError as error:
        # Windows CRT reports a disconnected pipe as EINVAL rather than EPIPE.
        # Check the actual destination so disk and unrelated output errors remain
        # fatal. Redirect its descriptor to prevent another failure at shutdown.
        disconnected = error.errno == errno.EPIPE or (
            sys.platform == "win32" and error.errno == errno.EINVAL)
        try:
            descriptor = sys.stdout.fileno()
            pipe = stat.S_ISFIFO(os.fstat(descriptor).st_mode)
        except (AttributeError, OSError, ValueError):
            raise error
        if not disconnected or not pipe:
            raise
        with open(os.devnull, "w", encoding="utf-8") as sink:
            os.dup2(sink.fileno(), descriptor)
        sys.stdout.flush()


def build_environment(root: Path, capture: Path, *, inherited=None, window=False,
                      captures=4, stride=30, seconds=600, watch=None, watch_len=None,
                      watch_max=None, fresh_spu=False, spin_dump=None, controlled=False):
    env = configure_spu_environment(dict(os.environ if inherited is None else inherited),
                                    fresh_spu=fresh_spu)
    for key in tuple(env):
        if key.startswith(("FP_", "VP_", "RSX_", "TEX_", "DRAW_", "CELLMARK_",
                           "PPU_POKE_", "PPU_FORCE_READ_", "LBP_", "SPU_ATOM_", "PAD_")):
            env.pop(key)
    for key in ("GCM_FLIPCB_ONTICK", "GCM_FLIP_NEEDS_FIFO", "GCM_GET_EQ_PUT",
                "GCM_DEFAULT_CTX_REPOINT", "GCM_GUEST_FB", "GCM_DRAINDBG", "PPU_WW_GUARD",
                "PPU_POKE", "PPU_POKE8", "PERF",
                "PS3_SAMPLE", "SPU_CTX_LOG", "SPU_CALLTRACE", "SPU_DMA_LIST_TRACE",
                "SPURS_PM_TRACE", "SPU_TASK_WAKE_TRACE",
                "I2_SKIP_INTRO", "I2_AUTO_START", "I2_AUTO_NEWGAME",
                "I2_AUTO_START_BTN", "I2_AUTO_START_DELAY_MS", "I2_FAST_CATALOG",
                "I2_UNLOCK_FPS", "I2_DISABLE_MESH_TRIMMING",
                "I2_SPIN_DUMP", "I2_SPIN_DUMP_AFTER"):
        env.pop(key, None)
    # Alternate pure START (0x0008) and CROSS (0x4000) during intro / title screen
    events = [f"{t}:{'0x0008' if t % 10 == 0 else '0x4000'}" for t in range(20, min(240, int(seconds)), 5)]
    if seconds > 240:
        # After title screen, only CROSS (0x4000) to select New Game and confirm difficulty
        events.extend([f"{t}:0x4000" for t in range(240, int(seconds), 4)])
    env.update({
        "PS3_VFS_ROOT": str(root / "build/game/vfs"),
        "PS3_HDD0_ROOT": str(root / "build/game/vfs/hdd0"),
        "PS3_SAVEDATA_ROOT": str(root / "build/game/vfs/hdd0/home/00000001/savedata"),
        "I2_HEADLESS": "0", "D3D12_HIDDEN": "1", "PS3_VERBOSE": "0",
        "I2_TELEMETRY_PATH": str(capture / "telemetry.json"),
        "PAD_ALWAYS_CHANGE": "1",
        "PAD_SCRIPT": ",".join(events), "PAD_FILE": str(capture / "pad.txt"),
        "PPU_WWATCH": watch or "00ABFE50", "PPU_WWATCH_LEN": watch_len or "0x20",
        "PPU_WWATCH_MAX": "0" if watch_max is None else str(watch_max),
        "I2_LIGHT_KICKER_STATS": "1", "I2_WWS_STATS": "1",
        "I2_BINK_SEM_STATS": "1", "I2_BINK_BUFFER_STATS": "1",
        "I2_GCM_CONTEXT_STATS": "1", "I2_GCM_FLIP_STATS": "1",
        "D3D12_DBG": "1", "D3D12_IQ": "1",
        "RSX_CAP": f"1:{captures}:{stride}", "RSX_CAP_DIR": str(capture / "rsx"),
        "RSX_CAP_AFTER_FILE": str(capture / "capture.ready"),
        "AUDIO_AMPLITUDE_STATS": "1", "AUDIO_PORT_STATS": "1", "AUDIO_DEVICE_STATS": "1",
        "AUDIO_WAV": str(capture / "mix.f32"),
        "SPU_DUMP_MISS": str(capture / "spu_misses"),
    })
    if spin_dump is not None:
        env["I2_SPIN_DUMP"] = str(spin_dump)
    if controlled:
        env.pop("PAD_SCRIPT", None)
        env.pop("AUDIO_WAV", None)
        env["RSX_CAP_REARM"] = "1"
    if window:
        env.pop("D3D12_HIDDEN", None)  # The backend tests presence, including "0".
    return env


def write_status(path: Path, status: dict):
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(status, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # Windows readers may hold the destination without FILE_SHARE_DELETE.
    # Observation must not terminate the child. Keep a complete pending file
    # if the bounded retries expire; the next update attempts publication again.
    for attempt in range(10):
        try:
            pending.replace(path)
            return path
        except PermissionError as error:
            if getattr(error, "winerror", None) not in (5, 32, 33):
                raise
            if attempt < 9:
                time.sleep(0.01)
    return pending


def run_witness(command, capture: Path, env, *, seconds, progress_interval=15,
                capture_after_seconds=None):
    if seconds <= 0:
        raise ValueError("seconds must be positive")
    if capture_after_seconds is not None and (
            not math.isfinite(capture_after_seconds) or not 0 <= capture_after_seconds < seconds):
        raise ValueError("capture_after_seconds must be finite and 0 <= value < seconds")
    capture = Path(capture)
    capture.mkdir(parents=True, exist_ok=False)
    (capture / "rsx").mkdir()
    (capture / "pad.txt").write_text("", encoding="ascii")
    witness = MenuWitness()
    phases = None
    if env.get("RSX_CAP_REARM") == "1":
        cap_parts = env.get("RSX_CAP", "1:1:1").split(":")
        phases = CapturePhases(capture / "capture.ready", int(cap_parts[1]) if len(cap_parts) > 1 else 1)
    started = time.monotonic()
    status = {"running": True, "duration_seconds": 0, "time_limit_seconds": seconds,
              "timed_out": False, "exit_code": None, "capture": str(capture),
              "capture_requested_reason": None, "menu_witness": witness.as_dict(),
              "diagnostic_limits": ["write watch reports before the store; not a UI or gameplay proof",
                                    "existing WWS observer is capped at 120 samples",
                                    "PCM captures software mix before WASAPI, not device playback"]}
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    pending_line = ""

    def consume(stream, final=False):
        nonlocal pending_line
        pending_line += stream.read()
        lines = pending_line.split("\n")
        pending_line = lines.pop()
        if final and pending_line:
            lines.append(pending_line)
            pending_line = ""
        elapsed = time.monotonic() - started
        for line in lines:
            witness.feed(line, elapsed)
            if phases:
                phases.observe(line)
        observation = witness.as_dict()
        reason = "state2_write_report" if observation["state2_write_observed"] else (
            "intro_eof_report" if observation["intro_eof_observed"] else (
                "intro_teardown_report" if observation.get("intro_teardown_observed") else None))
        if reason is None and not final and capture_after_seconds is not None and elapsed >= capture_after_seconds:
            reason = "diagnostic_timer"
        if reason and status["capture_requested_reason"] is None:
            if phases:
                phases.arm(reason)
            else:
                (capture / "capture.ready").write_text(reason + "\n", encoding="ascii")
            status["capture_requested_reason"] = reason
        request = capture / "capture.request"
        if phases and status["capture_requested_reason"] is not None and phases.state == "ready":
            if request.is_file():
                try:
                    phase = request.read_text(encoding="utf-8").strip()
                    if phase and "\n" not in phase and "\r" not in phase:
                        request.unlink()
                        phases.arm(phase)
                except (PermissionError, FileNotFoundError):
                    # A producer can still hold or replace the request on Windows.
                    # Leave it for the next polling pass; never kill the game here.
                    pass
            elif not final and capture_after_seconds is not None and elapsed >= capture_after_seconds and not status.get("diagnostic_timer_rearmed"):
                phases.arm("diagnostic_timer")
                status["diagnostic_timer_rearmed"] = True
        status["duration_seconds"] = round(elapsed, 3)
        status["menu_witness"] = observation

    with (capture / "game.log").open("wb") as output:
        process = subprocess.Popen(command, cwd=capture, env=env, stdout=output,
                                   stderr=subprocess.STDOUT, creationflags=flags)
        stream = None
        try:
            status["pid"] = process.pid
            stream = (capture / "game.log").open("r", encoding="utf-8", errors="replace")
            last_report = started
            while process.poll() is None:
                consume(stream)
                write_status(capture / "status.json", status)
                now = time.monotonic()
                if now - started >= seconds:
                    status["timed_out"] = True
                    break
                if now - last_report >= progress_interval:
                    last_report = now
                    current = status["menu_witness"]
                    report(json.dumps({"elapsed": status["duration_seconds"],
                                      "intro_frame": current["last_intro_frame"],
                                      "state2_write": current["state2_write_observed"],
                                      "captures_logged": current["rsx_capture_count"]}))
                time.sleep(min(0.1, max(0.001, seconds - (now - started))))
        except KeyboardInterrupt:
            status["interrupted"] = True
        finally:
            if process.poll() is None:
                process.kill()
            status["exit_code"] = process.wait()
            status["running"] = False
            try:
                if stream is not None:
                    consume(stream, final=True)
            finally:
                if stream is not None:
                    stream.close()
                write_status(capture / "status.json", status)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--capture-after-seconds", type=float,
                        help="arm diagnostic capture after this delay, even without a guest event")
    parser.add_argument("--window", action="store_true")
    parser.add_argument("--controlled", action="store_true",
                        help="disable scripted input and raw audio; enable capture rearming")
    parser.add_argument("--captures", type=int, default=4)
    parser.add_argument("--stride", type=int, default=30)
    parser.add_argument("--skip-intro", action="store_true")
    parser.add_argument("--fresh-spu", action="store_true",
                        help="reset policy-module local store on every dispatch")
    parser.add_argument("--spin-dump", nargs="?", const="1", default=None,
                        help="read the job-barrier object the title spins on "
                             "(pass a slot address, or omit for 0x0FEFFAF0)")
    parser.add_argument("--disable-mesh-trimming", action="store_true")
    parser.add_argument("--unlock-fps", action="store_true")
    parser.add_argument("--auto-start", action="store_true")
    parser.add_argument("--fast-catalog", action="store_true")
    parser.add_argument("--watch",
                        help="guest EA to watch for PPU stores (default 00ABFE50, the state marker)")
    parser.add_argument("--watch-len",
                        help="watched span in bytes around --watch (default 0x20)")
    parser.add_argument("--watch-max", type=int, default=0,
                        help="stop printing watch hits after N stores (0 = unlimited)")
    args = parser.parse_args()
    if not 1 <= args.seconds <= 1800 or not 1 <= args.captures <= 16 or args.stride < 1:
        parser.error("seconds 1..1800, captures 1..16 and stride >=1 required")
    if args.watch is not None:
        try:
            watch = int(args.watch, 16)
        except ValueError:
            watch = -1
        if not 0 <= watch <= 0xFFFFFFFF:
            parser.error("watch must be a 32-bit hex guest address")
        args.watch = f"{watch:08X}"
    if args.watch_len is not None:
        try:
            watch_len = int(args.watch_len, 0)
        except ValueError:
            watch_len = 0
        if not 0 < watch_len <= 0x10000:
            parser.error("watch-len must be 1..0x10000 bytes")
        args.watch_len = f"0x{watch_len:X}"
    if not 0 <= args.watch_max <= 1000000:
        parser.error("watch-max must be 0..1000000")
    if args.capture_after_seconds is not None and (
            not math.isfinite(args.capture_after_seconds) or not 0 <= args.capture_after_seconds < args.seconds):
        parser.error("capture-after-seconds must be finite and 0 <= value < seconds")
    root = args.root.resolve()
    executable = root / "build/infamous2-clang2/Infamous2Native.exe"
    elf = root / "build/game/EBOOT.ELF"
    if not executable.is_file() or not elf.is_file():
        parser.error("native executable or local ELF is missing")
    with executable.open("rb") as file, mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as binary:
        if binary.find(b"RSX_CAP_AFTER_FILE") < 0:
            parser.error("executable lacks RSX_CAP_AFTER_FILE; integrate and build candidate37 first")
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    capture = root / "build/game/menu-witness" / name
    env = build_environment(root, capture, window=args.window, captures=args.captures, stride=args.stride, seconds=args.seconds,
                            watch=args.watch, watch_len=args.watch_len,
                            watch_max=args.watch_max, fresh_spu=args.fresh_spu,
                            spin_dump=args.spin_dump, controlled=args.controlled)
    extra_cmd = []
    if args.skip_intro:
        env["I2_SKIP_INTRO"] = "1"
        extra_cmd.append("--skip-intro")
    if args.disable_mesh_trimming:
        env["I2_DISABLE_MESH_TRIMMING"] = "1"
        extra_cmd.append("--disable-mesh-trimming")
    if args.unlock_fps:
        env["I2_UNLOCK_FPS"] = "1"
        extra_cmd.append("--unlock-fps")
    if args.auto_start:
        env["I2_AUTO_START"] = "1"
        extra_cmd.append("--auto-start")
    if args.fast_catalog:
        env["I2_FAST_CATALOG"] = "1"
    report(f"Witness: {capture}")
    status = run_witness([str(executable), str(elf)] + extra_cmd, capture, env, seconds=args.seconds,
                         capture_after_seconds=args.capture_after_seconds)
    status["runtime_summary"] = summarize_log(capture / "game.log")
    status["executable"] = {"path": str(executable), "mtime_ns": executable.stat().st_mtime_ns}
    with executable.open("rb") as file:
        status["executable"]["sha256"] = hashlib.file_digest(file, "sha256").hexdigest()
    status["captures"] = [str(path.relative_to(capture)) for path in sorted((capture / "rsx").glob("frame_*/backbuffer.bmp"))]
    pcm = capture / "mix.f32"
    if pcm.is_file():
        from scripts.analyze_audio_full_scale import analyze_stream
        with pcm.open("rb") as stream:
            metrics = analyze_stream(stream)
        metrics["path"] = str(pcm)
        metrics_path = capture / "mix.full-scale.metrics.json"
        metrics_path = write_status(metrics_path, metrics)
        status["audio_metrics"] = str(metrics_path)
    status_path = write_status(capture / "status.json", status)
    report(f"Status: {status_path}")
    return 130 if status.get("interrupted") else (0 if status["timed_out"] or status["exit_code"] == 0 else 1)


if __name__ == "__main__":
    raise SystemExit(main())
