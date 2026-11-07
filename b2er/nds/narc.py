"""Strict NARC (Nitro archive) parsing and byte-preserving rebuilding.

Layout: ``NARC`` header (magic, BOM 0xFFFE, version, file size, header size 0x10,
section count 3), then ``BTAF`` (FAT: count, start/end pairs relative to the
GMIF payload), ``BTNF`` (name table, kept verbatim), and ``GMIF`` (payload).
On disk the section tags are byte-reversed (``BTAF`` is the FATB section).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from . import FormatError


@dataclass
class Narc:
    members: list[bytes]
    fnt: bytes = struct.pack("<IHH", 4, 0, 1)
    version: int = 0x0100
    pad_byte: int = 0xFF
    alignment: int = 4
    pad_tail: bool = True

    # -- parsing -------------------------------------------------------------
    @classmethod
    def parse(cls, data: bytes) -> "Narc":
        if len(data) < 0x10 or data[:4] != b"NARC":
            raise FormatError("not a NARC (missing magic)")
        bom, version, file_size, header_size, sections = struct.unpack_from("<HHIHH", data, 4)
        if bom != 0xFFFE or header_size != 0x10 or sections != 3:
            raise FormatError("NARC header is not the expected little-endian 3-section form")
        if file_size != len(data):
            raise FormatError(f"NARC declares {file_size} bytes but is {len(data)}")
        pos = 0x10
        tags = {}
        for expected in (b"BTAF", b"BTNF", b"GMIF"):
            if pos + 8 > len(data):
                raise FormatError(f"NARC section {expected!r} is truncated")
            tag, size = data[pos : pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
            if tag != expected:
                raise FormatError(f"expected NARC section {expected!r}, found {tag!r}")
            if size < 8 or pos + size > len(data):
                raise FormatError(f"NARC section {expected!r} has an invalid size")
            tags[expected] = (pos, size)
            pos += size
        if pos != len(data):
            raise FormatError("NARC has trailing bytes after GMIF")
        fat_pos, fat_size = tags[b"BTAF"]
        count = struct.unpack_from("<H", data, fat_pos + 8)[0]
        if 12 + count * 8 != fat_size:
            raise FormatError("NARC FAT size does not match its entry count")
        gmif_pos, gmif_size = tags[b"GMIF"]
        payload = data[gmif_pos + 8 : gmif_pos + gmif_size]
        members = []
        previous_end = 0
        pad_byte = 0xFF
        for i in range(count):
            start, end = struct.unpack_from("<II", data, fat_pos + 12 + i * 8)
            if start > end or end > len(payload):
                raise FormatError(f"NARC member {i} range 0x{start:x}..0x{end:x} is out of bounds")
            if start < previous_end:
                raise FormatError(f"NARC member {i} overlaps the previous member")
            if start > previous_end:
                pad_byte = payload[previous_end]
            members.append(bytes(payload[start:end]))
            previous_end = end
        btnf_pos, btnf_size = tags[b"BTNF"]
        starts = [struct.unpack_from("<I", data, fat_pos + 12 + i * 8)[0] for i in range(count)]
        alignment = 4 if all(start % 4 == 0 for start in starts) else 1
        if len(payload) > previous_end:
            pad_byte = payload[previous_end]
        narc = cls(members, bytes(data[btnf_pos + 8 : btnf_pos + btnf_size]), version, pad_byte, alignment,
                   pad_tail=len(payload) > previous_end)
        if narc.build() != data:
            raise FormatError("NARC uses a layout this tool cannot reproduce byte-for-byte")
        return narc

    # -- building ------------------------------------------------------------
    def build(self) -> bytes:
        payload = bytearray()
        fat = bytearray()
        for member in self.members:
            while len(payload) % self.alignment:
                payload.append(self.pad_byte)
            start = len(payload)
            payload += member
            fat += struct.pack("<II", start, len(payload))
        if self.pad_tail:
            while len(payload) % 4:
                payload.append(self.pad_byte)
        btaf = b"BTAF" + struct.pack("<IHH", 12 + len(fat), len(self.members), 0) + fat
        fnt = self.fnt
        btnf = b"BTNF" + struct.pack("<I", 8 + len(fnt)) + fnt
        gmif = b"GMIF" + struct.pack("<I", 8 + len(payload)) + bytes(payload)
        body = btaf + btnf + gmif
        return b"NARC" + struct.pack("<HHIHH", 0xFFFE, self.version, 0x10 + len(body), 0x10, 3) + body


# -- structural reading (any valid FAT layout) ----------------------------------------------------
@dataclass(frozen=True)
class NarcLayout:
    """Validated structure of a NARC whose member data need not be stored in index order.

    CTRMap's writer (pinned W2U jar, ``DirectNARC.insertEntries``) writes member data in a stable sort by
    each entry's *previous* start offset; entries appended past the base archive's count start as
    (0, 0), so their data lands right after member 0 and before member 1. FAT ranges are offsets into the
    GMIF payload. Valid: zero-length members (start == end, anywhere inside the payload), equal adjacent
    boundaries, and alignment gaps shorter than ``alignment`` filled with one pad byte. Invalid: any two
    non-empty ranges sharing a byte, out-of-bounds ranges, other gap contents."""
    count: int
    ranges: tuple[tuple[int, int], ...]
    payload_offset: int
    payload_size: int
    fnt: bytes
    version: int
    file_size: int

    def member(self, data: bytes, index: int) -> bytes:
        start, end = self.ranges[index]
        return bytes(data[self.payload_offset + start:self.payload_offset + end])

    def describe(self) -> dict:
        """Safe diagnostics: structure only, never member contents."""
        return {"file_size": self.file_size, "members": self.count, "payload_size": self.payload_size,
                "empty_members": sum(1 for s, e in self.ranges if s == e),
                "in_index_order": all(a[1] <= b[0] for a, b in zip(self.ranges, self.ranges[1:]) if a[0] != a[1] and b[0] != b[1])}


def read_layout(data: bytes, pad_bytes: frozenset[int] = frozenset({0xFF}), alignment: int = 4) -> NarcLayout:
    if len(data) < 0x10 or data[:4] != b"NARC":
        raise FormatError("not a NARC (missing magic)")
    bom, version, file_size, header_size, sections = struct.unpack_from("<HHIHH", data, 4)
    if bom != 0xFFFE:
        raise FormatError(f"NARC byte-order mark 0x{bom:04x} is not little-endian 0xFFFE")
    if header_size != 0x10 or sections != 3:
        raise FormatError(f"NARC header size 0x{header_size:x} / section count {sections} is not 0x10 / 3")
    if file_size != len(data):
        raise FormatError(f"NARC declares {file_size} bytes but is {len(data)}")
    pos, tags = 0x10, {}
    for expected in (b"BTAF", b"BTNF", b"GMIF"):
        if pos + 8 > len(data):
            raise FormatError(f"NARC section {expected!r} is truncated")
        tag, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        if tag != expected:
            raise FormatError(f"expected NARC section {expected!r} at 0x{pos:x}, found {tag!r}")
        if size < 8 or size % 4 and expected != b"GMIF" or pos + size > len(data):
            raise FormatError(f"NARC section {expected!r} at 0x{pos:x} has an invalid size 0x{size:x}")
        tags[expected] = (pos, size)
        pos += size
    if pos != len(data):
        raise FormatError(f"NARC has {len(data) - pos} bytes after GMIF")
    fat_pos, fat_size = tags[b"BTAF"]
    if fat_size < 12:
        raise FormatError("NARC FAT section is shorter than its header")
    count = struct.unpack_from("<H", data, fat_pos + 8)[0]
    if 12 + count * 8 != fat_size:
        raise FormatError(f"NARC FAT declares {count} entries but its section holds {(fat_size - 12) // 8}")
    gmif_pos, gmif_size = tags[b"GMIF"]
    payload_offset, payload_size = gmif_pos + 8, gmif_size - 8
    ranges = tuple(struct.unpack_from("<II", data, fat_pos + 12 + i * 8) for i in range(count))
    for i, (start, end) in enumerate(ranges):
        if start > end or end > payload_size:
            raise FormatError(f"NARC member {i} range 0x{start:x}..0x{end:x} is outside the 0x{payload_size:x}-byte payload")
    occupied = sorted((s, e, i) for i, (s, e) in enumerate(ranges) if s != e)
    cursor = 0
    for start, end, i in occupied:
        if start < cursor:
            other = next(j for s, e, j in occupied if s < cursor and e > start and j != i)
            raise FormatError(f"NARC members {other} and {i} overlap (0x{start:x} < 0x{cursor:x})")
        _check_gap(data, payload_offset, cursor, start, pad_bytes, alignment, f"before member {i}")
        cursor = end
    _check_gap(data, payload_offset, cursor, payload_size, pad_bytes, alignment, "after the last member")
    btnf_pos, btnf_size = tags[b"BTNF"]
    return NarcLayout(count, ranges, payload_offset, payload_size, bytes(data[btnf_pos + 8:btnf_pos + btnf_size]), version, file_size)


def _check_gap(data: bytes, base: int, start: int, end: int, pad_bytes, alignment: int, where: str) -> None:
    """A gap is valid only as alignment padding: shorter than `alignment`, ending on an aligned absolute
    offset (CTRMap pads until the stream position % 4 == 0), and made of one permitted pad byte."""
    gap = data[base + start:base + end]
    if not gap:
        return
    if len(gap) >= alignment or (base + end) % alignment:
        raise FormatError(f"NARC has a {len(gap)}-byte gap {where} at payload 0x{start:x} that is not alignment padding")
    if len(set(gap)) != 1 or gap[0] not in pad_bytes:
        raise FormatError(f"NARC gap {where} at payload 0x{start:x} is not made of a padding byte")


def read_members(data: bytes, pad_bytes: frozenset[int] = frozenset({0x00, 0xFF})) -> list[bytes]:
    """Members of any structurally valid NARC (index order, whatever the storage order)."""
    layout = read_layout(data, pad_bytes)
    return [layout.member(data, i) for i in range(layout.count)]


def replace_members_same_size(data: bytes, replacements: dict[int, bytes]) -> bytes:
    """Rewrite members in place inside their existing FAT ranges, preserving every other byte."""
    layout = read_layout(data, frozenset({0x00, 0xFF}))
    out = bytearray(data)
    for index, member in replacements.items():
        start, end = layout.ranges[index]
        if len(member) != end - start:
            raise FormatError(f"NARC member {index} would change size ({end - start} -> {len(member)})")
        out[layout.payload_offset + start:layout.payload_offset + end] = member
    return bytes(out)
