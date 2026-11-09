"""The replay diagnostic must not compare against a deleted temporary tree."""
from __future__ import annotations

import difflib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import patch_stack  # noqa: E402


class PatchStackDiagnoseTests(unittest.TestCase):
    def make_repo(self, root: Path) -> tuple[Path, list[Path]]:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
        versions = ["alpha\nbeta\ngamma\n", "alpha\nBETA\ngamma\n", "ALPHA\nBETA\ngamma\n"]
        (repo / "sample.txt").write_bytes(versions[0].encode("utf-8"))
        subprocess.run(["git", "-C", str(repo), "add", "sample.txt"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                        "user.email=test@local", "commit", "-m", "base"],
                       check=True, capture_output=True)
        patches = []
        for index in range(2):
            patch = root / f"change{index}.patch"
            patch.write_bytes("".join(difflib.unified_diff(
                versions[index].splitlines(True), versions[index + 1].splitlines(True),
                fromfile="a/sample.txt", tofile="b/sample.txt")).encode("utf-8"))
            patches.append(patch.resolve())
        return repo, patches

    def test_partial_prefix_has_no_false_differing_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, patches = self.make_repo(root)
            subprocess.run(["git", "-C", str(repo), "apply", str(patches[0])],
                           check=True, capture_output=True)
            report = patch_stack.diagnose(repo, patches)
            self.assertEqual(report["applied_prefix"], 1, report)
            self.assertEqual(report["differing_files"], [])
            self.assertIsNone(report["failed_at"])

    def test_real_divergence_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, patches = self.make_repo(root)
            subprocess.run(["git", "-C", str(repo), "apply", str(patches[0])],
                           check=True, capture_output=True)
            (repo / "sample.txt").write_text("custom\n", encoding="utf-8")
            report = patch_stack.diagnose(repo, patches)
            self.assertIsNone(report["applied_prefix"])
            self.assertEqual(report["differing_files"], ["sample.txt"])


if __name__ == "__main__":
    unittest.main()
