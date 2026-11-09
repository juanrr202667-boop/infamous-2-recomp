import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from scripts.inspect_game import inventory, parse_pack_prefix, parse_psarc_header, parse_self_header, parse_sfo, read_psarc_manifest, read_psarc_member_length, read_psarc_member_prefix


class InspectGameTests(unittest.TestCase):
    def test_pack_prefix_reads_resource_table_and_checks_file_length(self):
        table_words = [0, 1, 0, 2, 0, 1]
        table_words += [0x54455854, 10, 0, 0, 0, 0, 2]
        table_words += [0x10, 4, 0, 0, 0x20, 6, 4, 0]
        table_words += [0, 2, 0, 0]
        table = struct.pack(f">{len(table_words)}I", *table_words)
        header_words = [0] * 32
        header_words[0] = 0x5041434B
        header_words[1] = (9 << 16) | 0x80
        header_words[6] = 0x80
        header_words[7] = len(table)
        header_words[10] = 0x80 + len(table)
        header_words[11] = 10
        prefix = struct.pack(">32I", *header_words) + table

        result = parse_pack_prefix(prefix, declared_length=len(prefix) + 10)

        self.assertEqual(result["version"], 9)
        self.assertEqual(result["descriptor_count"], 1)
        self.assertEqual(result["resource_count"], 2)
        self.assertEqual(result["range_record_count"], 1)
        self.assertEqual(result["resource_type_flags"], {"0x00000010": 1, "0x00000020": 1})
        self.assertEqual(result["stored_resource_count"], 2)

        with self.assertRaises(ValueError):
            parse_pack_prefix(prefix, declared_length=len(prefix) + 9)
        with self.assertRaises(ValueError):
            parse_pack_prefix(prefix[:-4], declared_length=len(prefix) + 10)

    def test_retail_self_reports_encrypted_ppu_segments(self):
        data = bytearray(0x400)
        struct.pack_into(">IIHHIQQ", data, 0, 0x53434500, 2, 0x10, 1, 0, 0x400, 0x2000)
        struct.pack_into(">8Q", data, 0x20, 0, 0x70, 0x100, 0x180, 0, 0x200, 0, 0)
        data[0x100:0x104] = b"\x7fELF"
        data[0x104:0x106] = b"\x02\x02"
        struct.pack_into(">H", data, 0x100 + 18, 21)
        struct.pack_into(">H", data, 0x100 + 0x38, 2)
        struct.pack_into(">QQIIII", data, 0x200, 0x300, 0x80, 1, 0, 0, 1)
        struct.pack_into(">QQIIII", data, 0x220, 0x380, 0x80, 1, 0, 0, 0)

        result = parse_self_header(bytes(data))

        self.assertEqual(result["key_revision"], "0x0010")
        self.assertEqual(result["elf_machine"], "PowerPC64")
        self.assertEqual(result["encrypted_segments"], 1)
        self.assertFalse(result["recompilable_elf"])

    def test_sfo_decodes_strings_and_integer(self):
        keys = b"TITLE\x00ATTRIBUTE\x00"
        values = b"inFAMOUS 2\x00" + struct.pack("<I", 3)
        key_offset = 20 + 2 * 16
        data_offset = key_offset + len(keys)
        header = struct.pack("<4s4I", b"\x00PSF", 0x101, key_offset, data_offset, 2)
        entries = struct.pack("<HHIII", 0, 0x204, 11, 11, 0)
        entries += struct.pack("<HHIII", 6, 0x404, 4, 4, 11)

        result = parse_sfo(header + entries + keys + values)

        self.assertEqual(result["TITLE"], "inFAMOUS 2")
        self.assertEqual(result["ATTRIBUTE"], 3)

    def test_psarc_header_reads_big_endian_counts(self):
        header = struct.pack(">4sI4sIIIII", b"PSAR", 0x10003, b"zlib", 0x4D20, 30, 149, 65536, 3)

        result = parse_psarc_header(header)

        self.assertEqual(result["file_count"], 149)
        self.assertEqual(result["block_size"], 65536)
        self.assertEqual(result["compression"], "zlib")

    def test_psarc_manifest_lists_member_names_without_extracting_content(self):
        names = b"assets/cole.mesh\ncode/spu.bin\n"
        compressed = zlib.compress(names)
        toc_len = 32 + 3 * 30 + 2
        header = struct.pack(">4sI4sIIIII", b"PSAR", 0x10003, b"zlib", toc_len, 30, 3, 65536, 3)
        manifest = bytes(16) + struct.pack(">I", 0) + len(names).to_bytes(5, "big") + toc_len.to_bytes(5, "big")
        other = bytes.fromhex("01" * 16) + struct.pack(">I", 1) + (1).to_bytes(5, "big") + (toc_len + len(compressed)).to_bytes(5, "big")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "PS3_GAME" / "USRDIR" / "sample.psarc_s"
            path.parent.mkdir(parents=True)
            path.write_bytes(header + manifest + other + other + len(compressed).to_bytes(2, "big") + compressed)

            result = read_psarc_manifest(path)
            report = inventory(Path(folder))

        self.assertEqual(result, ["assets/cole.mesh", "code/spu.bin"])
        self.assertEqual(report["files"][0]["psarc"]["member_names"], result)

    def test_psarc_member_prefix_decompresses_selected_member(self):
        names = b"/model.xpps\n"
        payload = b"XPP\x00" + bytes(range(64))
        name_chunk = zlib.compress(names)
        data_chunk = zlib.compress(payload)
        toc_len = 32 + 2 * 30 + 2 * 2
        header = struct.pack(">4sI4sIIIII", b"PSAR", 0x10003, b"zlib", toc_len, 30, 2, 65536, 3)
        manifest = bytes(16) + (0).to_bytes(4, "big") + len(names).to_bytes(5, "big") + toc_len.to_bytes(5, "big")
        member = bytes(16) + (1).to_bytes(4, "big") + len(payload).to_bytes(5, "big") + (toc_len + len(name_chunk)).to_bytes(5, "big")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "game.psarc"
            path.write_bytes(header + manifest + member + len(name_chunk).to_bytes(2, "big") + len(data_chunk).to_bytes(2, "big") + name_chunk + data_chunk)

            result = read_psarc_member_prefix(path, "/model.xpps", 8)
            member_length = read_psarc_member_length(path, "/model.xpps")

        self.assertEqual(result, b"XPP\x00\x00\x01\x02\x03")
        self.assertEqual(member_length, len(payload))


if __name__ == "__main__":
    unittest.main()
