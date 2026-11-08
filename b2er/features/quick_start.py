"""Fast-start request and checkpoint contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .contracts import FeaturePlan, Requirement


@dataclass(frozen=True)
class StarterChoice:
    species: int
    form: int = 0

    def __post_init__(self) -> None:
        if self.species < 1 or self.form < 0:
            raise ValueError("invalid starter identity")


@dataclass(frozen=True)
class QuickStartSettings:
    enabled: bool = False
    player_name: str = ""
    rival_name: str = ""
    avatar: str = "default"
    starter_index: int = 0
    checkpoint: str = "first_pokemon_center"


def plan_quick_start(
    settings: QuickStartSettings,
    *,
    starters: Iterable[StarterChoice],
    max_name_length: int,
    legal_checkpoints: Iterable[str] = ("first_pokemon_center",),
) -> FeaturePlan:
    choices = tuple(starters)
    if len(choices) != 3:
        raise ValueError("quick start requires exactly three displayed starter choices")
    if max_name_length < 1:
        raise ValueError("max_name_length must be positive")
    if settings.starter_index not in range(3):
        raise ValueError("starter_index must select one of the three choices")
    if settings.enabled:
        if not settings.player_name or len(settings.player_name) > max_name_length:
            raise ValueError("player name is empty or too long")
        if settings.rival_name and len(settings.rival_name) > max_name_length:
            raise ValueError("rival name is too long")
        if settings.checkpoint not in set(legal_checkpoints):
            raise ValueError("checkpoint is not supported by this adapter")

    selected = choices[settings.starter_index]
    operations: tuple[dict[str, object], ...] = ()
    requirements: tuple[Requirement, ...] = ()
    if settings.enabled:
        operations = (
            {"kind": "set_player_identity", "name": settings.player_name, "avatar": settings.avatar},
            {"kind": "set_rival_name", "name": settings.rival_name},
            {"kind": "grant_starter", "species": selected.species, "form": selected.form},
            {"kind": "initialize_checkpoint", "checkpoint": settings.checkpoint},
        )
        requirements = (
            Requirement("save_initialization", "create a legal new-game state at the checkpoint"),
            Requirement("story_flags", "mark the rival fight and catching tutorial complete"),
            Requirement("checkpoint_inventory", "grant the same mandatory key items as normal progression"),
            Requirement("pokedex_initialization", "initialize Pokédex state without skipping required data"),
        )
    return FeaturePlan(
        feature="quick_start",
        version=1,
        settings={
            "enabled": settings.enabled,
            "player_name": settings.player_name,
            "rival_name": settings.rival_name,
            "avatar": settings.avatar,
            "starter_index": settings.starter_index,
            "checkpoint": settings.checkpoint,
        },
        operations=operations,
        requirements=requirements,
        metadata={
            "displayed_starters": [
                {"species": choice.species, "form": choice.form} for choice in choices
            ]
        },
    )
