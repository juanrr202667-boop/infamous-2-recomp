"""Execute the runner's real input polls and clock with synthetic CPU boundaries.

The C++ bodies are read from main.cpp on every compilation. Only the host clock,
RSX callbacks, and input sink are fixtures; no game, GPU, or copied policy runs.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def function_body(source: str, declaration: str) -> str:
    start = source.index(declaration)
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


PREAMBLE = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
using ULONGLONG = uint64_t;
using DWORD = unsigned long;
using LPVOID = void*;
#define WINAPI
#define WINDOW_WIDTH 1280
#define WINDOW_HEIGHT 720
#define RSX_BACKEND_NAME "CPU fixture"
static uint8_t memory[0x02000000];
static uint8_t* vm_base = memory;
static uint32_t ppu_vm_size = sizeof memory;
static bool g_headless = false;
static bool g_opt_unlock_fps = false;
static uint64_t now_ms = 1000;
static unsigned vblanks, flips, drains, pulses, presents;
static unsigned one_ms_sleeps;
static unsigned last_port, last_mask;
static int last_duration;
static unsigned iteration;
static bool frozen_clock;
static bool lagged_clock;
static bool flip_sequence;
static unsigned pending_flip, requested_flips, fifo_step, batch_bits;
static unsigned presented_batches[4];
static const uint64_t times[] = {1000, 1000, 1001, 1015, 1016, 1032};
struct ClockDone {};
struct rsx_backend {
    void (*draw_arrays)(void*, uint32_t, uint32_t, uint32_t);
    void (*draw_indexed)(void*, uint32_t, uint32_t, uint32_t);
    void (*present)(void*, uint32_t);
};
static rsx_backend g_headless_backend;
static ULONGLONG GetTickCount64() { return now_ms; }
static void Sleep(unsigned ms) { if (ms == 1) ++one_ms_sleeps; }
static void SwitchToThread() {}
extern "C" void cellPad_inject_pulse(uint32_t port, uint16_t mask, int duration) {
    ++pulses; last_port = port; last_mask = mask; last_duration = duration;
}
extern "C" void cellPad_inject_stick(uint32_t port, int lx, int ly, int rx, int ry, int duration) {
    (void)port; (void)lx; (void)ly; (void)rx; (void)ry; (void)duration;
}
struct ppu_context {
    uint64_t gpr[32];
    uint64_t lr;
    uint64_t ctr;
};
extern "C" {
    void func_0004A160(ppu_context*) {}
    void func_006BCF80(ppu_context*) {}
    void func_004859E4(ppu_context*) {}
    void func_006C99D0(ppu_context*) {}
    void func_004858A4(ppu_context*) {}
}
static void headless_draw_arrays(void*, uint32_t, uint32_t, uint32_t) {}
static void headless_draw_indexed(void*, uint32_t, uint32_t, uint32_t) {}
static void headless_present(void*, uint32_t) {}
static void rsx_set_backend(rsx_backend*) {}
static int rsx_backend_init(uint32_t, uint32_t, const char*) { return 0; }
static void write_telemetry(int) {}
static void backend_present() {}
static void present_guest_frame() {
    if (flip_sequence && presents < 4) {
        presented_batches[presents] = batch_bits;
        batch_bits = 0;
    }
    ++presents;
}
static void cellGcmTickVBlank() { ++vblanks; }
static void cellGcmTickFlip() { ++flips; }
static int cellGcm_take_flip_pending() {
    const unsigned result = pending_flip;
    pending_flip = 0;
    return result;
}
static unsigned cellGcm_flip_request_count() { return requested_flips; }
static void cellGcm_rsx_process_fifo() {
    ++drains;
    if (!flip_sequence) return;
    // Each call represents a drain ending at a flip or an incomplete batch.
    // Frame A is 0x11; frame B is assembled from 0x20 followed by 0x02.
    switch (fifo_step++) {
    case 0: batch_bits |= 0x11; pending_flip = 1; ++requested_flips; break;
    case 1: batch_bits |= 0x20; break;
    case 2: batch_bits |= 0x02; pending_flip = 1; ++requested_flips; break;
    }
}
static int rsx_backend_pump() {
    ++iteration;
    if (iteration == (frozen_clock ? 32u : 6u)) throw ClockDone{};
    now_ms = frozen_clock ? 1000 : lagged_clock ? 5000 : times[iteration];
    return 0;
}
'''


HARNESS = r'''
extern "C" sys_mem_alloc_info g_sys_mem_allocs[SYS_MEMORY_ALLOC_MAX] = {};
int main(int argc, char** argv) {
    if (argc < 2) return 2;
    if (strcmp(argv[1], "configure") == 0) {
        configure_input(argc, argv);
        guest_write32(0x00ABFE50, 2);
        i2_poll_auto_newgame();
        now_ms = 1500;
        i2_poll_auto_newgame();
        printf("RESULT %u %u %u %d\n", pulses, last_port, last_mask, last_duration);
        return 0;
    }
    if (strcmp(argv[1], "poll") == 0) {
        guest_write32(0x00ABFE50, 2);
        i2_poll_auto_newgame();
        unsigned entered = pulses;
        now_ms = 1499;
        i2_poll_auto_newgame();
        unsigned early = pulses;
        now_ms = 1500;
        i2_poll_auto_newgame();
        printf("RESULT %u %u %u %u %u %d\n", entered, early, pulses,
               last_port, last_mask, last_duration);
        return 0;
    }
    if (strcmp(argv[1], "clock") != 0 || argc < 3) return 2;
    const uint32_t hero = 0x20000;
    guest_write32(0x00ABFE50, strcmp(argv[2], "boot") == 0 ? 0 :
                 strcmp(argv[2], "unknown") == 0 ? 0xFFFFFFFF : 3);
    guest_write32(0x00ABFC64, 0x10000);
    guest_write32(0x102E0, hero);
    guest_write32(hero + 4, 0xB);
    guest_write32(hero + 0x340, 0x41100000); // 9.0f
    guest_write32(hero + 0x344, 0x3FA00000); // 1.25f
    guest_write32(hero + 0x348, 0xC0E00000); // -7.0f
    guest_write32(hero + 0x374, 0xC0800000); // -4.0f
    if (strcmp(argv[2], "rising") == 0) guest_write32(hero + 0x374, 0x40800000);
    if (strcmp(argv[2], "kicker") == 0 || strcmp(argv[2], "frozen") == 0)
        guest_write32(0x00A25A18, 3);
    flip_sequence = strcmp(argv[2], "busy-flips") == 0 ||
                    strcmp(argv[2], "pending-before-burst") == 0;
    if (flip_sequence) guest_write32(0x00A25A18, 3);
    if (strcmp(argv[2], "pending-before-burst") == 0) {
        batch_bits = 0x11;
        pending_flip = requested_flips = fifo_step = 1;
    }
    if (strcmp(argv[2], "fence") == 0) {
        guest_write32(0x00A25884, 0x40000);
        guest_write32(0x00A25890, 1);
    }
    g_opt_unlock_fps = strcmp(argv[2], "unlocked") == 0;
    frozen_clock = strcmp(argv[2], "frozen") == 0;
    lagged_clock = strcmp(argv[2], "lagged") == 0;
    uint8_t before[0x400];
    memcpy(before, vm_base + hero, sizeof before);
    try { frame_clock(nullptr); } catch (ClockDone&) {}
    if (flip_sequence) {
        printf("RESULT %u %u %02X %02X\n", requested_flips, presents,
               presented_batches[0], presented_batches[1]);
        return 0;
    }
    printf("RESULT %u %u %u %u %08X %08X %u\n", vblanks, flips, drains,
           memcmp(before, vm_base + hero, sizeof before) == 0,
           guest_read32(hero + 0x344), guest_read32(hero + 0x374), one_ms_sleeps);
    return 0;
}
'''


@unittest.skipUnless(os.name == "nt", "CPU fixture uses Windows clang-cl")
class NativeSprintSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        candidate = ROOT / "tools/llvm/bin/clang-cl.exe"
        compiler = (os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
                    or (str(candidate) if candidate.is_file() else None))
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.temporary = tempfile.TemporaryDirectory(prefix="i2-native-safety-")
        cls.addClassCleanup(cls.temporary.cleanup)
        directory = Path(cls.temporary.name)
        source = (ROOT / "src/port/main.cpp").read_text(encoding="utf-8")
        # Compile unchanged production bodies, including any poll the real
        # clock calls. A deleted physics hook needs no permanent test stub.
        polls = source[source.index("static inline void guest_write32("):
                       source.index("static void headless_draw_arrays(")]
        configure = source[source.index('    const char* env_skip_intro = getenv('):
                           source.index("    if (opt_mesh_trim) {")]
        clock = function_body(source, "static DWORD WINAPI frame_clock(")
        fixture = (PREAMBLE + polls + "\nstatic void configure_input(int argc, char** argv) {\n"
                   + configure + "\n}\n" + clock + HARNESS)
        probe = directory / "safety.cpp"
        probe.write_text(fixture, encoding="utf-8")
        cls.executable = directory / "safety.exe"
        result = subprocess.run(
            [compiler, "/nologo", "/std:c++17", "/EHsc", "/O1",
             "/D_CRT_SECURE_NO_WARNINGS", "/clang:-fuse-ld=lld", str(probe),
             "/Fe:" + str(cls.executable), "/Fo:" + str(directory / "safety.obj")],
            cwd=directory, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError("CPU fixture compilation failed:\n" + result.stdout + result.stderr)

    def probe(self, *arguments, **environment):
        env = {name: value for name, value in os.environ.items()
               if not name.startswith("I2_")}
        env.update(environment)
        result = subprocess.run([str(self.executable), *arguments], env=env,
                                capture_output=True, text=True, timeout=5, check=True)
        line = next(line for line in result.stdout.splitlines() if line.startswith("RESULT "))
        return line.split()[1:]

    def test_default_configuration_keeps_menu_input_neutral(self):
        # An enabled startup default would inject START/CROSS after 500 ms.
        self.assertEqual(self.probe("configure"), ["0", "0", "0", "0"])

    def test_default_poller_keeps_menu_input_neutral(self):
        # Protect the poller too when called without main's normalization.
        self.assertEqual(self.probe("poll"), ["0", "0", "0", "0", "0", "0"])

    def test_explicit_input_activation_reaches_pulse_sink(self):
        for arguments, environment in [
            (("configure", "--auto-start"), {}),
            (("configure",), {"I2_AUTO_START": "1"}),
            (("configure",), {"I2_AUTO_NEWGAME": "1"}),
        ]:
            with self.subTest(arguments=arguments, environment=environment):
                self.assertEqual(self.probe(*arguments, **environment), ["1", "0", "16392", "30"])

    def test_explicit_disable_overrides_environment(self):
        self.assertEqual(self.probe("configure", "--no-auto-start", I2_AUTO_START="1"),
                         ["0", "0", "0", "0"])

    def test_explicit_poller_waits_until_debounce_boundary(self):
        self.assertEqual(self.probe("poll", I2_AUTO_START="1"),
                         ["0", "0", "1", "0", "16392", "30"])

    def test_frame_clock_preserves_guest_hero_bytes(self):
        # A host floor/velocity/flag write corrupts this guest-owned object.
        for mode, velocity in [("normal", "C0800000"), ("rising", "40800000")]:
            with self.subTest(mode=mode):
                result = self.probe("clock", mode)
                self.assertEqual(result[3:6], ["1", "3FA00000", velocity])

    def test_fifo_pacing_experiment_is_opt_in(self):
        self.assertEqual(self.probe("clock", "unknown")[-1], "0")
        result = self.probe("clock", "unknown", I2_FIFO_BACKOFF_MS="1")
        self.assertEqual(result[:2], ["3", "3"])
        self.assertEqual(result[-1], "6")

    def test_fifo_bursts_do_not_advance_vblank_or_flip_time(self):
        # Three wall-clock deadlines occur at 1000, 1016, and 1032 ms.
        for mode in ["normal", "kicker", "fence", "unlocked", "boot", "unknown"]:
            with self.subTest(mode=mode):
                self.assertEqual(self.probe("clock", mode)[:2], ["3", "3"])

    def test_busy_kicker_can_drain_without_elapsed_guest_time(self):
        # Thirty-two outer iterations at one timestamp still fire one tick.
        result = self.probe("clock", "frozen")
        self.assertEqual(result[:2], ["1", "1"])
        self.assertGreater(int(result[2]), 64, "Busy FIFO must retain its burst drain")

    def test_busy_burst_presents_each_completed_batch_before_next_frame(self):
        # Delaying pending-flip consumption merges A and B into 0x33.
        self.assertEqual(self.probe("clock", "busy-flips"), ["2", "2", "11", "22"])

    def test_busy_burst_preserves_batch_already_pending_on_entry(self):
        self.assertEqual(self.probe("clock", "pending-before-burst"),
                         ["2", "2", "11", "22"])

    def test_catchup_limit_does_not_refire_at_same_timestamp(self):
        # Initial tick plus 240 capped catch-up ticks after a 4-second pause.
        self.assertEqual(self.probe("clock", "lagged")[:2], ["241", "241"])


if __name__ == "__main__":
    unittest.main()
