"""Synthetic checks for isolated WWS-plugin preparation and registration."""
import hashlib
import importlib
import importlib.util
import json
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


class SpuPluginTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("rebuild_spu_plugins"),
                             "WWS plugin staging implementation is missing")
        return importlib.import_module("rebuild_spu_plugins")

    def fixture(self, root):
        raw = bytearray(0x90)
        for offset in range(0, 16, 4):
            struct.pack_into(">I", raw, offset, 0x42000002)
        struct.pack_into(">III", raw, 0x10, 0xDA7ABA5E, 0x30, 0x90)
        struct.pack_into(">I", raw, 0x30, 0x35000000)  # bi $r0
        raw[0x40:0x48] = bytes.fromhex("8080808080808080")
        elf = bytearray(0x210)
        elf[:6] = b"\x7fELF\x02\x02"
        struct.pack_into(">Q", elf, 0x20, 0x40)
        struct.pack_into(">HH", elf, 0x36, 56, 1)
        struct.pack_into(">IIQQQQQQ", elf, 0x40, 1, 5, 0x100, 0x11000, 0x11000, 0x110, 0x110, 16)
        elf[0x180:] = raw
        source = root / "source.elf"
        source.write_bytes(elf)
        profile = dict(name="wws_plugin_00011080", elf_offset=0x180,
                       ea=0x11080, size=0x90, sha256=hashlib.sha256(raw).hexdigest(),
                       base=0x6C00, entry=0x6C30, code_end=0x6C34, image_id=244)
        return source, bytes(raw), profile

    def prepare(self, module, root, source, profiles):
        stage = root / "build/analysis/plugin-stage"
        with patch.object(module, "ROOT", root):
            return stage, module.prepare_plugins(source, stage, profiles=profiles)

    def redirect_directory(self, link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
            result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_elf_wrapper_preserves_plugin_load_address_and_header_entry(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, raw, profile = self.fixture(root)
            stage, manifest = self.prepare(module, root, source, [profile])
            wrapped = (stage / "images/wws_plugin_00011080.elf").read_bytes()
            self.assertEqual(struct.unpack_from(">I", wrapped, 0x18)[0], 0x6C30)
            self.assertEqual(struct.unpack_from(">I", wrapped, 52 + 8)[0], 0x6C00)
            self.assertEqual((stage / "images/wws_plugin_00011080.bin").read_bytes(), raw)
            self.assertEqual(manifest[0]["code_end"], 0x6C34)

    def test_header_or_source_ea_mismatch_rejects_before_output(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for change in ("magic", "entry", "size", "ila", "ea", "code_end"):
                with self.subTest(change=change):
                    source, raw, profile = self.fixture(root)
                    bad = bytearray(raw)
                    if change in ("magic", "entry", "size", "ila"):
                        offset, value = {"magic": (0x10, 0), "entry": (0x14, 0x31),
                                         "size": (0x18, 0x80), "ila": (0, 0)}[change]
                        struct.pack_into(">I", bad, offset, value)
                        source.write_bytes(source.read_bytes()[:0x180] + bad)
                        profile["sha256"] = hashlib.sha256(bad).hexdigest()
                    elif change == "ea":
                        profile["ea"] += 0x10
                    else:
                        profile["code_end"] = 0x6C38
                    with self.assertRaises(ValueError):
                        self.prepare(module, root, source, [profile])
                    self.assertFalse((root / "build/analysis/plugin-stage").exists())

    def test_hash_mismatch_rejects_before_output(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _, profile = self.fixture(root)
            profile["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                self.prepare(module, root, source, [profile])
            self.assertFalse((root / "build/analysis/plugin-stage").exists())

    def test_all_selected_plugins_validate_before_first_write(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _, first = self.fixture(root)
            second = dict(first, name="wws_plugin_00011110", image_id=243,
                          sha256="0" * 64)
            with self.assertRaises(ValueError):
                self.prepare(module, root, source, [first, second])
            self.assertFalse((root / "build/analysis/plugin-stage").exists())

    def test_active_lift_tree_is_rejected_and_existing_overlays_survive(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _, profile = self.fixture(root)
            active = root / "build/game/spu_lifted"
            (active / "spulight").mkdir(parents=True)
            registry = active / "jobmods_register.c"
            registry.write_text("existing SpuLight registry")
            light = active / "spulight/spu_recomp.c"
            light.write_text("existing SpuLight source")
            with patch.object(module, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "analysis"):
                    module.prepare_plugins(source, active, profiles=[profile])
            self.prepare(module, root, source, [profile])
            self.assertEqual(registry.read_text(), "existing SpuLight registry")
            self.assertEqual(light.read_text(), "existing SpuLight source")
            self.assertFalse((root / "build/analysis/plugin-stage/jobmods_register.c").exists())

    def test_generated_registry_registers_correct_id_source_and_span(self):
        module = self.module()
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            candidate = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(candidate) if candidate.is_file() else None
        if not compiler:
            self.skipTest("clang-cl unavailable; set I2_CLANG_CL")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _, profile = self.fixture(root)
            stage, _ = self.prepare(module, root, source, [profile])
            probe = root / "probe.cpp"
            probe.write_text('''
#include <cstdlib>
static unsigned phase;
extern "C" void spu_begin_image(int id) {
    if ((phase == 0 && id != 244) || (phase == 3 && id != 0)) std::abort();
    ++phase;
}
extern "C" void wws_plugin_00011080_spu_recomp_register() {
    if (phase++ != 1) std::abort();
}
extern "C" void spu_overlay_register_region(unsigned ea, unsigned span, int image) {
    if (phase++ != 2 || ea != 0x11080 || span != 0x90 || image != 244) std::abort();
}
extern "C" {
#include "spu_plugins_register.c"
}
int main() { return phase == 4 ? 0 : 1; }
''')
            executable = root / "probe.exe"
            build = subprocess.run([compiler, "/nologo", "/I" + str(stage), str(probe),
                                    "/Fe" + str(executable), "/Fo" + str(root / "probe.obj")],
                                   capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_redirected_descendant_rejects_before_preparation_writes(self):
        module = self.module()
        for dirname in ("images", "logs", "lifted"):
            with self.subTest(dirname=dirname), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source, _, profile = self.fixture(root)
                active = root / "build/game/spu_lifted"
                active.mkdir(parents=True)
                (active / "sentinel.txt").write_text("preserve active tree")
                stage = root / "build/analysis/plugin-stage"
                stage.mkdir(parents=True)
                self.redirect_directory(stage / dirname, active)
                with patch.object(module, "ROOT", root):
                    with self.assertRaisesRegex(ValueError, "redirect"):
                        module.prepare_plugins(source, stage, profiles=[profile])
                self.assertEqual(list(active.iterdir()), [active / "sentinel.txt"])
                self.assertEqual((active / "sentinel.txt").read_text(), "preserve active tree")
                self.assertFalse((stage / "plugins-manifest.json").exists())

    def test_redirected_nested_lift_output_rejects_before_lifter_launch(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _, profile = self.fixture(root)
            stage, manifest = self.prepare(module, root, source, [profile])
            active = root / "build/game/spu_lifted/spulight"
            active.mkdir(parents=True)
            (active / "spu_recomp.c").write_text("preserve SpuLight")
            (stage / "lifted").mkdir()
            self.redirect_directory(stage / "lifted/wws_plugin_00011080", active)
            with patch.object(module, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "redirect"):
                    module.lift_plugins(stage, manifest)
            self.assertEqual((active / "spu_recomp.c").read_text(), "preserve SpuLight")
            self.assertFalse((stage / "logs").exists())

    def test_real_lifter_retains_entry_and_reports_observed_pc_coverage(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, _, profile = self.fixture(root)
            profile["observed_pcs"] = [0x6C30]
            stage, manifest = self.prepare(module, root, source, [profile])
            with patch.object(module, "ROOT", root):
                report = module.lift_plugins(stage, manifest)
            self.assertEqual(report[0]["unsupported"], 0)
            self.assertEqual(report[0]["covered_pcs"], [0x6C30])
            self.assertTrue((stage / "lifted/wws_plugin_00011080/spu_recomp.c").is_file())
            self.assertTrue((stage / "logs/wws_plugin_00011080-lift.log").is_file())
            self.assertTrue((stage / "lift-report.json").is_file())


if __name__ == "__main__":
    unittest.main()
