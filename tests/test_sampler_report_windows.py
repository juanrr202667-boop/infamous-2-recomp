"""Check that sampler reports do not carry old function hits into the next window."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def test_unreported_guest_buckets_are_cleared_between_windows():
    compiler = os.environ.get('I2_CLANG_CL') or shutil.which('clang-cl')
    assert compiler, 'clang-cl is required for this native probe'
    source = (ROOT / 'tools/ps3recomp/runtime/ppu/ppu_loader.cpp').read_text(encoding='utf-8')
    start = source.index('static void samp_report(void)')
    end = source.index('\n/* PS3_SAMPLE_MAIN=1:', start)
    prelude = '''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define SAMP_SLOTS 32
#define SAMP_MODS 16
#define SAMP_TIDS 16
#define SAMP_TIDS2 2
#define SAMP_RVAS 2
static uint32_t s_samp_fn[SAMP_SLOTS], s_tid_id[SAMP_TIDS], s_t2_id[SAMP_TIDS2], s_rva_key[SAMP_RVAS];
static uint64_t s_samp_ct[SAMP_SLOTS], s_samp_total, s_samp_guest;
static char s_mod_name[SAMP_MODS][40];
static uint64_t s_mod_ct[SAMP_MODS], s_tid_ct[SAMP_TIDS], s_t2_guest[SAMP_TIDS2], s_t2_ntdll[SAMP_TIDS2], s_t2_other[SAMP_TIDS2], s_rva_ct[SAMP_RVAS];
'''
    driver = '''
int main() {
  for (unsigned i=0; i<13; ++i) { s_samp_fn[i] = 0x1000 + 4*i; s_samp_ct[i] = 13-i; }
  for (unsigned i=0; i<9; ++i) { snprintf(s_mod_name[i], 40, "module%u", i); s_mod_ct[i] = 9-i; }
  for (unsigned i=0; i<6; ++i) { s_tid_id[i] = i+1; s_tid_ct[i] = 6-i; }
  s_samp_guest = s_samp_total = 91;
  samp_report();
  for (unsigned i=0; i<13; ++i) if (s_samp_ct[i]) return 1;
  for (unsigned i=0; i<9; ++i) if (s_mod_ct[i]) return 2;
  for (unsigned i=0; i<6; ++i) if (s_tid_ct[i]) return 3;
  return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='i2-sampler-window-') as directory:
        folder = Path(directory)
        probe = folder / 'probe.cpp'
        probe.write_text(prelude + source[start:end] + driver, encoding='utf-8')
        exe = folder / 'probe.exe'
        built = subprocess.run([compiler, '/nologo', '/O1', str(probe), '/Fe' + str(exe),
                                '/Fo' + str(folder / 'probe.obj')], capture_output=True, text=True)
        assert built.returncode == 0, built.stdout + built.stderr
        result = subprocess.run([str(exe)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
