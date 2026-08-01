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
usage: rip-video [title]   (disc is always disc:0)
  title   which titles to rip: "all" (default), a number, a range (N-M),
          or a comma list (e.g. 0,5-7). list them first with: titles.
          rips lossless MKVs to $rip_dir; run transcode-video on the desktop to finish.
EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac
title="${1:-all}"

# expand the title spec into individual title numbers. makemkvcon rips one title
# per call ("saves a single title"), so a list or range becomes a loop. the disc
# is fixed at disc:0, so a stray "disc:N" here would otherwise make makemkvcon
# dump its usage and exit 1 — validate before touching the drive.
titles_to_rip=()
if [ "$title" = all ]; then
  titles_to_rip=(all)
else
  IFS=, read -ra tokens <<< "$title"
  for tok in "${tokens[@]}"; do
    if [[ $tok =~ ^([0-9]+)-([0-9]+)$ ]]; then
      lo=${BASH_REMATCH[1]}; hi=${BASH_REMATCH[2]}
      if (( 10#$lo > 10#$hi )); then
        echo "rip-video: bad range '$tok' (start greater than end)" >&2; exit 1
      fi
      for (( n=10#$lo; n<=10#$hi; n++ )); do titles_to_rip+=("$n"); done
    elif [[ $tok =~ ^[0-9]+$ ]]; then
      titles_to_rip+=("$tok")
    else
      echo "rip-video: invalid title '$tok' — use 'all', a number, N-M, or a comma list" >&2
      usage; exit 1
    fi
  done
fi

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
for t in "${titles_to_rip[@]}"; do
  makemkvcon mkv -r --profile="$(dirname "$0")/custom.mmcp.xml" --minlength=600 \
    disc:0 "$t" "$rip_dir/"
done
# name the rip after makemkvcon's output ("<title>_tNN.mkv" -> "<title>")
mkvs=("$rip_dir"/*.mkv)
[ "${#mkvs[@]}" -gt 0 ] && { b=$(basename "${mkvs[0]}" .mkv); RIP_NAME=${b%_t*}; }
rip_phase_end
