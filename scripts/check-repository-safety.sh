#!/usr/bin/env bash
set -euo pipefail

if ! command -v git >/dev/null 2>&1; then
  echo "git is required" >&2
  exit 2
fi

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$repo_root" ]]; then
  echo "Run this check inside a Git repository." >&2
  exit 2
fi

cd "$repo_root"

forbidden='\.(nds|srl|sav|dsv|duc|mch|ml[1-8]|state|bios|firmware|nand|key|keys|narc|ncgr|nclr|nscr|ncer|nanr|nftr|sdat|sseq|swar|strm|overlay|core|dll|rpm)$|(^|/)(header|arm7|arm9|y7|y9|banner)\.bin$|(^|/)(IRDO_Extracted|IREO_Extracted|NitroFS|nitrofs|tester/(input|cache|output|results)|build-[^/]*|build|output|outputs|cache|temp|tmp|user-data)(/|$)'

violations="$(git ls-files | grep -E -i "$forbidden" || true)"

if [[ -n "$violations" ]]; then
  echo "Repository safety check failed. Forbidden tracked paths:" >&2
  while IFS= read -r path; do
    printf '  %s\n' "$path" >&2
  done <<< "$violations"
  exit 1
fi

echo "Repository safety check passed."
