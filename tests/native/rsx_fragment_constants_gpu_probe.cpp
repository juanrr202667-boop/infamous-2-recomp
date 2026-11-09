// Synthetic RSX MOV CONST through the real D3D12 helper, HLSL and GPU.
// Self-test modes return before the first D3D12/DXGI device call.
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
#include "rsx_d3d12_fragment_constants.h"

static constexpr UINT width = 4, height = 4, constant_offset = 272;
static constexpr float expected[2][4] = {{0.25f, 0.125f, 0.5f, 1}, {0.75f, 0.125f, 0.5f, 1}};
static_assert(RSX_D3D12_FPCB_STRIDE % 256 == 0, "each fragment CBV must remain aligned");

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
struct Snapshots {
    alignas(256) float bank[2][RSX_D3D12_FPCB_STRIDE / sizeof(float)] = {};
    char hlsl[32768] = {};
    u32 constants = 0;
    int instructions = -1;
};

static bool checked(HRESULT result, const char* stage)
{
    if (SUCCEEDED(result)) return true;
    std::fprintf(stderr, "FAIL %s HRESULT=0x%08lX\n", stage, static_cast<unsigned long>(result));
    return false;
}

static void put_word(u8* p, u32 value)
{
    const u32 swapped = (value << 16) | (value >> 16);
    p[0] = static_cast<u8>(swapped >> 24); p[1] = static_cast<u8>(swapped >> 16);
    p[2] = static_cast<u8>(swapped >> 8); p[3] = static_cast<u8>(swapped);
}

static void put_float(u8* p, float value)
{
    u32 bits;
    std::memcpy(&bits, &value, sizeof(bits));
    put_word(p, bits);
}

static bool snapshots(Snapshots& output)
{
    u8 program[32] = {};
    // SDK test_fp_decompiler.c encoding: MOV r0.xyzw, CONST, unconditional, END.
    put_word(program, (1u << 24) | (15u << 9) | 1u);
    put_word(program + 4, 2u | (1u << 11) | (2u << 13) | (3u << 15) |
        (7u << 18) | (1u << 23) | (2u << 25) | (3u << 27));
    for (UINT component = 0; component < 4; ++component)
        put_float(program + 16 + component * 4, expected[0][component]);
    char original[sizeof(output.hlsl)] = {};
    u32 original_count = 0;
    const int original_instructions = rsx_d3d12_decompile_fragment(program, sizeof(program),
        0x40u, 0, original, sizeof(original), &original_count);
    if (original_instructions != 1 || original_count != 1) {
        std::fprintf(stderr, "FAIL initial real decompiler instructions=%d constants=%u\n",
            original_instructions, original_count); return false;
    }
    if (rsx_d3d12_snapshot_fragment_constants(output.bank[0], program, sizeof(program)) != 1) return false;
    put_float(program + 16, expected[1][0]);
    if (rsx_d3d12_snapshot_fragment_constants(output.bank[1], program, sizeof(program)) != 1) return false;
    // Both draws are recorded before a third patch; no draw reads this live value.
    put_float(program + 16, 0.875f);
    output.instructions = rsx_d3d12_decompile_fragment(program, sizeof(program), 0x40u, 0,
        output.hlsl, sizeof(output.hlsl), &output.constants);
    if (output.instructions != 1 || output.constants != 1 || std::strcmp(original, output.hlsl)) {
        std::fprintf(stderr, "FAIL generated buffered HLSL changed after literal-only repatch\n"); return false;
    }
    if (RSX_D3D12_FPCB_STRIDE != 2560 || sizeof(output.bank[0]) != 2560) return false;
    for (UINT draw = 0; draw < 2; ++draw) {
        const auto* values = reinterpret_cast<const uint8_t*>(output.bank[draw]) + constant_offset;
        if (std::memcmp(values, expected[draw], sizeof(expected[draw]))) {
            std::fprintf(stderr, "FAIL real snapshot draw=%u payload changed or wrong layout\n", draw + 1); return false;
        }
    }
    std::puts("PASS snapshots slots=1 first_r=0.25 second_r=0.75 repatched_r=0.875");
    return true;
}

static bool shader(const char* text, const char* target, Owned<ID3DBlob>& output)
{
    Owned<ID3DBlob> error;
    const HRESULT result = D3DCompile(text, std::strlen(text), "fragment_constants_fixture", nullptr,
        nullptr, "main", target, D3DCOMPILE_ENABLE_STRICTNESS, 0, &output.p, &error.p);
    if (error.p) std::fwrite(error.p->lpVtbl->GetBufferPointer(error.p), 1,
        error.p->lpVtbl->GetBufferSize(error.p), stderr);
    return checked(result, target);
}

static bool reflected_layout(ID3DBlob* pixel)
{
    Owned<ID3D12ShaderReflection> reflection;
    if (!checked(D3DReflect(pixel->lpVtbl->GetBufferPointer(pixel), pixel->lpVtbl->GetBufferSize(pixel),
            IID_ID3D12ShaderReflection, reflection.raw()), "D3DReflect PS")) return false;
    D3D12_SHADER_INPUT_BIND_DESC binding = {};
    if (!checked(reflection.p->lpVtbl->GetResourceBindingDescByName(reflection.p, "PSConstants", &binding),
            "Reflect PSConstants binding")) return false;
    auto* buffer = reflection.p->lpVtbl->GetConstantBufferByName(reflection.p, "PSConstants");
    auto* variable = buffer->lpVtbl->GetVariableByName(buffer, "fp_constants");
    D3D12_SHADER_VARIABLE_DESC constants = {}, alpha = {};
    if (!checked(variable->lpVtbl->GetDesc(variable, &constants), "Reflect fp_constants")) return false;
    variable = buffer->lpVtbl->GetVariableByName(buffer, "fp_alpha");
    if (!checked(variable->lpVtbl->GetDesc(variable, &alpha), "Reflect fp_alpha")) return false;
    std::printf("STATUS reflection register=b%u alpha_offset=%u constants_offset=%u constants_size=%u\n",
        binding.BindPoint, alpha.StartOffset, constants.StartOffset, constants.Size);
    if (binding.Type != D3D_SIT_CBUFFER || binding.BindPoint != 1 || binding.BindCount != 1 ||
            constants.StartOffset != 272 || constants.Size != 2048 || alpha.StartOffset != 256 || alpha.Size != 16) {
        std::fprintf(stderr, "FAIL reflected layout violates per-draw fragment bank contract\n"); return false;
    }
    return true;
}

static bool reflected_linkage(ID3DBlob* vertex, ID3DBlob* pixel)
{
    Owned<ID3D12ShaderReflection> vs, ps;
    if (!checked(D3DReflect(vertex->lpVtbl->GetBufferPointer(vertex), vertex->lpVtbl->GetBufferSize(vertex),
            IID_ID3D12ShaderReflection, vs.raw()), "D3DReflect VS linkage") ||
            !checked(D3DReflect(pixel->lpVtbl->GetBufferPointer(pixel), pixel->lpVtbl->GetBufferSize(pixel),
                IID_ID3D12ShaderReflection, ps.raw()), "D3DReflect PS linkage")) return false;
    D3D12_SHADER_DESC vs_desc = {}, ps_desc = {};
    if (!checked(vs.p->lpVtbl->GetDesc(vs.p, &vs_desc), "GetDesc VS linkage") ||
            !checked(ps.p->lpVtbl->GetDesc(ps.p, &ps_desc), "GetDesc PS linkage")) return false;
    UINT matched = 0;
    // D3D12 requires the complete declared PS signature, including inputs whose
    // ReadWriteMask is zero in this MOV CONST shader. Do not skip unused fields.
    for (UINT input_index = 0; input_index < ps_desc.InputParameters; ++input_index) {
        D3D12_SIGNATURE_PARAMETER_DESC input = {};
        if (!checked(ps.p->lpVtbl->GetInputParameterDesc(ps.p, input_index, &input), "GetInputParameterDesc")) return false;
        bool found = false;
        for (UINT output_index = 0; output_index < vs_desc.OutputParameters; ++output_index) {
            D3D12_SIGNATURE_PARAMETER_DESC output = {};
            if (!checked(vs.p->lpVtbl->GetOutputParameterDesc(vs.p, output_index, &output), "GetOutputParameterDesc")) return false;
            if (!_stricmp(input.SemanticName, output.SemanticName) && input.SemanticIndex == output.SemanticIndex &&
                    input.ComponentType == output.ComponentType && input.SystemValueType == output.SystemValueType &&
                    (output.Mask & input.Mask) == input.Mask) { found = true; break; }
        }
        if (found) ++matched;
        else std::fprintf(stderr, "FAIL linkage missing PS input %s%u mask=%u type=%u\n",
            input.SemanticName, input.SemanticIndex, input.Mask, input.ComponentType);
    }
    std::printf("STATUS linkage ps_inputs=%u matched=%u missing=%u\n",
        ps_desc.InputParameters, matched, ps_desc.InputParameters - matched);
    return matched == ps_desc.InputParameters;
}

static bool shader_pair(Snapshots& data, Owned<ID3DBlob>& vertex, Owned<ID3DBlob>& pixel)
{
    const char* vertex_hlsl =
        "struct VSOutput{float4 position:SV_POSITION;float4 col0:COLOR0;float4 col1:COLOR1;"
        "float4 fog:FOG;float4 tc0:TEXCOORD0;float4 tc1:TEXCOORD1;float4 tc2:TEXCOORD2;"
        "float4 tc3:TEXCOORD3;float4 tc4:TEXCOORD4;float4 tc5:TEXCOORD5;float4 tc6:TEXCOORD6;float4 tc7:TEXCOORD7;};"
        "VSOutput main(uint id:SV_VertexID){VSOutput o=(VSOutput)0;"
        "float2 p=float2((id<<1)&2,id&2);o.position=float4(p*float2(2,-2)+float2(-1,1),0,1);return o;}";
    return snapshots(data) && shader(vertex_hlsl, "vs_5_0", vertex) &&
        shader(data.hlsl, "ps_5_0", pixel) && reflected_layout(pixel.p) && reflected_linkage(vertex.p, pixel.p);
}

static void read_pixel(const uint8_t* data, UINT pitch, UINT x, UINT y, float (&value)[4])
{
    std::memcpy(value, data + static_cast<size_t>(y) * pitch + x * sizeof(value), sizeof(value));
}

static unsigned pixel_mismatches(const uint8_t* data, UINT pitch, UINT draw)
{
    unsigned mismatches = 0;
    for (UINT y = 0; y < height; ++y) for (UINT x = 0; x < width; ++x) {
        float value[4]; read_pixel(data, pitch, x, y, value);
        bool bad = false;
        for (UINT channel = 0; channel < 4; ++channel)
            bad = bad || !std::isfinite(value[channel]) || std::fabs(value[channel] - expected[draw][channel]) > 0.000001f;
        if (bad) ++mismatches;
    }
    return mismatches;
}

static int oracle_self_test()
{
    uint8_t first[1024] = {}, second[1024] = {}, corrupted[1024] = {};
    const float first_value[4] = {0.25f, 0.125f, 0.5f, 1.0f};
    const float second_value[4] = {0.75f, 0.125f, 0.5f, 1.0f};
    for (UINT y = 0; y < height; ++y) for (UINT x = 0; x < width; ++x) {
        std::memcpy(first + y * 256 + x * 16, first_value, sizeof(first_value));
        std::memcpy(second + y * 256 + x * 16, second_value, sizeof(second_value));
    }
    if (pixel_mismatches(first, 256, 0) || pixel_mismatches(second, 256, 1)) return 1;
    if (pixel_mismatches(first, 256, 1) != 16) return 1; // Stale bank on draw 2.
    if (pixel_mismatches(corrupted, 256, 0) != 16) return 1; // Zero at the consumed layout.
    std::memcpy(corrupted, second, sizeof(second));
    const float invalid = std::numeric_limits<float>::quiet_NaN();
    std::memcpy(corrupted, &invalid, sizeof(invalid));
    if (pixel_mismatches(corrupted, 256, 1) != 1) return 1;
    std::memcpy(corrupted, second, sizeof(second));
    const float bad_alpha = 0.5f;
    std::memcpy(corrupted + 12, &bad_alpha, sizeof(bad_alpha));
    if (pixel_mismatches(corrupted, 256, 1) != 1) return 1;
    std::puts("PASS CPU oracle cases=5 gpu_calls=0");
    return 0;
}

static bool validation(ID3D12InfoQueue* info)
{
    UINT64 errors = 0, warnings = 0;
    const UINT64 count = info->lpVtbl->GetNumStoredMessages(info);
    for (UINT64 index = 0; index < count; ++index) {
        SIZE_T size = 0;
        if (!checked(info->lpVtbl->GetMessage(info, index, nullptr, &size), "GetMessage size")) return false;
        std::vector<uint8_t> storage(size);
        auto* message = reinterpret_cast<D3D12_MESSAGE*>(storage.data());
        if (!checked(info->lpVtbl->GetMessage(info, index, message, &size), "GetMessage")) return false;
        if (message->Severity <= D3D12_MESSAGE_SEVERITY_ERROR) ++errors;
        if (message->Severity == D3D12_MESSAGE_SEVERITY_WARNING) ++warnings;
        std::fprintf(stderr, "[D3D12-IQ][severity=%d id=%d] %s\n", message->Severity, message->ID, message->pDescription);
    }
    std::printf("STATUS validation_messages=%llu errors=%llu warnings=%llu\n",
        static_cast<unsigned long long>(count), static_cast<unsigned long long>(errors), static_cast<unsigned long long>(warnings));
    return !errors && !warnings;
}

static bool resource(ID3D12Device* device, D3D12_HEAP_TYPE type,
        const D3D12_RESOURCE_DESC& desc, D3D12_RESOURCE_STATES state, Owned<ID3D12Resource>& output,
        const D3D12_CLEAR_VALUE* optimized_clear = nullptr)
{
    D3D12_HEAP_PROPERTIES heap = {};
    heap.Type = type; heap.CreationNodeMask = heap.VisibleNodeMask = 1;
    return checked(device->lpVtbl->CreateCommittedResource(device, &heap, D3D12_HEAP_FLAG_NONE,
        &desc, state, optimized_clear, IID_ID3D12Resource, output.raw()), "CreateCommittedResource");
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

int main(int argc, char** argv)
{
    bool warp = false, stale = false, wrong_layout = false, oracle = false, shaders_only = false;
    for (int index = 1; index < argc; ++index) {
        if (!std::strcmp(argv[index], "--warp")) warp = true;
        else if (!std::strcmp(argv[index], "--stale-second-cb")) stale = true;
        else if (!std::strcmp(argv[index], "--wrong-layout")) wrong_layout = true;
        else if (!std::strcmp(argv[index], "--oracle-self-test")) oracle = true;
        else if (!std::strcmp(argv[index], "--shader-self-test")) shaders_only = true;
        else { std::fprintf(stderr, "Usage: rsx_fragment_constants_gpu_probe.exe [--warp] [--stale-second-cb|--wrong-layout|--oracle-self-test|--shader-self-test]\n"); return 2; }
    }
    if (oracle) return oracle_self_test();
    Snapshots data;
    Owned<ID3DBlob> vertex, pixel;
    if (!shader_pair(data, vertex, pixel)) return 2;
    if (shaders_only) {
        std::printf("PASS offline helpers shaders=2 instructions=%d constants=%u stride=%u gpu_calls=0\n",
            data.instructions, data.constants, RSX_D3D12_FPCB_STRIDE);
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
            const HRESULT result = factory.p->lpVtbl->EnumAdapters1(factory.p, index, &candidate.p);
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
    if (!checked(device.p->lpVtbl->QueryInterface(device.p, IID_ID3D12InfoQueue, info.raw()), "QueryInterface InfoQueue")) return 2;

    D3D12_ROOT_PARAMETER parameter = {};
    parameter.ParameterType = D3D12_ROOT_PARAMETER_TYPE_CBV;
    parameter.Descriptor.ShaderRegister = 1;
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
    pipeline_desc.DepthStencilState.DepthFunc = D3D12_COMPARISON_FUNC_ALWAYS;
    pipeline_desc.DepthStencilState.StencilReadMask = D3D12_DEFAULT_STENCIL_READ_MASK;
    pipeline_desc.DepthStencilState.StencilWriteMask = D3D12_DEFAULT_STENCIL_WRITE_MASK;
    pipeline_desc.DepthStencilState.FrontFace = pipeline_desc.DepthStencilState.BackFace =
        {D3D12_STENCIL_OP_KEEP, D3D12_STENCIL_OP_KEEP, D3D12_STENCIL_OP_KEEP, D3D12_COMPARISON_FUNC_ALWAYS};
    pipeline_desc.SampleMask = UINT_MAX;
    pipeline_desc.PrimitiveTopologyType = D3D12_PRIMITIVE_TOPOLOGY_TYPE_TRIANGLE;
    pipeline_desc.NumRenderTargets = 1;
    pipeline_desc.RTVFormats[0] = DXGI_FORMAT_R32G32B32A32_FLOAT;
    pipeline_desc.SampleDesc.Count = 1;
    Owned<ID3D12PipelineState> pipeline;
    if (!checked(device.p->lpVtbl->CreateGraphicsPipelineState(device.p, &pipeline_desc,
            IID_ID3D12PipelineState, pipeline.raw()), "CreateGraphicsPipelineState")) { validation(info.p); return 2; }

    Owned<ID3D12DescriptorHeap> rtv_heap;
    D3D12_DESCRIPTOR_HEAP_DESC heap_desc = {};
    heap_desc.Type = D3D12_DESCRIPTOR_HEAP_TYPE_RTV; heap_desc.NumDescriptors = 1;
    if (!checked(device.p->lpVtbl->CreateDescriptorHeap(device.p, &heap_desc, IID_ID3D12DescriptorHeap, rtv_heap.raw()), "CreateDescriptorHeap RTV")) return 2;
    D3D12_CPU_DESCRIPTOR_HANDLE rtv = {};
    rtv_heap.p->lpVtbl->GetCPUDescriptorHandleForHeapStart(rtv_heap.p, &rtv);
    D3D12_RESOURCE_DESC color_desc = {};
    color_desc.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
    color_desc.Width = width; color_desc.Height = height;
    color_desc.DepthOrArraySize = color_desc.MipLevels = color_desc.SampleDesc.Count = 1;
    color_desc.Format = pipeline_desc.RTVFormats[0];
    color_desc.Flags = D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET;
    Owned<ID3D12Resource> color, readback, constants;
    D3D12_RESOURCE_STATES color_state = D3D12_RESOURCE_STATE_RENDER_TARGET;
    D3D12_CLEAR_VALUE color_clear = {};
    color_clear.Format = color_desc.Format;
    for (float& channel : color_clear.Color) channel = -1.0f;
    if (!resource(device.p, D3D12_HEAP_TYPE_DEFAULT, color_desc, color_state, color, &color_clear)) return 2;
    device.p->lpVtbl->CreateRenderTargetView(device.p, color.p, nullptr, rtv);
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT footprint = {};
    UINT64 bytes = 0;
    device.p->lpVtbl->GetCopyableFootprints(device.p, &color_desc, 0, 1, 0, &footprint, nullptr, nullptr, &bytes);
    const UINT64 readback_stride = (bytes + 511) & ~UINT64(511), readback_bytes = readback_stride * 2;
    D3D12_RESOURCE_DESC buffer_desc = {};
    buffer_desc.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER; buffer_desc.Width = readback_bytes;
    buffer_desc.Height = buffer_desc.DepthOrArraySize = buffer_desc.MipLevels = buffer_desc.SampleDesc.Count = 1;
    buffer_desc.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    if (!resource(device.p, D3D12_HEAP_TYPE_READBACK, buffer_desc, D3D12_RESOURCE_STATE_COPY_DEST, readback)) return 2;
    buffer_desc.Width = sizeof(data.bank);
    if (!resource(device.p, D3D12_HEAP_TYPE_UPLOAD, buffer_desc, D3D12_RESOURCE_STATE_GENERIC_READ, constants)) return 2;
    if (wrong_layout) for (UINT draw = 0; draw < 2; ++draw) {
        auto* bank = reinterpret_cast<uint8_t*>(data.bank[draw]);
        std::memcpy(bank, bank + constant_offset, 16); std::memset(bank + constant_offset, 0, 16);
    }
    const D3D12_RANGE no_read = {0, 0};
    void* mapped_constants = nullptr;
    if (!checked(constants.p->lpVtbl->Map(constants.p, 0, &no_read, &mapped_constants), "Map constants")) return 2;
    std::memcpy(mapped_constants, data.bank, sizeof(data.bank));
    const D3D12_RANGE written = {0, sizeof(data.bank)};
    constants.p->lpVtbl->Unmap(constants.p, 0, &written);
    const D3D12_GPU_VIRTUAL_ADDRESS base = constants.p->lpVtbl->GetGPUVirtualAddress(constants.p);
    if (base % 256) { std::fprintf(stderr, "FAIL CBV alignment\n"); return 2; }
    std::printf("STATUS root_cbv=b1 stride=%u bank_delta=%u pso_creates=1 cb0=0x%llX cb1=0x%llX\n",
        RSX_D3D12_FPCB_STRIDE, RSX_D3D12_FPCB_STRIDE,
        static_cast<unsigned long long>(base), static_cast<unsigned long long>(base + RSX_D3D12_FPCB_STRIDE));

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
    command.p->lpVtbl->SetGraphicsRootSignature(command.p, root.p);
    command.p->lpVtbl->IASetPrimitiveTopology(command.p, D3D_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    const D3D12_VIEWPORT viewport = {0, 0, 4, 4, 0, 1};
    const D3D12_RECT scissor = {0, 0, 4, 4};
    command.p->lpVtbl->RSSetViewports(command.p, 1, &viewport);
    command.p->lpVtbl->RSSetScissorRects(command.p, 1, &scissor);
    for (UINT draw = 0; draw < 2; ++draw) {
        transition(command.p, color.p, color_state, D3D12_RESOURCE_STATE_RENDER_TARGET);
        command.p->lpVtbl->ClearRenderTargetView(command.p, rtv, color_clear.Color, 0, nullptr);
        command.p->lpVtbl->OMSetRenderTargets(command.p, 1, &rtv, FALSE, nullptr);
        const UINT bank = stale && draw == 1 ? 0 : draw;
        command.p->lpVtbl->SetGraphicsRootConstantBufferView(command.p, 0, base + bank * RSX_D3D12_FPCB_STRIDE);
        command.p->lpVtbl->DrawInstanced(command.p, 3, 1, 0, 0);
        command.p->lpVtbl->OMSetRenderTargets(command.p, 0, nullptr, FALSE, nullptr);
        transition(command.p, color.p, color_state, D3D12_RESOURCE_STATE_COPY_SOURCE);
        D3D12_TEXTURE_COPY_LOCATION source = {}, destination = {};
        source.pResource = color.p; source.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
        destination.pResource = readback.p; destination.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT;
        destination.PlacedFootprint = footprint; destination.PlacedFootprint.Offset = draw * readback_stride;
        command.p->lpVtbl->CopyTextureRegion(command.p, &destination, 0, 0, 0, &source, nullptr);
    }
    if (!checked(command.p->lpVtbl->Close(command.p), "Close command list")) { validation(info.p); return 2; }
    ID3D12CommandList* lists[] = {reinterpret_cast<ID3D12CommandList*>(command.p)};
    queue.p->lpVtbl->ExecuteCommandLists(queue.p, 1, lists);
    if (!checked(queue.p->lpVtbl->Signal(queue.p, fence.p, 1), "Signal fence") ||
            !checked(fence.p->lpVtbl->SetEventOnCompletion(fence.p, 1, completed.value), "SetEventOnCompletion")) std::exit(2);
    const DWORD wait = WaitForSingleObject(completed.value, 10000);
    const UINT64 value = fence.p->lpVtbl->GetCompletedValue(fence.p);
    const HRESULT removed = device.p->lpVtbl->GetDeviceRemovedReason(device.p);
    if (wait != WAIT_OBJECT_0 || value == UINT64_MAX || value < 1 || removed != S_OK) {
        std::fprintf(stderr, "FAIL GPU completion wait=%lu fence=%llu removed=0x%08lX\n", wait,
            static_cast<unsigned long long>(value), static_cast<unsigned long>(removed));
        validation(info.p); std::exit(2);
    }
    std::puts("STATUS api_completed=1 fence_completed=1");
    const D3D12_RANGE read = {0, static_cast<SIZE_T>(readback_bytes)}, no_write = {0, 0};
    uint8_t* mapped = nullptr;
    if (!checked(readback.p->lpVtbl->Map(readback.p, 0, &read, reinterpret_cast<void**>(&mapped)), "Map readback")) return 2;
    unsigned errors = 0;
    for (UINT draw = 0; draw < 2; ++draw) {
        const auto* image = mapped + draw * readback_stride;
        const unsigned mismatches = pixel_mismatches(image, footprint.Footprint.RowPitch, draw);
        float rgba[4]; read_pixel(image, footprint.Footprint.RowPitch, 0, 0, rgba);
        std::printf("%s pixels draw=%u mismatches=%u rgba=%.9g,%.9g,%.9g,%.9g\n",
            mismatches ? "FAIL" : "PASS", draw + 1, mismatches, rgba[0], rgba[1], rgba[2], rgba[3]);
        errors += mismatches;
    }
    readback.p->lpVtbl->Unmap(readback.p, 0, &no_write);
    if (!validation(info.p) || errors) return 1;
    std::printf("PASS fragment constants draws=2 pso_creates=1 pixels=32 adapter=%s\n", warp ? "WARP" : "hardware");
    return 0;
}
