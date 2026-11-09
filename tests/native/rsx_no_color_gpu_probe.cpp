// Platform characterization only: zero RTVs, a real PS output/discard, depth.
// All self-test modes return before any D3D12/DXGI device API is called.
#define CINTERFACE
#define COBJMACROS
#include <windows.h>
#include <dxgi1_4.h>
#include <d3d12.h>
#include <d3d12sdklayers.h>
#include <d3d12shader.h>
#include <d3dcompiler.h>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <vector>

static constexpr UINT width = 4, height = 4;
static constexpr uint8_t sentinel[4] = {13, 37, 79, 191};

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

static float read_depth(const uint8_t* data, UINT pitch, UINT x, UINT y)
{
    float value;
    std::memcpy(&value, data + static_cast<size_t>(y) * pitch + x * sizeof(float), sizeof(value));
    return value;
}

static unsigned depth_mismatches(const uint8_t* data, UINT pitch, UINT draw)
{
    unsigned mismatches = 0;
    for (UINT y = 0; y < height; ++y) for (UINT x = 0; x < width; ++x) {
        const float expected = x < 2 ? (draw == 0 ? 1.0f : 0.75f) : 0.25f;
        const float actual = read_depth(data, pitch, x, y);
        if (!std::isfinite(actual) || std::fabs(actual - expected) > 0.000001f) ++mismatches;
    }
    return mismatches;
}

static unsigned sentinel_mismatches(const uint8_t* data, UINT pitch)
{
    unsigned mismatches = 0;
    for (UINT y = 0; y < height; ++y) for (UINT x = 0; x < width; ++x)
        if (std::memcmp(data + static_cast<size_t>(y) * pitch + x * 4, sentinel, 4)) ++mismatches;
    return mismatches;
}

static int oracle_self_test()
{
    uint8_t first[1024] = {}, second[1024] = {}, color[1024] = {};
    const float first_row[4] = {1.0f, 1.0f, 0.25f, 0.25f};
    const float second_row[4] = {0.75f, 0.75f, 0.25f, 0.25f};
    for (UINT y = 0; y < height; ++y) {
        std::memcpy(first + y * 256, first_row, sizeof(first_row));
        std::memcpy(second + y * 256, second_row, sizeof(second_row));
        for (UINT x = 0; x < width; ++x) std::memcpy(color + y * 256 + x * 4, sentinel, 4);
    }
    if (depth_mismatches(first, 256, 0) || depth_mismatches(second, 256, 1) ||
            sentinel_mismatches(color, 256)) return 1;
    const float lost_discard = 0.25f, failed_depth_test = 0.75f;
    std::memcpy(first, &lost_discard, sizeof(float));
    if (depth_mismatches(first, 256, 0) != 1) return 1;
    std::memcpy(second + 2 * sizeof(float), &failed_depth_test, sizeof(float));
    if (depth_mismatches(second, 256, 1) != 1) return 1;
    const float invalid = std::numeric_limits<float>::quiet_NaN();
    std::memcpy(second + 2 * sizeof(float), &invalid, sizeof(float));
    if (depth_mismatches(second, 256, 1) != 1) return 1;
    color[0] ^= 1;
    if (sentinel_mismatches(color, 256) != 1) return 1;
    std::puts("PASS CPU oracle cases=5 gpu_calls=0");
    return 0;
}

static bool shader(const char* text, const char* entry, const char* target, Owned<ID3DBlob>& output)
{
    Owned<ID3DBlob> error;
    HRESULT result = D3DCompile(text, std::strlen(text), "no_color_fixture", nullptr,
        nullptr, entry, target, D3DCOMPILE_ENABLE_STRICTNESS, 0, &output.p, &error.p);
    if (error.p) std::fwrite(error.p->lpVtbl->GetBufferPointer(error.p), 1,
        error.p->lpVtbl->GetBufferSize(error.p), stderr);
    return checked(result, target);
}

static bool pixel_has_target(ID3DBlob* bytecode)
{
    Owned<ID3D12ShaderReflection> reflection;
    if (!checked(D3DReflect(bytecode->lpVtbl->GetBufferPointer(bytecode),
            bytecode->lpVtbl->GetBufferSize(bytecode), IID_ID3D12ShaderReflection, reflection.raw()),
            "D3DReflect PS")) return false;
    D3D12_SHADER_DESC description = {};
    if (!checked(reflection.p->lpVtbl->GetDesc(reflection.p, &description), "GetDesc PS")) return false;
    for (UINT index = 0; index < description.OutputParameters; ++index) {
        D3D12_SIGNATURE_PARAMETER_DESC output = {};
        if (!checked(reflection.p->lpVtbl->GetOutputParameterDesc(reflection.p, index, &output),
                "GetOutputParameterDesc")) return false;
        if (output.SystemValueType == D3D_NAME_TARGET && output.SemanticIndex == 0 && output.Mask == 15)
            return true;
    }
    std::fprintf(stderr, "FAIL compiled PS lost SV_Target0 output\n");
    return false;
}

static bool shader_pair(Owned<ID3DBlob>& vertex, Owned<ID3DBlob>& pixel)
{
    const char* hlsl =
        "cbuffer Parameters:register(b0){float draw_depth;uint discard_left;}"
        "float4 vs_main(uint id:SV_VertexID):SV_POSITION{float2 p=float2((id<<1)&2,id&2);"
        "return float4(p*float2(2,-2)+float2(-1,1),draw_depth,1);}"
        "float4 ps_main(float4 position:SV_POSITION):SV_Target0{"
        "if(discard_left!=0 && position.x<2.0)discard;return float4(1,0.25,0.5,1);}";
    return shader(hlsl, "vs_main", "vs_5_0", vertex) && shader(hlsl, "ps_main", "ps_5_0", pixel) &&
           pixel_has_target(pixel.p);
}

struct ValidationStats {
    UINT64 errors = 0, warnings = 0;
    UINT64 expected_unbound_output_warnings = 0, unexpected_warnings = 0;
};

static void record_validation_message(ValidationStats& stats,
        D3D12_MESSAGE_SEVERITY severity, D3D12_MESSAGE_ID id, bool bound_ps)
{
    if (severity <= D3D12_MESSAGE_SEVERITY_ERROR) ++stats.errors;
    if (severity == D3D12_MESSAGE_SEVERITY_WARNING) {
        ++stats.warnings;
        // The exclusive hardware run observed exactly one 679 for the bound
        // SV_Target0 output without RTVs. Keep and report that diagnostic.
        if (bound_ps && id == D3D12_MESSAGE_ID_CREATEGRAPHICSPIPELINESTATE_RENDERTARGETVIEW_NOT_SET &&
                !stats.expected_unbound_output_warnings)
            ++stats.expected_unbound_output_warnings;
        else ++stats.unexpected_warnings;
    }
}

static bool validation_ok(const ValidationStats& stats, bool bound_ps)
{
    return !stats.errors && !stats.unexpected_warnings &&
           stats.expected_unbound_output_warnings == (bound_ps ? 1u : 0u);
}

static int validation_self_test()
{
    const auto observed_id = static_cast<D3D12_MESSAGE_ID>(679);
    ValidationStats bound, unbound, unexpected_bound, unexpected_id, error, duplicate;
    if (validation_ok(bound, true)) return 1;
    record_validation_message(bound, D3D12_MESSAGE_SEVERITY_WARNING, observed_id, true);
    if (!validation_ok(bound, true) || bound.warnings != 1 ||
            bound.expected_unbound_output_warnings != 1 || bound.unexpected_warnings) return 1;
    if (!validation_ok(unbound, false) || unbound.warnings) return 1;
    record_validation_message(unexpected_bound, D3D12_MESSAGE_SEVERITY_WARNING, observed_id, false);
    if (validation_ok(unexpected_bound, false) || unexpected_bound.unexpected_warnings != 1) return 1;
    record_validation_message(unexpected_id, D3D12_MESSAGE_SEVERITY_WARNING, D3D12_MESSAGE_ID_UNKNOWN, true);
    if (validation_ok(unexpected_id, true) || unexpected_id.unexpected_warnings != 1) return 1;
    record_validation_message(error, D3D12_MESSAGE_SEVERITY_ERROR, observed_id, true);
    if (validation_ok(error, true) || error.errors != 1 || error.expected_unbound_output_warnings) return 1;
    record_validation_message(duplicate, D3D12_MESSAGE_SEVERITY_WARNING, observed_id, true);
    record_validation_message(duplicate, D3D12_MESSAGE_SEVERITY_WARNING, observed_id, true);
    if (validation_ok(duplicate, true) || duplicate.warnings != 2 ||
            duplicate.expected_unbound_output_warnings != 1 || duplicate.unexpected_warnings != 1) return 1;
    std::puts("PASS CPU validation cases=6 gpu_calls=0");
    return 0;
}

static bool validation(ID3D12InfoQueue* info, bool bound_ps)
{
    ValidationStats stats;
    const UINT64 count = info->lpVtbl->GetNumStoredMessages(info);
    for (UINT64 index = 0; index < count; ++index) {
        SIZE_T size = 0;
        if (!checked(info->lpVtbl->GetMessage(info, index, nullptr, &size), "GetMessage size")) return false;
        std::vector<uint8_t> storage(size);
        auto* message = reinterpret_cast<D3D12_MESSAGE*>(storage.data());
        if (!checked(info->lpVtbl->GetMessage(info, index, message, &size), "GetMessage")) return false;
        record_validation_message(stats, message->Severity, message->ID, bound_ps);
        std::fprintf(stderr, "[D3D12-IQ][severity=%d id=%d] %s\n",
            message->Severity, message->ID, message->pDescription);
    }
    std::printf("STATUS validation_messages=%llu errors=%llu warnings=%llu "
        "expected_unbound_output_warnings=%llu unexpected_warnings=%llu\n",
        static_cast<unsigned long long>(count), static_cast<unsigned long long>(stats.errors),
        static_cast<unsigned long long>(stats.warnings),
        static_cast<unsigned long long>(stats.expected_unbound_output_warnings),
        static_cast<unsigned long long>(stats.unexpected_warnings));
    return validation_ok(stats, bound_ps);
}

static bool resource(ID3D12Device* device, D3D12_HEAP_TYPE type,
        const D3D12_RESOURCE_DESC& desc, D3D12_RESOURCE_STATES state,
        const D3D12_CLEAR_VALUE* clear, Owned<ID3D12Resource>& output)
{
    D3D12_HEAP_PROPERTIES heap = {};
    heap.Type = type; heap.CreationNodeMask = heap.VisibleNodeMask = 1;
    return checked(device->lpVtbl->CreateCommittedResource(device, &heap, D3D12_HEAP_FLAG_NONE,
        &desc, state, clear, IID_ID3D12Resource, output.raw()), "CreateCommittedResource");
}

static void transition(ID3D12GraphicsCommandList* command, ID3D12Resource* resource,
        D3D12_RESOURCE_STATES& state, D3D12_RESOURCE_STATES after)
{
    if (state == after) return;
    D3D12_RESOURCE_BARRIER barrier = {};
    barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
    barrier.Transition.pResource = resource;
    barrier.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
    barrier.Transition.StateBefore = state; barrier.Transition.StateAfter = after;
    command->lpVtbl->ResourceBarrier(command, 1, &barrier);
    state = after;
}

static D3D12_TEXTURE_COPY_LOCATION subresource(ID3D12Resource* texture)
{
    D3D12_TEXTURE_COPY_LOCATION location = {};
    location.pResource = texture;
    location.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    return location;
}

static D3D12_TEXTURE_COPY_LOCATION placed(ID3D12Resource* buffer,
        D3D12_PLACED_SUBRESOURCE_FOOTPRINT footprint, UINT64 offset)
{
    D3D12_TEXTURE_COPY_LOCATION location = {};
    location.pResource = buffer;
    location.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
    footprint.Offset = offset;
    location.PlacedFootprint = footprint;
    return location;
}

int main(int argc, char** argv)
{
    bool warp = false, omit_discard = false, omit_ps = false, oracle = false;
    bool shaders_only = false, validation_only = false;
    for (int index = 1; index < argc; ++index) {
        if (!std::strcmp(argv[index], "--warp")) warp = true;
        else if (!std::strcmp(argv[index], "--omit-discard")) omit_discard = true;
        else if (!std::strcmp(argv[index], "--omit-ps")) omit_ps = true;
        else if (!std::strcmp(argv[index], "--oracle-self-test")) oracle = true;
        else if (!std::strcmp(argv[index], "--shader-self-test")) shaders_only = true;
        else if (!std::strcmp(argv[index], "--validation-self-test")) validation_only = true;
        else { std::fprintf(stderr, "Usage: rsx_no_color_gpu_probe.exe [--warp] [--omit-discard|--omit-ps|--oracle-self-test|--shader-self-test|--validation-self-test]\n"); return 2; }
    }
    if (oracle) return oracle_self_test(); // No API calls or shader compilation above this return.
    if (validation_only) return validation_self_test();
    if (shaders_only) {
        Owned<ID3DBlob> vertex, pixel;
        if (!shader_pair(vertex, pixel)) return 2;
        std::puts("PASS offline shaders ps_sv_target=1 gpu_calls=0");
        return 0;
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
    if (!shader_pair(vertex, pixel)) return 2;
    D3D12_ROOT_PARAMETER parameter = {};
    parameter.ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
    parameter.Constants.Num32BitValues = 2;
    parameter.ShaderVisibility = D3D12_SHADER_VISIBILITY_ALL;
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
    if (!omit_ps) pipeline_desc.PS = {pixel.p->lpVtbl->GetBufferPointer(pixel.p), pixel.p->lpVtbl->GetBufferSize(pixel.p)};
    for (auto& blend : pipeline_desc.BlendState.RenderTarget) {
        blend.SrcBlend = blend.SrcBlendAlpha = D3D12_BLEND_ONE;
        blend.DestBlend = blend.DestBlendAlpha = D3D12_BLEND_ZERO;
        blend.BlendOp = blend.BlendOpAlpha = D3D12_BLEND_OP_ADD;
        blend.LogicOp = D3D12_LOGIC_OP_NOOP;
        blend.RenderTargetWriteMask = D3D12_COLOR_WRITE_ENABLE_ALL;
    }
    pipeline_desc.RasterizerState.FillMode = D3D12_FILL_MODE_SOLID;
    pipeline_desc.RasterizerState.CullMode = D3D12_CULL_MODE_NONE;
    pipeline_desc.RasterizerState.DepthClipEnable = TRUE;
    pipeline_desc.DepthStencilState.DepthEnable = TRUE;
    pipeline_desc.DepthStencilState.DepthWriteMask = D3D12_DEPTH_WRITE_MASK_ALL;
    pipeline_desc.DepthStencilState.DepthFunc = D3D12_COMPARISON_FUNC_LESS;
    pipeline_desc.DepthStencilState.StencilReadMask = D3D12_DEFAULT_STENCIL_READ_MASK;
    pipeline_desc.DepthStencilState.StencilWriteMask = D3D12_DEFAULT_STENCIL_WRITE_MASK;
    pipeline_desc.DepthStencilState.FrontFace = pipeline_desc.DepthStencilState.BackFace =
        {D3D12_STENCIL_OP_KEEP, D3D12_STENCIL_OP_KEEP, D3D12_STENCIL_OP_KEEP, D3D12_COMPARISON_FUNC_ALWAYS};
    pipeline_desc.SampleMask = UINT_MAX;
    pipeline_desc.PrimitiveTopologyType = D3D12_PRIMITIVE_TOPOLOGY_TYPE_TRIANGLE;
    pipeline_desc.NumRenderTargets = 0;
    pipeline_desc.DSVFormat = DXGI_FORMAT_D32_FLOAT;
    pipeline_desc.SampleDesc.Count = 1;
    Owned<ID3D12PipelineState> pipeline;
    if (!checked(device.p->lpVtbl->CreateGraphicsPipelineState(device.p, &pipeline_desc,
            IID_ID3D12PipelineState, pipeline.raw()), "CreateGraphicsPipelineState zero RT")) { validation(info.p, !omit_ps); return 2; }
    std::printf("STATUS num_render_targets=0 om_rtv_count=0 ps_sv_target=1 ps_bound=%u pso_created=1\n", omit_ps ? 0u : 1u);

    Owned<ID3D12DescriptorHeap> rtv_heap, dsv_heap;
    D3D12_DESCRIPTOR_HEAP_DESC heap_desc = {};
    heap_desc.Type = D3D12_DESCRIPTOR_HEAP_TYPE_RTV; heap_desc.NumDescriptors = 1;
    if (!checked(device.p->lpVtbl->CreateDescriptorHeap(device.p, &heap_desc, IID_ID3D12DescriptorHeap, rtv_heap.raw()), "CreateDescriptorHeap RTV")) return 2;
    heap_desc.Type = D3D12_DESCRIPTOR_HEAP_TYPE_DSV;
    if (!checked(device.p->lpVtbl->CreateDescriptorHeap(device.p, &heap_desc, IID_ID3D12DescriptorHeap, dsv_heap.raw()), "CreateDescriptorHeap DSV")) return 2;
    D3D12_CPU_DESCRIPTOR_HANDLE rtv = {}, dsv = {};
    rtv_heap.p->lpVtbl->GetCPUDescriptorHandleForHeapStart(rtv_heap.p, &rtv);
    dsv_heap.p->lpVtbl->GetCPUDescriptorHandleForHeapStart(dsv_heap.p, &dsv);

    D3D12_RESOURCE_DESC depth_desc = {};
    depth_desc.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    depth_desc.Width = width; depth_desc.Height = height;
    depth_desc.DepthOrArraySize = depth_desc.MipLevels = depth_desc.SampleDesc.Count = 1;
    depth_desc.Format = DXGI_FORMAT_D32_FLOAT;
    depth_desc.Layout = D3D12_TEXTURE_LAYOUT_UNKNOWN;
    depth_desc.Flags = D3D12_RESOURCE_FLAG_ALLOW_DEPTH_STENCIL;
    D3D12_RESOURCE_DESC color_desc = depth_desc;
    color_desc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    color_desc.Flags = D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET;
    D3D12_CLEAR_VALUE depth_clear = {};
    depth_clear.Format = depth_desc.Format; depth_clear.DepthStencil.Depth = 1.0f;
    D3D12_CLEAR_VALUE color_clear = {};
    color_clear.Format = color_desc.Format;
    for (UINT channel = 0; channel < 4; ++channel) color_clear.Color[channel] = sentinel[channel] / 255.0f;
    Owned<ID3D12Resource> depth, color, readback;
    D3D12_RESOURCE_STATES depth_state = D3D12_RESOURCE_STATE_DEPTH_WRITE;
    D3D12_RESOURCE_STATES color_state = D3D12_RESOURCE_STATE_RENDER_TARGET;
    if (!resource(device.p, D3D12_HEAP_TYPE_DEFAULT, depth_desc, depth_state, &depth_clear, depth) ||
        !resource(device.p, D3D12_HEAP_TYPE_DEFAULT, color_desc, color_state, &color_clear, color)) return 2;
    device.p->lpVtbl->CreateDepthStencilView(device.p, depth.p, nullptr, dsv);
    device.p->lpVtbl->CreateRenderTargetView(device.p, color.p, nullptr, rtv);
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT depth_layout = {}, color_layout = {};
    UINT64 depth_bytes = 0, color_bytes = 0;
    device.p->lpVtbl->GetCopyableFootprints(device.p, &depth_desc, 0, 1, 0, &depth_layout, nullptr, nullptr, &depth_bytes);
    device.p->lpVtbl->GetCopyableFootprints(device.p, &color_desc, 0, 1, 0, &color_layout, nullptr, nullptr, &color_bytes);
    const UINT64 depth_stride = (depth_bytes + 511) & ~UINT64(511);
    const UINT64 color_offset = depth_stride * 2, total_bytes = color_offset + color_bytes;
    D3D12_RESOURCE_DESC buffer_desc = {};
    buffer_desc.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER; buffer_desc.Width = total_bytes;
    buffer_desc.Height = buffer_desc.DepthOrArraySize = buffer_desc.MipLevels = buffer_desc.SampleDesc.Count = 1;
    buffer_desc.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    if (!resource(device.p, D3D12_HEAP_TYPE_READBACK, buffer_desc, D3D12_RESOURCE_STATE_COPY_DEST, nullptr, readback)) return 2;

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
    command.p->lpVtbl->ClearDepthStencilView(command.p, dsv, D3D12_CLEAR_FLAG_DEPTH, 1.0f, 0, 0, nullptr);
    // This RTV is used only by a clear, and never bound to the output merger.
    command.p->lpVtbl->ClearRenderTargetView(command.p, rtv, color_clear.Color, 0, nullptr);
    command.p->lpVtbl->SetGraphicsRootSignature(command.p, root.p);
    command.p->lpVtbl->IASetPrimitiveTopology(command.p, D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    const D3D12_VIEWPORT viewport = {0, 0, 4, 4, 0, 1};
    const D3D12_RECT scissor = {0, 0, 4, 4};
    command.p->lpVtbl->RSSetViewports(command.p, 1, &viewport);
    command.p->lpVtbl->RSSetScissorRects(command.p, 1, &scissor);
    auto depth_source = subresource(depth.p);
    for (UINT draw = 0; draw < 2; ++draw) {
        transition(command.p, depth.p, depth_state, D3D12_RESOURCE_STATE_DEPTH_WRITE);
        const struct { float depth; UINT discard; } parameters =
            {draw == 0 ? 0.25f : 0.75f, draw == 0 && !omit_discard ? 1u : 0u};
        command.p->lpVtbl->SetGraphicsRoot32BitConstants(command.p, 0, 2, &parameters, 0);
        command.p->lpVtbl->OMSetRenderTargets(command.p, 0, nullptr, FALSE, &dsv);
        command.p->lpVtbl->DrawInstanced(command.p, 3, 1, 0, 0);
        command.p->lpVtbl->OMSetRenderTargets(command.p, 0, nullptr, FALSE, nullptr);
        transition(command.p, depth.p, depth_state, D3D12_RESOURCE_STATE_COPY_SOURCE);
        auto destination = placed(readback.p, depth_layout, draw * depth_stride);
        command.p->lpVtbl->CopyTextureRegion(command.p, &destination, 0, 0, 0, &depth_source, nullptr);
    }
    transition(command.p, color.p, color_state, D3D12_RESOURCE_STATE_COPY_SOURCE);
    auto color_source = subresource(color.p), color_destination = placed(readback.p, color_layout, color_offset);
    command.p->lpVtbl->CopyTextureRegion(command.p, &color_destination, 0, 0, 0, &color_source, nullptr);
    if (!checked(command.p->lpVtbl->Close(command.p), "Close command list")) { validation(info.p, !omit_ps); return 2; }
    ID3D12CommandList* lists[] = {reinterpret_cast<ID3D12CommandList*>(command.p)};
    queue.p->lpVtbl->ExecuteCommandLists(queue.p, 1, lists);
    // Avoid releasing submitted resources on an unconfirmed completion path.
    if (!checked(queue.p->lpVtbl->Signal(queue.p, fence.p, 1), "Signal fence") ||
        !checked(fence.p->lpVtbl->SetEventOnCompletion(fence.p, 1, completed.value), "SetEventOnCompletion")) std::exit(2);
    const DWORD wait = WaitForSingleObject(completed.value, 10000);
    const UINT64 value = fence.p->lpVtbl->GetCompletedValue(fence.p);
    const HRESULT removed = device.p->lpVtbl->GetDeviceRemovedReason(device.p);
    if (wait != WAIT_OBJECT_0 || value == UINT64_MAX || value < 1 || removed != S_OK) {
        std::fprintf(stderr, "FAIL GPU completion wait=%lu fence=%llu removed=0x%08lX\n", wait,
            static_cast<unsigned long long>(value), static_cast<unsigned long>(removed));
        validation(info.p, !omit_ps); std::exit(2);
    }
    std::puts("STATUS api_completed=1 fence_completed=1");
    const D3D12_RANGE read = {0, static_cast<SIZE_T>(total_bytes)}, no_write = {0, 0};
    uint8_t* mapped = nullptr;
    if (!checked(readback.p->lpVtbl->Map(readback.p, 0, &read, reinterpret_cast<void**>(&mapped)), "Map readback")) return 2;
    unsigned depth_errors = 0;
    for (UINT draw = 0; draw < 2; ++draw) {
        const uint8_t* data = mapped + draw * depth_stride;
        unsigned errors = depth_mismatches(data, depth_layout.Footprint.RowPitch, draw);
        depth_errors += errors;
        std::printf("%s depth draw=%u mismatches=%u left=%.9g right=%.9g\n", errors ? "FAIL" : "PASS",
            draw + 1, errors, read_depth(data, depth_layout.Footprint.RowPitch, 0, 0),
            read_depth(data, depth_layout.Footprint.RowPitch, 2, 0));
    }
    const unsigned color_errors = sentinel_mismatches(mapped + color_offset, color_layout.Footprint.RowPitch);
    std::printf("%s sentinel pixels=16 mismatches=%u\n", color_errors ? "FAIL" : "PASS", color_errors);
    readback.p->lpVtbl->Unmap(readback.p, 0, &no_write);
    if (!validation(info.p, !omit_ps) || depth_errors || color_errors) return 1;
    std::printf("PASS zero-color depth: draws=2 depth_pixels=32 sentinel_pixels=16 adapter=%s\n", warp ? "WARP" : "hardware");
    return 0;
}
