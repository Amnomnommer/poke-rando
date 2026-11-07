"""Nintendo 3DS containers (Generations VI-VII): NCSD/NCCH/CIA inspection and the GARC archive codec.

Retail NCCH contents are encrypted; this project never handles keys. Building
requires a user-decrypted image, which these readers detect and report.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ..nds import FormatError

MEDIA_UNIT = 0x200


@dataclass(frozen=True)
class Ncch:
    offset: int
    product_code: str
    program_id: int
    version: int
    encrypted: bool
    exefs: tuple[int, int]
    romfs: tuple[int, int]


def parse_ncch(data: bytes, offset: int = 0) -> Ncch:
    if data[offset + 0x100 : offset + 0x104] != b"NCCH":
        raise FormatError("NCCH magic missing")
    flags = data[offset + 0x188 : offset + 0x190]
    product = data[offset + 0x150 : offset + 0x160].split(b"\x00", 1)[0].decode("ascii", "replace")
    exefs = struct.unpack_from("<II", data, offset + 0x1A0)
    romfs = struct.unpack_from("<II", data, offset + 0x1B0)
    return Ncch(offset, product, struct.unpack_from("<Q", data, offset + 0x118)[0], struct.unpack_from("<H", data, offset + 0x112)[0],
                not bool(flags[7] & 0x04), (exefs[0] * MEDIA_UNIT, exefs[1] * MEDIA_UNIT), (romfs[0] * MEDIA_UNIT, romfs[1] * MEDIA_UNIT))


def parse_ncsd(data: bytes) -> list[Ncch]:
    if data[0x100:0x104] != b"NCSD":
        raise FormatError("NCSD magic missing")
    partitions = []
    for i in range(8):
        off, size = struct.unpack_from("<II", data, 0x120 + i * 8)
        if size:
            start = off * MEDIA_UNIT
            if start + 0x200 > len(data):
                raise FormatError(f"NCSD partition {i} is outside the image")
            partitions.append(parse_ncch(data, start))
    if not partitions:
        raise FormatError("NCSD has no partitions")
    return partitions


def cia_content_offset(data: bytes) -> int | None:
    if len(data) < 0x20 or struct.unpack_from("<I", data, 0)[0] != 0x2020:
        return None
    cert, ticket, tmd = struct.unpack_from("<III", data, 0x08)
    align = lambda n: (n + 63) // 64 * 64  # noqa: E731
    return align(0x2020) + align(cert) + align(ticket) + align(tmd)


def validate(data: bytes) -> list[str]:
    try:
        parts = parse_ncsd(data)
    except FormatError as error:
        return [str(error)]
    return ["NCCH partition 0 is encrypted; a user-decrypted image is required to build"] if parts[0].encrypted else []


# --- GARC -------------------------------------------------------------------------------

@dataclass
class Garc:
    """Strict GARC: files are lists of subfile byte strings (bit-vector slots)."""

    files: list[dict[int, bytes]]
    version: int = 0x0400
    alignment: int = 4

    @classmethod
    def parse(cls, data: bytes) -> "Garc":
        if data[:4] != b"CRAG":
            raise FormatError("GARC magic missing")
        header_size, bom, version, chunks, data_offset, file_size = struct.unpack_from("<IHHIII", data, 4)
        if bom != 0xFEFF or chunks != 4 or version not in (0x0400, 0x0600) or file_size != len(data):
            raise FormatError("GARC header is not the expected little-endian v4/v6 four-chunk form")
        pos = header_size
        if data[pos:pos + 4] != b"OTAF":
            raise FormatError("GARC FATO section missing")
        fato_size, count = struct.unpack_from("<IH", data, pos + 4)
        offsets = struct.unpack_from(f"<{count}I", data, pos + 12)
        pos += fato_size
        if data[pos:pos + 4] != b"BTAF":
            raise FormatError("GARC FATB section missing")
        fatb_size, fatb_count = struct.unpack_from("<II", data, pos + 4)
        if fatb_count != count:
            raise FormatError("GARC FATO/FATB counts differ")
        entries_base = pos + 12
        pos += fatb_size
        if data[pos:pos + 4] != b"BMIF":
            raise FormatError("GARC FIMB section missing")
        if pos + 12 != data_offset:
            raise FormatError("GARC data offset does not follow FIMB")
        files = []
        for index, rel in enumerate(offsets):
            cursor = entries_base + rel
            vector = struct.unpack_from("<I", data, cursor)[0]
            cursor += 4
            subs = {}
            for bit in range(32):
                if vector & (1 << bit):
                    start, end, length = struct.unpack_from("<III", data, cursor)
                    cursor += 12
                    if not start <= start + length <= end or data_offset + end > len(data):
                        raise FormatError(f"GARC file {index} subfile {bit} range is invalid")
                    subs[bit] = bytes(data[data_offset + start : data_offset + start + length])
            files.append(subs)
        garc = cls(files, version)
        if garc.build() != data:
            garc.alignment = 1 if garc.with_alignment(1).build() == data else 4
            if garc.build() != data:
                raise FormatError("GARC layout cannot be reproduced byte-for-byte")
        return garc

    def with_alignment(self, alignment: int) -> "Garc":
        return Garc(self.files, self.version, alignment)

    def build(self) -> bytes:
        payload = bytearray()
        fatb_entries = bytearray()
        offsets = []
        largest = 0
        for subs in self.files:
            offsets.append(len(fatb_entries))
            vector = 0
            records = bytearray()
            for bit in sorted(subs):
                vector |= 1 << bit
                start = len(payload)
                payload += subs[bit]
                while len(payload) % self.alignment:
                    payload.append(0xFF)
                largest = max(largest, len(subs[bit]))
                records += struct.pack("<III", start, len(payload), len(subs[bit]))
            fatb_entries += struct.pack("<I", vector) + records
        header_size = 0x1C if self.version == 0x0400 else 0x24
        fato = b"OTAF" + struct.pack("<IHH", 12 + 4 * len(offsets), len(offsets), 0xFFFF) + struct.pack(f"<{len(offsets)}I", *offsets)
        fatb = b"BTAF" + struct.pack("<II", 12 + len(fatb_entries), len(self.files)) + bytes(fatb_entries)
        fimb = b"BMIF" + struct.pack("<II", 12, len(payload))
        data_offset = header_size + len(fato) + len(fatb) + len(fimb)
        total = data_offset + len(payload)
        if self.version == 0x0400:
            extra = struct.pack("<I", largest)
        else:
            extra = struct.pack("<III", largest, largest, self.alignment)
        header = b"CRAG" + struct.pack("<IHHIII", header_size, 0xFEFF, self.version, 4, data_offset, total) + extra
        return header + fato + fatb + fimb + bytes(payload)
