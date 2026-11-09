"""Coordinate capture batches using acknowledgements from the RSX backend."""

from pathlib import Path
import re


class CapturePhases:
    def __init__(self, trigger: Path, batch_size: int):
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.trigger = Path(trigger)
        self.batch_size = batch_size
        self.state = "ready"
        self.count = 0

    def arm(self, phase: str) -> None:
        if self.state != "ready":
            raise RuntimeError("capture gate is not ready")
        if not phase or "\n" in phase or "\r" in phase:
            raise ValueError("invalid phase")
        self.trigger.write_text(phase + "\n", encoding="utf-8")
        self.count = 0
        self.state = "capturing"

    def observe(self, line: str) -> None:
        if self.state == "capturing" and re.match(r"^\[RSXCAP\] frame \d+\s", line):
            self.count += 1
            if self.count == self.batch_size:
                self.trigger.unlink()
                self.state = "waiting"
        elif self.state == "waiting" and line.strip() == "[RSXCAP] rearm-ready":
            self.state = "ready"
