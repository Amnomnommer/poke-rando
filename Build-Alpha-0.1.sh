#!/usr/bin/env bash
# White 2 Alpha 0.1 one-command build (wrapper for `./b2er.sh build`). Black 2 is not required.
#   ./Build-Alpha-0.1.sh /path/to/cleanwhite2.nds --w2u /path/to/W2U-Project --seed SEED --species-max 1023 [--diagnostic-route19]
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -I "$here/scripts/b2er_cli.py" build "$@"
