"""Collect log observations without claiming that a playable menu exists."""

from __future__ import annotations

from collections import deque
import re


_STATE_WRITE = re.compile(
    r"\[ww\]\s+(0x[0-9A-Fa-f]+)\s+<-\s+(0x[0-9A-Fa-f]+)\s+\(w(\d+)\)"
)
_INTRO_OPEN = re.compile(
    r"\[(?:fs|sys_fs)\]\s+open\s+['\"](?:[^'\"\r\n]*/)?"
    r"v_00_intro_montage_g\.bik['\"]\s+->\s+fd\s+\d+\b"
)
_BINK_MESSAGE = re.compile(
    r"\[(i2-bink-buffer|i2-bink-sem)\](.*?)"
    r"(?=\[i2-bink-(?:buffer|sem)\]|$)"
)
_BINK_FIELDS = re.compile(
    r"(?<!\w)(gen|id|final|h_C|valid_mask)=((?:0[xX])?[0-9A-Fa-f]+)(?!\w)"
)
_RSX_CAPTURE = re.compile(r"\[RSXCAP\]\s+frame\s+(\d+)(?=\s|$)")


class MenuWitness:
    """Incrementally retain observations from one native process log.

    State values come from write-watch reports emitted before the store. They
    do not establish a committed memory value or a completed guest call.
    Elapsed times are supplied by the log reader, not parsed from guest logs.
    """

    def __init__(self) -> None:
        self.intro_opened = False
        self.intro_opened_elapsed: float | None = None
        self.intro_eof_observed = False
        self.intro_eof_elapsed: float | None = None
        self.intro_teardown_observed = False
        self.intro_teardown_elapsed: float | None = None
        self.state2_write_observed = False
        self.state2_write_elapsed: float | None = None
        self.last_state: int | None = None
        self.last_intro_frame: int | None = None
        self.last_rsx_frame: int | None = None
        self.rsx_capture_count = 0
        self._evidence: deque[dict] = deque(maxlen=8)

    def feed(self, line: str, elapsed_seconds: float) -> None:
        """Consume a complete log line at the caller's elapsed wall time."""
        if _INTRO_OPEN.search(line) and not self.intro_opened:
            self.intro_opened = True
            self.intro_opened_elapsed = elapsed_seconds
            self._record("intro_opened", line, elapsed_seconds)
        for message in _BINK_MESSAGE.finditer(line):
            pairs = _BINK_FIELDS.findall(message.group(2))
            fields = dict(pairs)
            # Interleaved writers can supply conflicting keys. Such a sample
            # cannot identify a generation or lifecycle outcome reliably.
            if any(fields[key] != value for key, value in pairs):
                continue
            if fields.get("gen") != "2":
                continue
            if message.group(1) == "i2-bink-sem":
                # A final lifecycle sample may precede a buffered frame log.
                # It does not by itself establish that the movie reached EOF.
                if fields.get("final") == "1" and not self.intro_teardown_observed:
                    self.intro_teardown_observed = True
                    self.intro_teardown_elapsed = elapsed_seconds
                    self._record("intro_teardown", line, elapsed_seconds)
                continue
            if fields.get("id") != "6" or "h_C" not in fields:
                continue
            if "valid_mask" in fields and not int(fields["valid_mask"], 16) & 1:
                continue
            self.last_intro_frame = int(fields["h_C"], 16)
            event = "intro_frame"
            if self.last_intro_frame >= 0xCC9 and not self.intro_eof_observed:
                self.intro_eof_observed = True
                self.intro_eof_elapsed = elapsed_seconds
                event = "intro_eof"
            self._record(event, line, elapsed_seconds)
        for match in _STATE_WRITE.finditer(line):
            address, value, width = match.groups()
            addr_val = int(address, 16)
            if (addr_val != 0x00ABFE50 and addr_val != 0x00AC003C) or width != "4":
                continue
            self.last_state = int(value, 16)
            if self.last_state == 2:
                if not self.state2_write_observed:
                    self.state2_write_elapsed = elapsed_seconds
                self.state2_write_observed = True
                self._record("state2_write", line, elapsed_seconds)
        for match in _RSX_CAPTURE.finditer(line):
            self.last_rsx_frame = int(match.group(1))
            self.rsx_capture_count += 1
            self._record("rsx_capture", line, elapsed_seconds)

    def _record(self, event: str, line: str, elapsed_seconds: float) -> None:
        self._evidence.append({
            "event": event, "elapsed_seconds": elapsed_seconds,
            "line": line.rstrip("\r\n"),
        })

    def as_dict(self) -> dict:
        """Return a snapshot; ``last_state`` is a pre-store hook report.

        Neither EOF, teardown nor a reported state write verifies playable UI.
        Graphics errors belong to the existing boot summary and are not
        counted here.
        """
        return {
            "intro_opened": self.intro_opened,
            "intro_opened_elapsed": self.intro_opened_elapsed,
            "intro_eof_observed": self.intro_eof_observed,
            "intro_eof_elapsed": self.intro_eof_elapsed,
            "intro_teardown_observed": self.intro_teardown_observed,
            "intro_teardown_elapsed": self.intro_teardown_elapsed,
            "state2_write_observed": self.state2_write_observed,
            "state2_write_elapsed": self.state2_write_elapsed,
            "last_state": self.last_state,
            "state_write_observation_kind": "write_watch_report_before_store",
            "last_intro_frame": self.last_intro_frame,
            "last_rsx_frame": self.last_rsx_frame,
            "rsx_capture_count": self.rsx_capture_count,
            "evidence": [item.copy() for item in self._evidence],
            "gameplay_verified": False,
        }
