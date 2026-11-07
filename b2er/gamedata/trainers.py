"""Generation V trainer data (trdata) and trainer party (trpoke) binary codecs."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..nds import FormatError

TRDATA_SIZE = 20
FORMAT_MOVES = 0x1
FORMAT_HELD_ITEM = 0x2
BATTLE_SINGLE, BATTLE_DOUBLE, BATTLE_TRIPLE, BATTLE_ROTATION = 0, 1, 2, 3
AI_DOUBLE_BATTLE = 0x80


@dataclass
class TrainerData:
    format: int
    trainer_class: int
    battle_type: int
    party_count: int
    items: tuple[int, int, int, int]
    ai: int
    can_heal: int
    reward_money: int
    reward_item: int


@dataclass
class PartyMember:
    difficulty: int
    ability_gender: int
    level: int
    species: int
    form: int
    held_item: int | None = None
    moves: tuple[int, int, int, int] | None = None


def decode_trdata(data: bytes) -> TrainerData:
    if len(data) != TRDATA_SIZE:
        raise FormatError(f"trdata is {len(data)} bytes, expected {TRDATA_SIZE}")
    fmt, cls, battle, count = struct.unpack_from("<4B", data, 0)
    items = struct.unpack_from("<4H", data, 4)
    ai, = struct.unpack_from("<I", data, 12)
    heal, money, reward = struct.unpack_from("<BBH", data, 16)
    if fmt & ~0x3:
        raise FormatError(f"unknown trdata format bits 0x{fmt:x}")
    if battle > BATTLE_ROTATION:
        raise FormatError(f"unknown battle type {battle}")
    if not 0 <= count <= 6:
        raise FormatError(f"party count {count} is not 0..6")
    return TrainerData(fmt, cls, battle, count, items, ai, heal, money, reward)


def encode_trdata(t: TrainerData) -> bytes:
    return struct.pack("<4B4HIBBH", t.format, t.trainer_class, t.battle_type, t.party_count, *t.items, t.ai,
                       t.can_heal, t.reward_money, t.reward_item)


def member_size(fmt: int) -> int:
    return 8 + (2 if fmt & FORMAT_HELD_ITEM else 0) + (8 if fmt & FORMAT_MOVES else 0)


def decode_trpoke(data: bytes, fmt: int, count: int) -> list[PartyMember]:
    size = member_size(fmt)
    if len(data) != size * count:
        raise FormatError(f"trpoke is {len(data)} bytes, expected {size * count} for {count} members of format {fmt}")
    members = []
    for i in range(count):
        pos = i * size
        difficulty, ab, level, species, form = struct.unpack_from("<BBHHH", data, pos)
        pos += 8
        held = moves = None
        if fmt & FORMAT_HELD_ITEM:
            held, = struct.unpack_from("<H", data, pos)
            pos += 2
        if fmt & FORMAT_MOVES:
            moves = struct.unpack_from("<4H", data, pos)
        if not 1 <= level <= 100:
            raise FormatError(f"party member {i} level {level} is not 1..100")
        members.append(PartyMember(difficulty, ab, level, species, form, held, moves))
    return members
