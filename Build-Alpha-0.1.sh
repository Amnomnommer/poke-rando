#!/usr/bin/env bash
# White 2 Alpha 0.1 one-command build (wrapper for `./b2er.sh build`).
#   ./Build-Alpha-0.1.sh --white2 cleanwhite2.nds --black2 cleanblack2.nds --w2u W2U-Project --seed SEED --species-max 1023
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -I "$here/scripts/b2er_cli.py" build "$@"
