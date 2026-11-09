"""Exercise trace capacity, merged sleep runs and automatic window closure."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[3]
stage = root / 'tools/ps3recomp'
compiler = os.environ.get('I2_CLANG_CL') or shutil.which('clang-cl')
if not compiler:
    raise SystemExit('clang-cl unavailable')
code = r'''
#include <windows.h>
#include "ps3emu/ps3_ftrace.h"
int main(int argc, char** argv) {
  if (!PS3_FT_ON() || argc != 2) return 2;
  uint64_t t=ps3_ft_now();
  if (argv[1][0]=='c') {
    for (int i=0;i<5;i++) ps3_ft_span(PS3_FT_FLIP_REQ,0,(uint32_t)i,0,t,t);
    ps3_ft_dump();
  } else if (argv[1][0]=='u') {
    ps3_ft_usleep(1,0x1234,t,t+1000);
    ps3_ft_usleep(1,0x1234,t+2000,t+3000);
    ps3_ft_dump();
  } else if (argv[1][0]=='a' && argv[1][1]=='l') {
    for (unsigned i=0;i<2000;i++) ps3_ft_usleep(1, (i&1) ? 0x5678 : 0x1234,
                                                  t+i*1000, t+i*1000+100);
    ps3_ft_dump();
  } else {
    Sleep(30); t=ps3_ft_now(); ps3_ft_span(PS3_FT_FLIP_REQ,0,1,0,t,t);
  }
  return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='i2-ftrace-contract-') as tmp:
    folder = Path(tmp)
    src = folder / 'probe.cpp'
    src.write_text(code)
    exe = folder / 'probe.exe'
    build = subprocess.run([compiler, '/nologo', '/O1', '/I' + str(stage / 'include'), str(src),
                            '/Fe' + str(exe), '/Fo' + str(folder / 'probe.obj')], capture_output=True, text=True)
    if build.returncode:
        raise SystemExit('compile failed\n' + build.stdout + build.stderr)
    for mode in ('capacity', 'usleep', 'alternating', 'automatic'):
        out = folder / (mode + '.txt')
        env = os.environ.copy()
        env['PS3_FTRACE'] = ('0:0.01:' if mode == 'automatic' else '0:10:') + str(out)
        if mode == 'capacity':
            env['PS3_FTRACE_CAP'] = '2'
        else:
            env.pop('PS3_FTRACE_CAP', None)
        run = subprocess.run([str(exe), mode], env=env, capture_output=True, text=True, timeout=5)
        text = out.read_text() if out.is_file() else ''
        ok = (('spans=2 dropped=3' in text) if mode == 'capacity' else
              ('\n2 1 2 1234 ' in text) if mode == 'usleep' else
              (text.count('\n2 1 3e8 1234 ') == 1 and text.count('\n2 1 3e8 5678 ') == 1) if mode == 'alternating' else
              ('spans=0 dropped=0' in text))
        if run.returncode or not ok:
            raise SystemExit(mode + ' failed\n' + text + run.stderr)
        print(mode + ': passed')
