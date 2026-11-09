"""Reject a generated lift that still omits the diagnosed switch cases."""
from pathlib import Path
import sys
import tempfile
import subprocess
import difflib
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rebuild_ppu


class PatchStackTests(unittest.TestCase):
    def make_stack(self, root):
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
        versions = ["alpha\nbeta\ngamma\n", "alpha\nBETA\ngamma\n", "ALPHA\nBETA\ngamma\n"]
        (repo / "sample.txt").write_bytes(versions[0].encode())
        subprocess.run(["git", "-C", str(repo), "add", "sample.txt"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                        "user.email=test@local", "commit", "-m", "baseline"],
                       check=True, capture_output=True)
        patches = []
        for i in range(2):
            p = root / f"change{i}.patch"
            p.write_bytes("".join(difflib.unified_diff(
                versions[i].splitlines(True), versions[i+1].splitlines(True),
                fromfile="a/sample.txt", tofile="b/sample.txt")).encode())
            patches.append(p)
        return repo, patches, versions

    def test_overlapping_stack_is_idempotent_and_preserves_unrelated_file(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, patches, versions = self.make_stack(Path(temp))
            (repo / "notes.txt").write_bytes(b"keep local work")
            try:
                rebuild_ppu.apply_patch_stack(repo, patches)
            except subprocess.CalledProcessError:
                self.fail("Fresh overlapping stack was rejected")
            self.assertEqual((repo / "sample.txt").read_text(), versions[-1])
            try:
                rebuild_ppu.apply_patch_stack(repo, patches)
            except subprocess.CalledProcessError:
                self.fail("Already applied overlapping stack was rejected")
            self.assertEqual((repo / "sample.txt").read_text(), versions[-1])
            self.assertEqual((repo / "notes.txt").read_bytes(), b"keep local work")

    def test_partial_stack_finishes_without_reapplying_old_patch(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, patches, versions = self.make_stack(Path(temp))
            subprocess.run(["git", "-C", str(repo), "apply", str(patches[0])], check=True)
            rebuild_ppu.apply_patch_stack(repo, patches)
            self.assertEqual((repo / "sample.txt").read_text(), versions[-1])

    def test_divergent_patched_file_is_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, patches, _ = self.make_stack(Path(temp))
            custom = b"custom local change\n"
            (repo / "sample.txt").write_bytes(custom)
            with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                rebuild_ppu.apply_patch_stack(repo, patches)
            self.assertEqual((repo / "sample.txt").read_bytes(), custom)


class LiftValidationTests(unittest.TestCase):
    def test_missing_cases_are_rejected_before_adoption(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "ppu_recomp.h").write_text("header")
            (root / "ppu_recomp_000.cpp").write_text(
                "void func_000B4068(ppu_context* ctx) { ps3_indirect_call(ctx); }\n")
            with self.assertRaisesRegex(ValueError, "switch"):
                rebuild_ppu.validate_lift(root)

    def test_cases_in_another_function_do_not_validate_dispatcher(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "ppu_recomp.h").write_text("header")
            (root / "ppu_recomp_000.cpp").write_text(
                "void func_000B4068(ppu_context* ctx) { ps3_indirect_call(ctx); }\n"
                "void func_000C0000(ppu_context* ctx) { " + " ".join(
                    f"case 0x{address:08X}u:" for address in rebuild_ppu.SWITCH_CASES) + " }\n")
            with self.assertRaisesRegex(ValueError, "switch"):
                rebuild_ppu.validate_lift(root)

    def test_complete_dispatcher_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "ppu_recomp.h").write_text("header")
            unit = root / "ppu_recomp_000.cpp"
            unit.write_text("void func_000B4068(ppu_context* ctx) { " + " ".join(
                f"case 0x{address:08X}u:" for address in rebuild_ppu.SWITCH_CASES) + " }\n")
            self.assertEqual(rebuild_ppu.validate_lift(root), [unit])

    def test_windows_encoded_comments_preserve_case_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "ppu_recomp.h").write_text("header")
            unit = root / "ppu_recomp_000.cpp"
            unit.write_bytes(b"/* comment \x97 */\n" + (
                "void func_000B4068(ppu_context* ctx) { " + " ".join(
                    f"case 0x{address:08X}u:" for address in rebuild_ppu.SWITCH_CASES) + " }\n"
                ).encode("ascii"))
            self.assertEqual(rebuild_ppu.validate_lift(root), [unit])
