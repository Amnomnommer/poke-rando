#!/usr/bin/env python3
"""Launch a generated Alpha 0.1 ROM in melonDS, optionally as a timed boot-log smoke test or with GDB capture.

Exit codes: 0 launched / smoke found no fault, 1 smoke found a fault or the emulator exited early, 2 setup error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from b2er import emulator  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("rom", type=Path, help="generated ROM (from Alpha-0.1-builds/)")
    parser.add_argument("--smoke", type=int, metavar="SECONDS", help="run for SECONDS, stop, and scan the log for ARM faults")
    parser.add_argument("--gdb", action="store_true",
                        help=f"attach gdb-multiarch to melonDS's ARM9 GDB stub on port {emulator.GDB_PORT} (enable it in melonDS first)")
    parser.add_argument("--gdb-break", action="append", default=[], type=lambda v: int(v, 0), metavar="ADDR",
                        help="with --gdb: log registers and a backtrace each time ADDR executes (repeatable)")
    parser.add_argument("--gdb-probe", choices=["wild-setup"],
                        help="with melonDS's GDB stub: log the W2U wild-battle setup hook's +0x24/+0x34 words before/after it runs")
    parser.add_argument("--emulator", help="emulator command override, e.g. 'flatpak run net.kuribo64.melonDS'")
    parser.add_argument("--log-dir", type=Path, default=REPO_ROOT / "emulator-logs")
    args = parser.parse_args(argv)
    try:
        result = emulator.launch(args.rom, args.log_dir, smoke_seconds=args.smoke, gdb=args.gdb or bool(args.gdb_break) or bool(args.gdb_probe),
                                 emulator=args.emulator, breakpoints=tuple(args.gdb_break), probe=args.gdb_probe)
    except (FileNotFoundError, RuntimeError) as error:
        print(f"launcher: {error}", file=sys.stderr)
        return 2
    if isinstance(result, int):
        return 0 if result == 0 else 1
    print(result.summary())
    for line in result.faults:
        print(f"  fault: {line}")
    return 0 if result.status == "no-fault-observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
