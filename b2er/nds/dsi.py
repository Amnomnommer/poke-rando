"""DSi-enhanced ("TWL-extended") cartridge components: strict header parsing, bounded extraction, checks.

Facts used (DSi extended header):
  0x012 unit code (CTRMap treats exactly 0x02 with a non-zero 0x210 total-used size as TWL-extended)
  0x208 banner size (0 means 0x840); 0x210 total used ROM size
  0x092 u16 TWL region start (units of 0x80000)
  0x1C0 / 0x1CC ARM9i ROM offset / size; 0x1D0 / 0x1DC ARM7i ROM offset / size
  0x220 / 0x224 modcrypt area 1 offset / size; 0x228 / 0x22C modcrypt area 2 offset / size

CTRMap's ROM builder (pinned W2U jar) reads, for a TWL-extended header, exactly
``arm9i.bin``, ``arm7i.bin`` and ``twlblowfishtable.bin`` in addition to the NTR
components. Its extractor defines them as: the 0x3000 bytes immediately before
ARM9i (blowfish table), the raw ARM7i region, and the raw ARM9i region whose
first modcrypt-area-1 bytes are stored decrypted (the builder re-encrypts them).
This module never handles keys: the area-1 transform is performed by the user's
pinned CTRMap (b2er/assembly/ctrmap_dsi.py) and cross-checked against these
raw extractions.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from . import FormatError

BLOWFISH_TABLE_SIZE = 0x3000
NTR_COMPONENTS = ("header.bin", "arm9.bin", "arm7.bin", "banner.bin")
OVERLAY_TABLES = (("arm9ovltable.bin", "y9.bin"), ("arm7ovltable.bin", "y7.bin"))
DSI_COMPONENTS = ("arm9i.bin", "arm7i.bin", "twlblowfishtable.bin")
MIN_HEADER = 0x200
EXTENDED_HEADER = 0x1000


@dataclass(frozen=True)
class DsiLayout:
    unit_code: int
    twl_region_start: int
    arm9i: tuple[int, int]
    arm7i: tuple[int, int]
    modcrypt1: tuple[int, int]
    modcrypt2: tuple[int, int]

    @property
    def blowfish(self) -> tuple[int, int]:
        return self.arm9i[0] - BLOWFISH_TABLE_SIZE, BLOWFISH_TABLE_SIZE

    def regions(self) -> dict[str, tuple[int, int]]:
        return {"arm9i.bin": self.arm9i, "arm7i.bin": self.arm7i, "twlblowfishtable.bin": self.blowfish}


def is_twl_extended(data: bytes) -> bool:
    """CTRMap's rule (SRLHeader.isTwlExtended): unit code exactly 0x02 and a non-zero total-used-ROM size at 0x210."""
    if len(data) < MIN_HEADER:
        raise FormatError("image is smaller than a DS header")
    return data[0x12] == 0x02 and len(data) >= 0x214 and struct.unpack_from("<I", data, 0x210)[0] > 0


def banner_size(header: bytes) -> int:
    """CTRMap's rule (SRLHeader.getIconSize): the u32 at 0x208 when non-zero, otherwise 0x840."""
    declared = struct.unpack_from("<I", header, 0x208)[0] if len(header) >= 0x20C else 0
    return declared or 0x840


def parse(data: bytes) -> DsiLayout | None:
    """Return the validated DSi layout, None for an NTR-only image, or raise FormatError."""
    if not is_twl_extended(data):
        return None
    if len(data) < EXTENDED_HEADER:
        raise FormatError("TWL-extended image is smaller than the 0x1000-byte extended header")
    u32 = lambda off: struct.unpack_from("<I", data, off)[0]  # noqa: E731
    layout = DsiLayout(
        data[0x12], struct.unpack_from("<H", data, 0x92)[0] * 0x80000,
        (u32(0x1C0), u32(0x1CC)), (u32(0x1D0), u32(0x1DC)), (u32(0x220), u32(0x224)), (u32(0x228), u32(0x22C)),
    )
    size = len(data)
    for name, (offset, length) in layout.regions().items():
        if length == 0:
            raise FormatError(f"TWL-extended header declares an empty {name} region")
        if offset < EXTENDED_HEADER or offset + length > size:
            raise FormatError(f"{name} region 0x{offset:x}+0x{length:x} is outside the 0x{size:x}-byte image")
    if layout.arm9i[0] < layout.twl_region_start + BLOWFISH_TABLE_SIZE:
        raise FormatError("ARM9i does not leave room for the blowfish table after the TWL region start")
    area1_offset, area1_size = layout.modcrypt1
    if area1_size:
        if area1_offset != layout.arm9i[0] or area1_size > layout.arm9i[1]:
            raise FormatError("modcrypt area 1 must start at ARM9i and lie within it (the CTRMap layout assumes this)")
    area2_offset, area2_size = layout.modcrypt2
    if area2_size and (area2_offset < EXTENDED_HEADER or area2_offset + area2_size > size):
        raise FormatError("modcrypt area 2 is outside the image")
    return layout


def extract_raw(data: bytes) -> dict[str, bytes]:
    """Exact bytes of every DSi component region (empty dict for NTR-only images)."""
    layout = parse(data)
    if layout is None:
        return {}
    return {name: bytes(data[offset:offset + length]) for name, (offset, length) in layout.regions().items()}


def required_components(header: bytes) -> list[str]:
    """CTRMap's required ROM components: NTR files always, DSi files only for TWL-extended headers."""
    return list(NTR_COMPONENTS) + (list(DSI_COMPONENTS) if is_twl_extended(header) else [])


def declared_sizes(header: bytes) -> dict[str, int]:
    if not is_twl_extended(header) or len(header) < 0x1E0:
        return {}
    return {"arm9i.bin": struct.unpack_from("<I", header, 0x1CC)[0], "arm7i.bin": struct.unpack_from("<I", header, 0x1DC)[0],
            "twlblowfishtable.bin": BLOWFISH_TABLE_SIZE}


def missing_components(roots: list[Path]) -> list[str]:
    """Every required CTRMap ROM component missing from the merged base/overlay roots."""
    def find(name: str) -> Path | None:
        for root in roots:
            if (root / name).is_file():
                return root / name
        return None

    header_path = find("header.bin")
    if header_path is None:
        return ["header.bin"]
    header = header_path.read_bytes()
    missing = [n for n in required_components(header) if find(n) is None]
    for primary, fallback in OVERLAY_TABLES:
        if find(primary) is None and find(fallback) is None:
            missing.append(f"{primary} or {fallback}")
    for directory in ("data", "overlay"):
        if not any((root / directory).is_dir() for root in roots):
            missing.append(f"{directory}/")
    for name, length in declared_sizes(header).items():
        path = find(name)
        if path is not None and path.stat().st_size != length:
            missing.append(f"{name} (size {path.stat().st_size}, header declares {length})")
    return missing


def check_assembled(rom: bytes, base_header: bytes) -> list[str]:
    """Structural checks of ROMBuilder output before any footer/finalization step may touch it."""
    problems = []
    if len(rom) < MIN_HEADER:
        return [f"assembled ROM is {len(rom)} bytes; a DS header needs at least {MIN_HEADER}"]
    if rom[0x0C:0x10] != base_header[0x0C:0x10]:
        problems.append(f"game code {rom[0x0C:0x10]!r} differs from the base {base_header[0x0C:0x10]!r}")
    if is_twl_extended(rom) != is_twl_extended(base_header):
        problems.append("DSi-enhanced status differs from the base ROM")
    try:
        from .rom import Rom
        Rom(rom).paths()
        if is_twl_extended(rom):
            parse(rom)  # validates ARM9i/ARM7i/blowfish/modcrypt bounds in the assembled image
    except FormatError as error:
        problems.append(f"assembled ROM is structurally invalid: {error}")
    return problems


def check_assembled_file(path: Path, base_header: Path) -> list[str]:
    path = Path(path)
    if not path.exists():
        return [f"ROMBuilder produced no output file ({path.name})"]
    if path.is_symlink() or not path.is_file():
        return [f"ROMBuilder output {path.name} is not a regular file"]
    if path.stat().st_size == 0:
        return [f"ROMBuilder produced an empty {path.name}"]
    return check_assembled(path.read_bytes(), Path(base_header).read_bytes())
