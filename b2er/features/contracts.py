"""Shared, serializable contracts for future randomizer features."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class FeatureState(str, Enum):
    UNAVAILABLE = "unavailable"
    PLANNED = "planned"
    IMPLEMENTED = "implemented"
    VERIFIED = "verified"


@dataclass(frozen=True)
class Requirement:
    capability: str
    reason: str

    def __post_init__(self) -> None:
        if not self.capability or not self.reason:
            raise ValueError("requirements need a capability and reason")


@dataclass(frozen=True)
class FeaturePlan:
    feature: str
    version: int
    settings: Mapping[str, Any]
    operations: tuple[Mapping[str, Any], ...] = ()
    requirements: tuple[Requirement, ...] = ()
    warnings: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.feature:
            raise ValueError("feature name cannot be empty")
        if self.version < 1:
            raise ValueError("plan version must be positive")

    def missing(self, capabilities: Mapping[str, FeatureState | str]) -> tuple[Requirement, ...]:
        usable = {FeatureState.IMPLEMENTED.value, FeatureState.VERIFIED.value}
        return tuple(
            requirement
            for requirement in self.requirements
            if str(getattr(capabilities.get(requirement.capability), "value", capabilities.get(requirement.capability)))
            not in usable
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "version": self.version,
            "settings": dict(self.settings),
            "operations": [dict(operation) for operation in self.operations],
            "requirements": [
                {"capability": item.capability, "reason": item.reason}
                for item in self.requirements
            ],
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
        }
