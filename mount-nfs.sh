#!/usr/bin/env bash
# mount-nfs — ensure an NFS export is mounted, on demand. shared by sync-music
# (NFS_MUSIC -> /mnt/music) and rip-video/transcode-video (NFS_VIDEOS ->
# /mnt/videos): each calls this instead of repeating the mountpoint/mount dance.
# skips if already mounted; mkdir -p + sudo mount -t nfs otherwise. never
# unmounts — a mounted share stays up for the session so re-runs are instant and
# the other commands see it too.
# ponytail: one callable helper, not sourced — no function namespace to share,
# and `set -e`/`exit` belong to the helper, not its caller's script.
set -euo pipefail

usage() {
  cat <<EOF
usage: mount-nfs <mnt> <export>
  ensures <export> (host:/path) is mounted at <mnt>. skips if already mounted,
  otherwise mkdir -p <mnt> and sudo mount -t nfs <export> <mnt>. exits non-zero
  with a clear message if <export> is empty or the mount fails. never unmounts.
EOF
}

[ $# -eq 2 ] || { usage; exit 1; }
mnt=$1
exp=$2

if mountpoint -q "$mnt"; then exit 0; fi

[ -n "$exp" ] || {
  echo "mount-nfs: $mnt not mounted and no export given (set NFS_MUSIC/NFS_VIDEOS?)" >&2
  exit 1
}
mkdir -p "$mnt"
if ! sudo mount -t nfs "$exp" "$mnt"; then
  echo "mount-nfs: mount $exp -> $mnt failed (export unreachable, or no sudo / nfs client on the host?)" >&2
  exit 1
fi
