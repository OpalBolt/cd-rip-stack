#!/usr/bin/env bash
# rip a DVD/Blu-ray: makemkvcon decrypts+remuxes to MKV, HandBrakeCLI transcodes
# each to 1080p H.265, then the lossless intermediate is deleted.
# run inside the nix devshell (makemkvcon/HandBrakeCLI come from PATH).
# ponytail: manual arg parsing — one optional positional, no getopts.
set -euo pipefail
source "$(dirname "$0")/rip-common.sh"
shopt -s nullglob

out_dir=~/media/video/library
tmp_dir=~/media/video/rip

usage() {
  cat <<EOF
usage: rip-video [title]
  title   makemkvcon title spec for disc:0: "all" (default), a single number, or N-M.
          list titles first with: titles
EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac
title="${1:-all}"

mkdir -p "$out_dir" "$tmp_dir"

RIP_SCRIPT=rip-video.sh
RIP_SW=makemkvcon+handbrake
RIP_NAME=-
rip_log_start

# phase 1 — decrypt + remux to large lossless MKVs.
# ponytail: arg order is source title output (makemkv docs); can't verify
# offline — run `makemkvcon mkv --help` if makemkvcon rejects this.
rip_phase rip
makemkvcon mkv -r disc:0 "$title" "$tmp_dir"
# name the rip after makemkvcon's output ("<title>_tNN.mkv" → "<title>")
mkvs=("$tmp_dir"/*.mkv)
[ "${#mkvs[@]}" -gt 0 ] && { b=$(basename "${mkvs[0]}" .mkv); RIP_NAME=${b%_t*}; }
rip_phase_end

# phase 2 — transcode each MKV to 1080p, drop the intermediate.
# ponytail: "H.265 MKV 1080p30" caps at 1080p (downscales UHD; DVD/BR stay
# native) and H.265 keeps files small — swap for an x264 preset if too slow.
rip_phase encode
for mkv in "${mkvs[@]}"; do
  HandBrakeCLI -i "$mkv" -o "$out_dir/$(basename "${mkv%.mkv}").mkv" \
    --preset "H.265 MKV 1080p30"
  rm -f "$mkv"
done
rip_phase_end
