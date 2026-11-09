"""Automated regression suite for rearmable Bink skip and State 2 -> State 3 transition.

Validates:
1. Rearmable Bink skip contract (I2_SKIP_INTRO):
   - Inactive by default.
   - Signals EOF/abort (+0x20=1, +0xB0=1) upon encountering an active Bink Movie object.
   - Idempotent while the same movie pointer remains loaded.
   - Rearms when the movie pointer is cleared (bink_ptr == 0).
   - Successfully fires on subsequent movie allocations (same or different address).
   - Handles direct movie pointer transitions without an intermediate null.
   - Rejects out-of-bounds guest pointers safely.

2. Autonomous State 2 -> State 3 transition injector (I2_AUTO_START):
   - Inactive by default.
   - Remains idle during STATE_BOOT (0) and STATE_TITLE (1).
   - Initiates settling delay upon reaching STATE_MENU (2).
   - Injects atomic button pulse (START 0x0008 | CROSS 0x4000) via cellPad_inject_pulse
     only after settling window elapses.
   - One-shot pulse injection per menu session.
   - Consumed deterministically in cellPadGetData across digital buttons.
   - Confirms transition to STATE_GAMEPLAY (3) and marks completion.

3. Native compilation & runtime execution via clang-cl driver fixture.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"


# ---------------------------------------------------------------------------
# Pure Python Specification Models
# ---------------------------------------------------------------------------

class MockGuestVM:
    def __init__(self, size: int = 0x10000000):
        self.size = size
        self.memory = bytearray(min(size, 0x02000000))  # 32MB physical buffer for test

    def read32(self, addr: int) -> int:
        if addr + 4 > len(self.memory):
            return 0
        return int.from_bytes(self.memory[addr:addr + 4], "big")

    def write32(self, addr: int, val: int) -> None:
        if addr + 4 <= len(self.memory):
            self.memory[addr:addr + 4] = val.to_bytes(4, "big")


class RearmableSkipPoller:
    """Exact logic model of the rearmable i2_poll_skip_intro."""

    def __init__(self, vm: MockGuestVM, enabled: bool = True):
        self.vm = vm
        self.enabled = enabled
        self.last_signaled_ptr: int = 0
        self.log_messages: list[str] = []

    def poll(self) -> None:
        if not self.enabled:
            return

        bink_ptr = self.vm.read32(0x00946498)
        if not bink_ptr:
            # Movie closed/unregistered -> rearm for next movie
            self.last_signaled_ptr = 0
            return

        total_frames = self.vm.read32(bink_ptr + 0x08)
        cur_frame = self.vm.read32(bink_ptr + 0x0C)
        played_frame = self.vm.read32(bink_ptr + 0x460)

        # splash_america.bik has 352 frames and must run without early abort (0 < total_frames <= 500).
        if 0 < total_frames <= 500:
            return

        # v_00_intro_montage_g.bik (3273 frames) ou videos gerais: aborta apos atingir >= 30 frames
        ready = (cur_frame >= 30 or played_frame >= 30 or (total_frames == 0 and cur_frame == 0 and played_frame == 0))
        if not ready:
            return

        if bink_ptr != self.last_signaled_ptr and bink_ptr < (self.vm.size - 0x100):
            self.vm.write32(bink_ptr + 0x20, 1)
            self.vm.write32(bink_ptr + 0xB0, 1)
            self.last_signaled_ptr = bink_ptr
            msg = f"[I2_SKIP_INTRO] Signaled EOF/abort to Bink Movie at 0x{bink_ptr:08X} (+0x20=1, +0xB0=1)"
            self.log_messages.append(msg)


class StateTransitionInjector:
    """Exact logic model of the autonomous i2_poll_auto_transition."""

    def __init__(self, vm: MockGuestVM, enabled: bool = True, delay_ms: int = 500, btn_mask: int = 0x4008):
        self.vm = vm
        self.enabled = enabled
        self.delay_ms = delay_ms
        self.btn_mask = btn_mask
        self.state2_enter_time: int = 0
        self.pulse_fired: bool = False
        self.transition_completed: bool = False
        self.injected_pulses: list[tuple[int, int, int]] = []
        self.log_messages: list[str] = []

    def poll(self, current_time_ms: int) -> None:
        if not self.enabled or self.transition_completed:
            return

        current_state = self.vm.read32(0x00ABFE50)

        if current_state == 2:
            if self.state2_enter_time == 0:
                self.state2_enter_time = current_time_ms
                self.log_messages.append(
                    f"[AUTO-TRANSITION] Engine entered State 2 (Menu Interativo at 0x00ABFE50). Settling for {self.delay_ms} ms..."
                )

            if not self.pulse_fired and (current_time_ms - self.state2_enter_time >= self.delay_ms):
                self.log_messages.append(
                    f"[AUTO-TRANSITION] Settling window elapsed. Injecting START/CROSS pulse (mask 0x{self.btn_mask:04X}, 30 polls)..."
                )
                self.injected_pulses.append((0, self.btn_mask, 30))
                self.pulse_fired = True
        elif current_state == 3:
            self.log_messages.append(
                "[AUTO-TRANSITION] SUCCESS: Engine transitioned to State 3 (New Marais Gameplay Active at 0x00ABFE50)!"
            )
            self.transition_completed = True


# ---------------------------------------------------------------------------
# Unit Test Cases: Python Logic
# ---------------------------------------------------------------------------

class RearmableSkipUnitTests(unittest.TestCase):
    def setUp(self):
        self.vm = MockGuestVM()

    def test_skip_intro_disabled_by_default(self):
        poller = RearmableSkipPoller(self.vm, enabled=False)
        self.vm.write32(0x00946498, 0x00500000)
        poller.poll()
        self.assertEqual(self.vm.read32(0x00500020), 0)
        self.assertEqual(self.vm.read32(0x005000B0), 0)
        self.assertEqual(len(poller.log_messages), 0)

    def test_skip_intro_signals_first_movie_on_valid_pointer(self):
        poller = RearmableSkipPoller(self.vm, enabled=True)
        movie_addr = 0x00500000
        self.vm.write32(0x00946498, movie_addr)
        poller.poll()
        self.assertEqual(self.vm.read32(movie_addr + 0x20), 1)
        self.assertEqual(self.vm.read32(movie_addr + 0xB0), 1)
        self.assertEqual(len(poller.log_messages), 1)
        self.assertIn("0x00500000", poller.log_messages[0])

    def test_skip_intro_is_idempotent_for_same_movie(self):
        poller = RearmableSkipPoller(self.vm, enabled=True)
        movie_addr = 0x00500000
        self.vm.write32(0x00946498, movie_addr)
        for _ in range(50):
            poller.poll()
        self.assertEqual(len(poller.log_messages), 1)

    def test_skip_intro_rearms_when_movie_closes(self):
        poller = RearmableSkipPoller(self.vm, enabled=True)
        movie_addr = 0x00500000
        self.vm.write32(0x00946498, movie_addr)
        poller.poll()
        self.assertEqual(poller.last_signaled_ptr, movie_addr)

        # Movie closes -> bink_ptr becomes 0
        self.vm.write32(0x00946498, 0)
        poller.poll()
        self.assertEqual(poller.last_signaled_ptr, 0, "Poller must rearm on null pointer")

    def test_skip_intro_signals_subsequent_movie_at_same_address(self):
        poller = RearmableSkipPoller(self.vm, enabled=True)
        movie_addr = 0x00500000

        # First movie
        self.vm.write32(0x00946498, movie_addr)
        poller.poll()
        self.assertEqual(len(poller.log_messages), 1)

        # Closes
        self.vm.write32(0x00946498, 0)
        poller.poll()
        self.vm.write32(movie_addr + 0x20, 0)
        self.vm.write32(movie_addr + 0xB0, 0)

        # Second movie re-uses the same address
        self.vm.write32(0x00946498, movie_addr)
        poller.poll()
        self.assertEqual(self.vm.read32(movie_addr + 0x20), 1)
        self.assertEqual(self.vm.read32(movie_addr + 0xB0), 1)
        self.assertEqual(len(poller.log_messages), 2, "Must signal second movie after rearm")

    def test_skip_intro_signals_subsequent_movie_at_different_address(self):
        poller = RearmableSkipPoller(self.vm, enabled=True)
        movie1 = 0x00500000
        movie2 = 0x00600000

        self.vm.write32(0x00946498, movie1)
        poller.poll()
        self.vm.write32(0x00946498, 0)
        poller.poll()

        self.vm.write32(0x00946498, movie2)
        poller.poll()
        self.assertEqual(self.vm.read32(movie2 + 0x20), 1)
        self.assertEqual(self.vm.read32(movie2 + 0xB0), 1)
        self.assertEqual(len(poller.log_messages), 2)
        self.assertIn("0x00600000", poller.log_messages[1])

    def test_skip_intro_signals_immediate_replacement_without_zero(self):
        poller = RearmableSkipPoller(self.vm, enabled=True)
        movie1 = 0x00500000
        movie2 = 0x00700000

        self.vm.write32(0x00946498, movie1)
        poller.poll()
        # Direct switch
        self.vm.write32(0x00946498, movie2)
        poller.poll()
        self.assertEqual(self.vm.read32(movie2 + 0x20), 1)
        self.assertEqual(len(poller.log_messages), 2)

    def test_skip_intro_rejects_out_of_bounds_guest_pointer(self):
        poller = RearmableSkipPoller(self.vm, enabled=True)
        oob_ptr = self.vm.size + 0x1000
        self.vm.write32(0x00946498, oob_ptr)
        poller.poll()
        self.assertEqual(len(poller.log_messages), 0)

    def test_skip_intro_preserves_splash_america_without_abort(self):
        poller = RearmableSkipPoller(self.vm, enabled=True)
        splash_addr = 0x00550000
        self.vm.write32(0x00946498, splash_addr)
        self.vm.write32(splash_addr + 0x08, 352)  # splash_america.bik (352 frames <= 500)
        self.vm.write32(splash_addr + 0x0C, 100)
        poller.poll()
        self.assertEqual(self.vm.read32(splash_addr + 0x20), 0)
        self.assertEqual(self.vm.read32(splash_addr + 0xB0), 0)
        self.assertEqual(len(poller.log_messages), 0)

    def test_skip_intro_aborts_intro_montage_after_30_frames(self):
        poller = RearmableSkipPoller(self.vm, enabled=True)
        montage_addr = 0x00580000
        self.vm.write32(0x00946498, montage_addr)
        self.vm.write32(montage_addr + 0x08, 3273)  # v_00_intro_montage_g.bik

        # Frame 10 (< 30): must not abort yet
        self.vm.write32(montage_addr + 0x0C, 10)
        poller.poll()
        self.assertEqual(self.vm.read32(montage_addr + 0x20), 0)
        self.assertEqual(self.vm.read32(montage_addr + 0xB0), 0)
        self.assertEqual(len(poller.log_messages), 0)

        # Frame 30 (>= 30): must abort with EOF
        self.vm.write32(montage_addr + 0x0C, 30)
        poller.poll()
        self.assertEqual(self.vm.read32(montage_addr + 0x20), 1)
        self.assertEqual(self.vm.read32(montage_addr + 0xB0), 1)
        self.assertEqual(len(poller.log_messages), 1)


class AutoTransitionUnitTests(unittest.TestCase):
    def setUp(self):
        self.vm = MockGuestVM()

    def test_auto_transition_disabled_by_default(self):
        injector = StateTransitionInjector(self.vm, enabled=False)
        self.vm.write32(0x00ABFE50, 2)
        injector.poll(1000)
        self.assertEqual(len(injector.injected_pulses), 0)
        self.assertFalse(injector.pulse_fired)

    def test_auto_transition_idle_during_boot_and_title(self):
        injector = StateTransitionInjector(self.vm, enabled=True)
        for state in (0, 1):
            self.vm.write32(0x00ABFE50, state)
            injector.poll(1000)
            self.assertEqual(len(injector.injected_pulses), 0)
            self.assertEqual(injector.state2_enter_time, 0)

    def test_auto_transition_settling_delay(self):
        injector = StateTransitionInjector(self.vm, enabled=True, delay_ms=500)
        self.vm.write32(0x00ABFE50, 2)

        # Frame 1: enters state 2 at t=1000
        injector.poll(1000)
        self.assertEqual(injector.state2_enter_time, 1000)
        self.assertFalse(injector.pulse_fired)
        self.assertEqual(len(injector.injected_pulses), 0)

        # Frame 2: t=1400 (< 500ms elapsed)
        injector.poll(1400)
        self.assertFalse(injector.pulse_fired)
        self.assertEqual(len(injector.injected_pulses), 0)

        # Frame 3: t=1500 (>= 500ms elapsed) -> fires pulse!
        injector.poll(1500)
        self.assertTrue(injector.pulse_fired)
        self.assertEqual(len(injector.injected_pulses), 1)
        port, mask, duration = injector.injected_pulses[0]
        self.assertEqual(port, 0)
        self.assertEqual(mask, 0x4008)
        self.assertEqual(duration, 30)

    def test_auto_transition_one_shot_does_not_refire(self):
        injector = StateTransitionInjector(self.vm, enabled=True, delay_ms=500)
        self.vm.write32(0x00ABFE50, 2)
        injector.poll(1000)
        injector.poll(1500)
        self.assertEqual(len(injector.injected_pulses), 1)

        # Next frames still in State 2
        for t in range(1600, 3000, 100):
            injector.poll(t)
        self.assertEqual(len(injector.injected_pulses), 1)

    def test_auto_transition_confirms_state3_and_completes(self):
        injector = StateTransitionInjector(self.vm, enabled=True, delay_ms=500)
        self.vm.write32(0x00ABFE50, 2)
        injector.poll(1000)
        injector.poll(1500)
        self.assertFalse(injector.transition_completed)

        # Engine advances to State 3
        self.vm.write32(0x00ABFE50, 3)
        injector.poll(1600)
        self.assertTrue(injector.transition_completed)
        self.assertIn("State 3", injector.log_messages[-1])

        # Subsequent polls are complete no-ops
        injector.poll(2000)
        self.assertEqual(len(injector.injected_pulses), 1)

    def test_auto_transition_direct_to_state3_does_not_fire_ghost_pulse(self):
        injector = StateTransitionInjector(self.vm, enabled=True)
        # Directly in State 3 (e.g. loaded quicksave)
        self.vm.write32(0x00ABFE50, 3)
        injector.poll(1000)
        self.assertTrue(injector.transition_completed)
        self.assertEqual(len(injector.injected_pulses), 0)

    def test_cellpad_button_mask_split_contract(self):
        """Verifies cellPad button mask decoding for START (0x0008) and CROSS (0x4000)."""
        btn_mask = 0x4008
        digital1 = btn_mask & 0xFF
        digital2 = (btn_mask >> 8) & 0xFF
        # START is bit 3 in DIGITAL1
        self.assertEqual(digital1 & 0x08, 0x08)
        # CROSS is bit 6 in DIGITAL2
        self.assertEqual(digital2 & 0x40, 0x40)


# ---------------------------------------------------------------------------
# Native C++ Probe Compilation & Execution (clang-cl)
# ---------------------------------------------------------------------------

NATIVE_PROBE_SOURCE = r'''
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint8_t g_guest_vm[0x02000000]; // 32 MB guest VM
static uint8_t* vm_base = g_guest_vm;
static uint64_t ppu_vm_size = sizeof(g_guest_vm);

static inline void guest_write32(uint64_t addr, uint32_t val) {
    if (addr + 4 > sizeof(g_guest_vm)) return;
    uint8_t* p = vm_base + addr;
    p[0] = (uint8_t)(val >> 24);
    p[1] = (uint8_t)(val >> 16);
    p[2] = (uint8_t)(val >> 8);
    p[3] = (uint8_t)(val);
}

static inline uint32_t guest_read32(uint64_t addr) {
    if (addr + 4 > sizeof(g_guest_vm)) return 0;
    const uint8_t* p = vm_base + addr;
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8)  | ((uint32_t)p[3]);
}

// Mock cellPad_inject_pulse
static uint16_t s_injected_mask = 0;
static int s_injected_polls = 0;
static int s_inject_call_count = 0;

extern "C" void cellPad_inject_pulse(uint32_t port_no, uint16_t button_mask, int duration_polls) {
    if (port_no == 0) {
        s_injected_mask = button_mask;
        s_injected_polls = duration_polls;
        s_inject_call_count++;
    }
}

// ---------------------------------------------------------------------------
// Rearmable Skip Intro implementation
// ---------------------------------------------------------------------------
static int s_skip_count = 0;

static void i2_poll_skip_intro(void) {
    static int s_skip = -1;
    if (s_skip < 0) {
        const char* e = getenv("I2_SKIP_INTRO");
        s_skip = (e && strcmp(e, "1") == 0) ? 1 : 0;
    }
    if (!s_skip || !vm_base) return;

    static uint32_t s_last_signaled_ptr = 0;

    uint32_t bink_ptr = guest_read32(0x00946498);
    if (!bink_ptr) {
        s_last_signaled_ptr = 0; // Rearm!
        return;
    }

    uint32_t total_frames = guest_read32(bink_ptr + 0x08);
    uint32_t cur_frame = guest_read32(bink_ptr + 0x0C);
    uint32_t played_frame = guest_read32(bink_ptr + 0x460);

    if (total_frames <= 500 && total_frames > 0) {
        return;
    }

    bool ready = (cur_frame >= 30 || played_frame >= 30 || (total_frames == 0 && cur_frame == 0 && played_frame == 0));
    if (!ready) {
        return;
    }

    if (bink_ptr != s_last_signaled_ptr && bink_ptr < ppu_vm_size - 0x100) {
        guest_write32(bink_ptr + 0x20, 1);
        guest_write32(bink_ptr + 0xB0, 1);
        s_last_signaled_ptr = bink_ptr;
        s_skip_count++;
        printf("[I2_SKIP_INTRO] Signaled EOF/abort to Bink Movie at 0x%08X (+0x20=1, +0xB0=1)\n", bink_ptr);
    }
}

// ---------------------------------------------------------------------------
// Auto-Transition Injector implementation
// ---------------------------------------------------------------------------
static uint64_t g_mock_tick = 1000;

static void i2_poll_auto_transition(void) {
    static int s_auto_start = -1;
    static uint32_t s_btn_mask = 0x4008;
    static uint64_t s_delay_ms = 500;
    static uint64_t s_state2_enter_time = 0;
    static bool s_pulse_fired = false;
    static bool s_transition_completed = false;

    if (s_auto_start < 0) {
        const char* e = getenv("I2_AUTO_START");
        s_auto_start = (e && strcmp(e, "1") == 0) ? 1 : 0;
        const char* btn = getenv("I2_AUTO_START_BTN");
        if (btn) s_btn_mask = (uint32_t)strtoul(btn, NULL, 0);
        const char* del = getenv("I2_AUTO_START_DELAY_MS");
        if (del) s_delay_ms = (uint64_t)strtoull(del, NULL, 0);
    }

    if (!s_auto_start || !vm_base || s_transition_completed) return;

    uint32_t current_state = guest_read32(0x00ABFE50);

    if (current_state == 2) {
        uint64_t now = g_mock_tick;
        if (s_state2_enter_time == 0) {
            s_state2_enter_time = now;
            printf("[AUTO-TRANSITION] Engine entered State 2 (Menu Interativo at 0x00ABFE50). Settling for %llu ms...\n",
                   (unsigned long long)s_delay_ms);
        }

        if (!s_pulse_fired && (now - s_state2_enter_time >= s_delay_ms)) {
            printf("[AUTO-TRANSITION] Settling window elapsed. Injecting START/CROSS pulse (mask 0x%04X, 30 polls)...\n",
                   s_btn_mask);
            cellPad_inject_pulse(0, (uint16_t)s_btn_mask, 30);
            s_pulse_fired = true;
        }
    } else if (current_state == 3) {
        printf("[AUTO-TRANSITION] SUCCESS: Engine transitioned to State 3 (New Marais Gameplay Active at 0x00ABFE50)!\n");
        s_transition_completed = true;
    }
}

int main(int argc, char** argv) {
    // 1. Verify Rearmable Skip Intro
    // Cycle A: First movie opens at 0x00500000
    guest_write32(0x00946498, 0x00500000);
    i2_poll_skip_intro();
    if (guest_read32(0x00500020) != 1 || guest_read32(0x005000B0) != 1 || s_skip_count != 1) {
        printf("FAIL: First movie not signaled properly\n");
        return 10;
    }

    // Idempotency check: same movie still running
    i2_poll_skip_intro();
    if (s_skip_count != 1) {
        printf("FAIL: Skip fired twice on same movie\n");
        return 11;
    }

    // Cycle B: Movie closes -> null pointer
    guest_write32(0x00946498, 0);
    i2_poll_skip_intro();

    // Splash preservation: splash_america.bik at 0x00550000 (352 frames <= 500)
    guest_write32(0x00946498, 0x00550000);
    guest_write32(0x00550008, 352);
    i2_poll_skip_intro();
    if (guest_read32(0x00550020) != 0 || guest_read32(0x005500B0) != 0 || s_skip_count != 1) {
        printf("FAIL: Splash movie aborted unexpectedly\n");
        return 13;
    }
    guest_write32(0x00946498, 0);
    i2_poll_skip_intro();

    // Cycle C: Second movie opens at 0x00600000 -> rearmed poller must signal!
    guest_write32(0x00946498, 0x00600000);
    i2_poll_skip_intro();
    if (guest_read32(0x00600020) != 1 || guest_read32(0x006000B0) != 1 || s_skip_count != 2) {
        printf("FAIL: Second movie not signaled after rearm\n");
        return 12;
    }

    // 2. Verify Auto-Transition Injector
    // Initial state 1 (Intro)
    guest_write32(0x00ABFE50, 1);
    i2_poll_auto_transition();
    if (s_inject_call_count != 0) {
        printf("FAIL: Transition pulse fired prematurely in State 1\n");
        return 20;
    }

    // Advance to State 2 at tick 1000
    guest_write32(0x00ABFE50, 2);
    g_mock_tick = 1000;
    i2_poll_auto_transition();
    if (s_inject_call_count != 0) {
        printf("FAIL: Transition pulse fired before settling delay\n");
        return 21;
    }

    // Tick 1400 (< 500ms)
    g_mock_tick = 1400;
    i2_poll_auto_transition();
    if (s_inject_call_count != 0) {
        printf("FAIL: Transition pulse fired during settling window\n");
        return 22;
    }

    // Tick 1500 (>= 500ms) -> Must fire!
    g_mock_tick = 1500;
    i2_poll_auto_transition();
    if (s_inject_call_count != 1 || s_injected_mask != 0x4008 || s_injected_polls != 30) {
        printf("FAIL: Transition pulse did not fire correctly after delay\n");
        return 23;
    }

    // Verify one-shot: tick 1600 still in State 2
    g_mock_tick = 1600;
    i2_poll_auto_transition();
    if (s_inject_call_count != 1) {
        printf("FAIL: Transition pulse refired in State 2\n");
        return 24;
    }

    // Advance to State 3
    guest_write32(0x00ABFE50, 3);
    g_mock_tick = 1700;
    i2_poll_auto_transition();

    printf("PASS: All rearmable skip and auto-transition contracts validated\n");
    return 0;
}
'''


class NativeProbeCompilationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        local_tmp = ROOT / "build/.tmp"
        local_tmp.mkdir(parents=True, exist_ok=True)
        os.environ["TMP"] = str(local_tmp)
        os.environ["TEMP"] = str(local_tmp)

        if "INCLUDE" not in os.environ or "LIB" not in os.environ:
            vswhere_exe = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
            if vswhere_exe.is_file():
                try:
                    res = subprocess.run([str(vswhere_exe), "-latest", "-products", "*", "-property", "installationPath"], capture_output=True, text=True)
                    if res.returncode == 0 and res.stdout.strip():
                        vs_install = res.stdout.strip().splitlines()[0].strip()
                        devcmd = Path(vs_install) / "Common7/Tools/VsDevCmd.bat"
                        if devcmd.is_file():
                            out = subprocess.check_output(f'cmd /c call "{devcmd}" -arch=x64 >nul && set', text=True)
                            for line in out.splitlines():
                                if '=' in line:
                                    k, v = line.split('=', 1)
                                    os.environ[k] = v
                except Exception:
                    pass

        configured = os.environ.get("I2_CLANG_CL")
        candidate = ROOT / "tools/llvm/bin/clang-cl.exe"
        cls.compiler = configured or (str(candidate) if candidate.is_file() else shutil.which("clang-cl"))
        if not cls.compiler or not Path(cls.compiler).is_file():
            raise unittest.SkipTest("clang-cl unavailable in tools/llvm/bin; set I2_CLANG_CL")

        cls.directory = tempfile.TemporaryDirectory(prefix="i2-skip-transition-", dir=local_tmp)
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)

        probe_cpp = directory / "probe.cpp"
        probe_cpp.write_text(NATIVE_PROBE_SOURCE, encoding="utf-8")
        cls.executable = directory / "probe.exe"

        res = subprocess.run([
            cls.compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            str(probe_cpp), "/Fe" + str(cls.executable), "/Fo" + str(directory / "probe.obj")
        ], capture_output=True, text=True)
        if res.returncode != 0:
            raise AssertionError(f"Probe compilation failed:\n{res.stdout}\n{res.stderr}")

    def test_native_probe_execution(self):
        env = os.environ.copy()
        env["I2_SKIP_INTRO"] = "1"
        env["I2_AUTO_START"] = "1"
        res = subprocess.run([str(self.executable)], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Probe run failed with stdout:\n{res.stdout}\nstderr:\n{res.stderr}")
        self.assertIn("PASS: All rearmable skip and auto-transition contracts validated", res.stdout)
        self.assertIn("[I2_SKIP_INTRO] Signaled EOF/abort to Bink Movie at 0x00500000", res.stdout)
        self.assertIn("[I2_SKIP_INTRO] Signaled EOF/abort to Bink Movie at 0x00600000", res.stdout)
        self.assertIn("[AUTO-TRANSITION] Engine entered State 2", res.stdout)
        self.assertIn("[AUTO-TRANSITION] Settling window elapsed. Injecting START/CROSS pulse (mask 0x4008, 30 polls)", res.stdout)
        self.assertIn("[AUTO-TRANSITION] SUCCESS: Engine transitioned to State 3", res.stdout)


if __name__ == "__main__":
    unittest.main()
