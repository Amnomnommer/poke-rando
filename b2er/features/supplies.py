"""Quick-start supply planning, including diagnostic defaults."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import FeaturePlan, Requirement


@dataclass(frozen=True)
class SupplySettings:
    enabled: bool = False
    preserve_normal_poke_balls: bool = True
    master_balls: int = 5
    rare_candies: int = 999

    def validate(self, *, max_stack: int | None = None) -> None:
        if self.master_balls < 0 or self.rare_candies < 0:
            raise ValueError("supply quantities cannot be negative")
        if max_stack is not None:
            if max_stack < 1:
                raise ValueError("max_stack must be positive")
            if self.master_balls > max_stack or self.rare_candies > max_stack:
                raise ValueError("supply quantity exceeds the adapter's safe stack limit")


def plan_supplies(
    settings: SupplySettings,
    *,
    diagnostic: bool = False,
    max_stack: int | None = None,
) -> FeaturePlan:
    effective = SupplySettings(
        enabled=settings.enabled or diagnostic,
        preserve_normal_poke_balls=settings.preserve_normal_poke_balls,
        master_balls=settings.master_balls,
        rare_candies=settings.rare_candies,
    )
    effective.validate(max_stack=max_stack)
    operations: tuple[dict[str, object], ...] = ()
    requirements: tuple[Requirement, ...] = ()
    if effective.enabled:
        operations = (
            {"kind": "grant_item", "item": "master_ball", "quantity": effective.master_balls},
            {"kind": "grant_item", "item": "rare_candy", "quantity": effective.rare_candies},
        )
        requirements = (
            Requirement("early_item_award", "grant supplies at a proven early-game award site"),
            Requirement("item_stack_limits", "encode quantities without overflowing the target game"),
        )
    return FeaturePlan(
        feature="quick_start_supplies",
        version=1,
        settings={
            "enabled": effective.enabled,
            "preserve_normal_poke_balls": effective.preserve_normal_poke_balls,
            "master_balls": effective.master_balls,
            "rare_candies": effective.rare_candies,
        },
        operations=operations,
        requirements=requirements,
        metadata={"diagnostic_forced": diagnostic and not settings.enabled},
    )
