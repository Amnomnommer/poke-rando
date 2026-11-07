"""Adapter over W2U's editable source data inside a disposable workroot copy.

It reads the TOML sources W2U compiles (enums, personal data, learnsets,
evolutions, moves, encounters, trainers, trainer text) and writes the
randomized encounters, trainers, and evolutions back as TOML for W2U's own
build to serialize. Nothing here is copied into this repository; the adapter
only knows file locations and field names.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import tomlw
from .species import SpeciesModel

ENUMS = {
    "species": "tools/mkdata/enum/species.toml",
    "moves": "tools/mkdata/enum/moves.toml",
    "items": "tools/mkdata/enum/items.toml",
    "evolution_methods": "tools/mkdata/enum/evolution_methods.toml",
    "types": "tools/mkdata/enum/types.toml",
}
PML_MESON = "data/pml/meson.build"
MOVES_MESON = "data/pml/moves/meson.build"
ENCOUNTERS_DIR = "data/encounters"
TRAINERS_DIR = "data/trainers"
TRAINER_NAMES = "data/text/system/382.toml"
TRAINER_CLASSES = "data/text/system/383.toml"
STAT_KEYS = ("Base HP", "Base Attack", "Base Defense", "Base Speed", "Base Special Attack", "Base Special Defense")


class DataError(ValueError):
    pass


def _load(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise DataError(f"cannot load {path}: {error}") from None


def _single(document: dict, path: Path):
    if len(document) != 1:
        raise DataError(f"{path} must contain exactly one top-level table")
    return next(iter(document.items()))


def _meson_list(text: str, suffix: str) -> list[str]:
    return re.findall(r"'([^']+" + re.escape(suffix) + r")'", text)


def _messages(path: Path) -> dict[int, str]:
    doc = _load(path)
    try:
        entries = doc["msg"]["section_0"]["entries"]
    except KeyError:
        raise DataError(f"{path} is not a message file") from None
    return {int(k): "".join(v.get("text", [])).replace("$", "") for k, v in entries.items()}


@dataclass
class Personal:
    species: int
    label: str
    stats: tuple[int, ...]
    types: tuple[str, str]
    capture_rate: int
    egg_groups: tuple[int, int]
    gender_ratio: int
    tm_words: tuple[int, int, int, int]
    form_count: int

    @property
    def bst(self) -> int:
        return sum(self.stats)

    def can_learn_machine(self, bit: int) -> bool:
        return bool((self.tm_words[bit // 32] >> (bit % 32)) & 1)


@dataclass
class Evolution:
    method: str
    parameter: int | str
    target: str


@dataclass
class W2UData:
    root: Path
    enums: dict[str, dict[str, int]]
    model: SpeciesModel
    personal: dict[int, Personal]
    learnsets: dict[int, list[tuple[int, str]]]
    evolutions: dict[int, list[Evolution]]
    evolution_paths: dict[int, Path]
    move_power: dict[str, int]
    encounters: dict[int, dict]
    trainers: dict[int, dict]
    trainer_names: dict[int, str]
    trainer_classes: dict[int, str]
    item_names: dict[int, str] = field(default_factory=dict)
    evolution_labels: dict[int, str] = field(default_factory=dict)
    label_mismatches: list[tuple[int, str, str]] = field(default_factory=list)

    # --- loading ------------------------------------------------------------
    @classmethod
    def load(cls, root: Path) -> "W2UData":
        root = Path(root)
        enums = {}
        for key, rel in ENUMS.items():
            doc = _load(root / rel)
            if "DEFINE" not in doc:
                raise DataError(f"{rel} has no DEFINE table")
            enums[key] = {str(k): int(v) for k, v in doc["DEFINE"].items()}
        model = SpeciesModel.from_enum(enums["species"])
        species_by_label = enums["species"]

        pml = (root / PML_MESON).read_text(encoding="utf-8")
        personal_files = _meson_list(pml, "/personal.toml")
        learnset_files = _meson_list(pml, "/learnset.toml")
        evolution_files = _meson_list(pml, "/evolutions.toml")
        if not (len(personal_files) == len(learnset_files) == len(evolution_files)):
            raise DataError("personal, learnset, and evolution source lists differ in length")

        personal: dict[int, Personal] = {}
        learnsets: dict[int, list[tuple[int, str]]] = {}
        evolutions: dict[int, list[Evolution]] = {}
        evolution_paths: dict[int, Path] = {}
        file_labels: dict[int, str] = {}
        label_mismatches: list[tuple[int, str, str]] = []
        for index, rel in enumerate(personal_files):
            path = root / "data/pml" / rel
            file_label, table = _single(_load(path), path)
            # The source-list position is authoritative (W2U's serializer ignores the table name);
            # some upstream table names are mis-encoded, so record them instead of failing.
            label = file_label if species_by_label.get(file_label) == index else model.names.get(index, file_label)
            if label != file_label:
                label_mismatches.append((index, file_label, label))
            file_labels[index] = file_label
            personal[index] = Personal(
                index, label,
                tuple(int(table[k]) for k in STAT_KEYS),
                (table["Primary Type"], table["Secondary Type"]),
                int(table["Capture Rate"]),
                (int(table["Egg Group 1"]), int(table["Egg Group 2"])),
                int(table["Gender Probability"]),
                tuple(int(table.get(f"TM HM {i}", 0)) & 0xFFFFFFFF for i in range(1, 5)),
                int(table.get("Form Count", 1)),
            )
            lpath = root / "data/pml" / learnset_files[index]
            _label, ltable = _single(_load(lpath), lpath)
            learnsets[index] = sorted(
                ((int(e["LEVEL"]), str(e["MOVE"])) for e in ltable.values() if isinstance(e, dict) and e.get("MOVE") not in (None, "MOVE_NONE")),
            )
            epath = root / "data/pml" / evolution_files[index]
            _label, etable = _single(_load(epath), epath)
            evolution_paths[index] = epath
            evolutions[index] = [
                Evolution(str(e["Method"]), e["Parameter"], str(e["Target Species"]))
                for _, e in sorted(etable.items(), key=lambda kv: int(kv[0].rsplit("_", 1)[1]))
                if isinstance(e, dict) and str(e.get("Method")) != "METHOD_NONE"
            ]

        move_power = {}
        moves_meson = root / MOVES_MESON
        if moves_meson.exists():
            for rel in _meson_list(moves_meson.read_text(encoding="utf-8"), ".toml"):
                path = root / "data/pml/moves" / rel
                label, table = _single(_load(path), path)
                move_power[label] = int(table.get("Power", 0))

        encounters = {int(p.stem): _load(p) for p in sorted((root / ENCOUNTERS_DIR).glob("*.toml"), key=lambda p: int(p.stem))}
        trainers = {int(p.stem): _load(p) for p in sorted((root / TRAINERS_DIR).glob("*.toml"), key=lambda p: int(p.stem))}
        names = _messages(root / TRAINER_NAMES) if (root / TRAINER_NAMES).exists() else {}
        classes = _messages(root / TRAINER_CLASSES) if (root / TRAINER_CLASSES).exists() else {}
        item_names = {v: k for k, v in enums["items"].items()}
        evo_labels = {}
        for index, path in evolution_paths.items():
            evo_labels[index] = _single(_load(path), path)[0]
        data = cls(root, enums, model, personal, learnsets, evolutions, evolution_paths, move_power, encounters,
                   trainers, names, classes, item_names)
        data.evolution_labels = evo_labels
        data.label_mismatches = label_mismatches
        return data

    # --- lookups --------------------------------------------------------------
    def species_id(self, label: str) -> int:
        try:
            return self.enums["species"][label]
        except KeyError:
            raise DataError(f"unknown species label {label!r}") from None

    def species_label(self, species: int) -> str:
        return self.personal[species].label

    def item_id(self, label: str) -> int:
        try:
            return self.enums["items"][label]
        except KeyError:
            raise DataError(f"unknown item label {label!r}") from None

    def damaging_moves_by_level(self, species: int, level: int) -> list[str]:
        return [m for lv, m in self.learnsets.get(species, []) if lv <= level and self.move_power.get(m, 0) > 0]

    def default_moves(self, species: int, level: int) -> list[str]:
        """The engine's default moveset: the last four distinct level-up moves at or below ``level``."""
        moves: list[str] = []
        for lv, move in self.learnsets.get(species, []):
            if lv <= level and move not in moves:
                moves.append(move)
        moves = moves[-4:]
        return moves + ["MOVE_NONE"] * (4 - len(moves))

    # --- writing --------------------------------------------------------------
    def write_encounter(self, index: int, document: dict) -> None:
        (self.root / ENCOUNTERS_DIR / f"{index}.toml").write_text(tomlw.dumps(document), encoding="utf-8")
        self.encounters[index] = document

    def write_trainer(self, index: int, document: dict) -> None:
        (self.root / TRAINERS_DIR / f"{index}.toml").write_text(tomlw.dumps(document), encoding="utf-8")
        self.trainers[index] = document

    def write_evolutions(self, species: int, entries: list[Evolution]) -> None:
        label = self.evolution_labels.get(species, self.species_label(species))
        table = {}
        padded = list(entries) + [Evolution("METHOD_NONE", 0, "SPECIES_NONE")] * (8 - len(entries))
        if len(padded) != 8:
            raise DataError(f"{label} has more than 8 evolution entries")
        for i, evo in enumerate(padded):
            table[f"EVOLUTION_{i}"] = {"Method": evo.method, "Parameter": evo.parameter, "Target Species": evo.target}
        self.evolution_paths[species].write_text(tomlw.dumps({label: table}), encoding="utf-8")
        self.evolutions[species] = [e for e in padded if e.method != "METHOD_NONE"]
