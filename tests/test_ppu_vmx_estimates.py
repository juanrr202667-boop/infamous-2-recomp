"""Execute real lifted VMX estimates against independent Power ISA values.

These tests catch missing lowering, the reserved vA field used as the source,
incorrect guest byte order, lost aliasing, and incorrect VSCR[NJ] handling.
The fixture includes the generator's production preambles and compiles only
four synthetic instructions; it never loads or modifies game files.
"""
from pathlib import Path
import math
import os
import struct
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools/ps3recomp/tools"
sys.path.insert(0, str(TOOLS))
from ppu_disasm import decode
from ppu_lifter import HEADER_PREAMBLE, SOURCE_PREAMBLE, PPULifter


HARNESS = r"""
#include <stdlib.h>
#include <xmmintrin.h>
int main(int argc, char** argv) {
    if (argc != 9) return 2;
    const int op = atoi(argv[1]);
    const int alias = atoi(argv[2]);
    const int nj = atoi(argv[3]);
    const int destination = alias ? 5 : 7;
    ppu_context state;
    memset(&state, 0xA5, sizeof state);
    state.cr = 0x5A123456u;
    state.fpscr = 0x87654321u;
    state.vscr = 0xA5000001u | (nj ? 0x10000u : 0u);
    // Independent guest-format input: each lane is big endian, including NaNs.
    for (int lane = 0; lane < 4; ++lane) {
        const uint32_t bits = (uint32_t)strtoul(argv[4 + lane], 0, 16);
        for (int byte = 0; byte < 4; ++byte)
            state.vr[5].b[lane * 4 + byte] = (uint8_t)(bits >> (24 - byte * 8));
    }
    // The reserved vA field decodes as v0; it must not supply the input.
    memset(&state.vr[0], 0x3F, sizeof state.vr[0]);
    ppu_context before;
    memcpy(&before, &state, sizeof state);
    const uint32_t initial_csr = _mm_getcsr();
    const uint32_t expected_csr = atoi(argv[8])
        ? ((initial_csr | 0x8040u | 0x21u) & ~0x6000u) | 0x4000u
        : initial_csr;
    _mm_setcsr(expected_csr); // Adversarial FTZ, DAZ, upward rounding, sticky flags.
    if (op == 0) {
        if (alias) func_00010100(&state); else func_00010000(&state);
    } else {
        if (alias) func_00010300(&state); else func_00010200(&state);
    }
    const uint32_t actual_csr = _mm_getcsr();
    _mm_setcsr(initial_csr);
    for (int lane = 0; lane < 4; ++lane) {
        for (int byte = 0; byte < 4; ++byte)
            printf("%02X", state.vr[destination].b[lane * 4 + byte]);
        printf(" ");
    }
    // Only the destination vector may change; flags and source stay intact.
    before.vr[destination] = state.vr[destination];
    printf("%d %d\n", memcmp(&before, &state, sizeof state) == 0,
           actual_csr == expected_csr);
    return 0;
}
"""


def bits(value):
    return struct.unpack(">I", struct.pack(">f", value))[0]


def value(raw):
    return struct.unpack(">f", struct.pack(">I", raw))[0]


@unittest.skipUnless(os.name == "nt", "The compiled fixture uses Windows clang-cl")
class VMXEstimateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configured = os.environ.get("I2_CLANG_CL")
        compiler = Path(configured) if configured else ROOT / "tools/llvm/bin/clang-cl.exe"
        if not compiler.is_file():
            raise unittest.SkipTest("clang-cl is unavailable; set I2_CLANG_CL")
        cls.temporary = tempfile.TemporaryDirectory(prefix="i2-vmx-estimates-")
        cls.addClassCleanup(cls.temporary.cleanup)
        directory = Path(cls.temporary.name)
        (directory / "ppu_recomp.h").write_text(HEADER_PREAMBLE, encoding="utf-8")
        functions = []
        for index, (opcode, destination) in enumerate([(394, 7), (394, 5),
                                                      (458, 7), (458, 5)]):
            address = 0x10000 + index * 0x100
            # Opcode 4, unary VX form: vD varies, reserved vA is zero, vB is 5.
            word = (4 << 26) | (destination << 21) | (5 << 11) | opcode
            instructions = [decode(word, address), decode(0x4E800020, address + 4)]
            function = PPULifter().lift_function(instructions, address, address + 8)
            functions.append("void " + function.name + "(ppu_context* ctx) {\n"
                             + "\n".join(function.body_lines) + "\n}\n")
        source = directory / "vmx_estimates.cpp"
        source.write_text(SOURCE_PREAMBLE + "\n".join(functions) + HARNESS,
                          encoding="utf-8")
        cls.executable = directory / "vmx_estimates.exe"
        command = [str(compiler), "/nologo", "/std:c++17", "/EHsc", "/O2",
                   "/clang:-fuse-ld=lld", str(source),
                   "/Fe:" + str(cls.executable), "/Fo:" + str(directory / "vmx.obj")]
        compiled = subprocess.run(command, cwd=directory, capture_output=True,
                                  text=True, timeout=60)
        if compiled.returncode:
            raise RuntimeError("VMX fixture compilation failed:\n" + compiled.stdout
                               + compiled.stderr)

    def run_instruction(self, operation, inputs, *, alias=False, nj=False,
                        host_adverse=False):
        completed = subprocess.run(
            [str(self.executable), str(operation), str(int(alias)), str(int(nj)),
             *(f"{raw:08X}" for raw in inputs), str(int(host_adverse))],
            capture_output=True, text=True, timeout=5, check=True)
        output = completed.stdout.split()
        self.assertEqual(len(output), 6, completed.stdout)
        self.assertEqual(output[-2], "1", "Instruction changed another register or flags")
        self.assertEqual(output[-1], "1", "Instruction changed host MXCSR")
        return [int(raw, 16) for raw in output[:4]]

    def assert_quiet_nan(self, raw):
        self.assertEqual(raw & 0x7FC00000, 0x7FC00000,
                         f"Expected quiet NaN, got 0x{raw:08X}")

    def assert_log_estimate(self, raw, expected):
        actual = value(raw)
        self.assertTrue(math.isfinite(actual), f"Nonfinite log estimate: 0x{raw:08X}")
        self.assertLessEqual(abs(actual - expected), 1.0 / 32.0)
        if expected:
            self.assertLessEqual(abs((actual - expected) / expected), 1.0 / 8.0)

    def test_exp_integer_results_are_exact_in_all_guest_lanes(self):
        result = self.run_instruction(0, [0xC0000000, 0xBF800000, 0, 0x40400000])
        self.assertEqual(result, [0x3E800000, 0x3F000000, 0x3F800000, 0x41000000])

    def test_exp_fractional_accuracy(self):
        result = self.run_instruction(0, [bits(x) for x in [-0.5, 0.5, 1.25, -3.25]])
        expected = [0.7071067811865476, 1.4142135623730951,
                    2.378414230005442, 0.10511205190671431]
        for raw, want in zip(result, expected):
            self.assertLessEqual(abs((value(raw) - want) / want), 1.0 / 16.0)

    def test_log_accuracy_in_all_guest_lanes(self):
        result = self.run_instruction(1, [0x3E800000, 0x3F000000, 0x3F800000, 0x41000000])
        for raw, want in zip(result, [-2.0, -1.0, 0.0, 3.0]):
            self.assert_log_estimate(raw, want)

    def test_destination_can_alias_source(self):
        for operation, inputs, expected in [
            (0, [0xC0000000, 0xBF800000, 0, 0x40400000],
             [0x3E800000, 0x3F000000, 0x3F800000, 0x41000000]),
            (1, [0x3E800000, 0x3F000000, 0x3F800000, 0x41000000],
             [-2.0, -1.0, 0.0, 3.0])]:
            with self.subTest(operation=operation):
                result = self.run_instruction(operation, inputs, alias=True)
                if operation == 0:
                    self.assertEqual(result, expected)
                else:
                    for raw, want in zip(result, expected):
                        self.assert_log_estimate(raw, want)

    def test_exp_infinities_and_signed_zeros(self):
        result = self.run_instruction(0, [0x7F800000, 0xFF800000, 0, 0x80000000])
        self.assertEqual(result, [0x7F800000, 0, 0x3F800000, 0x3F800000])

    def test_log_infinities_and_signed_zeros(self):
        result = self.run_instruction(1, [0x7F800000, 0xFF800000, 0, 0x80000000])
        self.assertEqual(result[0], 0x7F800000)
        self.assert_quiet_nan(result[1])
        self.assertEqual(result[2:], [0xFF800000, 0xFF800000])

    def test_log_negative_inputs_and_nans_return_quiet_nan(self):
        result = self.run_instruction(1, [0xBF800000, 0xC1200000, 0x7F800001, 0xFFC12345])
        for raw in result:
            self.assert_quiet_nan(raw)

    def test_exp_quiets_nan_inputs(self):
        result = self.run_instruction(0, [0x7F800001, 0xFF800001, 0x7FC12345, 0xFFC12345])
        for raw in result:
            self.assert_quiet_nan(raw)

    def test_log_denormal_inputs_are_preserved_with_nj_clear(self):
        result = self.run_instruction(1, [1, 0x80000001, 0x00400000, 0x80400000])
        self.assert_log_estimate(result[0], -149.0)
        self.assert_quiet_nan(result[1])
        self.assert_log_estimate(result[2], -127.0)
        self.assert_quiet_nan(result[3])

    def test_log_denormal_inputs_flush_to_signed_zero_with_nj_set(self):
        result = self.run_instruction(1, [1, 0x80000001, 0x00400000, 0x80400000], nj=True)
        self.assertEqual(result, [0xFF800000] * 4)

    def test_exp_denormal_results_are_preserved_with_nj_clear(self):
        result = self.run_instruction(0, [bits(x) for x in [-127, -128, -149, -150]])
        self.assertEqual(result, [0x00400000, 0x00200000, 1, 0])

    def test_exp_denormal_results_flush_with_nj_set(self):
        result = self.run_instruction(0, [bits(x) for x in [-127, -128, -149, -150]], nj=True)
        self.assertEqual(result, [0] * 4)

    def test_host_flush_modes_do_not_change_guest_exp_denormals(self):
        result = self.run_instruction(0, [bits(x) for x in [-127, -128, -149, -150]],
                                      host_adverse=True)
        self.assertEqual(result, [0x00400000, 0x00200000, 1, 0])

    def test_host_flush_modes_do_not_change_guest_log_denormals(self):
        result = self.run_instruction(1, [1, 0x80000001, 0x00400000, 0x80400000],
                                      host_adverse=True)
        self.assert_log_estimate(result[0], -149.0)
        self.assert_quiet_nan(result[1])
        self.assert_log_estimate(result[2], -127.0)
        self.assert_quiet_nan(result[3])

    def test_host_rounding_mode_does_not_change_guest_normal_estimates(self):
        result = self.run_instruction(0, [0xC0000000, 0xBF800000, 0, 0x40400000],
                                      host_adverse=True)
        self.assertEqual(result, [0x3E800000, 0x3F000000, 0x3F800000, 0x41000000])
        result = self.run_instruction(1, [0x3E800000, 0x3F000000, 0x3F800000, 0x41000000],
                                      host_adverse=True)
        for raw, want in zip(result, [-2.0, -1.0, 0.0, 3.0]):
            self.assert_log_estimate(raw, want)


if __name__ == "__main__":
    unittest.main()
