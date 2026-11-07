#!/usr/bin/env python3
"""Alpha 0.1 one-command local build. See docs/alpha-0.1.md.

Exit codes: 0 built and structurally verified, 1 build or verification failed, 2 invalid arguments.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from b2er.assembly import BuildError, pipeline  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Pokémon White 2 Expanded Randomizer Alpha 0.1 ROM locally.")
    parser.add_argument("--white2", required=True, type=Path, help="clean White 2 (IRDO) ROM; only read")
    parser.add_argument("--black2", required=True, type=Path, help="clean Black 2 (IREO) ROM; only read")
    parser.add_argument("--w2u", required=True, type=Path, help="pinned, clean W2U-Project checkout with initialized swan; only read")
    parser.add_argument("--seed", required=True, help="randomizer seed (any non-empty text)")
    parser.add_argument("--settings", type=Path, help="settings JSON (defaults are used when omitted)")
    parser.add_argument("--diagnostic-route19", action="store_true",
                        help="build the Patrat/Carbink/Chespin Route 19 diagnostic instead of a randomized ROM")
    parser.add_argument("--output-dir", type=Path, default=pipeline.REPO_ROOT / "Alpha-0.1-builds")
    parser.add_argument("--workroot-base", type=Path, default=pipeline.REPO_ROOT / ".alpha-work")
    parser.add_argument("--keep-workroot", action="store_true", help="keep the disposable workroot after success")
    parser.add_argument("--meson-source", type=Path, help="local Meson git checkout to clone instead of github.com")
    parser.add_argument("--java", help="Java executable for CTRMap (default: $JAVA or java on PATH)")
    parser.add_argument("--jobs", type=int, help="ninja parallelism")
    parser.add_argument("--builder", choices=("w2u", "mock"), default="w2u", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    request = pipeline.BuildRequest(
        white2=args.white2, black2=args.black2, w2u=args.w2u, seed=args.seed, settings_path=args.settings,
        diagnostic=args.diagnostic_route19, output_dir=args.output_dir, workroot_base=args.workroot_base,
        builder=args.builder, keep_workroot=args.keep_workroot, meson_source=args.meson_source, java=args.java, jobs=args.jobs,
    )
    try:
        result = pipeline.run(request)
    except BuildError as error:
        print(f"[alpha] BUILD FAILED: {error}", file=sys.stderr)
        return 1
    print(f"[alpha] done in {result['seconds']} s: {args.output_dir / result['outputs']['rom']}")
    print("[alpha] build completed; structural verification passed; runtime acceptance NOT yet performed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
