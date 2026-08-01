#!/usr/bin/env bash
# self-check for rip-video.sh title-spec expansion: a comma list and range expand
# to one makemkvcon call per title; 'all' is a single call; junk like 'disc:0' or
# a reversed range is rejected before the drive is touched. stubs mountpoint and
# makemkvcon so it runs with no drive. run: bash test_rip_titles.sh
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$(mktemp -d)"
vid=$PWD/nas; mkdir -p "$vid/rip"
bin=$PWD/bin; mkdir -p "$bin"

cat > "$bin/mount-nfs" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
# stub: record the title id (the arg right after disc:0) to calls.txt
cat > "$bin/makemkvcon" <<'EOF'
#!/usr/bin/env bash
prev=
for a in "$@"; do
  [ "$prev" = "disc:0" ] && printf '%s\n' "$a" >> calls.txt
  prev=$a
done
EOF
chmod +x "$bin"/*
export PATH="$bin:$PATH"
export VIDEOS_MOUNT=$vid

run_rip() { rm -f calls.txt; bash "$repo/rip-video.sh" "$@" >/dev/null 2>&1 || true; }
seen() { cat calls.txt 2>/dev/null || true; }

run_rip 0,5-7
[ "$(seen)" = "$(printf '0\n5\n6\n7')" ] || { echo "FAIL list+range -> [$(seen)]"; exit 1; }

run_rip all
[ "$(seen)" = "all" ] || { echo "FAIL all -> [$(seen)]"; exit 1; }

run_rip 3
[ "$(seen)" = "3" ] || { echo "FAIL single -> [$(seen)]"; exit 1; }

run_rip disc:0
[ -z "$(seen)" ] || { echo "FAIL disc:0 should be rejected, got [$(seen)]"; exit 1; }

run_rip 7-3
[ -z "$(seen)" ] || { echo "FAIL 7-3 should be rejected, got [$(seen)]"; exit 1; }

echo "PASS: title-spec expansion (list, range, all, validation)"
