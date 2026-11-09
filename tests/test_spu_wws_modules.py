"""Synthetic preservation and code-boundary checks for observed WWS modules."""
import hashlib
import importlib
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


class WwsModuleStageTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("rebuild_spu_wws_modules"),
                             "Observed WWS module staging is missing")
        return importlib.import_module("rebuild_spu_wws_modules")

    def fixture(self, root):
        raw = bytearray(0x80)
        for offset in range(0, 16, 4):
            struct.pack_into(">I", raw, offset, 0x42000002)
        struct.pack_into(">III", raw, 0x10, 0x30, 0xA0, 0x20)
        struct.pack_into(">I", raw, 0x20, 0xC0DEC0DE)
        struct.pack_into(">I", raw, 0x28, 0x4000)
        struct.pack_into(">4I", raw, 0x30, 0x40800003, 0x33800184, 0x40200000, 0x35000000)
        raw[0x40:0x50] = bytes(range(16))
        source = root / "source.elf"
        elf = bytearray(0x200)
        elf[:6] = b"\x7fELF\x02\x02"
        struct.pack_into(">Q", elf, 0x20, 0x40)
        struct.pack_into(">HH", elf, 0x36, 56, 1)
        struct.pack_into(">IIQQQQQQ", elf, 0x40, 1, 5, 0x100, 0x11000, 0x11000, 0x100, 0x100, 16)
        elf[0x180:] = raw
        source.write_bytes(elf)
        name = "jobmod_" + hashlib.sha1(raw).hexdigest()[:12] + "_e0030"
        profile = dict(name=name, elf_offset=0x180, ea=0x11080, size=0x80,
                       base=0x4000, entry=0x4030, bss_begin=0x40A0, bss_size=0x20,
                       code_end=0x4040, last_control=0x403C, last_control_word=0x35000000,
                       data_prefix=bytes(range(16)).hex(), image_id=201,
                       sha256=hashlib.sha256(raw).hexdigest(), extra_funcs=[0x4038],
                       observed_pcs=[0x4034])
        return source, raw, profile

    def prepare(self, module, root, source, profiles):
        stage = root / "build/analysis/wws-module-stage"
        with patch.object(module, "ROOT", root), patch.object(module.staging, "ROOT", root):
            manifest = module.prepare_modules(source, stage, profiles=profiles)
        return stage, manifest

    def test_wrapper_entry_bss_and_dedicated_registry_preserve_other_overlays(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, raw, profile = self.fixture(root)
            active = root / "build/game/spu_lifted"
            active.mkdir(parents=True)
            light, plugins = active / "jobmods_register.c", active / "spu_plugins_register.c"
            light.write_text("spu_begin_image(200); /* preserve */")
            plugins.write_text("spu_begin_image(241); /* preserve */")
            stage, manifest = self.prepare(module, root, source, [profile])
            wrapped = (stage / "images" / (profile["name"] + ".elf")).read_bytes()
            self.assertEqual(struct.unpack_from(">I", wrapped, 0x18)[0], 0x4030)
            self.assertEqual(struct.unpack_from(">I", wrapped, 60)[0], 0x4000)
            self.assertEqual(manifest[0]["bss_begin"], 0x40A0)
            self.assertEqual((stage / "images" / (profile["name"] + ".bin")).read_bytes(), raw)
            self.assertTrue((stage / "spu_wws_modules_register.c").is_file())
            self.assertFalse((stage / "jobmods_register.c").exists())
            self.assertEqual(light.read_text(), "spu_begin_image(200); /* preserve */")
            self.assertEqual(plugins.read_text(), "spu_begin_image(241); /* preserve */")

    def test_invalid_header_bss_hash_or_code_boundary_rejects_before_output(self):
        module = self.module()
        for change in ("marker", "base", "entry", "bss", "hash", "tail", "branch_to_data"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source, raw, profile = self.fixture(root)
                bad = bytearray(raw)
                if change in ("marker", "base", "entry", "bss"):
                    offset, value = {"marker": (0x20, 0), "base": (0x28, 0),
                                     "entry": (0x10, 0x31), "bss": (0x14, 0x70)}[change]
                    struct.pack_into(">I", bad, offset, value)
                elif change == "tail":
                    bad[0x40] = 7
                elif change == "branch_to_data":
                    struct.pack_into(">I", bad, 0x30, 0x32000200)  # br 0x4040
                if change == "hash":
                    profile["sha256"] = "0" * 64
                else:
                    profile["sha256"] = hashlib.sha256(bad).hexdigest()
                    profile["name"] = "jobmod_" + hashlib.sha1(bad).hexdigest()[:12] + "_e0030"
                    source.write_bytes(source.read_bytes()[:0x180] + bad)
                failure = {"tail": "boundary", "branch_to_data": "branch reaches", "hash": "fingerprint"}.get(change, "header")
                with self.assertRaisesRegex(ValueError, failure):
                    self.prepare(module, root, source, [profile])
                self.assertFalse((root / "build/analysis/wws-module-stage").exists())

    def test_existing_image_id_collision_rejects_without_rewriting_registry(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _, profile = self.fixture(root)
            active = root / "build/game/spu_lifted"
            active.mkdir(parents=True)
            registry = active / "existing.c"
            registry.write_text("spu_begin_image(201); /* already reserved */")
            with self.assertRaisesRegex(ValueError, "image"):
                self.prepare(module, root, source, [profile])
            self.assertEqual(registry.read_text(), "spu_begin_image(201); /* already reserved */")
            self.assertFalse((root / "build/analysis/wws-module-stage").exists())

    def test_registry_uses_content_signature_and_reserved_image_at_runtime(self):
        module = self.module()
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            self.skipTest("clang-cl unavailable; set I2_CLANG_CL")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _, profile = self.fixture(root)
            stage, _ = self.prepare(module, root, source, [profile])
            code = r'''
#include <cstdlib>
static unsigned phase;
extern "C" void spu_begin_image(int image) {
    if ((phase == 0 && image != 201) || (phase == 3 && image != 0)) std::abort();
    ++phase;
}
extern "C" void MODULE_spu_recomp_register() { if (phase++ != 1) std::abort(); }
extern "C" void spu_overlay_register_sig(const unsigned char* sig, int image) {
    if (phase++ != 2 || image != 201) std::abort();
    for (int k = 0; k < 16; ++k) {
        unsigned want = k % 4 == 0 ? 0x42 : k % 4 == 3 ? 2 : 0;
        if (sig[k] != want) std::abort();
    }
}
extern "C" {
#include "spu_wws_modules_register.c"
}
int main() { return phase == 4 ? 0 : 1; }
'''.replace("MODULE", profile["name"])
            probe = root / "probe.cpp"
            probe.write_text(code)
            exe = root / "probe.exe"
            build = subprocess.run([compiler, "/nologo", "/I" + str(stage), str(probe),
                                    "/Fe" + str(exe), "/Fo" + str(root / "probe.obj")],
                                   capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_real_lifter_reuses_extra_entries_and_covers_observed_pc(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _, profile = self.fixture(root)
            stage, manifest = self.prepare(module, root, source, [profile])
            with patch.object(module.staging, "ROOT", root):
                report = module.staging.lift_plugins(stage, manifest)
            self.assertEqual(report[0]["unsupported"], 0)
            self.assertEqual(report[0]["covered_pcs"], [0x4034])
            emitted = (stage / "lifted" / profile["name"] / "spu_recomp.c").read_text()
            self.assertIn(profile["name"] + "_spu_func_00004038(spu_context* ctx) {", emitted)


if __name__ == "__main__":
    unittest.main()
