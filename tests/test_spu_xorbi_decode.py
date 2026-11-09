"""Real RI10 XORBI decoder behavior, independently of existing XORI."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/ps3recomp/tools"))
from spu_disasm import spu_decode


class SpuXorbiDecodeTests(unittest.TestCase):
    def test_byte_xor_words_decode_and_preserve_signed_ri10_operands(self):
        cases = (
            (0x4604061E, "xorbi", "$r30, $r12, 16"),
            (0x4608061A, "xorbi", "$r26, $r12, 32"),
            (0x46020F1E, "xorbi", "$r30, $r30, 8"),
            (0x46FFC61E, "xorbi", "$r30, $r12, -1"),
            (0x4680061E, "xorbi", "$r30, $r12, -512"),
            (0x467FC61E, "xorbi", "$r30, $r12, 511"),
            (0x4640061E, "xorbi", "$r30, $r12, 256"),
            (0x46E0061E, "xorbi", "$r30, $r12, -128"),
            (0x4600061E, "xorbi", "$r30, $r12, 0"),
            (0x4604060C, "xorbi", "$r12, $r12, 16"),
            (0x4404061E, "xori", "$r30, $r12, 16"),
            (0x44E0061E, "xori", "$r30, $r12, -128"),
        )
        for word, mnemonic, operands in cases:
            with self.subTest(word=hex(word)):
                decoded = spu_decode(word, 0x9840)
                self.assertEqual(decoded.mnemonic, mnemonic)
                self.assertEqual(decoded.operands, operands)


if __name__ == "__main__":
    unittest.main()
