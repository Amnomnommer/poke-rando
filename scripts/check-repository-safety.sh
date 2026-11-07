#!/usr/bin/env bash
# Repository safety guard: rejects ROMs, saves, firmware, extracted or
# generated game data, and upstream-derived trees from the Git index.
# Policy and rationale: docs/repository-safety.md
set -euo pipefail

if ! command -v git >/dev/null 2>&1; then
  echo "git is required" >&2
  exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 (3.11 or newer) is required" >&2
  exit 2
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$repo_root" ]]; then
  echo "Run this check inside a Git repository." >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -I "$script_dir/repository_safety.py" --repo "$repo_root"
