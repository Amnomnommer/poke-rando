#!/usr/bin/env python3
"""b2er: identify Pokémon ROMs, list adapter capabilities, and build deterministic randomized ROMs.

    b2er identify ROM [--json]
    b2er capabilities [--json]
    b2er build --rom ROM --seed SEED [--black2 ROM --w2u DIR] [--settings FILE] [--species-max N] [--diagnostic-route19]

Exit codes: 0 success, 1 refused/failed (nothing emitted), 2 invalid arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from b2er import identify, pipeline  # noqa: E402
from b2er.adapters import default_registry  # noqa: E402
from b2er.assembly import BuildError  # noqa: E402


def cmd_identify(args) -> int:
    identity = identify.identify(args.rom, adapters=default_registry(), pins=args.pins_dir / "inputs.json")
    report = identity.to_json()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(f"status:    {report['status']}")
        print(f"platform:  {report['container'] or 'unknown'}")
        if report["title"]:
            print(f"title:     {report['title']} (Generation {report['generation']}; paired with {report['paired_version']})")
            print(f"region:    {report['region']}  game code {report['game_code']}  revision {report['revision']}")
        print(f"sha256:    {report['sha256']}  ({report['size_bytes']} bytes)")
        print(f"adapter:   {report['adapter'] or 'none'}" + (f" ({report['adapter_confidence']})" if report["adapter"] else ""))
        for line in report["evidence"]:
            print(f"evidence:  {line}")
        for line in report["diagnostics"]:
            print(f"note:      {line}")
    return 0 if identity.status == identify.CLEAN_KNOWN and identity.adapter else 1


def cmd_capabilities(args) -> int:
    matrix = default_registry().matrix()
    if args.json:
        print(json.dumps(matrix, indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    for adapter_id, info in matrix.items():
        print(f"{adapter_id}: {info['display_name']} (Generation {info['generation']}, adapter {info['version']})")
        for cap, support in sorted(info["capabilities"].items()):
            limit = f" {support['limit']}" if support.get("limit") else ""
            print(f"  {support['level']:12} {cap}{limit} — {support['evidence']}")
    return 0


def cmd_build(args) -> int:
    auxiliary = {k: v for k, v in (("black2", args.black2), ("w2u", args.w2u)) if v is not None}
    request = pipeline.BuildRequest(
        rom=args.rom, seed=args.seed, auxiliary=auxiliary, settings_path=args.settings, diagnostic=args.diagnostic_route19,
        species_max=args.species_max, output_dir=args.output_dir, workroot_base=args.workroot_base,
        keep_workroot=args.keep_workroot, pins_dir=args.pins_dir,
        options={"builder": args.builder, "meson_source": args.meson_source, "java": args.java, "jobs": args.jobs},
    )
    try:
        result = pipeline.run(request)
    except BuildError as error:
        print(f"[b2er] REFUSED/FAILED: {error}", file=sys.stderr)
        return 1
    print(f"[b2er] done in {result['seconds']} s: {Path(args.output_dir) / result['outputs']['rom']}")
    print("[b2er] build completed; structural verification passed; runtime acceptance NOT yet performed")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="b2er", description=__doc__.splitlines()[0])
    parser.add_argument("--pins-dir", type=Path, default=REPO_ROOT / "pins", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("identify", help="identify a ROM (read-only)")
    p.add_argument("rom", type=Path)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_identify)
    p = sub.add_parser("capabilities", help="show every adapter's capability matrix")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_capabilities)
    p = sub.add_parser("build", help="build a randomized ROM")
    p.add_argument("--rom", "--white2", dest="rom", required=True, type=Path, help="clean input ROM (only read)")
    p.add_argument("--seed", required=True)
    p.add_argument("--black2", type=Path, help="clean Black 2 ROM (White 2 adapter)")
    p.add_argument("--w2u", type=Path, help="pinned W2U-Project checkout (White 2 adapter)")
    p.add_argument("--settings", type=Path)
    p.add_argument("--species-max", type=int, help="explicitly request a lower National Dex limit (recorded in the manifest)")
    p.add_argument("--diagnostic-route19", action="store_true")
    p.add_argument("--output-dir", type=Path, default=pipeline.REPO_ROOT / "Alpha-0.1-builds")
    p.add_argument("--workroot-base", type=Path, default=pipeline.REPO_ROOT / ".alpha-work")
    p.add_argument("--keep-workroot", action="store_true")
    p.add_argument("--meson-source", type=Path)
    p.add_argument("--java")
    p.add_argument("--jobs", type=int)
    p.add_argument("--builder", choices=("w2u", "mock"), default="w2u", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_build)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
