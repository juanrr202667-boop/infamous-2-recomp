#pragma once
#include <cstddef>
#include <cstdint>

namespace fast_inflate {

// Decompress zlib (RFC 1950) or raw deflate (RFC 1951) data into dst.
// Returns true only after consuming src_len bytes and producing exactly dst_len.
// Rejects corruption, overlap, unsupported dictionaries and zero-length buffers.
// On failure dst may contain partial output; callers must not publish completion.
bool decompress(uint8_t* dst, size_t dst_len, const uint8_t* src, size_t src_len);

} // namespace fast_inflate
