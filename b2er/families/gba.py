"""Game Boy Advance cartridge container (Generation III): header facts, checksum, and the BIOS LZ77 codec."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ..nds import FormatError


@dataclass(frozen=True)
class GbaHeader:
    title: str
    game_code: str
    maker: str
    version: int
    checksum_ok: bool


def complement(data: bytes) -> int:
    return (-sum(data[0xA0:0xBD]) - 0x19) & 0xFF


def parse(data: bytes) -> GbaHeader:
    if len(data) < 0xC0 or data[0xB2] != 0x96:
        raise FormatError("not a GBA cartridge header (fixed byte 0x96 missing)")
    return GbaHeader(data[0xA0:0xAC].split(b"\x00", 1)[0].decode("ascii", "replace"), data[0xAC:0xB0].decode("ascii", "replace"),
                     data[0xB0:0xB2].decode("ascii", "replace"), data[0xBC], complement(data) == data[0xBD])


def validate(data: bytes) -> list[str]:
    try:
        h = parse(data)
    except FormatError as error:
        return [str(error)]
    problems = [] if h.checksum_ok else ["header complement checksum mismatch"]
    if len(data) & (len(data) - 1) or not 0x40000 <= len(data) <= 0x2000000:
        problems.append(f"image size {len(data)} is not a power-of-two cartridge size up to 32 MiB")
    return problems


def refresh(data: bytearray) -> None:
    data[0xBD] = complement(data)


def lz77_decompress(data: bytes) -> bytes:
    """BIOS LZ77 (type 0x10): header u32 = 0x10 | size << 8, then flag-grouped literals and 2-byte references."""
    if len(data) < 4 or data[0] != 0x10:
        raise FormatError("not LZ77 type 0x10 data")
    size = int.from_bytes(data[1:4], "little")
    out = bytearray()
    pos = 4
    while len(out) < size:
        if pos >= len(data):
            raise FormatError("truncated LZ77 stream")
        flags = data[pos]
        pos += 1
        for bit in range(8):
            if len(out) >= size:
                break
            if flags & (0x80 >> bit):
                if pos + 2 > len(data):
                    raise FormatError("truncated LZ77 reference")
                b1, b2 = data[pos], data[pos + 1]
                pos += 2
                length, disp = (b1 >> 4) + 3, (((b1 & 0xF) << 8) | b2) + 1
                if disp > len(out):
                    raise FormatError("LZ77 reference before the start of output")
                for _ in range(length):
                    out.append(out[-disp])
            else:
                if pos >= len(data):
                    raise FormatError("truncated LZ77 literal")
                out.append(data[pos])
                pos += 1
    return bytes(out[:size])


def lz77_compress(data: bytes) -> bytes:
    if len(data) >= 1 << 24:
        raise FormatError("LZ77 payload too large")
    out = bytearray(struct.pack("<I", 0x10 | (len(data) << 8)))
    pos = 0
    while pos < len(data):
        flag_index = len(out)
        out.append(0)
        for bit in range(8):
            if pos >= len(data):
                break
            best_len, best_disp = 0, 0
            for disp in range(1, min(pos, 0x1000) + 1):
                length = 0
                while length < 18 and pos + length < len(data) and data[pos + length] == data[pos + length - disp]:
                    length += 1
                if length > best_len:
                    best_len, best_disp = length, disp
            if best_len >= 3:
                out[flag_index] |= 0x80 >> bit
                out += bytes([((best_len - 3) << 4) | ((best_disp - 1) >> 8), (best_disp - 1) & 0xFF])
                pos += best_len
            else:
                out.append(data[pos])
                pos += 1
    while len(out) % 4:
        out.append(0)
    return bytes(out)
