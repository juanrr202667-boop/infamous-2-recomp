"""Exercise the production FIFO wrapper and walker with deterministic OS doubles."""
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / 'tools/ps3recomp/libs/video'


def _function(source, signature):
    start = source.index(signature + '\n{')
    opening = source.index('{', start)
    depth = 1
    cursor = opening + 1
    # The FIFO body has braces in comments/strings. Count lexed C tokens only.
    token = re.compile(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]', re.S)
    for match in token.finditer(source, cursor):
        if match.group() == '{':
            depth += 1
        elif match.group() == '}':
            depth -= 1
        if not depth:
            return source[start:match.end()]
    raise AssertionError('unterminated function: ' + signature)


@pytest.fixture(scope='module')
def fifo_probe(tmp_path_factory):
    if os.name != 'nt':
        pytest.skip('Windows OS doubles require the Windows SDK')
    compiler = os.environ.get('I2_CLANG_CL') or shutil.which('clang-cl')
    if not compiler:
        bundled = ROOT / 'tools/llvm/bin/clang-cl.exe'
        compiler = str(bundled) if bundled.is_file() else None
    if not compiler:
        pytest.skip('clang-cl unavailable; set I2_CLANG_CL')
    folder = tmp_path_factory.mktemp('gcm-fifo-profile')
    source = (SDK / 'cellGcmSys.c').read_text(encoding='utf-8')
    wrapper = _function(source, 'void cellGcm_rsx_process_fifo(void)')
    signature = ('static void gcm_rsx_process_fifo_unlocked(gcm_fifo_probe* probe)'
                 if 'gcm_fifo_probe* probe' in source else
                 'static void gcm_rsx_process_fifo_unlocked(void)')
    walker = _function(source, signature)
    header = SDK / 'gcm_fifo_profile.h'
    include = '#include "' + header.as_posix() + '"\n' if header.exists() else ''
    prelude = r'''
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <atomic>
#include <map>
using std::atomic_thread_fence;
using std::memory_order_acquire;
typedef uint32_t u32; typedef uint16_t u16; typedef uint64_t u64; typedef int32_t s32;
static uint64_t clock_ns=1000; static unsigned clock_calls, cpu_calls, writes, methods;
static int enabled=1;
static BOOL fake_qpc(LARGE_INTEGER* n) { ++clock_calls; n->QuadPart=clock_ns; return TRUE; }
static BOOL fake_freq(LARGE_INTEGER* n) { n->QuadPart=1000000000; return TRUE; }
static BOOL fake_cpu(HANDLE, FILETIME* c, FILETIME* e, FILETIME* k, FILETIME* u) {
  clock_ns+=500; /* CPU-query overhead must not be charged to FIFO lock wait. */
  ++cpu_calls; memset(c,0,sizeof(*c)); memset(e,0,sizeof(*e));
  k->dwHighDateTime=u->dwHighDateTime=0; k->dwLowDateTime=cpu_calls*10000;
  u->dwLowDateTime=cpu_calls*20000; return TRUE;
}
static DWORD fake_tid() { return 77; }
static const char* fake_getenv(const char* name) {
  return !strcmp(name,"GCM_FIFO_PROFILE") && enabled ? "1" : NULL;
}
static SRWLOCK s_gcm_fifo_lock=SRWLOCK_INIT, s_ref_pub_lock=SRWLOCK_INIT;
static void fake_acquire(SRWLOCK* p) { clock_ns += p==&s_gcm_fifo_lock ? 100 : 200; }
static void fake_release(SRWLOCK*) {}
#define QueryPerformanceCounter fake_qpc
#define QueryPerformanceFrequency fake_freq
#define GetThreadTimes fake_cpu
#define GetCurrentThreadId fake_tid
#define AcquireSRWLockExclusive fake_acquire
#define ReleaseSRWLockExclusive fake_release
#define getenv fake_getenv
#define PS3_FT_ON() 0
#define PS3_FT_DRAIN 7
#define PS3_FT_FLIP_FIFO 8
#define PS3_FT_SEMA 5
static uint64_t ps3_ft_now() { ++clock_calls; return clock_ns; }
static uint64_t ps3_ft_min_ns() { return 0; }
static void ps3_ft_span(int,unsigned,unsigned,unsigned,uint64_t,uint64_t) {}
static uint64_t s_ft_sema_t0;
static std::map<u32,u32> mem;
static u32 vm_read32(u32 a) { return mem[a]; }
static void vm_write32(u32 a,u32 v) { mem[a]=v; ++writes; clock_ns+=20; }
static u32 s_gcm_context_ea=1, s_gcm_ctx_out_ea=0, s_fifo_getoff=0x100;
static u32 s_last_ring_off, s_fifo_calloff, s_tr_i, s_sema_offset;
static u32 g_rsx_last_reference, g_gcm_fifo_drained_ea;
static int s_gcm_syscall_mode;
#define GCM_CONTROL_GUEST_ADDR 0x8000
#define GCM_LABEL_GUEST_BASE 0x9000
#define GCM_TRACE_N 16
#define GCM_FLIP_MARKER 0xFEAD0100
#define GCM_FIFO_CATCHUP_BYTES 0x100000
#define GCM_RECYCLE_MARGIN 0x100
#define GCM_RECYCLE_SLACK 0x100
#define CELL_GCM_MAX_REPORT_COUNT 16
#define NV4097_SET_SHADER_PROGRAM 0x8E4
#define NV4097_SET_SHADER_CONTROL 0x1D60
static u32 s_tr_off[16],s_tr_w[16],s_current_display_buffer_id,s_flip_request_count;
static int s_flip_pending;
struct rsx_state { u32 shader_program,shader_control; };
struct { u64 timestamp; u32 value; } s_report_data[16];
static void rsx_state_init(rsx_state* s) { memset(s,0,sizeof(*s)); }
static u32 gcm_io2ea(u32 a) { return a; }
static u32 gcm_ea2io(u32 a) { return a; }
static void gcm_ref_publish_one() {}
static void gcm_fifo_bad_branch(const char*,u32,u32) {}
static void gcm_fifo_dump_around(u32) {}
static void gcm_fifo_resync_why(const char*,u32* get,u32 put) { *get=put; }
extern "C" s32 cellGcmSetFlipCommand(u32) { return 0; }
extern "C" void rsx_raise_user_cmd(u32) {}
static void cellGcmQueueUserCommand(u32) {}
static int rsx_live_draw_enabled() { return 0; }
static int rsx_draw_engine_enabled() { return 0; }
static void rsx_live_draw_method(u32,u32) {}
static void rsx_draw_engine_method(u32,u32) {}
static void rsx_process_method(rsx_state*,u32,u32) { ++methods; clock_ns+=50; }
static void gcm_ref_push_at(u32,u32) {}
static void gcm_2d_method(u32,u32,u32) {}
static LARGE_INTEGER s_qpc_freq;
static void ensure_qpc_init() { s_qpc_freq.QuadPart=1000000000; }
static u64 get_timestamp_ns() { return clock_ns; }
extern "C" unsigned long long ps3_ms_now() { return clock_ns/1000000; }
'''
    driver = r'''
int main(int argc,char** argv) {
  const char* mode=argc>1?argv[1]:"empty";
  if (!strcmp(mode,"disabled")) enabled=0;
  if (!strcmp(mode,"no-context")) s_gcm_context_ea=0;
  mem[GCM_CONTROL_GUEST_ADDR]=0x100;
  if (!strcmp(mode,"park")) { mem[GCM_CONTROL_GUEST_ADDR]=0x200; mem[0x100]=0x20000100; }
  if (!strcmp(mode,"work-park")) {
    mem[GCM_CONTROL_GUEST_ADDR]=0x200; mem[0x100]=(1u<<18)|0x100; mem[0x104]=7;
    mem[0x108]=0x20000108;
  }
  if (!strcmp(mode,"same-get")) {
    mem[GCM_CONTROL_GUEST_ADDR]=0x200; mem[0x100]=(1u<<18)|0x100; mem[0x104]=7;
    mem[0x108]=0x20000100;
  }
  cellGcm_rsx_process_fifo();
  if (!enabled) {
    if (writes!=1 || clock_calls || cpu_calls) return 10;
    return 0;
  }
  if (!strcmp(mode,"cadence")) {
    clock_ns=999990000; cellGcm_rsx_process_fifo();
    clock_ns=1000001000;
  } else clock_ns=1000001000;
  if (!strcmp(mode,"work-park")) s_fifo_getoff=0x100;
  cellGcm_rsx_process_fifo();
  fprintf(stdout,"writes=%u methods=%u cpu_calls=%u clocks=%u\n",writes,methods,cpu_calls,clock_calls);
  return 0;
}
'''
    probe = folder / 'probe.cpp'
    probe.write_text(prelude + include + signature + ';\n' + wrapper + '\n' + walker + driver,
                     encoding='utf-8')
    exe = folder / 'probe.exe'
    result = subprocess.run([compiler, '/nologo', '/std:c++17', '/O1', str(probe),
                             '/Fe' + str(exe), '/Fo' + str(folder / 'probe.obj')],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return exe


def _run(exe, mode):
    result = subprocess.run([str(exe), mode], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    reports = [line for line in result.stderr.splitlines() if line.startswith('[gcm-fifo-profile]')]
    return result, [dict(re.findall(r'(\w+)=([^ ]+)', line)) for line in reports]


def test_disabled_drains_without_clock_or_cpu_queries(fifo_probe):
    _, reports = _run(fifo_probe, 'disabled')
    assert reports == []


def test_empty_drains_report_separate_lock_walker_and_tail_time(fifo_probe):
    result, reports = _run(fifo_probe, 'empty')
    assert len(reports) == 1, result.stderr
    report = reports[0]
    assert report['tid'] == '77'
    assert int(report['end_ns']) - int(report['window_ns']) == 1000
    assert report['calls'] == '2'
    assert report['entry_empty'] == '2'
    assert report['iterations'] == '0'
    assert report['lock_wait_ns'] == '200'
    assert report['walker_ns'] == '440'
    assert report['tail_ns'] == '440'
    assert report['empty_walker_ns'] == '440'
    assert report['empty_lock_wait_ns'] == '200'
    assert report['pure_park_walker_ns'] == '0'
    assert report['work_calls'] == '0'
    assert report['work_walker_ns'] == '0'
    assert report['no_context_walker_ns'] == '0'
    assert report['cpu_ns'] == '3000000'
    assert report['last_get_start'] == report['last_get_end'] == '00000100'
    assert report['last_put'] == '00000100'
    assert 'cpu_calls=2' in result.stdout


@pytest.mark.parametrize('mode,pure,work,iterations', [
    ('park', '2', '0', '2'), ('work-park', '0', '2', '4'),
])
def test_pure_park_is_distinct_from_work_before_park(fifo_probe, mode, pure, work, iterations):
    result, reports = _run(fifo_probe, mode)
    assert len(reports) == 1, result.stderr
    assert reports[0]['pure_park'] == pure
    assert reports[0]['work_park'] == work
    assert reports[0]['iterations'] == iterations
    assert reports[0]['last_reason'] == 'jump-park'
    assert reports[0]['empty_walker_ns'] == '0'
    assert reports[0]['pure_park_walker_ns'] == ('440' if mode == 'park' else '0')
    assert reports[0]['work_calls'] == ('0' if mode == 'park' else '2')
    assert reports[0]['work_walker_ns'] == ('0' if mode == 'park' else '540')


def test_wrapped_work_is_not_classified_empty_and_budget_is_clamped(fifo_probe):
    result, reports = _run(fifo_probe, 'same-get')
    assert len(reports) == 1, result.stderr
    report = reports[0]
    assert report['entry_empty'] == '0'
    assert report['pure_park'] == '0'
    assert report['work_calls'] == '2'
    assert int(report['work_walker_ns']) == int(report['walker_ns'])
    assert report['iterations'] == '2097152'
    assert report['last_get_start'] == report['last_get_end'] == '00000100'
    assert 'methods=1048576' in result.stdout


def test_no_context_is_counted_without_fabricating_empty_fifo(fifo_probe):
    result, reports = _run(fifo_probe, 'no-context')
    assert len(reports) == 1, result.stderr
    assert reports[0]['no_context'] == '2'
    assert reports[0]['entry_empty'] == '0'
    assert reports[0]['no_context_walker_ns'] == reports[0]['walker_ns']
    assert reports[0]['last_reason'] == 'no-context'


def test_reporting_waits_for_one_second_and_accumulates_calls(fifo_probe):
    result, reports = _run(fifo_probe, 'cadence')
    assert len(reports) == 1, result.stderr
    assert reports[0]['calls'] == '3'
    assert 'cpu_calls=2' in result.stdout
