"""Exercise stable target allocation and pitched seeds without a GPU device."""
from pathlib import Path
import importlib.util
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("linear_targets_compiler", ROOT / "tests/test_rsx_linear_view.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

DRIVER = r'''
#define CINTERFACE
#include <windows.h>
#include <d3d12.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "rsx_linear_catalog.h"
#if __has_include("rsx_d3d12_linear_targets.h")
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint8_t u8;
const GUID IID_ID3D12Resource = {0};
#define MAX_OFF_RTS 32
#define RT_SRV_BASE 97
typedef struct {
    ID3D12Resource* res;
    ID3D12Resource* up;
    u32 off, w, h, dxgi;
    D3D12_RESOURCE_STATES st;
    int used;
} OffRT;
static struct {
    ID3D12Device* device;
    ID3D12GraphicsCommandList* cmd_list;
    ID3D12DescriptorHeap* rt_rtv_heap;
    ID3D12DescriptorHeap* srv_heap;
    u32 srv_inc;
    OffRT off_rt[MAX_OFF_RTS];
} s_d3d;
static rsx_linear_version s_off_rt_views[MAX_OFF_RTS];
static u32 s_off_rt_count;
uint8_t* vm_base;
static unsigned local_calls, main_calls, broken_main;
static u32 local_base = 0x10000u;
static SIZE_T guest_size = 32u * 1024 * 1024;
static PVOID demand_handler;
static unsigned demand_faults, seed_commits, outside_commit_attempts;
u32 cellGcmResolveLocated(int local, u32 offset) {
    if (local != 1) abort();
    ++local_calls; return local_base + offset;
}
u32 cellGcmResolveIO(u32 offset) {
    ++main_calls;
    if (broken_main == 1) return 0;
    return 0x100000u + offset + ((broken_main == 2 && offset >= 0x100000u) ? 0x100000u : 0);
}
/* Keep real Windows allocations, but refuse an attempted commit outside the
 * owned guest arena before it can change unrelated process memory. */
static LPVOID WINAPI seed_virtual_alloc(LPVOID address, SIZE_T bytes, DWORD type, DWORD protect) {
    if (type & MEM_COMMIT) {
        uintptr_t start = (uintptr_t)address, base = (uintptr_t)vm_base;
        if (!address || start < base || bytes > guest_size || start - base > guest_size - bytes) {
            ++outside_commit_attempts; return NULL;
        }
        ++seed_commits;
    }
    return VirtualAlloc(address, bytes, type, protect);
}
#define VirtualAlloc seed_virtual_alloc
#include "rsx_d3d12_linear_targets.h"
#undef VirtualAlloc
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); return 1; } } while (0)
/* Model the runner's first-touch policy using real MEM_RESERVE and a real VEH.
 * Unlike blindly retrying a fault, never turn a committed protected page into a
 * writable one: production readability checks must reject those pages. */
static LONG WINAPI commit_guest_on_touch(EXCEPTION_POINTERS* exception) {
    if (exception->ExceptionRecord->ExceptionCode == EXCEPTION_ACCESS_VIOLATION) {
        uintptr_t fault = (uintptr_t)exception->ExceptionRecord->ExceptionInformation[1];
        uintptr_t base = (uintptr_t)vm_base;
        if (vm_base && fault >= base && fault - base < guest_size) {
            MEMORY_BASIC_INFORMATION info = {0};
            if (VirtualQuery((void*)fault, &info, sizeof(info)) != sizeof(info) ||
                info.AllocationBase != vm_base || info.State != MEM_RESERVE)
                return EXCEPTION_CONTINUE_SEARCH;
            void* page = (void*)(fault & ~(uintptr_t)0xFFFF);
            if (VirtualAlloc(page, 0x10000, MEM_COMMIT, PAGE_READWRITE)) {
                ++demand_faults; return EXCEPTION_CONTINUE_EXECUTION;
            }
        }
    }
    return EXCEPTION_CONTINUE_SEARCH;
}
typedef struct {
    ID3D12Resource api;
    D3D12_RESOURCE_DESC desc;
    unsigned char* bytes;
    unsigned released, mapped;
    D3D12_RESOURCE_STATES initial;
} fake_resource;
static fake_resource allocated[2 * MAX_OFF_RTS + 8];
static unsigned creates, releases, copies, barriers, rtvs, srvs, bad;
static unsigned fail_create, fail_map, null_map;
static unsigned event_clock, last_copy;
static ULONG STDMETHODCALLTYPE release_resource(ID3D12Resource* api) {
    fake_resource* r = (fake_resource*)api;
    if (r->released++) ++bad;
    ++releases; free(r->bytes); r->bytes = NULL; return 0;
}
static HRESULT STDMETHODCALLTYPE map_resource(ID3D12Resource* api, UINT sub, const D3D12_RANGE* range, void** out) {
    fake_resource* r = (fake_resource*)api;
    if (sub || !range || range->Begin || range->End || r->desc.Dimension != D3D12_RESOURCE_DIMENSION_BUFFER) ++bad;
    if (fail_map) return E_FAIL;
    r->mapped = 1; *out = null_map ? NULL : r->bytes; return S_OK;
}
static void STDMETHODCALLTYPE unmap_resource(ID3D12Resource* api, UINT sub, const D3D12_RANGE* range) {
    fake_resource* r = (fake_resource*)api;
    if (!r->mapped || sub) ++bad;
    r->mapped = 0;
}
static ID3D12ResourceVtbl resource_table;
static HRESULT STDMETHODCALLTYPE create_resource(ID3D12Device* device, const D3D12_HEAP_PROPERTIES* hp,
        D3D12_HEAP_FLAGS flags, const D3D12_RESOURCE_DESC* desc,
        D3D12_RESOURCE_STATES initial, const D3D12_CLEAR_VALUE* clear, REFIID iid, void** out) {
    ++creates; *out = NULL;
    if (creates == fail_create) return E_OUTOFMEMORY;
    fake_resource* r = &allocated[creates - 1];
    r->api.lpVtbl = &resource_table; r->desc = *desc; r->initial = initial;
    if (flags || desc->DepthOrArraySize != 1 || desc->MipLevels != 1 || desc->SampleDesc.Count != 1) ++bad;
    if (desc->Dimension == D3D12_RESOURCE_DIMENSION_TEXTURE2D) {
        if (hp->Type != D3D12_HEAP_TYPE_DEFAULT || initial != D3D12_RESOURCE_STATE_COPY_DEST ||
            desc->Format != DXGI_FORMAT_R8G8B8A8_UNORM ||
            desc->Flags != D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET || desc->Layout != D3D12_TEXTURE_LAYOUT_UNKNOWN) ++bad;
    } else if (desc->Dimension == D3D12_RESOURCE_DIMENSION_BUFFER) {
        if (hp->Type != D3D12_HEAP_TYPE_UPLOAD || initial != D3D12_RESOURCE_STATE_GENERIC_READ ||
            desc->Layout != D3D12_TEXTURE_LAYOUT_ROW_MAJOR || desc->Height != 1 || clear) ++bad;
        r->bytes = (unsigned char*)malloc((size_t)desc->Width);
        if (!r->bytes) return E_OUTOFMEMORY;
        memset(r->bytes, 0xCD, (size_t)desc->Width);
    } else ++bad;
    *out = &r->api; return S_OK;
}
static UINT STDMETHODCALLTYPE increment(ID3D12Device* device, D3D12_DESCRIPTOR_HEAP_TYPE type) {
    if (type != D3D12_DESCRIPTOR_HEAP_TYPE_RTV) ++bad;
    return 16;
}
static D3D12_CPU_DESCRIPTOR_HANDLE* STDMETHODCALLTYPE heap_start(ID3D12DescriptorHeap* heap, D3D12_CPU_DESCRIPTOR_HANDLE* out) {
    out->ptr = heap == s_d3d.rt_rtv_heap ? 0x1000 : 0x10000; return out;
}
static void STDMETHODCALLTYPE rtv(ID3D12Device* device, ID3D12Resource* resource,
        const D3D12_RENDER_TARGET_VIEW_DESC* desc, D3D12_CPU_DESCRIPTOR_HANDLE handle) {
    if (handle.ptr != 0x1000u + rtvs * 16u || !resource) ++bad;
    ++rtvs;
}
static void STDMETHODCALLTYPE srv(ID3D12Device* device, ID3D12Resource* resource,
        const D3D12_SHADER_RESOURCE_VIEW_DESC* desc, D3D12_CPU_DESCRIPTOR_HANDLE handle) {
    if (handle.ptr != 0x10000u + (RT_SRV_BASE + srvs) * 32u || !resource || !desc ||
        desc->Format != DXGI_FORMAT_R8G8B8A8_UNORM || desc->ViewDimension != D3D12_SRV_DIMENSION_TEXTURE2D ||
        desc->Shader4ComponentMapping != D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING ||
        desc->Texture2D.MipLevels != 1 || desc->Texture2D.MostDetailedMip) ++bad;
    ++srvs;
}
static void STDMETHODCALLTYPE copy_texture(ID3D12GraphicsCommandList* command,
        const D3D12_TEXTURE_COPY_LOCATION* dst, UINT x, UINT y, UINT z,
        const D3D12_TEXTURE_COPY_LOCATION* src, const D3D12_BOX* box) {
    ++copies; last_copy = ++event_clock;
    fake_resource* d = (fake_resource*)dst->pResource;
    fake_resource* s = (fake_resource*)src->pResource;
    if (x || y || z || box || dst->Type != D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX || dst->SubresourceIndex ||
        src->Type != D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT || src->PlacedFootprint.Offset ||
        src->PlacedFootprint.Footprint.Format != DXGI_FORMAT_R8G8B8A8_UNORM ||
        src->PlacedFootprint.Footprint.Width != d->desc.Width || src->PlacedFootprint.Footprint.Height != d->desc.Height ||
        src->PlacedFootprint.Footprint.Depth != 1 || src->PlacedFootprint.Footprint.RowPitch % 256 ||
        src->PlacedFootprint.Footprint.RowPitch < d->desc.Width * 4 ||
        s->desc.Width < (uint64_t)src->PlacedFootprint.Footprint.RowPitch * d->desc.Height || s->mapped) ++bad;
}
static void STDMETHODCALLTYPE barrier(ID3D12GraphicsCommandList* command, UINT count, const D3D12_RESOURCE_BARRIER* b) {
    barriers += count;
    if (++event_clock != last_copy + 1 || count != 1 ||
        b->Type != D3D12_RESOURCE_BARRIER_TYPE_TRANSITION || !b->Transition.pResource ||
        b->Transition.StateBefore != D3D12_RESOURCE_STATE_COPY_DEST ||
        b->Transition.StateAfter != D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE ||
        b->Transition.Subresource != D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES) ++bad;
}
static int setup(int demand_paging) {
    static ID3D12DeviceVtbl dt;
    static ID3D12DescriptorHeapVtbl ht;
    static ID3D12GraphicsCommandListVtbl ct;
    static ID3D12Device device;
    static ID3D12DescriptorHeap rh, sh;
    static ID3D12GraphicsCommandList command;
    dt.CreateCommittedResource = create_resource; dt.GetDescriptorHandleIncrementSize = increment;
    dt.CreateRenderTargetView = rtv; dt.CreateShaderResourceView = srv;
    ht.GetCPUDescriptorHandleForHeapStart = heap_start;
    ct.CopyTextureRegion = copy_texture; ct.ResourceBarrier = barrier;
    resource_table.Release = release_resource; resource_table.Map = map_resource; resource_table.Unmap = unmap_resource;
    device.lpVtbl = &dt; rh.lpVtbl = sh.lpVtbl = &ht; command.lpVtbl = &ct;
    s_d3d.device = &device; s_d3d.cmd_list = &command;
    s_d3d.rt_rtv_heap = &rh; s_d3d.srv_heap = &sh; s_d3d.srv_inc = 32;
    if (demand_paging) {
        guest_size = (SIZE_T)0x100000000ull;
        local_base = 0xC0000000u;
        vm_base = (uint8_t*)VirtualAlloc(NULL, guest_size, MEM_RESERVE, PAGE_READWRITE);
        if (!vm_base) {
            printf("UNAVAILABLE 4 GiB MEM_RESERVE error=%lu\n", (unsigned long)GetLastError());
            return 77;
        }
        demand_handler = AddVectoredExceptionHandler(1, commit_guest_on_touch);
        CHECK(demand_handler);
    } else {
        vm_base = (uint8_t*)VirtualAlloc(NULL, guest_size, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    }
    CHECK(vm_base); return 0;
}
static int no_effect(rsx_linear_view view) {
    rsx_linear_version previous[MAX_OFF_RTS]; OffRT targets[MAX_OFF_RTS];
    memcpy(previous, s_off_rt_views, sizeof(previous)); memcpy(targets, s_d3d.off_rt, sizeof(targets));
    unsigned old_count = s_off_rt_count, old_copies = copies, old_barriers = barriers, old_rtvs = rtvs, old_srvs = srvs;
    CHECK(off_rt_get_view(&view) == -1);
    CHECK(s_off_rt_count == old_count && !memcmp(previous, s_off_rt_views, sizeof(previous)));
    CHECK(!memcmp(targets, s_d3d.off_rt, sizeof(targets)));
    CHECK(copies == old_copies && barriers == old_barriers && rtvs == old_rtvs && srvs == old_srvs && !bad);
    return 0;
}
int main(int argc, char** argv) {
    if (argc != 2) return 2;
    int initialized = setup(!strncmp(argv[1], "demand-", 7));
    if (initialized) return initialized;
    rsx_linear_view view = {0xAB8000,1280,720,5120,4,0x85,1};
    if (!strcmp(argv[1], "demand-zero") || !strcmp(argv[1], "demand-preserve")) {
        view.offset = 0x011C0000;
        uint8_t* seed = vm_base + 0xC11C0000ull;
        MEMORY_BASIC_INFORMATION info = {0};
        CHECK(VirtualQuery(seed, &info, sizeof(info)) == sizeof(info));
        CHECK(info.State == MEM_RESERVE && info.AllocationBase == vm_base);
        if (!strcmp(argv[1], "demand-preserve")) {
            CHECK(VirtualAlloc(seed, 0x10000, MEM_COMMIT, PAGE_READWRITE) == seed);
            CHECK(VirtualAlloc(seed + 0x380000, 0x10000, MEM_COMMIT, PAGE_READWRITE) == seed + 0x380000);
            const unsigned char first[4] = {11,22,33,44}, last[4] = {0xAA,0xBB,0xCC,0xDD};
            memcpy(seed, first, 4); memcpy(seed + 0x383FFC, last, 4);
            CHECK(VirtualQuery(seed + 0x20000, &info, sizeof(info)) == sizeof(info));
            CHECK(info.State == MEM_RESERVE);
        }
        CHECK(off_rt_get_view(&view) == 0);
        fake_resource* upload = (fake_resource*)s_d3d.off_rt[0].up;
        CHECK(upload && upload->desc.Width == 0x384000 && copies == 1 && barriers == 1);
        for (unsigned i = 0; i < 0x384000; ++i) {
            if (!strcmp(argv[1], "demand-preserve") && (i < 4 || i >= 0x383FFC)) continue;
            CHECK(upload->bytes[i] == 0);
        }
        if (!strcmp(argv[1], "demand-preserve")) {
            const unsigned char first_rgba[4] = {22,33,44,11}, last_rgba[4] = {0xBB,0xCC,0xDD,0xAA};
            const unsigned char first_argb[4] = {11,22,33,44}, last_argb[4] = {0xAA,0xBB,0xCC,0xDD};
            CHECK(!memcmp(upload->bytes, first_rgba, 4));
            CHECK(!memcmp(upload->bytes + 0x383FFC, last_rgba, 4));
            CHECK(!memcmp(seed, first_argb, 4) && !memcmp(seed + 0x383FFC, last_argb, 4));
        }
        CHECK(VirtualQuery(vm_base, &info, sizeof(info)) == sizeof(info) && info.State == MEM_RESERVE);
        CHECK(VirtualQuery(seed + 0x390000, &info, sizeof(info)) == sizeof(info) && info.State == MEM_RESERVE);
        CHECK(VirtualQuery(vm_base + 0xFFFF0000ull, &info, sizeof(info)) == sizeof(info) && info.State == MEM_RESERVE);
        CHECK((demand_faults || seed_commits) && !outside_commit_attempts && local_calls && !main_calls && !bad);
    } else if (!strcmp(argv[1], "demand-protected")) {
        view.offset = 0x011C0000;
        uint8_t* seed = vm_base + 0xC11C0000ull;
        CHECK(VirtualAlloc(seed, 0x390000, MEM_COMMIT, PAGE_READWRITE) == seed);
        DWORD old;
        MEMORY_BASIC_INFORMATION info = {0};
        CHECK(VirtualProtect(seed + 0x20000, 4096, PAGE_NOACCESS, &old));
        CHECK(!no_effect(view));
        CHECK(VirtualQuery(seed + 0x20000, &info, sizeof(info)) == sizeof(info));
        CHECK(info.Protect == PAGE_NOACCESS);
        CHECK(VirtualProtect(seed + 0x20000, 4096, PAGE_READWRITE | PAGE_GUARD, &old));
        CHECK(!no_effect(view));
        CHECK(VirtualQuery(seed + 0x20000, &info, sizeof(info)) == sizeof(info));
        CHECK(info.Protect == (PAGE_READWRITE | PAGE_GUARD));
        CHECK(!demand_faults && !seed_commits && !outside_commit_attempts && !creates);
    } else if (!strcmp(argv[1], "demand-foreign")) {
        uint8_t* foreign = (uint8_t*)VirtualAlloc(NULL, 0x10000, MEM_RESERVE, PAGE_READWRITE);
        CHECK(foreign && foreign != vm_base);
        CHECK(!off_rt_seed_readable(foreign + 4096, 4));
        MEMORY_BASIC_INFORMATION info = {0};
        CHECK(VirtualQuery(foreign, &info, sizeof(info)) == sizeof(info));
        CHECK(info.State == MEM_RESERVE && info.AllocationBase == foreign);
        CHECK(!demand_faults && !outside_commit_attempts && !creates);
        CHECK(VirtualFree(foreign, 0, MEM_RELEASE));
    } else if (!strcmp(argv[1], "demand-bounds")) {
        view.offset = 0x3FFFFFFC; view.width = 2; view.height = 1; view.pitch = 8;
        CHECK(!no_effect(view));
        MEMORY_BASIC_INFORMATION info = {0};
        CHECK(VirtualQuery(vm_base + 0xFFFF0000ull, &info, sizeof(info)) == sizeof(info));
        CHECK(info.State == MEM_RESERVE && info.AllocationBase == vm_base);
        CHECK(!demand_faults && !seed_commits && !outside_commit_attempts && !creates);
    } else if (!strcmp(argv[1], "stable")) {
        CHECK(off_rt_get_view(&view) == 0);
        ID3D12Resource* first = s_d3d.off_rt[0].res;
        ID3D12Resource* upload = s_d3d.off_rt[0].up;
        CHECK(first && upload && s_off_rt_count == 1 && !releases);
        s_off_rt_views[0].complete_at = 0x100000007ull;
        s_d3d.off_rt[0].used = 0;
        CHECK(off_rt_get_view(&view) == 0 && s_d3d.off_rt[0].used);
        CHECK(creates == 2 && copies == 1 && s_off_rt_views[0].complete_at == 0x100000007ull);
        view.height = 180; CHECK(off_rt_get_view(&view) == 1);
        CHECK(s_d3d.off_rt[1].res != first && s_d3d.off_rt[0].res == first && s_d3d.off_rt[0].up == upload);
        CHECK(s_off_rt_views[0].view.height == 720 && s_off_rt_views[1].view.height == 180);
        CHECK(s_off_rt_views[0].complete_at == 0x100000007ull && !s_off_rt_views[1].complete_at);
        CHECK(s_d3d.off_rt[0].st == D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
        CHECK(creates == 4 && copies == 2 && barriers == 2 && rtvs == 2 && srvs == 2 && !releases && !bad);
    } else if (!strcmp(argv[1], "capacity")) {
        view.width = view.height = 1; view.pitch = 4;
        for (unsigned i = 0; i < 32; ++i) {
            view.offset = 0x1000 + 4 * i;
            CHECK(off_rt_get_view(&view) == (int)i);
            s_off_rt_views[i].complete_at = i + 1;
            s_d3d.off_rt[i].used = 0;
        }
        view.offset += 4; CHECK(!no_effect(view)); CHECK(!releases && creates == 64);
        view.offset = 0x1000;
        CHECK(off_rt_get_view(&view) == 0 && s_off_rt_views[0].complete_at == 1 && creates == 64);
    } else if (!strcmp(argv[1], "pitched")) {
        view.offset = 0x2000; view.width = view.height = 2; view.pitch = 16;
        const unsigned char argb[24] = {1,2,3,4, 5,6,7,8, 90,91,92,93,94,95,96,97, 9,10,11,12,13,14,15,16};
        memcpy(vm_base + 0x12000, argb, sizeof(argb));
        CHECK(off_rt_get_view(&view) == 0);
        fake_resource* up = (fake_resource*)s_d3d.off_rt[0].up;
        const unsigned char row0[8] = {2,3,4,1,6,7,8,5};
        const unsigned char row1[8] = {10,11,12,9,14,15,16,13};
        CHECK(up && !memcmp(up->bytes, row0, 8) && !memcmp(up->bytes + 256, row1, 8));
        CHECK(up->desc.Width == 512 && copies == 1 && barriers == 1 && !releases && !bad);
        CHECK(local_calls && !main_calls);
    } else if (!strcmp(argv[1], "main")) {
        view.offset = 0x2000; view.width = view.height = 1; view.pitch = 4; view.location = 2;
        const unsigned char argb[4] = {19,29,39,49}; memcpy(vm_base + 0x102000, argb, 4);
        CHECK(off_rt_get_view(&view) == 0);
        const unsigned char rgba[4] = {29,39,49,19};
        CHECK(!memcmp(((fake_resource*)s_d3d.off_rt[0].up)->bytes, rgba, 4));
        CHECK(main_calls && !local_calls && !bad);
        view.offset += 4; broken_main = 1; CHECK(!no_effect(view));
    } else if (!strcmp(argv[1], "bounds")) {
        CHECK(off_rt_get_view(NULL) == -1);
        view.width = 16385; view.pitch = 65540; CHECK(!no_effect(view));
        view.width = 1; view.height = 16385; view.pitch = 4; CHECK(!no_effect(view));
        view.height = 1; view.location = 0; CHECK(!no_effect(view));
        view.location = 1; view.format_id = 0x86; CHECK(!no_effect(view));
        view.format_id = 0x85; view.bytes_per_pixel = 8; view.pitch = 8; CHECK(!no_effect(view));
        view.bytes_per_pixel = 4; view.pitch = 4; view.offset = 0x100000000ull; CHECK(!no_effect(view));
        view.offset = 0xFFFFFFFCull; view.height = 2; CHECK(!no_effect(view));
        view.offset = 0xFFFEFFFCull; CHECK(!no_effect(view)); /* EA end crosses 4 GiB. */
        CHECK(!creates && !copies && !bad);
    } else if (!strcmp(argv[1], "unreadable")) {
        view.offset = 0x2000; view.width = 1; view.height = 2; view.pitch = 4096;
        DWORD old;
        CHECK(VirtualProtect(vm_base + 0x13000, 4096, PAGE_NOACCESS, &old));
        CHECK(!no_effect(view));
        CHECK(VirtualProtect(vm_base + 0x13000, 4096, PAGE_READWRITE | PAGE_GUARD, &old));
        CHECK(!no_effect(view));
        CHECK(VirtualProtect(vm_base + 0x13000, 4096, PAGE_READONLY, &old));
        CHECK(off_rt_get_view(&view) == 0 && !bad);
    } else if (!strcmp(argv[1], "main-discontinuous")) {
        view.offset = 0xFFFFCull; view.width = 2; view.height = 1; view.pitch = 8; view.location = 2;
        broken_main = 2; CHECK(!no_effect(view)); CHECK(!creates);
    } else if (!strncmp(argv[1], "rollback-", 9)) {
        view.offset = 0x2000; view.width = view.height = 1; view.pitch = 4;
        memset(&s_off_rt_views[0], 0xA5, sizeof(s_off_rt_views[0]));
        if (!strcmp(argv[1], "rollback-texture")) fail_create = 1;
        else if (!strcmp(argv[1], "rollback-upload")) fail_create = 2;
        else if (!strcmp(argv[1], "rollback-map")) fail_map = 1;
        else if (!strcmp(argv[1], "rollback-null-map")) null_map = 1;
        else return 2;
        CHECK(!no_effect(view));
        CHECK(releases == (fail_create == 1 ? 0u : fail_create == 2 ? 1u : 2u));
        CHECK(!allocated[1].mapped);
        fail_create = fail_map = null_map = 0;
        CHECK(off_rt_get_view(&view) == 0 && s_off_rt_count == 1 && !bad);
    } else return 2;
    for (unsigned i = 0; i < s_off_rt_count; ++i) {
        if (s_d3d.off_rt[i].up) s_d3d.off_rt[i].up->lpVtbl->Release(s_d3d.off_rt[i].up);
        if (s_d3d.off_rt[i].res) s_d3d.off_rt[i].res->lpVtbl->Release(s_d3d.off_rt[i].res);
    }
    if (demand_handler) CHECK(RemoveVectoredExceptionHandler(demand_handler));
    VirtualFree(vm_base, 0, MEM_RELEASE);
    CHECK(!bad && !outside_commit_attempts); puts("PASS stable linear target"); return 0;
}
#else
int main(void) { puts("FAIL stable linear target API not implemented"); return 1; }
#endif
'''


class LinearTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = type("TargetCompiler", (fixture.LinearViewTests,), {})
        original = fixture.DRIVER
        fixture.DRIVER = DRIVER
        try:
            compiler.setUpClass()
        finally:
            fixture.DRIVER = original
        cls.addClassCleanup(compiler.doClassCleanups)
        cls.executable = compiler.executable

    def run_case(self, case):
        result = subprocess.run([str(self.executable), case], capture_output=True, text=True)
        if result.returncode == 77 and case.startswith("demand-"):
            self.skipTest(result.stdout + result.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_reserved_guest_seed_uses_runner_first_touch_zero_pages(self):
        self.run_case("demand-zero")

    def test_reserved_guest_seed_preserves_existing_written_pages(self):
        self.run_case("demand-preserve")

    def test_demand_paging_never_relaxes_guard_or_noaccess_pages(self):
        self.run_case("demand-protected")

    def test_seed_never_commits_a_foreign_reservation(self):
        self.run_case("demand-foreign")

    def test_guest_end_overflow_does_not_commit_any_page(self):
        self.run_case("demand-bounds")

    def test_stable_dimensions_and_epoch(self):
        self.run_case("stable")

    def test_capacity_never_evicts(self):
        self.run_case("capacity")

    def test_seed_uses_guest_pitch_and_argb_channels(self):
        self.run_case("pitched")

    def test_main_uses_strict_io_mapping(self):
        self.run_case("main")

    def test_invalid_geometry_and_address_bounds(self):
        self.run_case("bounds")

    def test_checks_all_rows_before_reading(self):
        self.run_case("unreadable")

    def test_rejects_discontinuous_io_pages(self):
        self.run_case("main-discontinuous")

    def test_texture_failure_rolls_back(self):
        self.run_case("rollback-texture")

    def test_upload_failure_releases_texture_and_rolls_back(self):
        self.run_case("rollback-upload")

    def test_map_failure_releases_both_and_rolls_back(self):
        self.run_case("rollback-map")

    def test_successful_null_mapping_unmaps_and_rolls_back(self):
        self.run_case("rollback-null-map")
