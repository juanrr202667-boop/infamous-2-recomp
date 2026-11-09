"""Exercise the production VS cache with compiler/blob doubles, without GPU.

The compiler double tags each shader; selection, hashing and eviction are the
actual backend code. This fixture does not validate shader decoding or pixels.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"

PRELUDE = r'''
#include "rsx_commands.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <climits>
typedef int HRESULT;
#define FAILED(x) ((x) < 0)
struct ID3DBlob;
struct BlobVtable { unsigned (*Release)(ID3DBlob*); };
struct ID3DBlob { BlobVtable* lpVtbl; unsigned tag; bool released; };
static unsigned releases, compiles, compile_tag;
static bool compile_failure;
static unsigned release_blob(ID3DBlob* blob) {
    if (blob->released) std::abort();
    blob->released = true; ++releases; return 0;
}
static BlobVtable vtable = {release_blob};
static HRESULT D3DCompile(const char*, size_t, const char*, void*, void*,
        const char*, const char*, unsigned, unsigned, ID3DBlob** blob, ID3DBlob**) {
    if (compile_failure) return -1;
    *blob = new ID3DBlob{&vtable, compile_tag, false}; ++compiles; return 0;
}
int rsx_vp_decompile(const uint8_t* code, u32, char* hlsl, u32 size) {
    compile_tag = code[0]; std::snprintf(hlsl, size, "vp_c[%u]", compile_tag); return 1;
}
'''

DRIVER = r'''
int main(int argc, char** argv) {
    rsx_state state = {};
    state.vp_ucode_bytes = 32;
    state.vp_ucode[12] = 1; // first instruction terminates the selected program
    s_d3d.vp_ready = 1;
    if (argc > 1 && !std::strcmp(argv[1], "working-set")) {
        for (unsigned round = 0; round < 3; ++round) {
            for (unsigned i = 1; i <= 86; ++i) {
                state.vp_ucode[0] = (u8)i;
                if (vp_get_vs(&state) < 0) return 2;
                if (i % 43 == 0) vp_vs_release_pins();
            }
        }
        if (compiles != 86) {
            std::printf("FAIL expected 86 distinct compiles, got %u\n", compiles);
            return 1;
        }
        std::puts("PASS repeated working set stays cached"); return 0;
    }
    if (argc > 1 && !std::strcmp(argv[1], "collision")) {
        const u8 a[16] = {0x86,0x51,0x98,0x35,0x58,0x77,0xD1,0xBB,0,0,0,0,1,0,0,0};
        const u8 b[16] = {0xDB,0x1F,0x67,0x0F,0,0xC5,0xE5,0x3F,0,0,0,0,1,0,0,0};
        if (vp_hash_ucode(a, 16) != vp_hash_ucode(b, 16)) return 2;
        std::memcpy(state.vp_ucode, a, 16);
        int first = vp_get_vs(&state);
        std::memcpy(state.vp_ucode, b, 16);
        int second = vp_get_vs(&state);
        if (first == second || compiles != 2 ||
                s_d3d.vp_vs[first].identity == s_d3d.vp_vs[second].identity) return 1;
        std::puts("PASS real FNV collision preserves distinct shader identities"); return 0;
    }
    if (argc > 1 && !std::strcmp(argv[1], "hit-pins")) {
        for (unsigned i = 1; i <= 16; ++i) {
            state.vp_ucode[0] = (u8)i; vp_get_vs(&state);
        }
        vp_vs_release_pins();
        state.vp_ucode[0] = 1;
        int first = vp_get_vs(&state);
        ID3DBlob* original = s_d3d.vp_vs[first].vs;
        for (unsigned i = 17; i <= 48; ++i) {
            state.vp_ucode[0] = (u8)i; vp_get_vs(&state);
        }
        if (original->released || s_d3d.vp_vs[first].vs != original) return 1;
        std::puts("PASS cache hit pins the retained shader for the new batch"); return 0;
    }
    if (argc > 1 && !std::strcmp(argv[1], "recycle")) {
        for (unsigned batch = 0; batch < 4; ++batch) {
            int tickets[48]; unsigned identities[48];
            for (unsigned i = 0; i < 48; ++i) {
                state.vp_ucode[0] = (u8)(batch * 48 + i + 1);
                tickets[i] = vp_get_vs(&state);
                identities[i] = (unsigned)s_d3d.vp_vs[tickets[i]].identity;
            }
            for (unsigned i = 0; i < 48; ++i)
                if (s_d3d.vp_vs[tickets[i]].vs->tag != batch * 48 + i + 1 ||
                        s_d3d.vp_vs[tickets[i]].identity != identities[i]) return 1;
            vp_vs_release_pins();
        }
        if (s_d3d.vp_vs_capacity > 128 || releases == 0) return 1;
        unsigned created = compiles;
        vp_vs_cache_clear();
        if (releases != created || s_d3d.vp_vs || s_d3d.vp_vs_n ||
                s_d3d.vp_vs_capacity) return 1;
        std::puts("PASS bounded working set and exact blob cleanup"); return 0;
    }
    if (argc > 1 && !std::strcmp(argv[1], "failed-compile")) {
        state.vp_ucode[0] = 9;
        int first = vp_get_vs(&state);
        ID3DBlob* original = s_d3d.vp_vs[first].vs;
        vp_vs_release_pins();
        state.vp_ucode[0] = 10; compile_failure = true;
        if (vp_get_vs(&state) != -1 || original->released ||
                s_d3d.vp_vs[first].vs != original) return 1;
        std::puts("PASS failed compilation preserves existing cache"); return 0;
    }
    if (argc > 1) {
        state.vp_ucode[0] = 7;
        int first = vp_get_vs(&state);
        state.vp_ucode[16] = 19; // unrelated resident program after END
        int second = vp_get_vs(&state);
        if (first != second || compiles != 1) {
            std::puts("FAIL unrelated store tail recompiles selected program"); return 1;
        }
        std::puts("PASS selected program identity excludes trailing store"); return 0;
    }
    int tickets[48];
    for (unsigned i = 0; i < 48; ++i) {
        state.vp_ucode[0] = (u8)(i + 1);
        tickets[i] = vp_get_vs(&state);
    }
    for (unsigned i = 0; i < 48; ++i) {
        const int slot = tickets[i];
        if (slot < 0 || slot >= s_d3d.vp_vs_n || !s_d3d.vp_vs[slot].vs ||
                s_d3d.vp_vs[slot].vs->released || s_d3d.vp_vs[slot].vs->tag != i + 1) {
            std::printf("FAIL recorded draw %u lost its vertex shader (slot %d)\n", i, slot);
            return 1;
        }
    }
    std::puts("PASS all 48 recorded shader identities retained"); return 0;
}
'''


class VertexCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            candidate = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(candidate) if candidate.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-vertex-cache-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        backend = (SDK / "libs/video/rsx_d3d12_backend.c").read_text(encoding="utf-8")
        decompiler = (SDK / "libs/video/rsx_vp_decompiler.c").read_text(encoding="utf-8")
        entry_end = backend.index("} VPVSEntry;") + len("} VPVSEntry;")
        entry_start = backend.rfind("typedef struct {", 0, entry_end)
        cache_define = re.search(r"^#define VP_VS_CACHE[^\n]*", backend, re.M).group(0).split("/*")[0]
        member_start = re.search(r"^    VPVSEntry\*?\s+vp_vs", backend, re.M).start()
        member_end = backend.index("    VPFPEntry", member_start)
        members = backend[member_start:member_end]
        begin = backend.index("static u32 vp_hash_ucode(")
        end = backend.index("\n/* --- Guest blend state", begin)
        size_begin = decompiler.index("u32 rsx_vp_program_size_instrs(")
        size_end = decompiler.index("\n/* ---- output appender", size_begin)
        state = ("\nstatic struct {\n" + members +
                 "int vp_ready; const rsx_state* current_rsx_state;\n"
                 "struct { u32 raw; } cur_texs[16];\n} s_d3d;\n"
                 "static void compile_vp() { s_d3d.vp_ready = 1; }\n")
        source = (PRELUDE + backend[entry_start:entry_end] + "\n" + cache_define + state +
                  "static u32 rd_le(const u8* p) { return (u32)p[0] | ((u32)p[1]<<8) | "
                  "((u32)p[2]<<16) | ((u32)p[3]<<24); }\n" +
                  decompiler[size_begin:size_end] + backend[begin:end] + DRIVER)
        probe = directory / "probe.cpp"
        probe.write_text(source, encoding="utf-8")
        cls.executable = directory / "probe.exe"
        result = subprocess.run([compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(SDK / "libs/video"), "/I" + str(SDK / "include"),
            str(probe), "/Fe" + str(cls.executable), "/Fo" + str(directory / "probe.obj")],
            capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def invoke(self, *arguments, trace=False):
        env = os.environ.copy()
        for key in tuple(env):
            if key.startswith(("VP_", "RSX_DBG_")):
                env.pop(key)
        if trace:
            env["VP_COMPILE_TRACE"] = "1"
        return subprocess.run([str(self.executable), *arguments], env=env,
                              capture_output=True, text=True)

    def test_recorded_draws_keep_shader_identity_when_cache_fills(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_changes_after_end_do_not_change_selected_program_identity(self):
        result = self.invoke("tail")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_completed_batches_reuse_slots_and_release_every_blob(self):
        result = self.invoke("recycle")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_failed_shader_compilation_preserves_previous_entry(self):
        result = self.invoke("failed-compile")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_distinct_programs_with_real_hash_collision_remain_distinct(self):
        result = self.invoke("collision")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_lookup_hit_protects_shader_from_later_insertions(self):
        result = self.invoke("hit-pins")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_opt_in_trace_records_each_shader_compilation(self):
        result = self.invoke(trace=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sum(line.startswith("[VPTRACE] ") for line in result.stdout.splitlines()), 48)

    def test_repeated_86_shader_working_set_does_not_recompile(self):
        result = self.invoke("working-set")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
