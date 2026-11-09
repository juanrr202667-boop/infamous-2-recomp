"""Stage only four locally witnessed C0DEC0DE modules; no install or game build.

Reuse the verified staging guard, ELF wrapper and local lifter pipeline.
Same-SKU reference: backups/thiago.git @ 1ab02e702c37247ad4f53ac0b361d92e8b3269bc,
work/tools/lift_spu.py. Code/data limits below are verified on local bytes;
the reference supplies no explicit code-end override for these four modules.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import struct

import rebuild_spu_plugins as staging
from find_spu_functions import collect_branch_targets
from lift_jobmods import manual_link_targets, switch_table_targets
from spu_disasm import disassemble_spu

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STAGE = ROOT / "build/analysis/sprint-20261029/wws-module-stage"
PROFILES = (
    dict(name="jobmod_e9809289d7f3_e4640", elf_offset=0x800B80, ea=0x810B80,
         size=0x4940, base=0x4000, entry=0x8640, bss_begin=0x8960, bss_size=0xB0,
         code_end=0x8790, last_control=0x8780, last_control_word=0x327FF080,
         data_prefix="08090a0b18191a1b0c0d0e0f1c1d1e1f", image_id=201,
         sha256="986b36f10d14870160fd4be96d11e5c83b729613e6fc0f7b80ce2def09411e06"),
    dict(name="jobmod_bc11d5e7b134_e2898", elf_offset=0x7C0A00, ea=0x7D0A00,
         size=0x2D40, base=0x4000, entry=0x6898, bss_begin=0x6D60, bss_size=0x78C0,
         code_end=0x6B60, last_control=0x6B5C, last_control_word=0x327FEE80,
         data_prefix="0405060708090a0b0c0d0e0f10111213", image_id=202,
         sha256="dd7c95562b319f1c624fdb66a127a618bd068c9ad205b22998c8f0c14cdcbcc5"),
    dict(name="jobmod_a8878f357356_e58A0", elf_offset=0x7DD600, ea=0x7ED600,
         size=0x5E80, base=0x4000, entry=0x98A0, bss_begin=0x9EA0, bss_size=0x160,
         code_end=0x9BE0, last_control=0x9BD4, last_control_word=0x35000000,
         data_prefix="0405060708090a0b0c0d0e0f10111213", image_id=203,
         sha256="cd8c305548dfb77bb27012a323543bf6bd78630b404c3f459d479ff60d61999c"),
    dict(name="jobmod_6d1c65171899_eA2A0", elf_offset=0x812580, ea=0x822580,
         size=0xB6B0, base=0x4000, entry=0xE2A0, bss_begin=0xF6D0, bss_size=0x230,
         code_end=0xE740, last_control=0xE730, last_control_word=0x35000000,
         data_prefix="fffffffcfffffffafffffffc00000000", image_id=204,
         sha256="8c6641c543db8098d917f9888c74e22700c2888d338ee67ba4467579277f3296"),
)
REFERENCE_EXTRA = (0x4030, 0x4068, 0x4150, 0x41A0, 0x4388, 0x15280)


def registration_source(profiles):
    lines = ["/* Staged witnessed BCUS98125 WWS modules; preserves other registries. */",
             "#include <stdint.h>", "extern void spu_begin_image(int);",
             "extern void spu_overlay_register_sig(const uint8_t[16], int);"]
    lines.extend(f"extern void {p['name']}_spu_recomp_register(void);" for p in profiles)
    lines.append("__attribute__((constructor)) static void infamous2_register_wws_modules(void) {")
    for profile in profiles:
        sig = ",".join(f"0x{byte:02X}" for byte in bytes.fromhex(profile["sig"]))
        lines.extend([f"    {{ static const uint8_t sig[16] = {{{sig}}};",
                      f"      spu_begin_image({profile['image_id']}); {profile['name']}_spu_recomp_register();",
                      f"      spu_overlay_register_sig(sig, {profile['image_id']}); }}"])
    return "\n".join([*lines, "    spu_begin_image(0);", "}", ""])


def prepare_modules(elf_path, stage, *, profiles=PROFILES):
    stage = staging.staging_path(stage)
    data = Path(elf_path).read_bytes()
    existing = set()
    for path in (ROOT / "build/game/spu_lifted").glob("*.c"):
        existing.update(map(int, re.findall(r"spu_begin_image\(\s*(\d+)\s*\)", path.read_text(errors="replace"))))
    outputs, manifest = [], []
    names, selected_ids = set(), set()
    for original in profiles:
        profile = dict(original)
        image = profile["image_id"]
        if image not in range(201, 205) or image in existing or image in selected_ids:
            raise ValueError("WWS image ID is reserved, duplicate, or already active")
        selected_ids.add(image)
        offset, size, base = profile["elf_offset"], profile["size"], profile["base"]
        if offset < 0 or size < 0x40 or size % 16 or offset + size > len(data) or base != 0x4000:
            raise ValueError("Invalid WWS size/base")
        if staging.mapped_ea(data, offset, size) != profile["ea"]:
            raise ValueError("WWS source EA mismatch")
        raw = data[offset:offset + size]
        if hashlib.sha256(raw).hexdigest() != profile["sha256"]:
            raise ValueError("WWS fingerprint mismatch")
        entry, bss, bss_size = struct.unpack_from(">III", raw, 0x10)
        if (not all(raw[k * 4] in (0x42, 0x43) for k in range(4))
                or struct.unpack_from(">I", raw, 0x20)[0] != 0xC0DEC0DE
                or struct.unpack_from(">I", raw, 0x28)[0] != base
                or entry < 0x30 or entry % 4 or base + entry != profile["entry"]
                or bss - 32 != size or base + bss != profile["bss_begin"]
                or bss_size != profile["bss_size"] or bss % 16
                or base + bss + bss_size > 0x40000):
            raise ValueError("Invalid C0DEC0DE entry/BSS/header")
        name = f"jobmod_{hashlib.sha1(raw).hexdigest()[:12]}_e{entry:04X}"
        if profile["name"] != name or name in names:
            raise ValueError("WWS content name mismatch or duplicate")
        names.add(name)
        code_end, last = profile["code_end"], profile["last_control"]
        if (not base + entry < code_end <= base + size - 16 or code_end % 16
                or not base + entry <= last < code_end or last % 4
                or struct.unpack_from(">I", raw, last - base)[0] != profile["last_control_word"]
                or raw[code_end - base:code_end - base + 16].hex() != profile["data_prefix"]
                or any(struct.unpack_from(">I", raw, p - base)[0] not in (0, 0x00200000, 0x40200000)
                       for p in range(last + 4, code_end, 4))):
            raise ValueError("WWS code/data boundary mismatch")
        instructions = disassemble_spu(raw[:code_end - base], base_addr=base)
        if any(code_end <= target < base + size for target in collect_branch_targets(instructions)):
            raise ValueError("WWS branch reaches presumed data tail")
        lqr_to_boundary = [i.addr for i in instructions if i.mnemonic == "lqr"
                           and any(int(x, 16) == code_end for x in re.findall(r"0x[0-9A-Fa-f]+", i.operands))]
        if not lqr_to_boundary:
            raise ValueError("WWS data boundary lacks a verified LQR reference")
        profile.update(sig=raw[:16].hex(), source_elf_sha256=hashlib.sha256(data).hexdigest(),
                       lqr_to_data_boundary=lqr_to_boundary)
        if "extra_funcs" not in profile:
            profile["extra_funcs"] = sorted({a for a in (*REFERENCE_EXTRA,
                *manual_link_targets(raw, base), *switch_table_targets(raw, base)) if base <= a < code_end})
        outputs.extend([(stage / "images" / (name + ".bin"), raw),
                        (stage / "images" / (name + ".elf"), staging.wrap(raw, base=base, entry=profile["entry"]))])
        manifest.append(profile)
    outputs.extend([(stage / "spu_wws_modules_register.c", registration_source(manifest).encode()),
                    (stage / "wws-modules-manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())])
    for path, contents in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_suffix(path.suffix + ".next")
        pending.write_bytes(contents)
        pending.replace(path)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eboot", type=Path, default=ROOT / "build/game/EBOOT.ELF")
    parser.add_argument("--stage", type=Path, default=DEFAULT_STAGE)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    observations = json.loads((ROOT / "build/analysis/sprint-20261029/remaining-spu-module-matches.json").read_text())
    profiles = [dict(p) for p in PROFILES]
    for profile in profiles:
        profile["observed_pcs"] = sorted({c["pc"] for row in observations["rows"] for c in row["contexts"]
            if any(hit["module"] == profile["name"] for hit in c["base_consistent_hits"])})
        if not profile["observed_pcs"]:
            raise ValueError("Selected WWS module has no observed PC")
    manifest = prepare_modules(args.eboot, args.stage, profiles=profiles)
    if not args.prepare_only:
        report = staging.lift_plugins(args.stage, manifest)
        print(json.dumps([dict(name=p["name"], functions=p["functions"], unsupported=p["unsupported"],
                               covered_pcs=len(p["covered_pcs"])) for p in report], indent=2))
    print(f"Staged only at {args.stage}; no installation or game build")


if __name__ == "__main__":
    main()
