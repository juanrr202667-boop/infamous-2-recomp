"""Reject proprietary game material and secrets from the repository.

Checks paths *and* content signatures, because an extension alone is easy to
rename. The scanner is intentionally conservative about source files: a
synthetic fixture that merely contains the text "ELF" is not flagged, while a
file whose first bytes are a real ELF/SELF/PKG/PSARC/Bink header is.

Usage:
    python scripts/check_forbidden_files.py --tracked
    python scripts/check_forbidden_files.py --all --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Path fragments that identify proprietary inputs, extracted game trees, or
# local credential stores. These are path checks, not extension checks.
FORBIDDEN_PATH_PATTERNS = [
    (re.compile(r"(^|/)gamedata(/|$)", re.I), "local game data directory"),
    (re.compile(r"(^|/)BCUS98125", re.I), "game serial/title directory"),
    (re.compile(r"(^|/)PS3_GAME(/|$)", re.I), "PS3 game tree"),
    (re.compile(r"(^|/)dev_(hdd0|bdvd|flash)(/|$)", re.I), "PS3 virtual filesystem"),
    (re.compile(r"(^|/)(EBOOT\.BIN|EBOOT\.ELF|default\.self)(/|$)", re.I), "game executable"),
    (re.compile(r"(^|/)(saves?|savegame|savedata)(/|$)", re.I), "save data"),
    (re.compile(r"(^|/)(firmware|keys?)(/|$)", re.I), "firmware/key material"),
    (re.compile(r"(^|/)\.env(\.|$)", re.I), "environment secret file"),
    (re.compile(r"(^|/)(id_rsa|id_ed25519|credentials|secrets?)(\.|$|/)", re.I), "credential file"),
    (re.compile(r"\.(pem|key|p12|pfx)$", re.I), "private key or certificate"),
]

FORBIDDEN_EXTENSIONS = {
    ".iso": "disc image",
    ".pkg": "PS3 package",
    ".rap": "PS3 activation file",
    ".rif": "PS3 license file",
    ".edat": "PS3 encrypted data",
    ".self": "PS3 SELF executable",
    ".elf": "ELF executable",
    ".sprx": "PS3 module",
    ".prx": "PS3 module",
    ".bik": "Bink video",
    ".psarc": "PS3 archive",
    ".xpps": "PS3 packaged stream",
    ".rdc": "RenderDoc capture",
    ".wpix": "PIX capture",
    ".f32": "raw float audio",
    ".sfo": "PS3 system metadata",
}

# Content signatures are checked at offset 0 and, for PEM, anywhere in the
# first 4 KiB. Keep this list short and high-signal to avoid false positives.
MAGIC_SIGNATURES = [
    (b"\x7fELF", "ELF executable"),
    (b"SCE\x00", "PS3 SELF/RIF/EDAT container"),
    (b"\x7fPKG", "PS3 package"),
    (b"PSARC", "PS3 archive"),
    (b"BIK", "Bink video"),
    (b"KB2", "Bink 2 video"),
    (b"-----BEGIN ENCRYPTED PRIVATE KEY-----", "encrypted private key"),
    (b"-----BEGIN RSA PRIVATE KEY-----", "RSA private key"),
    (b"-----BEGIN OPENSSH PRIVATE KEY-----", "OpenSSH private key"),
]

# Synthetic fixtures are allowed to describe formats without containing game
# data. They are not exempt from path checks for game executables or secrets.
SYNTHETIC_ALLOWLIST = (
    re.compile(r"^tests/native/synthetic_recomp/", re.I),
    re.compile(r"^tests/fixtures/synthetic/", re.I),
)

# Source and documentation may quote a signature in order to test or describe
# it. Their first bytes are still checked, but a private-key block is only
# treated as a secret in a non-text file.
TEXT_SUFFIXES = {
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cmake", ".md", ".txt", ".py", ".ps1",
    ".json", ".yml", ".yaml", ".toml", ".patch", ".diff", ".gitignore",
    ".gitattributes", ".html", ".css", ".js", ".ts",
}


def normalize(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def path_findings(relative: str) -> list[dict]:
    findings = []
    for pattern, label in FORBIDDEN_PATH_PATTERNS:
        if pattern.search(relative):
            findings.append({"path": relative, "reason": label, "kind": "path"})
            break
    suffix = Path(relative).suffix.lower()
    if suffix in FORBIDDEN_EXTENSIONS:
        findings.append({"path": relative, "reason": FORBIDDEN_EXTENSIONS[suffix],
                         "kind": "extension"})
    return findings


def content_finding(path: Path, relative: str) -> dict | None:
    try:
        with path.open("rb") as handle:
            head = handle.read(4096)
    except OSError as error:
        return {"path": relative, "reason": f"unreadable: {error}", "kind": "error"}
    is_text = path.suffix.lower() in TEXT_SUFFIXES
    for signature, label in MAGIC_SIGNATURES:
        if head.startswith(signature):
            return {"path": relative, "reason": label, "kind": "content"}
        if not is_text and label.endswith("private key") and signature in head:
            return {"path": relative, "reason": label, "kind": "content"}
    return None


def is_synthetic(relative: str) -> bool:
    return any(pattern.search(relative) for pattern in SYNTHETIC_ALLOWLIST)


def scan_paths(paths: list[Path], root: Path = ROOT) -> list[dict]:
    findings = []
    for path in paths:
        relative = normalize(path, root)
        findings.extend(path_findings(relative))
        if not path.is_file():
            continue
        if path.is_symlink():
            findings.append({"path": relative, "reason": "symlink (review target)",
                             "kind": "symlink"})
            continue
        if is_synthetic(relative):
            continue
        finding = content_finding(path, relative)
        if finding:
            findings.append(finding)
    return findings


def tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True)
    names = [name for name in result.stdout.decode("utf-8", "replace").split("\0") if name]
    return [root / name for name in names]


def all_files(root: Path) -> list[Path]:
    ignored = {".git", "build", "gamedata", "tools", ".venv", "__pycache__", ".pytest_cache"}
    paths = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in ignored]
        for name in filenames:
            paths.append(Path(directory) / name)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--tracked", action="store_true",
                       help="scan files known to git (default)")
    group.add_argument("--all", action="store_true",
                       help="scan tracked and untracked files, excluding build/tools/gamedata")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    paths = all_files(root) if args.all else tracked_files(root)
    findings = scan_paths(paths, root)
    payload = {
        "ok": not findings,
        "scanned": len(paths),
        "findings": findings,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    elif findings:
        print(f"Forbidden material: {len(findings)} finding(s) in {len(paths)} file(s)")
        for finding in findings:
            print(f"- [{finding['kind']}] {finding['path']}: {finding['reason']}")
    else:
        print(f"Forbidden material: none in {len(paths)} file(s)")
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
