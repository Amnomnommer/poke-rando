"""Script-level randomization on the finished ROM: starter gift commands, gifts, statics, and items.

Sites are constant operands of decoded commands (see b2er.gamedata.scripts):
``PokePartyAdd``/``PokePartyAddEx`` species, wild-battle species, and ``ItemAdd``
item IDs. Patches are same-width in place, so the script archive keeps its exact
size and every other byte. A feature whose sites cannot be established by
evidence is skipped (policy "skip") or fails the build (policy "fail").
"""

from __future__ import annotations

from dataclasses import dataclass

from ..gamedata import scripts
from ..nds import FormatError
from ..nds.narc import Narc
from ..nds.rom import Rom
from .rng import Stream

GIFT_OPCODES = ("PokePartyAdd", "PokePartyAddEx")
ITEM_OPCODE = "ItemAdd"
TM_IDS = set(range(328, 420)) | {618, 619, 620}
HM_IDS = set(range(420, 426))
ORDINARY_ITEM_IDS = set(range(1, 328))  # balls, medicine, battle and held items, berries, mail


def item_class(item: int) -> str | None:
    if item in HM_IDS:
        return None  # progression: never moved
    if item in TM_IDS:
        return "tm"
    if item in ORDINARY_ITEM_IDS:
        return "ordinary"
    return None  # key items and anything unclassified are never moved


def find_script_archive(rom: Rom) -> tuple[str, Narc]:
    """Identify the event-script archive structurally; require a unique match."""
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
                if scripts.entry_points(member):
                    good += 1
            except FormatError:
                pass
        if good >= 0.9 * len(narc.members):
            candidates.append((path, narc))
    if len(candidates) != 1:
        raise FormatError(f"expected exactly one event-script archive, found {[c[0] for c in candidates]}")
    return candidates[0]


@dataclass
class ScriptResult:
    archive: str | None
    patches: list[dict]
    features: dict
    warnings: list[str]


def wild_opcodes(table: dict[int, scripts.Opcode]) -> dict[str, str]:
    found = {}
    for op in table.values():
        if "Wild" in op.name and any(o.name == "species" for o in op.operands):
            found[op.name] = "species"
    return found


def apply(rom: Rom, table: dict[int, scripts.Opcode], context: dict, settings: dict, key: bytes) -> ScriptResult:
    """Plan and apply script patches. ``context`` holds species/item facts from the source data."""
    warnings: list[str] = []
    features: dict = {}
    patches: list[dict] = []
    wanted = {name: "species" for name in GIFT_OPCODES}
    wanted[ITEM_OPCODE] = "itemId"
    statics = wild_opcodes(table)
    wanted.update(statics)
    try:
        archive_path, narc = find_script_archive(rom)
    except FormatError as error:
        reason = f"script archive not identified: {error}"
        for feature in ("starter_scripts", "gifts", "statics", "items"):
            features[feature] = f"skipped ({reason})"
        return ScriptResult(None, [], features, [reason])

    sites: list[scripts.ConstantSite] = []
    for index, member in enumerate(narc.members):
        try:
            sites += scripts.constant_sites(index, member, table, wanted)
        except FormatError:
            continue
    members = [bytearray(m) for m in narc.members]
    vanilla_starters = context["vanilla_starters"]
    chosen = context["starters"]
    gift_sites = [s for s in sites if s.opcode_name in GIFT_OPCODES]
    starter_sites = [s for s in gift_sites if s.value in vanilla_starters and dict(s.context).get("level") == 5]

    def patch(site: scripts.ConstantSite, value: int, feature: str) -> None:
        scripts.patch_u16(members[site.member], site, value, table)
        patches.append({"feature": feature, "member": site.member, "command": site.opcode_name,
                        "offset": site.value_offset, "before": site.value, "after": value})

    # Starter gift commands.
    if settings["starters"]["mode"] == "vanilla":
        features["starter_scripts"] = "vanilla"
    elif not all(any(s.value == v for s in starter_sites) for v in vanilla_starters):
        features["starter_scripts"] = "skipped (no constant level-5 gift command for every vanilla starter was decoded)"
        warnings.append(features["starter_scripts"])
    else:
        for site in starter_sites:
            patch(site, chosen[vanilla_starters.index(site.value)], "starter_scripts")
        features["starter_scripts"] = f"applied ({len(starter_sites)} gift commands)"

    # Other gifts.
    obtainable = context["obtainable"]
    pick = context["pick_similar"]
    if settings["gifts"]["mode"] == "random":
        stream = Stream(key, "gifts")
        others = [s for s in gift_sites if s not in starter_sites and context["is_ordinary"](s.value)]
        for site in others:
            level = dict(site.context).get("level", 5)
            if level >= scripts.VARIABLE_MIN:
                continue
            patch(site, pick(stream, site.value, obtainable, level), "gifts")
        features["gifts"] = f"applied ({len(others)} gift commands)"
    else:
        features["gifts"] = "vanilla"

    # Static encounters.
    if settings["statics"]["mode"] == "random":
        if not statics:
            features["statics"] = "skipped (no wild-battle command with a species operand in the SDK table)"
            warnings.append(features["statics"])
        else:
            stream = Stream(key, "statics")
            static_sites = [s for s in sites if s.opcode_name in statics and context["is_ordinary"](s.value)]
            for site in static_sites:
                level = dict(site.context).get("level", 30)
                patch(site, pick(stream, site.value, obtainable, level if level < scripts.VARIABLE_MIN else 30), "statics")
            features["statics"] = f"applied ({len(static_sites)} wild-battle commands)"
    else:
        features["statics"] = "vanilla"

    # Items: shuffle within class, never key items or HMs.
    if settings["items"]["mode"] == "shuffle":
        stream = Stream(key, "items")
        item_sites = [s for s in sites if s.opcode_name == ITEM_OPCODE and item_class(s.value)]
        for cls in ("ordinary", "tm"):
            group = [s for s in item_sites if item_class(s.value) == cls]
            shuffled = stream.shuffle([s.value for s in group])
            for site, value in zip(group, shuffled):
                if value != site.value:
                    patch(site, value, "items")
        features["items"] = f"applied ({len(item_sites)} ItemAdd sites shuffled within class; key items and HMs fixed)"
        if not item_sites:
            warnings.append("no shuffle-eligible ItemAdd sites were decoded")
    else:
        features["items"] = "vanilla"

    if patches:
        rebuilt = Narc(list(map(bytes, members)), narc.fnt, narc.version, narc.pad_byte, narc.alignment, narc.pad_tail).build()
        original = rom.read_path(archive_path)
        if len(rebuilt) != len(original):
            raise FormatError("script archive size changed; in-place patching must preserve size")
        rom.replace_file_in_place(rom.file_id(archive_path), rebuilt)
    return ScriptResult(archive_path, patches, features, warnings)


def verify(rom: Rom, archive_path: str, patches: list[dict]) -> list[str]:
    narc = Narc.parse(rom.read_path(archive_path))
    problems = []
    for p in patches:
        member = narc.members[p["member"]]
        value = int.from_bytes(member[p["offset"] : p["offset"] + 2], "little")
        if value != p["after"]:
            problems.append(f"script member {p['member']} offset 0x{p['offset']:x} holds {value}, expected {p['after']}")
    return problems
