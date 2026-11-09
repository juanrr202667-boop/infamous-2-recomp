#include "fast_inflate.h"
#include <cstring>

namespace fast_inflate {

struct BitReader {
    const uint8_t* src;
    size_t src_len;
    size_t src_pos;
    uint32_t bit_buf;
    int num_bits;
    bool failed;

    void init(const uint8_t* s, size_t len) {
        src = s;
        src_len = len;
        src_pos = 0;
        bit_buf = 0;
        num_bits = 0;
        failed = false;
    }

    uint32_t get_bits(int n) {
        while (num_bits < n) {
            if (src_pos == src_len) { failed = true; return 0; }
            uint32_t b = src[src_pos++];
            bit_buf |= (b << num_bits);
            num_bits += 8;
        }
        uint32_t val = bit_buf & ((1u << n) - 1u);
        bit_buf >>= n;
        num_bits -= n;
        return val;
    }
};

struct Tree {
    uint16_t counts[16];
    uint16_t symbols[288];

    bool build(const uint8_t* lengths, int num, bool allow_empty = false, bool allow_single = true) {
        std::memset(counts, 0, sizeof(counts));
        for (int i = 0; i < num; ++i) {
            if (lengths[i] > 15) return false;
            counts[lengths[i]]++;
        }
        counts[0] = 0;

        int available = 1, used = 0, maximum = 0;
        for (int length = 1; length <= 15; ++length) {
            available = available * 2 - counts[length];
            if (available < 0) return false; // oversubscribed prefix codes
            used += counts[length];
            if (counts[length]) maximum = length;
        }
        if (!used) return allow_empty;
        if (available && !(allow_single && used == 1 && maximum == 1)) return false;

        uint16_t offsets[16];
        offsets[1] = 0;
        for (int i = 1; i < 15; ++i) {
            offsets[i + 1] = offsets[i] + counts[i];
        }

        for (int i = 0; i < num; ++i) {
            if (lengths[i]) {
                symbols[offsets[lengths[i]]++] = (uint16_t)i;
            }
        }
        return true;
    }

    int decode(BitReader& r) const {
        int code = 0;
        int first = 0;
        int index = 0;
        for (int len = 1; len <= 15; ++len) {
            code |= (int)r.get_bits(1);
            if (r.failed) return -1;
            int count = counts[len];
            if (code - first < count) {
                return symbols[index + (code - first)];
            }
            index += count;
            first += count;
            first <<= 1;
            code <<= 1;
        }
        return -1;
    }
};

static const uint8_t clc_order[19] = {
    16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15
};

static const uint16_t length_base[29] = {
    3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31,
    35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258
};
static const uint8_t length_extra[29] = {
    0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2,
    3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0
};

static const uint16_t dist_base[30] = {
    1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193,
    257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577
};
static const uint8_t dist_extra[30] = {
    0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6,
    7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13
};

struct FixedTrees {
    Tree literal;
    Tree distance;
    FixedTrees() {
        uint8_t llengths[288];
        for (int i = 0; i <= 143; ++i) llengths[i] = 8;
        for (int i = 144; i <= 255; ++i) llengths[i] = 9;
        for (int i = 256; i <= 279; ++i) llengths[i] = 7;
        for (int i = 280; i <= 287; ++i) llengths[i] = 8;
        literal.build(llengths, 288);

        uint8_t dlengths[32];
        for (int i = 0; i < 32; ++i) dlengths[i] = 5;
        distance.build(dlengths, 32);
    }
};

static bool inflate_block_data(BitReader& r, const Tree& ltree, const Tree& dtree,
                               uint8_t* out, size_t out_max, size_t& out_pos) {
    for (;;) {
        int sym = ltree.decode(r);
        if (sym < 0 || sym > 285) return false;
        if (sym < 256) {
            if (out_pos >= out_max) return false;
            out[out_pos++] = (uint8_t)sym;
        } else if (sym == 256) {
            return true; // end of block
        } else {
            sym -= 257;
            uint32_t len = length_base[sym] + r.get_bits(length_extra[sym]);
            if (r.failed) return false;
            int dsym = dtree.decode(r);
            if (dsym < 0 || dsym >= 30) return false;
            uint32_t dist = dist_base[dsym] + r.get_bits(dist_extra[dsym]);
            if (r.failed || dist > out_pos) return false;
            if (len > out_max - out_pos) return false;
            const uint8_t* src_ref = out + out_pos - dist;
            for (uint32_t i = 0; i < len; ++i) {
                out[out_pos++] = src_ref[i];
            }
        }
    }
}

bool decompress(uint8_t* dst, size_t dst_len, const uint8_t* src, size_t src_len) {
    if (!dst || !src || dst_len == 0 || src_len == 0) return false;
    const uintptr_t destination = reinterpret_cast<uintptr_t>(dst);
    const uintptr_t source = reinterpret_cast<uintptr_t>(src);
    if (destination >= source ? destination - source < src_len : source - destination < dst_len) return false;
    static const FixedTrees fixed; // C++ guarantees thread-safe initialization.

    // Check zlib header (RFC 1950)
    size_t offset = 0;
    size_t deflate_len = src_len;
    bool wrapped = false;
    if (src_len >= 2 && (src[0] & 0x0F) == 8 && (((src[0] << 8) | src[1]) % 31 == 0)) {
        if ((src[0] >> 4) > 7 || (src[1] & 0x20) || src_len < 6) return false;
        wrapped = true;
        offset = 2;
        deflate_len = src_len - 6;
    }

    BitReader r;
    r.init(src + offset, deflate_len);

    size_t out_pos = 0;
    bool bfinal = false;

    while (!bfinal) {
        bfinal = (r.get_bits(1) != 0);
        uint32_t btype = r.get_bits(2);
        if (r.failed) return false;

        if (btype == 0) { // uncompressed
            r.bit_buf = 0;
            r.num_bits = 0;
            if (r.src_len - r.src_pos < 4) return false;
            uint16_t len = r.src[r.src_pos] | (r.src[r.src_pos + 1] << 8);
            uint16_t nlen = r.src[r.src_pos + 2] | (r.src[r.src_pos + 3] << 8);
            r.src_pos += 4;
            if ((uint16_t)(len ^ 0xFFFF) != nlen) return false;
            if (len > r.src_len - r.src_pos || len > dst_len - out_pos) return false;
            std::memcpy(dst + out_pos, r.src + r.src_pos, len);
            r.src_pos += len;
            out_pos += len;
        } else if (btype == 1) { // fixed Huffman
            if (!inflate_block_data(r, fixed.literal, fixed.distance, dst, dst_len, out_pos)) {
                return false;
            }
        } else if (btype == 2) { // dynamic Huffman
            uint32_t hlit = r.get_bits(5) + 257;
            uint32_t hdist = r.get_bits(5) + 1;
            uint32_t hclen = r.get_bits(4) + 4;

            if (r.failed || hlit > 286 || hdist > 30) return false;

            uint8_t cl_lens[19] = {};
            for (uint32_t i = 0; i < hclen; ++i) {
                cl_lens[clc_order[i]] = (uint8_t)r.get_bits(3);
            }
            if (r.failed) return false;

            Tree cl_tree;
            if (!cl_tree.build(cl_lens, 19, false, false)) return false;

            uint8_t tree_lens[288 + 32] = {};
            uint32_t total = hlit + hdist;
            uint32_t cur = 0;
            while (cur < total) {
                int sym = cl_tree.decode(r);
                if (sym < 0 || sym > 18) return false;
                if (sym < 16) {
                    tree_lens[cur++] = (uint8_t)sym;
                } else if (sym == 16) {
                    if (cur == 0) return false;
                    uint32_t rep = 3 + r.get_bits(2);
                    if (r.failed || rep > total - cur) return false;
                    uint8_t last = tree_lens[cur - 1];
                    while (rep--) tree_lens[cur++] = last;
                } else if (sym == 17) {
                    uint32_t rep = 3 + r.get_bits(3);
                    if (r.failed || rep > total - cur) return false;
                    while (rep--) tree_lens[cur++] = 0;
                } else if (sym == 18) {
                    uint32_t rep = 11 + r.get_bits(7);
                    if (r.failed || rep > total - cur) return false;
                    while (rep--) tree_lens[cur++] = 0;
                }
            }

            Tree dyn_ltree, dyn_dtree;
            if (!tree_lens[256]) return false; // EOB must have a code.
            if (!dyn_ltree.build(tree_lens, hlit)) return false;
            if (!dyn_dtree.build(tree_lens + hlit, hdist, true)) return false;

            if (!inflate_block_data(r, dyn_ltree, dyn_dtree, dst, dst_len, out_pos)) {
                return false;
            }
        } else {
            return false;
        }
    }

    if (r.failed || out_pos != dst_len || r.src_pos != r.src_len) return false;
    if (wrapped) {
        uint32_t a = 1, b = 0;
        for (size_t i = 0; i < out_pos; ++i) {
            a = (a + dst[i]) % 65521u;
            b = (b + a) % 65521u;
        }
        const uint8_t* trailer = src + src_len - 4;
        const uint32_t expected = (uint32_t(trailer[0]) << 24) | (uint32_t(trailer[1]) << 16) |
                                  (uint32_t(trailer[2]) << 8) | trailer[3];
        if ((b << 16 | a) != expected) return false;
    }
    return true;
}

} // namespace fast_inflate
