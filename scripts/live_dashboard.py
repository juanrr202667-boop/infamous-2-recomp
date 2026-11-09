"""Read-only local progress and capture viewer. Never starts the native game."""
from __future__ import annotations

import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import struct
import time
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "src/diagnostics/live_dashboard.html"
RUN_NAME = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{6}")
FRAME_NAME = re.compile(r"frame_\d{2,6}")
BMP_NAME = re.compile(r"(?:backbuffer|rt_[A-Za-z0-9_-]+)\.bmp")
MAX_JSON = 131072
TAIL_BYTES = 16384
MAX_BMP = 16777216
FRESH_SECONDS = 15
ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|$))")


def clean_text(value, limit=800):
    if not isinstance(value, (str, int, float)):
        return ""
    text = ANSI.sub("", str(value))
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    text = re.sub(r"(?i)\b(token|password|secret|api[_-]?key)\s*[:=]\s*\S+", r"\1=[oculto]", text)
    text = re.sub(r"[A-Za-z]:[\\/][^\s\"'<>]+", "[local path]", text)
    return text[:limit]


def timestamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds")


def process_alive(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def selected(data, keys):
    if not isinstance(data, dict):
        return {}
    return {key: value for key in keys if (value := data.get(key)) is None or
            isinstance(value, bool) or isinstance(value, (int, float)) and math.isfinite(value)}


class Dashboard:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.witness = self.root / "build/game/menu-witness"
        self.coordination = self.root / "build/coordination"

    def allowed(self, path, base):
        try:
            resolved = path.resolve(strict=True)
            return resolved.is_relative_to(self.root) and resolved.is_relative_to(base) and resolved.is_file()
        except (OSError, ValueError):
            return False

    def read_json(self, path, base):
        if not self.allowed(path, base):
            return {}, None
        try:
            with path.open("rb") as stream:
                raw = stream.read(MAX_JSON + 1)
            if len(raw) > MAX_JSON:
                return {}, None
            data = json.loads(raw)
            return (data, path.stat().st_mtime) if isinstance(data, dict) else ({}, None)
        except (OSError, ValueError):
            return {}, None

    def tail(self, path, base):
        if not self.allowed(path, base):
            return {"text": "", "truncated": False}
        try:
            with path.open("rb") as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - TAIL_BYTES))
                raw = stream.read(TAIL_BYTES)
            lines = raw.decode("utf-8", errors="replace").splitlines()
            if size > TAIL_BYTES and lines:
                lines = lines[1:]
            text = "\n".join(clean_text(line, 400) for line in lines[-32:])
            return {"text": text, "truncated": size > TAIL_BYTES or len(lines) > 32,
                    "updated_at": timestamp(path.stat().st_mtime)}
        except OSError:
            return {"text": "Log temporarily unavailable.", "truncated": False}

    def latest_run(self):
        try:
            runs = [path for path in self.witness.iterdir() if RUN_NAME.fullmatch(path.name)
                    and path.is_dir() and path.resolve().is_relative_to(self.witness)]
            return max(runs, key=lambda path: path.name, default=None)
        except OSError:
            return None

    def image_path(self, run, frame, name):
        if not (RUN_NAME.fullmatch(run) and FRAME_NAME.fullmatch(frame) and BMP_NAME.fullmatch(name)):
            return None
        path = self.witness / run / "rsx" / frame / name
        if not self.allowed(path, self.witness / run / "rsx" / frame):
            return None
        try:
            if not 54 <= path.stat().st_size <= MAX_BMP:
                return None
            with path.open("rb") as stream:
                if stream.read(2) != b"BM":
                    return None
            return path
        except OSError:
            return None

    def latest_image(self, run, active, now):
        try:
            frames = sorted((run / "rsx").glob("frame_*"), key=lambda path: path.name, reverse=True)
        except OSError:
            return None
        for frame in frames:
            path = self.image_path(run.name, frame.name, "backbuffer.bmp")
            if path is None:
                continue
            try:
                stat = path.stat()
                with path.open("rb") as stream:
                    header = stream.read(54)
                width, height = struct.unpack_from("<ii", header, 18)
                age = max(0, now - stat.st_mtime)
                version = f"{stat.st_mtime_ns}-{stat.st_size}"
                stale = not active or age > FRESH_SECONDS
                return {"frame": frame.name, "version": version, "updated_at": timestamp(stat.st_mtime),
                        "age_seconds": round(age, 1), "stale": stale,
                        "label": "Previous capture" if stale else "Latest available capture",
                        "notice": "Single capture; not continuous video.",
                        "width": abs(width), "height": abs(height),
                        "url": f"/api/image/{run.name}/{frame.name}/backbuffer.bmp?v={version}"}
            except (OSError, struct.error):
                continue
        return None

    def last_gate(self, live, now):
        explicit = live.get("last_test")
        if isinstance(explicit, dict):
            result = clean_text(explicit.get("result"), 30).lower()
            return {"name": clean_text(explicit.get("name"), 100),
                    "result": result if result in ("passed", "failed", "running") else "unknown",
                    "summary": clean_text(explicit.get("summary"), 500),
                    "updated_at": clean_text(explicit.get("updated_at"), 50), "source": "live-status.json"}
        directory = self.coordination / "codex"
        try:
            candidates = [path for path in directory.glob("CX-*.log")
                          if re.search(r"(?:pytest|ctest|gate|ninja|build)[\w-]*\.log$", path.name)
                          and self.allowed(path, directory)]
            candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        except OSError:
            candidates = []
        for path in candidates[:24]:
            text = self.tail(path, directory)["text"]
            summaries = [line for line in text.splitlines() if re.search(
                r"\b\d+ (?:passed|failed)\b|\d+% tests passed|ninja: (?:build stopped|no work)", line)]
            if not summaries:
                continue
            summary = summaries[-1]
            failed = bool(re.search(r"\b[1-9]\d* failed\b|build stopped", summary))
            passed = bool(re.search(r"\b\d+ passed\b|100% tests passed|no work", summary))
            return {"name": "Latest recorded result", "result": "failed" if failed else "passed" if passed else "unknown",
                    "summary": clean_text(summary, 500), "updated_at": timestamp(path.stat().st_mtime), "source": path.name}
        return {"name": "Latest test", "result": "unknown", "summary": "No test result available.",
                "updated_at": None, "source": None}

    def snapshot(self):
        now = time.time()
        live, live_time = self.read_json(self.coordination / "live-status.json", self.coordination)
        stage_value = live.get("stage")
        stage = {"title": clean_text(stage_value, 160) or "Waiting for stage update",
                 "detail": clean_text(live.get("detail")),
                 "updated_at": clean_text(live.get("updated_at"), 50) or (timestamp(live_time) if live_time else None),
                 "candidate": live.get("candidate") if isinstance(live.get("candidate"), int) else None}
        result = {"observed_at": timestamp(now), "stage": stage, "gate": self.last_gate(live, now),
                  "run": None, "image": None, "log": {"text": "", "truncated": False}, "metrics": {}}
        run = self.latest_run()
        if run is None:
            return result
        status, modified = self.read_json(run / "status.json", run)
        age = max(0, now - modified) if modified else None
        active = status.get("running") is True and age is not None and age <= FRESH_SECONDS and process_alive(status.get("pid"))
        label = ("Active capture · recent status" if active else "Capture status unavailable" if modified is None else
                 "Latest capture ended" if status.get("running") is not True else
                 "Capture status is stale" if age > FRESH_SECONDS else "Capture process not confirmed")
        info = selected(status, ("duration_seconds", "time_limit_seconds", "timed_out", "exit_code", "pid"))
        info.update({"id": run.name, "active": active, "activity_label": label, "stale": not active,
                     "updated_at": timestamp(modified) if modified else None, "age_seconds": round(age, 1) if age is not None else None,
                     "intro": selected(status.get("menu_witness"), ("intro_opened", "intro_eof_observed", "intro_eof_elapsed",
                         "last_intro_frame", "last_rsx_frame", "rsx_capture_count", "gameplay_verified"))})
        result["run"] = info
        result["image"] = self.latest_image(run, active, now)
        result["log"] = self.tail(run / "game.log", run)
        telemetry, _ = self.read_json(run / "telemetry.json", run)
        audio, _ = self.read_json(run / "mix.full-scale.metrics.json", run)
        result["metrics"] = {"graphics": selected(telemetry, ("backend_ready", "draws", "guest_frames", "backend_presents")),
                             "audio": selected(audio, ("duration_seconds", "nan_samples", "inf_samples"))}
        return result


def create_server(root, port=8766):
    state = Dashboard(root)
    page = PAGE.read_text(encoding="utf-8")
    hashes = lambda tag: " ".join("'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"
                                  for text in re.findall(r"<" + tag + r"(?:\s[^>]*)?>(.*?)</" + tag + ">", page, re.S))
    csp = ("default-src 'none'; connect-src 'self'; img-src 'self'; script-src " + hashes("script") +
           "; style-src " + hashes("style") + "; base-uri 'none'; object-src 'none'; frame-ancestors 'self'")

    class Handler(BaseHTTPRequestHandler):
        server_version = "LocalProgress"

        def log_message(self, *args):
            pass

        def respond(self, code, body=b"", kind="application/json; charset=utf-8", etag=None):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Security-Policy", csp)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-cache" if etag else "no-store")
            if etag:
                self.send_header("ETag", etag)
            self.end_headers()
            if self.command != "HEAD":
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            host = self.headers.get("Host", "")
            allowed_hosts = {"127.0.0.1", "localhost", f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if host not in allowed_hosts:
                self.respond(400, b'{"error":"Invalid host"}')
                return
            path = unquote(urlsplit(self.path).path)
            if path == "/":
                self.respond(200, page.encode(), "text/html; charset=utf-8")
            elif path == "/api/status":
                self.respond(200, json.dumps(state.snapshot(), ensure_ascii=False, allow_nan=False).encode())
            else:
                parts = path.split("/")
                image = state.image_path(*parts[3:]) if len(parts) == 6 and parts[:3] == ["", "api", "image"] else None
                if image is None:
                    self.respond(404, b'{"error":"Resource unavailable"}')
                    return
                try:
                    stat = image.stat()
                    etag = f'"{stat.st_mtime_ns}-{stat.st_size}"'
                    if self.headers.get("If-None-Match") == etag:
                        self.respond(304, etag=etag)
                    else:
                        with image.open("rb") as stream:
                            body = stream.read(MAX_BMP + 1)
                        if len(body) > MAX_BMP:
                            self.respond(404, b'{"error":"Image unavailable"}')
                        else:
                            self.respond(200, body, "image/bmp", etag)
                except OSError:
                    self.respond(404, b'{"error":"Image unavailable"}')

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be in 0..65535")
    try:
        server = create_server(args.root, args.port)
    except OSError:
        if args.port != 8766:
            raise
        server = create_server(args.root, 0)
    print(f"Local dashboard: http://127.0.0.1:{server.server_port}/  PID={os.getpid()}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
