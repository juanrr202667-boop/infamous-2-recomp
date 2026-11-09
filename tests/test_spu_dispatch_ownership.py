"""Compare the actual musttail dispatcher with the canonical ownership lookup.

Only synthetic registry entries run. A full-resolver boundary spy records when
the fast path delegates; it does not emulate the resolver's kernel services.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPU = ROOT / 'tools/ps3recomp/runtime/spu'


def _function(source, signature):
    start = source.index(signature + '\n{')
    depth = 0
    tokens = re.compile(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]', re.S)
    for token in tokens.finditer(source, source.index('{', start)):
        if token.group() == '{':
            depth += 1
        elif token.group() == '}':
            depth -= 1
            if not depth:
                return source[start:token.end()]
    raise AssertionError('unterminated production function')


@pytest.fixture(scope='module')
def ownership_probe(tmp_path_factory):
    bundled = ROOT / 'tools/llvm/bin/clang-cl.exe'
    compiler = (os.environ.get('I2_CLANG_CL') or
                (str(bundled) if bundled.is_file() else None) or shutil.which('clang-cl'))
    assert compiler, 'verified clang-cl is required for the musttail contract'
    folder = tmp_path_factory.mktemp('spu-dispatch-ownership')
    source = (SPU / 'spu_channels.c').read_text(encoding='utf-8')
    registry = source[source.index('typedef void (*spu_fn)'):source.index('/* Which image registered')]
    lookup = _function(source, 'spu_lifted_fn spu_lifted_lookup(const spu_context* ctx, uint32_t lsa)')
    fast = _function((SPU / 'spu_dispatch_mt.c').read_text(encoding='utf-8'),
                     'void spu_indirect_branch_mt(spu_context* ctx)')
    prelude = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "spu_context.h"
#include "spu_interp.h"
#define _Thread_local thread_local
typedef void (*spu_dispatch_fn)(spu_context*);
static int got, fallback;
static void base(spu_context*) { got=71; }
static void owner(spu_context*) { got=91; }
static void overlay(spu_context*) { got=92; }
'''
    boundary = r'''
void spu_indirect_branch(spu_context* ctx) {
  ++fallback;
  spu_lifted_fn fn=spu_lifted_lookup(ctx,ctx->pc);
  if (fn) fn(ctx); else got=-1;
}
'''
    driver = r'''
int main(int argc,char** argv) {
  const char* mode=argc>1?argv[1]:"resident-code";
  spu_begin_image(71); spu_register_function(0x4000,base);
  spu_begin_image(91); spu_register_function(0x4000,owner);
  spu_begin_image(92); spu_register_function(0x5000,overlay);
  spu_begin_image(71); spu_register_function(SPURS_TASKSET_PM_SYSCALL_LS,base);
  static spu_context ctx;
  ctx.pc=0x4000; ctx.image_id=71;
  int expected=91, expected_fallback=0;
  if (!strcmp(mode,"resident-task")) ctx.resident_task=91;
  else if (!strcmp(mode,"resident-overlay")) { ctx.resident_ovl=92; ctx.pc=0x5000; expected=92; }
  else if (!strcmp(mode,"base") || !strcmp(mode,"no-owner") || !strcmp(mode,"zero-image")) {
    expected=71;
    if (!strcmp(mode,"no-owner")) {
      ctx.resident_code[0].lsa=0x6000; ctx.resident_code[0].size=0x100;
      ctx.resident_code[0].image_id=91;
    }
    if (!strcmp(mode,"zero-image")) ctx.image_id=0;
  } else if (!strcmp(mode,"miss") || !strcmp(mode,"negative-image")) {
    ctx.pc=0x7770; expected=-1; expected_fallback=1;
    if (!strcmp(mode,"negative-image")) ctx.image_id=-1;
  } else if (!strcmp(mode,"negative-overlay")) {
    ctx.image_id=-1; ctx.resident_ovl=92; ctx.pc=0x5000; expected=92;
  } else if (!strcmp(mode,"wildcard-entry")) {
    spu_begin_image(0); spu_register_function(0x6000,base);
    ctx.pc=0x6000; ctx.image_id=-1; expected=71;
  } else if (!strcmp(mode,"null-lookup")) {
    if (spu_lifted_lookup(NULL,0x4000)!=base) return 3;
    puts("null lookup preserves first registered wildcard match"); return 0;
  } else if (!strcmp(mode,"special-syscall")) {
    ctx.pc=SPURS_TASKSET_PM_SYSCALL_LS; expected=71; expected_fallback=1;
  } else if (!strcmp(mode,"special-image")) {
    ctx.image_id=SPU_RESOLVER_ONLY_IMAGE_ID; ctx.resident_task=91; expected_fallback=1;
  } else {
    ctx.resident_code[0].lsa=0x4000; ctx.resident_code[0].size=0x100;
    ctx.resident_code[0].image_id=91;
    if (!strcmp(mode,"owner-collision")) {
      ctx.resident_task=71; ctx.resident_ovl=71;
    } else if (!strcmp(mode,"owner-unlifted")) {
      ctx.resident_code[0].image_id=93;
      ctx.resident_code[1]=ctx.resident_code[0]; ctx.resident_code[1].image_id=91;
      ctx.resident_task=91; ctx.resident_ovl=92; expected=-1; expected_fallback=1;
    }
  }
  spu_indirect_branch_mt(&ctx);
  printf("mode=%s got=%d expected=%d fallback=%d expected_fallback=%d\n",
         mode,got,expected,fallback,expected_fallback);
  return got==expected && fallback==expected_fallback ? 0 : 1;
}
'''
    probe = folder / 'probe.cpp'
    probe.write_text(prelude + registry + lookup + boundary + fast + driver, encoding='utf-8')
    exe = folder / 'probe.exe'
    built = subprocess.run([compiler, '/nologo', '/O2', '/std:c++17',
                            '/D_CRT_SECURE_NO_WARNINGS', '/I' + str(SPU),
                            str(probe), '/Fe' + str(exe), '/Fo' + str(folder / 'probe.obj')],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    return exe


@pytest.mark.parametrize('mode', [
    'resident-code', 'resident-task', 'resident-overlay', 'owner-collision',
    'owner-unlifted', 'base', 'no-owner', 'miss', 'zero-image', 'negative-image',
    'negative-overlay', 'wildcard-entry', 'null-lookup', 'special-syscall', 'special-image',
])
def test_dispatch_preserves_ownership_and_special_fallbacks(ownership_probe, mode):
    run = subprocess.run([str(ownership_probe), mode], capture_output=True, text=True, timeout=5)
    assert run.returncode == 0, run.stdout + run.stderr
