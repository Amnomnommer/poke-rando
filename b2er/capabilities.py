"""Capability declarations: what an adapter can do, with evidence, checked before anything is modified."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Capability(str, Enum):
    RECOGNITION = "rom_recognition"
    REPACK = "repacking"
    SPECIES_EXPANSION = "species_expansion"
    STARTERS = "starter_randomization"
    WILD = "wild_randomization"
    TRAINERS = "trainer_randomization"
    GIFTS = "gift_randomization"
    STATICS = "static_randomization"
    FIELD_ITEMS = "field_item_randomization"
    HIDDEN_ITEMS = "hidden_item_randomization"
    SHOP_ITEMS = "shop_item_randomization"
    EVOLUTION_NORMALIZATION = "evolution_normalization"
    PROGRESSION_ANALYSIS = "progression_analysis"
    MAJOR_BATTLE_DOUBLES = "major_battle_doubles"
    POKEDEX_EXPANSION = "pokedex_expansion"
    SAVE_FORMAT_EXPANSION = "save_format_expansion"
    DETERMINISTIC_VERIFICATION = "deterministic_verification"
    DIAGNOSTIC_FIXTURE = "diagnostic_fixture"


class Level(str, Enum):
    SUPPORTED = "supported"  # implemented and verified structurally/semantically
    EXPERIMENTAL = "experimental"  # implemented; output marked experimental in the manifest
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class Support:
    level: Level
    evidence: str
    limit: dict | None = None  # e.g. {"national_dex_max": 1023}

    def to_json(self) -> dict:
        out = {"level": self.level.value, "evidence": self.evidence}
        if self.limit:
            out["limit"] = dict(self.limit)
        return out


def unsupported(evidence: str) -> Support:
    return Support(Level.UNSUPPORTED, evidence)


class CapabilityError(RuntimeError):
    """A requested feature is not available; raised before any workspace or output exists."""


def check(declared: dict[Capability, Support], requested: dict[Capability, dict | None]) -> list[str]:
    """Return every reason a request cannot be satisfied (empty when it can)."""
    problems = []
    for capability, needs in requested.items():
        support = declared.get(capability, unsupported("not declared by the adapter"))
        if support.level == Level.UNSUPPORTED:
            problems.append(f"{capability.value}: unsupported ({support.evidence})")
            continue
        if needs and support.limit:
            for key, wanted in needs.items():
                available = support.limit.get(key)
                if available is not None and wanted > available:
                    problems.append(f"{capability.value}: requested {key}={wanted} but the adapter supports "
                                    f"{available} ({support.evidence})")
    return problems
