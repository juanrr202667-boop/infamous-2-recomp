"""Local dashboard API contracts; no native process or GPU is started."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/live_dashboard.py"
dashboard = None
if SOURCE.is_file():
    spec = importlib.util.spec_from_file_location("live_dashboard", SOURCE)
    dashboard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dashboard)

RUN = "20261015T195821Z-8955fa"


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(dashboard, "local read-only dashboard API not implemented")
        temporary = tempfile.TemporaryDirectory(prefix="i2-dashboard-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = self.root / "build/game/menu-witness" / RUN
        self.frame = self.run / "rsx/frame_07"
        self.frame.mkdir(parents=True)
        self.coordination = self.root / "build/coordination"
        (self.coordination / "codex").mkdir(parents=True)

    def write_status(self, **changes):
        status = {"running": False, "duration_seconds": 600.156, "time_limit_seconds": 600,
                  "pid": os.getpid(), "timed_out": True,
                  "menu_witness": {"intro_eof_observed": True, "last_intro_frame": 3273,
                                   "rsx_capture_count": 8, "gameplay_verified": False},
                  "executable": {"path": "C:/private/EBOOT.ELF"}, "secret": "do-not-expose"}
        status.update(changes)
        (self.run / "status.json").write_text(json.dumps(status), encoding="utf-8")

    def bmp(self, path=None):
        path = path or self.frame / "backbuffer.bmp"
        data = struct.pack("<2sIHHIIIIHHIIIIII", b"BM", 58, 0, 0, 54, 40, 1, 1,
                           1, 24, 0, 4, 0, 0, 0, 0) + b"\x10\x20\x30\x00"
        path.write_bytes(data)
        return data

    def start(self):
        self.server = dashboard.create_server(self.root, 0)
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = "http://127.0.0.1:" + str(self.server.server_address[1])

    def get(self, path):
        return urlopen(self.url + path, timeout=3)

    def test_status_is_projected_and_labels_old_collection(self):
        self.write_status()
        self.bmp()
        old = time.time() - 3600
        os.utime(self.run / "status.json", (old, old))
        os.utime(self.frame / "backbuffer.bmp", (old, old))
        self.start()
        with self.get("/api/status") as response:
            data = json.load(response)
        self.assertFalse(data["run"]["active"])
        self.assertIn("ended", data["run"]["activity_label"])
        self.assertTrue(data["image"]["stale"])
        self.assertIn("Previous", data["image"]["label"])
        self.assertIn("continuous", data["image"]["notice"])
        self.assertEqual(data["run"]["duration_seconds"], 600.156)
        self.assertEqual(data["run"]["intro"]["last_intro_frame"], 3273)
        self.assertNotIn("private", json.dumps(data))
        self.assertNotIn("do-not-expose", json.dumps(data))

    def test_fresh_status_and_live_pid_confirm_collection(self):
        self.write_status(running=True, timed_out=False)
        self.start()
        with self.get("/api/status") as response:
            data = json.load(response)
        self.assertTrue(data["run"]["active"])
        self.assertIn("Active", data["run"]["activity_label"])

    def test_running_flag_with_old_status_never_claims_active(self):
        self.write_status(running=True)
        old = time.time() - 120
        os.utime(self.run / "status.json", (old, old))
        self.start()
        with self.get("/api/status") as response:
            data = json.load(response)
        self.assertFalse(data["run"]["active"])
        self.assertIn("stale", data["run"]["activity_label"])

    def test_root_stage_and_latest_real_test_are_visible(self):
        self.write_status()
        (self.coordination / "live-status.json").write_text(json.dumps({
            "stage": "Integrating temporal views", "detail": "Preparing validation", "candidate": 49,
            "updated_at": "2026-10-15T21:30:00Z", "private_path": "C:/secret/file"}), encoding="utf-8")
        log = self.coordination / "codex/CX-024-final-pytest.log"
        log.write_text("........\n62 passed in 7.02s\n", encoding="utf-8")
        self.start()
        with self.get("/api/status") as response:
            data = json.load(response)
        self.assertEqual(data["stage"]["title"], "Integrating temporal views")
        self.assertEqual(data["stage"]["candidate"], 49)
        self.assertEqual(data["gate"]["result"], "passed")
        self.assertIn("62 passed", data["gate"]["summary"])
        self.assertNotIn("private_path", json.dumps(data))

    def test_only_newest_run_is_shown_even_before_its_first_image(self):
        self.write_status()
        self.bmp()
        newer = self.run.parent / "20261015T205821Z-aabbcc"
        newer.mkdir()
        (newer / "status.json").write_text('{"running": false}', encoding="utf-8")
        self.start()
        with self.get("/api/status") as response:
            data = json.load(response)
        self.assertEqual(data["run"]["id"], newer.name)
        self.assertIsNone(data["image"])

    def test_log_tail_is_bounded_and_sanitized(self):
        self.write_status()
        (self.run / "game.log").write_bytes(b"OLD-NOT-READ\n" * 100000 +
            b"\x1b[31mfinal token=SUPERSECRET\x1b[0m\x00 C:\\private\\asset.bin\n")
        (self.run / "mix.f32").write_bytes(b"PCM-MUST-NEVER-BE-SERVED")
        self.start()
        with self.get("/api/status") as response:
            data = json.load(response)
        text = data["log"]["text"]
        self.assertLessEqual(len(text), 17000)
        self.assertNotIn("SUPERSECRET", text)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x00", text)
        self.assertNotIn("C:\\private", text)
        self.assertNotIn("PCM-MUST", json.dumps(data))
        self.assertTrue(data["log"]["truncated"])

    def test_image_allowlist_headers_and_version(self):
        self.write_status()
        expected = self.bmp()
        self.start()
        with self.get("/api/status") as response:
            image = json.load(response)["image"]
        with self.get(image["url"]) as response:
            self.assertEqual(response.headers["Content-Type"], "image/bmp")
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(response.read(), expected)
            etag = response.headers["ETag"]
        request = Request(self.url + image["url"], headers={"If-None-Match": etag})
        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=3)
        self.assertEqual(raised.exception.code, 304)

    def test_traversal_assets_pcm_and_originals_are_never_served(self):
        self.write_status()
        self.bmp()
        (self.frame / "asset.bin").write_bytes(b"secret")
        self.start()
        for path in ("/../AGENTS.md", "/%2e%2e/AGENTS.md", "/api/image/../../EBOOT.ELF",
                     "/api/image/%252e%252e/file.bmp", "/build/game/vfs/asset.bin", "/mix.f32",
                     f"/api/image/{RUN}/frame_07/asset.bin",
                     f"/api/image/{RUN}/frame_07/%2e%2e%5cstatus.json"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as raised:
                self.get(path)
            self.assertIn(raised.exception.code, (400, 404))

    def test_symlink_image_escape_is_rejected(self):
        self.write_status()
        outside = self.root / "outside.bmp"
        self.bmp(outside)
        try:
            (self.frame / "backbuffer.bmp").symlink_to(outside)
        except OSError:
            self.skipTest("symlink privilege unavailable")
        self.start()
        with self.assertRaises(HTTPError) as raised:
            self.get(f"/api/image/{RUN}/frame_07/backbuffer.bmp")
        self.assertEqual(raised.exception.code, 404)

    def test_ui_has_local_csp_and_no_external_requests(self):
        self.start()
        with self.get("/") as response:
            html = response.read().decode("utf-8")
            csp = response.headers["Content-Security-Policy"]
        self.assertIn("default-src 'none'", csp)
        self.assertIn("connect-src 'self'", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertIn('lang="en"', html)
        self.assertNotIn("https://", html)
        self.assertNotIn("http://", html)

    def test_partial_json_is_reported_without_breaking_api(self):
        (self.run / "status.json").write_text('{"running":', encoding="utf-8")
        self.start()
        with self.get("/api/status") as response:
            data = json.load(response)
        self.assertFalse(data["run"]["active"])
        self.assertIn("unavailable", data["run"]["activity_label"])


if __name__ == "__main__":
    unittest.main()
