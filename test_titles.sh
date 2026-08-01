#!/usr/bin/env bash
# self-check for titles.sh: parses a captured makemkvcon info dump and confirms
# the per-title audio (with count) and subtitle languages appear. feeds the dump
# via MAKEMKV_INFO so it runs with no drive. run: bash test_titles.sh
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
dump="$repo/makemkv-titles-misc-data.txt"

out=$(MAKEMKV_INFO=cat bash "$repo/titles.sh" "$dump" 2>/dev/null)

# movie (title 0) carries 4 english audio tracks — the count hints at commentary/dups
echo "$out" | grep -q "^title 0 .*A:eng(4)" || { echo "FAIL: movie audio count"; echo "$out"; exit 1; }
# nordic + english subtitles
echo "$out" | grep -q "subs:dan,eng,fin,nor,swe" || { echo "FAIL: subtitle langs"; exit 1; }
# five titles survived the scan
[ "$(printf '%s\n' "$out" | grep -c '^title ')" -eq 5 ] || { echo "FAIL: expected 5 titles"; exit 1; }

echo "PASS: titles shows audio/sub tracks per title"
