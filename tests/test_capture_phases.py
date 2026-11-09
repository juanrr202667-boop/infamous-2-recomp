from pathlib import Path

import pytest


def test_phase_waits_for_backend_ack_before_rearming(tmp_path):
    from scripts.capture_phases import CapturePhases

    trigger = tmp_path / "capture.ready"
    phases = CapturePhases(trigger, batch_size=2)
    phases.arm("title")
    assert trigger.read_text() == "title\n"
    phases.observe("[RSXCAP] frame 1 -> frame_00")
    assert trigger.exists()
    phases.observe("[RSXCAP] frame 2 -> frame_01")
    assert not trigger.exists()
    with pytest.raises(RuntimeError):
        phases.arm("after-input")
    phases.observe("[RSXCAP] rearm-ready")
    phases.arm("after-input")
    assert trigger.read_text() == "after-input\n"
    phases.observe("[RSXCAP] frame 3 -> frame_02")
    phases.observe("[RSXCAP] frame 4 -> frame_03")
    assert not trigger.exists()
