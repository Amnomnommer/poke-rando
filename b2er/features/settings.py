"""Strict version-2 settings model for the future pre-build GUI.

This schema is intentionally separate from the live v1 White 2 pipeline.  An
adapter must opt into each feature before these settings can affect a ROM.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


SCHEMA_VERSION = 2

DEFAULTS: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "quick_start": {
        "enabled": False,
        "player_name": "",
        "rival_name": "",
        "avatar": "default",
        "starter_index": 0,
        "checkpoint": "first_pokemon_center",
    },
    "supplies": {
        "enabled": False,
        "preserve_normal_poke_balls": True,
        "master_balls": 5,
        "rare_candies": 999,
    },
    "egg_mode": {"mode": "off", "hatch_cycles": 1},
    "modern_rules": {"target": "generation_9", "required": True},
    "battle_gimmicks": {"enabled": False, "player_run": "seeded", "bosses": True},
    "follower": {"enabled": False, "party_slot": 0},
    "overworld_encounters": {"mode": "random", "despawn_distance": 12, "maximum_visible": 6},
    "fast_story": {"enabled": False, "checkpoint": "first_pokemon_center"},
    "sprite_customization": {"enabled": False, "catalog": []},
}

ALLOWED: dict[str, set[str]] = {
    "quick_start": {"enabled", "player_name", "rival_name", "avatar", "starter_index", "checkpoint"},
    "supplies": {"enabled", "preserve_normal_poke_balls", "master_balls", "rare_candies"},
    "egg_mode": {"mode", "hatch_cycles"},
    "modern_rules": {"target", "required"},
    "battle_gimmicks": {"enabled", "player_run", "bosses"},
    "follower": {"enabled", "party_slot"},
    "overworld_encounters": {"mode", "despawn_distance", "maximum_visible"},
    "fast_story": {"enabled", "checkpoint"},
    "sprite_customization": {"enabled", "catalog"},
}


def normalize_v2(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("settings must be an object")
    allowed_top = {"schema_version", *ALLOWED}
    unknown_top = set(raw) - allowed_top
    if unknown_top:
        raise ValueError(f"unknown settings sections: {sorted(unknown_top)}")
    if raw.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ValueError(f"settings schema_version must be {SCHEMA_VERSION}")
    result = deepcopy(DEFAULTS)
    for section, allowed_keys in ALLOWED.items():
        supplied = raw.get(section, {})
        if not isinstance(supplied, Mapping):
            raise ValueError(f"{section} must be an object")
        unknown = set(supplied) - allowed_keys
        if unknown:
            raise ValueError(f"unknown {section} settings: {sorted(unknown)}")
        result[section].update(supplied)
    _validate(result)
    return result


def _bool(value: Any, path: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{path} must be a boolean")


def _int(value: Any, path: str, *, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{path} must be an integer >= {minimum}")


def _validate(settings: Mapping[str, Any]) -> None:
    for section in ("quick_start", "supplies", "battle_gimmicks", "follower", "fast_story", "sprite_customization"):
        _bool(settings[section]["enabled"], f"{section}.enabled")
    _bool(settings["supplies"]["preserve_normal_poke_balls"], "supplies.preserve_normal_poke_balls")
    _bool(settings["modern_rules"]["required"], "modern_rules.required")
    _bool(settings["battle_gimmicks"]["bosses"], "battle_gimmicks.bosses")
    _int(settings["quick_start"]["starter_index"], "quick_start.starter_index")
    if settings["quick_start"]["starter_index"] > 2:
        raise ValueError("quick_start.starter_index must be 0, 1, or 2")
    _int(settings["supplies"]["master_balls"], "supplies.master_balls")
    _int(settings["supplies"]["rare_candies"], "supplies.rare_candies")
    _int(settings["egg_mode"]["hatch_cycles"], "egg_mode.hatch_cycles", minimum=1)
    _int(settings["follower"]["party_slot"], "follower.party_slot")
    if settings["follower"]["party_slot"] != 0:
        raise ValueError("follower.party_slot must be 0 (lead only)")
    _int(settings["overworld_encounters"]["despawn_distance"], "overworld_encounters.despawn_distance", minimum=1)
    _int(settings["overworld_encounters"]["maximum_visible"], "overworld_encounters.maximum_visible", minimum=1)
    if settings["egg_mode"]["mode"] not in {"off", "starter_egg", "route_encounter_egg", "gift_eggs", "full_egg"}:
        raise ValueError("egg_mode.mode is invalid")
    if settings["battle_gimmicks"]["player_run"] not in {"off", "seeded", "mega", "z_move", "dynamax", "terastallize"}:
        raise ValueError("battle_gimmicks.player_run is invalid")
    if settings["overworld_encounters"]["mode"] not in {"random", "overworld", "mixed"}:
        raise ValueError("overworld_encounters.mode is invalid")
    if settings["modern_rules"]["target"] != "generation_9":
        raise ValueError("the 1.0 mechanics target is generation_9")
    if not isinstance(settings["sprite_customization"]["catalog"], list):
        raise ValueError("sprite_customization.catalog must be an array")


def gui_sections() -> tuple[dict[str, Any], ...]:
    """Return stable, toolkit-neutral fields for a future desktop GUI."""
    return (
        {"id": "quick_start", "release": "1.0", "label": "Fast Start"},
        {"id": "supplies", "release": "1.0", "label": "Quick-Start Supplies"},
        {"id": "egg_mode", "release": "1.0", "label": "Randomized Egg Mode"},
        {"id": "modern_rules", "release": "1.0", "label": "Modern Mechanics"},
        {"id": "battle_gimmicks", "release": "post-1.1", "label": "Battle Gimmicks"},
        {"id": "follower", "release": "1.1", "label": "Lead Follower"},
        {"id": "overworld_encounters", "release": "1.2", "label": "Overworld Encounters"},
        {"id": "fast_story", "release": "1.3", "label": "Streamlined Story"},
        {"id": "sprite_customization", "release": "1.4", "label": "Character Sprites"},
    )
