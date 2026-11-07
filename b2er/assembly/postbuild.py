"""Finished-ROM processing and verification.

Processing: development-plugin exclusion check, ARM9 compression-state repair,
script-level randomization, integrity refresh.

Verification is structural and semantic only. It reports three separate states
(build completed, structural verification, runtime acceptance) and never marks
runtime acceptance as performed.
"""

from __future__ import annotations

from ..gamedata import encounters as enc
from ..gamedata import trainers as trn
from ..nds import FormatError, arm9 as arm9_tools
from ..nds.narc import Narc
from ..nds.rom import Rom
from ..randomizer import script_features
from ..w2u_data import W2UData
from .builders import encode_encounter_document, encode_trainer_document
from .patches import MAIN_MENU_SKIP

ENCOUNTER_ARCHIVE = "a/1/2/7"
TRDATA_ARCHIVE = "a/0/9/1"
TRPOKE_ARCHIVE = "a/0/9/2"
TARGET_GAME_CODE = "IRDO"


def postprocess(rom: Rom, *, settings: dict, key: bytes, table, context: dict) -> dict:
    report = {"plugins": {}, "arm9": {}, "scripts": {}}
    plugin_paths = [p for p in rom.paths() if p.rsplit("/", 1)[-1] == MAIN_MENU_SKIP]
    report["plugins"] = {"main_menu_skip_present": plugin_paths}
    state = arm9_tools.classify(rom.arm9())
    report["arm9"] = {"state": state.state, "detail": state.detail,
                      "field_offset": f"0x{state.params.compressed_end_field_offset:x}", "repaired": False}
    if state.state == "stale":
        rom.write_arm9_word(state.params.compressed_end_field_offset, 0)
        report["arm9"]["repaired"] = True
    if table is None:
        report["scripts"] = {"archive": None, "patches": [], "features": {
            f: "skipped (script-command description unavailable)" for f in ("starter_scripts", "gifts", "statics", "items")},
            "warnings": ["script-command description unavailable"]}
    else:
        result = script_features.apply(rom, table, context, settings, key)
        report["scripts"] = {"archive": result.archive, "patches": result.patches, "features": result.features,
                             "warnings": result.warnings}
    rom.refresh_integrity()
    return report


def _check(checks: list, name: str, ok: bool, detail: str) -> None:
    checks.append({"name": name, "status": "pass" if ok else "fail", "detail": detail})


def verify(rom: Rom, data: W2UData, settings: dict, plan: dict, post: dict) -> dict:
    checks: list[dict] = []
    _check(checks, "game_code", rom.game_code == TARGET_GAME_CODE, f"header game code {rom.game_code}")
    problems = rom.integrity_problems()
    _check(checks, "header_integrity", not problems, "; ".join(problems) or "CRCs and signature digest current")
    state = arm9_tools.classify(rom.arm9())
    _check(checks, "arm9_compression_state", state.bootable, state.detail)
    plugins = [p for p in rom.paths() if p.rsplit("/", 1)[-1] == MAIN_MENU_SKIP]
    _check(checks, "development_plugin_absent", not plugins, f"{len(plugins)} copies of {MAIN_MENU_SKIP}")

    # Encounters: every member must equal the independent encoding of the expected source data.
    try:
        narc = Narc.parse(rom.read_path(ENCOUNTER_ARCHIVE))
        mismatches = []
        species_problems = []
        if len(narc.members) != len(data.encounters):
            mismatches.append(f"{len(narc.members)} members, expected {len(data.encounters)}")
        for index in sorted(data.encounters):
            if index >= len(narc.members):
                break
            expected = encode_encounter_document(data, data.encounters[index])
            if narc.members[index] != expected:
                mismatches.append(f"record {index}")
            for container in enc.decode_record(narc.members[index], f"record {index}"):
                for slots in container.tables.values():
                    for slot in slots:
                        if slot.species and not data.model.is_ordinary(slot.species):
                            species_problems.append((index, slot.species))
        _check(checks, "encounters_match_plan", not mismatches, f"{len(mismatches)} mismatches {mismatches[:5]}")
        _check(checks, "encounter_species_valid", not species_problems, f"{len(species_problems)} invalid species")
        if settings["mode"] == "diagnostic-route19":
            record = plan["route19"]["record"]
            fixture = enc.verify_route19_fixture(narc.members[record])
            _check(checks, "route19_fixture", not fixture,
                   f"record {record}: 48 grass slots, 928 bytes, exact species/form/levels/weights" if not fixture else "; ".join(fixture[:5]))
    except FormatError as error:
        _check(checks, "encounters_decodable", False, str(error))

    # Trainers: class, battle type, party size, species, levels.
    try:
        trdata = Narc.parse(rom.read_path(TRDATA_ARCHIVE))
        trpoke = Narc.parse(rom.read_path(TRPOKE_ARCHIVE))
        mismatches = []
        for index in sorted(data.trainers):
            if index >= len(trdata.members):
                mismatches.append(f"trainer {index} missing")
                continue
            expected_d, expected_p = encode_trainer_document(data, data.trainers[index])
            actual = trn.decode_trdata(trdata.members[index])
            want = trn.decode_trdata(expected_d)
            if (actual.trainer_class, actual.battle_type, actual.party_count) != (want.trainer_class, want.battle_type, want.party_count):
                mismatches.append(f"trainer {index} header")
                continue
            got = trn.decode_trpoke(trpoke.members[index], actual.format, actual.party_count)
            exp = trn.decode_trpoke(expected_p, want.format, want.party_count)
            if [(m.species, m.level) for m in got] != [(m.species, m.level) for m in exp]:
                mismatches.append(f"trainer {index} party")
        _check(checks, "trainers_match_plan", not mismatches, f"{len(mismatches)} mismatches {mismatches[:5]}")
        converted = [d["trainer"] for d in plan.get("doubles", []) if d["converted"]]
        not_double = [t for t in converted if trn.decode_trdata(trdata.members[t]).battle_type != trn.BATTLE_DOUBLE]
        _check(checks, "major_battles_doubles", not not_double, f"{len(converted)} converted, {len(not_double)} not doubles in ROM")
    except FormatError as error:
        _check(checks, "trainers_decodable", False, str(error))

    scripts_report = post.get("scripts", {})
    if scripts_report.get("archive") and scripts_report.get("patches"):
        script_problems = script_features.verify(rom, scripts_report["archive"], scripts_report["patches"])
        _check(checks, "script_patches_present", not script_problems, f"{len(scripts_report['patches'])} patches; {script_problems[:3]}")

    structural_ok = all(c["status"] == "pass" for c in checks)
    return {
        "build_completed": True,
        "structural_verification": "passed" if structural_ok else "failed",
        "runtime_acceptance": "not yet performed",
        "checks": checks,
    }
