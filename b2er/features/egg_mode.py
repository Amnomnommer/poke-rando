"""Deterministic, adapter-neutral randomized Egg modes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from typing import Iterable

from .contracts import FeaturePlan, Requirement


class EggMode(str, Enum):
    OFF = "off"
    STARTER = "starter_egg"
    ROUTE = "route_encounter_egg"
    GIFTS = "gift_eggs"
    FULL = "full_egg"


@dataclass(frozen=True)
class EggCandidate:
    species: int
    form: int = 0
    hatchable: bool = True

    def __post_init__(self) -> None:
        if self.species < 1 or self.form < 0:
            raise ValueError("invalid Egg candidate identity")


def _pick(seed: str, stream: str, candidates: tuple[EggCandidate, ...]) -> EggCandidate:
    digest = hashlib.sha256(f"egg-mode\0{seed}\0{stream}".encode("utf-8")).digest()
    return candidates[int.from_bytes(digest[:8], "big") % len(candidates)]


def plan_egg_mode(
    mode: EggMode | str,
    *,
    seed: str,
    candidates: Iterable[EggCandidate],
    starter_slots: int = 3,
    route_sites: Iterable[str] = (),
    gift_sites: Iterable[str] = (),
    hatch_cycles: int = 1,
) -> FeaturePlan:
    selected_mode = EggMode(mode)
    if not seed:
        raise ValueError("Egg mode requires a non-empty seed")
    if hatch_cycles < 1:
        raise ValueError("hatch_cycles must be positive")
    pool = tuple(candidate for candidate in candidates if candidate.hatchable)
    if selected_mode is not EggMode.OFF and not pool:
        raise ValueError("Egg mode has no hatchable candidates")

    targets: list[tuple[str, str]] = []
    if selected_mode in {EggMode.STARTER, EggMode.FULL}:
        if starter_slots < 1:
            raise ValueError("starter_slots must be positive")
        targets.extend(("starter", str(index)) for index in range(starter_slots))
    if selected_mode in {EggMode.ROUTE, EggMode.FULL}:
        targets.extend(("route", str(site)) for site in route_sites)
    if selected_mode in {EggMode.GIFTS, EggMode.FULL}:
        targets.extend(("gift", str(site)) for site in gift_sites)
    if selected_mode is not EggMode.OFF and not targets:
        raise ValueError("selected Egg mode has no eligible target sites")

    operations = tuple(
        {
            "kind": "replace_with_egg",
            "site_kind": kind,
            "site": site,
            "species": picked.species,
            "form": picked.form,
            "hatch_cycles": hatch_cycles,
        }
        for kind, site in targets
        for picked in (_pick(seed, f"{kind}:{site}", pool),)
    )
    requirements = () if selected_mode is EggMode.OFF else (
        Requirement("egg_creation", "create a legal Egg with deterministic contents"),
        Requirement("egg_hatching", "apply the configured quick-hatch value safely"),
        Requirement("egg_persistence", "preserve Egg identity through save, party and PC storage"),
    )
    return FeaturePlan(
        feature="randomized_egg_mode",
        version=1,
        settings={"mode": selected_mode.value, "hatch_cycles": hatch_cycles},
        operations=operations,
        requirements=requirements,
        metadata={"candidate_count": len(pool)},
    )
