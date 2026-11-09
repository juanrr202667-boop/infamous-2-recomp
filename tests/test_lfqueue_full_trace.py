"""Exercise diagnostics against the native LFQueue fixture."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "build/windows-tests/runner-tests/lfqueue_probe.exe"


@pytest.mark.skipif(not PROBE.is_file(), reason="native LFQueue fixture not built")
def test_full_queue_reports_guest_counters_only_when_enabled():
    env = os.environ.copy()
    env["I2_LFQUEUE_TRACE_FULL"] = "1"
    result = subprocess.run([str(PROBE), "full"], env=env, capture_output=True,
                            text=True, timeout=5, check=True)
    assert "[i2-lfqueue-full] queue=00011000 completed=0 ticket=12 depth=12" in result.stderr
    env.pop("I2_LFQUEUE_TRACE_FULL")
    result = subprocess.run([str(PROBE), "full"], env=env, capture_output=True,
                            text=True, timeout=5, check=True)
    assert "[i2-lfqueue-full]" not in result.stderr


@pytest.mark.skipif(not PROBE.is_file(), reason="native LFQueue fixture not built")
def test_push_trace_reports_queue_state_before_notification():
    env = os.environ.copy()
    env["I2_LFQUEUE_TRACE_PUSH"] = "1"
    result = subprocess.run([str(PROBE), "push"], env=env, capture_output=True,
                            text=True, timeout=5, check=True)
    assert "[i2-lfqueue-state] queue=00011000 ticket=0 completed=0 pop_reserved=0 waiter=0000 wake=0" in result.stderr
