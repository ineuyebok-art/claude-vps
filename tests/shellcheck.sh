#!/usr/bin/env bash
# Runs shellcheck over every shell script in the repository.
set -euo pipefail
cd "$(dirname "$0")/.."
mapfile -t files < <(git ls-files | while read -r f; do
  [[ -f "$f" ]] || continue
  case "$f" in
    *.sh) echo "$f" ;;
    *.py|*.ps1|*.md|*.html|*.yml|*.json|*.service|*.timer|*.css|*.js|*.woff2) ;;
    *) head -1 "$f" | grep -qE '^#!.*\b(ba)?sh\b' && echo "$f" ;;
  esac
done)
echo "shellcheck: ${#files[@]} files"
shellcheck -x -S style "${files[@]}"
