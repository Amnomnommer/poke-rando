"""Translate W2U's Generation V source data (in a workroot) to and from the normalized model."""

from __future__ import annotations

import copy

from ...model import (BattleFormat, EncounterArea, EncounterTable, Evolution, EvolutionMethod, GameModel, Move,
                      PartyMember, SpeciesData, SpeciesKey, Slot, Trainer, TrainerRole)
from ...gamedata import trainer_codec
from ...w2u_data import Evolution as W2UEvolution, W2UData
from .common import (FEASIBLE, LEVEL_PARAMETER_METHODS, METHOD_TO_MODEL, TABLE_KINDS, TABLE_RATES,
                     WHITE2_EVOLUTION_ITEMS, w2u_codec)

FORMATS = {0: BattleFormat.SINGLE, 1: BattleFormat.DOUBLE, 2: BattleFormat.TRIPLE, 3: BattleFormat.ROTATION}
FORMAT_CODES = {v: k for k, v in FORMATS.items()}
AI_DOUBLE_BATTLE = 0x80
ROLE_BY_CLASS = {"Leader": TrainerRole.GYM_LEADER, "Elite Four": TrainerRole.ELITE_FOUR, "Champion": TrainerRole.CHAMPION}
ROLE_BY_NAME = {"Rival": TrainerRole.RIVAL, "N": TrainerRole.BOSS, "Ghetsis": TrainerRole.BOSS, "Colress": TrainerRole.BOSS,
                "Zinzolin": TrainerRole.BOSS, "Shadow": TrainerRole.BOSS}
UNDISCOVERED = 15


def _label_of(data: W2UData, kind: str, value) -> str | None:
    if isinstance(value, str):
        return value
    for label, number in data.enums[kind].items():
        if number == value:
            return label
    return None


def species_key(data: W2UData, label: str, form: int = 0) -> SpeciesKey | None:
    internal = data.species_id(label)
    if internal == 0:
        return None
    decoded = data.codec.decode(internal)
    if not isinstance(decoded, SpeciesKey):
        raise ValueError(f"{label} is a reserved identity, not a species")
    return SpeciesKey(decoded.national, form)


def to_model(data: W2UData, game_id: str) -> GameModel:
    data.codec = w2u_codec(data.enums["species"])
    targets = set()
    for evos in data.evolutions.values():
        for e in evos:
            targets.add(data.enums["species"].get(e.target))
    species: dict[SpeciesKey, SpeciesData] = {}
    for internal, p in data.personal.items():
        if not data.model.is_ordinary(internal):
            continue
        key = SpeciesKey(internal)
        evolutions = []
        for e in data.evolutions.get(internal, []):
            target = data.enums["species"].get(e.target)
            if target is None or not data.model.is_ordinary(target):
                continue
            method = METHOD_TO_MODEL.get(e.method, EvolutionMethod.SPECIAL)
            evo = Evolution(method, SpeciesKey(target), ext={"native": e.method, "parameter": e.parameter})
            if e.method in LEVEL_PARAMETER_METHODS and isinstance(e.parameter, int):
                evo.level = e.parameter
            if method in (EvolutionMethod.ITEM_USE, EvolutionMethod.ITEM_HOLD, EvolutionMethod.TRADE_ITEM):
                evo.item = _label_of(data, "items", e.parameter)
            if method is EvolutionMethod.MOVE_KNOWN:
                evo.move = _label_of(data, "moves", e.parameter)
            if e.method.endswith("_NIGHT"):
                evo.ext["time"] = "night"
            elif e.method.endswith("_DAY"):
                evo.ext["time"] = "day"
            evolutions.append(evo)
        machines = frozenset(b for b in range(128) if p.can_learn_machine(b))
        legendary = UNDISCOVERED in p.egg_groups and p.capture_rate <= 45 and not evolutions and internal not in targets
        species[key] = SpeciesData(key, p.label, p.stats, p.types, p.capture_rate, p.egg_groups, machines,
                                   list(data.learnsets.get(internal, [])), evolutions, legendary,
                                   {"internal": internal, "gender_ratio": p.gender_ratio, "form_count": p.form_count})
    moves = {label: Move(label, power) for label, power in data.move_power.items()}
    encounters = []
    for index in sorted(data.encounters):
        seasons = []
        document = data.encounters[index]
        for container_key in sorted(document, key=lambda k: int(k.rsplit("_", 1)[1])):
            container = document[container_key]
            tables = []
            for (category, kind), name in TABLE_KINDS.items():
                raw = container.get(category, {}).get(kind, {})
                slots = []
                for slot_key, rate in zip(sorted(raw, key=lambda k: int(k.split()[-1])), TABLE_RATES[name]):
                    s = raw[slot_key]
                    key = species_key(data, s["Species"], int(s["Form"]))
                    slots.append(Slot(key, int(s["Minimum Level"]), int(s["Maximum Level"]), rate, {"slot_key": slot_key}))
                tables.append(EncounterTable(name, slots))
            seasons.append(tables)
        encounters.append(EncounterArea(index, seasons, {"source": f"data/encounters/{index}.toml"}))
    trainers = []
    for index in sorted(data.trainers):
        document = data.trainers[index]
        header = trainer_codec.trainer_header(index, document)
        class_name = data.trainer_classes.get(header["CLASS"], "")
        name = data.trainer_names.get(index, "")
        role = ROLE_BY_CLASS.get(class_name) or ROLE_BY_NAME.get(name, TrainerRole.ORDINARY)
        party = []
        for slot, m in enumerate(document.get("PARTY", [])):
            core = trainer_codec.member_core(index, slot, m)
            key = species_key(data, m["SPECIES"], core["FORM"])
            if key is None:
                continue
            moves_list = [mv for mv in m["MOVES"] if mv != "MOVE_NONE"] if "MOVES" in m else None
            ext = {k: v for k, v in m.items() if k not in ("SPECIES", "LEVEL", "FORM", "MOVES")}
            party.append(PartyMember(key, core["LEVEL"], moves_list, ext))
        ext = {k: v for k, v in document.items() if k != "PARTY"}
        ext["uses_moves"] = any("MOVES" in m for m in document.get("PARTY", []))
        trainers.append(Trainer(index, name, class_name, role, FORMATS[header["BATTLE_TYPE"]], party, ext))
    return GameModel(game_id, data.codec, species, moves, encounters, trainers, starters=[],
                     obtainable_evolution_items=WHITE2_EVOLUTION_ITEMS, feasible_evolution_methods=FEASIBLE,
                     ext={"label_mismatches": data.label_mismatches})


def _label(data: W2UData, key: SpeciesKey | None) -> str:
    if key is None:
        return "SPECIES_NONE"
    return data.species_label(data.codec.encode(SpeciesKey(key.national)))


def _evolution_native(data: W2UData, evo: Evolution) -> W2UEvolution:
    target = _label(data, evo.target)
    if evo.ext.get("native") and not evo.ext.get("changed"):
        return W2UEvolution(evo.ext["native"], evo.ext["parameter"], target)
    if evo.method is EvolutionMethod.LEVEL:
        return W2UEvolution("METHOD_LEVELUP", int(evo.level), target)
    if evo.method is EvolutionMethod.ITEM_HOLD:
        method = "METHOD_ITEM_HELD_NIGHT" if evo.ext.get("time") == "night" else "METHOD_ITEM_HELD_DAY"
        return W2UEvolution(method, data.item_id(evo.item), target)
    if evo.method is EvolutionMethod.ITEM_USE:
        return W2UEvolution("METHOD_ITEM_USE", data.item_id(evo.item), target)
    raise ValueError(f"cannot express {evo.method.value} for W2U")


def write_model(data: W2UData, original: GameModel, model: GameModel) -> dict:
    """Write every changed species evolution list, encounter area, and trainer back as W2U TOML."""
    summary = {"evolutions": 0, "encounters": 0, "trainers": 0}
    for key, species in model.species.items():
        if species.evolutions != original.species[key].evolutions:
            data.write_evolutions(species.ext["internal"], [_evolution_native(data, e) for e in species.evolutions])
            summary["evolutions"] += 1
    originals = {a.id: a for a in original.encounters}
    for area in model.encounters:
        if area.seasons == originals[area.id].seasons:
            continue
        document = copy.deepcopy(data.encounters[area.id])
        for container_key, season in zip(sorted(document, key=lambda k: int(k.rsplit("_", 1)[1])), area.seasons):
            container = document[container_key]
            for ((category, kind), _name), table in zip(TABLE_KINDS.items(), season):
                raw = container[category][kind]
                for slot in table.slots:
                    raw[slot.ext["slot_key"]] = {"Species": _label(data, slot.species),
                                                  "Form": slot.species.form if slot.species else 0,
                                                  "Minimum Level": slot.min_level if slot.species else 0,
                                                  "Maximum Level": slot.max_level if slot.species else 0}
        data.write_encounter(area.id, document)
        summary["encounters"] += 1
    trainer_originals = {t.id: t for t in original.trainers}
    for trainer in model.trainers:
        before = trainer_originals[trainer.id]
        if trainer == before:
            continue
        document = {k: v for k, v in trainer.ext.items() if k != "uses_moves"}
        document["BATTLE_TYPE"] = FORMAT_CODES[trainer.battle_format]
        if trainer.battle_format is BattleFormat.DOUBLE and before.battle_format is BattleFormat.SINGLE:
            document["AI"] = int(document["AI"]) | AI_DOUBLE_BATTLE
        template = before.party[0].ext if before.party else {"GENDER": 0, "ABILITY": 0, "DIFFICULTY_VALUE": 0}
        party = []
        for member in trainer.party:
            entry = {"SPECIES": _label(data, member.species), "LEVEL": member.level, "FORM": member.species.form}
            entry.update(member.ext or template)
            if trainer.ext.get("uses_moves"):
                moves = list(member.moves or model.default_moves(member.species, member.level))
                entry["MOVES"] = (moves + ["MOVE_NONE"] * 4)[:4]
            party.append(entry)
        document["PARTY"] = party
        data.write_trainer(trainer.id, document)
        summary["trainers"] += 1
    return summary
