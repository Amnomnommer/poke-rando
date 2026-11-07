"""Pokémon White Version 2 (IRDO) adapter: W2U-backed expanded engine, shared Generation V machinery."""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys
from pathlib import Path

from ... import w2u_checkout
from ...assembly import BuildError, builders, ctrmap_dsi, patches, prereqs, stage, w2u_sdk
from ...assembly.builders import encode_encounter_document, trainer_labels
from ...gamedata import trainer_codec
from ...capabilities import Capability as C, Level, Support, unsupported
from ...gamedata import encounters as enc, trainers as trn
from ...model import BattleFormat, GameModel, SpeciesKey
from ...nds import FormatError, arm9 as arm9_tools, dsi
from ...nds.narc import Narc
from ...nds.rom import Rom
from ...w2u_data import W2UData
from . import sites as site_tools
from .common import Gen5Adapter
from .w2u_model import FORMAT_CODES, to_model, write_model

ENCOUNTER_ARCHIVE, TRDATA_ARCHIVE, TRPOKE_ARCHIVE = "a/1/2/7", "a/0/9/1", "a/0/9/2"
ROUTE19_SIGNATURE = {SpeciesKey(504), SpeciesKey(509), SpeciesKey(519)}  # Patrat, Purrloin, Pidove


class White2Adapter(Gen5Adapter):
    id = "gen5.white2"
    version = "0.1.0"
    display_name = "Pokémon White Version 2 (USA/Europe, IRDO) on the W2U expanded engine"
    fingerprints = ("gen5-white2",)
    auxiliary_inputs = {"w2u": "clean W2U-Project checkout at the pinned commit with swan initialized"}
    optional_inputs = {"black2": "clean Black 2 ROM (optional provenance input; the White 2 ROM target never reads it)"}
    BLACK2_PLACEHOLDER = (b"b2er placeholder - NOT A ROM.\n"
                          b"W2U's Meson configuration requires this path to exist for its separate Black 2 package\n"
                          b"targets. The White2Upgrade.nds target never reads it; b2er verifies no Black 2 target ran.\n")
    BLACK2_TARGET_OUTPUTS = ("black2upgrade-data.tar.gz", "black2upgrade-package-manifest.json",
                             "src/black2upgrade-compatibility.json", "black2upgrade-artifacts")

    def capabilities(self):
        return {
            C.RECOGNITION: Support(Level.SUPPORTED, "approved clean SHA-256 and IRDO header"),
            C.REPACK: Support(Level.SUPPORTED, "W2U Meson/Ninja/CTRMap assembly in a disposable workroot"),
            C.SPECIES_EXPANSION: Support(Level.EXPERIMENTAL, "W2U layout: ordinary species 1..1023; IDs 1024/1025 are "
                                         "Egg/Bad Egg (docs/species-id-model.md)", {"national_dex_max": 1023}),
            C.STARTERS: Support(Level.EXPERIMENTAL, "script gift commands; the selection screen display is unpatched"),
            C.WILD: Support(Level.SUPPORTED, "encounter sources rewritten and verified byte-for-byte"),
            C.TRAINERS: Support(Level.SUPPORTED, "trainer sources rewritten and verified"),
            C.GIFTS: Support(Level.EXPERIMENTAL, "PokePartyAdd(Ex) constant script sites"),
            C.STATICS: Support(Level.EXPERIMENTAL, "CallWildBattle(Ex) constant script sites"),
            C.FIELD_ITEMS: Support(Level.EXPERIMENTAL, "ItemAdd constant script sites only"),
            C.HIDDEN_ITEMS: unsupported("hidden-item storage not yet decoded for White 2"),
            C.SHOP_ITEMS: unsupported("shop tables not yet decoded for White 2"),
            C.EVOLUTION_NORMALIZATION: Support(Level.SUPPORTED, "evolution sources rewritten"),
            C.PROGRESSION_ANALYSIS: Support(Level.EXPERIMENTAL, "global obtainability and TM/HM coverage; not route-ordered"),
            C.MAJOR_BATTLE_DOUBLES: Support(Level.SUPPORTED, "battle type, party size and AI flag rewritten; W2U single-NPC Doubles fix"),
            C.POKEDEX_EXPANSION: Support(Level.EXPERIMENTAL, "W2U Pokédex through #1023", {"national_dex_max": 1023}),
            C.SAVE_FORMAT_EXPANSION: unsupported("W2U save layout used unchanged; #1025 needs a wider Pokédex block"),
            C.DETERMINISTIC_VERIFICATION: Support(Level.SUPPORTED, "independent re-encoding of every encounter and trainer record"),
            C.DIAGNOSTIC_FIXTURE: Support(Level.SUPPORTED, "Route 19 resolved by evidence; 48 slots verified"),
        }

    # --- preflight and staging ---------------------------------------------------
    def preflight(self, inputs: dict) -> list[str]:
        problems = []
        options = inputs["options"]
        pins = Path(options["pins_dir"]) / "upstream.json"
        w2u = inputs["auxiliary"].get("w2u")
        if w2u is None:
            problems.append("--w2u is required for White 2 builds")
        else:
            try:
                report = w2u_checkout.verify(Path(w2u), pins)
                problems += [f"W2U checkout: {d['category']}: {d['subject']}: {d['detail']}" for d in report["defects"]]
                if report["checks"].get("swan_checkout", {}).get("status") != "pass":
                    problems.append("W2U checkout: include/swan must be initialized at the pinned commit")
            except w2u_checkout.OperationalError as error:
                problems.append(f"W2U checkout: {error}")
        if options.get("builder", "w2u") == "w2u":
            problems += [f"host prerequisite missing: {m.item} ({m.hint})" for m in prereqs.check_w2u_host(java=options.get("java"))]
        return problems

    def prepare(self, ctx) -> dict:
        upstream = ctx.options["pins_upstream"]
        swan = next(d for d in upstream["source_dependencies"] if d["id"] == "swan")
        w2u = ctx.workspace / "W2U-Project"
        staged = stage.stage_w2u(Path(ctx.auxiliary["w2u"]), w2u, swan["path"])
        port = ctx.workspace / "Port-Pokeweb"
        port.mkdir()
        shutil.copyfile(ctx.rom, port / "cleanwhite2.nds")
        black2 = ctx.auxiliary.get("black2")
        if black2 is not None:
            shutil.copyfile(black2, port / "cleanblack2.nds")
        else:
            (port / "cleanblack2.nds").write_bytes(self.BLACK2_PLACEHOLDER)
        ctx.state["black2_input"] = "approved clean ROM" if black2 is not None else "placeholder (not a ROM)"
        for name in ("cleanwhite2.nds", "cleanblack2.nds"):
            (port / name).chmod(stat.S_IRUSR | stat.S_IRGRP)
        builder = ctx.options.get("builder", "w2u")
        ctrmap_report: dict = {}
        if builder == "mock":
            # The mock builder never runs CTRMap, so arm9i.bin is staged raw; real builds use the pinned CTRMap.
            provider = lambda components: components["arm9i.bin"]  # noqa: E731
        else:
            provider = lambda components: ctrmap_dsi.ctrmap_arm9i(  # noqa: E731
                port / "cleanwhite2.nds", w2u / w2u_sdk.JAR, ctx.workspace / "ctrmap-dsi", components, ctx.options.get("java"),
                original=ctx.rom, report=ctrmap_report)
        base = stage.extract_base((port / "cleanwhite2.nds").read_bytes(), ctx.workspace / "IRDO_Extracted", provider)
        pins = ctx.options.get("patch_pins") or {}
        report = {
            "staged": staged, "base": base, "ctrmap_dsi": ctrmap_report or {"skipped": "mock builder or NTR-only ROM"},
            "main_menu_skip": patches.remove_main_menu_skip(w2u, pins.get("meson_build", patches.MESON_BUILD_SHA256),
                                                            pins.get("main_menu_skip", patches.MAIN_MENU_SKIP_SHA256)),
            "pmc_arm9": patches.repair_pmc_arm9(w2u, pins.get("pmc_arm9", patches.PMC_ARM9_SHA256)),
            "cmproj": patches.write_cmproj(w2u, pins.get("cmproj_example", patches.CMPROJ_EXAMPLE_SHA256)),
            "posix_shell": patches.make_shell_portable(w2u, pins.get("bash_arithmetic_sites")),
            "rombuilder_guard": patches.guard_rombuilder(w2u, Path(__file__).resolve().parents[3],
                                                         ctx.workspace / "IRDO_Extracted" / "header.bin"),
        }
        ctx.state["w2u"] = w2u
        ctx.say(f"staged W2U ({staged['files']} files); removed {len(report['main_menu_skip']['removed_files'])} "
                f"development-plugin copies; ARM9 {report['pmc_arm9']['state_before']} -> {report['pmc_arm9']['state_after']}")
        return report

    # --- model ----------------------------------------------------------------------
    def read_model(self, ctx) -> GameModel:
        data = W2UData.load(ctx.state["w2u"])
        model = to_model(data, "gen5-white2")
        ctx.state["data"] = data
        try:
            table = w2u_sdk.load_table(ctx.state["w2u"])
        except BuildError as error:
            table = None
            ctx.state["site_error"] = str(error)
        ctx.state["table"] = table
        if table is not None:
            try:
                found = site_tools.discover(Rom((ctx.workspace / "Port-Pokeweb" / "cleanwhite2.nds").read_bytes()), table,
                                            model.codec, data.item_names)
                model.starters, model.gifts, model.statics = found["starters"], found["gifts"], found["statics"]
                model.item_locations = found["items"]
                ctx.state["sites"] = found
            except FormatError as error:
                ctx.state["site_error"] = str(error)
        return model

    def dynamic_capabilities(self, ctx, model):
        caps = dict(self.capabilities())
        error = ctx.state.get("site_error")
        found = ctx.state.get("sites", {})
        if error or not found:
            for c in (C.STARTERS, C.GIFTS, C.STATICS, C.FIELD_ITEMS):
                caps[c] = unsupported(f"script sites unavailable: {error}")
            return caps
        if found["starter_species_found"] != list(site_tools.VANILLA_STARTERS):
            caps[C.STARTERS] = unsupported(f"level-5 gift commands found for {found['starter_species_found']}, "
                                           f"need all of {list(site_tools.VANILLA_STARTERS)}")
        if not model.gifts:
            caps[C.GIFTS] = unsupported("no constant gift commands decoded")
        if not model.statics:
            caps[C.STATICS] = unsupported("no constant wild-battle commands decoded")
        if not any(i.item_class in ("ordinary", "tm") for i in model.item_locations):
            caps[C.FIELD_ITEMS] = unsupported("no shuffle-eligible ItemAdd sites decoded")
        return caps

    def resolve_diagnostic_area(self, model: GameModel) -> tuple[int, dict]:
        matches = []
        for area in model.encounters:
            if len(area.seasons) != enc.SEASONS:
                continue
            grass = [s for season in area.seasons for t in season if t.kind == "grass" for s in t.slots]
            species = {s.species for s in grass if s.species}
            if ROUTE19_SIGNATURE <= species and all(s.species and s.max_level <= 5 for s in grass):
                matches.append(area.id)
        if len(matches) != 1:
            raise BuildError(f"Route 19 must match exactly one encounter area by evidence; matched {matches}")
        return matches[0], {"rule": "unique four-season area whose grass has Patrat, Purrloin and Pidove at levels <= 5",
                            "area": matches[0]}

    # --- apply, rebuild, postprocess ------------------------------------------------------
    def apply(self, ctx, model, plan) -> dict:
        data: W2UData = ctx.state["data"]
        original = to_model(W2UData.load(ctx.state["w2u"]), "gen5-white2")
        summary = write_model(data, original, model)
        ctx.state["written"] = W2UData.load(ctx.state["w2u"])
        return summary

    def rebuild(self, ctx) -> bytes:
        upstream = ctx.options["pins_upstream"]
        meson = next((d for d in upstream["source_dependencies"] if d["id"] == "meson"), {})
        builder = (builders.MockBuilder() if ctx.options.get("builder") == "mock" else
                   builders.W2UMesonBuilder(meson.get("commit"), ctx.options.get("meson_source"), ctx.options.get("java"),
                                            ctx.options.get("jobs")))
        roots = [ctx.state["w2u"] / "vfs", ctx.workspace / "IRDO_Extracted"]  # CTRMap merges overlay over base
        missing = dsi.missing_components(roots)
        if missing:
            raise BuildError("required ROM components missing before assembly: " + ", ".join(missing))
        ctx.say(f"assembling with {builder.name} (Black 2 input: {ctx.state.get('black2_input')})")
        output = builder.build(ctx.workspace, ctx.state["w2u"])
        problems = dsi.check_assembled_file(output, ctx.workspace / "IRDO_Extracted" / "header.bin")
        if problems:
            raise BuildError("ROMBuilder output rejected: " + "; ".join(problems))
        rom = output.read_bytes()
        build_dir = ctx.state["w2u"] / "build-alpha"
        ran = [name for name in self.BLACK2_TARGET_OUTPUTS if (build_dir / name).exists()]
        if ran:
            raise BuildError(f"a Black 2 package target ran during the White 2 build: {ran}")
        return rom

    def site_patches(self, ctx, model: GameModel) -> list[dict]:
        found = ctx.state.get("sites") or {}
        item_ids = ctx.state["data"].enums["items"]
        patches_list = []
        for kind in ("starters", "gifts", "statics"):
            original = {s.id: s for s in found.get(kind, [])}
            for site in getattr(model, kind):
                before = original[site.id]
                if site.species != before.species:
                    patches_list.append({**{k: site.ext[k] for k in ("archive", "member", "command_offset", "value_offset", "command")},
                                         "feature": kind, "before": before.species.national, "after": model.codec.encode(site.species)})
        original_items = {i.id: i for i in found.get("items", [])}
        for loc in model.item_locations:
            before = original_items[loc.id]
            if loc.item != before.item:
                patches_list.append({**{k: loc.ext[k] for k in ("archive", "member", "command_offset", "value_offset", "command")},
                                     "feature": "field_items", "before": item_ids[before.item], "after": item_ids[loc.item]})
        return patches_list

    def postprocess(self, ctx, rom_bytes, model, plan):
        rom = Rom(rom_bytes)
        report = {"plugins_present": [p for p in rom.paths() if p.rsplit("/", 1)[-1] == patches.MAIN_MENU_SKIP]}
        if report["plugins_present"]:
            raise BuildError(f"finished ROM still contains {patches.MAIN_MENU_SKIP}")
        state = arm9_tools.classify(rom.arm9())
        report["arm9"] = {"state": state.state, "detail": state.detail, "repaired": state.state == "stale"}
        if state.state == "stale":
            rom.write_arm9_word(state.params.compressed_end_field_offset, 0)
        report["script_patches"] = self.site_patches(ctx, model)
        if report["script_patches"]:
            site_tools.apply_patches(rom, ctx.state["table"], report["script_patches"])
        rom.refresh_integrity()
        return bytes(rom.data), report

    # --- verification -----------------------------------------------------------------------
    def verify(self, ctx, rom_bytes, model, plan, post) -> dict:
        rom = Rom(rom_bytes)
        data: W2UData = ctx.state["written"]
        checks = []

        def check(name, ok, detail):
            checks.append({"name": name, "status": "pass" if ok else "fail", "detail": detail})

        check("game_code", rom.game_code == "IRDO", f"header game code {rom.game_code}")
        problems = rom.integrity_problems()
        check("header_integrity", not problems, "; ".join(problems) or "CRCs and signature digest current")
        state = arm9_tools.classify(rom.arm9())
        check("arm9_compression_state", state.bootable, state.detail)
        plugins = [p for p in rom.paths() if p.rsplit("/", 1)[-1] == patches.MAIN_MENU_SKIP]
        check("development_plugin_absent", not plugins, f"{len(plugins)} copies of {patches.MAIN_MENU_SKIP}")
        try:
            narc = Narc.parse(rom.read_path(ENCOUNTER_ARCHIVE))
            mismatch = [i for i in sorted(data.encounters)
                        if i >= len(narc.members) or narc.members[i] != encode_encounter_document(data, data.encounters[i])]
            check("encounters_match_plan", not mismatch and len(narc.members) == len(data.encounters),
                  f"{len(narc.members)} records; mismatched {mismatch[:5]}")
            invalid = []
            for i, member in enumerate(narc.members):
                for container in enc.decode_record(member, f"record {i}"):
                    for slots in container.tables.values():
                        for s in slots:
                            if s.species and not 1 <= s.species <= min(model.codec.national_max, ctx.settings["species"]["national_max"]):
                                invalid.append((i, s.species))
            check("encounter_species_in_range", not invalid, f"{len(invalid)} out-of-range or reserved species IDs")
            planned = {a.id: a for a in model.encounters}
            level_problems = [(i, s.min_level, s.max_level) for i, a in planned.items() for s in a.all_slots()
                              if s.species and not 1 <= s.min_level <= s.max_level <= 100]
            check("encounter_levels_valid", not level_problems, f"{len(level_problems)} invalid level ranges")
            if ctx.settings["mode"] == "diagnostic-route19":
                area = plan["diagnostic"]["area"]
                fixture = enc.verify_route19_fixture(narc.members[area])
                check("route19_fixture", not fixture,
                      "4 seasons x 12 grass slots, 928 bytes, exact species/form/levels, 35/35/30" if not fixture else "; ".join(fixture[:4]))
        except FormatError as error:
            check("encounters_decodable", False, str(error))
        try:
            trdata = Narc.parse(rom.read_path(TRDATA_ARCHIVE)).members
            trpoke = Narc.parse(rom.read_path(TRPOKE_ARCHIVE)).members
            mismatch, details = [], []
            labels = trainer_labels(data)
            for i in sorted(data.trainers):
                # Read-only: the normalized plan comes from the written sources, the ROM side from the rebuilt image.
                planned_record = trainer_codec.encode(i, data.trainers[i], labels)
                if i >= len(trdata) or i >= len(trpoke):
                    mismatch.append(i)
                    details.append(f"trainer {i}: missing from the rebuilt ROM")
                    continue
                problems = trainer_codec.compare(i, trainer_codec.decode(i, trdata[i], trpoke[i]), planned_record)
                if problems:
                    mismatch.append(i)
                    details += problems
            check("trainers_match_plan", not mismatch, f"{len(mismatch)} mismatched {mismatch[:5]}; {details[:5]}" if mismatch
                  else f"{len(data.trainers)} trainers match field-for-field")
            converted = [x["trainer"] for x in plan.get("doubles", []) if x["converted"]]
            wrong = [t for t in converted if trn.decode_trdata(trdata[t]).battle_type != FORMAT_CODES[BattleFormat.DOUBLE]]
            check("major_battles_doubles", not wrong, f"{len(converted)} converted; {len(wrong)} not Doubles in the ROM")
        except (FormatError, trainer_codec.TrainerFieldError) as error:
            check("trainers_decodable", False, str(error))
        if post.get("script_patches"):
            problems = site_tools.verify_patches(rom, post["script_patches"])
            check("script_patches_present", not problems, f"{len(post['script_patches'])} patches; {problems[:3]}")
        passed = all(c["status"] == "pass" for c in checks)
        return {"build_completed": True, "structural_verification": "passed" if passed else "failed",
                "runtime_acceptance": "not yet performed", "checks": checks}

    def tool_versions(self, ctx) -> dict:
        upstream = ctx.options["pins_upstream"]
        versions = {"python": sys.version.split()[0], "w2u_commit": upstream["upstream"]["commit"],
                    "swan_commit": next(d["commit"] for d in upstream["source_dependencies"] if d["id"] == "swan"),
                    "builder": ctx.options.get("builder", "w2u")}
        meson = next((d for d in upstream["source_dependencies"] if d["id"] == "meson"), None)
        if meson:
            versions["meson_commit"] = meson.get("commit")
        if ctx.options.get("builder", "w2u") == "w2u":
            java = ctx.options.get("java") or "java"
            result = subprocess.run([java, "-version"], capture_output=True, text=True)
            versions["java"] = (result.stderr or result.stdout).splitlines()[0] if result.returncode == 0 else None
            gcc = subprocess.run(["arm-none-eabi-gcc", "--version"], capture_output=True, text=True)
            versions["arm_none_eabi_gcc"] = gcc.stdout.splitlines()[0] if gcc.returncode == 0 else None
        return versions
