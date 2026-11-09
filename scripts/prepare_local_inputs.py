"""Derive local loader tables and SPU images from the verified BCUS98125 ELF.

Only the developer's ignored build/game tree is written. Game bytes are never
printed, committed, downloaded, or sent anywhere by this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from rebuild_ppu import ELF_HASH, REVISION

ROOT = Path(__file__).resolve().parents[1]
POLICY_OFFSET = 0x877080
POLICY_SIZE = 11904
POLICY_SHA256 = "3a1930a2bc834787b6d4e74d9cfc1d78d87d42d07d2bd56ea3a1090ac72f237d"
LOADER_NAMES = (
    "EBOOT.functions.json", "EBOOT.image.json",
    "EBOOT.loader.json", "EBOOT.imports.json",
)


def verify_elf(path: Path, expected_sha256: str = ELF_HASH) -> bytes:
    if not path.is_file():
        raise ValueError(f"Missing local EBOOT.ELF: {path}")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("EBOOT.ELF SHA-256 does not match the verified BCUS98125 edition")
    return data


def policy_from_elf(data: bytes, *, offset: int = POLICY_OFFSET,
                    size: int = POLICY_SIZE,
                    expected_sha256: str = POLICY_SHA256) -> bytes:
    policy = data[offset:offset + size]
    if len(policy) != size or hashlib.sha256(policy).hexdigest() != expected_sha256:
        raise ValueError("WWS policy slice does not match the verified local ELF")
    return policy


def install_outputs(outputs: dict[Path, bytes], *, check_only: bool = False) -> tuple[int, int]:
    """Refuse all divergent destinations before writing any generated output."""
    conflicts = [path for path, contents in outputs.items()
                 if path.exists() and (not path.is_file() or path.read_bytes() != contents)]
    if conflicts:
        raise ValueError("Local output conflict; preserve and review: " +
                         ", ".join(str(path) for path in conflicts))
    existing = sum(path.is_file() for path in outputs)
    missing = len(outputs) - existing
    if not check_only:
        for path, contents in outputs.items():
            if path.is_file():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as staged:
                pending = Path(staged.name)
                staged.write(contents)
            try:
                os.replace(pending, path)
            finally:
                pending.unlink(missing_ok=True)
    return existing, missing


def prepare(root: Path, *, check_only: bool = False) -> tuple[int, int]:
    root = root.resolve()
    elf = root / "build/game/EBOOT.ELF"
    data = verify_elf(elf)
    policy = policy_from_elf(data)
    sdk = root / "tools/ps3recomp"
    tools = sdk / "tools"
    for name in ("ppu_loader.py", "extract_spu_images.py"):
        if not (tools / name).is_file():
            raise ValueError(f"Missing pinned SDK tool: {tools / name}; run scripts/setup.ps1")
    revision = subprocess.check_output(
        ["git", "-C", str(sdk), "rev-parse", "HEAD"], text=True).strip()
    if revision != REVISION:
        raise ValueError("PS3Recomp revision differs from the pinned commit")

    with tempfile.TemporaryDirectory(prefix="i2-inputs-", dir=root / "build") as temp:
        stage = Path(temp)
        loader = stage / "loader"
        images = stage / "spu_images"
        for tool, output in (("ppu_loader.py", loader),
                             ("extract_spu_images.py", images)):
            subprocess.run([sys.executable, str(tools / tool), str(elf),
                            "--output", str(output)], check=True, capture_output=True)
        outputs = {}
        for name in LOADER_NAMES:
            path = loader / name
            if not path.is_file():
                raise ValueError(f"SDK did not produce expected loader table: {name}")
            json.loads(path.read_text(encoding="utf-8"))
            outputs[root / "build/game/loader" / name] = path.read_bytes()
        extracted = sorted(images.glob("spu_*.elf"))
        if not extracted:
            raise ValueError("SDK did not find embedded SPU images in the verified ELF")
        outputs.update({root / "build/game/spu_images" / path.name: path.read_bytes()
                        for path in extracted})
        outputs[root / "build/game/spu_images/pm_wwsjob.bin"] = policy
        return install_outputs(outputs, check_only=check_only)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT,
                        help="project checkout containing an ignored build/game/EBOOT.ELF")
    parser.add_argument("--check-only", action="store_true",
                        help="validate all inputs and compare outputs without writing")
    args = parser.parse_args()
    try:
        existing, missing = prepare(args.root, check_only=args.check_only)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Local input preparation failed: {error}\n")
    action = "would create" if args.check_only else "created"
    print(f"Verified local ELF and SDK: {existing} outputs unchanged; {missing} {action}.")
    print("Game inputs and generated outputs remain under ignored build/game/.")


if __name__ == "__main__":
    main()
