"""Machine-readable Generation 9 rules baseline for the 1.0 roadmap."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RuleStatus(str, Enum):
    NATIVE = "native"
    IMPLEMENTED = "implemented"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


REQUIRED_1_0_DOMAINS = (
    "species_1_1025",
    "fairy_type",
    "current_type_chart",
    "physical_special_split",
    "current_status",
    "modern_weather",
    "modern_terrain",
    "modern_moves",
    "modern_abilities",
    "modern_items",
    "modern_evolutions",
    "modern_learnsets",
    "modern_compatibility",
    "modern_experience",
)


@dataclass(frozen=True)
class ModernRulesSupport:
    game: str
    domains: dict[str, RuleStatus]

    def __post_init__(self) -> None:
        missing = set(REQUIRED_1_0_DOMAINS) - set(self.domains)
        extra = set(self.domains) - set(REQUIRED_1_0_DOMAINS)
        if missing or extra:
            raise ValueError(f"modern-rules domains differ: missing={sorted(missing)}, extra={sorted(extra)}")

    @property
    def complete(self) -> bool:
        return all(status in {RuleStatus.NATIVE, RuleStatus.IMPLEMENTED} for status in self.domains.values())

    def unresolved(self) -> tuple[str, ...]:
        return tuple(name for name in REQUIRED_1_0_DOMAINS if self.domains[name] not in {RuleStatus.NATIVE, RuleStatus.IMPLEMENTED})
