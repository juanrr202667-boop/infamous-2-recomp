"""Local input preparation must validate before writing and preserve local work."""
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prepare_local_inputs


class PrepareLocalInputsTests(unittest.TestCase):
    def test_rejects_wrong_elf_before_generating_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            elf = root / "build/game/EBOOT.ELF"
            elf.parent.mkdir(parents=True)
            elf.write_bytes(b"wrong edition")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                prepare_local_inputs.verify_elf(elf, "expected")
            self.assertFalse((root / "build/game/loader").exists())

    def test_policy_slice_uses_verified_offset_and_hash(self):
        policy = b"synthetic policy"
        data = b"padding" + policy + b"tail"
        self.assertEqual(prepare_local_inputs.policy_from_elf(
            data, offset=7, size=len(policy),
            expected_sha256=hashlib.sha256(policy).hexdigest()), policy)
        with self.assertRaisesRegex(ValueError, "policy"):
            prepare_local_inputs.policy_from_elf(
                data, offset=7, size=len(policy), expected_sha256="bad")

    def test_conflict_refuses_entire_batch_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            existing = root / "spu_images/pm_wwsjob.bin"
            existing.parent.mkdir()
            existing.write_bytes(b"developer data")
            planned = {
                root / "loader/EBOOT.functions.json": b"[]\n",
                existing: b"different policy",
            }
            with self.assertRaisesRegex(ValueError, "conflict"):
                prepare_local_inputs.install_outputs(planned)
            self.assertEqual(existing.read_bytes(), b"developer data")
            self.assertFalse((root / "loader").exists())

    def test_check_only_reports_missing_without_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "loader/EBOOT.functions.json"
            self.assertEqual(prepare_local_inputs.install_outputs(
                {target: b"[]\n"}, check_only=True), (0, 1))
            self.assertFalse(target.exists())
            self.assertEqual(prepare_local_inputs.install_outputs(
                {target: b"[]\n"}), (0, 1))
            self.assertEqual(target.read_bytes(), b"[]\n")
            self.assertEqual(prepare_local_inputs.install_outputs(
                {target: b"[]\n"}, check_only=True), (1, 0))


if __name__ == "__main__":
    unittest.main()
