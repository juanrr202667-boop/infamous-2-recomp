"""Run an unattended native boot probe and write machine-readable status."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "boot-probe.log"
STATUS = ROOT / "logs" / "boot-status.json"
TELEMETRY = ROOT / "logs" / "boot-telemetry.json"


def summarize_log(path: Path) -> dict:
    stages = {
        "elf_loaded": False,
        "guest_entry_reached": False,
        "crt_initialized": False,
        "spu_initialized": False,
        "gcm_initialized": False,
        "headless_backend_ready": False,
        "d3d12_backend_ready": False,
    }
    signals = {
        "elf_loaded": "PT_LOAD segments, entry OPD",
        "guest_entry_reached": "[ppu] run: code ",
        "crt_initialized": "[crt] sys_initialize_tls:",
        "spu_initialized": "[SPU] initialize(",
        "gcm_initialized": "[cellGcmSys] Init(",
        "headless_backend_ready": "headless diagnostic backend init OK",
        "d3d12_backend_ready": "[D3D12] Initialization complete",
    }
    issues = Counter()
    activity = Counter()
    d3d_draws = Counter()
    unresolved_call_targets = []
    busy_guest_reads, first_null_calls = [], []
    failed_opens, missing_workloads, unresolved_nids, missing_jobs = [], [], [], []
    first_malformed_dma = None
    first_incomplete_spu_run = None
    first_graphics_failures: list[str] = []
    draws = presents = guest_frames = lines = 0
    title_id = None
    events: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            lines += 1
            first_unresolved_import = False
            first_unresolved_call = False
            first_hot_read = False
            first_policy_return = False
            graphics_failure = False
            if re.search(r"\[(?:FP|VP)\].*\bPSO FAIL\b", line):
                issues["graphics_pipeline_failure"] += 1
                graphics_failure = True
            # D3D12 severities: 0 corruption, 1 error, 2 warning.
            # Count emitted messages only; runtime diagnostics may be capped.
            if re.search(r"\[D3D12-IQ\]\[sev=[01]\s+id=\d+\]", line):
                issues["graphics_validation_error"] += 1
                graphics_failure = True
            first_graphics_failure = False
            if graphics_failure:
                context = line.strip()
                if len(first_graphics_failures) < 10 and context not in first_graphics_failures:
                    first_graphics_failures.append(context)
                    first_graphics_failure = True
            if "[pm-end]" in line:
                issues["spu_policy_unexpected_return"] += 1
                if first_incomplete_spu_run is None:
                    first_incomplete_spu_run = dict(re.findall(
                        r"\b(halted|status|pc|lr)=(0x[0-9A-Fa-f]+|\d+)", line))
                    lane = re.search(r"\[pm-end\] lane (\d+)", line)
                    if lane:
                        first_incomplete_spu_run["lane"] = lane.group(1)
                    first_policy_return = True
            match = re.search(r"\[HOTREAD\] spinning on (0x[0-9A-Fa-f]+) \(=(0x[0-9A-Fa-f]+)\).*lr=(0x[0-9A-Fa-f]+)", line)
            if match:
                issues["guest_hot_read"] += 1
                context = dict(zip(("address", "value", "lr"), match.groups()))
                if len(busy_guest_reads) < 10 and context not in busy_guest_reads:
                    busy_guest_reads.append(context)
                    first_hot_read = True
            if "[null-call]" in line:
                issues["guest_null_call"] += 1
                if len(first_null_calls) < 10:
                    first_null_calls.append(line[line.find("[null-call]"):].strip())
            match = re.search(r"unresolved indirect call -> (0x[0-9A-Fa-f]{8})", line)
            if match:
                issues["unresolved_indirect_call"] += 1
                target = "0x" + match.group(1)[2:].upper()
                if target not in unresolved_call_targets:
                    unresolved_call_targets.append(target)
                    first_unresolved_call = True
            if "FATAL:" in line:
                issues["fatal_runtime"] += 1
            for key, marker in (
                ("lfqueue_pushes_logged", "[i2-lfqueue] push "),
                ("spurs_event_wakes_logged", "EventFlagWait WAKE"),
                ("spu_task_signals_logged", "[spu_workload] signal task "),
            ):
                if marker in line:
                    activity[key] += 1
            match = re.search(r"\[hle\] unresolved NID (0x[0-9A-Fa-f]{8})", line)
            if match:
                issues["unresolved_import"] += 1
                nid = "0x" + match.group(1)[2:].upper()
                if nid not in unresolved_nids:
                    unresolved_nids.append(nid)
                    first_unresolved_import = True
            for stage, signal in signals.items():
                if signal in line:
                    stages[stage] = True
            # Native guest/SPU threads can interleave bytes in one log line.
            # Flip mode is configured only after GCM init returns successfully.
            if "[cellGcmSys] SetFlipMode(" in line or "[cellGcmSys] SetDisplayBuffer(" in line:
                stages["gcm_initialized"] = True
            match = re.search(r"\[headless\] draw #(\d+)", line)
            if match:
                draws = max(draws, int(match.group(1)))
            # D3D12 logs only its first draws and uses zero-based indices.
            # This proves a lower bound; the runner's draw telemetry counts
            # only headless callbacks and cannot supply a D3D12 total.
            match = re.search(r"\[D3D12\] draw_arrays #(\d+)", line)
            if match:
                d3d_draws["arrays"] = max(d3d_draws["arrays"], int(match.group(1)) + 1)
            if "[D3D12] draw_indexed(" in line:
                d3d_draws["indexed"] += 1
            draws = max(draws, sum(d3d_draws.values()))
            match = re.search(r"\[headless\] present #(\d+)", line)
            if match:
                presents = max(presents, int(match.group(1)))
            match = re.search(r"\[headless\] guest frame #(\d+)", line)
            if match:
                guest_frames = max(guest_frames, int(match.group(1)))
            match = re.search(r"BootCheck: type=\d+, titleId='([^']+)'", line)
            if match:
                title_id = match.group(1)
            match = re.search(r"title id from PARAM\.SFO .*: '([^']+)'", line)
            if match:
                title_id = match.group(1)
            if "PM NOT LIFTED" in line:
                issues["spu_workload_not_lifted"] += 1
                match = re.search(r"fp=0x[0-9A-Fa-f]+ size=\d+", line)
                if match and match.group(0) not in missing_workloads:
                    missing_workloads.append(match.group(0))
            if "async dispatch MISS" in line:
                issues["spu_dispatch_miss"] += 1
            if "[spurs-job] dispatch MISS" in line:
                issues["spu_job_dispatch_miss"] += 1
                match = re.search(r"fp=0x[0-9A-Fa-f]+ size=\d+", line)
                if match and match.group(0) not in missing_jobs:
                    missing_jobs.append(match.group(0))
            if "invalid cond" in line:
                issues["invalid_condition"] += 1
            if "256 identical transfers" in line and "halting the SPU" in line:
                issues["spu_repeated_dma_halt"] += 1
            if "REJECTED malformed transfer" in line:
                issues["spu_malformed_dma"] += 1
                if first_malformed_dma is None:
                    fields = dict(re.findall(r"\b(pc|cmd|lsa|ea|size)=(0x[0-9A-Fa-f]+|\d+)", line))
                    first_malformed_dma = fields
            if "EventFlagWait BLOCKED" in line:
                issues["spurs_event_flag_wait"] += 1
            if "sleeping" in line and "WAIT_SIGNAL" in line:
                issues["spu_wait_signal"] += 1
            if "open FAILED" in line:
                issues["file_open_failed"] += 1
                clean_open = line[line.find("[sys_fs]"):].strip() if "[sys_fs]" in line else line.strip()
                if len(failed_opens) < 10 and clean_open not in failed_opens:
                    failed_opens.append(clean_open)
            if "[null-read]" in line or "[null-write]" in line:
                issues["guest_null_access"] += 1
            is_first_spu_issue = (
                (issues["spu_repeated_dma_halt"] == 1 and "256 identical transfers" in line)
                or (issues["spu_malformed_dma"] == 1 and "REJECTED malformed transfer" in line)
                or (issues["spurs_event_flag_wait"] == 1 and "EventFlagWait BLOCKED" in line)
                or (issues["spu_wait_signal"] == 1 and "WAIT_SIGNAL" in line)
            )
            first_queue_progress = (
                (activity["lfqueue_pushes_logged"] == 1 and "[i2-lfqueue] push " in line)
                or (activity["spurs_event_wakes_logged"] == 1 and "EventFlagWait WAKE" in line)
            )
            if len(events) < 120 and (first_graphics_failure or is_first_spu_issue or first_policy_return or first_unresolved_import or first_unresolved_call or first_queue_progress or first_hot_read or any(signal in line for signal in (
                "[boot]", "[ppu] loaded", "[ppu] run:", "BootCheck:",
                "title id from PARAM.SFO",
                "[cellGcmSys] Init(", "[rsx]", "[headless] draw",
                "[headless] guest frame", "PM NOT LIFTED", "dispatch MISS",
                "open FAILED", "ERROR:", "FATAL:",
            ))):
                events.append(line.strip())
    return {
        "stages": stages, "title_id": title_id,
        "draws_observed": draws, "backend_presents_observed": presents,
        "draws_total_known": False,
        "guest_frames_observed": guest_frames,
        "issues": dict(issues), "missing_workloads": missing_workloads,
        "missing_jobs": missing_jobs,
        "unresolved_nids": unresolved_nids,
        "unresolved_call_targets": unresolved_call_targets,
        "activity": dict(activity),
        "busy_guest_reads": busy_guest_reads,
        "first_null_calls": first_null_calls,
        "first_failed_file_opens": failed_opens,
        "first_malformed_dma": first_malformed_dma,
        "first_incomplete_spu_run": first_incomplete_spu_run,
        "first_graphics_failures": first_graphics_failures,
        "events": events,
        "log_lines": lines, "log_bytes": path.stat().st_size,
    }


def classify_outcome(status: dict, code: int | None, timed_out: bool) -> str:
    if status["issues"].get("fatal_runtime", 0):
        return "native_runtime_failed"
    if not timed_out and code != 0:
        return "native_process_failed"
    if (status["issues"].get("graphics_pipeline_failure", 0)
            or status["issues"].get("graphics_validation_error", 0)):
        return "guest_graphics_failed"
    if status["guest_frames_observed"] or status["draws_observed"]:
        return "guest_graphics_observed"
    if status["stages"]["guest_entry_reached"]:
        return "guest_running_no_graphics_observed"
    return "guest_entry_not_reached"


def configure_spu_environment(inherited: dict[str, str], *, fresh_spu: bool = False,
                              spu_ls_watch: str | None = None) -> dict[str, str]:
    env = inherited.copy()
    # The runtime enables fresh mode by presence, even for the value "0".
    # Only the probe's explicit options control these two experiments.
    env.pop("SPURS_PM_FRESH", None)
    env.pop("SPU_LS_WATCH", None)
    if fresh_spu:
        env["SPURS_PM_FRESH"] = "1"
    if spu_ls_watch:
        env["SPU_LS_WATCH"] = spu_ls_watch
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--window", action="store_true", help="show D3D12 window")
    parser.add_argument("--fresh-spu", action="store_true",
                        help="reset policy-module local store on every dispatch")
    parser.add_argument("--spu-ls-watch", metavar="ADDRESS",
                        help="trace SPU local-store reads and writes at an address")
    args = parser.parse_args()
    if not 1 <= args.seconds <= 120:
        parser.error("--seconds must be between 1 and 120")
    exe = ROOT / "build" / "infamous2-clang2" / "Infamous2Native.exe"
    elf = ROOT / "build" / "game" / "EBOOT.ELF"
    if not exe.is_file() or not elf.is_file():
        parser.error("build the native executable and decrypt the ELF first")
    vfs = ROOT / "build" / "game" / "vfs"
    hdd0 = vfs / "hdd0"
    spu_misses = ROOT / "build" / "game" / "spu_misses"
    hdd0.mkdir(parents=True, exist_ok=True)
    spu_misses.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    TELEMETRY.unlink(missing_ok=True)
    env = configure_spu_environment(
        dict(os.environ), fresh_spu=args.fresh_spu, spu_ls_watch=args.spu_ls_watch)
    env.update({
        "PS3_VFS_ROOT": str(vfs), "PS3_HDD0_ROOT": str(hdd0),
        "PS3_TITLE": "inFAMOUS 2 native boot probe",
        "I2_HEADLESS": "0" if args.window else "1",
        "SPU_DUMP_MISS": str(spu_misses),
        "I2_TELEMETRY_PATH": str(TELEMETRY),
    })
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    started = time.monotonic()
    with LOG.open("wb") as output:
        process = subprocess.Popen(
            [str(exe), str(elf)], cwd=ROOT, env=env,
            stdout=output, stderr=subprocess.STDOUT, creationflags=flags,
        )
        try:
            code = process.wait(timeout=args.seconds)
            timed_out = False
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            code, timed_out = None, True
    run_duration = time.monotonic() - started
    status = summarize_log(LOG)
    if TELEMETRY.is_file():
        telemetry = json.loads(TELEMETRY.read_text(encoding="utf-8"))
        status["draws_observed"] = max(status["draws_observed"], telemetry["draws"])
        status["draws_total_known"] = bool(telemetry["headless"])
        status["guest_frames_observed"] = max(
            status["guest_frames_observed"], telemetry["guest_frames"])
        status["backend_presents_observed"] = max(
            status["backend_presents_observed"], telemetry["backend_presents"])
        status["stages"]["headless_backend_ready"] |= (
            telemetry["headless"] and telemetry["backend_ready"])
        status["telemetry"] = telemetry
    status.update({
        "duration_seconds": round(run_duration, 2),
        "time_limit_seconds": args.seconds, "timed_out": timed_out,
        "exit_code": code, "mode": "window" if args.window else "headless",
        "fresh_spu": args.fresh_spu,
        "spu_ls_watch": args.spu_ls_watch,
        "log": str(LOG), "telemetry_path": str(TELEMETRY), "vfs": str(vfs),
    })
    status["outcome"] = classify_outcome(status, code, timed_out)
    STATUS.write_text(json.dumps(status, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (LOG.parent / "boot-events.log").write_text(
        "\n".join(status["events"]) + "\n", encoding="utf-8")
    print(f"Boot: {status['duration_seconds']} s, "
          f"{'time limit reached' if timed_out else f'exit {code}'}; "
          f"headless={status['stages']['headless_backend_ready']}; "
          f"entry={status['stages']['guest_entry_reached']}; "
          f"GCM={status['stages']['gcm_initialized']}; "
          f"draws{'=' if status['draws_total_known'] else '>='}{status['draws_observed']}; "
          f"guest frames={status['guest_frames_observed']}")
    print(f"Outcome: {status['outcome']}; issues: {status['issues']}; "
          f"title ID: {status['title_id']}")
    print(f"Status: {STATUS}\nEvents: {LOG.parent / 'boot-events.log'}\nLog: {LOG}")
    return 0 if timed_out or code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
