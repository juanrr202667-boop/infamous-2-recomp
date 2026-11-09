"""The public file inventory must fail on stale or omitted tracked content."""
import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_publication_manifest  # noqa: E402


class PublicationManifestTests(unittest.TestCase):
    def setUp(self):
        self.files = {"README.md": b"status", "LICENSE": b"license"}
        self.manifest = {
            "effective_patch_count": 58,
            "upstream_revision": "sdk-pin",
            "files_sha256": {
                name: hashlib.sha256(data).hexdigest()
                for name, data in self.files.items()
            },
        }
        self.patches = {"patch_count": 58, "sdk_revision": "sdk-pin"}

    def test_valid_inventory_passes(self):
        self.assertEqual(check_publication_manifest.verify(
            self.manifest, self.files, self.patches), [])

    def test_changed_and_missing_files_fail(self):
        self.files["README.md"] = b"changed"
        del self.files["LICENSE"]
        self.files["unlisted.md"] = b"extra"
        errors = check_publication_manifest.verify(
            self.manifest, self.files, self.patches)
        self.assertTrue(any("hash mismatch" in error for error in errors))
        self.assertTrue(any("missing tracked" in error for error in errors))
        self.assertTrue(any("unlisted tracked" in error for error in errors))

    def test_patch_count_and_revision_must_match(self):
        self.patches["patch_count"] = 57
        self.patches["sdk_revision"] = "another-sdk"
        errors = check_publication_manifest.verify(
            self.manifest, self.files, self.patches)
        self.assertTrue(any("patch count" in error for error in errors))
        self.assertTrue(any("SDK revision" in error for error in errors))

    def test_personal_windows_path_is_rejected(self):
        self.files["README.md"] = b"C:\\Users\\Example\\private"
        self.manifest["files_sha256"]["README.md"] = hashlib.sha256(
            self.files["README.md"]).hexdigest()
        errors = check_publication_manifest.verify(
            self.manifest, self.files, self.patches)
        self.assertTrue(any("personal path" in error for error in errors))

    def test_personal_path_in_batch_file_without_trailing_slash_is_rejected(self):
        self.files["tools/setup.bat"] = b"cd C:\\Users\\Example"
        self.manifest["files_sha256"]["tools/setup.bat"] = hashlib.sha256(
            self.files["tools/setup.bat"]).hexdigest()
        errors = check_publication_manifest.verify(
            self.manifest, self.files, self.patches)
        self.assertTrue(any("personal path" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
