"""Lead-only follower planning for the 1.1/1.2 roadmap."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import FeaturePlan, Requirement


@dataclass(frozen=True)
class FollowerSettings:
    enabled: bool = False
    party_slot: int = 0


def plan_follower(settings: FollowerSettings) -> FeaturePlan:
    if settings.party_slot != 0:
        raise ValueError("only the lead Pokémon may follow")
    return FeaturePlan(
        feature="lead_follower",
        version=1,
        settings={"enabled": settings.enabled, "party_slot": settings.party_slot},
        operations=() if not settings.enabled else ({"kind": "spawn_lead_follower", "party_slot": 0},),
        requirements=() if not settings.enabled else (
            Requirement("overworld_follower_actor", "spawn and update one follower actor"),
            Requirement("follower_sprite_catalog", "resolve a compatible, licensed sprite for the lead species and form"),
            Requirement("follower_map_safety", "hide or reposition the follower where maps cannot support it"),
        ),
    )
