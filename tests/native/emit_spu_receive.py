"""Emit one synthetic STOP through the actual SPU lifter for the native test."""
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools/ps3recomp/tools"))
from spu_lifter import SPULifter

instruction = SimpleNamespace(addr=0x20C, raw=0x110, mnemonic="stop", operands="0x110")
lifter = SPULifter()
function = lifter.lift_function([instruction], 0x200, 0x210)
Path(sys.argv[1]).write_text(
    "// Synthetic instruction emitted by the actual lifter.\n"
    "static void lifted_receive(spu_context* ctx) {\n"
    + "\n".join(function.body_lines) + "\n}\n", encoding="utf-8")
instruction.raw = 0x111
function = SPULifter().lift_function([instruction], 0x200, 0x210)
with Path(sys.argv[1]).open("a", encoding="utf-8") as output:
    output.write("static void lifted_try_receive(spu_context* ctx) {\n"
                 + "\n".join(function.body_lines) + "\n}\n")
