"""Unit tests for GCM live chain inspector mapping validation and table pointers."""
import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ELF_PATH = ROOT / "build" / "game" / "EBOOT.ELF"


def table_pointer_valid(pointer):
    """Mirror of inspect_gcm_live_chain.table_pointer_valid."""
    return isinstance(pointer, int) and 0x10000 <= pointer <= 0xFFFFE000 and pointer % 2 == 0


def io2ea_lookup(ea_table_ptr, io, half_fn):
    if not table_pointer_valid(ea_table_ptr) or not isinstance(io, int) or not 0 <= io <= 0xFFFFFFFF:
        return None
    entry_ea = ea_table_ptr + (io >> 20) * 2
    page = half_fn(entry_ea)
    return (page << 20) | (io & 0xFFFFF) if page is not None and page != 0xFFFF else None


def ea2io_lookup(io_table_ptr, ea, half_fn):
    if not table_pointer_valid(io_table_ptr) or not isinstance(ea, int) or not 0 <= ea <= 0xFFFFFFFF:
        return None
    entry_ea = io_table_ptr + (ea >> 20) * 2
    page = half_fn(entry_ea)
    return (page << 20) | (ea & 0xFFFFF) if page is not None and page != 0xFFFF else None


def validate_mapping(io_table, ea_table, validation_addresses, half_fn):
    errors = [
        f"{key} is not a bounded, aligned guest table pointer"
        for key, ptr in (("io_table", io_table), ("ea_table", ea_table))
        if not table_pointer_valid(ptr)
    ]
    roundtrips = []
    for name, ea in validation_addresses:
        io = ea2io_lookup(io_table, ea, half_fn)
        back = io2ea_lookup(ea_table, io, half_fn) if io is not None else None
        valid = isinstance(ea, int) and ea >= 0x10000 and io is not None and back == ea
        roundtrips.append({"name": name, "ea": ea, "io": io, "roundtrip_ea": back, "valid": valid})
        if not valid:
            errors.append(f"{name} does not roundtrip through the guest offset tables")
    return {"valid": not errors, "errors": errors, "roundtrip_samples": roundtrips}


class GcmChainTableValidationTests(unittest.TestCase):
    def test_table_pointer_boundary_and_alignment(self):
        # 1 was the historical bug where driver +0x30 (flag 1) was accepted
        self.assertFalse(table_pointer_valid(1))
        self.assertFalse(table_pointer_valid(0))
        self.assertFalse(table_pointer_valid(0xFF))
        self.assertFalse(table_pointer_valid(0x20003001))  # unaligned
        self.assertFalse(table_pointer_valid(0xFFFFE002))  # out of upper bound
        self.assertFalse(table_pointer_valid(None))
        self.assertFalse(table_pointer_valid("0x20003000"))

        # Valid pointers
        self.assertTrue(table_pointer_valid(0x10000))
        self.assertTrue(table_pointer_valid(0x20003000))
        self.assertTrue(table_pointer_valid(0x20005000))
        self.assertTrue(table_pointer_valid(0xB01234))
        self.assertTrue(table_pointer_valid(0xB01238))
        self.assertTrue(table_pointer_valid(0xFFFFE000))

    def test_elf_table_pointer_struct_instructions(self):
        """Confirm from EBOOT.ELF that table pointers are stored at +0x34 and +0x38."""
        if not ELF_PATH.is_file():
            self.skipTest(f"EBOOT.ELF not found at {ELF_PATH}")

        with ELF_PATH.open("rb") as f:
            hdr = f.read(64)
            self.assertEqual(hdr[:4], b"\x7fELF")
            e_phoff, e_phnum = struct.unpack(">QQ", hdr[32:48])[:2]
            f.seek(e_phoff)
            file_offset = None
            for _ in range(e_phnum):
                p_type, _, p_offset, p_vaddr, _, p_filesz, _, _ = struct.unpack(">IIQQQQQQ", f.read(56))
                if p_type == 1 and p_vaddr <= 0x004EF28C < p_vaddr + p_filesz:
                    file_offset = p_offset + (0x004EF28C - p_vaddr)
                    break

            self.assertIsNotNone(file_offset, "Segment containing 0x004EF28C not found")
            f.seek(file_offset)
            words = struct.unpack(">II", f.read(8))
            # 004EF28C: 90190034 -> stw r0, 0x34(r25) (io_table at +0x34)
            # 004EF290: 91390038 -> stw r9, 0x38(r25) (ea_table at +0x38)
            self.assertEqual(words[0], 0x90190034, "Expected stw r0, 0x34(r25) at 0x004EF28C")
            self.assertEqual(words[1], 0x91390038, "Expected stw r9, 0x38(r25) at 0x004EF290")

    def test_valid_synthetic_table_roundtrip(self):
        """Synthetic memory table mapping where rings and buffers roundtrip."""
        io_table_base = 0x20003000
        ea_table_base = 0x20005000

        # Map EA 0x40410000 (page 0x404) <-> IO 0x00010000 (page 0x000)
        # Map EA 0x40910000 (page 0x409) <-> IO 0x00510000 (page 0x005)
        mem = {}

        def write_half(addr, val):
            mem[addr] = val & 0xFFFF

        def read_half(addr):
            return mem.get(addr, 0xFFFF)

        # EA to IO table: at io_table_base + (EA >> 20) * 2
        write_half(io_table_base + 0x404 * 2, 0x000)  # EA page 0x404 -> IO page 0x000
        write_half(io_table_base + 0x409 * 2, 0x005)  # EA page 0x409 -> IO page 0x005

        # IO to EA table: at ea_table_base + (IO >> 20) * 2
        write_half(ea_table_base + 0x000 * 2, 0x404)  # IO page 0x000 -> EA page 0x404
        write_half(ea_table_base + 0x005 * 2, 0x409)  # IO page 0x005 -> EA page 0x409

        addresses = [
            ("ring0-begin", 0x40410000),
            ("ring1-begin", 0x40411000),
            ("buffer0-start", 0x40914400),
        ]

        result = validate_mapping(io_table_base, ea_table_base, addresses, read_half)
        self.assertTrue(result["valid"])
        self.assertEqual(len(result["errors"]), 0)
        for sample in result["roundtrip_samples"]:
            self.assertTrue(sample["valid"])
            self.assertEqual(sample["roundtrip_ea"], sample["ea"])

    def test_reject_shifted_pointer_and_unmapped_pages(self):
        """Simulate old bug: pointer 1 and unmapped pages produce validation errors."""
        mem = {}

        def read_half(addr):
            return mem.get(addr, 0xFFFF)

        addresses = [
            ("ring0-begin", 0x40410000),
            ("buffer0-start", 0x40914400),
        ]

        # Case 1: pointer = 1 (shifted pointer bug)
        res_bug = validate_mapping(1, 0x20005000, addresses, read_half)
        self.assertFalse(res_bug["valid"])
        self.assertTrue(any("io_table is not a bounded" in err for err in res_bug["errors"]))

        # Case 2: valid pointers but unmapped pages (returning 0xFFFF)
        res_unmapped = validate_mapping(0x20003000, 0x20005000, addresses, read_half)
        self.assertFalse(res_unmapped["valid"])
        self.assertEqual(len(res_unmapped["errors"]), len(addresses))
        self.assertTrue(all("does not roundtrip" in err for err in res_unmapped["errors"]))

    def test_reject_asymmetric_roundtrip_mismatch(self):
        """When ea2io maps to IO page, but io2ea maps back to a different EA page."""
        io_table_base = 0x20003000
        ea_table_base = 0x20005000
        mem = {}

        def write_half(addr, val):
            mem[addr] = val & 0xFFFF

        def read_half(addr):
            return mem.get(addr, 0xFFFF)

        # EA 0x40410000 -> IO 0x00010000
        write_half(io_table_base + 0x404 * 2, 0x000)
        # But IO 0x000 maps to EA page 0x405 (mismatch!)
        write_half(ea_table_base + 0x000 * 2, 0x405)

        addresses = [("ring0-begin", 0x40410000)]
        res = validate_mapping(io_table_base, ea_table_base, addresses, read_half)
        self.assertFalse(res["valid"])
        self.assertEqual(res["roundtrip_samples"][0]["roundtrip_ea"], 0x40510000)
        self.assertNotEqual(res["roundtrip_samples"][0]["roundtrip_ea"], 0x40410000)
        self.assertIn("ring0-begin does not roundtrip", res["errors"][0])


if __name__ == "__main__":
    unittest.main()
