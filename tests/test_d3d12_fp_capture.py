"""Exercise capture serialization with distinct completed/in-flight FP banks.

This compiles the backend's serializer; it does not render or prove gameplay.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "tools/ps3recomp/libs/video/rsx_d3d12_backend.c"

PRELUDE = r'''
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
typedef uint32_t u32;
typedef uint64_t u64;
#define MAX_DRAWS 3
#define RSX_D3D12_FP_CONSTANT_COUNT 128
#define RSX_D3D12_FP_CONSTANT_FLOAT_OFFSET 68
#define VP_FPCB_STRIDE 2560
typedef struct { int is_clear; u32 fp_addr; int fp_exp32; u32 cb_slot; } D3D12DrawRecord;
static struct {
    void* vp_fpcb_mapped;
    u32 vp_parity;
    D3D12DrawRecord draws[MAX_DRAWS];
} s_d3d;
static struct { u32 display_target; } s_rt_plan[MAX_DRAWS];
static u32 s_temporal_fp_hash[MAX_DRAWS];
static unsigned char s_temporal_fp_hash_valid[MAX_DRAWS];
static u32 s_recorded_fp_hash[MAX_DRAWS];
static unsigned char s_recorded_fp_prefix[MAX_DRAWS][32];
'''

DRIVER = r'''
int main(int argc, char** argv) {
    unsigned char banks[2 * MAX_DRAWS * VP_FPCB_STRIDE] = {0};
    s_d3d.vp_fpcb_mapped = banks;
    s_d3d.vp_parity = argc > 1 ? (u32)atoi(argv[1]) : 1;
    const u32 slot = argc > 3 ? (u32)atoi(argv[3]) : 0;
    s_d3d.draws[0].cb_slot = slot;
    s_d3d.draws[0].fp_addr = 0xAC7602;
    s_d3d.draws[1].fp_addr = 0xAC7602;
    s_d3d.draws[1].is_clear = 1;
    s_d3d.draws[2].fp_addr = 0xAC7602;
    s_rt_plan[0].display_target = 1;
    s_rt_plan[1].display_target = 1;
    s_temporal_fp_hash[0] = 0x1234ABCD;
    s_temporal_fp_hash_valid[0] = 1;
    s_recorded_fp_hash[slot] = 0xABCDEF12;
    s_recorded_fp_prefix[slot][0] = 0xDE;
    s_recorded_fp_prefix[slot][1] = 0xAD;
    float* completed = (float*)(banks +
        ((s_d3d.vp_parity ^ 1) * MAX_DRAWS + slot) * VP_FPCB_STRIDE);
    float* pending = (float*)(banks +
        (s_d3d.vp_parity * MAX_DRAWS + slot) * VP_FPCB_STRIDE);
    completed[68] = 1.25f; completed[69] = -2.5f;
    completed[70] = 0; completed[71] = 4;
    pending[68] = 99;
    completed[68 + 127 * 4] = 17;
    if (argc > 2 && !strcmp(argv[2], "missing")) s_d3d.vp_fpcb_mapped = NULL;
    write_capture_fragment_state(stdout, MAX_DRAWS + 2);
    return 0;
}
'''


class FragmentCaptureTests(unittest.TestCase):
    def compile_probe(self):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            candidate = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(candidate) if candidate.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        source = BACKEND.read_text(encoding="utf-8")
        match = re.search(r"static void write_capture_fragment_state\([^;]*?\)\n\{.*?\n}\n", source, re.S)
        if not match:
            raise AssertionError("backend has no fragment-state capture serializer")
        self.directory = tempfile.TemporaryDirectory(prefix="i2-fp-capture-")
        self.addCleanup(self.directory.cleanup)
        directory = Path(self.directory.name)
        probe = directory / "probe.c"
        probe.write_text(PRELUDE + "\n#include <string.h>\n" + match[0] + DRIVER, encoding="utf-8")
        self.executable = directory / "probe.exe"
        result = subprocess.run([compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
                                 str(probe), "/Fe" + str(self.executable),
                                 "/Fo" + str(directory / "probe.obj")], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def run_capture(self, parity=1, enabled=True, missing=False, slot=0):
        if not hasattr(self, "executable"):
            self.compile_probe()
        env = os.environ.copy()
        env.pop("RSX_CAP_FP_STATE", None)
        if enabled:
            env["RSX_CAP_FP_STATE"] = "1"
        result = subprocess.run([str(self.executable), str(parity), "missing" if missing else "ok", str(slot)],
                                capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_completed_bank_and_selected_shader_identity_are_serialized(self):
        for parity in (0, 1):
            for slot in (0, 2):
                with self.subTest(parity=parity, slot=slot):
                    output = self.run_capture(parity, slot=slot)
                    self.assertIn("hash=1234ABCD hash_valid=1", output)
                    self.assertIn("record_hash=ABCDEF12", output)
                    self.assertIn("record_prefix=DEAD000000000000", output)
                    self.assertIn("k[0] = 1.25 -2.5 0 4", output)
                    self.assertIn("k[127] = 17 0 0 0", output)
                    self.assertNotIn("op001", output)
                    self.assertNotIn("op002", output)

    def test_disabled_diagnostic_emits_nothing(self):
        self.assertEqual(self.run_capture(enabled=False), "")

    def test_missing_bank_is_reported_without_reading_memory(self):
        self.assertIn("unavailable", self.run_capture(missing=True))
