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

# ponytail: shared mount helper (Task 2) — the same one sync-music uses for /mnt/music.
mount-nfs "$vid" "${NFS_VIDEOS:-}"

mkdir -p "$rip_dir"

RIP_SCRIPT=rip-video.sh
RIP_SW=makemkvcon
RIP_NAME=-
rip_log_start

# decrypt + remux to lossless MKV. --profile picks tracks at rip (see custom.mmcp.xml).
# --minlength=$RIP_MINLENGTH (default 120s = 2 min) drops logos/FBI warnings but keeps
# extras like deleted scenes. must match titles.sh: makemkv renumbers titles by what
# survives the scan, so a different value here makes the title numbers point elsewhere.
# ponytail: makemkvcon arg order is source title output — verify if it rejects this.
rip_phase rip
for t in "${titles_to_rip[@]}"; do
  makemkvcon mkv -r --profile="$(dirname "$0")/custom.mmcp.xml" --minlength="${RIP_MINLENGTH:-120}" \
    disc:0 "$t" "$rip_dir/"
done
# resolve the real movie name and rename the staged MKVs so downstream
# (transcode/move) sees "<Title> (Year)_tNN.mkv". Blu-rays carry the name in
# disc metadata (no prompt, no network); DVDs rip as a label code (B1) and
# prompt a TMDB search. an already-named file (cache) is left untouched.
# ponytail: rename over sidecar — transcode derives its name via ${b%_t*}, so a
# rip-time rename makes it pick the real name up for free.
mkvs=("$rip_dir"/*.mkv)
if [ "${#mkvs[@]}" -gt 0 ]; then
  b=$(basename "${mkvs[0]}" .mkv); orig=${b%_t*}
  new=$orig
  # movie-name always prints Title\tYear\tLang; if it's missing/empty, keep
  # the label so the rip still completes (naming never blocks).
  if IFS=$'\t' read -r title year lang < <(movie-name "${mkvs[0]}"); then
    new=$title
    if [ "$year" != - ]; then new="$title ($year)"; fi
    # ponytail: strip only '/' here — it would split the path at rename time
    # and mis-file the MKV (transcode's sanitize() does the full pass at placement).
    new=${new//\//-}
  fi
  if [ "$new" != "$orig" ]; then
    for f in "$rip_dir/${orig}_t"*.mkv; do
      n=$(basename "$f" .mkv); n=${n##*_t}
      mv -- "$f" "$rip_dir/${new}_t${n}.mkv"
    done
    echo "rip-video: ${orig} → ${new}" >&2   # keep the original label visible
  fi
  RIP_NAME=$new
fi
rip_phase_end
