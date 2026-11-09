"""Pause a trace writer after reserving a slot and race it against dump."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parents[3]
header = (root / 'tools/ps3recomp/include/ps3emu/ps3_ftrace.h').read_text()
start = header.index('static inline void ps3_ft_span(')
before, span = header[:start], header[start:]
needle = 'const uint32_t i = __atomic_fetch_add(&s->n, 1, __ATOMIC_RELAXED);'
assert needle in span
span = span.replace(needle, needle + '\n    ftrace_test_after_claim();', 1)
header = before.replace('enum ps3_ft_kind {', 'void ftrace_test_after_claim(void);\nenum ps3_ft_kind {', 1) + span
program = r'''
#include <windows.h>
#include "ps3emu/ps3_ftrace.h"
static HANDLE claimed, release_writer, dump_started;
extern "C" void ftrace_test_after_claim(void) { SetEvent(claimed); WaitForSingleObject(release_writer, INFINITE); }
static DWORD WINAPI writer(void*) { uint64_t t=ps3_ft_now(); ps3_ft_span(PS3_FT_FLIP_REQ,0,7,0,t,t); return 0; }
static DWORD WINAPI dump(void*) { SetEvent(dump_started); ps3_ft_dump(); return 0; }
int main() {
  if (!PS3_FT_ON()) return 2;
  claimed=CreateEventA(NULL,TRUE,FALSE,NULL); release_writer=CreateEventA(NULL,TRUE,FALSE,NULL);
  dump_started=CreateEventA(NULL,TRUE,FALSE,NULL);
  HANDLE w=CreateThread(NULL,0,writer,NULL,0,NULL);
  if (WaitForSingleObject(claimed,3000)!=WAIT_OBJECT_0) return 3;
  HANDLE d=CreateThread(NULL,0,dump,NULL,0,NULL);
  if (WaitForSingleObject(dump_started,3000)!=WAIT_OBJECT_0) return 4;
  DWORD early=WaitForSingleObject(d,250);
  SetEvent(release_writer);
  WaitForSingleObject(w,3000); WaitForSingleObject(d,3000);
  return early==WAIT_TIMEOUT ? 0 : 1;
}
'''
compiler = os.environ.get('I2_CLANG_CL') or shutil.which('clang-cl')
if not compiler:
    raise SystemExit('clang-cl unavailable')
with tempfile.TemporaryDirectory(prefix='i2-ftrace-race-') as temp:
    folder = Path(temp)
    (folder / 'ps3emu').mkdir()
    (folder / 'ps3emu/ps3_ftrace.h').write_text(header)
    (folder / 'probe.cpp').write_text(program)
    exe = folder / 'probe.exe'
    build = subprocess.run([compiler, '/nologo', '/O1', '/I' + str(folder), str(folder / 'probe.cpp'),
                            '/Fe' + str(exe), '/Fo' + str(folder / 'probe.obj')], capture_output=True, text=True)
    if build.returncode:
        raise SystemExit('compile error:\n' + build.stdout + build.stderr)
    env = os.environ.copy()
    env['PS3_FTRACE'] = '0:10:' + str(folder / 'trace.txt')
    run = subprocess.run([str(exe)], env=env, capture_output=True, text=True, timeout=10)
    if run.returncode:
        raise SystemExit('FAIL dump did not wait for an active writer: ' + str(run.returncode))
    if '\n9 0 7 0 ' not in (folder / 'trace.txt').read_text():
        raise SystemExit('FAIL committed event missing from dump')
    print('PASS dump waited for the committed event')
