"""The targeted scan and virtual call must report without changing guest behavior."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/ps3recomp/tools'))
from ppu_disasm import decode
from ppu_lifter import HEADER_PREAMBLE, SOURCE_PREAMBLE, PPULifter


@unittest.skipUnless(os.name == 'nt', 'Windows clang-cl fixture')
class LightLoopLift(unittest.TestCase):
    def test_loop_and_callback_preserve_registers_and_trampoline(self):
        compiler = Path(os.environ.get('I2_CLANG_CL', ROOT / 'tools/llvm/bin/clang-cl.exe'))
        if not compiler.is_file():
            self.skipTest('clang-cl unavailable')
        with tempfile.TemporaryDirectory(prefix='i2-light-loop-') as tmp:
            p = Path(tmp)
            (p / 'ppu_recomp.h').write_text(HEADER_PREAMBLE, encoding='utf-8')
            code = [decode(0x38630001, 0x315174), decode(0x4E800421, 0x31533C),
                    decode(0x4E800020, 0x315340)]
            body = PPULifter().lift_function(code, 0x314BC8, 0x315344)
            harness = r'''
extern "C" PPU_THREAD_LOCAL void (*g_trampoline_fn)(void*) = nullptr;
static int events[3], n;
extern "C" void i2_light_loop_trace(ppu_context* ctx, unsigned phase) {
    if (phase == 0 && ctx->gpr[3] != 10) events[n++] = -1;
    else if (phase == 1 && ctx->ctr != 0x1234) events[n++] = -2;
    else if (phase == 2 && ctx->gpr[3] != 42) events[n++] = -3;
    else events[n++] = phase;
}
static void trampoline(void* p) { ((ppu_context*)p)->gpr[4]++; }
extern "C" void ps3_indirect_call(ppu_context* c) { c->gpr[3] = 42; g_trampoline_fn = trampoline; }
int main() { ppu_context c{}; c.gpr[3]=10; c.ctr=0x1234; func_00314BC8(&c);
    return !(n == 3 && events[0] == 0 && events[1] == 1 && events[2] == 2 && c.gpr[3] == 42 && c.gpr[4] == 1); }
'''
            source = p / 'fixture.cpp'
            source.write_text(SOURCE_PREAMBLE + '\nvoid ' + body.name + '(ppu_context* ctx) {\n' +
                              '\n'.join(body.body_lines) + '\n}\n' + harness, encoding='utf-8')
            exe = p / 'fixture.exe'
            r = subprocess.run([str(compiler), '/nologo', '/std:c++17', '/EHsc', '/clang:-fuse-ld=lld',
                                str(source), '/Fe:' + str(exe), '/Fo:' + str(p / 'fixture.obj')],
                               cwd=p, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run([str(exe)], cwd=p, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, 'loop/callback observations or guest behavior missing')
