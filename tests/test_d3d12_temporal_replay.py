"""Execute temporal replay and the real D3D12 copy adapter on CPU pixel stores.

Only COM command recording is simulated. No device, GPU, game or generated
game source participates in this fixture.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"


def backend_type(source, name):
    end = source.index("} " + name + ";") + len("} " + name + ";")
    return source[source.rfind("typedef struct {", 0, end):end]


def backend_function(source, name):
    start = source.index("static void " + name + "(")
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


class TemporalReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            bundled = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(bundled) if bundled.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        backend = (SDK / "libs/video/rsx_d3d12_backend.c").read_text(encoding="utf-8")
        driver = (ROOT / "tests/native/rsx_d3d12_temporal_replay_probe.c").read_text(encoding="utf-8")
        driver = driver.replace("/* BACKEND_TYPES */", "\n".join(
            backend_type(backend, name) for name in ("D3D12DrawRecord", "OffRT")))
        capture_end = backend.index("} s_present_capture;") + len("} s_present_capture;")
        capture_start = backend.rfind("static struct {", 0, capture_end)
        driver = driver.replace("/* BACKEND_PRESENT_CAPTURE */", backend[capture_start:capture_end])
        driver = driver.replace("/* BACKEND_TRANSITION */", backend_function(backend, "off_rt_transition"))
        feedback = (backend_function(backend, "temporal_snapshot_feedback")
                    if "static void temporal_snapshot_feedback(" in backend
                    else "static void temporal_snapshot_feedback(u32 draw) { (void)draw; }")
        driver = driver.replace("/* BACKEND_FEEDBACK */", feedback)
        display = []
        for name in ("temporal_display_before", "temporal_display_after"):
            display.append(backend_function(backend, name) if "static void " + name + "(" in backend
                           else "static void " + name + "(u32 draw) { (void)draw; }")
        driver = driver.replace("/* BACKEND_DISPLAY */", "\n".join(display))
        if "static void temporal_render_clear_only(" in backend:
            start = backend.index("static D3D12_RECT temporal_clear_rect(")
            clear = backend[start:backend.index("\n}", start)+2]
            clear += "\n" + backend_function(backend, "temporal_render_clear_only")
        else:
            clear = "static void temporal_render_clear_only(u32 draw,D3D12_CPU_DESCRIPTOR_HANDLE h) { (void)draw; (void)h; }"
        driver = driver.replace("/* BACKEND_CLEAR_ONLY */", clear)

        cls.directory = tempfile.TemporaryDirectory(prefix="i2-temporal-replay-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        source = directory / "probe.c"
        source.write_text(driver, encoding="utf-8")
        cls.executable = directory / "probe.exe"
        built = subprocess.run([
            compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(SDK / "include"), "/I" + str(SDK / "libs/video"),
            str(source), "/Fe" + str(cls.executable), "/Fo" + str(directory / "probe.obj"),
        ], capture_output=True, text=True)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def run_case(self, case):
        result = subprocess.run([str(self.executable), case], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_partial_write_materializes_retained_pixels_before_publication(self):
        self.run_case("pending-and-partial")

    def test_interior_offset_and_different_pitch_preserve_guest_addresses(self):
        self.run_case("offset-and-pitch")

    def test_previous_batch_producer_survives_as_copy_source(self):
        self.run_case("cross-batch")

    def test_copy_failure_does_not_publish_pending_versions(self):
        self.run_case("copy-failure")

    def test_invalid_catalog_aborts_before_recording_commands(self):
        self.run_case("plan-failure")

    def test_feedback_materializes_then_refreshes_once_per_view_per_draw(self):
        self.run_case("feedback-refresh")

    def test_invalid_feedback_mask_aborts_before_any_command(self):
        self.run_case("feedback-invalid")

    def test_feedback_source_must_be_an_active_destination(self):
        self.run_case("feedback-source-invalid")

    def test_active_destination_cannot_be_sampled_directly(self):
        self.run_case("feedback-direct-invalid")

    def test_display_bridge_loads_latest_alias_and_publishes_real_writeback(self):
        self.run_case("display-bridge")

    def test_clear_only_replays_without_a_vertex_pipeline(self):
        self.run_case("clear-no-pipeline")
