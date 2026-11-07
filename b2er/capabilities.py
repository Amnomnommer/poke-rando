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
    """Per-capability implementation level (never a claim of game support; see SupportStatus)."""

    SUPPORTED = "implemented"  # implemented and verified structurally/semantically
    EXPERIMENTAL = "partial"  # implemented with documented gaps; marked in the manifest
    UNSUPPORTED = "unavailable"


class SupportStatus(str, Enum):
    """Game/release support ladder. Each rung requires all lower rungs."""

    UNKNOWN = "unknown"
    RECOGNIZED = "recognized"  # identified; nothing is built
    BUILDABLE = "buildable"  # adapter can safely unpack, rebuild, and verify
    FEATURE_COMPLETE = "feature-complete"  # every required transformation implemented
    RUNTIME_VALIDATED = "runtime-validated"  # required gameplay tests passed
    PLAYTHROUGH_VALIDATED = "playthrough-validated"  # a complete playthrough passed

    @property
    def rank(self) -> int:
        return list(SupportStatus).index(self)

    def at_least(self, other: "SupportStatus") -> bool:
        return self.rank >= other.rank


# Every capability a release must implement before it may be called feature-complete.
FEATURE_COMPLETE_REQUIRES = (
    "repacking", "species_expansion", "starter_randomization", "wild_randomization", "trainer_randomization",
    "gift_randomization", "static_randomization", "field_item_randomization", "hidden_item_randomization",
    "shop_item_randomization", "evolution_normalization", "progression_analysis", "major_battle_doubles",
    "pokedex_expansion", "save_format_expansion", "deterministic_verification",
)


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
            problems.append(f"{capability.value}: unavailable ({support.evidence})")
            continue
        if needs and support.limit:
            for key, wanted in needs.items():
                available = support.limit.get(key)
                if available is not None and wanted > available:
                    problems.append(f"{capability.value}: requested {key}={wanted} but the adapter supports "
                                    f"{available} ({support.evidence})")
    return problems
