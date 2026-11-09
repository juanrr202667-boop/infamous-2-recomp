"""Extraction rejects mismatched executables before producing SPU wrappers."""
from pathlib import Path
import struct
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rebuild_spu


class SpuJobExtractionTests(unittest.TestCase):
    SPECS = (
        ("first", 0x40, 3, 0xE16801510DB89EFD, 3),
        ("second", 0x50, 4, 0xC3FEF9E3F2847AAF, 4),
    )

    def source(self, root, second=b"defg"):
        data = bytearray(0x60)
        data[:6] = b"\x7fELF\x02\x02"
        data[0x40:0x43] = b"abc"
        data[0x50:0x54] = second
        path = root / "EBOOT.ELF"
        path.write_bytes(data)
        return path

    def test_verified_blobs_keep_zero_base_and_raw_fingerprints(self):
        self.assertTrue(hasattr(rebuild_spu, "prepare_job_images"), "job extraction missing")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            images = root / "images"
            jobs = rebuild_spu.prepare_job_images(self.source(root), images, self.SPECS)
            self.assertEqual(len(jobs), 2)
            for name, _, size, _, _ in self.SPECS:
                elf = (images / (name + ".elf")).read_bytes()
                self.assertEqual(struct.unpack_from(">I", elf, 0x18)[0], 0)
                self.assertEqual(struct.unpack_from(">I", elf, 52 + 8)[0], 0)
                self.assertEqual(struct.unpack_from(">I", elf, 52 + 16)[0], size)
            self.assertEqual((images / "first.bin").read_bytes(), b"abc")

    def test_corrupt_second_blob_rejects_entire_batch_before_writing(self):
        self.assertTrue(hasattr(rebuild_spu, "prepare_job_images"), "job validation missing")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            images = root / "images"
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                rebuild_spu.prepare_job_images(self.source(root, b"BAD!"), images, self.SPECS)
            self.assertFalse(images.exists())

    def test_truncated_executable_rejects_blob(self):
        self.assertTrue(hasattr(rebuild_spu, "prepare_job_images"), "job validation missing")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.source(root)
            source.write_bytes(source.read_bytes()[:0x52])
            with self.assertRaisesRegex(ValueError, "size"):
                rebuild_spu.prepare_job_images(source, root / "images", self.SPECS)

    def test_cache_requires_matching_code_boundary_and_both_outputs(self):
        self.assertTrue(hasattr(rebuild_spu, "job_lift_is_current"), "job lift cache missing")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = {"fingerprint": "054414E2CCD626FF", "size": 1040,
                       "base": 0, "entry": 0, "code_end": 0x3A0}
            (root / "lift-profile.json").write_text(json.dumps(profile))
            (root / "spu_recomp.c").write_text("source")
            self.assertFalse(rebuild_spu.job_lift_is_current(root, profile))
            (root / "spu_recomp.h").write_text("header")
            self.assertTrue(rebuild_spu.job_lift_is_current(root, profile))
            self.assertFalse(rebuild_spu.job_lift_is_current(root, dict(profile, code_end=1040)))

    def test_bink_generated_outputs_are_adopted_without_existing_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            images, lifted = root / "images", root / "lifted"
            images.mkdir()
            (images / "pm_wwsjob.bin").write_bytes(bytes(11904))
            name = "spu_0001_at_0085E380"
            (images / (name + ".elf")).write_bytes(bytes(101596))

            def run(command, **kwargs):
                # Only the external lifter/compiler are replaced. Adoption
                # must create the real destination and copy the real files.
                if "--symbol-prefix" in command and name + "_" in command:
                    stage = Path(command[command.index("-o") + 1])
                    stage.mkdir(parents=True)
                    (stage / "spu_recomp.c").write_text("/* emitted source */\n")
                    (stage / "spu_recomp.h").write_text("/* emitted header */\n")
                return SimpleNamespace(returncode=0, check_returncode=lambda: None)

            with patch.object(rebuild_spu, "ROOT", root), \
                    patch.object(rebuild_spu, "IMAGES", images), \
                    patch.object(rebuild_spu, "LIFTED", lifted), \
                    patch.object(rebuild_spu, "prepare_job_images", return_value=[]), \
                    patch.object(rebuild_spu, "fnv1a64", return_value=0x7117B11EE89D75F0), \
                    patch.object(rebuild_spu.subprocess, "run", side_effect=run):
                rebuild_spu.main()
            self.assertEqual((lifted / name / "spu_recomp.c").read_text(),
                             "/* emitted source */\n")
            self.assertEqual((lifted / name / "spu_recomp.h").read_text(),
                             "/* emitted header */\n")

    def test_policy_is_relifted_before_cached_registry_can_reuse_old_addresses(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            images, lifted = root / "images", root / "lifted"
            images.mkdir()
            (images / "pm_wwsjob.bin").write_bytes(bytes(11904))
            calls = []

            def run(command, **kwargs):
                calls.append(command)
                return SimpleNamespace(returncode=0, check_returncode=lambda: None)

            # External lifter/compiler are intentionally not executed in this test.
            with patch.object(rebuild_spu, "ROOT", root), \
                    patch.object(rebuild_spu, "IMAGES", images), \
                    patch.object(rebuild_spu, "LIFTED", lifted), \
                    patch.object(rebuild_spu, "prepare_job_images", return_value=[]), \
                    patch.object(rebuild_spu.subprocess, "run", side_effect=run):
                rebuild_spu.main()
            policy_lifts = [i for i, command in enumerate(calls)
                            if Path(command[1]).name == "spu_lifter.py"]
            self.assertEqual(len(policy_lifts), 1, "PM must not reuse an unvalidated lift")
            command = calls[policy_lifts[0]]
            self.assertEqual(command[command.index("--base") + 1], "0xA00")
            registry_index = next(i for i, command in enumerate(calls)
                                  if Path(command[1]).name == "build_spu_workloads.py")
            self.assertLess(policy_lifts[0], registry_index)
