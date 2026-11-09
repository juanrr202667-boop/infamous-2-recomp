"""Synthetic regression for a switch following a shared return helper."""
from pathlib import Path
import sys
import unittest

TOOLS = Path(__file__).resolve().parents[1] / "tools/ps3recomp/tools"
sys.path.insert(0, str(TOOLS))
from ppu_disasm import decode
from ppu_lifter import discover_jump_tables


class TailJumpTableTests(unittest.TestCase):
    def _discover_with_branch(self, target):
        # The stack restore is reachable only along the early return arm.
        # Treating it as the switch's base definition drops both case labels.
        words = [
            (32 << 26) | (30 << 21) | (2 << 16) | 16,  # lwz r30,16(r2)
            (16 << 26) | (12 << 21) | (2 << 16) | 16, # beq switch
            (58 << 26) | (30 << 21) | (1 << 16) | 224,# ld r30,224(r1)
            (14 << 26) | (1 << 21) | (1 << 16) | 240, # addi r1,r1,240
            (18 << 26) | ((target - 0x10010) & 0x3FFFFFC), # b target
            (32 << 26) | (11 << 21) | (30 << 16) | 32,# lwz r11,32(r30)
            (21 << 26) | (3 << 21) | (9 << 16) | (2 << 11) | (29 << 1),
            (31 << 26) | (9 << 16) | (11 << 11) | (23 << 1),
            (31 << 26) | (11 << 11) | (266 << 1),     # add r0,r0,r11
            (31 << 26) | (9 << 16) | (467 << 1),      # mtctr r0
            0x4E800420,                              # bctr
        ]
        insns = [decode(word, 0x10000 + i * 4) for i, word in enumerate(words)]
        self.assertEqual(insns[4].mnemonic, "b")
        self.assertEqual(insns[8].mnemonic, "add")
        memory = {0x100010: 0x200000, 0x200020: 0x10080,
                  0x10080: 16, 0x10084: 20}
        return discover_jump_tables(insns, memory.get, [0x100000], 0x10000, 0x20000,
                                    [0x10000, 0x10100])

    def test_return_helper_does_not_hide_live_toc_base(self):
        self.assertEqual(self._discover_with_branch(0x13000),
                         {0x10028: [0x10090, 0x10094]})

    def test_return_helper_before_function_is_also_excluded(self):
        self.assertEqual(self._discover_with_branch(0xF000),
                         {0x10028: [0x10090, 0x10094]})

    def test_branch_inside_function_keeps_the_register_clobber(self):
        # A branch outside the scan window can still be inside the function.
        # Its restore must not be discarded as if it were a return helper.
        self.assertEqual(self._discover_with_branch(0x10060), {})


if __name__ == "__main__":
    unittest.main()
