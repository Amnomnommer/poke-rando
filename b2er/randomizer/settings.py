"""Settings schema, strict normalization, impossible-combination rejection, and the build key."""

from __future__ import annotations

import copy
import hashlib
import json

SCHEMA_VERSION = 1

# Each option: (allowed values or type, default).
SCHEMA: dict[str, dict[str, tuple]] = {
    "species": {
        "include_legendaries": ((True, False), False),
    },
    "starters": {
        "mode": (("random", "vanilla"), "random"),
    },
    "wild": {
        "mode": (("random", "vanilla"), "random"),
        "similar_strength": ((True, False), True),
    },
    "trainers": {
        "mode": (("random", "vanilla"), "random"),
        "similar_strength": ((True, False), True),
    },
    "items": {
        "mode": (("shuffle", "vanilla"), "shuffle"),
    },
    "gifts": {
        "mode": (("random", "vanilla"), "vanilla"),
    },
    "statics": {
        "mode": (("random", "vanilla"), "vanilla"),
    },
    "evolutions": {
        "normalize_impossible": ((True,), True),
    },
    "doubles": {
        "major_battles": ((True, False), True),
        "all_trainers": ((True, False), False),
    },
}
TOP_LEVEL = {
    "schema_version": ((SCHEMA_VERSION,), SCHEMA_VERSION),
    "mode": (("randomized", "diagnostic-route19"), "randomized"),
    "unsupported_feature_policy": (("skip", "fail"), "skip"),
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
        for section, key in (("wild", "mode"), ("trainers", "mode"), ("starters", "mode"), ("items", "mode"),
                             ("gifts", "mode"), ("statics", "mode")):
            if settings[section][key] not in ("vanilla",):
                errors.append(f"diagnostic-route19 mode requires {section}.{key} = 'vanilla' (the fixture must be the only change)")
        if settings["doubles"]["major_battles"] or settings["doubles"]["all_trainers"]:
            errors.append("diagnostic-route19 mode requires doubles.major_battles and doubles.all_trainers = false")
    if settings["doubles"]["all_trainers"] and not settings["doubles"]["major_battles"]:
        errors.append("doubles.all_trainers = true implies doubles.major_battles = true")
    return errors


def diagnostic_settings() -> dict:
    return normalize({
        "mode": "diagnostic-route19",
        "starters": {"mode": "vanilla"}, "wild": {"mode": "vanilla"}, "trainers": {"mode": "vanilla"},
        "items": {"mode": "vanilla"}, "gifts": {"mode": "vanilla"}, "statics": {"mode": "vanilla"},
        "doubles": {"major_battles": False, "all_trainers": False},
    })


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def build_key(settings: dict, seed: str, provenance: dict) -> str:
    """SHA-256 over everything that determines the output. ``provenance`` holds hashes/commits only."""
    if not isinstance(seed, str) or seed == "":
        raise SettingsError("seed must be a non-empty string")
    material = {"tool": "b2er-alpha-0.1", "settings": settings, "seed": seed, "provenance": provenance}
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
