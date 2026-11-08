"""Universal deterministic transformation pipeline (adapter-driven).

identify -> validate -> snapshot inputs -> select adapter -> static capability
check -> isolated workspace -> extract -> normalized model -> dynamic capability
check -> deterministic plan + obtainability/progression checks -> apply ->
rebuild -> post-process -> structural/semantic verification -> inputs unchanged
-> ROM + reproducibility manifest. Unsupported requests are refused before any
workspace or output exists.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import capabilities as caps, coverage, identify
from .adapters import BuildContext, default_registry
from .assembly import BuildError, reports, workroot
from .gamedata.trainer_codec import TrainerFieldError
from .randomizer import policy, settings as settings_mod

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_VERSION = "0.1.0-alpha.1"


@dataclass
class BuildRequest:
    rom: Path
    seed: str
    auxiliary: dict[str, Path] = field(default_factory=dict)  # e.g. {"black2": ..., "w2u": ...}
    settings_path: Path | None = None
    settings_override: dict | None = None
    diagnostic: bool = False
    species_max: int | None = None
    output_dir: Path = REPO_ROOT / "Alpha-0.1-builds"
    workroot_base: Path = REPO_ROOT / ".alpha-work"
    keep_workroot: bool = False
    pins_dir: Path = REPO_ROOT / "pins"
    options: dict = field(default_factory=dict)  # adapter options: builder, meson_source, java, jobs, patch_pins


def file_snapshot(path: Path) -> dict:
    info = os.stat(path)
    return {"sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(), "size": info.st_size,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns, "inode": info.st_ino, "device": info.st_dev}


def project_revision() -> dict:
    def git(*args):
        r = subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain", "--untracked-files=no"))}


def load_settings(req: BuildRequest) -> dict:
    raw = req.settings_override or {}
    if req.settings_path:
        raw = json.loads(Path(req.settings_path).read_text(encoding="utf-8"))
    if req.diagnostic:
        return settings_mod.diagnostic_settings(req.species_max or raw.get("species", {}).get("national_max", 1025))
    if req.species_max is not None:
        raw = {**raw, "species": {**raw.get("species", {}), "national_max": req.species_max}}
    return settings_mod.normalize(raw)


def run(req: BuildRequest) -> dict:
    started = time.monotonic()
    registry = default_registry()
    claim_problems = coverage.audit(registry)
    if claim_problems:
        raise BuildError("coverage registry claims are not backed by adapter capabilities: " + "; ".join(claim_problems))
    problems: list[str] = []
    if not req.seed:
        problems.append("--seed must be a non-empty string")
    try:
        settings = load_settings(req)
    except (settings_mod.SettingsError, OSError, json.JSONDecodeError) as error:
        problems.append(str(error))
        settings = None
    pins_inputs = req.pins_dir / "inputs.json"
    try:
        identity = identify.identify(req.rom, adapters=registry, pins=pins_inputs)
    except OSError as error:
        raise BuildError(f"cannot read ROM: {error}") from None
    support = (identity.release or {}).get("support_status", "unknown")
    if identity.status != identify.CLEAN_KNOWN or identity.adapter is None or \
            not caps.SupportStatus(support).at_least(caps.SupportStatus.BUILDABLE):
        raise BuildError(f"ROM not buildable: status={identity.status}, support={support}; "
                         + "; ".join(identity.diagnostics or ["the identified release is recognized only"]))
    adapter = registry.get(identity.adapter)
    aux_identities = {}
    unexpected = sorted(set(req.auxiliary) - set(adapter.auxiliary_inputs) - set(adapter.optional_inputs))
    if unexpected:
        problems.append(f"{adapter.id} does not take inputs {unexpected}")
    for name in list(adapter.auxiliary_inputs) + list(adapter.optional_inputs):
        path = req.auxiliary.get(name)
        if path is None:
            if name in adapter.auxiliary_inputs:
                problems.append(f"missing required input --{name} ({adapter.auxiliary_inputs[name]})")
        elif Path(path).is_file():
            aux = identify.identify(path, adapters=registry, pins=pins_inputs)
            aux_identities[name] = aux
            if aux.status != identify.CLEAN_KNOWN:
                problems.append(f"auxiliary {name}: status={aux.status}; " + "; ".join(aux.diagnostics))
    if settings is not None:
        problems += caps.check(adapter.capabilities(), settings_mod.requested_capabilities(settings))
    options = {**req.options, "pins_dir": req.pins_dir,
               "pins_upstream": json.loads((req.pins_dir / "upstream.json").read_text(encoding="utf-8"))}
    problems += adapter.preflight({"auxiliary": req.auxiliary, "options": options})
    if problems:
        raise BuildError("refused before any modification:\n  - " + "\n  - ".join(problems))

    input_files = [Path(req.rom)] + [Path(p) for p in req.auxiliary.values() if Path(p).is_file()]
    snapshot_before = {str(i): file_snapshot(p) for i, p in enumerate(input_files)}
    provenance = {
        "input_sha256": identity.sha256,
        "auxiliary_sha256": {k: v.sha256 for k, v in sorted(aux_identities.items())},
        "pins_sha256": {n: hashlib.sha256((req.pins_dir / n).read_bytes()).hexdigest() for n in ("inputs.json", "upstream.json")},
        "project": project_revision(),
        "adapter": {"id": adapter.id, "version": adapter.version},
        "tool_version": TOOL_VERSION,
        "builder": req.options.get("builder", "w2u"),
    }
    build_key = settings_mod.build_key(settings, req.seed, provenance)
    key = bytes.fromhex(settings_mod.seed_key(req.seed, provenance))
    label = f"{adapter.id.replace('.', '-')}-" + ("diagnostic-" if settings["mode"] == "diagnostic-route19" else "") + build_key[:12]
    root = workroot.create(req.workroot_base, label)
    ctx = BuildContext(root, Path(req.rom), {k: Path(v) for k, v in req.auxiliary.items()}, options, settings, key)
    ctx.say(f"identified {identity.fingerprint['title']} ({identity.status}, adapter {adapter.id}); build key {build_key[:16]}...")
    succeeded = False
    try:
        prepared = adapter.prepare(ctx)
        try:
            model = adapter.read_model(ctx)
        except TrainerFieldError as error:
            raise BuildError(f"REFUSED: invalid trainer source: {error}") from None
        dynamic = adapter.dynamic_capabilities(ctx, model)
        refused = caps.check(dynamic, settings_mod.requested_capabilities(settings))
        if refused:
            raise BuildError("requested features unavailable for this ROM (nothing was emitted):\n  - " + "\n  - ".join(refused))
        diagnostic = adapter.resolve_diagnostic_area(model, ctx) if settings["mode"] == "diagnostic-route19" else None
        try:
            planned, plan_obj = policy.Planner(model, settings, key, diagnostic).run()
        except policy.PlanError as error:
            raise BuildError(f"no safe plan: {error}") from None
        plan = plan_obj.to_json()
        applied = adapter.apply(ctx, planned, plan)
        rom = adapter.rebuild(ctx)
        rom, post = adapter.postprocess(ctx, rom, planned, plan)
        verification = run_verifier(ctx, adapter, rom, planned, plan, post)
        if verification["structural_verification"] != "passed":
            failed = [c for c in verification["checks"] if c["status"] == "fail"]
            raise BuildError("verification failed:\n  " + "\n  ".join(f"{c['name']}: {c['detail']}" for c in failed))
        snapshot_after = {str(i): file_snapshot(p) for i, p in enumerate(input_files)}
        if snapshot_after != snapshot_before:
            raise BuildError("an input changed during the build")
        output_sha = hashlib.sha256(rom).hexdigest()
        experimental = sorted(c.value for c in settings_mod.requested_capabilities(settings)
                              if dynamic[c].level == caps.Level.EXPERIMENTAL)
        manifest = {
            "schema_version": 2,
            "tool": {"name": "b2er", "version": TOOL_VERSION, "dependencies": adapter.tool_versions(ctx)},
            "identity": {k: v for k, v in identity.to_json().items() if k not in ("diagnostics",)},
            "adapter": {"id": adapter.id, "version": adapter.version, "family": getattr(adapter, "family_id", None),
                        "species_codec": planned.codec.describe()},
            "support_status": identity.release["support_status"],
            "build_key": build_key,
            "seed": req.seed,
            "settings": settings,
            "provenance": provenance,
            "capabilities": {c.value: dynamic[c].to_json() for c in settings_mod.requested_capabilities(settings)},
            "experimental_capabilities": experimental,
            "unsupported_capabilities": sorted(c.value for c, s in dynamic.items() if s.level == caps.Level.UNSUPPORTED),
            "transformations": {"prepare": {k: v for k, v in prepared.items() if k in ("main_menu_skip", "pmc_arm9", "posix_shell", "rombuilder_guard", "narc_premerge", "base", "ctrmap_dsi")},
                                "source_changes": applied, "features": plan["features"],
                                "script_patches": len(post.get("script_patches", [])), "arm9": post.get("arm9")},
            "output": {"file": f"{label}.nds", "sha256": output_sha, "size_bytes": len(rom)},
            "verification": {k: verification[k] for k in ("build_completed", "structural_verification", "runtime_acceptance")},
            "warnings": [f"{c}: experimental" for c in experimental] + [f"species limited to #1..#{settings['species']['national_max']} by request"
                                                                       if settings["species"]["national_max"] < 1025 else ""],
        }
        manifest["warnings"] = [w for w in manifest["warnings"] if w]
        documents = {"manifest.json": manifest, "plan.json": {**plan, "script_patches": post.get("script_patches", [])},
                     "settings.json": settings, "verification.json": verification,
                     "spoiler.txt": spoiler(plan, settings, req.seed, build_key)}
        forbidden = [str(Path(p).resolve()) for p in [req.rom, *req.auxiliary.values(), req.output_dir, req.workroot_base, root]]
        for name, doc in documents.items():
            if isinstance(doc, str):
                if any(f in doc for f in forbidden):
                    raise BuildError(f"{name} would contain a local path")
            else:
                reports.assert_sanitized(doc, forbidden)
        written = reports.write_outputs(Path(req.output_dir), label, rom, documents)
        ctx.say(f"wrote {written['rom']} sha256={output_sha}; structural verification passed; runtime acceptance not yet performed")
        succeeded = True
        return {"label": label, "build_key": build_key, "sha256": output_sha, "outputs": written, "manifest": manifest,
                "verification": verification, "plan": plan, "seconds": round(time.monotonic() - started, 1)}
    finally:
        if succeeded and not req.keep_workroot:
            workroot.remove(root, Path(req.workroot_base))
        elif not succeeded:
            print(f"[b2er] workroot kept for diagnosis: {root} (local game data; never commit it)", flush=True)


VERIFIER_LOG = "verifier-traceback.log"


def run_verifier(ctx, adapter, rom: bytes, planned, plan: dict, post: dict) -> dict:
    """Semantic verification; an unexpected exception becomes a clean FAILED diagnostic, traceback kept locally."""
    try:
        return adapter.verify(ctx, rom, planned, plan, post)
    except BuildError:
        raise
    except Exception as error:  # noqa: BLE001 - every verifier crash must withhold outputs, not leak a traceback
        (Path(ctx.workspace) / VERIFIER_LOG).write_text(traceback.format_exc(), encoding="utf-8")
        raise BuildError(f"FAILED: semantic verification could not complete ({type(error).__name__}: {error}); "
                         f"no outputs were published; traceback in the kept workroot's {VERIFIER_LOG}") from None


def spoiler(plan: dict, settings: dict, seed: str, build_key: str) -> str:
    lines = ["Pokémon expanded randomizer — spoiler log", "", f"Seed: {seed}", f"Build key: {build_key}",
             f"Mode: {settings['mode']}", "", "Features:"]
    lines += [f"  {k}: {v}" for k, v in sorted(plan["features"].items())]
    if plan.get("starters", {}).get("names"):
        lines += ["", "Starters: " + ", ".join(plan["starters"]["names"])]
    if plan.get("diagnostic"):
        d = plan["diagnostic"]
        lines += ["", f"Diagnostic area {d['area']} ({d['evidence']['rule']}):"]
        lines += [f"  {f['name']} {f['species']} level {f['level']} weight {f['weight']}%" for f in d["fixture"]]
    for title, key, fmt in (("Evolution changes", "evolution_changes", lambda c: f"{c['name']}: " + "; ".join(c["reasons"])),
                            ("Wild areas", "wild", lambda w: f"area {w['area']}: " + ", ".join(f"{a}->{b}" for a, b in w["mapping"].items())),
                            ("Major battles", "doubles", lambda d: f"trainer {d['trainer']} {d['name']}: "
                             + ("Doubles" if d["converted"] else f"kept Singles ({d['reason']})")),
                            ("Trainers", "trainers", lambda t: f"{t['trainer']} {t['class']} {t['name']} [{t['format']}]: "
                             + ", ".join(f"{p['name']} L{p['level']}" for p in t["party"])),
                            ("Pokémon sites", "sites", lambda s: f"{s['kind']} {s['site']}: {s['before']} -> {s['after']}"),
                            ("Items", "items", lambda i: f"{i['site']}: {i['before']} -> {i['after']}")):
        if plan.get(key):
            lines += ["", f"{title}:"] + [f"  {fmt(x)}" for x in plan[key]]
    lines += ["", "Checks:"] + [f"  [{c['status']}] {c['name']}: {c['detail']}" for c in plan["checks"]]
    return "\n".join(lines) + "\n"
