"""Generation V wild-encounter records: strict decode/encode and the Route 19 diagnostic fixture.

One season container is 232 bytes: 8 rate bytes, then 56 slots of
``u16 (form << 11 | species)``, ``u8 min level``, ``u8 max level`` in table order
grass singles (12), grass doubles (12), grass special (12), surf singles (5),
surf special (5), fish singles (5), fish special (5). A record holds 1 or 4
season containers (232 or 928 bytes).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field, replace

from ..nds import FormatError

RATE_KEYS = ("GRASS_SINGLES", "GRASS_DOUBLES", "GRASS_RARE", "SURF_SINGLES", "SURF_RARE", "FISH_SINGLES", "FISH_RARE", "UNKNOWN")
TABLES = (
    ("GRASS", "SINGLES", 12),
    ("GRASS", "DOUBLES", 12),
    ("GRASS", "SPECIAL", 12),
    ("SURF", "SINGLES", 5),
    ("SURF", "SPECIAL", 5),
    ("FISH", "SINGLES", 5),
    ("FISH", "SPECIAL", 5),
)
SLOTS_PER_CONTAINER = sum(count for _, _, count in TABLES)
CONTAINER_SIZE = 8 + 4 * SLOTS_PER_CONTAINER  # 232
SEASONS = 4
MAX_SPECIES = 0x7FF
MAX_FORM = 0x1F
GRASS_RATES = (20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1)


@dataclass(frozen=True)
class Slot:
    species: int
    form: int
    min_level: int
    max_level: int

    def validate(self, where: str) -> None:
        if not 0 <= self.species <= MAX_SPECIES:
            raise FormatError(f"{where}: species {self.species} does not fit 11 bits")
        if not 0 <= self.form <= MAX_FORM:
            raise FormatError(f"{where}: form {self.form} does not fit 5 bits")
        if self.species == 0:
            if self.min_level or self.max_level:
                raise FormatError(f"{where}: empty slot has levels")
            return
        if not 1 <= self.min_level <= self.max_level <= 100:
            raise FormatError(f"{where}: levels {self.min_level}..{self.max_level} are not within 1..100 and ordered")


@dataclass
class Container:
    rates: tuple[int, ...]
    tables: dict[tuple[str, str], list[Slot]] = field(default_factory=dict)

    def validate(self, where: str) -> None:
        if len(self.rates) != 8 or not all(0 <= r <= 255 for r in self.rates):
            raise FormatError(f"{where}: needs 8 rate bytes")
        for category, kind, count in TABLES:
            slots = self.tables.get((category, kind))
            if slots is None or len(slots) != count:
                raise FormatError(f"{where}: table {category}.{kind} must have exactly {count} slots")
            for index, slot in enumerate(slots):
                slot.validate(f"{where} {category}.{kind}[{index}]")


def decode_container(data: bytes, where: str = "container") -> Container:
    if len(data) != CONTAINER_SIZE:
        raise FormatError(f"{where}: container is {len(data)} bytes, expected {CONTAINER_SIZE}")
    rates = tuple(data[:8])
    tables = {}
    pos = 8
    for category, kind, count in TABLES:
        slots = []
        for _ in range(count):
            packed, low, high = struct.unpack_from("<HBB", data, pos)
            slots.append(Slot(packed & MAX_SPECIES, packed >> 11, low, high))
            pos += 4
        tables[(category, kind)] = slots
    container = Container(rates, tables)
    container.validate(where)
    return container


def encode_container(container: Container, where: str = "container") -> bytes:
    container.validate(where)
    out = bytearray(container.rates)
    for category, kind, _count in TABLES:
        for slot in container.tables[(category, kind)]:
            out += struct.pack("<HBB", (slot.form << 11) | slot.species, slot.min_level, slot.max_level)
    return bytes(out)


def decode_record(data: bytes, where: str = "record") -> list[Container]:
    if len(data) not in (CONTAINER_SIZE, CONTAINER_SIZE * SEASONS):
        raise FormatError(f"{where}: record is {len(data)} bytes, expected {CONTAINER_SIZE} or {CONTAINER_SIZE * SEASONS}")
    return [decode_container(data[i : i + CONTAINER_SIZE], f"{where} season {i // CONTAINER_SIZE}")
            for i in range(0, len(data), CONTAINER_SIZE)]


def encode_record(containers: list[Container], where: str = "record") -> bytes:
    if len(containers) not in (1, SEASONS):
        raise FormatError(f"{where}: a record has 1 or 4 containers")
    return b"".join(encode_container(c, f"{where} season {i}") for i, c in enumerate(containers))


# --- Route 19 diagnostic fixture ---------------------------------------------

PATRAT, CARBINK, CHESPIN = 504, 703, 650
# Slot assignment over the fixed grass rates: Patrat 20+10+5, Carbink 20+10+5, Chespin 10+10+4+4+1+1.
FIXTURE_ASSIGNMENT = (
    (PATRAT, 5), (CARBINK, 6), (PATRAT, 5), (CARBINK, 6), (CHESPIN, 7), (CHESPIN, 7),
    (PATRAT, 5), (CARBINK, 6), (CHESPIN, 7), (CHESPIN, 7), (CHESPIN, 7), (CHESPIN, 7),
)
FIXTURE_WEIGHTS = {PATRAT: 35, CARBINK: 35, CHESPIN: 30}


def fixture_grass_slots() -> list[Slot]:
    return [Slot(species, 0, level, level) for species, level in FIXTURE_ASSIGNMENT]


def weights(slots: list[Slot]) -> dict[int, int]:
    totals: dict[int, int] = {}
    for slot, rate in zip(slots, GRASS_RATES):
        totals[slot.species] = totals.get(slot.species, 0) + rate
    return totals


def apply_route19_fixture(containers: list[Container]) -> list[Container]:
    if len(containers) != SEASONS:
        raise FormatError("the Route 19 fixture requires a four-season record")
    result = []
    for container in containers:
        tables = dict(container.tables)
        tables[("GRASS", "SINGLES")] = fixture_grass_slots()
        result.append(Container(container.rates, tables))
    return result


def verify_route19_fixture(data: bytes) -> list[str]:
    """Return problems with a finished-ROM Route 19 record; empty means the fixture is exact."""
    problems = []
    if len(data) != CONTAINER_SIZE * SEASONS:
        return [f"Route 19 record is {len(data)} bytes, expected {CONTAINER_SIZE * SEASONS}"]
    try:
        containers = decode_record(data, "Route 19")
    except FormatError as error:
        return [str(error)]
    expected = fixture_grass_slots()
    for season, container in enumerate(containers):
        slots = container.tables[("GRASS", "SINGLES")]
        for index, (slot, want) in enumerate(zip(slots, expected)):
            if slot != want:
                offset = season * CONTAINER_SIZE + 8 + index * 4
                problems.append(f"season {season} grass slot {index} at byte 0x{offset:x}: {slot} != {want}")
        if weights(slots) != FIXTURE_WEIGHTS:
            problems.append(f"season {season} weights {weights(slots)} != {FIXTURE_WEIGHTS}")
    return problems
