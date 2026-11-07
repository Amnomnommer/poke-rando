"""Alpha 0.1 one-command build orchestration.

Order: aggregate preflight -> unique workroot -> stage pinned W2U and swan ->
copy clean ROMs into the workroot -> extract the base file system -> project
patches -> deterministic source-level generation -> assembly -> post-build
processing -> structural/semantic verification -> clean-input re-verification
-> local outputs -> workroot cleanup.

Source ROMs and the source W2U checkout are only ever read.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import rom_inputs, w2u_checkout
from ..nds.rom import Rom
from ..randomizer import engine, settings as settings_mod
from ..w2u_data import W2UData
from . import BuildError, builders, patches, postbuild, prereqs, reports, stage, workroot, w2u_sdk

REPO_ROOT = Path(__file__).resolve().parents[2]
ALPHA_VERSION = "0.1.0-alpha.1"


@dataclass
class BuildRequest:
    white2: Path
    black2: Path
    w2u: Path
    seed: str
    settings_path: Path | None = None
    diagnostic: bool = False
    output_dir: Path = REPO_ROOT / "Alpha-0.1-builds"
    workroot_base: Path = REPO_ROOT / ".alpha-work"
    builder: str = "w2u"
    keep_workroot: bool = False
    pins_dir: Path = REPO_ROOT / "pins"
    patch_pins: dict | None = None  # tests only: pre-image hashes for synthetic checkouts
    meson_source: Path | None = None
    java: str | None = None
    jobs: int | None = None
    log: list[str] = field(default_factory=list)

    def say(self, message: str) -> None:
        self.log.append(message)
        print(f"[alpha] {message}", flush=True)


def project_revision() -> dict:
    def git(*args):
        result = subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None
    head = git("rev-parse", "HEAD")
    dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    return {"commit": head, "dirty": dirty}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preflight(req: BuildRequest) -> tuple[dict, dict, rom_inputs.InputVerification]:
    problems: list[str] = []
    if not req.seed:
        problems.append("--seed must be a non-empty string")
    raw_settings = {}
    if req.settings_path is not None:
        try:
            raw_settings = json.loads(Path(req.settings_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            problems.append(f"settings file: {error}")
    try:
        settings = settings_mod.diagnostic_settings() if req.diagnostic else settings_mod.normalize(raw_settings)
    except settings_mod.SettingsError as error:
        problems.append(str(error))
        settings = None
    inputs = None
    try:
        inputs = rom_inputs.verify_inputs(req.white2, req.black2, req.pins_dir / "inputs.json")
        for defect in inputs.defects:
            problems.append(f"clean ROM {defect['role']}: {defect['category']}: {defect['detail']}")
    except rom_inputs.OperationalError as error:
        problems.append(f"clean ROM inputs: {error}")
    w2u_report = None
    try:
        w2u_report = w2u_checkout.verify(Path(req.w2u), req.pins_dir / "upstream.json")
        for defect in w2u_report["defects"]:
            problems.append(f"W2U checkout: {defect['category']}: {defect['subject']}: {defect['detail']}")
        if w2u_report["checks"].get("swan_checkout", {}).get("status") != "pass":
            problems.append("W2U checkout: include/swan must be initialized at the pinned commit (git submodule update --init)")
    except w2u_checkout.OperationalError as error:
        problems.append(f"W2U checkout: {error}")
    if req.builder == "w2u":
        for item in prereqs.check_w2u_host(java=req.java):
            problems.append(f"host prerequisite missing: {item.item} ({item.hint})")
    elif req.builder != "mock":
        problems.append(f"unknown builder {req.builder!r}")
    if problems:
        raise BuildError("preflight failed; nothing was built:\n  - " + "\n  - ".join(problems))
    return settings, w2u_report, inputs


def run(req: BuildRequest) -> dict:
    started = time.monotonic()
    settings, w2u_report, inputs_before = preflight(req)
    pins_upstream = json.loads((req.pins_dir / "upstream.json").read_text(encoding="utf-8"))
    swan_pin = next(d for d in pins_upstream["source_dependencies"] if d["id"] == "swan")
    meson_pin = next((d for d in pins_upstream["source_dependencies"] if d["id"] == "meson"), {})
    provenance = {
        "white2_sha256": inputs_before.inputs[0].sha256,
        "black2_sha256": inputs_before.inputs[1].sha256,
        "w2u_commit": pins_upstream["upstream"]["commit"],
        "swan_commit": swan_pin["commit"],
        "pins_sha256": {name: file_sha256(req.pins_dir / name) for name in ("inputs.json", "upstream.json")},
        "project": project_revision(),
        "builder": req.builder,
        "alpha_version": ALPHA_VERSION,
    }
    build_key = settings_mod.build_key(settings, req.seed, provenance)
    key_bytes = bytes.fromhex(build_key)
    label = "W2ER-Alpha-0.1-" + ("Route19-Diagnostic-" if settings["mode"] == "diagnostic-route19" else "") + build_key[:12]
    req.say(f"build key {build_key}")
    root = workroot.create(req.workroot_base, label)
    req.say(f"workroot {root.name} (disposable)")
    succeeded = False
    try:
        w2u = root / "W2U-Project"
        staged = stage.stage_w2u(Path(req.w2u), w2u, swan_pin["path"])
        req.say(f"staged W2U ({staged['files']} files) and swan ({staged['swan_files']} files)")
        port = root / "Port-Pokeweb"
        port.mkdir()
        for source, name in ((req.white2, "cleanwhite2.nds"), (req.black2, "cleanblack2.nds")):
            target = port / name
            shutil.copyfile(source, target)
            target.chmod(stat.S_IRUSR | stat.S_IRGRP)
        base_info = stage.extract_base((port / "cleanwhite2.nds").read_bytes(), root / "IRDO_Extracted")
        req.say(f"extracted base file system ({base_info['files']} files, {base_info['overlays']} overlays)")
        pins = req.patch_pins or {}
        patch_report = {
            "main_menu_skip": patches.remove_main_menu_skip(w2u, pins.get("meson_build", patches.MESON_BUILD_SHA256),
                                                            pins.get("main_menu_skip", patches.MAIN_MENU_SKIP_SHA256)),
            "pmc_arm9": patches.repair_pmc_arm9(w2u, pins.get("pmc_arm9", patches.PMC_ARM9_SHA256)),
            "cmproj": patches.write_cmproj(w2u, pins.get("cmproj_example", patches.CMPROJ_EXAMPLE_SHA256)),
        }
        req.say(f"patches: removed {len(patch_report['main_menu_skip']['removed_files'])} plugin copies; "
                f"ARM9 {patch_report['pmc_arm9']['state_before']} -> {patch_report['pmc_arm9']['state_after']}")

        data = W2UData.load(w2u)
        generator = engine.Generator(data, settings, key_bytes)
        plan = generator.run().to_json()
        plan["species_model"] = data.model.describe()
        plan["label_mismatches"] = data.label_mismatches
        req.say("generation: " + ", ".join(f"{k}={v}" for k, v in sorted(plan["features"].items())))

        builder = (builders.MockBuilder() if req.builder == "mock"
                   else builders.W2UMesonBuilder(meson_pin.get("commit"), req.meson_source, req.java, req.jobs))
        req.say(f"assembling with {builder.name} (this can take a long time)")
        built = builder.build(root, w2u)
        rom = Rom(built.read_bytes())

        try:
            table = w2u_sdk.load_table(w2u)
        except BuildError as error:
            table = None
            plan.setdefault("warnings", []).append(str(error))
        vanilla_starters = [data.species_id(s) for s in engine.VANILLA_STARTERS]
        context = {
            "vanilla_starters": vanilla_starters,
            "starters": plan.get("starters", {}).get("chosen", vanilla_starters),
            "obtainable": plan.get("obtainable") or sorted(s for s in data.personal if data.model.encounter_encodable(s)),
            "pick_similar": lambda stream, original, pool, level: generator.pick_similar(stream, original, pool, level, similar=True),
            "is_ordinary": data.model.is_ordinary,
        }
        post = postbuild.postprocess(rom, settings=settings, key=key_bytes, table=table, context=context)
        if post["plugins"]["main_menu_skip_present"]:
            raise BuildError(f"finished ROM still contains {patches.MAIN_MENU_SKIP}")
        if settings["unsupported_feature_policy"] == "fail":
            skipped = {k: v for k, v in post["scripts"]["features"].items() if str(v).startswith("skipped")}
            if skipped:
                raise BuildError(f"features could not be applied and policy is 'fail': {skipped}")
        verification = postbuild.verify(rom, data, settings, plan, post)
        verification["features"] = {**plan["features"], **post["scripts"]["features"]}
        verification["arm9"] = post["arm9"]
        if verification["structural_verification"] != "passed":
            failed = [c for c in verification["checks"] if c["status"] == "fail"]
            raise BuildError("structural verification failed:\n  " + "\n  ".join(f"{c['name']}: {c['detail']}" for c in failed))

        inputs_after = rom_inputs.verify_inputs(req.white2, req.black2, req.pins_dir / "inputs.json")
        changes = rom_inputs.compare_fingerprints(inputs_before, inputs_after)
        if changes or not inputs_after.verified:
            raise BuildError(f"clean ROM inputs changed during the build: {changes}")

        rom_bytes = bytes(rom.data)
        output_sha = hashlib.sha256(rom_bytes).hexdigest()
        manifest = {
            "schema_version": reports.MANIFEST_SCHEMA_VERSION,
            "product": "Pokémon White 2 Expanded Randomizer",
            "version": ALPHA_VERSION,
            "label": label,
            "build_key": build_key,
            "seed_sha256": hashlib.sha256(req.seed.encode("utf-8")).hexdigest(),
            "settings": settings,
            "provenance": provenance,
            "patches": patch_report,
            "output": {"file": f"{label}.nds", "sha256": output_sha, "size_bytes": len(rom_bytes)},
            "features": verification["features"],
            "species_model": plan["species_model"],
            "verification": {k: verification[k] for k in ("build_completed", "structural_verification", "runtime_acceptance")},
            "reproducibility": "identical inputs, project revision, pins, settings, and seed reproduce this build key; "
                               "the output hash is reproducible when the assembly toolchain is deterministic",
        }
        documents = {
            "manifest.json": manifest,
            "plan.json": {**plan, "script_patches": post["scripts"]["patches"]},
            "settings.json": settings,
            "verification.json": verification,
            "spoiler.txt": reports.spoiler(plan, settings, req.seed, build_key, post["scripts"]),
        }
        forbidden = [str(Path(p).resolve()) for p in (req.white2, req.black2, req.w2u, req.output_dir, req.workroot_base, root)]
        for name, document in documents.items():
            if not isinstance(document, str):
                reports.assert_sanitized(document, forbidden)
            elif any(f in document for f in forbidden):
                raise BuildError(f"{name} would contain a local path")
        written = reports.write_outputs(Path(req.output_dir), label, rom_bytes, documents)
        req.say(f"wrote {written['rom']} sha256={output_sha}")
        req.say("structural verification passed; runtime acceptance not yet performed")
        succeeded = True
        return {"label": label, "build_key": build_key, "sha256": output_sha, "outputs": written,
                "manifest": manifest, "verification": verification, "seconds": round(time.monotonic() - started, 1)}
    finally:
        if succeeded and not req.keep_workroot:
            workroot.remove(root, Path(req.workroot_base))
        elif not succeeded:
            req.say(f"workroot kept for diagnosis: {root} (contains local game data; never commit it)")
