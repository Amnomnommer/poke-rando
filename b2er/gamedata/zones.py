"""White 2 zone header table -> wild encounter record mapping (proven from the user's clean ROM).

Evidence (resource-report.json ea1ce32c..., clean White 2 3e50aec3..., rebuilt diagnostic ROM ee123785...):
  * ``a/0/1/2`` is a one-member NARC holding 615 zone headers of 0x30 bytes (29580 bytes);
  * the u16 at +0x14 carries the wild encounter record index in its low 13 bits; 0x1FFF means "no
    encounters" (448 zones hold 0xDFFF, 24 hold 0x1FFF); the upper bits are flags (W2U changes them in
    19 zones without changing any index);
  * exactly 135 zones carry an index and they cover records 0..134 one-to-one (a/1/2/7 has 135);
  * zone 437 -> record 128 and zone 446 -> record 129. In melonDS, Route 19 showed W2U's record 128
    (all Gible L100, unique in the archive), so Route 19 is zone 437 / record 128. The previous
    species heuristic chose record 129, which zone 446 (the next route) uses.
"""

from __future__ import annotations

import struct

from ..nds import FormatError
from ..nds.narc import read_layout

ZONE_ARCHIVE = "a/0/1/2"
ENTRY_SIZE = 0x30
ENCOUNTER_FIELD = 0x14
ENCOUNTER_MASK = 0x1FFF
NO_ENCOUNTER = 0x1FFF
ROUTE19_ZONE = 437
# Proven for the approved clean White 2 (USA/Europe, IRDO) only; any other input must prove it again.
PROVEN_ROUTE19 = {"3e50aec3db401332175a5d2b5fe2a68ac1a05ec63995dba9d1506b1b51837446": (ROUTE19_ZONE, 128)}


def zone_table(archive: bytes) -> list[bytes]:
    layout = read_layout(archive, frozenset({0x00, 0xFF}))
    if layout.count != 1:
        raise FormatError(f"zone archive has {layout.count} members, expected 1")
    table = layout.member(archive, 0)
    if not table or len(table) % ENTRY_SIZE:
        raise FormatError(f"zone table is {len(table)} bytes, not a multiple of 0x{ENTRY_SIZE:x}")
    return [table[i:i + ENTRY_SIZE] for i in range(0, len(table), ENTRY_SIZE)]


def encounter_indices(archive: bytes) -> list[int | None]:
    """Per zone: the wild encounter record index, or None."""
    out = []
    for entry in zone_table(archive):
        value = struct.unpack_from("<H", entry, ENCOUNTER_FIELD)[0] & ENCOUNTER_MASK
        out.append(None if value == NO_ENCOUNTER else value)
    return out


def check_mapping(indices: list[int | None], record_count: int) -> list[str]:
    """Every record must be used by exactly one zone and every index must exist (true for White 2)."""
    used = [i for i in indices if i is not None]
    problems = []
    if sorted(used) != list(range(record_count)):
        missing = sorted(set(range(record_count)) - set(used))
        extra = sorted({i for i in used if i >= record_count})
        dupes = sorted({i for i in used if used.count(i) > 1})
        problems.append(f"zone -> encounter mapping is not one-to-one over {record_count} records "
                        f"(missing {missing[:5]}, out of range {extra[:5]}, shared {dupes[:5]})")
    return problems


def resolve(archive: bytes, record_count: int, zone: int = ROUTE19_ZONE) -> int:
    indices = encounter_indices(archive)
    problems = check_mapping(indices, record_count)
    if problems:
        raise FormatError("; ".join(problems))
    if not 0 <= zone < len(indices) or indices[zone] is None:
        raise FormatError(f"zone {zone} has no wild encounter record")
    return indices[zone]
