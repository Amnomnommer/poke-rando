#!/usr/bin/env bash
# b2er — identify Pokémon ROMs, show capabilities, build deterministic randomized ROMs. See docs/alpha-0.1.md.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -I "$here/scripts/b2er_cli.py" "$@"
