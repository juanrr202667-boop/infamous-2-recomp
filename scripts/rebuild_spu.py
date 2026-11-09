"""Rebuild local SPU images, including verified job binaries from EBOOT."""

from pathlib import Path
import subprocess
import sys
import json
import shutil


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools/ps3recomp/tools"
IMAGES = ROOT / "build/game/spu_images"
LIFTED = ROOT / "build/game/spu_lifted"
sys.path.insert(0, str(TOOLS))
from build_spu_workloads import fnv1a64, debase_includes
from wrap_spu_elf import wrap

# File offsets in this title's decrypted EBOOT.ELF, not guest addresses.
# These are executable jobchain stubs, not WWS C0DEC0DE overlay headers.
JOB_SPECS = (
    ("spujob_054414E2CCD626FF", 0x87A900, 1040, 0x054414E2CCD626FF, 0x3A0),
    ("spujob_64D9D14299A2B7AB", 0x87A180, 272, 0x64D9D14299A2B7AB, 0xB0),
    ("spujob_5EC85F9D752F82D1", 0x890900, 18960, 0x5EC85F9D752F82D1, 0x49A0),
    ("spujob_B5EFEE742877FBAD", 0x888880, 32800, 0xB5EFEE742877FBAD, 0x5780),
    ("spujob_8927C5C0BC233071", 0x87C200, 2192, 0x8927C5C0BC233071, 0x850),
    ("spujob_A3A153B14368B1B7", 0x897080, 7696, 0xA3A153B14368B1B7, 0x1D60),
    ("spujob_6F43025331D3AA1A", 0x87EB00, 7184, 0x6F43025331D3AA1A, 0x1B30),
    ("spujob_2BC127B9AAF9165A", 0x885880, 5520, 0x2BC127B9AAF9165A, 0x1510),
    ("spujob_7FD662F57F44EC4C", 0x87B900, 2192, 0x7FD662F57F44EC4C, 0x830),
    ("spujob_BCF018351EFDA059", 0x898F00, 1936, 0xBCF018351EFDA059, 0x720),
    ("spujob_E557DC3BB8031540", 0x87CB00, 6032, 0xE557DC3BB8031540, 0x16D0),
    ("spujob_5025422B57A0DDDD", 0x87a300, 1424, 0x5025422B57A0DDDD, 0x4c0),
    ("spujob_34DA9231D038D0F1", 0x87ad80, 2832, 0x34DA9231D038D0F1, 0xab0),
    ("spujob_939A8ABE78393C5E", 0x87e300, 1936, 0x939A8ABE78393C5E, 0x760),
    ("spujob_4FEFED619C14A417", 0x880780, 5264, 0x4FEFED619C14A417, 0x1410),
    ("spujob_5CDFC1EC84BDF9D0", 0x881c80, 8848, 0x5CDFC1EC84BDF9D0, 0x2210),
    ("spujob_5EE63B107D3D252B", 0x883f80, 6288, 0x5EE63B107D3D252B, 0x1820),
    ("spujob_D1404EE2D3742E81", 0x886e80, 6544, 0xD1404EE2D3742E81, 0x1730),
    ("spujob_C7CA9505E88A57B6", 0x895380, 3600, 0xC7CA9505E88A57B6, 0xdd0),
    ("spujob_9317E059F45944BB", 0x896200, 3600, 0x9317E059F45944BB, 0xdc0),
    ("spujob_10F7D3184428EC3C", 0x899700, 2960, 0x10F7D3184428EC3C, 0xb10),
    ("spujob_9F187A310A19C156", 0x89a300, 16528, 0x9F187A310A19C156, 0x3d10),
    ("spujob_E2B5E47BA5F8AFC2", 0x89e400, 19344, 0xE2B5E47BA5F8AFC2, 0x49a0),
    ("spujob_F651035BD5DA73CA", 0x8a3000, 21392, 0xF651035BD5DA73CA, 0x51b0),
    ("spujob_90D94A4CAF480C0C", 0x8a8400, 20624, 0x90D94A4CAF480C0C, 0x4f10),
    ("spujob_3ED4398B76463E6D", 0x8ad500, 23952, 0x3ED4398B76463E6D, 0x5b80),
    ("spujob_5238936F9B40C6A1", 0x8b3300, 7184, 0x5238936F9B40C6A1, 0x1a80),
    ("spujob_1BDCAC579E2C161A", 0x8b4f80, 3600, 0x1BDCAC579E2C161A, 0xdd0),
    ("spujob_3D27904E7BE0ACA5", 0x8b5e00, 4240, 0x3D27904E7BE0ACA5, 0xff0),
    ("spujob_4B1101EC9E5864D0", 0x8b6f00, 10000, 0x4B1101EC9E5864D0, 0x2560),
)


def prepare_job_images(elf_path: Path, images: Path, specs=JOB_SPECS) -> list[dict]:
    data = elf_path.read_bytes()
    if data[:6] != b"\x7fELF\x02\x02":
        raise ValueError("Expected a decrypted big-endian ELF64 EBOOT")
    jobs = []
    for name, offset, size, fingerprint, code_end in specs:
        raw = data[offset:offset + size]
        if len(raw) != size:
            raise ValueError(f"{name}: binary size mismatch at file offset 0x{offset:X}")
        if fnv1a64(raw) != fingerprint:
            raise ValueError(f"{name}: fingerprint mismatch; EBOOT differs from verified input")
        jobs.append({"name": name, "raw": raw, "profile": {
            "fingerprint": f"{fingerprint:016X}", "size": size,
            "base": 0, "entry": 0, "code_end": code_end,
        }})
    # Validate the entire batch before writing anything.
    images.mkdir(parents=True, exist_ok=True)
    for job in jobs:
        for suffix, contents in ((".bin", job["raw"]),
                                 (".elf", wrap(job["raw"], base=0, entry=0))):
            path = images / (job["name"] + suffix)
            if not path.exists() or path.read_bytes() != contents:
                path.write_bytes(contents)
    return jobs


def job_lift_is_current(out: Path, profile: dict) -> bool:
    if not all((out / name).is_file() for name in ("spu_recomp.c", "spu_recomp.h")):
        return False
    try:
        return json.loads((out / "lift-profile.json").read_text()) == profile
    except (OSError, ValueError):
        return False


def main() -> None:
    policy = IMAGES / "pm_wwsjob.bin"
    if not policy.is_file() or policy.stat().st_size != 11904:
        raise SystemExit("Expected the local 11904-byte WWS policy at build/game/spu_images/pm_wwsjob.bin")
    jobs = prepare_job_images(ROOT / "build/game/EBOOT.ELF", IMAGES)

    # This threaded RAD image needs the lifter's post-STOP continuation.
    # Relift it explicitly: the registry otherwise reuses stale generated C.
    # Do not use --relift on the whole batch, which would rebase the raw PM.
    bink_name = "spu_0001_at_0085E380"
    bink_image = IMAGES / (bink_name + ".elf")
    if bink_image.is_file():
        binary = bink_image.read_bytes()
        if len(binary) != 101596 or fnv1a64(binary) != 0x7117B11EE89D75F0:
            raise ValueError("Bink SPU image differs from the verified local executable")
        staged = ROOT / "build/game/spu-bink-next"
        subprocess.run([
            sys.executable, str(TOOLS / "spu_lifter.py"), str(bink_image),
            "--auto-functions", str(bink_image), "--symbol-prefix", bink_name + "_",
            "-o", str(staged),
        ], cwd=ROOT, check=True)
        (LIFTED / bink_name).mkdir(parents=True, exist_ok=True)
        for filename in ("spu_recomp.c", "spu_recomp.h"):
            debase_includes(str(staged / filename))
            shutil.copy2(staged / filename, LIFTED / bink_name / filename)

    for job in jobs:
        name, profile = job["name"], job["profile"]
        out = LIFTED / name
        if job_lift_is_current(out, profile):
            print(f"Cached SPURS job: {name}")
            continue
        subprocess.run([
            sys.executable, str(TOOLS / "spu_lifter.py"), str(IMAGES / (name + ".elf")),
            "--auto-functions", str(IMAGES / (name + ".elf")),
            "--symbol-prefix", name + "_", "--code-end", hex(profile["code_end"]),
            "-o", str(out),
        ], cwd=ROOT, check=True)
        for filename in ("spu_recomp.c", "spu_recomp.h"):
            debase_includes(str(out / filename))
        (out / "lift-profile.json").write_text(json.dumps(profile, indent=2) + "\n")

    # SPURS loads the raw policy at LS 0xA00. Relative branches must use that
    # base; the binary's absolute ILA/LQA operands already contain LS addresses.
    subprocess.run([
        sys.executable, str(TOOLS / "wrap_spu_elf.py"), str(policy),
        "--base", "0xA00", "--entry", "0xA00",
        "--out", str(IMAGES / "pm_wwsjob.elf"),
    ], cwd=ROOT, check=True)
    # Always relift this policy: an old LS-zero lift must not survive simply
    # because the ELF wrapper was corrected. The two jobs keep their own cache.
    subprocess.run([
        sys.executable, str(TOOLS / "spu_lifter.py"), str(IMAGES / "pm_wwsjob.elf"),
        "--auto-functions", str(IMAGES / "pm_wwsjob.elf"), "--base", "0xA00",
        "--symbol-prefix", "pm_wwsjob_", "-o", str(LIFTED / "pm_wwsjob"),
    ], cwd=ROOT, check=True)
    subprocess.run([
        sys.executable, str(TOOLS / "build_spu_workloads.py"),
        "--images", str(IMAGES), "--lifted", str(LIFTED),
        "--out", str(LIFTED / "spu_workloads.c"),
        "--title", "inFAMOUS 2", "--constructor",
    ], cwd=ROOT, check=True)
    build_log = ROOT / "logs/spu-build.log"
    build_log.parent.mkdir(parents=True, exist_ok=True)
    with build_log.open("wb") as output:
        result = subprocess.run([
            "cmake", "--build", str(ROOT / "build/infamous2-clang2"),
            "--config", "Release", "--target", "Infamous2Native", "-j", "4",
        ], cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, check=False)
    print(f"Native build exit: {result.returncode}. Log: {build_log}")
    result.check_returncode()


if __name__ == "__main__":
    main()
