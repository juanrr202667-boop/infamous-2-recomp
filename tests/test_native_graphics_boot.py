"""Opt-in GPU regression against the local game and captured shader output."""

import os
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("I2_GRAPHICS_TEST") == "1",
                     "set I2_GRAPHICS_TEST=1 to run the local D3D12 boot")
class NativeGraphicsBootTests(unittest.TestCase):
    def test_half_color_export_survives_full_register_alpha_write(self):
        # AUTO exported r0 because it saw an alpha-only write. The guest
        # control selects h0, where this initial UI shader computes its RGB.
        capture = ROOT / "build/game/graphics-regression"
        capture.mkdir(parents=True, exist_ok=True)
        (capture / "fp_dump.hlsl").unlink(missing_ok=True)
        env = os.environ.copy()
        for key in tuple(env):
            if key.startswith(("FP_", "VP_", "RSX_", "TEX_", "DRAW_", "CELLMARK_")):
                env.pop(key)
        env.update({
            "PS3_VERBOSE": "0", "D3D12_HIDDEN": "1", "I2_HEADLESS": "0",
            "FP_DUMP": "1", "RTT_DUMP": "1",
            "PS3_VFS_ROOT": str(ROOT / "build/game/vfs"),
            "PS3_HDD0_ROOT": str(ROOT / "build/game/vfs/hdd0"),
        })
        with (capture / "boot.log").open("wb") as log:
            process = subprocess.Popen(
                [str(ROOT / "build/infamous2-clang2/Infamous2Native.exe"),
                 str(ROOT / "build/game/EBOOT.ELF")],
                cwd=capture, env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        log = (capture / "boot.log").read_text(encoding="utf-8", errors="replace")
        self.assertTrue("[D3D12] Initialization complete" in log, "D3D12 did not initialize")
        self.assertTrue("shader_control=0x3008400" in log, "Initial half-export control was not observed")
        shader = (capture / "fp_dump.hlsl").read_text(encoding="utf-8")
        programs = [block for block in re.split(r"/\* fp_addr=", shader)[1:]
                    if block.startswith("0x0F2E7981 ")]
        self.assertTrue(programs, "Initial UI fragment program was not captured")
        for program in programs:
            self.assertIn("r[0].w =", program)
            self.assertIn("h[0].xyz =", program)
            self.assertRegex(program, r"float4 _o(?:0)? = h\[0\];",
                             "Guest half-color export was replaced by the full register alpha temporary")


if __name__ == "__main__":
    unittest.main()
