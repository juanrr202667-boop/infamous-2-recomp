"""Verify every tracked public file against the staged publication inventory.

Uses Git index blobs so line-ending conversion cannot change the answer. Run
after staging changes; CI checks the committed index on each pull request.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "PUBLICATION_MANIFEST.json"
PATCH_MANIFEST = "scripts/patches/manifest.json"
PERSONAL_WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\\/\s\"']+", re.I)


def is_text(data: bytes) -> bool:
    if b"\0" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def verify(manifest: dict, files: dict[str, bytes], patches: dict) -> list[str]:
    errors = []
    recorded = manifest.get("files_sha256", {})
    if not isinstance(recorded, dict):
        return ["files_sha256 must be an object"]
    for name in sorted(recorded.keys() - files.keys()):
        errors.append(f"missing tracked file: {name}")
    for name in sorted(files.keys() - recorded.keys()):
        errors.append(f"unlisted tracked file: {name}")
    for name in sorted(files.keys() & recorded.keys()):
        digest = recorded[name]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            errors.append(f"invalid hash: {name}")
        elif hashlib.sha256(files[name]).hexdigest() != digest:
            errors.append(f"hash mismatch: {name}")
        if is_text(files[name]) and PERSONAL_WINDOWS_PATH.search(files[name].decode("utf-8")):
            errors.append(f"personal path in tracked file: {name}")
    if manifest.get("effective_patch_count") != patches.get("patch_count"):
        errors.append("patch count differs from scripts/patches/manifest.json")
    if manifest.get("upstream_revision") != patches.get("sdk_revision"):
        errors.append("SDK revision differs from scripts/patches/manifest.json")
    return errors


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true",
                        help="regenerate file hashes from the staged Git index")
    args = parser.parse_args(argv)
    names = [name.decode("utf-8") for name in git(ROOT, "ls-files", "-z").split(b"\0") if name]
    if MANIFEST not in names or PATCH_MANIFEST not in names:
        print("Publication or patch manifest is not tracked")
        return 1
    manifest = json.loads(git(ROOT, "show", ":" + MANIFEST))
    patches = json.loads(git(ROOT, "show", ":" + PATCH_MANIFEST))
    files = {name: git(ROOT, "show", ":" + name)
             for name in names if name != MANIFEST}
    if args.update:
        manifest["files_sha256"] = {
            name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())
        }
        manifest["effective_patch_count"] = patches["patch_count"]
        manifest["upstream_revision"] = patches["sdk_revision"]
        manifest["snapshot_date"] = date.today().isoformat()
        (ROOT / MANIFEST).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Updated {MANIFEST} from {len(files)} staged files; review and stage it")
        return 0
    errors = verify(manifest, files, patches)
    if errors:
        print(f"Publication manifest: {len(errors)} error(s)")
        for error in errors[:20]:
            print("- " + error)
        return 1
    print(f"Publication manifest: {len(files)} indexed files verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
