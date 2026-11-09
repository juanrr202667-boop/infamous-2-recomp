"""Run the backend's actual RSX_CAP gate against temporary local files.

The capture callback records the scheduled frame. No device, readback, pixels,
rendering, presentation, FIFO decoding, or game execution is exercised here.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"

PRELUDE = r'''
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned int u32;
static struct { u32 frame_count; } s_d3d;
static u32 s_dbg_last_draws;
static int s_present_this_frame = 1;
static void rsx_capture_frame(u32 fi, u32 ndraws, u32 capidx) {
    printf("CAP %u %u %u %u\n", s_d3d.frame_count, capidx, ndraws, fi);
}
static void run_frame(u32 frame, u32 draws) {
    const u32 fi = frame % 2;
    s_d3d.frame_count = frame;
    s_dbg_last_draws = draws;
'''

DRIVER = r'''
}
int main(int argc, char** argv) {
    if (argc < 2) return 2;
    for (int i = 2; i < argc; ++i) {
        if (!strcmp(argv[i], "create")) {
            FILE* file = fopen(argv[1], "wb");
            if (!file) return 3;
            fclose(file);
        } else if (!strcmp(argv[i], "remove")) {
            if (remove(argv[1])) return 4;
        } else if (!strcmp(argv[i], "directory")) {
            if (!CreateDirectoryA(argv[1], NULL)) return 5;
        } else if (!strcmp(argv[i], "wait")) {
            Sleep(1500);
        } else if (!strcmp(argv[i], "present0")) {
            s_present_this_frame = 0;
        } else if (!strcmp(argv[i], "present1")) {
            s_present_this_frame = 1;
        } else {
            u32 frame, draws;
            if (sscanf(argv[i], "%u:%u", &frame, &draws) != 2) return 6;
            run_frame(frame, draws);
        }
    }
    return 0;
}
'''


class CaptureTriggerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            bundled = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(bundled) if bundled.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-rsx-capture-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        source = (SDK / "libs/video/rsx_d3d12_backend.c").read_text(encoding="utf-8")
        start = source.index('    { const char* cap = getenv("RSX_CAP");')
        end = source.index("\n\n    /* Present */", start)
        probe = directory / "probe.c"
        probe.write_text(PRELUDE + source[start:end] + DRIVER, encoding="utf-8")
        cls.executable = directory / "probe.exe"
        result = subprocess.run([
            compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            str(probe), "/Fe" + str(cls.executable),
            "/Fo" + str(directory / "probe.obj"),
        ], text=True, capture_output=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def run_frames(self, *steps, capture="890:3:2", after_file=True, rearm=False, events=False, allow_empty=False, presented_only=False):
        with tempfile.TemporaryDirectory(prefix="i2-rsx-trigger-") as directory:
            trigger = Path(directory) / "capture ready.flag"
            env = os.environ.copy()
            env.pop("RSX_CAP", None)
            env.pop("RSX_CAP_AFTER_FILE", None)
            env.pop("RSX_CAP_REARM", None)
            env.pop("RSX_CAP_ALLOW_EMPTY", None)
            env.pop("RSX_CAP_PRESENTED_ONLY", None)
            if presented_only:
                env["RSX_CAP_PRESENTED_ONLY"] = "1"
            if allow_empty:
                env["RSX_CAP_ALLOW_EMPTY"] = "1"
            if rearm:
                env["RSX_CAP_REARM"] = "1"
            if capture is not None:
                env["RSX_CAP"] = capture
            if after_file:
                env["RSX_CAP_AFTER_FILE"] = str(trigger)
            result = subprocess.run([
                str(self.executable), str(trigger), *steps,
            ], env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            if events:
                return result.stdout.splitlines()
            return [tuple(map(int, line.split()[1:]))
                    for line in result.stdout.splitlines() if line.startswith("CAP ")]

    def test_absent_trigger_blocks_even_expired_start(self):
        # Removing the wait would capture these frames after the old start.
        self.assertEqual(self.run_frames("890:1", "900:1", "1200:1"), [])

    def test_presented_only_does_not_consume_capture_on_intermediate_batch(self):
        self.assertEqual(self.run_frames("create", "present0", "20:1", "21:1",
                                         "present1", "22:1", capture="890:1:1",
                                         presented_only=True), [(22, 0, 1, 0)])

    def test_created_trigger_captures_current_frame_then_original_stride(self):
        # Keeping the old start would miss 101 or consume count too early.
        self.assertEqual(self.run_frames(
            "100:1", "create", "101:1", "102:1", "103:1", "104:1",
            "105:1", "106:1", "1100:1",
        ), [(101, 0, 1, 1), (103, 1, 1, 1), (105, 2, 1, 1)])

    def test_removing_trigger_preserves_arm_and_recreation_does_not_restart(self):
        # Rechecking or rearming the file would suppress 124 or shift 127.
        self.assertEqual(self.run_frames(
            "120:1", "create", "121:1", "remove", "122:1", "123:1",
            "124:1", "create", "125:1", "126:1", "127:1",
            capture="890:3:3",
        ), [(121, 0, 1, 1), (124, 1, 1, 0), (127, 2, 1, 1)])

    def test_unset_trigger_preserves_absolute_start_count_and_stride(self):
        # Always replacing start would capture 889 rather than 890.
        self.assertEqual(self.run_frames(
            "889:1", "890:1", "891:1", "892:1", "893:1", "894:1", "895:1",
            after_file=False,
        ), [(890, 0, 1, 0), (892, 1, 1, 0), (894, 2, 1, 0)])

    def test_armed_trigger_still_requires_draws(self):
        # Bypassing the existing draw guard would capture the empty frame 20.
        self.assertEqual(self.run_frames(
            "create", "20:0", "21:1", "22:1", "23:1",
            capture="890:2:2",
        ), [(21, 0, 1, 1), (22, 1, 1, 0)])

    def test_opt_in_allows_capture_without_new_draws(self):
        self.assertEqual(self.run_frames("create", "20:0", capture="890:1:1",
                                         allow_empty=True), [(20, 0, 0, 0)])

    def test_directory_does_not_arm_capture(self):
        # Treating any filesystem entry as a file would capture frame 900.
        self.assertEqual(self.run_frames("directory", "900:1"), [])

    def test_trigger_without_rsx_cap_does_not_enable_capture(self):
        # Enabling capture through the trigger alone would create output here.
        self.assertEqual(self.run_frames("create", "900:1", capture=None), [])

    def test_opt_in_rearms_after_removal_and_keeps_unique_indices(self):
        # A second phase must capture new frames without overwriting the first.
        self.assertEqual(self.run_frames(
            "create", "10:1", "11:1", "remove", "12:1", "create",
            "20:1", "21:1", capture="890:2:1", rearm=True,
        ), [(10, 0, 1, 0), (11, 1, 1, 1),
            (20, 2, 1, 0), (21, 3, 1, 1)])

    def test_rearm_acknowledges_removal_only_after_batch_completed(self):
        events = self.run_frames("create", "10:1", "remove", "11:1", "12:1",
                                 "13:1", capture="890:2:1", rearm=True, events=True)
        self.assertEqual(events, ["CAP 10 0 1 0", "CAP 11 1 1 1", "[RSXCAP] rearm-ready"])

    def test_capture_report_is_visible_before_process_exits(self):
        import time
        with tempfile.TemporaryDirectory(prefix="i2-rsx-live-") as directory:
            output = Path(directory) / "output.txt"
            env = os.environ.copy()
            env["RSX_CAP"] = "1:1:1"
            env.pop("RSX_CAP_AFTER_FILE", None)
            env.pop("RSX_CAP_REARM", None)
            with output.open("w") as stream:
                child = subprocess.Popen([str(self.executable), "unused", "1:1", "wait"],
                                         env=env, stdout=stream)
                try:
                    deadline = time.monotonic() + 1.0
                    while time.monotonic() < deadline and not output.read_text():
                        time.sleep(0.02)
                    self.assertIn("CAP 1 0 1 1", output.read_text())
                    self.assertIsNone(child.poll())
                finally:
                    child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
