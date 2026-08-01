#!/usr/bin/env bash
# batch-transcode every lossless MKV in /mnt/videos/rip/ to 1080p H.265 and place
# it in the Jellyfin library at /mnt/videos/library/<Movie (Year)>/[extras/].
# pulls each file to a local temp dir to encode off NFS, uploads the result, then
# deletes the lossless source. one phase line per file plus a total. track
# selection already happened at rip (custom.mmcp.xml), so this is a pass-through
# re-encode: keep every audio + subtitle track, re-encode video only.
# run inside the nix devshell (HandBrakeCLI comes from PATH).
# ponytail: no args, no getopts — it transcodes whatever is staged.
set -euo pipefail
source "$(dirname "$0")/rip-common.sh"

vid="${VIDEOS_MOUNT:-/mnt/videos}"
rip_dir="$vid/rip"
lib_dir="$vid/library"
tmp_dir="${VIDEO_WORK_DIR:-$HOME/media/video/work}"

usage() {
  cat <<EOF
usage: transcode-video
  transcodes every *.mkv under $rip_dir to 1080p H.265 and moves it to
  $lib_dir/<Movie (Year)>/[extras/], deleting the lossless source on success.
  re-run for newly ripped discs.
EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  '') ;;
  *) echo "transcode-video takes no arguments" >&2; usage; exit 1 ;;
esac

# ponytail: shared mount helper (Task 2) — the same one sync-music uses for /mnt/music.
mount-nfs "$vid" "${NFS_VIDEOS:-}"

mkdir -p "$tmp_dir"

mapfile -t files < <(find "$rip_dir" -type f -name '*.mkv' 2>/dev/null)
if [ "${#files[@]}" -eq 0 ]; then
  echo "no MKVs in $rip_dir"
  exit 0
fi

# ponytail: strip filesystem-hostile chars; keeps ( ) for "Title (Year)".
sanitize() { printf '%s' "$1" | tr -d '/:*?"<>|'; }

# main feature per movie = lowest _tNN present. compute before deleting anything.
declare -A main_num
for f in "${files[@]}"; do
  b=$(basename "$f" .mkv)
  if [[ $b =~ ^(.*)_t([0-9]+)$ ]]; then
    name=${BASH_REMATCH[1]}; num=${BASH_REMATCH[2]}
    if (( 10#$num < 10#${main_num[$name]:-999} )); then main_num[$name]=$num; fi
  else
    echo "skip (no _tNN suffix): $f" >&2
  fi
done

RIP_SCRIPT=transcode-video.sh
RIP_SW=handbrake
RIP_NAME=-
rip_log_start

failed=0
for f in "${files[@]}"; do
  b=$(basename "$f" .mkv)
  [[ $b =~ ^(.*)_t([0-9]+)$ ]] || continue
  name=${BASH_REMATCH[1]}; num=${BASH_REMATCH[2]}
  RIP_NAME=$name

  safe=$(sanitize "$name")
  if [ "$num" = "${main_num[$name]}" ]; then
    dest_dir="$lib_dir/$safe"; dest="$dest_dir/$safe.mkv"
  else
    dest_dir="$lib_dir/$safe/extras"; dest="$dest_dir/${safe}_t${num}.mkv"
  fi
  mkdir -p "$dest_dir"

  local_src="$tmp_dir/$b.mkv"; local_out="$tmp_dir/$b.h265.mkv"
  rip_phase encode
  # pull off NFS once, encode locally, push the result back.
  cp "$f" "$local_src"
  if HandBrakeCLI -i "$local_src" -o "$local_out" \
        --preset "H.265 MKV 1080p30" \
        --all-audio --aencoder copy \
        --audio-copy-mask aac,ac3,eac3,truehd,dts,dtshd,mp2,mp3,opus,vorbis,flac,alac,pcm \
        --audio-fallback ac3 \
        --all-subtitles ; then
    mv "$local_out" "$dest"
    rm -f "$local_src" "$f"        # local temp + lossless source on the NAS
    rip_phase_end
  else
    # ponytail: leave the lossless source so a re-run retries; total logs FAIL.
    rc=$?
    rm -f "$local_src" "$local_out"
    echo "FAILED ($rc): $f — source left in place" >&2
    failed=1
  fi
done

exit $failed
