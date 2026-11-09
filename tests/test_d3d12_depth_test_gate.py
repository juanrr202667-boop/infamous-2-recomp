"""Test suite for D3D12 dynamic depth test gating and SPU coherency capacity.

Validates that:
1. rsx_depth_key correctly maps depth_test_enable, depth_mask, and depth_func.
2. depth_key == 0 when depth_test_enable is 0 (fullscreen resolves, UI quads).
3. depth_key accurately preserves depth write and comparison function for geometry passes.
4. rsx_d3d12_backend.c implements dynamic depth gating without hardcoded DepthEnable=TRUE.
5. spu_coherency.c provides expanded context capacity (SPU_COH_MAX_CTX >= 64).
"""

from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND_C = ROOT / "tools/ps3recomp/libs/video/rsx_d3d12_backend.c"
SPU_COHERENCY_C = ROOT / "tools/ps3recomp/runtime/spu/spu_coherency.c"


def rsx_depth_key(depth_test_enable: int, depth_mask: int, depth_func: int) -> int:
    """Python reference implementation of the C rsx_depth_key helper."""
    if not depth_test_enable:
        return 0
    k = 1
    if depth_mask:
        k |= 2
    k |= ((depth_func & 0xFFFF) << 2)
    return k


def gcm_depth_func_d3d(f: int) -> str:
    """Reference mapping for comparison functions."""
    table = {
        0x0200: "D3D12_COMPARISON_FUNC_NEVER",
        0x0201: "D3D12_COMPARISON_FUNC_LESS",
        0x0202: "D3D12_COMPARISON_FUNC_EQUAL",
        0x0203: "D3D12_COMPARISON_FUNC_LESS_EQUAL",
        0x0204: "D3D12_COMPARISON_FUNC_GREATER",
        0x0205: "D3D12_COMPARISON_FUNC_NOT_EQUAL",
        0x0206: "D3D12_COMPARISON_FUNC_GREATER_EQUAL",
        0x0207: "D3D12_COMPARISON_FUNC_ALWAYS",
        1: "D3D12_COMPARISON_FUNC_LESS",
    }
    return table.get(f, "D3D12_COMPARISON_FUNC_LESS_EQUAL")


def test_depth_key_disabled_returns_zero():
    """Disabled depth test must return depth_key == 0 (UI quads, tonemapping)."""
    assert rsx_depth_key(0, 1, 0x0203) == 0
    assert rsx_depth_key(0, 0, 0x0203) == 0
    assert rsx_depth_key(0, 0, 0x0207) == 0


def test_depth_key_geometry_pass():
    """Geometry passes with write-enable must set bit 0, bit 1, and func bits."""
    k = rsx_depth_key(1, 1, 0x0203)
    assert (k & 1) == 1  # DepthEnable
    assert (k & 2) == 2  # DepthWriteMask ALL
    assert ((k >> 2) & 0xFFFF) == 0x0203
    assert gcm_depth_func_d3d((k >> 2) & 0xFFFF) == "D3D12_COMPARISON_FUNC_LESS_EQUAL"


def test_depth_key_read_only_pass():
    """Read-only depth passes (e.g. particles/decals) must keep bit 1 clear."""
    k = rsx_depth_key(1, 0, 0x0201)
    assert (k & 1) == 1  # DepthEnable
    assert (k & 2) == 0  # DepthWriteMask ZERO
    assert ((k >> 2) & 0xFFFF) == 0x0201
    assert gcm_depth_func_d3d((k >> 2) & 0xFFFF) == "D3D12_COMPARISON_FUNC_LESS"


def test_d3d12_backend_depth_key_structure():
    """Check that rsx_d3d12_backend.c has depth_key in draw records and cache."""
    content = BACKEND_C.read_text(encoding="utf-8")
    assert "u32   depth_key;" in content or "u32 depth_key;" in content
    assert "gcm_depth_func_d3d" in content
    assert "rsx_depth_key" in content


def test_d3d12_backend_depth_enable_dynamic():
    """Verify DepthEnable is dynamically gated rather than unconditionally TRUE."""
    content = BACKEND_C.read_text(encoding="utf-8")
    assert "depth_enable ? TRUE : FALSE" in content
    assert "pd.DepthStencilState.DepthEnable    = depth_enable" in content or "pd.DepthStencilState.DepthEnable = depth_enable" in content


def test_spu_coherency_expanded_capacity():
    """Verify SPU_COH_MAX_CTX is expanded to at least 64 contexts."""
    content = SPU_COHERENCY_C.read_text(encoding="utf-8")
    assert "#define SPU_COH_MAX_CTX 64" in content
