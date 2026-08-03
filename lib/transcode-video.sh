#!/usr/bin/env bash
# batch-transcode every lossless MKV in /mnt/videos/rip/ to 1080p H.265 and place
# it in the Jellyfin library at /mnt/videos/library/<Movie (Year)>/[extras/].
# pulls each file to a local temp dir to encode off NFS, uploads the result, then
# deletes the lossless source. one phase line per file plus a total. track
# selection already happened at rip (custom.mmcp.xml), so this is a pass-through
# re-encode: keep every audio + subtitle track, re-encode video only.
#
# split films: rip-video --part N stages halves as <Movie (Year)>_tNN.pN.mkv. the
# main feature (lowest _tNN) is the ONLY title that merges across .pN — mkvmerge
# appends the parts in order on local temp, then the merged file is transcoded as
# one movie. every other title is an independent extra kept unique by its .pN
# suffix, so two different extras sharing a title number are NEVER joined. no
# footguns. (ponytail: merge is main-feature-only on purpose — same-index extras
# would silently corrupt if merged.)
#
# run inside the nix devshell (HandBrakeCLI + mkvmerge come from PATH).
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
  a film ripped in parts (<Movie (Year)>_tNN.pN.mkv) has its main feature merged
  in part order, then transcoded as one movie. re-run for newly ripped discs.
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

# encode <local_src> <local_out>: HandBrake pass-through re-encode — keep every
# audio + subtitle track, re-encode video only. identical for a merged main
# feature and an individual extra; factored so the flags can't drift between them.
encode() {
  HandBrakeCLI -i "$1" -o "$2" \
    --preset "H.265 MKV 1080p30" \
    --all-audio --aencoder copy \
    --audio-copy-mask aac,ac3,eac3,truehd,dts,dtshd,mp2,mp3,opus,vorbis,flac,alac,pcm \
    --audio-fallback ac3 \
    --all-subtitles --subtitle-burned=none   # keep all subs, burn none (soft, Task 4)
}

# main feature per movie = lowest _tNN present (_eNN are extras-disc titles, never
# main; .pN is a split-film half, ignored for "lowest"). compute before deleting.
declare -A main_num
for f in "${files[@]}"; do
  b=$(basename "$f" .mkv)
  if [[ $b =~ ^(.*)_t([0-9]+)(\.p[0-9]+)?$ ]]; then
    name=${BASH_REMATCH[1]}; num=${BASH_REMATCH[2]}
    if (( 10#$num < 10#${main_num[$name]:-999} )); then main_num[$name]=$num; fi
  elif [[ $b =~ ^(.*)_e([0-9]+)$ ]]; then
    :   # extras-disc title — filed as extras below, never main
  else
    echo "skip (no _tNN/_eNN suffix): $f" >&2
  fi
done

RIP_SCRIPT=transcode-video.sh
RIP_SW=handbrake
RIP_NAME=-
rip_log_start

failed=0
declare -A handled          # path -> 1: a main-feature group already handled it

for f in "${files[@]}"; do
  [ -n "${handled[$f]:-}" ] && continue
  b=$(basename "$f" .mkv)
  if   [[ $b =~ ^(.*)_t([0-9]+)(\.p[0-9]+)?$ ]]; then sfx=t; name=${BASH_REMATCH[1]}; num=${BASH_REMATCH[2]}; part=${BASH_REMATCH[3]:-}
  elif [[ $b =~ ^(.*)_e([0-9]+)$ ]]; then sfx=e; name=${BASH_REMATCH[1]}; num=${BASH_REMATCH[2]}; part=
  else echo "skip (no _tNN/_eNN suffix): $f" >&2; continue; fi
  RIP_NAME=$name
  safe=$(sanitize "$name")

  # ── main feature: merge its .pN parts (if any), transcode as one movie ──
  if [ "$sfx" = t ] && [ "$num" = "${main_num[$name]}" ]; then
    dest_dir="$lib_dir/$safe"; dest="$dest_dir/$safe.mkv"
    # gather every sibling for this (name, title): the split main feature. match
    # "<name>_t<num>" exactly or "<name>_t<num>.p<digits>". fixed-string prefix
    # (name holds parens/spaces), not a regex.
    pre="${name}_t${num}"
    siblings=()
    for g in "${files[@]}"; do
      gb=$(basename "$g" .mkv)
      if [ "$gb" = "$pre" ]; then siblings+=("$g"); continue; fi
      rest=${gb#"$pre"}
      if [ "${rest:0:2}" = ".p" ] && [[ ${rest:2} =~ ^[0-9]+$ ]]; then siblings+=("$g"); fi
    done
    # order: no-suffix first (part -1), then .pN ascending. normal use is clean
    # .p1 .p2 ...; the no-suffix-first rule also rescues a forgotten --part on disc 1.
    mapfile -t sibs < <(for g in "${siblings[@]}"; do
      rg=$(basename "$g" .mkv); rg=${rg#"$pre"}
      if [ -z "$rg" ]; then printf '%s\t%s\n' -1 "$g"; else printf '%s\t%s\n' "${rg:3}" "$g"; fi
    done | sort -n | cut -f2-)

    mkdir -p "$dest_dir"
    rip_phase encode
    local_parts=()
    if [ "${#sibs[@]}" -gt 1 ]; then
      # pull every part to local temp, mkvmerge-append in order, then encode the
      # merged file. mkvmerge: 0 ok, 1 warning (still usable), 2+ failure. the `+`
      # appends a file's tracks to the previous one — the standard join for a split
      # DVD (both halves share codecs/channels from the same master + profile).
      for g in "${sibs[@]}"; do lb="$tmp_dir/$(basename "$g")"; cp "$g" "$lb"; local_parts+=("$lb"); done
      merged="$tmp_dir/${safe}_t${num}.merged.mkv"
      margs=("${local_parts[0]}")
      for ((i=1; i<${#local_parts[@]}; i++)); do margs+=("+" "${local_parts[$i]}"); done
      mkvmerge -o "$merged" "${margs[@]}" >&2 && mrc=0 || mrc=$?
      if [ "$mrc" -le 1 ]; then
        local_src=$merged
      else
        echo "FAILED mkvmerge ($mrc): ${sibs[*]} — sources left in place (see mkvmerge output above)" >&2
        rm -f "${local_parts[@]}" "$merged"
        for g in "${sibs[@]}"; do handled[$g]=1; done
        failed=1; rip_phase_end; continue
      fi
    else
      local_src="$tmp_dir/$b.mkv"; cp "$f" "$local_src"
    fi

    local_out="$tmp_dir/${safe}_t${num}.h265.mkv"
    if encode "$local_src" "$local_out"; then
      mv "$local_out" "$dest"
      rm -f "$local_src" "${local_parts[@]}"     # local temp + merged-source parts
      rm -f "${sibs[@]}"                          # lossless sources on the NAS
      for g in "${sibs[@]}"; do handled[$g]=1; done
      rip_phase_end
    else
      rc=$?
      rm -f "$local_src" "$local_out" "${local_parts[@]}"
      echo "FAILED ($rc): ${sibs[*]} — sources left in place" >&2
      for g in "${sibs[@]}"; do handled[$g]=1; done
      failed=1; rip_phase_end
    fi
    continue
  fi

  # ── extras (non-main _t title, or an _e extras-disc title): transcode as-is ──
  # the .pN suffix is kept in the extras filename so split-disc extras never collide.
  dest_dir="$lib_dir/$safe/extras"; dest="$dest_dir/${safe}_${sfx}${num}${part}.mkv"
  mkdir -p "$dest_dir"
  local_src="$tmp_dir/$b.mkv"; local_out="$tmp_dir/$b.h265.mkv"
  rip_phase encode
  cp "$f" "$local_src"
  if encode "$local_src" "$local_out"; then
    mv "$local_out" "$dest"
    rm -f "$local_src" "$f"
    rip_phase_end
  else
    # ponytail: leave the lossless source so a re-run retries; total logs FAIL.
    rc=$?
    rm -f "$local_src" "$local_out"
    echo "FAILED ($rc): $f — source left in place" >&2
    failed=1; rip_phase_end
  fi
done

exit $failed
