#!/usr/bin/env python3
"""List candidate Egg/Bad Egg (1024/1025) and species-limit sites in a local ARM9 image.

Example (inside your own W2U checkout; output stays local):
    python3 -I scripts/scan_species_sentinels.py --arm9 /path/to/W2U-Project/pmc/ARM9PMC.bin \
        --symbols /path/to/W2U-Project/pmc/IRDO.yml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from b2er import sentinels  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arm9", required=True, type=Path)
    parser.add_argument("--symbols", type=Path)
    parser.add_argument("--base", type=lambda v: int(v, 0), default=0x02000000)
    args = parser.parse_args(argv)
    symbols = sentinels.load_symbols(args.symbols.read_text(encoding="utf-8")) if args.symbols else []
    print(sentinels.report(sentinels.scan(args.arm9.read_bytes(), args.base, symbols)), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
