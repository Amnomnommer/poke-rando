"""Generated-data tests for structural NARC reading (member storage order independent of index order).

Real failure: `merged data/a/0/0/4: NARC member 15065 overlaps the previous member`. Pinned CTRMap's
DirectNARC.insertEntries writes member data in a stable sort by each entry's previous start offset, and
entries appended past the base count start as (0, 0), so the first appended member (index == base count,
15065 there) is stored right after member 0. The FAT ranges never overlap; the old byte-reproducible
parser wrongly assumed index-ordered storage. Cross-checked locally against the pinned jar and ndspy.
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from b2er.assembly import narc_premerge as npm  # noqa: E402
from b2er.nds import FormatError  # noqa: E402
from b2er.nds.narc import Narc, read_layout, read_members, replace_members_same_size  # noqa: E402


def ctrmap_merge(base: list[bytes], replacements: dict[int, bytes]) -> bytes:
    """Model of CTRMap's insertEntries: stable sort by previous start, (0, 0) for appended entries,
    0xFF padding to the absolute 4-byte boundary after every entry."""
    count = max([len(base) - 1, *replacements]) + 1
    previous, cursor = [], 0
    for member in base:
        cursor += -cursor % 4
        previous.append(cursor)
        cursor += len(member)
    previous += [0] * (count - len(base))
    data = [replacements.get(i, base[i] if i < len(base) else b"") for i in range(count)]
    header = 0x10 + 12 + 8 * count + 16  # NARC + BTAF + BTNF(8 + 8-byte FNT)
    payload, ranges = bytearray(), [None] * count
    for i in sorted(range(count), key=lambda i: previous[i]):
        start = len(payload)
        payload += data[i]
        ranges[i] = (start, len(payload))
        while (header + 8 + len(payload)) % 4:
            payload.append(0xFF)
    return assemble(ranges, bytes(payload))


def assemble(ranges, payload: bytes, fat_count: int | None = None, fnt: bytes = struct.pack("<IHH", 4, 0, 1)) -> bytes:
    fat = b"".join(struct.pack("<II", s, e) for s, e in ranges)
    btaf = b"BTAF" + struct.pack("<IHH", 12 + len(fat), len(ranges) if fat_count is None else fat_count, 0) + fat
    btnf = b"BTNF" + struct.pack("<I", 8 + len(fnt)) + fnt
    gmif = b"GMIF" + struct.pack("<I", 8 + len(payload)) + payload
    body = btaf + btnf + gmif
    return b"NARC" + struct.pack("<HHIHH", 0xFFFE, 0x0100, 0x10 + len(body), 0x10, 3) + body


def reference_members(data: bytes) -> list[bytes]:
    """Independent spec-level reader: FAT entries are (start, end) offsets into the GMIF payload."""
    fat_size = struct.unpack_from("<I", data, 0x14)[0]
    count = struct.unpack_from("<H", data, 0x18)[0]
    btnf = 0x10 + fat_size
    gmif = btnf + struct.unpack_from("<I", data, btnf + 4)[0]
    payload = data[gmif + 8:]
    return [payload[s:e] for s, e in (struct.unpack_from("<II", data, 0x1C + 8 * i) for i in range(count))]


BASE = [bytes([i % 251]) * (5 + i % 7) for i in range(40)]


class LayoutTests(unittest.TestCase):
    def test_a004_shaped_out_of_order_merge(self) -> None:
        new = {i: bytes([0xA0 + i % 50]) * (3 + i % 5) for i in [*range(10, 15), *range(40, 52)]}
        new[43] = b""
        merged = ctrmap_merge(BASE, new)
        layout = read_layout(merged)
        expected = [new.get(i, BASE[i] if i < 40 else b"") for i in range(52)]
        self.assertEqual(read_members(merged), expected)
        self.assertEqual(reference_members(merged), expected)
        self.assertFalse(layout.describe()["in_index_order"])
        self.assertEqual(layout.ranges[40][0], 8, "first appended member is stored right after member 0")
        with self.assertRaises(FormatError):
            Narc.parse(merged)  # the byte-reproducible parser stays strict about its own layout
        self.assertEqual(npm.narc_extent(merged, "data/a/0/0/4"), len(merged))
        self.assertNotIn("members_data", npm.describe(merged))

    def test_large_valid_member_table(self) -> None:
        base = [bytes([i & 0xFF]) * (i % 9) for i in range(15065)]
        merged = ctrmap_merge(base, {15065 + k: b"\x55" * (4 + k) for k in range(30)})
        layout = read_layout(merged)
        self.assertEqual(layout.count, 15095)
        self.assertEqual(read_members(merged)[15065], b"\x55" * 4)
        self.assertEqual(read_members(merged)[:15065], base)

    def test_relative_offsets_zero_length_and_equal_boundaries(self) -> None:
        payload = b"AAAA" + b"BB" + b"\xFF\xFF" + b"CCCC"
        data = assemble([(0, 4), (4, 6), (6, 6), (8, 12), (12, 12), (0, 0)], payload)
        self.assertEqual(read_members(data), [b"AAAA", b"BB", b"", b"CCCC", b"", b""])
        self.assertEqual(reference_members(data), read_members(data))
        self.assertEqual(read_layout(data).describe()["empty_members"], 3)

    def test_genuine_overlap_rejected(self) -> None:
        for ranges in ([(0, 8), (4, 12)], [(0, 8), (0, 8)], [(4, 12), (0, 8)], [(0, 12), (4, 8)]):
            with self.assertRaises(FormatError) as ctx:
                read_layout(assemble(ranges, b"X" * 12))
            self.assertIn("overlap", str(ctx.exception))

    def test_gaps_must_be_alignment_padding(self) -> None:
        with self.assertRaises(FormatError):
            read_layout(assemble([(0, 2), (8, 12)], b"AA" + b"\xFF" * 6 + b"CCCC"))  # 6-byte gap
        with self.assertRaises(FormatError):
            read_layout(assemble([(0, 2), (4, 8)], b"AA" + b"\x02\x02" + b"CCCC"))  # stale, not padding
        with self.assertRaises(FormatError):
            read_layout(assemble([(0, 4)], b"AAAA" + b"\xFF" * 8))  # unreferenced tail
        self.assertEqual(read_members(assemble([(0, 2), (4, 8)], b"AA\x00\x00CCCC")), [b"AA", b"CCCC"])
        with self.assertRaises(npm.PremergeError):  # merged archives must use CTRMap's 0xFF padding
            npm.narc_extent(assemble([(0, 2), (4, 8)], b"AA\x00\x00CCCC"), "x")

    def test_truncated_fat_and_count_exceeding_entries(self) -> None:
        good = assemble([(0, 4)], b"AAAA")
        with self.assertRaises(FormatError) as ctx:
            read_layout(assemble([(0, 4)], b"AAAA", fat_count=2))
        self.assertIn("FAT declares 2 entries", str(ctx.exception))
        truncated = bytearray(good[:0x1C])
        struct.pack_into("<I", truncated, 8, len(truncated))
        with self.assertRaises(FormatError):
            read_layout(bytes(truncated))
        with self.assertRaises(FormatError):
            read_layout(assemble([(0, 8)], b"AAAA"))  # range beyond payload

    def test_malformed_sections_and_header(self) -> None:
        good = assemble([(0, 4)], b"AAAA")
        cases = []
        for offset, value, fmt in ((0x14, 0xFFFF, "<I"), (0x14, 6, "<I"), (0x0C, 0x20, "<H"), (0x0E, 2, "<H"), (8, len(good) + 4, "<I")):
            bad = bytearray(good)
            struct.pack_into(fmt, bad, offset, value)
            cases.append(bytes(bad))
        bad = bytearray(good)
        bad[0x10:0x14] = b"FATB"
        cases.append(bytes(bad))
        cases.append(good + b"\xFF" * 4)
        for data in cases:
            with self.assertRaises(FormatError):
                read_layout(data)

    def test_byte_order(self) -> None:
        good = bytearray(assemble([(0, 4)], b"AAAA"))
        self.assertEqual(read_members(bytes(good)), [b"AAAA"])
        struct.pack_into("<H", good, 4, 0xFEFF)  # big-endian form: not produced by NDS tools, refused
        with self.assertRaises(FormatError) as ctx:
            read_layout(bytes(good))
        self.assertIn("byte-order", str(ctx.exception))

    def test_round_trip_determinism_and_in_place_patch(self) -> None:
        new = {12: b"\x77" * 6, 41: b"\x66" * 3}
        merged = ctrmap_merge(BASE, new)
        self.assertEqual({ctrmap_merge(BASE, new) for _ in range(3)}, {merged})
        index_ordered = Narc(read_members(merged)).build()
        self.assertEqual(read_members(index_ordered), read_members(merged))
        self.assertEqual(Narc.parse(index_ordered).members, read_members(merged))
        patched = replace_members_same_size(merged, {41: b"\x11" * 3})
        self.assertEqual(len(patched), len(merged))
        self.assertEqual(read_members(patched)[41], b"\x11" * 3)
        self.assertEqual(read_members(patched)[:41], read_members(merged)[:41])
        with self.assertRaises(FormatError):
            replace_members_same_size(merged, {41: b"\x11" * 4})


if __name__ == "__main__":
    unittest.main()
