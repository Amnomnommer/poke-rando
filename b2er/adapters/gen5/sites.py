"""Generation V script sites: discovery on the clean base ROM, guarded patching of the finished ROM.

Sites are constant operands of fully decoded commands (b2er.gamedata.scripts).
Discovery runs before any modification; patching re-checks every original
value and command, so a site W2U changed fails instead of being overwritten.
"""

from __future__ import annotations

from ...gamedata import scripts
from ...model import ItemLocation, PokemonSite, SpeciesCodec, SpeciesKey
from ...nds import FormatError
from ...nds.narc import Narc
from ...nds.rom import Rom

GIFT_OPCODES = ("PokePartyAdd", "PokePartyAddEx")
ITEM_OPCODE = "ItemAdd"
TM_IDS = set(range(328, 420)) | {618, 619, 620}
HM_IDS = set(range(420, 426))
ORDINARY_ITEM_IDS = set(range(1, 328))
VANILLA_STARTERS = (495, 498, 501)


def item_class(item: int) -> str:
    if item in HM_IDS:
        return "hm"
    if item in TM_IDS:
        return "tm"
    if item in ORDINARY_ITEM_IDS:
        return "ordinary"
    return "key"  # key items and anything unclassified are treated as progression-relevant


def find_script_archive(rom: Rom) -> tuple[str, Narc]:
    candidates = []
    for path in sorted(rom.paths()):
        data = rom.read_path(path)
        if data[:4] != b"NARC":
            continue
        try:
            narc = Narc.parse(data)
        except FormatError:
            continue
        if len(narc.members) < 50:
            continue
        good = 0
        for member in narc.members:
            try:
                good += bool(scripts.entry_points(member))
            except FormatError:
                pass
        if good >= 0.9 * len(narc.members):
            candidates.append((path, narc))
    if len(candidates) != 1:
        raise FormatError(f"expected exactly one event-script archive, found {[c[0] for c in candidates]}")
    return candidates[0]


def wild_opcodes(table) -> dict[str, str]:
    return {op.name: "species" for op in table.values() if "Wild" in op.name and any(o.name == "species" for o in op.operands)}


def discover(rom: Rom, table, codec: SpeciesCodec, item_names: dict[int, str]) -> dict:
    """Return model entries for starters, gifts, statics, and item locations found in the script archive."""
    archive, narc = find_script_archive(rom)
    wanted = {name: "species" for name in GIFT_OPCODES}
    wanted[ITEM_OPCODE] = "itemId"
    statics_ops = wild_opcodes(table)
    wanted.update(statics_ops)
    starters, gifts, statics, items = [], [], [], []
    for index, member in enumerate(narc.members):
        try:
            sites = scripts.constant_sites(index, member, table, wanted)
        except FormatError:
            continue
        for s in sites:
            ext = {"archive": archive, "member": s.member, "command_offset": s.command_offset, "value_offset": s.value_offset,
                   "command": s.opcode_name, "value": s.value}
            site_id = f"{archive}:{s.member}:{s.value_offset:#x}"
            if s.opcode_name == ITEM_OPCODE:
                items.append(ItemLocation(site_id, "scripted", item_names.get(s.value, str(s.value)), item_class(s.value),
                                          ext))
                continue
            level = dict(s.context).get("level", 0)
            if not 1 <= s.value <= codec.national_max or level >= scripts.VARIABLE_MIN:
                continue
            kind = "static" if s.opcode_name in statics_ops else ("starter" if s.value in VANILLA_STARTERS and level == 5 else "gift")
            site = PokemonSite(site_id, kind, SpeciesKey(s.value), level or 5, ext)
            {"starter": starters, "gift": gifts, "static": statics}[kind].append(site)
    return {"archive": archive, "starters": starters, "gifts": gifts, "statics": statics, "items": items,
            "starter_species_found": sorted({s.species.national for s in starters})}


def apply_patches(rom: Rom, table, patches: list[dict]) -> None:
    """Apply same-width u16 patches in place; every site's original value and command must still match."""
    by_archive: dict[str, list[dict]] = {}
    for p in patches:
        by_archive.setdefault(p["archive"], []).append(p)
    for archive, group in by_archive.items():
        original = rom.read_path(archive)
        narc = Narc.parse(original)
        members = [bytearray(m) for m in narc.members]
        for p in group:
            site = scripts.ConstantSite(p["member"], p["command_offset"], p["command"], "", p["value_offset"], p["before"], ())
            scripts.patch_u16(members[p["member"]], site, p["after"], table)
        rebuilt = Narc(list(map(bytes, members)), narc.fnt, narc.version, narc.pad_byte, narc.alignment, narc.pad_tail).build()
        if len(rebuilt) != len(original):
            raise FormatError("script archive size changed; in-place patching must preserve size")
        rom.replace_file_in_place(rom.file_id(archive), rebuilt)


def verify_patches(rom: Rom, patches: list[dict]) -> list[str]:
    problems = []
    cache: dict[str, Narc] = {}
    for p in patches:
        narc = cache.setdefault(p["archive"], Narc.parse(rom.read_path(p["archive"])))
        member = narc.members[p["member"]]
        value = int.from_bytes(member[p["value_offset"]:p["value_offset"] + 2], "little")
        if value != p["after"]:
            problems.append(f"{p['archive']} member {p['member']} @0x{p['value_offset']:x} = {value}, expected {p['after']}")
    return problems
