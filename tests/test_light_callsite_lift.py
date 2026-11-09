"""Exercise the two actual direct-call emission sites with synthetic PPU code."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/ps3recomp/tools"))
from ppu_disasm import decode
from ppu_lifter import HEADER_PREAMBLE, SOURCE_PREAMBLE, PPULifter

@unittest.skipUnless(os.name == "nt", "Windows clang-cl fixture")
class LightCallsiteLift(unittest.TestCase):
    def test_direct_sites_preserve_lr_result_and_emit_paired_events(self):
        compiler = Path(os.environ.get("I2_CLANG_CL", ROOT / "tools/llvm/bin/clang-cl.exe"))
        if not compiler.is_file(): self.skipTest("clang-cl unavailable")
        with tempfile.TemporaryDirectory(prefix="i2-light-call-") as tmp:
            p = Path(tmp); (p / "ppu_recomp.h").write_text(HEADER_PREAMBLE, encoding="utf-8")
            pieces = []
            for addr in (0x1DB714, 0x1DB978, 0x1DBA00):
                word = 0x48000001 | ((0x314BC8 - addr) & 0x03FFFFFC)
                body = PPULifter().lift_function([decode(word, addr), decode(0x4E800020, addr+4)], addr, addr+8)
                pieces.append("void " + body.name + "(ppu_context* ctx) {\n" + "\n".join(body.body_lines) + "\n}\n")
            harness = r'''
extern "C" PPU_THREAD_LOCAL void (*g_trampoline_fn)(void*) = nullptr;
static int events[8], n = 0;
extern "C" void i2_light_callsite_trace(ppu_context* c, unsigned phase, unsigned site) {
    events[n++] = (int)phase; events[n++] = (!phase && c->lr != site) ? -1 : (int)site;
}
static void trampoline(void* p) { ((ppu_context*)p)->gpr[4]++; }
void func_00314BC8(ppu_context* c) { c->gpr[3] = 0x1234; c->lr = 0xABCDEF; g_trampoline_fn = trampoline; }
int main() {
    ppu_context c{}; func_001DB714(&c); func_001DB978(&c); func_001DBA00(&c);
    return !(n == 8 && events[0] == 0 && events[1] == 0x1DB718 &&
             events[2] == 1 && events[3] == 0x1DB718 &&
             events[4] == 0 && events[5] == 0x1DB97C &&
             events[6] == 1 && events[7] == 0x1DB97C && c.gpr[3] == 0x1234 && c.gpr[4] == 3);
}
'''
            src = p / "fixture.cpp"
            src.write_text(SOURCE_PREAMBLE + "\nvoid func_00314BC8(ppu_context*);\n" + "\n".join(pieces) + harness, encoding="utf-8")
            exe = p / "fixture.exe"
            r = subprocess.run([str(compiler), "/nologo", "/std:c++17", "/EHsc", "/clang:-fuse-ld=lld", str(src), "/Fe:"+str(exe), "/Fo:"+str(p/"fixture.obj")], cwd=p, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([str(exe)], cwd=p, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, "callsite events or semantics missing")
