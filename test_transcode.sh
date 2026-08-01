#!/usr/bin/env bash
# self-check for transcode-video.sh placement logic: groups files by movie, picks
# the lowest _tNN as the main feature, files extras under extras/, sanitizes
# filesystem-hostile chars, and empties the rip dir. stubs mountpoint + HandBrakeCLI
# so it runs with no drive and no encoder. run: bash test_transcode.sh
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$(mktemp -d)"


vid=$PWD/nas; rip=$vid/rip; lib=$vid/library; work=$PWD/work
mkdir -p "$rip" "$lib" "$work"

# fake binary — mount-nfs always claims the share is up.
bin=$PWD/bin; mkdir -p "$bin"
cat > "$bin/mount-nfs" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "$bin/HandBrakeCLI" <<'EOF'
#!/usr/bin/env bash
i= o=
while [ $# -gt 0 ]; do case "$1" in -i) i=$2; shift 2;; -o) o=$2; shift 2;; *) shift;; esac; done
cp "$i" "$o"
EOF
chmod +x "$bin"/*
export PATH="$bin:$PATH"
export VIDEOS_MOUNT=$vid VIDEO_WORK_DIR=$work

# staged lossless MKVs: a 2-title movie, a single-title movie, and a colon to sanitize.
: >"$rip/The Matrix (1999)_t00.mkv"
: >"$rip/The Matrix (1999)_t01.mkv"
: >"$rip/Rogue One (2016)_t02.mkv"
: >"$rip/Movie: Sub (2020)_t00.mkv"

bash "$repo/transcode-video.sh"

check() { [ -e "$1" ] || { echo "FAIL: missing $1"; exit 1; }; }
check "$lib/The Matrix (1999)/The Matrix (1999).mkv"          # main (t00)
check "$lib/The Matrix (1999)/extras/The Matrix (1999)_t01.mkv" # extra
check "$lib/Rogue One (2016)/Rogue One (2016).mkv"            # only title -> main
check "$lib/Movie Sub (2020)/Movie Sub (2020).mkv"            # colon sanitized

left=$(find "$rip" -type f -name '*.mkv' | wc -l)
[ "$left" -eq 0 ] || { echo "FAIL: rip dir not empty ($left left)"; exit 1; }

echo "PASS: placement, grouping, sanitize, cleanup"
