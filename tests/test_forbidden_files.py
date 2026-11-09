"""Path and content checks for proprietary material and secrets."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.check_forbidden_files import scan_paths


class ForbiddenFileTests(unittest.TestCase):
    def scan(self, files: dict[str, bytes]) -> list[dict]:
        temporary = tempfile.TemporaryDirectory(prefix="i2-forbidden-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        paths = []
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            paths.append(path)
        return scan_paths(paths, root)

    def test_game_assets_and_secrets_are_rejected(self):
        findings = self.scan({
            "gamedata/stream.dat": b"data",
            "BCUS98125/EBOOT.BIN": b"x",
            "capture.iso": b"x",
            "firmware/keys.bin": b"x",
            ".env": b"TOKEN=1",
            "server.pem": b"-----BEGIN RSA PRIVATE KEY-----\n",
        })
        reasons = {finding["path"]: finding["reason"] for finding in findings}
        self.assertIn("gamedata/stream.dat", reasons)
        self.assertIn("capture.iso", reasons)
        self.assertIn(".env", reasons)
        self.assertIn("server.pem", reasons)

    def test_elf_and_self_content_are_rejected_even_without_extension(self):
        findings = self.scan({
            "fixtures/blob": b"\x7fELF" + b"\0" * 32,
            "fixtures/container": b"SCE\0" + b"\0" * 32,
            "fixtures/archive": b"PSARC" + b"\0" * 32,
        })
        self.assertEqual(len(findings), 3)
        self.assertTrue(all(finding["kind"] == "content" for finding in findings))

    def test_synthetic_fixture_and_text_may_describe_a_signature(self):
        findings = self.scan({
            "tests/native/synthetic_recomp/notes.bin": b"\x7fELF" + b"\0" * 32,
            "docs/format.md": b"An ELF starts with \\x7fELF; a key starts with "
                              b"-----BEGIN RSA PRIVATE KEY-----",
            "src/quote.py": b"PEM = '-----BEGIN OPENSSH PRIVATE KEY-----'",
        })
        self.assertEqual(findings, [])

    def test_private_key_in_an_unknown_binary_is_rejected(self):
        findings = self.scan({
            "fixtures/blob": b"\0\0" + b"-----BEGIN OPENSSH PRIVATE KEY-----" + b"\0" * 16,
        })
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["reason"], "OpenSSH private key")


if __name__ == "__main__":
    unittest.main()
