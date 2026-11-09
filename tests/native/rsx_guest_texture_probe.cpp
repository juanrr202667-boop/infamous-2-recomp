// Real D3D12 validation and readback using the production guest root builder.
// A four-unit signature rejects shaders using t4/s4 or t15/s15. Incorrect
// per-draw descriptor strides alias the two colors. A draw window overlapping
// cache/RT descriptors changes a sampled pixel after the reserved-slot writes.
#define CINTERFACE
#define COBJMACROS
#include <windows.h>
#include <dxgi1_4.h>
#include <d3d12.h>
#include <d3d12sdklayers.h>
#include <d3dcompiler.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include "rsx_d3d12_guest_pipeline.h"

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

static bool messages(ID3D12InfoQueue* info)
{
    if (!info) return true;
    bool clean = true;
    const UINT64 count = info->lpVtbl->GetNumStoredMessages(info);
    for (UINT64 i = 0; i < count; ++i) {
        SIZE_T bytes = 0;
        info->lpVtbl->GetMessage(info, i, nullptr, &bytes);
        std::vector<unsigned char> storage(bytes);
        auto* message = reinterpret_cast<D3D12_MESSAGE*>(storage.data());
        if (SUCCEEDED(info->lpVtbl->GetMessage(info, i, message, &bytes))) {
            std::fprintf(stderr, "[D3D12-IQ][sev=%d id=%d] %s\n",
                         message->Severity, message->ID, message->pDescription);
            if (message->Severity == D3D12_MESSAGE_SEVERITY_CORRUPTION ||
                message->Severity == D3D12_MESSAGE_SEVERITY_ERROR) clean = false;
        }
    }
    return clean;
}

static bool shader(const char* source, const char* profile, ID3DBlob** output)
{
    Owned<ID3DBlob> errors;
    HRESULT result = D3DCompile(source, std::strlen(source), "synthetic-texture-probe",
        nullptr, nullptr, "main", profile, D3DCOMPILE_ENABLE_STRICTNESS, 0, output, errors.out());
    if (errors.p) std::fprintf(stderr, "%s", static_cast<char*>(errors.p->lpVtbl->GetBufferPointer(errors.p)));
    return checked(result, "D3DCompile");
}

static D3D12_RESOURCE_DESC buffer(UINT64 bytes)
{
    D3D12_RESOURCE_DESC description = {};
    description.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
    description.Width = bytes;
    description.Height = 1;
    description.DepthOrArraySize = 1;
    description.MipLevels = 1;
    description.SampleDesc.Count = 1;
    description.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    return description;
}

static D3D12_RESOURCE_DESC texture(UINT width, D3D12_RESOURCE_FLAGS flags = D3D12_RESOURCE_FLAG_NONE)
{
    D3D12_RESOURCE_DESC description = {};
    description.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    description.Width = width;
    description.Height = 1;
    description.DepthOrArraySize = 1;
    description.MipLevels = 1;
    description.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    description.SampleDesc.Count = 1;
    description.Flags = flags;
    return description;
}

static bool resource(ID3D12Device* device, D3D12_HEAP_TYPE type,
                     const D3D12_RESOURCE_DESC& description, D3D12_RESOURCE_STATES state,
                     ID3D12Resource** output)
{
    D3D12_HEAP_PROPERTIES heap = {};
    heap.Type = type;
    return checked(device->lpVtbl->CreateCommittedResource(device, &heap,
        D3D12_HEAP_FLAG_NONE, &description, state, nullptr, IID_ID3D12Resource,
        reinterpret_cast<void**>(output)), "CreateCommittedResource");
}

static void transition(ID3D12GraphicsCommandList* list, ID3D12Resource* resource,
                       D3D12_RESOURCE_STATES before, D3D12_RESOURCE_STATES after)
{
    D3D12_RESOURCE_BARRIER barrier = {};
    barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    barrier.Transition.pResource = resource;
    barrier.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    barrier.Transition.StateBefore = before;
    barrier.Transition.StateAfter = after;
    list->lpVtbl->ResourceBarrier(list, 1, &barrier);
}

static D3D12_TEXTURE_COPY_LOCATION placed(ID3D12Resource* resource, UINT64 offset, UINT width)
{
    D3D12_TEXTURE_COPY_LOCATION location = {};
    location.pResource = resource;
    location.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
    location.PlacedFootprint.Offset = offset;
    location.PlacedFootprint.Footprint.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    location.PlacedFootprint.Footprint.Width = width;
    location.PlacedFootprint.Footprint.Height = 1;
    location.PlacedFootprint.Footprint.Depth = 1;
    location.PlacedFootprint.Footprint.RowPitch = D3D12_TEXTURE_DATA_PITCH_ALIGNMENT;
    return location;
}

static D3D12_TEXTURE_COPY_LOCATION subresource(ID3D12Resource* resource)
{
    D3D12_TEXTURE_COPY_LOCATION location = {};
    location.pResource = resource;
    location.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    return location;
}

static int run(UINT unit)
{
    Owned<ID3D12Debug> debug;
    if (SUCCEEDED(D3D12GetDebugInterface(IID_ID3D12Debug, debug.raw())))
        debug.p->lpVtbl->EnableDebugLayer(debug.p);
    Owned<IDXGIFactory4> factory;
    Owned<IDXGIAdapter> adapter;
    Owned<ID3D12Device> device;
    if (!checked(CreateDXGIFactory1(IID_IDXGIFactory4, factory.raw()), "CreateDXGIFactory1") ||
        !checked(factory.p->lpVtbl->EnumWarpAdapter(factory.p, IID_IDXGIAdapter, adapter.raw()), "EnumWarpAdapter") ||
        !checked(D3D12CreateDevice(reinterpret_cast<IUnknown*>(adapter.p), D3D_FEATURE_LEVEL_11_0,
                                 IID_ID3D12Device, device.raw()), "D3D12CreateDevice(WARP)")) return 2;
    Owned<ID3D12InfoQueue> info;
    device.p->lpVtbl->QueryInterface(device.p, IID_ID3D12InfoQueue, info.raw());
    Owned<ID3D12RootSignature> root;
    Owned<ID3DBlob> errors;
    if (!checked(rsx_d3d12_create_guest_root_signature(device.p, root.out(), errors.out()), "guest root signature")) {
        if (errors.p) std::fprintf(stderr, "%s", static_cast<char*>(errors.p->lpVtbl->GetBufferPointer(errors.p)));
        messages(info.p);
        return 2;
    }

    const char* vertex_source =
        "struct V { float4 p:SV_POSITION; float2 uv:TEXCOORD0; };\n"
        "V main(uint id:SV_VertexID) { V v; float2 p=float2((id<<1)&2,id&2);"
        "v.p=float4(p*float2(2,-2)+float2(-1,1),0,1); v.uv=p; return v; }";
    char pixel_source[320];
    std::snprintf(pixel_source, sizeof(pixel_source),
        "Texture2D rsx_tex[16]:register(t0); SamplerState rsx_samp[16]:register(s0);\n"
        "float4 main(float4 p:SV_POSITION,float2 uv:TEXCOORD0):SV_TARGET {"
        "return rsx_tex[%u].Sample(rsx_samp[%u],uv); }", unit, unit);
    Owned<ID3DBlob> vertex, pixel;
    if (!shader(vertex_source, "vs_5_0", vertex.out()) || !shader(pixel_source, "ps_5_0", pixel.out())) return 2;
    D3D12_GRAPHICS_PIPELINE_STATE_DESC description = {};
    description.pRootSignature = root.p;
    description.VS = { vertex.p->lpVtbl->GetBufferPointer(vertex.p), vertex.p->lpVtbl->GetBufferSize(vertex.p) };
    description.PS = { pixel.p->lpVtbl->GetBufferPointer(pixel.p), pixel.p->lpVtbl->GetBufferSize(pixel.p) };
    description.SampleMask = UINT_MAX;
    description.RasterizerState.FillMode = D3D12_FILL_MODE_SOLID;
    description.RasterizerState.CullMode = D3D12_CULL_MODE_NONE;
    description.RasterizerState.DepthClipEnable = TRUE;
    description.BlendState.RenderTarget[0].SrcBlend = D3D12_BLEND_ONE;
    description.BlendState.RenderTarget[0].DestBlend = D3D12_BLEND_ZERO;
    description.BlendState.RenderTarget[0].BlendOp = D3D12_BLEND_OP_ADD;
    description.BlendState.RenderTarget[0].SrcBlendAlpha = D3D12_BLEND_ONE;
    description.BlendState.RenderTarget[0].DestBlendAlpha = D3D12_BLEND_ZERO;
    description.BlendState.RenderTarget[0].BlendOpAlpha = D3D12_BLEND_OP_ADD;
    description.BlendState.RenderTarget[0].LogicOp = D3D12_LOGIC_OP_NOOP;
    description.BlendState.RenderTarget[0].RenderTargetWriteMask = D3D12_COLOR_WRITE_ENABLE_ALL;
    description.DepthStencilState.DepthWriteMask = D3D12_DEPTH_WRITE_MASK_ZERO;
    description.DepthStencilState.DepthFunc = D3D12_COMPARISON_FUNC_ALWAYS;
    description.DepthStencilState.FrontFace.StencilFailOp = D3D12_STENCIL_OP_KEEP;
    description.DepthStencilState.FrontFace.StencilDepthFailOp = D3D12_STENCIL_OP_KEEP;
    description.DepthStencilState.FrontFace.StencilPassOp = D3D12_STENCIL_OP_KEEP;
    description.DepthStencilState.FrontFace.StencilFunc = D3D12_COMPARISON_FUNC_ALWAYS;
    description.DepthStencilState.BackFace = description.DepthStencilState.FrontFace;
    description.PrimitiveTopologyType = D3D12_PRIMITIVE_TOPOLOGY_TYPE_TRIANGLE;
    description.NumRenderTargets = 1;
    description.RTVFormats[0] = DXGI_FORMAT_R8G8B8A8_UNORM;
    description.SampleDesc.Count = 1;
    Owned<ID3D12PipelineState> pipeline;
    HRESULT result = device.p->lpVtbl->CreateGraphicsPipelineState(device.p, &description,
        IID_ID3D12PipelineState, pipeline.raw());
    if (!checked(result, "CreateGraphicsPipelineState")) {
        messages(info.p);
        std::fprintf(stderr, "FAIL guest shader requires texture/sampler slot %u\n", unit);
        return 1;
    }

    Owned<ID3D12CommandQueue> queue;
    Owned<ID3D12CommandAllocator> allocator;
    Owned<ID3D12GraphicsCommandList> list;
    D3D12_COMMAND_QUEUE_DESC queue_description = {};
    queue_description.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
    if (!checked(device.p->lpVtbl->CreateCommandQueue(device.p, &queue_description,
                     IID_ID3D12CommandQueue, queue.raw()), "CreateCommandQueue") ||
        !checked(device.p->lpVtbl->CreateCommandAllocator(device.p, D3D12_COMMAND_LIST_TYPE_DIRECT,
                     IID_ID3D12CommandAllocator, allocator.raw()), "CreateCommandAllocator") ||
        !checked(device.p->lpVtbl->CreateCommandList(device.p, 0, D3D12_COMMAND_LIST_TYPE_DIRECT,
                     allocator.p, pipeline.p, IID_ID3D12GraphicsCommandList, list.raw()), "CreateCommandList")) return 2;
    Owned<ID3D12Resource> red, green, blue, upload, target, readback;
    auto one = texture(1);
    auto two = texture(2, D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET);
    if (!resource(device.p, D3D12_HEAP_TYPE_DEFAULT, one, D3D12_RESOURCE_STATE_COPY_DEST, red.out()) ||
        !resource(device.p, D3D12_HEAP_TYPE_DEFAULT, one, D3D12_RESOURCE_STATE_COPY_DEST, green.out()) ||
        !resource(device.p, D3D12_HEAP_TYPE_DEFAULT, one, D3D12_RESOURCE_STATE_COPY_DEST, blue.out()) ||
        !resource(device.p, D3D12_HEAP_TYPE_UPLOAD, buffer(1536), D3D12_RESOURCE_STATE_GENERIC_READ, upload.out()) ||
        !resource(device.p, D3D12_HEAP_TYPE_DEFAULT, two, D3D12_RESOURCE_STATE_RENDER_TARGET, target.out()) ||
        !resource(device.p, D3D12_HEAP_TYPE_READBACK, buffer(256), D3D12_RESOURCE_STATE_COPY_DEST, readback.out())) return 2;
    unsigned char* mapped = nullptr;
    D3D12_RANGE no_reads = {0, 0};
    if (!checked(upload.p->lpVtbl->Map(upload.p, 0, &no_reads, reinterpret_cast<void**>(&mapped)), "Map upload")) return 2;
    std::memset(mapped, 0, 1536);
    const unsigned char colors[3][4] = {{255,0,0,255},{0,255,0,255},{0,0,255,255}};
    ID3D12Resource* textures[3] = {red.p, green.p, blue.p};
    for (UINT i = 0; i < 3; ++i) std::memcpy(mapped + i*512, colors[i], 4);
    upload.p->lpVtbl->Unmap(upload.p, 0, nullptr);
    for (UINT i = 0; i < 3; ++i) {
        auto destination = subresource(textures[i]);
        auto source = placed(upload.p, i*512, 1);
        list.p->lpVtbl->CopyTextureRegion(list.p, &destination, 0, 0, 0, &source, nullptr);
        transition(list.p, textures[i], D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE);
    }

    // Draws 3/5 expose an old base of 32 after expansion to 16 units:
    // draw 3's unit 15 collides with cache slot 95; draw 5's unit 0 with RT112.
    const UINT draws[2] = {3, 5};
    UINT heap_count = rsx_d3d12_guest_draw_srv_index(5, RSX_D3D12_GUEST_TEXTURE_UNITS-1) + 1;
    if (heap_count < 113) heap_count = 113;
    D3D12_DESCRIPTOR_HEAP_DESC heap_description = {};
    heap_description.Type = D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV;
    heap_description.NumDescriptors = heap_count;
    heap_description.Flags = D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE;
    Owned<ID3D12DescriptorHeap> srv_heap, rtv_heap;
    if (!checked(device.p->lpVtbl->CreateDescriptorHeap(device.p, &heap_description,
                    IID_ID3D12DescriptorHeap, srv_heap.raw()), "Create SRV heap")) return 2;
    const UINT increment = device.p->lpVtbl->GetDescriptorHandleIncrementSize(device.p, D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV);
    D3D12_CPU_DESCRIPTOR_HANDLE cpu = {};
    srv_heap.p->lpVtbl->GetCPUDescriptorHandleForHeapStart(srv_heap.p, &cpu);
    D3D12_SHADER_RESOURCE_VIEW_DESC srv = {};
    srv.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    srv.ViewDimension = D3D12_SRV_DIMENSION_TEXTURE2D;
    srv.Shader4ComponentMapping = D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
    srv.Texture2D.MipLevels = 1;
    for (UINT i = 0; i < heap_count; ++i) {
        D3D12_CPU_DESCRIPTOR_HANDLE handle = {cpu.ptr + static_cast<SIZE_T>(i)*increment};
        device.p->lpVtbl->CreateShaderResourceView(device.p, nullptr, &srv, handle);
    }
    for (UINT i = 0; i < 2; ++i) {
        UINT index = rsx_d3d12_guest_draw_srv_index(draws[i], unit);
        if (index >= heap_count) { std::fprintf(stderr, "FAIL descriptor index exceeds heap\n"); return 1; }
        D3D12_CPU_DESCRIPTOR_HANDLE handle = {cpu.ptr + static_cast<SIZE_T>(index)*increment};
        device.p->lpVtbl->CreateShaderResourceView(device.p, textures[i], &srv, handle);
    }
    // Reserved cache and RT descriptors can change independently of draw data.
    for (UINT index : {95u, 96u, 112u}) {
        D3D12_CPU_DESCRIPTOR_HANDLE handle = {cpu.ptr + static_cast<SIZE_T>(index)*increment};
        device.p->lpVtbl->CreateShaderResourceView(device.p, blue.p, &srv, handle);
    }
    heap_description = {};
    heap_description.Type = D3D12_DESCRIPTOR_HEAP_TYPE_RTV;
    heap_description.NumDescriptors = 1;
    if (!checked(device.p->lpVtbl->CreateDescriptorHeap(device.p, &heap_description,
                    IID_ID3D12DescriptorHeap, rtv_heap.raw()), "Create RTV heap")) return 2;
    D3D12_CPU_DESCRIPTOR_HANDLE rtv = {};
    rtv_heap.p->lpVtbl->GetCPUDescriptorHandleForHeapStart(rtv_heap.p, &rtv);
    device.p->lpVtbl->CreateRenderTargetView(device.p, target.p, nullptr, rtv);
    ID3D12DescriptorHeap* heaps[] = {srv_heap.p};
    list.p->lpVtbl->SetDescriptorHeaps(list.p, 1, heaps);
    list.p->lpVtbl->SetGraphicsRootSignature(list.p, root.p);
    list.p->lpVtbl->OMSetRenderTargets(list.p, 1, &rtv, FALSE, nullptr);
    const float black[4] = {0, 0, 0, 1};
    list.p->lpVtbl->ClearRenderTargetView(list.p, rtv, black, 0, nullptr);
    list.p->lpVtbl->IASetPrimitiveTopology(list.p, D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    D3D12_GPU_DESCRIPTOR_HANDLE gpu = {};
    srv_heap.p->lpVtbl->GetGPUDescriptorHandleForHeapStart(srv_heap.p, &gpu);
    for (UINT i = 0; i < 2; ++i) {
        D3D12_VIEWPORT viewport = {static_cast<float>(i), 0, 1, 1, 0, 1};
        D3D12_RECT scissor = {static_cast<LONG>(i), 0, static_cast<LONG>(i+1), 1};
        list.p->lpVtbl->RSSetViewports(list.p, 1, &viewport);
        list.p->lpVtbl->RSSetScissorRects(list.p, 1, &scissor);
        D3D12_GPU_DESCRIPTOR_HANDLE table = {gpu.ptr + static_cast<UINT64>(rsx_d3d12_guest_draw_srv_index(draws[i], 0))*increment};
        list.p->lpVtbl->SetGraphicsRootDescriptorTable(list.p, 1, table);
        list.p->lpVtbl->DrawInstanced(list.p, 3, 1, 0, 0);
    }
    transition(list.p, target.p, D3D12_RESOURCE_STATE_RENDER_TARGET, D3D12_RESOURCE_STATE_COPY_SOURCE);
    auto destination = placed(readback.p, 0, 2);
    auto source = subresource(target.p);
    list.p->lpVtbl->CopyTextureRegion(list.p, &destination, 0, 0, 0, &source, nullptr);
    if (!checked(list.p->lpVtbl->Close(list.p), "Close command list")) return 2;
    ID3D12CommandList* commands[] = {reinterpret_cast<ID3D12CommandList*>(list.p)};
    queue.p->lpVtbl->ExecuteCommandLists(queue.p, 1, commands);
    Owned<ID3D12Fence> fence;
    if (!checked(device.p->lpVtbl->CreateFence(device.p, 0, D3D12_FENCE_FLAG_NONE,
                    IID_ID3D12Fence, fence.raw()), "CreateFence") ||
        !checked(queue.p->lpVtbl->Signal(queue.p, fence.p, 1), "Signal")) return 2;
    HANDLE completed = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    if (!completed) return 2;
    result = fence.p->lpVtbl->SetEventOnCompletion(fence.p, 1, completed);
    DWORD wait = SUCCEEDED(result) ? WaitForSingleObject(completed, 10000) : WAIT_FAILED;
    CloseHandle(completed);
    if (wait != WAIT_OBJECT_0) { std::fprintf(stderr, "FAIL GPU completion wait: %lu\n", wait); messages(info.p); return 2; }
    D3D12_RANGE pixels = {0, 8};
    if (!checked(readback.p->lpVtbl->Map(readback.p, 0, &pixels, reinterpret_cast<void**>(&mapped)), "Map readback")) return 2;
    const unsigned char expected[8] = {255,0,0,255, 0,255,0,255};
    bool correct = std::memcmp(mapped, expected, sizeof(expected)) == 0;
    std::printf("slot %u draws 3/5: RGBA=(%u,%u,%u,%u) (%u,%u,%u,%u)\n", unit,
        mapped[0], mapped[1], mapped[2], mapped[3], mapped[4], mapped[5], mapped[6], mapped[7]);
    readback.p->lpVtbl->Unmap(readback.p, 0, &no_reads);
    const bool clean = messages(info.p);
    if (!correct) { std::fprintf(stderr, "FAIL expected isolated red/green pixels after cache/RT updates\n"); return 1; }
    if (!clean) { std::fprintf(stderr, "FAIL D3D12 validation error\n"); return 1; }
    std::printf("PASS guest texture/sampler slot %u, isolated draw descriptors\n", unit);
    return 0;
}

int main(int argc, char** argv)
{
    if (argc != 2) return 2;
    const int unit = std::atoi(argv[1]);
    if (unit != 0 && unit != 3 && unit != 4 && unit != 15) return 2;
    return run(static_cast<UINT>(unit));
}
