"""Prepare only four witnessed WWS plugins in analysis staging; never install/build.

BCUS98125 fingerprints come from the local EBOOT. The same-SKU reference at
backups/thiago.git (1ab02e702c37247ad4f53ac0b361d92e8b3269bc),
work/tools/lift_spu.py, identifies their DA7ABA5E header, LS base and entry.
Observed PC windows and full comparison: spu-context-plugin-matches.json.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import stat
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"
SDK_REVISION = "5244db92669b54831c6d3ebb7a96d58c4dbfa6df"
DEFAULT_STAGE = ROOT / "build/analysis/sprint-20261029/spu-plugin-stage"
sys.path.insert(0, str(SDK / "tools"))
from wrap_spu_elf import wrap
from find_spu_functions import detect_functions
from build_spu_workloads import debase_includes

PROFILES = (
    dict(name="wws_plugin_0081F000", elf_offset=0x80F000, ea=0x81F000,
         size=0xB90, base=0x6C00, entry=0x6C30, code_end=0x7684, image_id=241,
         sha256="bd6aa8b296bc8d99957ef3ab51c2d272cf9410ede470f87a0f2d7238e4d8aa85"),
    dict(name="wws_plugin_0081FC00", elf_offset=0x80FC00, ea=0x81FC00,
         size=0x790, base=0x6C00, entry=0x6C30, code_end=0x72C4, image_id=242,
         sha256="7f3b3e64d1939558e42cdbdb174139d68accd7af3b81de1f55e2648ac2723927"),
    dict(name="wws_plugin_00820400", elf_offset=0x810400, ea=0x820400,
         size=0xB90, base=0x6C00, entry=0x6C30, code_end=0x7674, image_id=243,
         sha256="216c4062e68703b78949453cebf91243af2e249e6b2073f146d195e615221692"),
    dict(name="wws_plugin_00821000", elf_offset=0x811000, ea=0x821000,
         size=0x1510, base=0x6C00, entry=0x6C30, code_end=0x7F64, image_id=244,
         sha256="cc7142e974454a56efdf4526c1358ad42ee277fd7f0618d731b97529c5a219fd"),
)


def staging_path(path):
    path = Path(path).resolve()
    analysis = (ROOT / "build/analysis").resolve()
    if not path.is_relative_to(analysis) or path == analysis:
        raise ValueError("Plugin outputs must stay in a subdirectory of build/analysis")
    # A safe root is insufficient when an existing child redirects a later
    # write. Inspect reparse metadata before descending, never follow it.
    pending = [path] if path.exists() else []
    while pending:
        parent = pending.pop()
        for child in parent.iterdir():
            metadata = child.lstat()
            if (stat.S_ISLNK(metadata.st_mode)
                    or getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise ValueError(f"Staging descendant redirects are forbidden: {child}")
            if stat.S_ISDIR(metadata.st_mode):
                pending.append(child)
    return path


def mapped_ea(data, offset, size):
    if data[:6] != b"\x7fELF\x02\x02" or len(data) < 64:
        raise ValueError("Expected a decrypted big-endian ELF64 EBOOT")
    phoff = struct.unpack_from(">Q", data, 0x20)[0]
    entsize, count = struct.unpack_from(">HH", data, 0x36)
    if entsize != 56 or phoff + count * entsize > len(data):
        raise ValueError("Invalid ELF program headers")
    for index in range(count):
        kind, _, off, va, _, filesz, _, _ = struct.unpack_from(">IIQQQQQQ", data, phoff + index * entsize)
        if kind == 1 and off <= offset and offset + size <= off + filesz:
            return va + offset - off
    raise ValueError("Plugin region is not file-backed")


def registry_source(profiles):
    lines = ["/* Staged local BCUS98125 WWS plugins; no other registry is replaced. */",
             "#include <stdint.h>",
             "extern void spu_begin_image(int);",
             "extern void spu_overlay_register_region(uint32_t, uint32_t, int);"]
    lines.extend(f"extern void {p['name']}_spu_recomp_register(void);" for p in profiles)
    lines.append("__attribute__((constructor)) static void infamous2_register_spu_plugins(void) {")
    for profile in profiles:
        lines.extend([f"    spu_begin_image({profile['image_id']});",
                      f"    {profile['name']}_spu_recomp_register();",
                      f"    spu_overlay_register_region(0x{profile['ea']:08X}u, "
                      f"0x{profile['size']:X}u, {profile['image_id']});"])
    lines.extend(["    spu_begin_image(0);", "}", ""])
    return "\n".join(lines)


def prepare_plugins(elf_path, stage, *, profiles=PROFILES):
    stage = staging_path(stage)
    data = Path(elf_path).read_bytes()
    outputs = []
    manifest = []
    names, ids = set(), set()
    for original in profiles:
        profile = dict(original)
        name = profile["name"]
        if not re.fullmatch(r"wws_plugin_[0-9A-F]{8}", name) or name in names:
            raise ValueError("Invalid or duplicate plugin name")
        if profile["image_id"] not in range(241, 245) or profile["image_id"] in ids:
            raise ValueError("Plugin image IDs must be distinct and reserved 241..244")
        names.add(name)
        ids.add(profile["image_id"])
        offset, size, base = profile["elf_offset"], profile["size"], profile["base"]
        if (offset < 0 or size < 0x40 or size % 16 or offset + size > len(data)
                or base != 0x6C00 or base + size > 0x40000):
            raise ValueError("Invalid plugin size/base")
        if mapped_ea(data, offset, size) != profile["ea"]:
            raise ValueError("Plugin source EA mismatch")
        raw = data[offset:offset + size]
        if hashlib.sha256(raw).hexdigest() != profile["sha256"]:
            raise ValueError("Plugin fingerprint mismatch")
        magic, entry_offset, header_size = struct.unpack_from(">III", raw, 0x10)
        if (not all(raw[k * 4] in (0x42, 0x43) for k in range(4))
                or magic != 0xDA7ABA5E or header_size != size
                or entry_offset < 0x30 or entry_offset % 4 or entry_offset >= size
                or base + entry_offset != profile["entry"]):
            raise ValueError("Invalid DA7ABA5E plugin header/entry")
        code_size = profile["code_end"] - base
        rodata_start = (code_size + 15) & ~15
        if (not entry_offset < code_size <= size - 8 or code_size % 4
                or struct.unpack_from(">I", raw, code_size - 4)[0] != 0x35000000
                or any(raw[code_size:rodata_start])
                or raw[rodata_start:rodata_start + 8] != bytes.fromhex("8080808080808080")):
            raise ValueError("Plugin code-end/return/padding/shuffle boundary mismatch")
        outputs.extend([(stage / "images" / (name + ".bin"), raw),
                        (stage / "images" / (name + ".elf"),
                         wrap(raw, base=base, entry=profile["entry"]))])
        profile["source_elf_sha256"] = hashlib.sha256(data).hexdigest()
        manifest.append(profile)
    outputs.extend([(stage / "spu_plugins_register.c", registry_source(manifest).encode()),
                    (stage / "plugins-manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())])
    # Validate the complete selection before writing any staged file.
    for path, contents in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_suffix(path.suffix + ".next")
        pending.write_bytes(contents)
        pending.replace(path)
    return manifest


def lift_plugins(stage, profiles):
    stage = staging_path(stage)
    revision = subprocess.check_output(["git", "-C", str(SDK), "rev-parse", "HEAD"], text=True).strip()
    if revision != SDK_REVISION:
        raise ValueError("Review toolkit revision before staging WWS plugins")
    logs = stage / "logs"
    logs.mkdir(exist_ok=True)
    report = []
    for profile in profiles:
        name = profile["name"]
        elf = stage / "images" / (name + ".elf")
        lifted = stage / "lifted" / name
        extra = sorted({profile["entry"], *profile.get("extra_funcs", [])})
        command = [sys.executable, str(SDK / "tools/spu_lifter.py"),
                   "--auto-functions", str(elf), "--base", hex(profile["base"]),
                   "--symbol-prefix", name + "_", "--extra-funcs", ",".join(map(hex, extra)),
                   "--code-end", hex(profile["code_end"]), "-o", str(lifted)]
        with (logs / (name + "-lift.log")).open("w", encoding="utf-8") as log:
            log.write(json.dumps(dict(command=command, sdk_revision=revision)) + "\n")
            log.flush()
            subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
        for filename in ("spu_recomp.c", "spu_recomp.h"):
            debase_includes(str(lifted / filename))
        source = (lifted / "spu_recomp.c").read_text(encoding="ascii", errors="replace")
        definitions = {int(x, 16) for x in re.findall(
            rf"void {name}_spu_func_([0-9A-F]+)\(spu_context\* ctx\)\s*\{{", source)}
        if profile["entry"] not in definitions:
            raise ValueError(f"Plugin lift missing entry {name}")
        unsupported = len(re.findall(r"UNSUPPORTED|unsupported|/\*.*\.word", source))
        if unsupported:
            raise ValueError(f"Plugin lift has unsupported instructions: {name}")
        bounds, _ = detect_functions(elf.read_bytes(), verbose=False)
        # Match the local CLI's extra-entry splits before its code-end cap.
        for address in extra:
            split = []
            for start, end in bounds:
                split.extend([(start, address), (address, end)] if start < address < end else [(start, end)])
            if not any(start == address for start, _ in split):
                following = min([start for start, _ in split if start > address]
                                + [profile["base"] + profile["size"]])
                split.append((address, following))
            by_start = {}
            for start, end in split:
                by_start[start] = min(by_start.get(start, end), end)
            bounds = sorted(by_start.items())
        bounds = [(s, min(e, profile["code_end"])) for s, e in bounds if s < profile["code_end"]]
        observed = sorted(set(profile.get("observed_pcs", [])))
        covered = [pc for pc in observed if any(s <= pc < e and s in definitions for s, e in bounds)]
        if covered != observed:
            raise ValueError(f"Observed PC outside generated functions: {name}")
        item = dict(profile, sdk_revision=revision, command=command,
                    functions=len(definitions), unsupported=unsupported, covered_pcs=covered,
                    bounds=bounds,
                    generated_sha256={f: hashlib.sha256((lifted / f).read_bytes()).hexdigest()
                                      for f in ("spu_recomp.c", "spu_recomp.h")},
                    lifter_sha256=hashlib.sha256((SDK / "tools/spu_lifter.py").read_bytes()).hexdigest())
        report.append(item)
    (stage / "lift-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eboot", type=Path, default=ROOT / "build/game/EBOOT.ELF")
    parser.add_argument("--stage", type=Path, default=DEFAULT_STAGE)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--observations", type=Path,
                        default=ROOT / "build/analysis/sprint-20261029/spu-context-plugin-matches.json")
    args = parser.parse_args()
    profiles = [dict(p) for p in PROFILES]
    observations = json.loads(args.observations.read_text())
    for profile in profiles:
        profile["observed_pcs"] = sorted({row["pc"] for row in observations["rows"]
            if any(h["plugin"] == profile["name"] for h in row["base_consistent_matches"])})
        if not profile["observed_pcs"]:
            raise ValueError(f"No sampled PC for selected plugin: {profile['name']}")
    manifest = prepare_plugins(args.eboot, args.stage, profiles=profiles)
    if args.prepare_only:
        print(f"Prepared {len(manifest)} plugins at {args.stage}; no installation or build")
    else:
        report = lift_plugins(args.stage, manifest)
        print(json.dumps([dict(name=p["name"], functions=p["functions"],
                               covered_pcs=len(p["covered_pcs"]), unsupported=p["unsupported"])
                          for p in report], indent=2))
        print(f"Staged only: {args.stage}; no installation or build")


if __name__ == "__main__":
    main()
