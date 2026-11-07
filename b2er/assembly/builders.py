"""Assembly backends.

* :class:`W2UMesonBuilder` runs W2U's own Meson/Ninja/CTRMap build inside the
  workroot on the user's machine (local only, never in CI).
* :class:`MockBuilder` assembles a structurally valid ROM from the staged
  sources with this project's own encoders. It exercises every pipeline stage
  in CI with synthetic inputs and is never used to produce a playable ROM.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..gamedata import encounters as enc
from ..gamedata import trainers as trn
from ..nds.narc import Narc
from ..nds.rom import Rom, build_rom
from ..w2u_data import W2UData
from . import BuildError

MESON_REPOSITORY = "https://github.com/mesonbuild/meson"


def _run(command: list[str], cwd: Path, log: Path, env: dict | None = None) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as handle:
        handle.write(("$ " + " ".join(command) + "\n").encode())
        handle.flush()
        result = subprocess.run(command, cwd=cwd, stdout=handle, stderr=subprocess.STDOUT, env=env)
    if result.returncode != 0:
        raise BuildError(f"{command[0]} failed with exit code {result.returncode}; see {log.name} in the workroot logs")


class W2UMesonBuilder:
    name = "w2u-meson"

    def __init__(self, meson_commit: str, meson_source: Path | None = None, java: str | None = None, jobs: int | None = None) -> None:
        self.meson_commit = meson_commit
        self.meson_source = meson_source
        self.java = java
        self.jobs = jobs

    def prepare_meson(self, workroot: Path, log: Path) -> Path:
        target = workroot / "meson"
        if self.meson_source is not None:
            _run(["git", "clone", "--quiet", "--no-hardlinks", str(self.meson_source), str(target)], workroot, log)
        else:
            _run(["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout", MESON_REPOSITORY, str(target)], workroot, log)
        _run(["git", "-C", str(target), "-c", "advice.detachedHead=false", "checkout", "--quiet", self.meson_commit], workroot, log)
        head = subprocess.run(["git", "-C", str(target), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        if head != self.meson_commit:
            raise BuildError(f"Meson checkout is at {head}, expected pinned {self.meson_commit}")
        return target / "meson.py"

    def build(self, workroot: Path, w2u: Path) -> Path:
        logs = workroot / "logs"
        meson_py = self.prepare_meson(workroot, logs / "meson-fetch.log")
        env = dict(os.environ)
        if self.java:
            env["JAVA"] = self.java
        _run(["python3", str(meson_py), "setup", "build-alpha", "--cross-file=meson/nitro.ini", "-Dstrip_rpms=true"],
             w2u, logs / "meson-setup.log", env)
        command = ["ninja", "-C", "build-alpha", "White2Upgrade.nds"]
        if self.jobs:
            command[1:1] = ["-j", str(self.jobs)]
        _run(command, w2u, logs / "ninja.log", env)
        output = w2u / "build-alpha" / "White2Upgrade.nds"
        if not output.is_file():
            raise BuildError("the W2U build finished without producing White2Upgrade.nds")
        return output


class MockBuilder:
    """Synthetic assembly for CI: base ROM files + staged VFS overrides, encoded by this project's codecs."""

    name = "mock"

    def build(self, workroot: Path, w2u: Path) -> Path:
        base = Rom((workroot / "Port-Pokeweb" / "cleanwhite2.nds").read_bytes())
        files = {path: base.file_bytes(fid) for path, fid in base.paths().items()}
        data = W2UData.load(w2u)
        files["a/1/2/7"] = Narc([encode_encounter_document(data, data.encounters[i]) for i in sorted(data.encounters)]).build()
        trdata, trpoke = [], []
        for index in sorted(data.trainers):
            d, p = encode_trainer_document(data, data.trainers[index])
            trdata.append(d)
            trpoke.append(p)
        files["a/0/9/1"] = Narc(trdata).build()
        files["a/0/9/2"] = Narc(trpoke).build()
        # CTRMap imports the whole VFS tree; mirror that for staged plugins.
        patches_dir = w2u / "vfs" / "data" / "patches"
        if patches_dir.is_dir():
            for plugin in sorted(patches_dir.iterdir()):
                if plugin.is_file():
                    files[f"patches/{plugin.name}"] = plugin.read_bytes()
        arm9 = (w2u / "pmc" / "ARM9PMC.bin").read_bytes()
        overlays = [(o.overlay_id, base.file_bytes(o.file_id), o.compressed) for o in base.overlays()]
        image = build_rom(game_code=base.game_code.encode(), arm9=arm9, files=files, overlays=overlays)
        output = w2u / "build-alpha" / "White2Upgrade.nds"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(image)
        return output


def encode_encounter_document(data: W2UData, document: dict) -> bytes:
    containers = []
    for key in sorted(document, key=lambda k: int(k.rsplit("_", 1)[1])):
        c = document[key]
        rates = tuple(int(c[k]) for k in enc.RATE_KEYS)
        tables = {}
        for category, kind, count in enc.TABLES:
            table = c.get(category, {}).get(kind, {})
            slots = [table[k] for k in sorted(table, key=lambda k: int(k.split()[-1]))]
            if len(slots) != count:
                raise BuildError(f"encounter table {category}.{kind} has {len(slots)} slots, expected {count}")
            tables[(category, kind)] = [
                enc.Slot(data.species_id(s["Species"]), int(s["Form"]), int(s["Minimum Level"]), int(s["Maximum Level"]))
                for s in slots
            ]
        containers.append(enc.Container(rates, tables))
    return enc.encode_record(containers)


def _label(data: W2UData, kind: str, value) -> int:
    return value if isinstance(value, int) else data.enums[kind][value]


def encode_trainer_document(data: W2UData, document: dict) -> tuple[bytes, bytes]:
    party = document.get("PARTY", [])
    has_moves = any("MOVES" in m for m in party)
    has_item = any("HELD_ITEM" in m for m in party)
    fmt = (trn.FORMAT_HELD_ITEM if has_item else 0) | (trn.FORMAT_MOVES if has_moves else 0)
    tr = trn.TrainerData(fmt, int(document["CLASS"]), int(document["BATTLE_TYPE"]), len(party),
                         tuple(_label(data, "items", i) for i in document["ITEMS"]), int(document["AI"]),
                         int(document["CAN_HEAL"]), int(document["REWARD_MONEY"]), _label(data, "items", document["REWARD_ITEM"]))
    poke = bytearray()
    for m in party:
        poke += bytes([int(m["DIFFICULTY_VALUE"]), (int(m["ABILITY"]) << 4) | (int(m["GENDER"]) & 0xF)])
        poke += int(m["LEVEL"]).to_bytes(2, "little") + data.species_id(m["SPECIES"]).to_bytes(2, "little")
        poke += int(m["FORM"]).to_bytes(2, "little")
        if has_item:
            poke += _label(data, "items", m.get("HELD_ITEM", "ITEM_NONE")).to_bytes(2, "little")
        if has_moves:
            for move in m.get("MOVES", ["MOVE_NONE"] * 4):
                poke += _label(data, "moves", move).to_bytes(2, "little")
    return trn.encode_trdata(tr), bytes(poke)
