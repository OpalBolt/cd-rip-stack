#!/usr/bin/env bash
# self-check for sync-music.sh: moves a beets-style library tree to the NAS
# target verbatim, removes the source (files + empty dirs), and logs one line per
# folder plus a total. stubs mount-nfs (no real NFS/sudo) and uses real rsync.
# isolates HOME so the real beets library is never touched. run: bash test_sync_music.sh
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$(mktemp -d)"

home=$PWD/home
mnt=$PWD/nas/music
src=$home/media/audio/music/library
mkdir -p "$src/Radiohead/1997 OK Computer" "$src/_Compilations/2021 Now"
echo flac > "$src/Radiohead/1997 OK Computer/01 - Airbag.flac"
echo flac > "$src/_Compilations/2021 Now/01 - Track.flac"

# fake mount-nfs: claim the share is up without touching sudo or the network.
bin=$PWD/bin; mkdir -p "$bin"
cat > "$bin/mount-nfs" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$bin/mount-nfs"
export PATH="$bin:$PATH"
export HOME=$home MUSIC_MOUNT=$mnt NFS_MUSIC=stub:/x

bash "$repo/sync-music.sh"

check() { [ -e "$1" ] || { echo "FAIL: missing $1"; exit 1; }; }
check "$mnt/library/Radiohead/1997 OK Computer/01 - Airbag.flac"
check "$mnt/library/_Compilations/2021 Now/01 - Track.flac"

left=$(find "$src" -type f | wc -l)
[ "$left" -eq 0 ] || { echo "FAIL: source not emptied ($left files left)"; exit 1; }
[ ! -d "$src/Radiohead" ] || { echo "FAIL: empty source dir left behind"; exit 1; }

lines=$(wc -l < rip.log)
[ "$lines" -eq 3 ] || { echo "FAIL: expected 3 log lines (2 move + 1 total), got $lines"; exit 1; }

echo "PASS: music moved verbatim, source removed, one log line per folder"
