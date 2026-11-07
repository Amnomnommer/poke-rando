#!/usr/bin/env python3
"""Verify a user-supplied local W2U checkout against pins/upstream.json.

Read-only and offline. See docs/w2u-checkout-verifier.md.

Exit codes: 0 verified, 1 verification failure, 2 operational error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from b2er import w2u_checkout  # noqa: E402


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # invalid arguments -> exit 2
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(w2u_checkout.EXIT_OPERATIONAL_ERROR)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description="Read-only, offline verification of a local W2U checkout.")
    parser.add_argument("checkout", type=Path, help="path to the top level of the local W2U checkout")
    parser.add_argument(
        "--pins",
        type=Path,
        default=REPO_ROOT / "pins" / "upstream.json",
        help="pin file to verify against (default: pins/upstream.json)",
    )
    parser.add_argument("--json", action="store_true", help="emit the machine-readable JSON report")
    args = parser.parse_args(argv)

    try:
        report = w2u_checkout.verify(args.checkout, args.pins)
    except w2u_checkout.OperationalError as error:
        report = w2u_checkout.error_report(str(error))

    output = w2u_checkout.render_json(report) if args.json else w2u_checkout.render_text(report)
    stream = sys.stderr if report["result"] == "error" and not args.json else sys.stdout
    stream.write(output)
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
