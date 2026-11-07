"""ARM9 module-parameter discovery and compressed-static-end validation and repair.

The NITRO SDK places a module-parameter block in the ARM9 static image:

    +0x00 autoload_list      +0x04 autoload_list_end   +0x08 autoload_start
    +0x0C static_bss_start   +0x10 static_bss_end      +0x14 compressed_static_end
    +0x18 sdk_version        +0x1C 0xDEC00621          +0x20 0x2106C0DE

``compressed_static_end`` is a RAM address. When non-zero, the boot code runs
backward-LZ decompression on the static image ending there. A non-zero value
over an image that is not BLZ-compressed is the confirmed cause of the earlier
0x02004E44 boot abort (RECOVERY_BASELINE.md).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from . import FormatError, blz

MAGIC = struct.pack("<II", 0xDEC00621, 0x2106C0DE)
MAGIC_TO_PARAMS = 0x1C
SEARCH_LIMIT = 0x8000
COMPRESSED_END_FIELD = 0x14


@dataclass(frozen=True)
class ModuleParams:
    offset: int  # ARM9-relative offset of the parameter block
    autoload_list: int
    autoload_list_end: int
    autoload_start: int
    static_bss_start: int
    static_bss_end: int
    compressed_static_end: int
    sdk_version: int

    @property
    def compressed_end_field_offset(self) -> int:
        return self.offset + COMPRESSED_END_FIELD


def find_module_params(arm9: bytes, ram_address: int = 0x02000000) -> ModuleParams:
    """Locate the unique module-parameter block by its SDK markers. Raises FormatError otherwise."""
    hits = []
    start = 0
    while True:
        index = arm9.find(MAGIC, start, min(len(arm9), SEARCH_LIMIT))
        if index < 0:
            break
        hits.append(index)
        start = index + 4
    if len(hits) != 1:
        raise FormatError(f"expected exactly one ARM9 module-parameter marker in the first 0x{SEARCH_LIMIT:x} bytes, found {len(hits)}")
    offset = hits[0] - MAGIC_TO_PARAMS
    if offset < 0:
        raise FormatError("module-parameter marker is too close to the start of ARM9")
    values = struct.unpack_from("<7I", arm9, offset)
    params = ModuleParams(offset, *values)
    if not ram_address <= params.autoload_start <= params.static_bss_end:
        raise FormatError("module parameters are not plausible RAM addresses")
    return params


@dataclass(frozen=True)
class CompressionState:
    params: ModuleParams
    state: str  # "uncompressed", "compressed", or "stale"
    detail: str

    @property
    def bootable(self) -> bool:
        return self.state in ("uncompressed", "compressed")


def classify(arm9: bytes, ram_address: int = 0x02000000) -> CompressionState:
    params = find_module_params(arm9, ram_address)
    end = params.compressed_static_end
    if end == 0:
        return CompressionState(params, "uncompressed", "compressed_static_end is zero")
    relative = end - ram_address
    if not 0 < relative <= len(arm9):
        return CompressionState(params, "stale", f"compressed_static_end 0x{end:08x} points outside the ARM9 image")
    info = blz.footer(arm9, relative)
    if info is None:
        return CompressionState(params, "stale", f"no valid backward-LZ footer ends at 0x{end:08x}")
    try:
        blz.decompress(arm9, relative)
    except FormatError as error:
        return CompressionState(params, "stale", f"backward-LZ data at 0x{end:08x} does not decode: {error}")
    return CompressionState(params, "compressed", f"valid backward-LZ region ends at 0x{end:08x}")


def repaired(arm9: bytes, ram_address: int = 0x02000000) -> tuple[bytes, CompressionState, bool]:
    """Return (arm9, original state, changed). A stale end is cleared; valid states are untouched."""
    state = classify(arm9, ram_address)
    if state.state != "stale":
        return arm9, state, False
    data = bytearray(arm9)
    struct.pack_into("<I", data, state.params.compressed_end_field_offset, 0)
    return bytes(data), state, True


def synthetic_arm9(size: int = 0x6000, compressed_static_end: int = 0, params_offset: int = 0xFB0) -> bytes:
    """A synthetic ARM9 image with a module-parameter block (tests and mock builds only)."""
    data = bytearray((i * 7 + 3) & 0xFF for i in range(size))
    data[params_offset + 0x1C : params_offset + 0x24] = b"\x00" * 8
    struct.pack_into(
        "<7I", data, params_offset,
        0x02090000, 0x02090040, 0x02088000, 0x02088000, 0x02100000, compressed_static_end, 0x0503757C,
    )
    data[params_offset + MAGIC_TO_PARAMS : params_offset + MAGIC_TO_PARAMS + 8] = MAGIC
    return bytes(data)
