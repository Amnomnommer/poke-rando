"""Generate a synthetic W2U-shaped checkout, synthetic clean ROMs, and matching pins for mock builds.

Everything is invented at runtime: species, stats, moves, trainers, scripts, the
ARM9 image, and the SDK command description. It reproduces only file *shapes*
the pipeline reads, never upstream or game content.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import subprocess
import zipfile
from pathlib import Path

from b2er import tomlw
from b2er.gamedata import encounters as enc
from b2er.nds import arm9
from b2er.nds.narc import Narc
from b2er.nds.rom import build_rom

MAX_SPECIES = 1023
EGG, BAD_EGG = 1024, 1025
FORMS = range(1026, 1031)
NAMED = {
    495: "SNIVY", 496: "SERVINE", 497: "SERPERIOR", 498: "TEPIG", 499: "PIGNITE", 500: "EMBOAR",
    501: "OSHAWOTT", 502: "DEWOTT", 503: "SAMUROTT", 504: "PATRAT", 505: "WATCHOG", 509: "PURRLOIN",
    519: "PIDOVE", 650: "CHESPIN", 703: "CARBINK",
}
MOVES = {"MOVE_NONE": (0, 0), "MOVE_POUND": (1, 40), "MOVE_GROWL": (45, 0), "MOVE_TACKLE": (33, 50),
         "MOVE_EMBER": (52, 40), "MOVE_SURF": (57, 90), "MOVE_CUT": (15, 50)}
ITEMS = {"ITEM_NONE": 0, "ITEM_MASTER_BALL": 1, "ITEM_POTION": 17, "ITEM_FIRE_STONE": 82, "ITEM_METAL_COAT": 233,
         "ITEM_RARE_CANDY": 50, "ITEM_TM01": 328, "ITEM_TM02": 329, "ITEM_HM01": 420, "ITEM_BICYCLE": 450,
         "ITEM_LINKING_CORD": 900}
METHODS = ["METHOD_NONE", "METHOD_FRIENDSHIP", "METHOD_FRIENDSHIP_DAY", "METHOD_FRIENDSHIP_NIGHT", "METHOD_LEVELUP",
           "METHOD_TRADE", "METHOD_TRADE_ITEM", "METHOD_TRADE_MONS_SPECIFIC", "METHOD_ITEM_USE"]
OPCODES = {0x02: ("VMHalt", [], "End"), 0x1E: ("VMJump", [("addr", "int")], "Jump"),
           0xB5: ("ItemAdd", [("itemId", "ushort"), ("amount", "ushort"), ("pSuccess", "ref ushort")], ""),
           0x10C: ("PokePartyAdd", [("pSuccess", "ref ushort"), ("species", "ushort"), ("forme", "ushort"), ("level", "ushort")], ""),
           0x200: ("CallWildBattle", [("species", "ushort"), ("level", "ushort")], "")}


def env() -> dict:
    e = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    e.update({"GIT_AUTHOR_NAME": "Synthetic", "GIT_AUTHOR_EMAIL": "s@example.invalid", "GIT_COMMITTER_NAME": "Synthetic",
              "GIT_COMMITTER_EMAIL": "s@example.invalid", "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000",
              "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
    return e


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, check=True, env=env()).stdout.decode().strip()


def write(root: Path, rel: str, data: bytes | str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return path


def species_label(i: int) -> str:
    if i == 0:
        return "SPECIES_NONE"
    if i == EGG:
        return "SPECIES_EGG"
    if i == BAD_EGG:
        return "SPECIES_BAD_EGG"
    return f"SPECIES_{NAMED[i]}" if i in NAMED else f"SPECIES_{i}"


def h(*parts) -> int:
    return int.from_bytes(hashlib.sha256(repr(parts).encode()).digest()[:4], "big")


def evolution_for(i: int) -> list[dict]:
    if i in (495, 496, 498, 499, 501, 502, 504):
        return [{"Method": "METHOD_LEVELUP", "Parameter": 17 if i % 3 == 0 else 36, "Target Species": species_label(i + 1)}]
    if 10 <= i < 990 and i % 3 == 1 and i + 2 not in NAMED and i + 1 not in NAMED:
        methods = [("METHOD_LEVELUP", 16), ("METHOD_TRADE", 0), ("METHOD_ITEM_USE", "ITEM_LINKING_CORD"),
                   ("METHOD_ITEM_USE", "ITEM_FIRE_STONE"), ("METHOD_TRADE_ITEM", "ITEM_METAL_COAT")]
        method, param = methods[h("evo", i) % len(methods)]
        if isinstance(param, str):
            param = ITEMS[param]
        return [{"Method": method, "Parameter": param, "Target Species": species_label(i + 1)}]
    return []


def make_checkout(root: Path) -> dict:
    """Create the synthetic W2U git repository with an initialized swan submodule. Returns facts for pins."""
    root.mkdir(parents=True)
    git(root, "init", "-q")
    all_ids = list(range(0, BAD_EGG + 1)) + list(FORMS)
    defines = {species_label(i): i for i in all_ids}
    write(root, "tools/mkdata/enum/species.toml", tomlw.dumps({"DEFINE": defines}))
    write(root, "tools/mkdata/enum/moves.toml", tomlw.dumps({"DEFINE": {k: v[0] for k, v in MOVES.items()}}))
    write(root, "tools/mkdata/enum/items.toml", tomlw.dumps({"DEFINE": ITEMS}))
    write(root, "tools/mkdata/enum/evolution_methods.toml", tomlw.dumps({"DEFINE": {m: i for i, m in enumerate(METHODS)}}))
    write(root, "tools/mkdata/enum/types.toml", tomlw.dumps({"DEFINE": {"TYPE_NORMAL": 0, "TYPE_FIRE": 1, "TYPE_WATER": 2}}))
    types = ["TYPE_NORMAL", "TYPE_FIRE", "TYPE_WATER"]
    personal_list, learn_list, evo_list = [], [], []
    for i in all_ids:
        d = f"s{i}"
        stats = [20 + h("stat", i, k) % 80 for k in range(6)]
        legendary = i in (150, 151, 380, 381) or i >= 1000 and i <= 1010
        personal = {
            "Base HP": stats[0], "Base Attack": stats[1], "Base Defense": stats[2], "Base Speed": stats[3],
            "Base Special Attack": stats[4], "Base Special Defense": stats[5],
            "Primary Type": types[h("t", i) % 3], "Secondary Type": types[h("t", i) % 3],
            "Capture Rate": 3 if legendary else 120, "Gender Probability": 127,
            "Egg Group 1": 15 if legendary else 1 + h("eg", i) % 10, "Egg Group 2": 15 if legendary else 1,
            "Form Count": 1,
            "TM HM 1": h("tm", i, 1) - 2**31, "TM HM 2": h("tm", i, 2) - 2**31,
            "TM HM 3": h("tm", i, 3) - 2**31, "TM HM 4": h("tm", i, 4) % 2**20,
        }
        if i == 0:
            personal.update({k: 0 for k in ("Base HP", "Base Attack", "Base Defense", "Base Speed", "Base Special Attack", "Base Special Defense")})
        write(root, f"data/pml/{d}/personal.toml", tomlw.dumps({species_label(i): personal}))
        if i % 7 == 0:
            entries = [("MOVE_GROWL", 1), ("MOVE_TACKLE", 15)]  # no damaging move before level 15
        else:
            entries = [("MOVE_TACKLE", 1), ("MOVE_GROWL", 3), ("MOVE_EMBER", 9), ("MOVE_SURF", 30)]
        learnset = {f"LEARNSET_ENTRY_{k}": {"MOVE": m, "LEVEL": lv} for k, (m, lv) in enumerate(entries)}
        write(root, f"data/pml/{d}/learnset.toml", tomlw.dumps({species_label(i): learnset} if i else {species_label(i): {}}))
        evos = evolution_for(i) if i <= MAX_SPECIES else []
        table = {}
        for k in range(8):
            e = evos[k] if k < len(evos) else {"Method": "METHOD_NONE", "Parameter": 0, "Target Species": "SPECIES_NONE"}
            table[f"EVOLUTION_{k}"] = e
        write(root, f"data/pml/{d}/evolutions.toml", tomlw.dumps({species_label(i): table}))
        personal_list.append(f"{d}/personal.toml")
        learn_list.append(f"{d}/learnset.toml")
        evo_list.append(f"{d}/evolutions.toml")
    meson = "personal_sources = [\n" + "".join(f"  '{p}',\n" for p in personal_list) + "]\n"
    meson += "learnset_sources = [\n" + "".join(f"  '{p}',\n" for p in learn_list) + "]\n"
    meson += "evolution_sources = [\n" + "".join(f"  '{p}',\n" for p in evo_list) + "]\n"
    write(root, "data/pml/meson.build", meson)
    move_files = []
    for label, (number, power) in MOVES.items():
        write(root, f"data/pml/moves/{number}.toml", tomlw.dumps({label: {"Power": power}}))
        move_files.append(f"{number}.toml")
    write(root, "data/pml/moves/meson.build", "moves = [\n" + "".join(f"  '{m}',\n" for m in move_files) + "]\n")

    def slot(label, lo, hi):
        return {"Species": label, "Form": 0, "Minimum Level": lo, "Maximum Level": hi}

    def container(species_pool, lo, hi, seed):
        c = {k: (10 if k.startswith("GRASS") else 0) for k in enc.RATE_KEYS}
        for category, kind, count in enc.TABLES:
            table = {}
            for s in range(count):
                if category == "GRASS" and kind == "SINGLES":
                    label = species_pool[(s + seed) % len(species_pool)]
                    table[f"Slot {s}"] = slot(label, lo, hi)
                else:
                    table[f"Slot {s}"] = slot("SPECIES_NONE", 0, 0)
            c.setdefault(category, {})[kind] = table
        return c

    route19 = ["SPECIES_PATRAT", "SPECIES_PURRLOIN", "SPECIES_PIDOVE", "SPECIES_20", "SPECIES_33"]
    records = {
        0: {"ENCOUNTER_CONTAINER_0": container(["SPECIES_40", "SPECIES_41", "SPECIES_44"], 10, 12, 0)},
        1: {f"ENCOUNTER_CONTAINER_{k}": container(["SPECIES_60", "SPECIES_61", "SPECIES_64", "SPECIES_67"], 20, 22, k) for k in range(4)},
        2: {f"ENCOUNTER_CONTAINER_{k}": container(route19, 2, 4, k) for k in range(4)},
        3: {"ENCOUNTER_CONTAINER_0": container(["SPECIES_100", "SPECIES_101", "SPECIES_103", "SPECIES_104"], 30, 35, 1)},
        4: {"ENCOUNTER_CONTAINER_0": container(["SPECIES_200", "SPECIES_202", "SPECIES_205", "SPECIES_206", "SPECIES_208"], 40, 45, 2)},
    }
    for index, document in records.items():
        write(root, f"data/encounters/{index}.toml", tomlw.dumps(document))
    write(root, "data/encounters/meson.build", "encounters = [\n" + "".join(f"  '{i}.toml',\n" for i in records) + "]\n")

    def member(label, level, moves=False):
        m = {"SPECIES": label, "LEVEL": level, "FORM": 0, "GENDER": 0, "ABILITY": 0, "DIFFICULTY_VALUE": 50}
        if moves:
            m["MOVES"] = ["MOVE_TACKLE", "MOVE_NONE", "MOVE_NONE", "MOVE_NONE"]
        return m

    def trainer(cls, party, battle=0):
        return {"CLASS": cls, "BATTLE_TYPE": battle, "ITEMS": ["ITEM_POTION", "ITEM_NONE", "ITEM_NONE", "ITEM_NONE"], "AI": 7,
                "CAN_HEAL": 0, "REWARD_MONEY": 20, "REWARD_ITEM": "ITEM_NONE", "PARTY": party}

    trainers = {
        0: trainer(0, [member("SPECIES_40", 5)]),
        1: trainer(2, [member("SPECIES_SNIVY", 5)]),  # first rival (one Pokémon)
        2: trainer(2, [member("SPECIES_SERVINE", 22, True), member("SPECIES_60", 21, True), member("SPECIES_61", 21, True)]),
        3: trainer(3, [member("SPECIES_100", 14), member("SPECIES_103", 16)]),  # Leader
        4: trainer(3, [member("SPECIES_200", 30)]),  # Leader with one Pokémon (padded to two)
        5: trainer(4, [member("SPECIES_205", 50), member("SPECIES_206", 50)]),  # Elite Four
        6: trainer(1, [member("SPECIES_41", 12, True), member("SPECIES_44", 13, True)]),
        7: trainer(5, [member("SPECIES_208", 60), member("SPECIES_202", 60)]),  # Champion
    }
    for index, document in trainers.items():
        write(root, f"data/trainers/{index}.toml", tomlw.dumps(document))
    names = {0: "--", 1: "Rival", 2: "Rival", 3: "Cheren", 4: "Roxie", 5: "Shauntal", 6: "Ace", 7: "Iris"}
    classes = {0: "Youngster", 1: "Ace Trainer", 2: "Pokémon Trainer", 3: "Leader", 4: "Elite Four", 5: "Champion"}

    def messages(entries):
        return tomlw.dumps({"msg": {"section_0": {"entries": {str(k): {"text": [v + "$"]} for k, v in entries.items()}}}})

    write(root, "data/text/system/382.toml", messages(names))
    write(root, "data/text/system/383.toml", messages(classes))
    write(root, "meson.build", "project('Synthetic')\nmain_menu_skip_patch = 'vfs/data/patches/MainMenuSkip(1).dll'\n"
                               "rom = custom_target(\n  input: [\n    main_menu_skip_patch,\n    other,\n  ],\n)\n")
    write(root, "White2Upgrade.cmproj.example", "LastROMPath: \"\"\nVFSBase: ../IRDO_Extracted\nVFSOverlay: vfs\n")
    plugin = b"synthetic development plugin"
    write(root, "MainMenuSkip(1).dll", plugin)
    write(root, "vfs/data/patches/MainMenuSkip(1).dll", plugin)
    write(root, "vfs/data/patches/White2Upgrade.dll", b"synthetic core plugin")
    stale = arm9.synthetic_arm9(compressed_static_end=0x020776CC)
    write(root, "pmc/ARM9PMC.bin", stale)
    jar = synthetic_jar()
    write(root, "tools/CTRMap/CTRMapV-dirty.jar", jar)
    files = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]
    for start in range(0, len(files), 500):
        git(root, "add", "-f", "--", *files[start:start + 500])
    swan = root / "include" / "swan"
    swan.mkdir(parents=True)
    git(swan, "init", "-q")
    write(swan, "swan.h", "// synthetic swan header\n")
    git(swan, "add", "swan.h")
    git(swan, "commit", "-q", "-m", "swan")
    swan_head = git(swan, "rev-parse", "HEAD")
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{swan_head},include/swan")
    git(root, "commit", "-q", "-m", "synthetic upstream")
    return {"head": git(root, "rev-parse", "HEAD"), "swan": swan_head, "jar_sha256": hashlib.sha256(jar).hexdigest(),
            "arm9_sha256": hashlib.sha256(stale).hexdigest(),
            "meson_sha256": hashlib.sha256((root / "meson.build").read_bytes()).hexdigest(),
            "cmproj_sha256": hashlib.sha256((root / "White2Upgrade.cmproj.example").read_bytes()).hexdigest(),
            "plugin_sha256": hashlib.sha256(plugin).hexdigest()}


def synthetic_jar() -> bytes:
    lines = []
    table = dict(OPCODES)
    for filler in range(0x300, 0x380):  # pad past the loader's plausibility floor
        table[filler] = (f"SyntheticNop{filler:x}", [], "")
    for code, (name, operands, flow) in table.items():
        lines.append(f"0x{code:x}:")
        lines.append(f"  Name: {name}")
        if operands:
            lines.append("  Parameters:")
            for oname, otype in operands:
                lines.append(f"    - Name: {oname}")
                lines.append(f"      Type: {otype}")
        if flow:
            lines.append(f"  CommandType: {flow}")
        lines.append("")
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("yml/Base.yml", "\n".join(lines))
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as z:
        z.writestr("ctrmap/resources/scripting/cm_ide/sdk/EV_GEN_V/SDK5-B2W2-Generated.lib", inner.getvalue())
    return outer.getvalue()


def script(commands: list[bytes]) -> bytes:
    body = b"".join(commands) + struct.pack("<H", 0x02)
    return struct.pack("<iH", 2, 0xFD13) + body  # one entry, pointing just past the terminator


def poke_add(species: int, level: int) -> bytes:
    return struct.pack("<5H", 0x10C, 0x8000, species, 0, level)


def item_add(item: int, amount: int = 1) -> bytes:
    return struct.pack("<4H", 0xB5, item, amount, 0x8000)


def make_roms(directory: Path) -> tuple[Path, Path]:
    members = [script([item_add(17)]) for _ in range(60)]
    members[10] = script([poke_add(495, 5), poke_add(498, 5), poke_add(501, 5)])
    members[11] = script([item_add(17, 2), item_add(82), item_add(50), item_add(328), item_add(329), item_add(420), item_add(450)])
    members[12] = script([poke_add(40, 10)])
    members[13] = script([struct.pack("<3H", 0x200, 380, 50)])
    scripts_narc = Narc(members).build()
    files = {"a/0/5/6": scripts_narc, "a/1/2/7": Narc([b""]).build(), "a/0/9/1": Narc([b""]).build(),
             "a/0/9/2": Narc([b""]).build(), "a/0/1/6": Narc([b"synthetic"]).build()}
    white = build_rom(game_code=b"IRDO", arm9=arm9.synthetic_arm9(), files=files, overlays=[(36, b"synthetic overlay 36", False)])
    black = build_rom(game_code=b"IREO", arm9=arm9.synthetic_arm9(), files={"x.bin": b"synthetic black 2"})
    directory.mkdir(parents=True, exist_ok=True)
    w, b = directory / "clean white 2.nds", directory / "clean black 2.nds"
    w.write_bytes(white)
    b.write_bytes(black)
    return w, b


def make_pins(directory: Path, facts: dict, white: Path, black: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    unresolved = {"status": "unresolved", "value": None, "reason": "synthetic"}
    inputs = {"schema_version": 1, "inputs": [
        {"id": "white2", "game_code": "IRDO", "role": "target", "sha256": hashlib.sha256(white.read_bytes()).hexdigest(),
         "size_bytes": dict(unresolved)},
        {"id": "black2", "game_code": "IREO", "role": "required-by-upstream",
         "sha256": hashlib.sha256(black.read_bytes()).hexdigest(), "size_bytes": dict(unresolved)}]}
    upstream = {"schema_version": 1,
                "upstream": {"repository": "https://example.invalid/w2u", "commit": facts["head"]},
                "source_dependencies": [
                    {"id": "swan", "path": "include/swan", "storage": "submodule", "commit": facts["swan"]},
                    {"id": "meson", "path": "subprojects/meson", "storage": "fetched", "commit": "1" * 40}],
                "python_requirements": dict(unresolved),
                "external_binaries": [
                    {"id": "ctrmap-jar", "path": "tools/CTRMap/CTRMapV-dirty.jar", "sha256": facts["jar_sha256"]},
                    {"id": "pmc-arm9", "path": "pmc/ARM9PMC.bin", "sha256": facts["arm9_sha256"]}]}
    (directory / "inputs.json").write_text(json.dumps(inputs), encoding="utf-8")
    (directory / "upstream.json").write_text(json.dumps(upstream), encoding="utf-8")
    return directory


def patch_pins(facts: dict) -> dict:
    return {"meson_build": facts["meson_sha256"], "pmc_arm9": facts["arm9_sha256"], "cmproj_example": facts["cmproj_sha256"],
            "main_menu_skip": facts["plugin_sha256"]}
