"""Pure assignment rules for optional battle gimmicks.

Team randomization happens first.  This module assigns battle roles afterward;
it never creates type-themed teams.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
from typing import Iterable

from .contracts import FeaturePlan, Requirement


class Gimmick(str, Enum):
    MEGA = "mega"
    Z_MOVE = "z_move"
    DYNAMAX = "dynamax"
    TERA = "terastallize"


class PlayerGimmickMode(str, Enum):
    OFF = "off"
    SEEDED = "seeded"
    MEGA = Gimmick.MEGA.value
    Z_MOVE = Gimmick.Z_MOVE.value
    DYNAMAX = Gimmick.DYNAMAX.value
    TERA = Gimmick.TERA.value


@dataclass(frozen=True)
class BattleMember:
    slot: int
    species: int
    is_ace: bool = False
    mega_eligible: bool = False
    signature_z_move: str | None = None

    def __post_init__(self) -> None:
        if self.slot < 0 or self.species < 1:
            raise ValueError("invalid battle member")


def _coin(seed: str, stream: str) -> bool:
    return bool(hashlib.sha256(f"gimmick\0{seed}\0{stream}".encode()).digest()[0] & 1)


def _choice(seed: str, stream: str, values: tuple[Gimmick, ...]) -> Gimmick:
    digest = hashlib.sha256(f"gimmick\0{seed}\0{stream}".encode()).digest()
    return values[int.from_bytes(digest[:8], "big") % len(values)]


def plan_player_gimmick(mode: PlayerGimmickMode | str, *, seed: str) -> FeaturePlan:
    selected_mode = PlayerGimmickMode(mode)
    chosen: Gimmick | None
    if selected_mode is PlayerGimmickMode.OFF:
        chosen = None
    elif selected_mode is PlayerGimmickMode.SEEDED:
        if not seed:
            raise ValueError("seeded gimmick selection needs a seed")
        chosen = _choice(seed, "player-run", tuple(Gimmick))
    else:
        chosen = Gimmick(selected_mode.value)
    return FeaturePlan(
        feature="player_run_gimmick",
        version=1,
        settings={"mode": selected_mode.value},
        operations=() if chosen is None else ({"kind": "allow_run_gimmick", "gimmick": chosen.value},),
        requirements=() if chosen is None else (
            Requirement(f"battle_{chosen.value}", "implement the selected gimmick in the target engine"),
            Requirement("run_gimmick_lock", "prevent the player from using a different gimmick in this run"),
        ),
        metadata={"selected": None if chosen is None else chosen.value},
    )


def _team(members: Iterable[BattleMember]) -> tuple[BattleMember, ...]:
    team = tuple(members)
    if not team or len({member.slot for member in team}) != len(team):
        raise ValueError("battle team must contain unique slots")
    if sum(member.is_ace for member in team) != 1:
        raise ValueError("battle team must have exactly one ace")
    return team


def plan_gym_gimmick(members: Iterable[BattleMember], *, seed: str, boss_id: str) -> FeaturePlan:
    team = _team(members)
    ace = next(member for member in team if member.is_ace)
    if ace.mega_eligible:
        assignment = {"slot": ace.slot, "gimmick": Gimmick.MEGA.value}
    elif ace.signature_z_move:
        assignment = {
            "slot": ace.slot,
            "gimmick": Gimmick.Z_MOVE.value,
            "move": ace.signature_z_move,
        }
    elif _coin(seed, f"gym:{boss_id}"):
        assignment = {"slot": team[0].slot, "gimmick": Gimmick.DYNAMAX.value}
    else:
        assignment = {"slot": ace.slot, "gimmick": Gimmick.TERA.value}
    return FeaturePlan(
        feature="gym_gimmick",
        version=1,
        settings={"enabled": True, "boss_id": boss_id},
        operations=({"kind": "assign_boss_gimmick", **assignment},),
        requirements=(
            Requirement(f"battle_{assignment['gimmick']}", "implement the assigned boss gimmick"),
            Requirement("boss_gimmick_ai", "make AI understand when and how to activate it"),
        ),
        metadata={"hierarchy": ["ace_mega", "ace_signature_z", "seeded_dynamax_lead_or_tera_ace"]},
    )


def plan_champion_gimmicks(members: Iterable[BattleMember], *, steel_type: str = "steel") -> FeaturePlan:
    team = _team(members)
    if len(team) < 4:
        raise ValueError("champion gimmick sequence requires at least four team members")
    lead = team[0]
    ace = next(member for member in team if member.is_ace)
    if lead.slot == ace.slot:
        raise ValueError("champion lead and final ace must be distinct")
    mega = next((member for member in team if member.mega_eligible and member.slot not in {lead.slot, ace.slot}), None)
    if mega is None:
        raise ValueError("champion team needs a distinct Mega-eligible member")
    reserved = {lead.slot, mega.slot, ace.slot}
    z_user = next((member for member in team if member.slot not in reserved and member.signature_z_move), None)
    if z_user is None:
        z_user = next((member for member in team if member.slot not in reserved), None)
    if z_user is None:
        raise ValueError("champion team needs a fourth distinct member for its Z-Move")
    operations = (
        {"kind": "assign_boss_gimmick", "phase": 1, "slot": lead.slot, "gimmick": Gimmick.DYNAMAX.value},
        {"kind": "assign_boss_gimmick", "phase": 2, "slot": mega.slot, "gimmick": Gimmick.MEGA.value},
        {
            "kind": "assign_boss_gimmick",
            "phase": 3,
            "slot": z_user.slot,
            "gimmick": Gimmick.Z_MOVE.value,
            "move": z_user.signature_z_move,
        },
        {
            "kind": "assign_boss_gimmick",
            "phase": 4,
            "slot": ace.slot,
            "gimmick": Gimmick.TERA.value,
            "tera_type": steel_type,
        },
    )
    return FeaturePlan(
        feature="champion_gimmick_sequence",
        version=1,
        settings={"enabled": True, "battle_format": "doubles"},
        operations=operations,
        requirements=tuple(
            Requirement(f"battle_{gimmick.value}", f"implement champion {gimmick.value}")
            for gimmick in Gimmick
        ) + (
            Requirement("multi_gimmick_boss", "allow the champion's four scripted phases without unlocking them for the player"),
            Requirement("boss_gimmick_ai", "make AI activate each assigned phase"),
        ),
        metadata={"distinct_slots": True, "final_tera_type": steel_type},
    )
