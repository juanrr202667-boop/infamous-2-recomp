"""Process and environment contracts for the unattended menu witness."""
import importlib.util
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class MenuProbeTests(unittest.TestCase):
    def launcher(self):
        source = ROOT / "scripts/probe_menu.py"
        self.assertTrue(source.is_file(), "menu witness launcher is absent")
        spec = importlib.util.spec_from_file_location("menu_probe_under_test", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_controlled_environment_disables_inherited_input_and_audio(self):
        module = self.launcher()
        with tempfile.TemporaryDirectory() as temporary:
            capture = Path(temporary) / "run"
            env = module.build_environment(
                ROOT, capture, inherited={"AUDIO_WAV": "old", "PAD_SCRIPT": "old"},
                controlled=True)
            self.assertNotIn("PAD_SCRIPT", env)
            self.assertNotIn("AUDIO_WAV", env)
            self.assertEqual(env["RSX_CAP_REARM"], "1")
            self.assertEqual(env["PAD_FILE"], str(capture / "pad.txt"))

    def test_cli_accepts_controlled_mode_before_checking_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/probe_menu.py"), "--root", temporary,
                 "--seconds", "1", "--controlled"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("native executable or local ELF is missing", result.stderr)

    def test_controlled_witness_rearms_after_backend_ack(self):
        module = self.launcher()
        child = '''
from pathlib import Path
import time
t = Path("capture.ready")
while not t.exists(): time.sleep(0.01)
print("[RSXCAP] frame 1 -> frame_00", flush=True)
while t.exists(): time.sleep(0.01)
print("[RSXCAP] rearm-ready", flush=True)
Path("capture.request").write_text("after-input")
while not t.exists(): time.sleep(0.01)
print("[RSXCAP] frame 2 -> frame_01", flush=True)
time.sleep(0.2)
'''
        with tempfile.TemporaryDirectory() as temporary:
            capture = Path(temporary) / "run"
            env = module.build_environment(ROOT, capture, inherited=os.environ,
                                           captures=1, controlled=True)
            status = module.run_witness([sys.executable, "-u", "-c", child], capture,
                                        env, seconds=5, capture_after_seconds=0)
            self.assertFalse(status["timed_out"])
            self.assertEqual(status["exit_code"], 0)
            self.assertEqual(status["menu_witness"]["rsx_capture_count"], 2)

    def test_rearm_retries_transient_request_sharing_violation(self):
        module = self.launcher()
        child = '''
from pathlib import Path
import time
t = Path("capture.ready")
while not t.exists(): time.sleep(0.01)
print("[RSXCAP] frame 1 -> frame_00", flush=True)
while t.exists(): time.sleep(0.01)
print("[RSXCAP] rearm-ready", flush=True)
Path("capture.request").write_text("after-input")
while not t.exists(): time.sleep(0.01)
print("[RSXCAP] frame 2 -> frame_01", flush=True)
time.sleep(0.2)
'''
        with tempfile.TemporaryDirectory() as temporary:
            capture = Path(temporary) / "run"
            env = module.build_environment(ROOT, capture, inherited=os.environ,
                                           captures=1, controlled=True)
            real_read = Path.read_text
            denied = []
            def read_once_locked(path, *args, **kwargs):
                if path.name == "capture.request" and not denied:
                    denied.append(True)
                    raise PermissionError("sharing violation")
                return real_read(path, *args, **kwargs)
            with patch.object(Path, "read_text", read_once_locked):
                status = module.run_witness([sys.executable, "-u", "-c", child], capture,
                                            env, seconds=5, capture_after_seconds=0)
            self.assertEqual(len(denied), 1)
            self.assertFalse(status["timed_out"])
            self.assertEqual(status["exit_code"], 0)
            self.assertEqual(status["menu_witness"]["rsx_capture_count"], 2)

    def test_environment_uses_real_paths_and_clears_inherited_experiments(self):
        module = self.launcher()
        with tempfile.TemporaryDirectory() as temporary:
            capture = Path(temporary) / "run"
            inherited = {"FP_ONE": "1", "SPURS_PM_FRESH": "1", "AUDIO_WAV": "1",
                         "GCM_GUEST_FB": "0", "PPU_POKE": "ABFE50:2",
                         "PPU_POKE8": "ABFE50:2", "KEEP": "yes",
                         "I2_SKIP_INTRO": "1", "I2_AUTO_START": "1",
                         "I2_AUTO_NEWGAME": "1", "I2_AUTO_START_BTN": "start",
                         "I2_AUTO_START_DELAY_MS": "1234", "I2_FAST_CATALOG": "1",
                         "I2_UNLOCK_FPS": "1", "I2_DISABLE_MESH_TRIMMING": "1",
                         "I2_LWCOND_TRACE": "1"}
            original = inherited.copy()
            env = module.build_environment(ROOT, capture, inherited=inherited)
            self.assertEqual(inherited, original)
            self.assertNotIn("FP_ONE", env)
            self.assertNotIn("SPURS_PM_FRESH", env)
            self.assertNotIn("GCM_GUEST_FB", env)
            self.assertNotIn("PPU_POKE", env)
            self.assertNotIn("PPU_POKE8", env)
            for key in ("I2_SKIP_INTRO", "I2_AUTO_START", "I2_AUTO_NEWGAME",
                        "I2_AUTO_START_BTN", "I2_AUTO_START_DELAY_MS", "I2_FAST_CATALOG",
                        "I2_UNLOCK_FPS", "I2_DISABLE_MESH_TRIMMING"):
                with self.subTest(key=key):
                    self.assertNotIn(key, env)
            self.assertEqual(env["KEEP"], "yes")
            self.assertEqual(env["I2_LWCOND_TRACE"], "1")
            self.assertEqual(env["AUDIO_WAV"], str(capture / "mix.f32"))
            self.assertEqual(env["RSX_CAP_AFTER_FILE"], str(capture / "capture.ready"))
            self.assertEqual(env["PPU_WWATCH"], "00ABFE50")
            self.assertEqual(env["PPU_WWATCH_MAX"], "0")
            visible = module.build_environment(ROOT, capture, inherited=inherited, window=True)
            self.assertNotIn("D3D12_HIDDEN", visible)

    def test_environment_observes_a_caller_selected_guest_window(self):
        module = self.launcher()
        with tempfile.TemporaryDirectory() as temporary:
            capture = Path(temporary) / "run"
            watched = module.build_environment(ROOT, capture, watch="00370668",
                                               watch_len="0xC0", watch_max=3000)
            self.assertEqual(watched["PPU_WWATCH"], "00370668")
            self.assertEqual(watched["PPU_WWATCH_LEN"], "0xC0")
            self.assertEqual(watched["PPU_WWATCH_MAX"], "3000")
            default = module.build_environment(ROOT, capture)
            self.assertEqual(default["PPU_WWATCH"], "00ABFE50")
            self.assertEqual(default["PPU_WWATCH_LEN"], "0x20")
            self.assertEqual(default["PPU_WWATCH_MAX"], "0")

    def test_environment_requests_fresh_policy_module_context_only_on_demand(self):
        module = self.launcher()
        with tempfile.TemporaryDirectory() as temporary:
            capture = Path(temporary) / "run"
            fresh = module.build_environment(ROOT, capture, fresh_spu=True)
            self.assertEqual(fresh["SPURS_PM_FRESH"], "1")
            default = module.build_environment(ROOT, capture)
            self.assertNotIn("SPURS_PM_FRESH", default)
            # An inherited experiment must never leak through without the flag.
            inherited = {"SPURS_PM_FRESH": "1"}
            self.assertNotIn("SPURS_PM_FRESH",
                             module.build_environment(ROOT, capture, inherited=inherited))

    def test_environment_arms_the_spin_dump_only_on_demand(self):
        module = self.launcher()
        with tempfile.TemporaryDirectory() as temporary:
            capture = Path(temporary) / "run"
            armed = module.build_environment(ROOT, capture, spin_dump="1")
            self.assertEqual(armed["I2_SPIN_DUMP"], "1")
            addressed = module.build_environment(ROOT, capture, spin_dump="0FEFFAF0,380000")
            self.assertEqual(addressed["I2_SPIN_DUMP"], "0FEFFAF0,380000")
            default = module.build_environment(ROOT, capture)
            self.assertNotIn("I2_SPIN_DUMP", default)
            # A leftover experiment in the caller's environment must not arm it.
            inherited = {"I2_SPIN_DUMP": "1", "I2_SPIN_DUMP_AFTER": "1"}
            scrubbed = module.build_environment(ROOT, capture, inherited=inherited)
            self.assertNotIn("I2_SPIN_DUMP", scrubbed)
            self.assertNotIn("I2_SPIN_DUMP_AFTER", scrubbed)

    def test_cli_rejects_invalid_watch_before_looking_for_native_files(self):
        for option, value, message in (("--watch", "zz", "watch must be a 32-bit"),
                                       ("--watch", "100000000", "watch must be a 32-bit"),
                                       ("--watch-len", "0", "watch-len must be")):
            with self.subTest(option=option, value=value), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                completed = subprocess.run(
                    [sys.executable, str(ROOT / "scripts/probe_menu.py"), "--root", str(root),
                     "--seconds", "1", option + "=" + value],
                    capture_output=True, text=True, timeout=5)
                self.assertEqual(completed.returncode, 2)
                self.assertIn(message, completed.stderr)
                self.assertEqual(list(root.iterdir()), [])

    def test_child_state_report_arms_capture_without_claiming_gameplay(self):
        module = self.launcher()
        with tempfile.TemporaryDirectory() as temporary:
            capture = Path(temporary) / "run"
            command = [sys.executable, "-u", "-c", 'import time; print("[ww] 0x00ABFE50 <- 0x2 (w4) guest-fn=0x004859E4 lr=0x00485B98 tid=1"); time.sleep(0.3)']
            status = module.run_witness(command, capture, os.environ.copy(), seconds=2, progress_interval=60)
            self.assertEqual(status["exit_code"], 0)
            self.assertFalse(status["timed_out"])
            self.assertTrue(status["menu_witness"]["state2_write_observed"])
            self.assertFalse(status["menu_witness"]["gameplay_verified"])
            self.assertTrue((capture / "capture.ready").is_file())
            self.assertEqual(json.loads((capture / "status.json").read_text())["pid"], status["pid"])

    def test_diagnostic_timer_arms_capture_for_a_child_waiting_for_the_file(self):
        module = self.launcher()
        child = '''
import time
from pathlib import Path
deadline = time.monotonic() + 5
while not Path("capture.ready").is_file():
    if time.monotonic() >= deadline:
        raise SystemExit("capture file never arrived")
print("capture acknowledged", flush=True)
'''
        for delay in (0, 0.1):
            with self.subTest(delay=delay), tempfile.TemporaryDirectory() as temporary:
                capture = Path(temporary) / "run"
                status = module.run_witness([sys.executable, "-u", "-c", child], capture,
                                            os.environ.copy(), seconds=3,
                                            capture_after_seconds=delay)
                self.assertEqual(status["exit_code"], 0)
                self.assertFalse(status["timed_out"])
                self.assertEqual((capture / "capture.ready").read_text(), "diagnostic_timer\n")
                self.assertEqual(status["capture_requested_reason"], "diagnostic_timer")
                self.assertIn("capture acknowledged", (capture / "game.log").read_text())
                self.assertFalse(status["menu_witness"]["state2_write_observed"])
                self.assertFalse(status["menu_witness"]["intro_eof_observed"])
                self.assertFalse(status["menu_witness"]["gameplay_verified"])

    def test_diagnostic_timer_waits_for_the_requested_delay(self):
        module = self.launcher()
        child = '''
import time
from pathlib import Path
deadline = time.monotonic() + 5
while not Path("status.json").is_file():
    if time.monotonic() >= deadline:
        raise SystemExit("initial status never arrived")
if Path("capture.ready").exists():
    raise SystemExit("capture armed before timer deadline")
Path("timer.ack").touch()
while not Path("capture.ready").is_file():
    if time.monotonic() >= deadline:
        raise SystemExit("capture file never arrived")
'''
        with tempfile.TemporaryDirectory() as temporary:
            capture = Path(temporary) / "run"
            # The first status is published at t=0. The child verifies that no
            # capture exists, then advances the launcher clock beyond the delay.
            with patch.object(module.time, "monotonic",
                              lambda: 1.0 if (capture / "timer.ack").exists() else 0.0):
                status = module.run_witness([sys.executable, "-u", "-c", child], capture,
                                            os.environ.copy(), seconds=3,
                                            capture_after_seconds=0.5)
            self.assertEqual(status["exit_code"], 0, (capture / "game.log").read_text())
            self.assertEqual(status["capture_requested_reason"], "diagnostic_timer")

    def test_earlier_event_reason_survives_the_diagnostic_timer(self):
        module = self.launcher()
        child = '''
import json, time
from pathlib import Path
print("[ww] 0x00ABFE50 <- 0x2 (w4)", flush=True)
deadline = time.monotonic() + 5
while not Path("capture.ready").is_file():
    if time.monotonic() >= deadline:
        raise SystemExit("event did not arm capture")
Path("event.ack").write_text("capture observed")
while True:
    try:
        if json.loads(Path("status.json").read_text())["duration_seconds"] >= 1:
            break
    except (FileNotFoundError, PermissionError, json.JSONDecodeError):
        pass
    if time.monotonic() >= deadline:
        raise SystemExit("timer deadline was not observed")
'''
        with tempfile.TemporaryDirectory() as temporary:
            capture = Path(temporary) / "run"
            # Advance the launcher's clock only after the child acknowledges the
            # event capture. This orders both triggers without a scheduling race.
            with patch.object(module.time, "monotonic",
                              lambda: 1.0 if (capture / "event.ack").exists() else 0.0):
                status = module.run_witness([sys.executable, "-u", "-c", child], capture,
                                            os.environ.copy(), seconds=3,
                                            capture_after_seconds=0.5)
            self.assertEqual(status["exit_code"], 0, (capture / "game.log").read_text())
            self.assertGreaterEqual(status["duration_seconds"], 1)
            self.assertEqual(status["capture_requested_reason"], "state2_write_report")
            self.assertEqual((capture / "capture.ready").read_text(), "state2_write_report\n")
            self.assertFalse(status["menu_witness"]["gameplay_verified"])

    def test_later_event_keeps_the_diagnostic_timer_reason(self):
        module = self.launcher()
        child = '''
import time
from pathlib import Path
deadline = time.monotonic() + 5
while not Path("capture.ready").is_file():
    if time.monotonic() >= deadline:
        raise SystemExit("capture file never arrived")
print("[ww] 0x00ABFE50 <- 0x2 (w4)", flush=True)
'''
        with tempfile.TemporaryDirectory() as temporary:
            capture = Path(temporary) / "run"
            status = module.run_witness([sys.executable, "-u", "-c", child], capture,
                                        os.environ.copy(), seconds=3,
                                        capture_after_seconds=0)
            self.assertEqual(status["exit_code"], 0)
            self.assertTrue(status["menu_witness"]["state2_write_observed"])
            self.assertEqual(status["capture_requested_reason"], "diagnostic_timer")
            self.assertEqual((capture / "capture.ready").read_text(), "diagnostic_timer\n")
            self.assertFalse(status["menu_witness"]["gameplay_verified"])

    def test_invalid_capture_timer_rejects_before_creating_directory_or_child(self):
        module = self.launcher()
        for delay in (-1, 1, 2, float("nan"), float("inf"), float("-inf")):
            with self.subTest(delay=delay), tempfile.TemporaryDirectory() as temporary:
                capture = Path(temporary) / "run"
                launched = Path(temporary) / "launched"
                child = "from pathlib import Path; Path(__import__('sys').argv[1]).touch()"
                with self.assertRaisesRegex(ValueError, "capture_after_seconds"):
                    module.run_witness([sys.executable, "-c", child, str(launched)], capture,
                                       os.environ.copy(), seconds=1,
                                       capture_after_seconds=delay)
                self.assertFalse(capture.exists())
                self.assertFalse(launched.exists())

    def test_cli_rejects_invalid_capture_timer_before_looking_for_native_files(self):
        for delay in ("-1", "1", "2", "nan", "inf", "-inf"):
            with self.subTest(delay=delay), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                completed = subprocess.run(
                    [sys.executable, str(ROOT / "scripts/probe_menu.py"), "--root", str(root),
                     "--seconds", "1", "--capture-after-seconds=" + delay],
                    capture_output=True, text=True, timeout=5)
                self.assertEqual(completed.returncode, 2)
                self.assertIn("capture-after-seconds must be finite", completed.stderr)
                self.assertEqual(list(root.iterdir()), [])

    def test_cli_capture_timer_reaches_the_real_child_launcher(self):
        module = self.launcher()
        real_run = module.run_witness
        child = '''
import json, os, sys, time
from pathlib import Path
deadline = time.monotonic() + 5
while not Path("capture.ready").is_file():
    if time.monotonic() >= deadline:
        raise SystemExit("capture file never arrived")
keys = ("I2_SKIP_INTRO", "I2_AUTO_START", "I2_AUTO_NEWGAME",
        "I2_AUTO_START_BTN", "I2_AUTO_START_DELAY_MS", "I2_FAST_CATALOG",
        "I2_UNLOCK_FPS", "I2_DISABLE_MESH_TRIMMING", "I2_LWCOND_TRACE", "KEEP")
Path("child.environment.json").write_text(json.dumps({
    "environment": {key: os.environ.get(key) for key in keys}, "flags": sys.argv[1:]}))
print("capture acknowledged", flush=True)
'''
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "build/infamous2-clang2/Infamous2Native.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"local fixture RSX_CAP_AFTER_FILE")
            elf = root / "build/game/EBOOT.ELF"
            elf.parent.mkdir(parents=True)
            elf.write_bytes(b"local fixture")

            def run_python_child(command, capture, env, **kwargs):
                return real_run([sys.executable, "-u", "-c", child, *command[2:]], capture,
                                env, **kwargs)

            with patch.object(module.sys, "argv", ["probe_menu.py", "--root", str(root),
                                                 "--seconds", "3", "--capture-after-seconds", "0",
                                                 "--skip-intro", "--auto-start", "--fast-catalog",
                                                 "--unlock-fps", "--disable-mesh-trimming"]), \
                 patch.dict(module.os.environ, {"I2_SKIP_INTRO": "inherited", "I2_AUTO_START": "inherited",
                                                "I2_AUTO_NEWGAME": "1", "I2_AUTO_START_BTN": "start",
                                                "I2_AUTO_START_DELAY_MS": "1234", "I2_FAST_CATALOG": "inherited",
                                                "I2_UNLOCK_FPS": "inherited", "I2_DISABLE_MESH_TRIMMING": "inherited",
                                                "I2_LWCOND_TRACE": "1", "KEEP": "yes"}), \
                 patch.object(module, "run_witness", run_python_child):
                self.assertEqual(module.main(), 0)
            status_path = next((root / "build/game/menu-witness").glob("*/status.json"))
            status = json.loads(status_path.read_text())
            self.assertEqual(status["capture_requested_reason"], "diagnostic_timer")
            self.assertEqual(status["exit_code"], 0)
            self.assertFalse(status["menu_witness"]["gameplay_verified"])
            observed = json.loads((status_path.parent / "child.environment.json").read_text())
            self.assertEqual(observed["environment"], {
                "I2_SKIP_INTRO": "1", "I2_AUTO_START": "1", "I2_AUTO_NEWGAME": None,
                "I2_AUTO_START_BTN": None, "I2_AUTO_START_DELAY_MS": None,
                "I2_FAST_CATALOG": "1", "I2_UNLOCK_FPS": "1", "I2_DISABLE_MESH_TRIMMING": "1",
                "I2_LWCOND_TRACE": "1", "KEEP": "yes"})
            self.assertEqual(observed["flags"], ["--skip-intro", "--disable-mesh-trimming",
                                                  "--unlock-fps", "--auto-start"])

    def test_deadline_reaps_only_the_launched_child_and_keeps_artifacts(self):
        module = self.launcher()
        with tempfile.TemporaryDirectory() as temporary:
            capture = Path(temporary) / "run"
            command = [sys.executable, "-u", "-c", 'import time; print("started"); time.sleep(3)']
            start = time.monotonic()
            # Leave room for a cold Python process to start before checking that
            # the deadline terminates it and preserves its output.
            status = module.run_witness(command, capture, os.environ.copy(), seconds=0.75, progress_interval=60)
            self.assertLess(time.monotonic() - start, 2)
            self.assertTrue(status["timed_out"])
            self.assertIsNotNone(status["exit_code"])
            self.assertNotEqual(status["exit_code"], 0)
            self.assertIn("started", (capture / "game.log").read_text())
            self.assertFalse(status["menu_witness"]["state2_write_observed"])
            self.assertFalse((capture / "capture.ready").exists())

    def test_existing_run_directory_is_preserved_and_rejected(self):
        module = self.launcher()
        with tempfile.TemporaryDirectory() as temporary:
            capture = Path(temporary)
            marker = capture / "old.log"
            marker.write_text("preserve")
            with self.assertRaises(FileExistsError):
                module.run_witness([sys.executable, "-c", "pass"], capture, os.environ.copy(), seconds=1)
            self.assertEqual(marker.read_text(), "preserve")

    def test_disconnected_progress_pipe_keeps_child_until_deadline(self):
        with tempfile.TemporaryDirectory() as temporary:
            capture = Path(temporary) / "run"
            # The collector and its owned child are both real processes. Closing
            # the reader reproduces a profiler releasing the collector's stdout.
            driver = '''
import os, sys
from pathlib import Path
from scripts.probe_menu import run_witness
print("ready", flush=True)
command = [sys.executable, "-u", "-c",
           'import time; from pathlib import Path; time.sleep(0.3); '
           'Path("done").write_text("completed"); time.sleep(3)']
status = run_witness(command, Path(sys.argv[1]), os.environ.copy(),
                     seconds=1.5, progress_interval=0.05)
raise SystemExit(0 if status["timed_out"] else 1)
'''
            with (Path(temporary) / "collector.stderr").open("w+") as errors:
                with subprocess.Popen([sys.executable, "-u", "-c", driver, str(capture)],
                                      cwd=ROOT, stdout=subprocess.PIPE, stderr=errors,
                                      text=True) as collector:
                    self.assertEqual(collector.stdout.readline().strip(), "ready")
                    collector.stdout.close()
                    try:
                        self.assertEqual(collector.wait(timeout=6), 0)
                    finally:
                        if collector.poll() is None:
                            collector.kill()
                            collector.wait()
                errors.seek(0)
                self.assertEqual(errors.read(), "")
            status = json.loads((capture / "status.json").read_text())
            self.assertTrue(status["timed_out"])
            self.assertGreaterEqual(status["duration_seconds"], 0.6)
            self.assertEqual((capture / "done").read_text(), "completed")
            self.assertIsNotNone(status["exit_code"])
            self.assertFalse(status["running"])

    def test_cli_finishes_with_status_after_console_reader_disconnects(self):
        module = self.launcher()
        real_run = module.run_witness
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "build/infamous2-clang2/Infamous2Native.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"local fixture RSX_CAP_AFTER_FILE")
            elf = root / "build/game/EBOOT.ELF"
            elf.parent.mkdir(parents=True)
            elf.write_bytes(b"local fixture")
            read_fd, write_fd = os.pipe()
            os.close(read_fd)
            console = os.fdopen(write_fd, "w", encoding="utf-8")

            def run_python_child(command, capture, env, **kwargs):
                return real_run([sys.executable, "-c", 'print("completed")'], capture,
                                env, **kwargs)

            try:
                with patch.object(module.sys, "stdout", console), \
                     patch.object(module.sys, "argv", ["probe_menu.py", "--root", str(root), "--seconds", "1"]), \
                     patch.object(module, "run_witness", run_python_child):
                    self.assertEqual(module.main(), 0)
                statuses = list((root / "build/game/menu-witness").glob("*/status.json"))
                self.assertEqual(len(statuses), 1)
                status = json.loads(statuses[0].read_text())
                self.assertFalse(status["running"])
                self.assertEqual(status["exit_code"], 0)
                self.assertIn("sha256", status["executable"])
            finally:
                try:
                    console.close()
                except OSError:
                    pass

    def test_console_disk_failure_propagates_and_reaps_child(self):
        module = self.launcher()
        with tempfile.TemporaryDirectory() as temporary:
            capture = Path(temporary) / "run"
            with (Path(temporary) / "console.log").open("w") as disk:
                class FailedDisk:
                    def write(self, text):
                        raise OSError(errno.ENOSPC, "synthetic disk full")

                    def fileno(self):
                        return disk.fileno()

                with patch.object(module.sys, "stdout", FailedDisk()):
                    with self.assertRaises(OSError) as raised:
                        module.run_witness([sys.executable, "-c", "import time; time.sleep(3)"],
                                           capture, os.environ.copy(), seconds=1, progress_interval=0.01)
                self.assertEqual(raised.exception.errno, errno.ENOSPC)
                status = json.loads((capture / "status.json").read_text())
                self.assertFalse(status["running"])
                self.assertFalse(status["timed_out"])
                self.assertIsNotNone(status["exit_code"])

    def test_cli_final_message_tolerates_reader_disconnect_after_child_exit(self):
        module = self.launcher()
        real_run = module.run_witness
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "build/infamous2-clang2/Infamous2Native.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"local fixture RSX_CAP_AFTER_FILE")
            elf = root / "build/game/EBOOT.ELF"
            elf.parent.mkdir(parents=True)
            elf.write_bytes(b"local fixture")
            read_fd, write_fd = os.pipe()
            console = os.fdopen(write_fd, "w", encoding="utf-8")
            reader_open = True

            def run_python_child(command, capture, env, **kwargs):
                nonlocal reader_open
                status = real_run([sys.executable, "-c", 'print("completed")'], capture,
                                  env, **kwargs)
                os.close(read_fd)
                reader_open = False
                return status

            try:
                with patch.object(module.sys, "stdout", console), \
                     patch.object(module.sys, "argv", ["probe_menu.py", "--root", str(root), "--seconds", "1"]), \
                     patch.object(module, "run_witness", run_python_child):
                    self.assertEqual(module.main(), 0)
                status_path = next((root / "build/game/menu-witness").glob("*/status.json"))
                status = json.loads(status_path.read_text())
                self.assertFalse(status["running"])
                self.assertIn("sha256", status["executable"])
            finally:
                if reader_open:
                    os.close(read_fd)
                try:
                    console.close()
                except OSError:
                    pass

    def test_invalid_argument_from_regular_output_file_is_not_suppressed(self):
        module = self.launcher()
        with tempfile.TemporaryDirectory() as temporary:
            with (Path(temporary) / "console.log").open("w") as disk:
                class FailedDisk:
                    def write(self, text):
                        raise OSError(errno.EINVAL, "synthetic regular-file failure")

                    def fileno(self):
                        return disk.fileno()

                with patch.object(module.sys, "stdout", FailedDisk()):
                    with self.assertRaises(OSError) as raised:
                        module.report("must fail")
                self.assertEqual(raised.exception.errno, errno.EINVAL)

    @unittest.skipUnless(sys.platform == "win32", "Windows reader denies delete sharing")
    def test_locked_status_preserves_complete_pending_snapshot_then_recovers(self):
        module = self.launcher()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "status.json"
            path.write_text('{"old":true}', encoding="utf-8")
            with path.open("r"):
                started = time.monotonic()
                written = module.write_status(path, {"running": False})
                self.assertLess(time.monotonic() - started, 1.5)
                self.assertEqual(json.loads(path.read_text()), {"old": True})
                self.assertEqual(json.loads(written.read_text()), {"running": False})
            self.assertEqual(module.write_status(path, {"recovered": True}), path)
            self.assertEqual(json.loads(path.read_text()), {"recovered": True})

    @unittest.skipUnless(sys.platform == "win32", "Windows reader denies delete sharing")
    def test_status_reader_does_not_end_the_real_child_early(self):
        module = self.launcher()
        with tempfile.TemporaryDirectory() as temporary:
            capture = Path(temporary) / "run"
            locked = threading.Event()
            stop = threading.Event()

            def reader():
                deadline = time.monotonic() + 3
                while not (capture / "status.json").exists() and not stop.is_set():
                    if time.monotonic() >= deadline:
                        return
                    time.sleep(0.002)
                if not stop.is_set():
                    with (capture / "status.json").open("r"):
                        locked.set()
                        stop.wait(3)

            thread = threading.Thread(target=reader)
            thread.start()
            try:
                command = [sys.executable, "-u", "-c",
                           'import time; from pathlib import Path; time.sleep(0.5); Path("done").write_text("completed")']
                status = module.run_witness(command, capture, os.environ.copy(), seconds=2)
                self.assertTrue(locked.is_set(), "reader did not overlap the native witness writer")
                self.assertEqual(status["exit_code"], 0)
                self.assertFalse(status["timed_out"])
                self.assertEqual((capture / "done").read_text(), "completed")
                self.assertFalse(json.loads((capture / "status.tmp").read_text())["running"])
            finally:
                stop.set()
                thread.join(4)

    def test_reader_open_failure_still_reaps_the_real_child(self):
        module = self.launcher()
        real_open, real_popen = Path.open, module.subprocess.Popen
        children = []

        def open_with_failure(path, mode="r", *args, **kwargs):
            if path.name == "game.log" and mode == "r":
                raise PermissionError("synthetic log read failure")
            return real_open(path, mode, *args, **kwargs)

        def launch(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child

        with tempfile.TemporaryDirectory() as temporary:
            capture = Path(temporary) / "run"
            try:
                with patch.object(Path, "open", open_with_failure), patch.object(module.subprocess, "Popen", launch):
                    with self.assertRaises(PermissionError):
                        module.run_witness([sys.executable, "-c", "import time; time.sleep(3)"],
                                           capture, os.environ.copy(), seconds=1)
                self.assertIsNotNone(children[0].poll(), "launched child survived reader failure")
                self.assertFalse(json.loads((capture / "status.json").read_text())["running"])
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                    child.wait()


if __name__ == "__main__":
    unittest.main()
