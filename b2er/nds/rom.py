"""Nintendo DS ROM header, FNT/FAT, and overlay-table access with strict bounds checks."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from . import FormatError

HEADER_SIZE = 0x200
EXTENDED_HEADER_SIZE = 0x1000
DIR_ID_BASE = 0xF000

OFF = {
    "game_code": 0x0C,
    "unit_code": 0x12,
    "arm9_offset": 0x20,
    "arm9_entry": 0x24,
    "arm9_ram": 0x28,
    "arm9_size": 0x2C,
    "arm7_offset": 0x30,
    "arm7_size": 0x3C,
    "fnt_offset": 0x40,
    "fnt_size": 0x44,
    "fat_offset": 0x48,
    "fat_size": 0x4C,
    "y9_offset": 0x50,
    "y9_size": 0x54,
    "y7_offset": 0x58,
    "y7_size": 0x5C,
    "banner_offset": 0x68,
    "secure_crc": 0x6C,
    "used_rom_size": 0x80,
    "header_size": 0x84,
    "logo_crc": 0x15C,
    "header_crc": 0x15E,
}
SIGNATURE_OFFSET = 0xF80
SIGNATURE_SIZE = 0x80


def crc16(data: bytes | bytearray | memoryview) -> int:
    crc = 0xFFFF
    for value in bytes(data):
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def u16(data, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _check(label: str, offset: int, length: int, size: int) -> None:
    if offset < 0 or length < 0 or offset + length > size:
        raise FormatError(f"{label} 0x{offset:x}+0x{length:x} is outside the 0x{size:x}-byte image")


@dataclass(frozen=True)
class Overlay:
    index: int
    overlay_id: int
    ram_address: int
    ram_size: int
    bss_size: int
    file_id: int
    flags: int  # raw u32: low 24 bits compressed size, bit 24 compressed

    @property
    def compressed(self) -> bool:
        return bool(self.flags & 0x01000000)


class Rom:
    """A mutable in-memory DS ROM image. All accessors bounds-check against the image."""

    def __init__(self, data: bytes | bytearray) -> None:
        self.data = bytearray(data)
        if len(self.data) < HEADER_SIZE:
            raise FormatError("image is smaller than a DS header")
        self._paths: dict[str, int] | None = None
        self.validate_layout()

    # --- header -----------------------------------------------------------
    def field(self, name: str) -> int:
        offset = OFF[name]
        return u16(self.data, offset) if name in ("secure_crc", "logo_crc", "header_crc") else u32(self.data, offset)

    @property
    def game_code(self) -> str:
        return bytes(self.data[0x0C:0x10]).decode("ascii", errors="replace")

    def validate_layout(self) -> None:
        size = len(self.data)
        for label, off, length in (
            ("ARM9", self.field("arm9_offset"), self.field("arm9_size")),
            ("ARM7", self.field("arm7_offset"), self.field("arm7_size")),
            ("FNT", self.field("fnt_offset"), self.field("fnt_size")),
            ("FAT", self.field("fat_offset"), self.field("fat_size")),
            ("ARM9 overlay table", self.field("y9_offset"), self.field("y9_size")),
        ):
            _check(label, off, length, size)
        if self.field("fat_size") % 8:
            raise FormatError("FAT size is not a multiple of 8")
        if self.field("y9_size") % 32:
            raise FormatError("ARM9 overlay table size is not a multiple of 32")
        for file_id, (start, end) in enumerate(self.fat()):
            if end < start:
                raise FormatError(f"FAT entry {file_id} is reversed")
            _check(f"file {file_id}", start, end - start, size)

    # --- FAT / FNT ---------------------------------------------------------
    def fat(self) -> list[tuple[int, int]]:
        off, size = self.field("fat_offset"), self.field("fat_size")
        return [struct.unpack_from("<II", self.data, off + i) for i in range(0, size, 8)]

    def file_range(self, file_id: int) -> tuple[int, int]:
        fat = self.fat()
        if not 0 <= file_id < len(fat):
            raise FormatError(f"file id {file_id} is outside the FAT ({len(fat)} entries)")
        return fat[file_id]

    def file_bytes(self, file_id: int) -> bytes:
        start, end = self.file_range(file_id)
        return bytes(self.data[start:end])

    def paths(self) -> dict[str, int]:
        """Map every NitroFS path ("a/0/1/6") to its file id."""
        if self._paths is not None:
            return self._paths
        fnt_off, fnt_size = self.field("fnt_offset"), self.field("fnt_size")
        fnt = bytes(self.data[fnt_off : fnt_off + fnt_size])
        if len(fnt) < 8:
            raise FormatError("FNT is too small")
        dir_count = u16(fnt, 6)
        if dir_count == 0 or dir_count * 8 > len(fnt):
            raise FormatError("FNT directory count is invalid")
        result: dict[str, int] = {}
        visited: set[int] = set()

        def walk(dir_index: int, prefix: str) -> None:
            if dir_index in visited:
                raise FormatError("FNT directory cycle")
            visited.add(dir_index)
            sub_off, first_id = u32(fnt, dir_index * 8), u16(fnt, dir_index * 8 + 4)
            pos, file_id = sub_off, first_id
            while True:
                if pos >= len(fnt):
                    raise FormatError("FNT sub-table runs past the FNT")
                length = fnt[pos]
                pos += 1
                if length == 0:
                    break
                name_len = length & 0x7F
                if pos + name_len > len(fnt):
                    raise FormatError("FNT name runs past the FNT")
                name = fnt[pos : pos + name_len].decode("latin-1")
                pos += name_len
                if length & 0x80:
                    child = u16(fnt, pos) - DIR_ID_BASE
                    pos += 2
                    if not 0 <= child < dir_count:
                        raise FormatError("FNT child directory id is invalid")
                    walk(child, f"{prefix}{name}/")
                else:
                    result[f"{prefix}{name}"] = file_id
                    file_id += 1

        walk(0, "")
        self._paths = result
        return result

    def file_id(self, path: str) -> int:
        try:
            return self.paths()[path.strip("/")]
        except KeyError:
            raise FormatError(f"no NitroFS file named {path!r}") from None

    def read_path(self, path: str) -> bytes:
        return self.file_bytes(self.file_id(path))

    def replace_file_in_place(self, file_id: int, payload: bytes) -> None:
        """Replace a file without moving anything; the payload must fit the existing allocation."""
        start, end = self.file_range(file_id)
        fat = self.fat()
        following = sorted(s for s, _ in fat if s >= end) + [len(self.data)]
        limit = following[0]
        if start + len(payload) > limit:
            raise FormatError(f"file {file_id} payload of {len(payload)} bytes does not fit its 0x{limit - start:x}-byte slot")
        self.data[start : start + len(payload)] = payload
        if len(payload) < end - start:
            self.data[start + len(payload) : end] = b"\xff" * (end - start - len(payload))
        struct.pack_into("<I", self.data, self.field("fat_offset") + file_id * 8 + 4, start + len(payload))

    # --- overlays and ARM9 ------------------------------------------------
    def overlays(self) -> list[Overlay]:
        off, size = self.field("y9_offset"), self.field("y9_size")
        result = []
        for index, pos in enumerate(range(off, off + size, 32)):
            ov_id, ram, ram_size, bss, _sinit_s, _sinit_e, file_id, flags = struct.unpack_from("<8I", self.data, pos)
            result.append(Overlay(index, ov_id, ram, ram_size, bss, file_id, flags))
        return result

    def arm9(self) -> bytes:
        off, size = self.field("arm9_offset"), self.field("arm9_size")
        return bytes(self.data[off : off + size])

    def write_arm9_word(self, arm9_relative: int, value: int) -> None:
        if not 0 <= arm9_relative <= self.field("arm9_size") - 4:
            raise FormatError("ARM9 word is outside the ARM9 binary")
        struct.pack_into("<I", self.data, self.field("arm9_offset") + arm9_relative, value)

    # --- integrity --------------------------------------------------------
    def expected_crcs(self) -> dict[str, int]:
        arm9 = self.field("arm9_offset")
        crcs = {
            "logo_crc": crc16(self.data[0xC0:0x15C]),
        }
        if arm9 < 0x8000 and len(self.data) >= 0x8000:
            crcs["secure_crc"] = crc16(self.data[arm9:0x8000])
        header = bytearray(self.data[:0x15E])
        for name in ("secure_crc", "logo_crc"):
            if name in crcs:
                struct.pack_into("<H", header, OFF[name], crcs[name])
        crcs["header_crc"] = crc16(header)
        return crcs

    def has_ctrmap_signature(self) -> bool:
        if len(self.data) < EXTENDED_HEADER_SIZE:
            return False
        sig = self.data[SIGNATURE_OFFSET : SIGNATURE_OFFSET + SIGNATURE_SIZE]
        return sig[:2] == b"\x00\x01" and sig[2:107] == b"\xff" * 105 and sig[107] == 0

    def ctrmap_signature(self) -> bytes:
        return b"\x00\x01" + b"\xff" * 105 + b"\x00" + hashlib.sha1(bytes(self.data[:0xE00])).digest()

    def integrity_problems(self) -> list[str]:
        problems = []
        for name, value in self.expected_crcs().items():
            if self.field(name) != value:
                problems.append(f"{name} is stale")
        if self.has_ctrmap_signature() and bytes(self.data[SIGNATURE_OFFSET : SIGNATURE_OFFSET + SIGNATURE_SIZE]) != self.ctrmap_signature():
            problems.append("CTRMap header signature digest is stale")
        return problems

    def refresh_integrity(self) -> None:
        """Recompute secure-area, logo, and header CRCs and (when present) the CTRMap signature digest."""
        for name, value in self.expected_crcs().items():
            struct.pack_into("<H", self.data, OFF[name], value)
        if self.has_ctrmap_signature():
            self.data[SIGNATURE_OFFSET : SIGNATURE_OFFSET + SIGNATURE_SIZE] = self.ctrmap_signature()


# --- Builder for synthetic and mock images ------------------------------------


def _build_fnt(paths: list[str]) -> tuple[bytes, list[str]]:
    """Build an FNT for the given file paths. Returns (fnt bytes, paths in file-id order)."""
    tree: dict = {}
    for path in paths:
        node = tree
        parts = path.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = None
    dirs: list[tuple[str, dict, int]] = []  # (prefix, node, parent index)

    def collect(prefix: str, node: dict, parent: int) -> None:
        index = len(dirs)
        dirs.append((prefix, node, parent))
        for name in sorted(k for k, v in node.items() if isinstance(v, dict)):
            collect(f"{prefix}{name}/", node[name], index)

    collect("", tree, 0)
    order: list[str] = []
    sub_tables = []
    child_index = {d[0]: i for i, d in enumerate(dirs)}
    for prefix, node, _parent in dirs:
        table = bytearray()
        for name in sorted(k for k, v in node.items() if v is None):
            encoded = name.encode("latin-1")
            table += bytes([len(encoded)]) + encoded
        for name in sorted(k for k, v in node.items() if isinstance(v, dict)):
            encoded = name.encode("latin-1")
            table += bytes([0x80 | len(encoded)]) + encoded + struct.pack("<H", DIR_ID_BASE + child_index[f"{prefix}{name}/"])
        table += b"\x00"
        sub_tables.append(bytes(table))
    first_ids = []
    for prefix, node, _parent in dirs:
        first_ids.append(len(order))
        order += [f"{prefix}{name}" for name in sorted(k for k, v in node.items() if v is None)]
    main = bytearray()
    offset = len(dirs) * 8
    for index, (prefix, node, parent) in enumerate(dirs):
        third = len(dirs) if index == 0 else DIR_ID_BASE + parent
        main += struct.pack("<IHH", offset, first_ids[index], third)
        offset += len(sub_tables[index])
    return bytes(main) + b"".join(sub_tables), order


def build_rom(
    *,
    game_code: bytes,
    arm9: bytes,
    files: dict[str, bytes],
    overlays: list[tuple[int, bytes, bool]] = (),
    title: bytes = b"SYNTHETICROM",
    extended_header: bool = True,
    ctrmap_signature: bool = True,
) -> bytes:
    """Assemble a structurally valid DS image (used for synthetic tests and the mock builder)."""
    header_size = EXTENDED_HEADER_SIZE if extended_header else HEADER_SIZE
    image = bytearray(max(header_size, 0x4000))

    def align(n: int = 0x200) -> None:
        while len(image) % n:
            image.append(0xFF)

    arm9_offset = 0x4000
    image[arm9_offset:] = arm9
    align()
    arm7_offset = len(image)
    image += b"\x00" * 0x100
    align()
    fnt, order = _build_fnt(sorted(files))
    overlay_file_ids = list(range(len(overlays)))
    fs_offset_ids = len(overlays)
    # NitroFS file ids follow overlay file ids; rebuild FNT with that first id.
    fnt = bytearray(fnt)
    for i in range(0, u16(fnt, 6) * 8, 8):
        struct.pack_into("<H", fnt, i + 4, u16(fnt, i + 4) + fs_offset_ids)
    fnt_offset = len(image)
    image += fnt
    align()
    y9_offset = len(image)
    y9 = bytearray()
    payloads: list[bytes] = []
    for index, (ov_id, payload, compressed) in enumerate(overlays):
        flags = (len(payload) & 0xFFFFFF) | (0x01000000 if compressed else 0)
        y9 += struct.pack("<8I", ov_id, 0x02200000, len(payload), 0, 0, 0, overlay_file_ids[index], flags)
        payloads.append(payload)
    image += y9
    align()
    payloads += [files[path] for path in order]
    fat_offset = len(image)
    image += b"\x00" * (8 * len(payloads))
    align()
    for file_id, payload in enumerate(payloads):
        start = len(image)
        image += payload
        struct.pack_into("<II", image, fat_offset + file_id * 8, start, start + len(payload))
        align(4)
    align()
    image[0:12] = title[:12].ljust(12, b"\x00")
    image[0x0C:0x10] = game_code
    image[0x10:0x12] = b"01"
    image[0x12] = 0x02 if extended_header else 0x00
    for name, value in (
        ("arm9_offset", arm9_offset), ("arm9_ram", 0x02000000), ("arm9_size", len(arm9)),
        ("arm7_offset", arm7_offset), ("arm7_size", 0x100),
        ("fnt_offset", fnt_offset), ("fnt_size", len(fnt)),
        ("fat_offset", fat_offset), ("fat_size", 8 * len(payloads)),
        ("y9_offset", y9_offset), ("y9_size", len(y9)),
        ("used_rom_size", len(image)), ("header_size", header_size),
    ):
        struct.pack_into("<I", image, OFF[name], value)
    if extended_header and ctrmap_signature:
        image[SIGNATURE_OFFSET : SIGNATURE_OFFSET + 108] = b"\x00\x01" + b"\xff" * 105 + b"\x00"
    rom = Rom(image)
    rom.refresh_integrity()
    return bytes(rom.data)
