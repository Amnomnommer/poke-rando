#!/usr/bin/env python3
"""Verify the two user-supplied clean ROM inputs against pins/inputs.json.

Read-only and offline. Both paths are required; nothing is discovered.
See docs/rom-input-verifier.md.

Exit codes: 0 both inputs verified, 1 verification failure, 2 operational error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from b2er import rom_inputs  # noqa: E402


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # invalid arguments -> exit 2
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(rom_inputs.EXIT_OPERATIONAL_ERROR)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description="Read-only verification of the clean White 2 and Black 2 ROM inputs.")
    parser.add_argument("--white2", required=True, type=Path, help="path to the clean White 2 (IRDO) ROM")
    parser.add_argument("--black2", required=True, type=Path, help="path to the clean Black 2 (IREO) ROM")
    parser.add_argument("--pins", type=Path, default=REPO_ROOT / "pins" / "inputs.json",
                        help="pin file to verify against (default: pins/inputs.json)")
    parser.add_argument("--json", action="store_true", help="emit the machine-readable JSON report")
    args = parser.parse_args(argv)

    try:
        report = rom_inputs.verify_inputs(args.white2, args.black2, args.pins).report()
    except rom_inputs.OperationalError as error:
        report = rom_inputs.error_report(str(error))

    output = rom_inputs.render_json(report) if args.json else rom_inputs.render_text(report)
    (sys.stderr if report["result"] == "error" and not args.json else sys.stdout).write(output)
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
