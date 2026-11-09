"""The real RI10 decoder must recognize ORBI, preserving its signed operand."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/ps3recomp/tools"))
from spu_disasm import spu_decode


class SpuOrbiDecodeTests(unittest.TestCase):
    def test_real_ri10_orbi_words_decode_without_becoming_unknown_data(self):
        cases = (
            (0x06011D38, "orbi", "$r56, $r58, 4"),
            (0x06FFDD38, "orbi", "$r56, $r58, -1"),
            (0x06801D38, "orbi", "$r56, $r58, -512"),
            (0x067FDD38, "orbi", "$r56, $r58, 511"),
            (0x06401D38, "orbi", "$r56, $r58, 256"),
            (0x06001D38, "orbi", "$r56, $r58, 0"),
            (0x06E01D38, "orbi", "$r56, $r58, -128"),
            (0x06011D3A, "orbi", "$r58, $r58, 4"),
            (0x04011D38, "ori", "$r56, $r58, 4"),
            (0x04E01D38, "ori", "$r56, $r58, -128"),
        )
        for word, mnemonic, operands in cases:
            with self.subTest(word=hex(word)):
                decoded = spu_decode(word, 0x7014)
                self.assertEqual(decoded.mnemonic, mnemonic)
                self.assertEqual(decoded.operands, operands)


if __name__ == "__main__":
    unittest.main()
