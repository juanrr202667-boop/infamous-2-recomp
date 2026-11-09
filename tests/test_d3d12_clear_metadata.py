"""Run the production ordered-clear recorder and inspect its real record type."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/ps3recomp"

DRIVER = r'''
template<class T> auto captured(const T& dr, bool absent, int)
    -> decltype(dr.surface_meta.valid, bool()) {
    const auto& m = dr.surface_meta;
    if (m.clear_flags != 0xF3 || m.clear_depth != 0.375f || m.clear_stencil != 0x63)
        return false;
    if (absent) return !m.valid && !m.color_pitch[0] && !m.clip_w && !m.scissor_w;
    for (unsigned i = 0; i < 4; ++i)
        if (m.color_pitch[i] != 5120 + i * 256 || m.color_offset[i] != 0xAB8000 + i * 0x100000)
            return false;
    return m.valid && m.dma_seen == 0x15 && m.color_dma[0] == 0xFEED0000 &&
        m.color_dma[2] == 0x31415926 && m.zeta_dma == 0xFEED0001 &&
        m.clip_x == 7 && m.clip_y == 11 && m.clip_w == 1280 &&
        m.clip_h == 720 && m.scissor_x == 13 && m.scissor_y == 17 &&
        m.scissor_w == 1200 && m.scissor_h == 680 && m.color_target == 0x13;
}
template<class T> bool captured(const T&, bool, long) { return false; }
int main(int argc, char** argv) {
    bool absent = argc > 1 && strstr(argv[1], "no-state");
    rsx_state state = {};
    for (unsigned i = 0; i < 4; ++i) {
        state.surface_color_offset[i] = 0xAB8000 + i * 0x100000;
        state.surface_color_pitch[i] = 5120 + i * 256;
    }
    state.surface_clip_x = 7; state.surface_clip_y = 11;
    state.surface_clip_w = 1280; state.surface_clip_h = 720;
    state.scissor_x = 13; state.scissor_y = 17;
    state.scissor_w = 1200; state.scissor_h = 680; state.color_target = 0x13;
    state.surface_color_dma[0] = 0xFEED0000; state.surface_color_dma[1] = 0xFEED0001;
    state.surface_color_dma[2] = 0x31415926; state.surface_zeta_dma = 0xFEED0001;
    state.surface_dma_seen = 0x15;
    s_d3d.current_rsx_state = absent ? NULL : &state;
    d3d12_clear(NULL, 0xF3, 0xFF123456, 0.375f, 0x63);
    state.surface_color_pitch[0] = 0;
    state.surface_clip_w = state.scissor_w = 0;
    state.surface_color_dma[0] = state.surface_dma_seen = 0;
    if (s_d3d.draw_count != 1 || !captured(s_d3d.draws[0], absent, 0)) {
        puts("FAIL ordered clear lost flags, region or per-MRT pitch snapshot"); return 1;
    }
    if (argc > 1 && strstr(argv[1], "manifest")) {
        FILE* out = tmpfile();
        if (!out) return 2;
        write_surface_metadata(out, &s_d3d.draws[0]);
        rewind(out);
        char text[2048] = {};
        fread(text, 1, sizeof(text) - 1, out); fclose(out);
        if (!strstr(text, absent ? "surface_valid=0 pitch=0/0/0/0" :
                                  "surface_valid=1 pitch=5120/5376/5632/5888") ||
            !strstr(text, "clear_flags=0xF3 clear_depth=0.375 clear_stencil=0x63") ||
            !strstr(text, absent ? "surface_dma=unobserved dma_seen=0x0" :
                                  "surface_dma=partial dma_seen=0x15") ||
            (!absent && !strstr(text, "color_dma=0xFEED0000/0xFEED0001/0x31415926/0x0 zeta_dma=0xFEED0001")) ||
            (!absent && (!strstr(text, "clip=7,11,1280x720 scissor=13,17,1200x680") ||
                         !strstr(text, "raw_rt=0xAB8000/0xBB8000/0xCB8000/0xDB8000")))) {
            puts("FAIL manifest changed or inferred captured metadata"); return 1;
        }
    }
    puts("PASS ordered clear metadata survives later state changes"); return 0;
}
'''


class ClearMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("I2_CLANG_CL") or shutil.which("clang-cl")
        if not compiler:
            candidate = ROOT / "tools/llvm/bin/clang-cl.exe"
            compiler = str(candidate) if candidate.is_file() else None
        if not compiler:
            raise unittest.SkipTest("clang-cl unavailable; set I2_CLANG_CL")
        spec = importlib.util.spec_from_file_location("present_fixture", ROOT / "tests/test_d3d12_present_gate.py")
        present = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(present)
        backend = (SDK / "libs/video/rsx_d3d12_backend.c").read_text(encoding="utf-8")
        end = backend.index("} D3D12DrawRecord;") + len("} D3D12DrawRecord;")
        start = backend.rfind("typedef struct {", 0, end)
        prelude = present.PRELUDE.replace("/* BACKEND_RECORD */", backend[start:end] +
                                          "\ntypedef D3D12DrawRecord ProbeDraw;\n")
        # Recorder tests own only the classification boundary. The actual
        # recorder and snapshot/writer functions below remain unchanged. This
        # also permits testing an explicitly absent surface state offscreen.
        prelude += "static u32 current_rt_off(u32* w,u32* h,u32* mrt) { " \
                   "if(w)*w=0; if(h)*h=0; memset(mrt,0,3*sizeof(u32)); return 0xAB8000; }\n"
        clear_start = backend.index("static void d3d12_clear(void* ud,")
        clear_end = backend.index("static void d3d12_set_render_target", clear_start)
        pins_start = backend.index("static void vp_vs_release_pins(void)")
        pins_end = backend.index("static void vp_vs_cache_clear(void)", pins_start)
        snapshot = ""
        if "static void record_surface_metadata(" in backend:
            snap_start = backend.index("static void record_surface_metadata(")
            snap_end = backend.index("\n/*", snap_start)
            snapshot = backend[snap_start:snap_end]
        rect_start = backend.index("static D3D12_RECT temporal_clear_rect(")
        rect_end = backend.index("\n}", rect_start) + 2
        snapshot += backend[rect_start:rect_end] + "\n"
        writer = "static void write_surface_metadata(FILE*, const D3D12DrawRecord*) {}\n"
        if "static void write_surface_metadata(" in backend:
            writer_start = backend.index("static void write_surface_metadata(")
            writer_end = backend.index("static void rsx_capture_frame(", writer_start)
            writer = backend[writer_start:writer_end]
        cls.directory = tempfile.TemporaryDirectory(prefix="i2-clear-metadata-")
        cls.addClassCleanup(cls.directory.cleanup)
        directory = Path(cls.directory.name)
        source = directory / "probe.cpp"
        source.write_text(prelude + snapshot + writer + backend[pins_start:pins_end] +
                          backend[clear_start:clear_end] + DRIVER, encoding="utf-8")
        cls.executable = directory / "probe.exe"
        built = subprocess.run([compiler, "/nologo", "/O1", "/D_CRT_SECURE_NO_WARNINGS",
            "/I" + str(SDK / "include"), "/I" + str(SDK / "libs/video"),
            str(source), "/Fe" + str(cls.executable), "/Fo" + str(directory / "probe.obj")],
            capture_output=True, text=True)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def check_capture(self, *arguments):
        env = os.environ.copy()
        for key in ("CLEAR_RGB", "RSX_ACCUM_FRAME", "RT_DISPLAY_BY_SIZE"):
            env.pop(key, None)
        result = subprocess.run([str(self.executable), *arguments], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ordered_clear_preserves_flags_pitch_and_region(self):
        self.check_capture()

    def test_missing_state_is_explicit_and_preserves_clear_arguments(self):
        self.check_capture("no-state")

    def test_manifest_preserves_recorded_values(self):
        self.check_capture("manifest")

    def test_manifest_does_not_infer_absent_state(self):
        self.check_capture("manifest-no-state")
