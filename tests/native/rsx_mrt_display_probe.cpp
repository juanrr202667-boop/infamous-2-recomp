// Behavioral fixture: MRT display buffer detection and GPU-side composite.
//
// In inFAMOUS 2, geometry draws bind the registered display buffer (0x003B0000 or
// 0x00734000) as MRT2 (surface_color_offset[2]), while surface_color_offset[0]
// is the offscreen G-buffer target 0x00AB8000.
// This test validates:
// 1. Detection: draws targeting a display buffer via MRT2 must be recognized as
//    targeting display content (has_display = 1).
// 2. Composite: copying from the MRT2 offscreen resource to the swapchain backbuffer
//    via D3D12 GPU CopyTextureRegion correctly transfers rendered pixels.

#define CINTERFACE
#define COBJMACROS
#include <windows.h>
#include <dxgi1_4.h>
#include <d3d12.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <vector>

#include "rsx_d3d12_backend.h"

// Mock display buffer registry for testing detection in isolation
static uint32_t s_mock_display_buffers[2] = { 0x003B0000u, 0x00734000u };

extern "C" int cellGcmOffsetIsDisplay(uint32_t offset)
{
    return (offset == s_mock_display_buffers[0] || offset == s_mock_display_buffers[1]) ? 1 : 0;
}

template<class T> struct Owned {
    T* p = nullptr;
    ~Owned() { if (p) p->lpVtbl->Release(p); }
    T** out() { return &p; }
    void** raw() { return reinterpret_cast<void**>(&p); }
};

static bool checked(HRESULT result, const char* stage)
{
    if (SUCCEEDED(result)) return true;
    std::fprintf(stderr, "FAIL %s: 0x%08lX\n", stage, static_cast<unsigned long>(result));
    return false;
}

static int test_detection(void)
{
    // Mode 1: Legacy direct backbuffer draw (rt_off == 0)
    uint32_t mrt_zero[3] = { 0, 0, 0 };
    if (!rsx_d3d12_draw_targets_display(0, mrt_zero, 0)) {
        std::fprintf(stderr, "FAIL: direct backbuffer draw (rt_off=0) must be detected as display\n");
        return 1;
    }

    // Mode 2: Clear operation must not count as display draw
    if (rsx_d3d12_draw_targets_display(0, mrt_zero, 1)) {
        std::fprintf(stderr, "FAIL: clear operation must not be detected as display draw\n");
        return 1;
    }

    // Mode 3: Pure offscreen draw (rt_off = 0x00AB8000, no MRT)
    if (rsx_d3d12_draw_targets_display(0x00AB8000u, mrt_zero, 0)) {
        std::fprintf(stderr, "FAIL: pure offscreen draw must not be detected as display\n");
        return 1;
    }

    // Mode 4: MRT draw with display buffer 0 (0x003B0000) at MRT2 (index 1 in out_mrt) - duck_fp G-buffer pass
    // Must be rejected to avoid premature presentation of intermediate MRT2 green screen.
    uint32_t mrt_disp0[3] = { 0x00E3C000u, 0x003B0000u, 0 };
    if (rsx_d3d12_draw_targets_display(0x00AB8000u, mrt_disp0, 0)) {
        std::fprintf(stderr, "FAIL: MRT draw with display buffer 0 at MRT2 must NOT be detected as display\n");
        return 1;
    }

    // Mode 5: MRT draw with display buffer 1 (0x00734000) at MRT2 (index 1 in out_mrt)
    uint32_t mrt_disp1[3] = { 0x00E3C000u, 0x00734000u, 0 };
    if (rsx_d3d12_draw_targets_display(0x00AB8000u, mrt_disp1, 0)) {
        std::fprintf(stderr, "FAIL: MRT draw with display buffer 1 at MRT2 must NOT be detected as display\n");
        return 1;
    }

    // Mode 6: MRT draw with display buffer at MRT1 (index 0) or MRT3 (index 2)
    uint32_t mrt_disp_mrt1[3] = { 0x003B0000u, 0, 0 };
    if (rsx_d3d12_draw_targets_display(0x00AB8000u, mrt_disp_mrt1, 0)) {
        std::fprintf(stderr, "FAIL: MRT draw with display buffer at MRT1 must NOT be detected as display\n");
        return 1;
    }
    uint32_t mrt_disp_mrt3[3] = { 0x00E3C000u, 0x00100000u, 0x00734000u };
    if (rsx_d3d12_draw_targets_display(0x00AB8000u, mrt_disp_mrt3, 0)) {
        std::fprintf(stderr, "FAIL: MRT draw with display buffer at MRT3 must NOT be detected as display\n");
        return 1;
    }

    // Mode 7: Single-target draw directly targeting display buffer (final post-processing resolve / UI pass)
    if (!rsx_d3d12_draw_targets_display(0x003B0000u, mrt_zero, 0)) {
        std::fprintf(stderr, "FAIL: single-target draw directly targeting display buffer must be detected as display\n");
        return 1;
    }

    // Mode 8: Explicit surface_color_target == 0x17 (CELL_GCM_SURFACE_TARGET_MRT2) rejection
    if (rsx_d3d12_draw_targets_display_ext(0x003B0000u, mrt_zero, 0, 0x17)) {
        std::fprintf(stderr, "FAIL: draw with surface_color_target == 0x17 must be rejected\n");
        return 1;
    }

    std::printf("PASS: MRT display target detection\n");
    return 0;
}

static int test_warp_composite(void)
{
    Owned<IDXGIFactory4> factory;
    Owned<IDXGIAdapter> adapter;
    Owned<ID3D12Device> device;
    if (!checked(CreateDXGIFactory1(IID_IDXGIFactory4, factory.raw()), "CreateDXGIFactory1") ||
        !checked(factory.p->lpVtbl->EnumWarpAdapter(factory.p, IID_IDXGIAdapter, adapter.raw()), "EnumWarpAdapter") ||
        !checked(D3D12CreateDevice(reinterpret_cast<IUnknown*>(adapter.p), D3D_FEATURE_LEVEL_11_0,
                                   IID_ID3D12Device, device.raw()), "D3D12CreateDevice(WARP)")) {
        return 2;
    }

    const UINT width = 128;
    const UINT height = 72;
    const UINT pitch = (width * 4 + 255) & ~255u;

    // Create command queue, allocator, list
    D3D12_COMMAND_QUEUE_DESC qd = {};
    qd.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
    Owned<ID3D12CommandQueue> queue;
    if (!checked(device.p->lpVtbl->CreateCommandQueue(device.p, &qd, IID_ID3D12CommandQueue, queue.raw()), "CreateCommandQueue"))
        return 2;

    Owned<ID3D12CommandAllocator> allocator;
    if (!checked(device.p->lpVtbl->CreateCommandAllocator(device.p, D3D12_COMMAND_LIST_TYPE_DIRECT,
                                                         IID_ID3D12CommandAllocator, allocator.raw()), "CreateCommandAllocator"))
        return 2;

    Owned<ID3D12GraphicsCommandList> list;
    if (!checked(device.p->lpVtbl->CreateCommandList(device.p, 0, D3D12_COMMAND_LIST_TYPE_DIRECT,
                                                     allocator.p, nullptr, IID_ID3D12GraphicsCommandList, list.raw()), "CreateCommandList"))
        return 2;

    // Resource descriptions
    D3D12_RESOURCE_DESC td = {};
    td.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    td.Width = width;
    td.Height = height;
    td.DepthOrArraySize = 1;
    td.MipLevels = 1;
    td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    td.SampleDesc.Count = 1;
    td.Flags = D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET;

    D3D12_HEAP_PROPERTIES hp_def = {};
    hp_def.Type = D3D12_HEAP_TYPE_DEFAULT;

    // Target 1: Offscreen MRT resource (simulates 0x003B0000)
    Owned<ID3D12Resource> rt_mrt;
    if (!checked(device.p->lpVtbl->CreateCommittedResource(device.p, &hp_def, D3D12_HEAP_FLAG_NONE,
            &td, D3D12_RESOURCE_STATE_RENDER_TARGET, nullptr, IID_ID3D12Resource, rt_mrt.raw()), "Create rt_mrt"))
        return 2;

    // Target 2: Backbuffer resource (simulates swapchain render target)
    Owned<ID3D12Resource> rt_backbuffer;
    if (!checked(device.p->lpVtbl->CreateCommittedResource(device.p, &hp_def, D3D12_HEAP_FLAG_NONE,
            &td, D3D12_RESOURCE_STATE_RENDER_TARGET, nullptr, IID_ID3D12Resource, rt_backbuffer.raw()), "Create rt_backbuffer"))
        return 2;

    // Upload buffer to populate rt_mrt with test color (0x33, 0x77, 0xBB, 0xFF)
    D3D12_HEAP_PROPERTIES hp_up = {};
    hp_up.Type = D3D12_HEAP_TYPE_UPLOAD;
    D3D12_RESOURCE_DESC bd = {};
    bd.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    bd.Width = (UINT64)pitch * height;
    bd.Height = 1;
    bd.DepthOrArraySize = 1;
    bd.MipLevels = 1;
    bd.SampleDesc.Count = 1;
    bd.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;

    Owned<ID3D12Resource> up;
    if (!checked(device.p->lpVtbl->CreateCommittedResource(device.p, &hp_up, D3D12_HEAP_FLAG_NONE,
            &bd, D3D12_RESOURCE_STATE_GENERIC_READ, nullptr, IID_ID3D12Resource, up.raw()), "Create upload"))
        return 2;

    void* mapped = nullptr;
    D3D12_RANGE nr = { 0, 0 };
    if (FAILED(up.p->lpVtbl->Map(up.p, 0, &nr, &mapped)) || !mapped) return 2;
    for (UINT y = 0; y < height; ++y) {
        uint8_t* row = reinterpret_cast<uint8_t*>(mapped) + (size_t)y * pitch;
        for (UINT x = 0; x < width; ++x) {
            row[x * 4 + 0] = 0x33;
            row[x * 4 + 1] = 0x77;
            row[x * 4 + 2] = 0xBB;
            row[x * 4 + 3] = 0xFF;
        }
    }
    D3D12_RANGE wr = { 0, (SIZE_T)pitch * height };
    up.p->lpVtbl->Unmap(up.p, 0, &wr);

    // Initial copy to rt_mrt
    D3D12_RESOURCE_BARRIER b_init = {};
    b_init.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b_init.Transition.pResource = rt_mrt.p;
    b_init.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    b_init.Transition.StateBefore = D3D12_RESOURCE_STATE_RENDER_TARGET;
    b_init.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_DEST;
    list.p->lpVtbl->ResourceBarrier(list.p, 1, &b_init);

    D3D12_TEXTURE_COPY_LOCATION dst_loc = {};
    dst_loc.pResource = rt_mrt.p;
    dst_loc.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    dst_loc.SubresourceIndex = 0;

    D3D12_TEXTURE_COPY_LOCATION src_up = {};
    src_up.pResource = up.p;
    src_up.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
    src_up.PlacedFootprint.Offset = 0;
    src_up.PlacedFootprint.Footprint.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    src_up.PlacedFootprint.Footprint.Width = width;
    src_up.PlacedFootprint.Footprint.Height = height;
    src_up.PlacedFootprint.Footprint.Depth = 1;
    src_up.PlacedFootprint.Footprint.RowPitch = pitch;

    list.p->lpVtbl->CopyTextureRegion(list.p, &dst_loc, 0, 0, 0, &src_up, nullptr);

    b_init.Transition.StateBefore = D3D12_RESOURCE_STATE_COPY_DEST;
    b_init.Transition.StateAfter = D3D12_RESOURCE_STATE_RENDER_TARGET;
    list.p->lpVtbl->ResourceBarrier(list.p, 1, &b_init);

    // NOW execute the composite presentation sequence (rt_mrt -> rt_backbuffer)
    D3D12_RESOURCE_BARRIER b[2] = {};
    b[0].Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b[0].Transition.pResource = rt_mrt.p;
    b[0].Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    b[0].Transition.StateBefore = D3D12_RESOURCE_STATE_RENDER_TARGET;
    b[0].Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_SOURCE;

    b[1].Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b[1].Transition.pResource = rt_backbuffer.p;
    b[1].Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    b[1].Transition.StateBefore = D3D12_RESOURCE_STATE_RENDER_TARGET;
    b[1].Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_DEST;
    list.p->lpVtbl->ResourceBarrier(list.p, 2, b);

    D3D12_TEXTURE_COPY_LOCATION dl = {};
    dl.pResource = rt_backbuffer.p;
    dl.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    dl.SubresourceIndex = 0;

    D3D12_TEXTURE_COPY_LOCATION sl = {};
    sl.pResource = rt_mrt.p;
    sl.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    sl.SubresourceIndex = 0;

    D3D12_BOX box = {};
    box.left = 0; box.top = 0; box.front = 0;
    box.right = width; box.bottom = height; box.back = 1;
    list.p->lpVtbl->CopyTextureRegion(list.p, &dl, 0, 0, 0, &sl, &box);

    b[0].Transition.StateBefore = D3D12_RESOURCE_STATE_COPY_SOURCE;
    b[0].Transition.StateAfter = D3D12_RESOURCE_STATE_RENDER_TARGET;
    b[1].Transition.StateBefore = D3D12_RESOURCE_STATE_COPY_DEST;
    b[1].Transition.StateAfter = D3D12_RESOURCE_STATE_RENDER_TARGET;
    list.p->lpVtbl->ResourceBarrier(list.p, 2, b);

    // Readback buffer to verify rt_backbuffer pixels
    D3D12_HEAP_PROPERTIES hp_rb = {};
    hp_rb.Type = D3D12_HEAP_TYPE_READBACK;
    Owned<ID3D12Resource> rb;
    if (!checked(device.p->lpVtbl->CreateCommittedResource(device.p, &hp_rb, D3D12_HEAP_FLAG_NONE,
            &bd, D3D12_RESOURCE_STATE_COPY_DEST, nullptr, IID_ID3D12Resource, rb.raw()), "Create readback"))
        return 2;

    D3D12_RESOURCE_BARRIER b_rb = {};
    b_rb.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    b_rb.Transition.pResource = rt_backbuffer.p;
    b_rb.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    b_rb.Transition.StateBefore = D3D12_RESOURCE_STATE_RENDER_TARGET;
    b_rb.Transition.StateAfter = D3D12_RESOURCE_STATE_COPY_SOURCE;
    list.p->lpVtbl->ResourceBarrier(list.p, 1, &b_rb);

    D3D12_TEXTURE_COPY_LOCATION rb_dst = {};
    rb_dst.pResource = rb.p;
    rb_dst.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
    rb_dst.PlacedFootprint.Offset = 0;
    rb_dst.PlacedFootprint.Footprint.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    rb_dst.PlacedFootprint.Footprint.Width = width;
    rb_dst.PlacedFootprint.Footprint.Height = height;
    rb_dst.PlacedFootprint.Footprint.Depth = 1;
    rb_dst.PlacedFootprint.Footprint.RowPitch = pitch;

    list.p->lpVtbl->CopyTextureRegion(list.p, &rb_dst, 0, 0, 0, &dl, nullptr);

    b_rb.Transition.StateBefore = D3D12_RESOURCE_STATE_COPY_SOURCE;
    b_rb.Transition.StateAfter = D3D12_RESOURCE_STATE_RENDER_TARGET;
    list.p->lpVtbl->ResourceBarrier(list.p, 1, &b_rb);

    list.p->lpVtbl->Close(list.p);

    ID3D12CommandList* lists[] = { reinterpret_cast<ID3D12CommandList*>(list.p) };
    queue.p->lpVtbl->ExecuteCommandLists(queue.p, 1, lists);

    // Wait for GPU
    Owned<ID3D12Fence> fence;
    device.p->lpVtbl->CreateFence(device.p, 0, D3D12_FENCE_FLAG_NONE, IID_ID3D12Fence, fence.raw());
    queue.p->lpVtbl->Signal(queue.p, fence.p, 1);
    HANDLE event = CreateEventA(nullptr, FALSE, FALSE, nullptr);
    fence.p->lpVtbl->SetEventOnCompletion(fence.p, 1, event);
    WaitForSingleObject(event, INFINITE);
    CloseHandle(event);

    // Verify readback pixels
    void* rb_map = nullptr;
    if (FAILED(rb.p->lpVtbl->Map(rb.p, 0, &nr, &rb_map)) || !rb_map) return 2;
    for (UINT y = 0; y < height; ++y) {
        const uint8_t* row = reinterpret_cast<const uint8_t*>(rb_map) + (size_t)y * pitch;
        for (UINT x = 0; x < width; ++x) {
            if (row[x * 4 + 0] != 0x33 ||
                row[x * 4 + 1] != 0x77 ||
                row[x * 4 + 2] != 0xBB ||
                row[x * 4 + 3] != 0xFF) {
                std::fprintf(stderr, "FAIL: backbuffer pixel (%u,%u) = (0x%02X,0x%02X,0x%02X,0x%02X) != (0x33,0x77,0xBB,0xFF)\n",
                             x, y, row[x * 4 + 0], row[x * 4 + 1], row[x * 4 + 2], row[x * 4 + 3]);
                rb.p->lpVtbl->Unmap(rb.p, 0, nullptr);
                return 1;
            }
        }
    }
    rb.p->lpVtbl->Unmap(rb.p, 0, nullptr);

    std::printf("PASS: WARP GPU composite copy\n");
    return 0;
}

int main(int argc, char** argv)
{
    const char* mode = (argc > 1) ? argv[1] : "all";
    if (std::strcmp(mode, "detection") == 0) {
        return test_detection();
    } else if (std::strcmp(mode, "composite") == 0) {
        return test_warp_composite();
    } else {
        int r = test_detection();
        if (r != 0) return r;
        return test_warp_composite();
    }
}
