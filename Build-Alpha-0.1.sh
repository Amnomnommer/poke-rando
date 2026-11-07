#!/usr/bin/env bash
# Pokémon White 2 Expanded Randomizer — Alpha 0.1 local build.
#
#   ./Build-Alpha-0.1.sh --white2 /path/to/cleanwhite2.nds --black2 /path/to/cleanblack2.nds \
#                        --w2u /path/to/W2U-Project --seed "example-seed" [--settings settings.json]
#
# Add --diagnostic-route19 for the Patrat/Carbink/Chespin isolation build.
# See docs/alpha-0.1.md. Clean ROMs and the W2U checkout are only read.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -I "$here/scripts/build_alpha.py" "$@"
