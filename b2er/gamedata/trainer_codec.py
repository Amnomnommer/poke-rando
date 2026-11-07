"""The one authoritative W2U trainer-record codec (TOML document <-> trdata/trpoke), shared by model
reading, source writing, planning and verification.

Source of truth: pinned W2U ``tools/mkdata/impl/trainer.py`` resolves every field with
``mkdata_util.resolve_label``: an integer passes through unchanged and a string is looked up in the
combined define tables (species, moves, items, ``enum/btl_gender.toml``, ``enum/btl_abil.toml``). So
both symbolic and numeric forms are legitimate input, and the pinned data uses both
(e.g. ``ABILITY = 0`` and ``ABILITY = "BATTLE_ABILITY_HIDDEN"``). ``AI``, ``CAN_HEAL`` and
``REWARD_MONEY`` are packed without resolution, so they must be integers. Ability and gender share one
byte: ``(ability << 4) | (gender & 0xF)``.

Unlike W2U this codec never masks or wraps: out-of-range numbers, unknown symbols and symbols from the
wrong table fail with the trainer ID, party slot, field and value.
"""

from __future__ import annotations

from typing import Callable, Mapping

from . import trainers as trn

# Pinned W2U tools/mkdata/enum/btl_abil.toml and btl_gender.toml (verified against the checkout at load).
ABILITY = {"BATTLE_ABILITY_RANDOM": 0, "BATTLE_ABILITY_PRIMARY": 1, "BATTLE_ABILITY_SECONDARY": 2, "BATTLE_ABILITY_HIDDEN": 3}
GENDER = {"GENDER_RANDOM": 0, "GENDER_MALE": 1, "GENDER_FEMALE": 2}
W2U_ENUMS = {"btl_abil": ("tools/mkdata/enum/btl_abil.toml", ABILITY), "btl_gender": ("tools/mkdata/enum/btl_gender.toml", GENDER)}
ABILITY_NAMES = {v: k for k, v in ABILITY.items()}
GENDER_NAMES = {v: k for k, v in GENDER.items()}

U8, U16, U32 = 0xFF, 0xFFFF, 0xFFFFFFFF
TRAINER_FIELDS = ("CLASS", "BATTLE_TYPE", "ITEMS", "AI", "CAN_HEAL", "REWARD_MONEY", "REWARD_ITEM", "PARTY")
MEMBER_FIELDS = ("DIFFICULTY_VALUE", "ABILITY", "GENDER", "LEVEL", "SPECIES", "FORM")


class TrainerFieldError(ValueError):
    """A trainer field that W2U's schema does not accept; the message names trainer, slot, field and value."""


def where(trainer_id, slot=None, field=None) -> str:
    text = f"trainer {trainer_id}"
    if slot is not None:
        text += f" party slot {slot}"
    if field is not None:
        text += f" field {field}"
    return text


def numeric(value, lo: int, hi: int, at: str) -> int:
    """An integer-only field (W2U packs it without label resolution, or no symbols exist for it)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TrainerFieldError(f"{at}: value {value!r} must be an integer {lo}..{hi}")
    if not lo <= value <= hi:
        raise TrainerFieldError(f"{at}: value {value} is outside {lo}..{hi}")
    return value


def symbolic(value, symbols: Mapping[str, int], lo: int, hi: int, at: str) -> int:
    """A field that accepts a symbol from one specific W2U table or a validated integer."""
    if isinstance(value, str):
        if value not in symbols:
            raise TrainerFieldError(f"{at}: unknown symbol {value!r}")
        resolved = symbols[value]
        if not lo <= resolved <= hi:
            raise TrainerFieldError(f"{at}: symbol {value!r} resolves to {resolved}, outside {lo}..{hi}")
        return resolved
    return numeric(value, lo, hi, at)


def ability(value, at: str = "ABILITY") -> int:
    return symbolic(value, ABILITY, 0, max(ABILITY.values()), at)


def gender(value, at: str = "GENDER") -> int:
    return symbolic(value, GENDER, 0, max(GENDER.values()), at)


def pack_ability_gender(ability_value: int, gender_value: int) -> int:
    return (ability_value << 4) | gender_value


def unpack_ability_gender(byte: int, at: str = "ABILITY/GENDER") -> tuple[int, int]:
    a, g = byte >> 4, byte & 0xF
    if a not in ABILITY_NAMES or g not in GENDER_NAMES:
        raise TrainerFieldError(f"{at}: packed byte 0x{byte:02x} is invalid (ability {a}, gender {g})")
    return a, g


class Labels:
    """Symbol resolution for species/moves/items: integers are range-checked, strings use one W2U table."""

    def __init__(self, enums: Mapping[str, Mapping[str, int]], species: Callable[[str], int] | None = None):
        self.enums = enums
        self.species_lookup = species

    def item(self, value, at: str) -> int:
        return symbolic(value, self.enums["items"], 0, U16, at)

    def move(self, value, at: str) -> int:
        return symbolic(value, self.enums["moves"], 0, U16, at)

    def species(self, value, at: str) -> int:
        if isinstance(value, str) and self.species_lookup is not None:
            try:
                return numeric(self.species_lookup(value), 0, U16, at)
            except TrainerFieldError:
                raise
            except Exception:
                raise TrainerFieldError(f"{at}: unknown symbol {value!r}") from None
        return symbolic(value, self.enums["species"], 0, U16, at)


def _get(mapping: Mapping, key: str, at: str):
    if not isinstance(mapping, Mapping):
        raise TrainerFieldError(f"{at}: expected a table, got {type(mapping).__name__}")
    if key not in mapping:
        raise TrainerFieldError(f"{at} field {key}: missing")
    return mapping[key]


def trainer_header(trainer_id, document: Mapping) -> dict:
    """Normalized trainer-level integers used by model reading and verification."""
    at = lambda f: where(trainer_id, field=f)  # noqa: E731
    return {"CLASS": numeric(_get(document, "CLASS", where(trainer_id)), 0, U8, at("CLASS")),
            "BATTLE_TYPE": numeric(_get(document, "BATTLE_TYPE", where(trainer_id)), 0, trn.BATTLE_ROTATION, at("BATTLE_TYPE"))}


def member_core(trainer_id, slot: int, member: Mapping) -> dict:
    """Normalized LEVEL/FORM (and validated ability/gender) of one party slot, for model reading."""
    at = lambda f: where(trainer_id, slot, f)  # noqa: E731
    base = where(trainer_id, slot)
    return {"LEVEL": numeric(_get(member, "LEVEL", base), 1, 100, at("LEVEL")),
            "FORM": numeric(_get(member, "FORM", base), 0, U16, at("FORM")),
            "ABILITY": ability(_get(member, "ABILITY", base), at("ABILITY")),
            "GENDER": gender(_get(member, "GENDER", base), at("GENDER")),
            "DIFFICULTY_VALUE": numeric(_get(member, "DIFFICULTY_VALUE", base), 0, U8, at("DIFFICULTY_VALUE"))}


def encode(trainer_id, document: Mapping, labels: Labels) -> tuple[trn.TrainerData, list[trn.PartyMember]]:
    """Resolve one W2U trainer document to typed records exactly as W2U's serializer lays them out.

    The document is only read, never modified."""
    base = where(trainer_id)
    party = _get(document, "PARTY", base) if "PARTY" in document else []
    if not isinstance(party, list) or len(party) > 6:
        raise TrainerFieldError(f"{base} field PARTY: expected a list of at most 6 members")
    has_moves = any(isinstance(m, Mapping) and "MOVES" in m for m in party)
    has_item = any(isinstance(m, Mapping) and "HELD_ITEM" in m for m in party)
    fmt = (trn.FORMAT_HELD_ITEM if has_item else 0) | (trn.FORMAT_MOVES if has_moves else 0)
    header = trainer_header(trainer_id, document)
    items = _get(document, "ITEMS", base)
    if not isinstance(items, list) or len(items) != 4:
        raise TrainerFieldError(f"{where(trainer_id, field='ITEMS')}: value {items!r} must list exactly 4 items")
    at = lambda f: where(trainer_id, field=f)  # noqa: E731
    data = trn.TrainerData(
        fmt, header["CLASS"], header["BATTLE_TYPE"], len(party),
        tuple(labels.item(v, f"{at('ITEMS')}[{i}]") for i, v in enumerate(items)),
        numeric(_get(document, "AI", base), 0, U32, at("AI")),
        numeric(_get(document, "CAN_HEAL", base), 0, U8, at("CAN_HEAL")),
        numeric(_get(document, "REWARD_MONEY", base), 0, U8, at("REWARD_MONEY")),
        labels.item(_get(document, "REWARD_ITEM", base), at("REWARD_ITEM")))
    members = []
    for slot, member in enumerate(party):
        core = member_core(trainer_id, slot, member)
        mat = lambda f: where(trainer_id, slot, f)  # noqa: E731
        held = labels.item(member.get("HELD_ITEM", "ITEM_NONE"), mat("HELD_ITEM")) if has_item else None
        moves = None
        if has_moves:
            listed = member.get("MOVES", ["MOVE_NONE"] * 4)
            if not isinstance(listed, list) or len(listed) != 4:
                raise TrainerFieldError(f"{mat('MOVES')}: value {listed!r} must list exactly 4 moves")
            moves = tuple(labels.move(v, f"{mat('MOVES')}[{i}]") for i, v in enumerate(listed))
        members.append(trn.PartyMember(core["DIFFICULTY_VALUE"], pack_ability_gender(core["ABILITY"], core["GENDER"]),
                                       core["LEVEL"], labels.species(_get(member, "SPECIES", where(trainer_id, slot)), mat("SPECIES")),
                                       core["FORM"], held, moves))
    return data, members


def encode_bytes(trainer_id, document: Mapping, labels: Labels) -> tuple[bytes, bytes]:
    data, members = encode(trainer_id, document, labels)
    return trn.encode_trdata(data), trn.encode_trpoke(members)


def decode(trainer_id, trdata: bytes, trpoke: bytes) -> tuple[trn.TrainerData, list[trn.PartyMember]]:
    """Decode ROM records and validate the packed ability/gender of every slot."""
    data = trn.decode_trdata(trdata)
    members = trn.decode_trpoke(trpoke, data.format, data.party_count)
    for slot, member in enumerate(members):
        unpack_ability_gender(member.ability_gender, where(trainer_id, slot, "ABILITY/GENDER"))
    return data, members


def compare(trainer_id, rom: tuple[trn.TrainerData, list[trn.PartyMember]],
            plan: tuple[trn.TrainerData, list[trn.PartyMember]]) -> list[str]:
    """Every field-level difference between a decoded ROM record and the planned record (neither is modified)."""
    (got, got_party), (want, want_party) = rom, plan
    problems = [f"{where(trainer_id, field=f)}: ROM {getattr(got, f)!r} != plan {getattr(want, f)!r}"
                for f in got.__dataclass_fields__ if getattr(got, f) != getattr(want, f)]
    for slot, (a, b) in enumerate(zip(got_party, want_party)):
        problems += [f"{where(trainer_id, slot, f)}: ROM {getattr(a, f)!r} != plan {getattr(b, f)!r}"
                     for f in a.__dataclass_fields__ if getattr(a, f) != getattr(b, f)]
    if len(got_party) != len(want_party):
        problems.append(f"{where(trainer_id, field='PARTY')}: ROM has {len(got_party)} members, plan has {len(want_party)}")
    return problems


def check_w2u_enums(enums: Mapping[str, Mapping[str, int]]) -> list[str]:
    """The checkout's btl_abil/btl_gender tables must equal this codec's typed mapping."""
    return [f"{rel} differs from the codec mapping" for key, (rel, expected) in W2U_ENUMS.items()
            if dict(enums.get(key, {})) != expected]
