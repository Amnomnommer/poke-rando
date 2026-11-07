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
