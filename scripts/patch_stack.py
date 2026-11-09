"""Verify and apply the ordered PS3Recomp patch stack.

The authoritative order lives in ``rebuild_ppu.PATCH_STACK``. This tool adds
static manifest checks (names, count, SHA-256) and a dry-run mode that reports
exactly where a checkout diverges from the sequence, without writing to it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import rebuild_ppu  # noqa: E402

MANIFEST = ROOT / "scripts" / "patches" / "manifest.json"


class PatchStackError(RuntimeError):
    pass


def load_manifest(path: Path = MANIFEST) -> dict:
    if not path.is_file():
        raise PatchStackError(f"Patch manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def verify_manifest(manifest: dict, patches_dir: Path = ROOT / "scripts" / "patches") -> list[str]:
    """Return a list of static manifest problems; empty means valid."""
    problems: list[str] = []
    entries = manifest.get("patches", [])
    names = [entry.get("name") for entry in entries]
    if manifest.get("patch_count") != len(entries):
        problems.append(
            f"patch_count is {manifest.get('patch_count')}, entries are {len(entries)}")
    if names != list(rebuild_ppu.PATCH_STACK):
        problems.append("manifest order does not match rebuild_ppu.PATCH_STACK")
    if manifest.get("sdk_revision") != rebuild_ppu.REVISION:
        problems.append("manifest sdk_revision differs from rebuild_ppu.REVISION")
    seen = set()
    for entry in entries:
        name = entry.get("name", "")
        if not name:
            problems.append("entry without a name")
            continue
        if name in seen:
            problems.append(f"duplicate entry: {name}")
        seen.add(name)
        patch = patches_dir / name
        if not patch.is_file():
            problems.append(f"missing file: {patch}")
            continue
        digest = hashlib.sha256(patch.read_bytes()).hexdigest()
        if entry.get("sha256") != digest:
            problems.append(f"sha256 mismatch: {name}")
        if entry.get("order") != len([n for n in names[:names.index(name) + 1]]):
            problems.append(f"non-sequential order: {name}")
        if not entry.get("subsystem"):
            problems.append(f"missing subsystem: {name}")
    return problems


def _patch_files(patches: list[Path]) -> list[str]:
    return sorted({
        match.group(1)
        for patch in patches
        for match in re.finditer(
            r"^(?:--- a/|\+\+\+ b/)(.+)$", patch.read_text(encoding="utf-8"), re.MULTILINE)
    })


def _contents(directory: Path, files: list[str]) -> dict[str, bytes | None]:
    return {
        relative: (directory / relative).read_bytes().replace(b"\r\n", b"\n")
        if (directory / relative).is_file() else None
        for relative in files
    }


def diagnose(repo: Path, patches: list[Path]) -> dict:
    """Replay the stack in a temporary tree and report the first difference."""
    repo = Path(repo)
    if not (repo / ".git").exists():
        raise PatchStackError(f"Not a git checkout: {repo}")
    files = _patch_files(patches)
    active = _contents(repo, files)
    result = {"applied_prefix": None, "failed_at": None, "error": None,
              "patch_count": len(patches), "checked_files": len(files),
              "differing_files": []}
    with tempfile.TemporaryDirectory(prefix="i2-patch-replay-") as temp:
        stage = Path(temp)
        subprocess.run(["git", "-C", str(stage), "init"], check=True, capture_output=True)
        for relative in files:
            original = subprocess.run(
                ["git", "-C", str(repo), "show", "HEAD:" + relative], capture_output=True)
            if original.returncode == 0:
                destination = stage / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(original.stdout)
        if _contents(stage, files) == active:
            result["applied_prefix"] = 0
        for count, patch in enumerate(patches, 1):
            check = subprocess.run(
                ["git", "-C", str(stage), "apply", "--check", str(patch)],
                capture_output=True, text=True)
            if check.returncode != 0:
                result["failed_at"] = count
                result["error"] = (check.stderr or check.stdout).strip()
                break
            subprocess.run(["git", "-C", str(stage), "apply", str(patch)],
                           check=True, capture_output=True)
            if _contents(stage, files) == active:
                result["applied_prefix"] = count
        if result["applied_prefix"] is None:
            # Compare while the temporary tree still exists. Reading it after
            # the context manager returns makes every path look missing.
            staged = _contents(stage, files)
            result["differing_files"] = [
                relative for relative in files if staged.get(relative) != active.get(relative)
            ]
            if result["failed_at"] is None:
                result["error"] = "checkout matches no prefix of the stack"
    return result


def apply_stack(repo: Path, patches: list[Path], prefix: int) -> None:
    apply_command = ["git", "-C", str(repo), "apply"]
    for patch in patches[prefix:]:
        subprocess.run(apply_command + ["--check", str(patch)], check=True)
        subprocess.run(apply_command + [str(patch)], check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, default=ROOT / "tools" / "ps3recomp")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--check", action="store_true", help="verify without writing (default)")
    parser.add_argument("--apply", action="store_true", help="apply the missing suffix after verification")
    parser.add_argument("--static", action="store_true", help="only verify names, order and hashes")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
    except (PatchStackError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}")
        return 2

    problems = verify_manifest(manifest, args.manifest.parent)
    if args.static:
        payload = {"ok": not problems, "patch_count": manifest.get("patch_count"),
                   "problems": problems}
        print(json.dumps(payload, indent=2) if args.json else
              ("Patch manifest OK: %d patches" % payload["patch_count"] if payload["ok"]
               else "Patch manifest problems:\n- " + "\n- ".join(problems)))
        return 0 if not problems else 1
    if problems:
        print("Patch manifest problems:")
        for problem in problems:
            print(f"- {problem}")
        return 1

    patches = [ROOT / "scripts" / "patches" / name for name in rebuild_ppu.PATCH_STACK]
    try:
        report = diagnose(args.sdk, patches)
    except (PatchStackError, OSError) as error:
        print(f"ERROR: {error}")
        return 2

    payload = {
        "ok": report["applied_prefix"] == report["patch_count"],
        "sdk": str(args.sdk),
        "expected_prefix": report["patch_count"],
        "applied_prefix": report["applied_prefix"],
        "failed_at": report["failed_at"],
        "error": report["error"],
        "differing_files": report["differing_files"],
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Patch stack: {report['patch_count']} patches")
        print(f"Applied prefix: {report['applied_prefix']}")
        if report["failed_at"]:
            print(f"First failure: patch {report['failed_at']}")
            print(report["error"])
        elif report["applied_prefix"] == report["patch_count"]:
            print("Checkout matches the complete ordered stack.")
        else:
            print("Checkout does not match the complete stack.")

    if args.apply and report["applied_prefix"] is not None:
        apply_stack(args.sdk, patches, report["applied_prefix"])
        print(f"Applied patches {report['applied_prefix'] + 1}..{report['patch_count']}.")
        return 0
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
