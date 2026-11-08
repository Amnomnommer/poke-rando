"""Future overworld-encounter behavior contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .contracts import FeaturePlan, Requirement


class EncounterPresentation(str, Enum):
    RANDOM = "random"
    OVERWORLD = "overworld"
    MIXED = "mixed"


@dataclass(frozen=True)
class OverworldSettings:
    mode: EncounterPresentation = EncounterPresentation.RANDOM
    despawn_distance: int = 12
    maximum_visible: int = 6


def plan_overworld(settings: OverworldSettings) -> FeaturePlan:
    mode = EncounterPresentation(settings.mode)
    if settings.despawn_distance < 1 or settings.maximum_visible < 1:
        raise ValueError("overworld encounter limits must be positive")
    enabled = mode is not EncounterPresentation.RANDOM
    return FeaturePlan(
        feature="overworld_encounters",
        version=1,
        settings={
            "mode": mode.value,
            "despawn_distance": settings.despawn_distance,
            "maximum_visible": settings.maximum_visible,
        },
        operations=() if not enabled else ({"kind": "enable_overworld_encounters", "mode": mode.value},),
        requirements=() if not enabled else (
            Requirement("overworld_encounter_actor", "represent generated encounters as map actors"),
            Requirement("encounter_sprite_catalog", "resolve licensed overworld sprites by species and form"),
            Requirement("spawn_budget", "enforce map-specific actor and performance limits"),
            Requirement("encounter_persistence", "define deterministic spawn, despawn and re-entry behavior"),
        ),
    )
