"""Nitro backward LZ ("BLZ") code compression: footer validation, decompression, literal encoding.

Layout: the compressed region ends at ``end``. The last 8 bytes are a footer:
``u32 (header_length << 24 | compressed_length)`` and ``u32 extra_size``.
Decoding proceeds backwards from ``end - header_length``; each control byte
gives 8 flags (MSB first). A set flag is a 2-byte reference
``(length - 3) << 12 | (displacement - 3)``; a clear flag is a literal byte.
"""

from __future__ import annotations

import struct

from . import FormatError


def footer(data: bytes, end: int | None = None) -> tuple[int, int, int] | None:
    """Return (header_length, compressed_length, extra_size) when a plausible footer ends at ``end``."""
    end = len(data) if end is None else end
    if end < 8 or end > len(data):
        return None
    top_bottom, extra = struct.unpack_from("<II", data, end - 8)
    header_length = top_bottom >> 24
    compressed_length = top_bottom & 0xFFFFFF
    if not 8 <= header_length <= 11:
        return None
    if compressed_length < header_length or compressed_length > end:
        return None
    if extra > 0x800000:
        return None
    return header_length, compressed_length, extra


def decompress(data: bytes, end: int | None = None) -> bytes:
    """Decompress a BLZ region ending at ``end``. Bytes before the region and after ``end`` are kept."""
    end = len(data) if end is None else end
    info = footer(data, end)
    if info is None:
        raise FormatError("no valid backward-LZ footer")
    header_length, compressed_length, extra = info
    start = end - compressed_length
    source = bytes(data[:end])
    out = bytearray(source) + bytearray(extra)
    in_pos = end - header_length
    out_pos = len(out)
    while in_pos > start:
        in_pos -= 1
        control = source[in_pos]
        for _ in range(8):
            if in_pos <= start:
                break
            if control & 0x80:
                if in_pos - 2 < start:
                    raise FormatError("truncated backward-LZ reference")
                in_pos -= 2
                pair = source[in_pos] | (source[in_pos + 1] << 8)
                length = (pair >> 12) + 3
                displacement = (pair & 0x0FFF) + 3
                for _ in range(length):
                    out_pos -= 1
                    if out_pos < start or out_pos + displacement >= len(out):
                        raise FormatError("backward-LZ reference is out of bounds")
                    out[out_pos] = out[out_pos + displacement]
            else:
                in_pos -= 1
                out_pos -= 1
                if out_pos < start:
                    raise FormatError("backward-LZ literal is out of bounds")
                out[out_pos] = source[in_pos]
            control = (control << 1) & 0xFF
    return bytes(out) + bytes(data[end:])


def compress(payload: bytes) -> bytes:
    """Greedy BLZ encoder (whole payload compressed, no raw prefix). Raises if it would not shrink.

    Used for synthetic fixtures and round-trip tests; mirrors :func:`decompress` exactly.
    """
    n = len(payload)
    decode_order = bytearray()
    group: list[bytes] = []
    control = 0
    p = n
    while p > 0:
        best_len, best_disp = 0, 0
        for disp in range(3, min(0x1002, n - p + 1) + 1):
            length = 0
            while length < 18 and p - 1 - length >= 0 and p - 1 - length + disp < n \
                    and payload[p - 1 - length] == payload[p - 1 - length + disp]:
                length += 1
            if length > best_len:
                best_len, best_disp = length, disp
                if length == 18:
                    break
        bit = 0x80 >> len(group)
        if best_len >= 3:
            pair = ((best_len - 3) << 12) | (best_disp - 3)
            group.append(bytes([pair >> 8, pair & 0xFF]))
            control |= bit
            p -= best_len
        else:
            group.append(bytes([payload[p - 1]]))
            p -= 1
        if len(group) == 8 or p == 0:
            decode_order += bytes([control]) + b"".join(group)
            group, control = [], 0
    stream = bytes(reversed(decode_order))
    padding = (-len(stream)) % 4
    header_length = 8 + padding
    compressed_length = len(stream) + header_length
    extra = n - compressed_length
    if extra < 0:
        raise FormatError("payload does not compress")
    return stream + b"\xff" * padding + struct.pack("<II", (header_length << 24) | compressed_length, extra)
