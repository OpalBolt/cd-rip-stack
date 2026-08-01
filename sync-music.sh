#!/usr/bin/env bash
# sync-music — move finished, beets-tagged music from the local library to the
# NAS. run after `tag` (beets) has sorted FLACs into ~/media/audio/music/library/.
# mounts NFS_MUSIC at MUSIC_MOUNT on demand (via the shared mount-nfs helper),
# then rsyncs each top-level folder across with --remove-source-files and sweeps
# the now-empty source dirs. one rip.log line per folder; on any failure it stops
# and leaves the source of the failed (and all later) folders in place — fix and
# re-run, rsync dedupes the rest.
# ponytail: rsync --remove-source-files does move+verify+delete in one pass; mv
# across NFS would re-traverse per file and risks a partial tree on a hiccup.
set -euo pipefail
source "$(dirname "$0")/rip-common.sh"

mnt="${MUSIC_MOUNT:-/mnt/music}"
src="$HOME/media/audio/music/library"   # beets library path (see beets-config.yaml)
dst="$mnt/library"

usage() {
  cat <<EOF
usage: sync-music
  moves every top-level folder under $src to $dst (beets subtree kept verbatim),
  deleting the source on success. mounts $mnt first (needs NFS_MUSIC in the env).
  re-run after each \`tag\`.
EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  '') ;;
  *) echo "sync-music takes no arguments" >&2; usage; exit 1 ;;
esac

[ -d "$src" ] || { echo "sync-music: no source library at $src (run \`rip\` + \`tag\` first)" >&2; exit 0; }

# only pay the mount cost when there is actually something to move.
shopt -s nullglob
entries=("$src"/*)
[ "${#entries[@]}" -gt 0 ] || { echo "sync-music: $src is empty — nothing to move"; exit 0; }

mount-nfs "$mnt" "${NFS_MUSIC:-}"
mkdir -p "$dst"

RIP_SCRIPT=sync-music.sh
RIP_SW=rsync
RIP_NAME=-
rip_log_start

# one folder per iteration so each gets its own log line and a failure stops the
# run with later sources intact.
# ponytail: --remove-source-files drops files as they transfer; on a mid-folder
# failure the moved files are already on the NAS (deduped on re-run) and the rest
# stay local — re-running picks up where it stopped.
for d in "${entries[@]}"; do
  RIP_NAME=$(basename "$d")
  rip_phase move
  if rsync -a --remove-source-files "$d" "$dst/"; then
    # --remove-source-files leaves the now-empty source dirs behind — sweep them.
    find "$d" -depth -type d -empty -delete 2>/dev/null || true
    rip_phase_end
  else
    rc=$?
    echo "sync-music: FAILED ($rc) moving $RIP_NAME — source left in place, re-run to retry" >&2
    exit "$rc"
  fi
done
