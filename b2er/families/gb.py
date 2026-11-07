"""Game Boy / Game Boy Color cartridge container (Generations I-II): header facts, checksums, refresh."""

from __future__ import annotations

from dataclasses import dataclass

from ..nds import FormatError

ROM_SIZE_CODES = {code: 0x8000 << code for code in range(0, 9)}


@dataclass(frozen=True)
class GbHeader:
    title: str
    cgb: bool
    sgb: bool
    cartridge_type: int
    rom_size: int
    ram_size_code: int
    destination: str  # "Japan" or "Overseas"
    version: int
    header_checksum_ok: bool
    global_checksum_ok: bool


def header_checksum(data: bytes) -> int:
    value = 0
    for b in data[0x134:0x14D]:
        value = (value - b - 1) & 0xFF
    return value


def global_checksum(data: bytes) -> int:
    return (sum(data) - data[0x14E] - data[0x14F]) & 0xFFFF


def parse(data: bytes) -> GbHeader:
    if len(data) < 0x8000:
        raise FormatError("Game Boy image is smaller than 32 KiB")
    code = data[0x148]
    if code not in ROM_SIZE_CODES:
        raise FormatError(f"unknown Game Boy ROM size code 0x{code:02x}")
    cgb = data[0x143] in (0x80, 0xC0)
    title = data[0x134 : 0x13F if cgb else 0x144].split(b"\x00", 1)[0].decode("ascii", errors="replace")
    return GbHeader(title, cgb, data[0x146] == 0x03, data[0x147], ROM_SIZE_CODES[code], data[0x149],
                    "Japan" if data[0x14A] == 0 else "Overseas", data[0x14C],
                    header_checksum(data) == data[0x14D], global_checksum(data) == int.from_bytes(data[0x14E:0x150], "big"))


def validate(data: bytes) -> list[str]:
    try:
        h = parse(data)
    except FormatError as error:
        return [str(error)]
    problems = []
    if not h.header_checksum_ok:
        problems.append("header checksum mismatch")
    if h.rom_size != len(data):
        problems.append(f"header declares {h.rom_size} bytes but the image is {len(data)}")
    if not h.global_checksum_ok:
        problems.append("global checksum mismatch (image modified or bad dump)")
    return problems


def refresh(data: bytearray) -> None:
    data[0x14D] = header_checksum(data)
    data[0x14E:0x150] = global_checksum(data).to_bytes(2, "big")
