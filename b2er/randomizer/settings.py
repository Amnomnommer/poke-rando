"""Settings schema, strict normalization, impossible-combination rejection, and the build key."""

from __future__ import annotations

import copy
import hashlib
import json

SCHEMA_VERSION = 1

# Each option: (allowed values or type, default).
SCHEMA: dict[str, dict[str, tuple]] = {
    "species": {
        "national_max": ((1025, 1023, 1010, 905, 898, 809, 721, 649), 1025),
        "include_legendaries": ((True, False), False),
    },
    "starters": {"mode": (("random", "vanilla"), "random")},
    "wild": {"mode": (("random", "vanilla"), "random"), "similar_strength": ((True, False), True)},
    "trainers": {"mode": (("random", "vanilla"), "random"), "similar_strength": ((True, False), True)},
    "items": {
        "field": (("shuffle", "vanilla"), "shuffle"),
        "hidden": (("shuffle", "vanilla"), "vanilla"),
        "shops": (("random", "vanilla"), "vanilla"),
    },
    "gifts": {"mode": (("random", "vanilla"), "vanilla")},
    "statics": {"mode": (("random", "vanilla"), "vanilla")},
    "evolutions": {"normalize_impossible": ((True, False), True)},
    "doubles": {"major_battles": ((True, False), True), "all_trainers": ((True, False), False)},
}
TOP_LEVEL = {
    "schema_version": ((SCHEMA_VERSION,), SCHEMA_VERSION),
    "mode": (("randomized", "diagnostic-route19"), "randomized"),
}
STRETCH_KEYS = {
    "trainers.keep_type_themes": "themed teams are a deferred stretch goal",
    "trainers.improved_ai": "improved trainer AI is a deferred stretch goal",
    "challenge_mode": "Challenge Mode is a deferred stretch goal",
    "level_caps": "custom level caps are a deferred stretch goal",
}


class SettingsError(ValueError):
    pass


def normalize(raw: dict | None) -> dict:
    """Fill defaults, reject unknown or stretch options, and return a canonical settings dict."""
    raw = copy.deepcopy(raw or {})
    if not isinstance(raw, dict):
        raise SettingsError("settings must be a JSON object")
    errors = []
    result: dict = {}
    for key, (allowed, default) in TOP_LEVEL.items():
        value = raw.pop(key, default)
        if value not in allowed or isinstance(value, bool) != isinstance(default, bool):
            errors.append(f"{key}: {value!r} is not one of {list(allowed)}")
        result[key] = value
    if "unsupported_feature_policy" in raw:
        raw.pop("unsupported_feature_policy")
        errors.append("unsupported_feature_policy: removed; unsupported features are always refused before building")
    for section, options in SCHEMA.items():
        given = raw.pop(section, {})
        if not isinstance(given, dict):
            errors.append(f"{section} must be an object")
            given = {}
        normalized = {}
        for option, (allowed, default) in options.items():
            value = given.pop(option, default)
            if value not in allowed or isinstance(value, bool) != isinstance(default, bool):
                errors.append(f"{section}.{option}: {value!r} is not one of {list(allowed)}")
            normalized[option] = value
        for leftover in sorted(given):
            dotted = f"{section}.{leftover}"
            errors.append(f"{dotted}: {STRETCH_KEYS.get(dotted, 'unknown option')}")
        result[section] = normalized
    for leftover in sorted(raw):
        errors.append(f"{leftover}: {STRETCH_KEYS.get(leftover, 'unknown option')}")
    if not errors:
        errors += impossible_combinations(result)
    if errors:
        raise SettingsError("invalid settings:\n  " + "\n  ".join(errors))
    return result


def impossible_combinations(settings: dict) -> list[str]:
    errors = []
    if settings["mode"] == "diagnostic-route19":
        for section, key in (("wild", "mode"), ("trainers", "mode"), ("starters", "mode"), ("items", "field"),
                             ("items", "hidden"), ("items", "shops"), ("gifts", "mode"), ("statics", "mode")):
            if settings[section][key] != "vanilla":
                errors.append(f"diagnostic-route19 mode requires {section}.{key} = 'vanilla' (the fixture must be the only change)")
        if settings["doubles"]["major_battles"] or settings["doubles"]["all_trainers"]:
            errors.append("diagnostic-route19 mode requires doubles.major_battles and doubles.all_trainers = false")
        if settings["evolutions"]["normalize_impossible"]:
            errors.append("diagnostic-route19 mode requires evolutions.normalize_impossible = false")
    if settings["doubles"]["all_trainers"] and not settings["doubles"]["major_battles"]:
        errors.append("doubles.all_trainers = true implies doubles.major_battles = true")
    return errors


def requested_capabilities(settings: dict) -> dict:
    """Map normalized settings to the capabilities a build needs (with numeric requirements)."""
    from ..capabilities import Capability as C
    req = {C.REPACK: None, C.DETERMINISTIC_VERIFICATION: None,
           C.SPECIES_EXPANSION: {"national_dex_max": settings["species"]["national_max"]}}
    if settings["mode"] == "diagnostic-route19":
        req[C.DIAGNOSTIC_FIXTURE] = None
        return req
    req[C.PROGRESSION_ANALYSIS] = None
    flags = [
        (settings["starters"]["mode"] == "random", C.STARTERS), (settings["wild"]["mode"] == "random", C.WILD),
        (settings["trainers"]["mode"] == "random", C.TRAINERS), (settings["gifts"]["mode"] == "random", C.GIFTS),
        (settings["statics"]["mode"] == "random", C.STATICS), (settings["items"]["field"] == "shuffle", C.FIELD_ITEMS),
        (settings["items"]["hidden"] == "shuffle", C.HIDDEN_ITEMS), (settings["items"]["shops"] == "random", C.SHOP_ITEMS),
        (settings["evolutions"]["normalize_impossible"], C.EVOLUTION_NORMALIZATION),
        (settings["doubles"]["major_battles"], C.MAJOR_BATTLE_DOUBLES),
    ]
    for wanted, cap in flags:
        if wanted:
            req[cap] = None
    return req


def diagnostic_settings(national_max: int = 1025) -> dict:
    return normalize({
        "mode": "diagnostic-route19", "species": {"national_max": national_max},
        "starters": {"mode": "vanilla"}, "wild": {"mode": "vanilla"}, "trainers": {"mode": "vanilla"},
        "items": {"field": "vanilla", "hidden": "vanilla", "shops": "vanilla"}, "gifts": {"mode": "vanilla"},
        "statics": {"mode": "vanilla"}, "evolutions": {"normalize_impossible": False},
        "doubles": {"major_battles": False, "all_trainers": False},
    })


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def build_key(settings: dict, seed: str, provenance: dict) -> str:
    """SHA-256 over everything that determines the output. ``provenance`` holds hashes/commits only."""
    if not isinstance(seed, str) or seed == "":
        raise SettingsError("seed must be a non-empty string")
    material = {"tool": "b2er", "settings": settings, "seed": seed, "provenance": provenance}
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def seed_key(seed: str, provenance: dict) -> str:
    """Root RNG key: seed + inputs/tool provenance only (per-feature streams add their own settings)."""
    return hashlib.sha256(canonical_json({"tool": "b2er", "seed": seed, "provenance": provenance}).encode("utf-8")).hexdigest()
