#!/usr/bin/env bash
# batch-transcode every lossless MKV in /mnt/videos/rip/ to 1080p H.265 and place
# it in the Jellyfin library at /mnt/videos/library/<Movie (Year)>/[extras/].
# TV: anything staged under rip/TV/ routes to library/TV/<Show (Year)>/ — episodes
# as "Season NN/<Show> - S01E02.mkv" (season 00 as "Specials"), TV extras under
# extras/. The subdirectory is the type (settled): no main-feature pick, no
# merging, each file transcodes as-is.
# pulls each file to a local temp dir to encode off NFS, uploads the result, then
# deletes the lossless source (--keep: moves it to rip-DONE instead — verify the
# encodes there, then clean by hand). positional args pick movies by staged-name
# prefix; junk-labelled files (mid-rip or failed rename: no "(Year)") are always
# skipped so transcode never races a running rip-video. track
# selection already happened at rip (custom.mmcp.xml), so this is a pass-through
# re-encode: keep every audio + subtitle track, re-encode video only.
#
# split films: rip-video --part N stages halves as <Movie (Year)>_tNN.pN.mkv. the
# main feature (biggest _tNN group by bytes — see below) is the ONLY title that
# merges across .pN — mkvmerge
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
done_dir="$vid/rip-DONE"
tmp_dir="${VIDEO_WORK_DIR:-$HOME/media/video/work}"

usage() {
  cat <<EOF
usage: transcode-video [--keep] [name ...]
  transcodes every staged *.mkv under $rip_dir to 1080p H.265 and moves it to
  $lib_dir/<Movie (Year)>/[extras/] — TV files (under $rip_dir/TV/) to
  $lib_dir/TV/<Show (Year)>/Season NN/<Show> - S01E02.mkv (season 0: Specials).
  a film ripped in parts (<Movie (Year)>_tNN.pN.mkv) has its main feature merged
  in part order, then transcoded as one movie. re-run for newly ripped discs.
  name    transcode only movies whose staged filename starts with it (prefix
          match: "X2 (2003)" runs one movie, "The Godfather" grabs all three)
  --keep  move lossless sources to $done_dir instead of deleting them; verify
          the encodes, then clean $done_dir by hand
  files with a junk disc label (no "(Year)" in the name — a rip in progress,
          or a failed rename) are always skipped.
EOF
}

keep=0; want=()
for a in "$@"; do
  case "$a" in
    -h|--help) usage; exit 0 ;;
    --keep) keep=1 ;;
    -*) echo "transcode-video: unknown option: $a" >&2; usage; exit 1 ;;
    *) want+=("$a") ;;
  esac
done

# ponytail: shared mount helper (Task 2) — the same one sync-music uses for /mnt/music.
mount-nfs "$vid" "${NFS_VIDEOS:-}"

mkdir -p "$tmp_dir"
[ "$keep" = 1 ] && mkdir -p "$done_dir"

mapfile -t all_files < <(find "$rip_dir" -type f -name '*.mkv' 2>/dev/null)

# keep only finished rips, and only the asked-for movies.
# - junk guard: rip-video renames staged files to "<Title> (Year)_tNN.mkv" only
#   AFTER the rip completes (makemkvcon writes under a disc label like B1), so a
#   name without a "(YYYY)" tail is a rip in progress or a failed rename. never
#   encode those — a half-written source would encode "fine", land in the
#   library, and the cleanup would delete the file out from under makemkvcon.
# - movie filter: positional args are staged-name prefixes.
files=()
declare -A want_hit
tv_dir="$rip_dir/TV"
for f in "${all_files[@]}"; do
  b=$(basename "$f")
  # movies carry _tNN/_eNN[.pN]; TV episodes carry _SNNENN or _SNNENN-ENN (a double).
  if [[ $b =~ ^(.*)_([te][0-9]+(\.p[0-9]+)?|S[0-9]{2}E[0-9]{2}(-E[0-9]{2})?)\.mkv$ ]] && [[ ${BASH_REMATCH[1]} =~ \([0-9]{4}\)$ ]]; then
    :
  else
    echo "skip (junk label — mid-rip or failed rename): $f" >&2
    continue
  fi
  if [ "${#want[@]}" -gt 0 ]; then
    m=0
    for w in "${want[@]}"; do [[ $b == "$w"* ]] && { m=1; want_hit[$w]=1; }; done
    [ "$m" = 1 ] || continue
  fi
  files+=("$f")
done
if [ "${#want[@]}" -gt 0 ]; then
  for w in "${want[@]}"; do
    [ -n "${want_hit[$w]:-}" ] || echo "no staged MKVs match: $w" >&2
  done
fi
if [ "${#files[@]}" -eq 0 ]; then
  if [ "${#want[@]}" -gt 0 ]; then echo "no matching MKVs to transcode" >&2; exit 1; fi
  echo "no MKVs in $rip_dir"
  exit 0
fi

# ponytail: strip filesystem-hostile chars; keeps ( ) for "Title (Year)".
sanitize() { printf '%s' "$1" | tr -d '/:*?"<>|'; }

# archive <src>: --keep parks the lossless source in $done_dir instead of
# deleting it (verify-then-clean). never clobbers an existing file there.
archive() {
  d="$done_dir/$(basename "$1")"
  if [ -e "$d" ]; then
    echo "--keep: $d already exists — source left in $rip_dir" >&2
  else
    mv "$1" "$d"
  fi
}

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

# main feature per movie = the _tNN group with the most bytes (a split main's
# .pN halves sum). tie → lowest number. "lowest _tNN" alone picked menu/decoy
# titles on real rips (Warcraft t00 was 3 MB while the film sat on t01), so size
# decides. _eNN are extras-disc titles, never main. compute before deleting.
declare -A grp_sz
for f in "${files[@]}"; do
  [[ $f == "$tv_dir"/* ]] && continue              # TV never has a main feature
  b=$(basename "$f" .mkv)
  if [[ $b =~ ^(.*)_t([0-9]+)(\.p[0-9]+)?$ ]]; then
    name=${BASH_REMATCH[1]}; num=${BASH_REMATCH[2]}
    grp_sz["$name|$num"]=$(( ${grp_sz["$name|$num"]:-0} + $(stat -c%s "$f") ))
  elif [[ $b =~ ^(.*)_e([0-9]+)(\.p[0-9]+)?$ ]]; then
    :   # extras-disc title — filed as extras below, never main
  elif [[ $b =~ ^(.*)_S[0-9]{2}E[0-9]{2}(-E[0-9]{2})?$ ]]; then
    :   # TV episode — no main feature, never merged
  else
    echo "skip (no _tNN/_eNN suffix): $f" >&2
  fi
done
declare -A main_num
for k in "${!grp_sz[@]}"; do
  name=${k%%|*}; num=${k#*|}; cur=${main_num[$name]:-}
  if [ -z "$cur" ] || (( grp_sz[$k] > grp_sz["$name|$cur"] )) \
     || { (( grp_sz[$k] == grp_sz["$name|$cur"] )) && (( 10#$num < 10#$cur )); }; then
    main_num[$name]=$num
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
  tv=0; [[ $f == "$tv_dir"/* ]] && tv=1   # the subdirectory is the type (settled)
  if   [[ $b =~ ^(.*)_t([0-9]+)(\.p[0-9]+)?$ ]]; then sfx=t; name=${BASH_REMATCH[1]}; num=${BASH_REMATCH[2]}; part=${BASH_REMATCH[3]:-}
  elif [[ $b =~ ^(.*)_e([0-9]+)(\.p[0-9]+)?$ ]]; then sfx=e; name=${BASH_REMATCH[1]}; num=${BASH_REMATCH[2]}; part=${BASH_REMATCH[3]:-}
  elif [ "$tv" = 1 ] && [[ $b =~ ^(.*)_S([0-9]{2})E([0-9]{2})(-E[0-9]{2})?$ ]]; then sfx=S; name=${BASH_REMATCH[1]}; seas=${BASH_REMATCH[2]}; num=; part=
  else echo "skip (no _tNN/_eNN/_SNNENN suffix): $f" >&2; continue; fi
  RIP_NAME=$name
  safe=$(sanitize "$name")

  # ── main feature: merge its .pN parts (if any), transcode as one movie ──
  # (movies only — a TV file never reaches here, even a stray _tNN under rip/TV)
  if [ "$sfx" = t ] && [ "$tv" = 0 ] && [ "$num" = "${main_num[$name]}" ]; then
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
      if [ "$keep" = 1 ]; then for g in "${sibs[@]}"; do archive "$g"; done
      else rm -f "${sibs[@]}"; fi                # lossless sources on the NAS
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

  # ── filing destination for everything that is not a movie main feature ──
  # TV episode: library/TV/<Show>/Season NN/<Show> - S01E02.mkv (00 = Specials).
  # TV _eNN: library/TV/<Show>/extras/. Movie extra: library/<Movie>/extras/.
  if [ "$sfx" = S ]; then
    fol="Season $seas"; [ "$seas" = 00 ] && fol=Specials
    code=${b##*_}
    dest_dir="$lib_dir/TV/$safe/$fol"; dest="$dest_dir/$safe - $code.mkv"
  else
    root=$lib_dir; [ "$tv" = 1 ] && root="$lib_dir/TV"
    dest_dir="$root/$safe/extras"; dest="$dest_dir/${safe}_${sfx}${num}${part}.mkv"
  fi
  mkdir -p "$dest_dir"
  local_src="$tmp_dir/$b.mkv"; local_out="$tmp_dir/$b.h265.mkv"
  rip_phase encode
  cp "$f" "$local_src"
  if encode "$local_src" "$local_out"; then
    mv "$local_out" "$dest"
    rm -f "$local_src"
    if [ "$keep" = 1 ]; then archive "$f"; else rm -f "$f"; fi   # lossless source
    rip_phase_end
  else
    # ponytail: leave the lossless source so a re-run retries; total logs FAIL.
    rc=$?
    rm -f "$local_src" "$local_out"
    echo "FAILED ($rc): $f — source left in place" >&2
    failed=1; rip_phase_end
  fi
done

[ "$keep" = 1 ] && echo "--keep: lossless sources moved to $done_dir — verify, then clean by hand"
exit $failed
