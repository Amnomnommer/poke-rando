#!/usr/bin/env bash
# Launch a generated Alpha 0.1 ROM in melonDS.
#   ./Launch-Alpha-0.1.sh Alpha-0.1-builds/<name>.nds            # play (manual acceptance)
#   ./Launch-Alpha-0.1.sh Alpha-0.1-builds/<name>.nds --smoke 60 # automated boot-log smoke test
#   ./Launch-Alpha-0.1.sh Alpha-0.1-builds/<name>.nds --gdb      # attach GDB to the ARM9 stub (port 3333)
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -I "$here/scripts/launch_alpha.py" "$@"
