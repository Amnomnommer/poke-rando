"""Shared Generation V machinery: encounter table layout, evolution-method mapping, species codec."""

from __future__ import annotations

from ...model import EvolutionMethod, Reserved, SpeciesCodec, SpeciesKey
from ..base import GameAdapter

# (W2U category, kind) -> normalized table kind; order is the binary slot order.
TABLE_KINDS = {
    ("GRASS", "SINGLES"): "grass", ("GRASS", "DOUBLES"): "grass_doubles", ("GRASS", "SPECIAL"): "grass_special",
    ("SURF", "SINGLES"): "surf", ("SURF", "SPECIAL"): "surf_special",
    ("FISH", "SINGLES"): "fish", ("FISH", "SPECIAL"): "fish_special",
}
GRASS_RATES = (20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1)
WATER_RATES = (60, 30, 5, 4, 1)
TABLE_RATES = {"grass": GRASS_RATES, "grass_doubles": GRASS_RATES, "grass_special": GRASS_RATES,
               "surf": WATER_RATES, "surf_special": WATER_RATES, "fish": WATER_RATES, "fish_special": WATER_RATES}

METHOD_TO_MODEL = {
    "METHOD_LEVELUP": EvolutionMethod.LEVEL,
    "METHOD_FRIENDSHIP": EvolutionMethod.FRIENDSHIP, "METHOD_FRIENDSHIP_DAY": EvolutionMethod.FRIENDSHIP,
    "METHOD_FRIENDSHIP_NIGHT": EvolutionMethod.FRIENDSHIP,
    "METHOD_TRADE": EvolutionMethod.TRADE, "METHOD_TRADE_ITEM": EvolutionMethod.TRADE_ITEM,
    "METHOD_TRADE_MONS_SPECIFIC": EvolutionMethod.TRADE_SPECIES,
    "METHOD_ITEM_USE": EvolutionMethod.ITEM_USE, "METHOD_ITEM_USE_MALE": EvolutionMethod.ITEM_USE,
    "METHOD_ITEM_USE_FEMALE": EvolutionMethod.ITEM_USE,
    "METHOD_ITEM_HELD_DAY": EvolutionMethod.ITEM_HOLD, "METHOD_ITEM_HELD_NIGHT": EvolutionMethod.ITEM_HOLD,
    "METHOD_LEVELUP_MOVE": EvolutionMethod.MOVE_KNOWN, "METHOD_LEVELUP_PARTY_MEMBER": EvolutionMethod.PARTY_SPECIES,
    "METHOD_LOCATION_ELEC": EvolutionMethod.LOCATION, "METHOD_LOCATION_MOSS": EvolutionMethod.LOCATION,
    "METHOD_LOCATION_ICE": EvolutionMethod.LOCATION, "METHOD_LOCATION_ELEC2": EvolutionMethod.LOCATION,
}
for _m in ("ATK_OVER_DEF", "ATK_EQU_DEF", "DEF_OVER_ATK", "RANDOM_1", "RANDOM_2", "NINJASK", "SHEDINJA", "FEMALE",
           "DAY", "NIGHT", "PARTY_TYPE"):
    METHOD_TO_MODEL[f"METHOD_LEVELUP_{_m}"] = EvolutionMethod.LEVEL_CONDITIONAL
LEVEL_PARAMETER_METHODS = {k for k, v in METHOD_TO_MODEL.items() if v in (EvolutionMethod.LEVEL, EvolutionMethod.LEVEL_CONDITIONAL)}

# Methods the White 2 engine can satisfy (items and moves are further checked for availability).
FEASIBLE = frozenset({EvolutionMethod.LEVEL, EvolutionMethod.LEVEL_CONDITIONAL, EvolutionMethod.FRIENDSHIP,
                      EvolutionMethod.ITEM_USE, EvolutionMethod.ITEM_HOLD, EvolutionMethod.MOVE_KNOWN,
                      EvolutionMethod.LOCATION})

# Evolution items obtainable in an unmodified White 2 playthrough (shops, field, gifts).
WHITE2_EVOLUTION_ITEMS = frozenset({
    "ITEM_FIRE_STONE", "ITEM_WATER_STONE", "ITEM_THUNDERSTONE", "ITEM_LEAF_STONE", "ITEM_MOON_STONE", "ITEM_SUN_STONE",
    "ITEM_SHINY_STONE", "ITEM_DUSK_STONE", "ITEM_DAWN_STONE", "ITEM_OVAL_STONE", "ITEM_KINGS_ROCK", "ITEM_METAL_COAT",
    "ITEM_DRAGON_SCALE", "ITEM_UPGRADE", "ITEM_PROTECTOR", "ITEM_ELECTIRIZER", "ITEM_MAGMARIZER", "ITEM_DUBIOUS_DISC",
    "ITEM_REAPER_CLOTH", "ITEM_RAZOR_CLAW", "ITEM_RAZOR_FANG", "ITEM_PRISM_SCALE", "ITEM_DEEPSEATOOTH", "ITEM_DEEPSEASCALE",
})


def w2u_codec(defines: dict[str, int]) -> SpeciesCodec:
    """The pinned W2U layout: 1..1023 ordinary, 1024 Egg, 1025 Bad Egg, then form records (not exposed in Alpha 0.1)."""
    egg, bad = defines["SPECIES_EGG"], defines["SPECIES_BAD_EGG"]
    ordinary = sorted(v for v in defines.values() if 0 < v < egg)
    if ordinary != list(range(1, egg)):
        raise ValueError("W2U ordinary species IDs are not contiguous")
    return SpeciesCodec("w2u-9489aeb", 1, egg - 1, {Reserved.NONE: 0, Reserved.EGG: egg, Reserved.BAD_EGG: bad}, {}, 16)


class Gen5Adapter(GameAdapter):
    generation = 5
    family_id = "nds-gen5"

    def validate(self, data: bytes) -> list[str]:
        from ...families.nds_gen4 import validate as nds_validate  # shared DS container validation
        return nds_validate(data)
