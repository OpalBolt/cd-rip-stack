#!/usr/bin/env bash
# rip a DVD/Blu-ray to lossless MKV on the NAS (/mnt/videos/rip/). makemkvcon
# decrypts + remuxes with a track-selection profile (custom.mmcp.xml) so audio +
# subtitles are picked at rip time. no transcode, no delete — run transcode-video
# on the desktop later. run inside the nix devshell (makemkvcon comes from PATH).
# ponytail: manual arg parsing — one optional positional, no getopts.
set -euo pipefail
source "$(dirname "$0")/rip-common.sh"
shopt -s nullglob

vid="${VIDEOS_MOUNT:-/mnt/videos}"
rip_dir="$vid/rip"

usage() {
  cat <<EOF
usage: rip-video [title]
  title   makemkvcon title spec for disc:0: "all" (default), a single number, or N-M.
          list titles first with: titles
          rips lossless MKVs to $rip_dir; run transcode-video on the desktop to finish.
EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac
title="${1:-all}"

# ponytail: inline mount check — Task 2 will extract a shared mount-nfs helper.
if ! mountpoint -q "$vid"; then
  [ -n "${NFS_VIDEOS:-}" ] || { echo "$vid not mounted and \$NFS_VIDEOS unset" >&2; exit 1; }
  mkdir -p "$vid"
  sudo mount -t nfs "$NFS_VIDEOS" "$vid" || { echo "mount $NFS_VIDEOS -> $vid failed" >&2; exit 1; }
fi

mkdir -p "$rip_dir"

RIP_SCRIPT=rip-video.sh
RIP_SW=makemkvcon
RIP_NAME=-
rip_log_start

# decrypt + remux to lossless MKV. --profile picks tracks at rip (see
# custom.mmcp.xml); --minlength=600 drops titles under 10 min (FBI warnings,
# logos, menus — and, accepted tradeoff, short extras).
# ponytail: makemkvcon arg order is source title output — verify if it rejects this.
rip_phase rip
makemkvcon mkv -r --profile="$(dirname "$0")/custom.mmcp.xml" --minlength=600 \
  disc:0 "$title" "$rip_dir/"
# name the rip after makemkvcon's output ("<title>_tNN.mkv" -> "<title>")
mkvs=("$rip_dir"/*.mkv)
[ "${#mkvs[@]}" -gt 0 ] && { b=$(basename "${mkvs[0]}" .mkv); RIP_NAME=${b%_t*}; }
rip_phase_end
