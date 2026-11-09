"""Run the real planner/replay for display writes that later become textures.

Only resource allocation is represented by local identities. No GPU, game,
proprietary capture or Python implementation of temporal planning participates.
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


class DisplayCoherenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            bundled = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(bundled) if bundled.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        backend = (SDK / "libs/video/rsx_d3d12_backend.c").read_text(encoding="utf-8")
        driver = (ROOT / "tests/native/rsx_display_coherence_probe.c").read_text(encoding="utf-8")
        driver = driver.replace("/* BACKEND_TYPES */", "\n".join(
            backend_type(backend, name) for name in ("D3D12DrawRecord", "OffRT")))
        driver = driver.replace("/* BACKEND_TRANSITION */", backend_function(backend, "off_rt_transition"))
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-display-coherence-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        source = directory / "probe.c"
        source.write_text(driver, encoding="utf-8")
        cls.executable = directory / "probe.exe"
        built = subprocess.run([
            compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(SDK / "include"), "/I" + str(SDK / "libs/video"), str(source),
            "/Fe" + str(cls.executable), "/Fo" + str(directory / "probe.obj"),
        ], capture_output=True, text=True)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def run_case(self, case):
        result = subprocess.run([str(self.executable), case], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_display_metadata_preserves_previous_mrt_identity_for_texture_read(self):
        self.run_case("display-identity")

    def test_display_write_publishes_latest_pending_epoch_before_texture_read(self):
        self.run_case("display-epoch")

    def test_display_selector_b_uses_declared_member_b_identity(self):
        self.run_case("selector-b")

    def test_guest_offset_zero_remains_a_valid_active_mrt_and_texture_identity(self):
        self.run_case("guest-zero")

    def test_partial_feedback_alias_of_display_is_rejected(self):
        self.run_case("partial-alias")

    def test_inactive_zero_members_do_not_create_guest_zero_targets(self):
        self.run_case("inactive-members")
