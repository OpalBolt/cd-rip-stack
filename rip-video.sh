#!/usr/bin/env bash
# rip a DVD/Blu-ray: makemkvcon decrypts+remuxes to MKV, HandBrakeCLI transcodes
# each to 1080p H.265, then the lossless intermediate is deleted.
# run inside the nix devshell (makemkvcon/HandBrakeCLI come from PATH).
# ponytail: manual arg parsing — one optional positional, no getopts.
set -euo pipefail

out_dir=~/media/video/library
tmp_dir=~/media/video/rip
log=~/media/video/rip.log

usage() {
  cat <<EOF
usage: rip-video [title]
  title   makemkvcon title spec for disc:0: "all" (default), a single number, or N-M.
          list titles first with: makemkvcon -r info disc:0
EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac
title="${1:-all}"

mkdir -p "$out_dir" "$tmp_dir"

start=$(date +%s)
finish() {
  local rc=$?
  local elapsed=$(( $(date +%s) - start ))
  local mm=$(( elapsed / 60 ))
  local ss=$(( elapsed % 60 ))
  local status=ok
  [ "$rc" -ne 0 ] && status="FAIL($rc)"
  printf '[%s] rip-video title=%-6s %dm%02d  %s\n' \
    "$(date '+%F %T')" "$title" "$mm" "$ss" "$status" | tee -a "$log"
}
trap finish EXIT

# phase 1 — decrypt + remux to large lossless MKVs.
# ponytail: arg order is source title output (makemkv docs); can't verify
# offline — run `makemkvcon mkv --help` if makemkvcon rejects this.
makemkvcon mkv -r disc:0 "$title" "$tmp_dir"

# phase 2 — transcode each MKV to 1080p, drop the intermediate.
# ponytail: "H.265 MKV 1080p30" caps at 1080p (downscales UHD; DVD/BR stay
# native) and H.265 keeps files small — swap for an x264 preset if too slow.
shopt -s nullglob
for mkv in "$tmp_dir"/*.mkv; do
  HandBrakeCLI -i "$mkv" -o "$out_dir/$(basename "${mkv%.mkv}").mkv" \
    --preset "H.265 MKV 1080p30"
  rm -f "$mkv"
done
