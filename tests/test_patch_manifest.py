"""The ordered patch manifest must stay consistent with the code that applies it."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
import rebuild_ppu  # noqa: E402
import patch_stack  # noqa: E402


class PatchManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = patch_stack.load_manifest()
        self.patches_dir = ROOT / "scripts" / "patches"

    def test_manifest_matches_the_execution_order(self):
        self.assertEqual(self.manifest["patch_count"], len(rebuild_ppu.PATCH_STACK))
        self.assertEqual(
            [entry["name"] for entry in self.manifest["patches"]],
            list(rebuild_ppu.PATCH_STACK))
        self.assertEqual(patch_stack.verify_manifest(self.manifest, self.patches_dir), [])

    def test_manifest_hashes_and_order_are_checked(self):
        broken = copy.deepcopy(self.manifest)
        broken["patches"][0]["sha256"] = "0" * 64
        broken["patches"][1]["order"] = 99
        problems = patch_stack.verify_manifest(broken, self.patches_dir)
        self.assertTrue(any("sha256 mismatch" in problem for problem in problems))
        self.assertTrue(any("non-sequential order" in problem for problem in problems))

    def test_repaired_depth_patch_is_active_in_dependency_order(self):
        active = self.patches_dir / "d3d12-depth-clear-handling.patch"
        self.assertTrue(active.is_file())
        self.assertIn("d3d12-depth-clear-handling.patch", rebuild_ppu.PATCH_STACK)
        # Preserve the historical dependency order while allowing later fixes
        # to be appended (for example, the SPU overlay lookup correction).
        depth_index = rebuild_ppu.PATCH_STACK.index(active.name)
        self.assertEqual(
            rebuild_ppu.PATCH_STACK[depth_index:depth_index + 5],
            ("d3d12-depth-clear-handling.patch",
             "rsx-fp-color-input-clamp.patch",
             "rsx-fp-pack-unpack.patch",
             "rsx-fp-predicated-write.patch",
             "rsx-fp-bx2-texture.patch"))
        self.assertEqual(active.read_text(encoding="utf-8").count("diff --git"), 2)


if __name__ == "__main__":
    unittest.main()
