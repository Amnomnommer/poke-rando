"""Generation-neutral normalized game model.

Randomization policy reads and changes only these objects. Adapters translate
between engine formats (NARCs, TOML sources, scripts) and this model, and keep
generation-specific details in ``ext`` fields so nothing important is
flattened away.

Species identity: :class:`SpeciesKey` is (National Dex number, form). Engine
internal IDs are an adapter concern (see :class:`SpeciesCodec`). Egg and Bad
Egg are reserved non-Pokédex identities, never National Dex numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True, order=True)
class SpeciesKey:
    national: int
    form: int = 0

    def __post_init__(self) -> None:
        if self.national < 1 or self.form < 0:
            raise ValueError(f"invalid species key {self.national}/{self.form}")

    def __str__(self) -> str:
        return f"#{self.national:04d}" + (f"-f{self.form}" if self.form else "")


class Reserved(str, Enum):
    """Engine identities that are not Pokémon species."""

    NONE = "none"
    EGG = "egg"
    BAD_EGG = "bad_egg"


class SpeciesCodecError(ValueError):
    pass


@dataclass(frozen=True)
class SpeciesCodec:
    """Bounds-checked, versioned mapping between normalized identities and engine internal IDs.

    ``national_max``: highest National Dex number the engine layout represents.
    ``reserved``: engine IDs of reserved identities. ``form_records``: internal
    ID -> SpeciesKey for alternate-form records. ``internal_bits``: width of the
    narrowest field that must hold the ID.
    """

    layout: str
    version: int
    national_max: int
    reserved: dict[Reserved, int]
    form_records: dict[int, SpeciesKey] = field(default_factory=dict)
    internal_bits: int = 16

    def __post_init__(self) -> None:
        used = set(self.reserved.values()) | set(self.form_records)
        if any(1 <= i <= self.national_max for i in used if i != self.reserved.get(Reserved.NONE)):
            raise SpeciesCodecError(f"{self.layout}: reserved or form IDs collide with National Dex IDs 1..{self.national_max}")
        if any(i >= 1 << self.internal_bits for i in used | {self.national_max}):
            raise SpeciesCodecError(f"{self.layout}: IDs exceed {self.internal_bits} bits")

    def encode(self, key: SpeciesKey | Reserved) -> int:
        if isinstance(key, Reserved):
            if key not in self.reserved:
                raise SpeciesCodecError(f"{self.layout} has no {key.value} identity")
            return self.reserved[key]
        if key.form == 0:
            if not 1 <= key.national <= self.national_max:
                raise SpeciesCodecError(f"{key} is outside {self.layout} (1..{self.national_max})")
            return key.national
        for internal, form_key in self.form_records.items():
            if form_key == key:
                return internal
        raise SpeciesCodecError(f"{key} has no form record in {self.layout}")

    def decode(self, internal: int) -> SpeciesKey | Reserved:
        if not 0 <= internal < 1 << self.internal_bits:
            raise SpeciesCodecError(f"internal ID {internal} exceeds {self.internal_bits} bits")
        for reserved, value in self.reserved.items():
            if value == internal:
                return reserved
        if 1 <= internal <= self.national_max:
            return SpeciesKey(internal)
        if internal in self.form_records:
            return self.form_records[internal]
        raise SpeciesCodecError(f"internal ID {internal} is not defined in {self.layout}")

    def describe(self) -> dict:
        return {"layout": self.layout, "version": self.version, "national_dex": f"1..{self.national_max}",
                "reserved": {k.value: v for k, v in self.reserved.items()}, "form_records": len(self.form_records),
                "internal_bits": self.internal_bits}


class EvolutionMethod(str, Enum):
    LEVEL = "level"
    LEVEL_CONDITIONAL = "level_conditional"  # stats, gender, time of day, random, party type, Ninjask/Shedinja
    FRIENDSHIP = "friendship"
    ITEM_USE = "item_use"
    ITEM_HOLD = "item_hold"
    TRADE = "trade"
    TRADE_ITEM = "trade_item"
    TRADE_SPECIES = "trade_species"
    MOVE_KNOWN = "move_known"
    LOCATION = "location"
    PARTY_SPECIES = "party_species"
    SPECIAL = "special"  # beauty, upside-down, overworld rain, other engine-unique methods


@dataclass
class Evolution:
    method: EvolutionMethod
    target: SpeciesKey
    level: int | None = None
    item: str | None = None
    move: str | None = None
    ext: dict = field(default_factory=dict)  # engine method label, raw parameter, time-of-day, gender...


@dataclass
class SpeciesData:
    key: SpeciesKey
    name: str
    base_stats: tuple[int, int, int, int, int, int]
    types: tuple[str, ...]
    capture_rate: int
    egg_groups: tuple[int, ...]
    machines: frozenset[int]
    learnset: list[tuple[int, str]]
    evolutions: list[Evolution]
    legendary: bool = False
    ext: dict = field(default_factory=dict)

    @property
    def bst(self) -> int:
        return sum(self.base_stats)


@dataclass
class Move:
    id: str
    power: int
    ext: dict = field(default_factory=dict)


@dataclass
class Slot:
    species: SpeciesKey | None
    min_level: int
    max_level: int
    rate: int | None = None
    ext: dict = field(default_factory=dict)


@dataclass
class EncounterTable:
    kind: str  # e.g. "grass", "grass_doubles", "grass_special", "surf", "surf_special", "fish", "fish_special"
    slots: list[Slot]


@dataclass
class EncounterArea:
    id: int
    seasons: list[list[EncounterTable]]  # one entry per season (1 or 4 in Generation V)
    ext: dict = field(default_factory=dict)

    def all_slots(self):
        for season in self.seasons:
            for table in season:
                yield from table.slots


class BattleFormat(str, Enum):
    SINGLE = "single"
    DOUBLE = "double"
    TRIPLE = "triple"
    ROTATION = "rotation"


class TrainerRole(str, Enum):
    ORDINARY = "ordinary"
    RIVAL = "rival"
    GYM_LEADER = "gym_leader"
    ELITE_FOUR = "elite_four"
    CHAMPION = "champion"
    BOSS = "boss"  # villain team leaders and admins, other story-defining bosses

    @property
    def major(self) -> bool:
        return self is not TrainerRole.ORDINARY


@dataclass
class PartyMember:
    species: SpeciesKey
    level: int
    moves: list[str] | None = None
    ext: dict = field(default_factory=dict)


@dataclass
class Trainer:
    id: int
    name: str
    trainer_class: str
    role: TrainerRole
    battle_format: BattleFormat
    party: list[PartyMember]
    ext: dict = field(default_factory=dict)


@dataclass
class PokemonSite:
    """A starter, gift, or static encounter source."""

    id: str
    kind: str  # "starter", "gift", "static"
    species: SpeciesKey
    level: int
    ext: dict = field(default_factory=dict)


@dataclass
class ItemLocation:
    id: str
    kind: str  # "field", "hidden", "gift", "shop"
    item: str
    item_class: str  # "ordinary", "tm", "key", "hm", "unknown"
    ext: dict = field(default_factory=dict)


@dataclass
class GameModel:
    game_id: str
    codec: SpeciesCodec
    species: dict[SpeciesKey, SpeciesData]
    moves: dict[str, Move]
    encounters: list[EncounterArea]
    trainers: list[Trainer]
    starters: list[PokemonSite]
    gifts: list[PokemonSite] = field(default_factory=list)
    statics: list[PokemonSite] = field(default_factory=list)
    item_locations: list[ItemLocation] = field(default_factory=list)
    obtainable_evolution_items: frozenset[str] = frozenset()
    feasible_evolution_methods: frozenset[EvolutionMethod] = frozenset()
    ext: dict = field(default_factory=dict)

    def sp(self, key: SpeciesKey) -> SpeciesData:
        """Species data for a key; alternate forms without their own record use the base form's data."""
        found = self.species.get(key)
        if found is None:
            found = self.species.get(SpeciesKey(key.national))
        if found is None:
            raise KeyError(f"{key} is not in {self.game_id}")
        return found

    def damaging_moves_by(self, key: SpeciesKey, level: int) -> list[str]:
        data = self.sp(key)
        return [m for lv, m in data.learnset if lv <= level and self.moves.get(m, Move(m, 0)).power > 0]

    def default_moves(self, key: SpeciesKey, level: int) -> list[str]:
        moves: list[str] = []
        for lv, move in self.sp(key).learnset:
            if lv <= level and move not in moves:
                moves.append(move)
        return moves[-4:]
