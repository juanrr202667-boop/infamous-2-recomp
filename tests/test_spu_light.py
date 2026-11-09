"""WWS overlays preserve the verified load address and startup entry."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rebuild_spu_light


class SpuLightExtractionTests(unittest.TestCase):
    def source(self, root):
        raw = bytearray(0x80)
        for offset, value in {0x10: 0x40, 0x14: len(raw),
                              0x20: 0xC0DEC0DE, 0x28: 0x4000}.items():
            struct.pack_into(">I", raw, offset, value)
        data = bytearray(0x100)
        data[:6] = b"\x7fELF\x02\x02"
        data[0x80:] = raw
        path = root / "source.elf"
        path.write_bytes(data)
        return path, bytes(raw)

    def prepare(self, source, raw, images):
        return rebuild_spu_light.prepare_overlay(
            source, images, offset=0x80, size=0x80,
            fingerprint=rebuild_spu_light.fnv1a64(raw))

    def test_wrapper_uses_header_load_base_and_startup_offset(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, raw = self.source(root)
            profile = self.prepare(source, raw, root / "images")
            elf = (root / "images/spulight.elf").read_bytes()
            self.assertEqual(profile["base"], 0x4000)
            self.assertEqual(profile["entry"], 0x4040)
            self.assertEqual(struct.unpack_from(">I", elf, 0x18)[0], 0x4040)
            self.assertEqual(struct.unpack_from(">I", elf, 52 + 8)[0], 0x4000)
            self.assertEqual((root / "images/spulight.bin").read_bytes(), raw)

    def test_invalid_headers_rejected_before_any_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for changes in ({0x10: 0}, {0x10: 0x80}, {0x10: 0x41},
                            {0x14: 0x70}, {0x20: 0}, {0x28: 0}):
                with self.subTest(changes=changes):
                    source, raw = self.source(root)
                    mutable = bytearray(raw)
                    for offset, value in changes.items():
                        struct.pack_into(">I", mutable, offset, value)
                    source.write_bytes(source.read_bytes()[:0x80] + mutable)
                    with self.assertRaises(ValueError):
                        self.prepare(source, mutable, root / "images")
                    self.assertFalse((root / "images").exists())

    def test_wrong_executable_fingerprint_rejects_before_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, raw = self.source(root)
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                rebuild_spu_light.prepare_overlay(source, root / "images",
                    offset=0x80, size=0x80, fingerprint=1)
            self.assertFalse((root / "images").exists())

    def test_bss_can_start_after_raw_bytes_without_extending_the_dma_image(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, raw = self.source(root)
            mutable = bytearray(raw)
            struct.pack_into(">II", mutable, 0x14, 0xA0, 0x30)
            source.write_bytes(source.read_bytes()[:0x80] + mutable)
            profile = self.prepare(source, mutable, root / "images")
            self.assertEqual(profile["bss_begin"], 0x40A0)
            self.assertEqual(profile["bss_size"], 0x30)
            self.assertEqual((root / "images/spulight.bin").stat().st_size, 0x80)

    def test_lifter_failure_preserves_active_source_header_and_registry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            images, lifted, logs = root / "images", root / "lifted", root / "requested-logs"
            active = lifted / "spulight"
            images.mkdir()
            (images / "spulight.bin").write_bytes(bytes(0x80))
            active.mkdir(parents=True)
            logs.mkdir()
            for name in ("spu_recomp.c", "spu_recomp.h"):
                (active / name).write_text("previous valid " + name)
            registry = lifted / "jobmods_register.c"
            registry.write_text("previous valid registry")
            before = {path: path.read_bytes() for path in [registry, *active.iterdir()]}
            def fail(command, **kwargs):
                staged = Path(command[command.index("-o") + 1])
                staged.mkdir(parents=True, exist_ok=True)
                (staged / "spu_recomp.c").write_text("incomplete output")
                raise subprocess.CalledProcessError(1, command)
            with patch.object(rebuild_spu_light, "ROOT", root), \
                    patch.object(rebuild_spu_light.subprocess, "run", side_effect=fail):
                with self.assertRaises(subprocess.CalledProcessError):
                    rebuild_spu_light.lift_overlay(images, lifted, logs,
                        {"base": 0x4000, "entry": 0x9400})
            for path, contents in before.items():
                self.assertEqual(path.read_bytes(), contents)
            self.assertTrue((logs / "spulight-lift.log").exists())
            self.assertFalse((root / "logs").exists())
