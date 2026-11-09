// Real D3D12 display load/writeback with two masked draws. No game data.
// Compilation does not execute this program or authorize a GPU submission.
#define CINTERFACE
#define COBJMACROS
#include <windows.h>
#include <dxgi1_4.h>
#include <d3d12.h>
#include <d3d12sdklayers.h>
#include <d3dcompiler.h>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include "rsx_d3d12_feedback.h"
#include "rsx_d3d12_display_bridge.h"

template<class T> struct Owned {
    T* p = nullptr;
    Owned() = default;
    Owned(const Owned&) = delete;
    Owned& operator=(const Owned&) = delete;
    ~Owned() { if (p) p->lpVtbl->Release(p); }
    void** raw() { return reinterpret_cast<void**>(&p); }
};

struct Event {
    HANDLE value = nullptr;
    ~Event() { if (value) CloseHandle(value); }
};

static bool checked(HRESULT result, const char* stage)
{
    if (SUCCEEDED(result)) return true;
    std::fprintf(stderr, "FAIL %s HRESULT=0x%08lX\n", stage, static_cast<unsigned long>(result));
    return false;
}

static bool resource(ID3D12Device* device, D3D12_HEAP_TYPE heap_type,
        bool is_texture, UINT64 size, D3D12_RESOURCE_STATES state,
        Owned<ID3D12Resource>& output)
{
    D3D12_HEAP_PROPERTIES heap = {};
    heap.Type = heap_type;
    heap.CreationNodeMask = heap.VisibleNodeMask = 1;
    D3D12_RESOURCE_DESC desc = {};
    desc.Dimension = is_texture ? D3D12_RESOURCE_DIMENSION_TEXTURE2D : D3D12_RESOURCE_DIMENSION_BUFFER;
    desc.Width = size;
    desc.Height = desc.DepthOrArraySize = desc.MipLevels = desc.SampleDesc.Count = 1;
    desc.Format = is_texture ? DXGI_FORMAT_R8G8B8A8_UNORM : DXGI_FORMAT_UNKNOWN;
    desc.Layout = is_texture ? D3D12_TEXTURE_LAYOUT_UNKNOWN : D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    desc.Flags = is_texture ? D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET : D3D12_RESOURCE_FLAG_NONE;
    return checked(device->lpVtbl->CreateCommittedResource(device, &heap,
        D3D12_HEAP_FLAG_NONE, &desc, state, nullptr, IID_ID3D12Resource, output.raw()),
        "CreateCommittedResource");
}

static D3D12_TEXTURE_COPY_LOCATION placed(ID3D12Resource* buffer, UINT64 offset)
{
    D3D12_TEXTURE_COPY_LOCATION location = {};
    location.pResource = buffer;
    location.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
    location.PlacedFootprint.Offset = offset;
    location.PlacedFootprint.Footprint.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    location.PlacedFootprint.Footprint.Width = 2;
    location.PlacedFootprint.Footprint.Height = location.PlacedFootprint.Footprint.Depth = 1;
    location.PlacedFootprint.Footprint.RowPitch = 256;
    return location;
}

static D3D12_TEXTURE_COPY_LOCATION subresource(ID3D12Resource* texture)
{
    D3D12_TEXTURE_COPY_LOCATION location = {};
    location.pResource = texture;
    location.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    return location;
}

static void transition(ID3D12GraphicsCommandList* command, ID3D12Resource* texture,
        D3D12_RESOURCE_STATES& state, D3D12_RESOURCE_STATES after)
{
    if (state == after) return;
    D3D12_RESOURCE_BARRIER barrier = {};
    barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    barrier.Transition.pResource = texture;
    barrier.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    barrier.Transition.StateBefore = state;
    barrier.Transition.StateAfter = after;
    command->lpVtbl->ResourceBarrier(command, 1, &barrier);
    state = after;
}

static bool shader(const char* text, const char* profile, Owned<ID3DBlob>& output)
{
    Owned<ID3DBlob> error;
    const HRESULT result = D3DCompile(text, std::strlen(text), "display_bridge_fixture", nullptr,
        nullptr, "main", profile, D3DCOMPILE_ENABLE_STRICTNESS, 0, &output.p, &error.p);
    if (error.p) std::fwrite(error.p->lpVtbl->GetBufferPointer(error.p), 1,
        error.p->lpVtbl->GetBufferSize(error.p), stderr);
    return checked(result, profile);
}

static bool validation(ID3D12InfoQueue* info)
{
    UINT64 errors = 0, warnings = 0;
    const UINT64 count = info->lpVtbl->GetNumStoredMessages(info);
    for (UINT64 index = 0; index < count; ++index) {
        SIZE_T size = 0;
        if (!checked(info->lpVtbl->GetMessage(info, index, nullptr, &size), "GetMessage size")) return false;
        std::vector<unsigned char> storage(size);
        auto* message = reinterpret_cast<D3D12_MESSAGE*>(storage.data());
        if (!checked(info->lpVtbl->GetMessage(info, index, message, &size), "GetMessage")) return false;
        if (message->Severity <= D3D12_MESSAGE_SEVERITY_ERROR) ++errors;
        if (message->Severity == D3D12_MESSAGE_SEVERITY_WARNING) ++warnings;
        if (index < 32) std::fprintf(stderr, "[D3D12-IQ][severity=%d id=%d] %s\n",
            message->Severity, message->ID, message->pDescription);
    }
    std::printf("STATUS validation_messages=%llu errors=%llu warnings=%llu\n",
        static_cast<unsigned long long>(count), static_cast<unsigned long long>(errors),
        static_cast<unsigned long long>(warnings));
    return !errors && !warnings;
}

int main(int argc, char** argv)
{
    bool warp = false, omit_load = false, omit_writeback = false;
    for (int index = 1; index < argc; ++index) {
        if (!std::strcmp(argv[index], "--warp")) warp = true;
        else if (!std::strcmp(argv[index], "--omit-load")) omit_load = true;
        else if (!std::strcmp(argv[index], "--omit-writeback")) omit_writeback = true;
        else { std::fprintf(stderr, "Usage: rsx_display_bridge_gpu_probe.exe [--warp] [--omit-load] [--omit-writeback]\n"); return 2; }
    }
    Owned<ID3D12Debug> debug;
    if (!checked(D3D12GetDebugInterface(IID_ID3D12Debug, debug.raw()), "D3D12GetDebugInterface")) return 2;
    debug.p->lpVtbl->EnableDebugLayer(debug.p);
    std::puts("STATUS debug_layer=enabled");

    Owned<IDXGIFactory4> factory;
    Owned<IDXGIAdapter1> adapter;
    Owned<ID3D12Device> device;
    if (!checked(CreateDXGIFactory1(IID_IDXGIFactory4, factory.raw()), "CreateDXGIFactory1")) return 2;
    if (warp) {
        if (!checked(factory.p->lpVtbl->EnumWarpAdapter(factory.p, IID_IDXGIAdapter1, adapter.raw()), "EnumWarpAdapter") ||
            !checked(D3D12CreateDevice(reinterpret_cast<IUnknown*>(adapter.p), D3D_FEATURE_LEVEL_11_0,
                IID_ID3D12Device, device.raw()), "D3D12CreateDevice WARP")) return 2;
    } else {
        for (UINT index = 0; !device.p; ++index) {
            Owned<IDXGIAdapter1> candidate;
            HRESULT result = factory.p->lpVtbl->EnumAdapters1(factory.p, index, &candidate.p);
            if (result == DXGI_ERROR_NOT_FOUND) break;
            if (!checked(result, "EnumAdapters1")) return 2;
            DXGI_ADAPTER_DESC1 desc = {};
            if (!checked(candidate.p->lpVtbl->GetDesc1(candidate.p, &desc), "GetDesc1")) return 2;
            if (desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) continue;
            if (SUCCEEDED(D3D12CreateDevice(reinterpret_cast<IUnknown*>(candidate.p), D3D_FEATURE_LEVEL_11_0,
                IID_ID3D12Device, device.raw()))) {
                adapter.p = candidate.p; candidate.p = nullptr;
                std::printf("STATUS hardware_adapter=%u vendor=0x%04X device=0x%04X\n", index, desc.VendorId, desc.DeviceId);
            }
        }
        if (!device.p) { std::fprintf(stderr, "FAIL no hardware D3D12 adapter; no silent WARP fallback\n"); return 2; }
    }
    Owned<ID3D12InfoQueue> info;
    if (!checked(device.p->lpVtbl->QueryInterface(device.p, IID_ID3D12InfoQueue, info.raw()), "QueryInterface info queue")) return 2;

    Owned<ID3DBlob> vertex, pixel;
    if (!shader("float4 main(uint id:SV_VertexID):SV_POSITION { float2 p=float2((id<<1)&2,id&2); return float4(p*float2(2,-2)+float2(-1,1),0,1); }", "vs_5_0", vertex) ||
        !shader("Texture2D<float4> previous:register(t0); float4 main(float4 position:SV_POSITION):SV_TARGET { float4 v=previous.Load(int3(uint2(position.xy),0)); return float4(v.r+1.0/255.0,0,0,0); }", "ps_5_0", pixel)) return 2;
    D3D12_DESCRIPTOR_RANGE range = {D3D12_DESCRIPTOR_RANGE_TYPE_SRV, 1, 0, 0, D3D12_DESCRIPTOR_RANGE_OFFSET_APPEND};
    D3D12_ROOT_PARAMETER parameter = {};
    parameter.ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
    parameter.DescriptorTable.NumDescriptorRanges = 1;
    parameter.DescriptorTable.pDescriptorRanges = &range;
    parameter.ShaderVisibility = D3D12_SHADER_VISIBILITY_PIXEL;
    D3D12_ROOT_SIGNATURE_DESC root_desc = {};
    root_desc.NumParameters = 1; root_desc.pParameters = &parameter;
    root_desc.Flags = D3D12_ROOT_SIGNATURE_FLAG_ALLOW_INPUT_ASSEMBLER_INPUT_LAYOUT;
    Owned<ID3DBlob> serialized, serialization_error;
    Owned<ID3D12RootSignature> root;
    if (!checked(D3D12SerializeRootSignature(&root_desc, D3D_ROOT_SIGNATURE_VERSION_1,
            &serialized.p, &serialization_error.p), "D3D12SerializeRootSignature") ||
        !checked(device.p->lpVtbl->CreateRootSignature(device.p, 0, serialized.p->lpVtbl->GetBufferPointer(serialized.p),
            serialized.p->lpVtbl->GetBufferSize(serialized.p), IID_ID3D12RootSignature, root.raw()), "CreateRootSignature")) return 2;
    D3D12_GRAPHICS_PIPELINE_STATE_DESC pipeline_desc = {};
    pipeline_desc.pRootSignature = root.p;
    pipeline_desc.VS = {vertex.p->lpVtbl->GetBufferPointer(vertex.p), vertex.p->lpVtbl->GetBufferSize(vertex.p)};
    pipeline_desc.PS = {pixel.p->lpVtbl->GetBufferPointer(pixel.p), pixel.p->lpVtbl->GetBufferSize(pixel.p)};
    auto& blend = pipeline_desc.BlendState.RenderTarget[0];
    blend.SrcBlend = blend.SrcBlendAlpha = D3D12_BLEND_ONE;
    blend.DestBlend = blend.DestBlendAlpha = D3D12_BLEND_ZERO;
    blend.BlendOp = blend.BlendOpAlpha = D3D12_BLEND_OP_ADD;
    blend.LogicOp = D3D12_LOGIC_OP_NOOP;
    blend.RenderTargetWriteMask = D3D12_COLOR_WRITE_ENABLE_RED;
    pipeline_desc.RasterizerState.FillMode = D3D12_FILL_MODE_SOLID;
    pipeline_desc.RasterizerState.CullMode = D3D12_CULL_MODE_NONE;
    pipeline_desc.RasterizerState.DepthClipEnable = TRUE;
    pipeline_desc.DepthStencilState.DepthFunc = D3D12_COMPARISON_FUNC_ALWAYS;
    pipeline_desc.DepthStencilState.FrontFace = pipeline_desc.DepthStencilState.BackFace =
        {D3D12_STENCIL_OP_KEEP, D3D12_STENCIL_OP_KEEP, D3D12_STENCIL_OP_KEEP, D3D12_COMPARISON_FUNC_ALWAYS};
    pipeline_desc.SampleMask = UINT_MAX;
    pipeline_desc.PrimitiveTopologyType = D3D12_PRIMITIVE_TOPOLOGY_TYPE_TRIANGLE;
    pipeline_desc.NumRenderTargets = 1;
    pipeline_desc.RTVFormats[0] = DXGI_FORMAT_R8G8B8A8_UNORM;
    pipeline_desc.SampleDesc.Count = 1;
    Owned<ID3D12PipelineState> pipeline;
    if (!checked(device.p->lpVtbl->CreateGraphicsPipelineState(device.p, &pipeline_desc,
        IID_ID3D12PipelineState, pipeline.raw()), "CreateGraphicsPipelineState")) return 2;

    Owned<ID3D12DescriptorHeap> rtv_heap, srv_heap;
    D3D12_DESCRIPTOR_HEAP_DESC heap_desc = {};
    heap_desc.Type = D3D12_DESCRIPTOR_HEAP_TYPE_RTV; heap_desc.NumDescriptors = 1;
    if (!checked(device.p->lpVtbl->CreateDescriptorHeap(device.p, &heap_desc, IID_ID3D12DescriptorHeap, rtv_heap.raw()), "CreateDescriptorHeap RTV")) return 2;
    heap_desc.Type = D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV; heap_desc.NumDescriptors = 2;
    heap_desc.Flags = D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE;
    if (!checked(device.p->lpVtbl->CreateDescriptorHeap(device.p, &heap_desc, IID_ID3D12DescriptorHeap, srv_heap.raw()), "CreateDescriptorHeap SRV")) return 2;
    D3D12_CPU_DESCRIPTOR_HANDLE rtv = {}, srv = {};
    D3D12_GPU_DESCRIPTOR_HANDLE gpu_srv = {};
    rtv_heap.p->lpVtbl->GetCPUDescriptorHandleForHeapStart(rtv_heap.p, &rtv);
    srv_heap.p->lpVtbl->GetCPUDescriptorHandleForHeapStart(srv_heap.p, &srv);
    srv_heap.p->lpVtbl->GetGPUDescriptorHandleForHeapStart(srv_heap.p, &gpu_srv);
    const UINT srv_increment = device.p->lpVtbl->GetDescriptorHandleIncrementSize(device.p, D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV);

    Owned<ID3D12CommandQueue> queue;
    Owned<ID3D12CommandAllocator> allocator;
    Owned<ID3D12GraphicsCommandList> command;
    Owned<ID3D12Fence> fence;
    D3D12_COMMAND_QUEUE_DESC queue_desc = {};
    queue_desc.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
    if (!checked(device.p->lpVtbl->CreateCommandQueue(device.p, &queue_desc, IID_ID3D12CommandQueue, queue.raw()), "CreateCommandQueue") ||
        !checked(device.p->lpVtbl->CreateCommandAllocator(device.p, D3D12_COMMAND_LIST_TYPE_DIRECT, IID_ID3D12CommandAllocator, allocator.raw()), "CreateCommandAllocator") ||
        !checked(device.p->lpVtbl->CreateCommandList(device.p, 0, D3D12_COMMAND_LIST_TYPE_DIRECT, allocator.p, pipeline.p,
            IID_ID3D12GraphicsCommandList, command.raw()), "CreateCommandList") ||
        !checked(device.p->lpVtbl->CreateFence(device.p, 0, D3D12_FENCE_FLAG_NONE, IID_ID3D12Fence, fence.raw()), "CreateFence")) return 2;
    Event completed;
    completed.value = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    if (!completed.value) return 2;
    Owned<ID3D12Resource> canonical, display, upload, readback;
    D3D12_RESOURCE_STATES canonical_state = D3D12_RESOURCE_STATE_COPY_DEST;
    D3D12_RESOURCE_STATES display_state = D3D12_RESOURCE_STATE_COPY_DEST;
    if (!resource(device.p, D3D12_HEAP_TYPE_DEFAULT, true, 2, canonical_state, canonical) ||
        !resource(device.p, D3D12_HEAP_TYPE_DEFAULT, true, 2, display_state, display) ||
        !resource(device.p, D3D12_HEAP_TYPE_UPLOAD, false, 1024, D3D12_RESOURCE_STATE_GENERIC_READ, upload) ||
        !resource(device.p, D3D12_HEAP_TYPE_READBACK, false, 1024, D3D12_RESOURCE_STATE_COPY_DEST, readback)) return 2;
    device.p->lpVtbl->CreateRenderTargetView(device.p, display.p, nullptr, rtv);
    unsigned char* mapped = nullptr;
    const D3D12_RANGE no_reads = {0, 0};
    if (!checked(upload.p->lpVtbl->Map(upload.p, 0, &no_reads, reinterpret_cast<void**>(&mapped)), "Map upload")) return 2;
    const unsigned char initial[8] = {10,77,88,255,20,77,88,255};
    const unsigned char display_initial[8] = {100,7,8,9,200,17,18,19};
    std::memset(mapped, 0xCD, 1024);
    std::memcpy(mapped, initial, sizeof(initial));
    std::memcpy(mapped + 512, display_initial, sizeof(display_initial));
    const D3D12_RANGE written = {0, 1024};
    upload.p->lpVtbl->Unmap(upload.p, 0, &written);
    auto upload_source = placed(upload.p, 0), canonical_location = subresource(canonical.p);
    command.p->lpVtbl->CopyTextureRegion(command.p, &canonical_location, 0, 0, 0, &upload_source, nullptr);
    auto display_source = placed(upload.p, 512), display_location = subresource(display.p);
    command.p->lpVtbl->CopyTextureRegion(command.p, &display_location, 0, 0, 0, &display_source, nullptr);
    transition(command.p, display.p, display_state, D3D12_RESOURCE_STATE_RENDER_TARGET);
    rsx_d3d12_display_bridge_context bridge = {};
    bridge.command_list = command.p; bridge.display = display.p;
    bridge.width = 2; bridge.height = 1; bridge.format = DXGI_FORMAT_R8G8B8A8_UNORM;

    rsx_d3d12_feedback_context feedback = {};
    feedback.device = device.p; feedback.command_list = command.p;
    const rsx_linear_view view = {0xAB8000,2,1,8,4,0x85,1};
    const rsx_d3d12_linear_resource source = {canonical.p, &canonical_state, view, DXGI_FORMAT_R8G8B8A8_UNORM};
    if (!rsx_d3d12_feedback_reserve(&feedback, 0, &source)) {
        std::fprintf(stderr, "FAIL feedback reserve HRESULT=0x%08lX\n", static_cast<unsigned long>(feedback.last_hr)); return 2;
    }
    ID3D12Resource* snapshot = feedback.slots[0].resource;
    if (!snapshot || snapshot == canonical.p || snapshot == display.p) { std::fprintf(stderr, "FAIL snapshot aliases a draw resource\n"); return 1; }
    D3D12_SHADER_RESOURCE_VIEW_DESC srv_desc = {};
    srv_desc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    srv_desc.ViewDimension = D3D12_SRV_DIMENSION_TEXTURE2D;
    srv_desc.Shader4ComponentMapping = D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
    srv_desc.Texture2D.MipLevels = 1;
    for (UINT index = 0; index < 2; ++index) {
        auto handle = srv; handle.ptr += index * srv_increment;
        device.p->lpVtbl->CreateShaderResourceView(device.p, snapshot, &srv_desc, handle);
    }
    ID3D12DescriptorHeap* heaps[] = {srv_heap.p};
    command.p->lpVtbl->SetDescriptorHeaps(command.p, 1, heaps);
    command.p->lpVtbl->SetGraphicsRootSignature(command.p, root.p);
    command.p->lpVtbl->IASetPrimitiveTopology(command.p, D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    const D3D12_VIEWPORT viewport = {0,0,2,1,0,1};
    const D3D12_RECT scissor = {0,0,2,1};
    command.p->lpVtbl->RSSetViewports(command.p, 1, &viewport);
    command.p->lpVtbl->RSSetScissorRects(command.p, 1, &scissor);
    for (UINT draw = 0; draw < 2; ++draw) {
        if (!rsx_d3d12_feedback_reserve(&feedback, 0, &source) || feedback.slots[0].resource != snapshot) {
            std::fprintf(stderr, "FAIL stable feedback allocation\n"); return 1;
        }
        if (!rsx_d3d12_feedback_snapshot(&feedback, 0, &source)) {
            std::fprintf(stderr, "FAIL feedback snapshot draw=%u HRESULT=0x%08lX\n", draw + 1,
                static_cast<unsigned long>(feedback.last_hr)); return 2;
        }
        if (!omit_load && !rsx_d3d12_display_bridge_begin(&bridge, &source)) {
            std::fprintf(stderr, "FAIL display bridge begin draw=%u HRESULT=0x%08lX\n", draw + 1,
                static_cast<unsigned long>(bridge.last_hr)); return 2;
        }
        auto handle = gpu_srv; handle.ptr += draw * srv_increment;
        command.p->lpVtbl->SetGraphicsRootDescriptorTable(command.p, 0, handle);
        command.p->lpVtbl->OMSetRenderTargets(command.p, 1, &rtv, FALSE, nullptr);
        command.p->lpVtbl->DrawInstanced(command.p, 3, 1, 0, 0);
        if (!omit_writeback && !rsx_d3d12_display_bridge_end(&bridge, &source)) {
            std::fprintf(stderr, "FAIL display bridge end draw=%u HRESULT=0x%08lX\n", draw + 1,
                static_cast<unsigned long>(bridge.last_hr)); return 2;
        }
        transition(command.p, canonical.p, canonical_state, D3D12_RESOURCE_STATE_COPY_SOURCE);
        auto output = placed(readback.p, draw * 512);
        command.p->lpVtbl->CopyTextureRegion(command.p, &output, 0, 0, 0, &canonical_location, nullptr);
    }
    if (!checked(command.p->lpVtbl->Close(command.p), "Close command list")) { validation(info.p); return 2; }
    ID3D12CommandList* lists[] = {reinterpret_cast<ID3D12CommandList*>(command.p)};
    queue.p->lpVtbl->ExecuteCommandLists(queue.p, 1, lists);
    // No automatic local destruction if completion cannot be established.
    if (!checked(queue.p->lpVtbl->Signal(queue.p, fence.p, 1), "Signal fence") ||
        !checked(fence.p->lpVtbl->SetEventOnCompletion(fence.p, 1, completed.value), "SetEventOnCompletion")) std::exit(2);
    const DWORD wait = WaitForSingleObject(completed.value, 10000);
    const UINT64 completed_value = fence.p->lpVtbl->GetCompletedValue(fence.p);
    const HRESULT removed = device.p->lpVtbl->GetDeviceRemovedReason(device.p);
    if (wait != WAIT_OBJECT_0 || completed_value == UINT64_MAX || completed_value < 1 || removed != S_OK) {
        std::fprintf(stderr, "FAIL GPU completion wait=%lu fence=%llu removed=0x%08lX\n", wait,
            static_cast<unsigned long long>(completed_value), static_cast<unsigned long>(removed));
        validation(info.p); std::exit(2);
    }
    const D3D12_RANGE read = {0, 1024};
    if (!checked(readback.p->lpVtbl->Map(readback.p, 0, &read, reinterpret_cast<void**>(&mapped)), "Map readback")) return 2;
    const unsigned char expected[2][8] = {{11,77,88,255,21,77,88,255},{12,77,88,255,22,77,88,255}};
    bool correct = true;
    for (UINT draw = 0; draw < 2; ++draw) {
        const bool same = !std::memcmp(mapped + draw * 512, expected[draw], 8);
        const unsigned char* actual = mapped + draw * 512;
        const bool channels = actual[1] == 77 && actual[2] == 88 && actual[3] == 255 &&
                              actual[5] == 77 && actual[6] == 88 && actual[7] == 255;
        std::printf("%s draw=%u red=%u,%u expected=%u,%u channels_preserved=%u exact_rgba_pixels=2\n",
            same ? "PASS" : "FAIL", draw + 1, actual[0], actual[4], expected[draw][0], expected[draw][4], channels ? 1u : 0u);
        correct = correct && same;
    }
    readback.p->lpVtbl->Unmap(readback.p, 0, &no_reads);
    rsx_d3d12_feedback_release(&feedback);
    rsx_d3d12_feedback_release(&feedback);
    if (!validation(info.p) || !correct) return 1;
    std::printf("PASS production display bridge: draws=2 exact_rgba_pixels=4 channels=77,88,255 adapter=%s\n", warp ? "WARP" : "hardware");
    return 0;
}
