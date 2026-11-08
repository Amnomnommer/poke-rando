"""Read-only runtime resource-identity diagnostic for a clean and a rebuilt Generation V ROM.

Emits JSON with only logical names, file IDs, offsets, sizes, hashes, decoded species/form/level numbers
and equality results. It never emits ROM bytes, text, player data or save data, and never writes files.

Why: the first structurally verified diagnostic ROM showed W2U's own encounter record 128 (all Gible L100)
on Route 19 while the verifier had checked record 129, chosen by a species-signature heuristic. The game
selects encounter records through the zone header table, so the mapping must be proven from that table
in the user's ROM, not inferred.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from .gamedata import encounters as enc
from .nds import FormatError
from .nds.narc import read_layout
from .nds.rom import Rom

ENCOUNTER_ARCHIVE = "a/1/2/7"
ZONE_ARCHIVE = "a/0/1/2"  # candidate zone-header archive; its layout is reported, not assumed
ZONE_ENTRY_SIZE = 0x30
SCHEMA = 1


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_identity(clean: Rom, rebuilt: Rom) -> dict:
    a, b = clean.paths(), rebuilt.paths()
    rows, shifted, changed = [], [], 0
    for path in sorted(set(a) | set(b)):
        row = {"path": path, "clean_id": a.get(path), "rebuilt_id": b.get(path)}
        if path in a:
            data = clean.file_bytes(a[path])
            row.update(clean_offset=clean.fat()[a[path]][0], clean_size=len(data), clean_sha256=sha(data))
        if path in b:
            data = rebuilt.file_bytes(b[path])
            row.update(rebuilt_offset=rebuilt.fat()[b[path]][0], rebuilt_size=len(data), rebuilt_sha256=sha(data))
        row["same_id"] = row["clean_id"] == row["rebuilt_id"]
        row["same_bytes"] = row.get("clean_sha256") == row.get("rebuilt_sha256")
        if path in a and path in b and not row["same_id"]:
            shifted.append(path)
        changed += not row["same_bytes"]
        rows.append(row)
    return {"files": rows, "summary": {"clean_files": len(a), "rebuilt_files": len(b), "ids_shifted": len(shifted),
                                       "shifted_paths": shifted[:50], "content_changed": changed,
                                       "only_clean": sorted(set(a) - set(b))[:50], "only_rebuilt": sorted(set(b) - set(a))[:50]}}


def encounter_members(rom: Rom) -> dict:
    """Decoded numbers of every encounter record, read through the archive's own FAT (numeric file ID)."""
    file_id = rom.file_id(ENCOUNTER_ARCHIVE)
    data = rom.file_bytes(file_id)
    layout = read_layout(data, frozenset({0x00, 0xFF}))
    members = []
    for index in range(layout.count):
        member = layout.member(data, index)
        row = {"member": index, "fat_range": list(layout.ranges[index]), "size": len(member), "sha256": sha(member)}
        try:
            containers = enc.decode_record(member, f"member {index}")
            row["seasons"] = len(containers)
            row["grass_singles"] = [[[s.species, s.form, s.min_level, s.max_level] for s in c.tables[("GRASS", "SINGLES")]]
                                    for c in containers]
        except FormatError as error:
            row["decode_error"] = str(error)
        members.append(row)
    return {"path": ENCOUNTER_ARCHIVE, "file_id": file_id, "size": len(data), "sha256": sha(data), "members": members}


def zone_table(rom: Rom, encounter_count: int) -> dict:
    """Every u16 column of the candidate zone-header table, with the columns whose values are all valid
    encounter indices (or 0xFFFF) flagged as candidates for the zone -> encounter mapping."""
    try:
        file_id = rom.file_id(ZONE_ARCHIVE)
    except FormatError as error:
        return {"path": ZONE_ARCHIVE, "error": str(error)}
    data = rom.file_bytes(file_id)
    out = {"path": ZONE_ARCHIVE, "file_id": file_id, "size": len(data), "sha256": sha(data)}
    try:
        layout = read_layout(data, frozenset({0x00, 0xFF}))
        blob = layout.member(data, 0)
        out["narc_members"] = layout.count
    except FormatError:
        blob = data
    if len(blob) % ZONE_ENTRY_SIZE:
        out["error"] = f"table size {len(blob)} is not a multiple of 0x{ZONE_ENTRY_SIZE:x}"
        return out
    zones = [list(struct.unpack_from(f"<{ZONE_ENTRY_SIZE // 2}H", blob, i)) for i in range(0, len(blob), ZONE_ENTRY_SIZE)]
    candidates = [col * 2 for col in range(ZONE_ENTRY_SIZE // 2)
                  if all(z[col] < encounter_count or z[col] == 0xFFFF for z in zones) and len({z[col] for z in zones}) > 10]
    out.update(entry_size=ZONE_ENTRY_SIZE, zones=len(zones), u16_fields=zones, encounter_column_candidates=candidates,
               zones_by_candidate={str(off): {str(m): [i for i, z in enumerate(zones) if z[off // 2] == m] for m in (128, 129)}
                                   for off in candidates})
    return out


def report(clean_path: Path, rebuilt_path: Path) -> dict:
    clean_bytes, rebuilt_bytes = Path(clean_path).read_bytes(), Path(rebuilt_path).read_bytes()
    clean, rebuilt = Rom(clean_bytes), Rom(rebuilt_bytes)
    rebuilt_enc = encounter_members(rebuilt)
    out = {"schema": SCHEMA,
           "inputs": {"clean": {"sha256": sha(clean_bytes), "size": len(clean_bytes), "game_code": clean.game_code},
                      "rebuilt": {"sha256": sha(rebuilt_bytes), "size": len(rebuilt_bytes), "game_code": rebuilt.game_code}},
           "identity": file_identity(clean, rebuilt),
           "encounters": {"clean": encounter_members(clean), "rebuilt": rebuilt_enc,
                          "verifier_archive": {"path": ENCOUNTER_ARCHIVE, "rebuilt_file_id": rebuilt.file_id(ENCOUNTER_ARCHIVE)}},
           "zones": {"clean": zone_table(clean, len(rebuilt_enc["members"])),
                     "rebuilt": zone_table(rebuilt, len(rebuilt_enc["members"]))}}
    out["encounters"]["all_gible_level_100_members"] = [
        m["member"] for m in rebuilt_enc["members"]
        if m.get("grass_singles") and all(s[0] == 443 and s[2] == s[3] == 100 for s in m["grass_singles"][0])]
    return out
