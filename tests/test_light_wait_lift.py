"""Exercise the wait-observer callsites through synthetic PPU instructions."""
from pathlib import Path
import os, subprocess, sys, tempfile, unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/ps3recomp/tools'))
from ppu_disasm import decode
from ppu_lifter import HEADER_PREAMBLE, SOURCE_PREAMBLE, PPULifter

@unittest.skipUnless(os.name == 'nt', 'Windows clang-cl fixture')
class LightWaitLift(unittest.TestCase):
    def test_hooks_wrap_only_selected_calls_and_keep_results(self):
        compiler = Path(os.environ.get('I2_CLANG_CL', ROOT / 'tools/llvm/bin/clang-cl.exe'))
        if not compiler.is_file(): self.skipTest('clang-cl unavailable')
        with tempfile.TemporaryDirectory(prefix='i2-light-wait-') as tmp:
            path=Path(tmp); (path/'ppu_recomp.h').write_text(HEADER_PREAMBLE, encoding='utf8')
            pieces=[]
            for addr,target in [(0x424090,0x32c50),(0x32d24,0x3707bc),(0x32d40,0x3707bc)]:
                word=0x48000001 | ((target-addr)&0x03fffffc)
                body=PPULifter().lift_function([decode(word,addr),decode(0x4e800020,addr+4)],addr,addr+8)
                pieces.append('void '+body.name+'(ppu_context* ctx) {\n'+'\n'.join(body.body_lines)+'\n}\n')
            harness=r'''
extern "C" PPU_THREAD_LOCAL void (*g_trampoline_fn)(void*) = nullptr;
static unsigned events[8], count;
extern "C" void i2_light_wait_trace(ppu_context*, unsigned p) { events[count++]=p; }
void func_00032C50(ppu_context* c) { c->gpr[3]=1; }
void func_003707BC(ppu_context* c) { c->gpr[3]=2; }
int main() { ppu_context c{}; func_00424090(&c); if(c.gpr[3]!=1) return 1;
  func_00032D24(&c); if(c.gpr[3]!=2) return 2; func_00032D40(&c);
  return !(count==4 && events[0]==0 && events[1]==3 && events[2]==1 && events[3]==2 && c.gpr[3]==2); }
'''
            src=path/'fixture.cpp'; src.write_text(SOURCE_PREAMBLE+'\nvoid func_00032C50(ppu_context*);\nvoid func_003707BC(ppu_context*);\n'+''.join(pieces)+harness,encoding='utf8')
            exe=path/'fixture.exe'
            p=subprocess.run([str(compiler),'/nologo','/std:c++17','/EHsc','/clang:-fuse-ld=lld',str(src),'/Fe:'+str(exe),'/Fo:'+str(path/'fixture.obj')],cwd=path,capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
            p=subprocess.run([str(exe)],cwd=path,capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stdout+p.stderr)
