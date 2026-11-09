"""Static contracts for the public build and test entry points."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BuildInfrastructureTests(unittest.TestCase):
    def test_windows_tests_preset_is_synthetic_and_uses_the_toolchain(self):
        presets = json.loads((ROOT / "CMakePresets.json").read_text(encoding="utf-8"))
        configure = {item["name"]: item for item in presets["configurePresets"]}
        preset = configure["windows-tests"]
        self.assertEqual(preset["cacheVariables"]["PORT_BUILD_TESTS_ONLY"], "ON")
        toolchain_value = preset.get(
            "toolchainFile", configure["windows-base"]["toolchainFile"])
        toolchain = Path(toolchain_value.replace("${sourceDir}", str(ROOT)))
        self.assertTrue(toolchain.is_file(), toolchain)
        self.assertIn("windows-port", configure)

    def test_root_enables_ctest_and_test_only_mode_is_ordered_first(self):
        root = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("enable_testing()", root)
        port = (ROOT / "src" / "port" / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("PORT_BUILD_TESTS_ONLY", port)
        self.assertLess(port.index("PORT_BUILD_TESTS_ONLY"),
                        port.index('if(NOT EXISTS "${RECOMP_DIR}/ppu_recomp.h")'))

    def test_native_tests_force_synthetic_header_in_test_only_mode(self):
        tests = (ROOT / "tests" / "native" / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("PORT_TESTS_USE_SYNTHETIC", tests)
        self.assertIn('LABELS "cpu;windows;sdk"', tests)
        self.assertIn("gpu-optional", tests)
        self.assertIn("TMP=${CMAKE_BINARY_DIR}/tmp", tests)

    def test_public_entry_points_exist(self):
        for relative in (
            "scripts/setup.ps1",
            "scripts/build.ps1",
            "scripts/test.ps1",
            "scripts/check_forbidden_files.py",
            "scripts/patch_stack.py",
            "scripts/regression_gate.py",
            ".github/workflows/ci.yml",
            "tests/baselines/regression-gate.json",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)


if __name__ == "__main__":
    unittest.main()
