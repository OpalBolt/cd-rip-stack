#!/usr/bin/env bash
# rip a DVD/Blu-ray to lossless MKV on the NAS (/mnt/videos/rip/). makemkvcon
# decrypts + remuxes with a PER-MOVIE track-selection profile so the film's
# original-language audio is picked at rip time (Task 3); the desktop transcode
# is then a dumb pass-through re-encode (video only). no transcode, no delete —
# run transcode-video on the desktop later. run inside the nix devshell.
#
# Task 3 audio flow — the title and audio come from a TMDB lookup the user drives,
# NOT from scanning the disc (the scan is slower than typing the title):
#   refuse if offline -> movie-name --tmdb (user types the title, picks a result)
#   -> original_language from TMDB -> build a selection string -> sed it into a
#   temp custom.mmcp.xml -> rip -> rename staged MKVs with the resolved name.
# ponytail: manual arg parsing — one optional positional, no getopts.
set -euo pipefail
source "$(dirname "$0")/rip-common.sh"
shopt -s nullglob

# ── Task 3: audio selection ──

# ISO 639-1 (TMDB original_language) → ISO 639-2/B (MakeMKV). unknown codes pass
# through unchanged (MakeMKV won't match them; the nolang/single nets catch all).
# start with the user's library; add rows as needed.
iso2b() {
  case "$1" in
    en) printf eng;; de) printf deu;; da) printf dan;; fr) printf fra;;
    es) printf spa;; it) printf ita;; sv) printf swe;; no) printf nor;;
    fi) printf fin;; nl) printf nld;; *) printf '%s' "$1";;
  esac
}

# build the MakeMKV selection string. keep-lang = ISO 639-2/B to keep as the
# original-language audio; "" = keep-all fallback (TMDB had no original_language).
# type qualifiers audio(...)/subtitle(...) drop a dub-language AUDIO while keeping
# same-language SUBTITLES; video survives via the nolang catch (docker-ripper's
# trick). subtitle clause is a permissive baseline — Task 4 narrows it to dan.
# ponytail: verify the audio(...)/subtitle(...) qualifier syntax empirically on
# the first real disc (MakeMKV apdefs.h is truth); this is one tunable string.
build_selection() {  # $1 = keep-lang (2/B) or ""
  local k=$1 aud sub
  if [ -n "$k" ]; then aud="audio(${k}|nolang|single)";   sub="subtitle(${k}|dan|nolang|single)"
  else               aud="audio(eng|dan|nolang|single)";  sub="subtitle(eng|dan|nolang|single)"; fi
  printf '%s' "-sel:all,+sel:${aud},-sel:audio(havemulti|havecore),+sel:${sub},+sel:(nolang|single),-sel:mvcvideo"
}

# the selection string baked into custom.mmcp.xml; rip-video sed-replaces it with
# build_selection's output at rip time. MUST stay byte-identical to the string in
# custom.mmcp.xml — it is both the substitution target and the manual-use default.
BASELINE='-sel:all,+sel:(eng|dan|nolang|single),-sel:(havemulti|havecore),-sel:mvcvideo'

# refuse to start without network + a working TMDB key: the title and the audio
# language both come from TMDB, so an offline rip would guess both wrong.
require_online() {
  [ -n "${TMDB_API_KEY:-}" ] \
    || { echo "rip-video: no \$TMDB_API_KEY in env — set it and re-run." >&2; exit 1; }
  curl -fsS --max-time 5 -o /dev/null \
      "https://api.themoviedb.org/3/configuration?api_key=${TMDB_API_KEY}" \
    || { echo "rip-video: offline or TMDB_API_KEY rejected — fix and re-run." >&2; exit 1; }
}

# ── main ──

vid="${VIDEOS_MOUNT:-/mnt/videos}"
rip_dir="$vid/rip"
# 2 min drops logos/FBI warnings but keeps extras (user decision: keep all extras,
# including <10-min ones, rather than the task's 600s suggestion).
minlen="${RIP_MINLENGTH:-120}"

usage() {
  cat <<EOF
usage: rip-video [title]   (disc is always disc:0)
  title   which disc titles to rip: "all" (default), a number, a range (N-M),
          or a comma list (e.g. 0,5-7). list them first with: titles.
          asks for the movie title (TMDB), rips lossless MKVs with the original-
          language audio to $rip_dir; run transcode-video on the desktop to finish.
EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac
title="${1:-all}"

# expand the title spec into individual title numbers. makemkvcon rips one title
# per call ("saves a single title"), so a list or range becomes a loop. the disc
# is fixed at disc:0, so a stray "disc:N" here would make makemkvcon dump its
# usage and exit 1 — validate before touching the drive.
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

require_online

# ponytail: shared mount helper (Task 2) — the same one sync-music uses for /mnt/music.
mount-nfs "$vid" "${NFS_VIDEOS:-}"
mkdir -p "$rip_dir"

RIP_SCRIPT=rip-video.sh
RIP_SW=makemkvcon
RIP_NAME=-
rip_log_start
# layer temp cleanup onto the EXIT trap rip_log_start set (it still logs the total).
TMP_PROFILE=
rip_video_cleanup() { [ -n "$TMP_PROFILE" ] && rm -f "$TMP_PROFILE"; }
trap 'rip_video_cleanup; rip_log_finish' EXIT

# ── Task 3: ask for the title, TMDB-resolve it (no disc scan before rip) ──
if line=$(movie-name --tmdb); then
  IFS=$'\t' read -r TITLE YEAR origlang <<< "$line"
else
  echo "rip-video: title not resolved — aborting (re-run to retry)." >&2
  exit 1
fi
display=$TITLE; [ "$YEAR" != - ] && display="$TITLE ($YEAR)"
display=${display//\//-}     # a '/' would split the path; transcode sanitizes the rest
RIP_NAME=$display

if [ "$origlang" != "-" ] && [ -n "$origlang" ]; then keep=$(iso2b "$origlang")
else                                                      keep=""; fi
sel=$(build_selection "$keep")
rip_note audio "kept=${keep:-all} origlang=${origlang:-?}"

# inject the selection string into a temp copy of the profile. BASELINE is the
# literal substitution target (see custom.mmcp.xml); '#' delimiter because neither
# BASELINE nor sel contains '#', and as a BRE pattern BASELINE is fully literal.
TMP_PROFILE=$(mktemp --suffix=.mmcp.xml)
sed "s#${BASELINE}#${sel}#g" "$(dirname "$0")/custom.mmcp.xml" > "$TMP_PROFILE"

# decrypt + remux to lossless MKV with the per-movie profile. --minlength drops
# short junk (logos/FBI warnings) but keeps extras. must match titles.sh: makemkv
# renumbers titles by what survives the scan, so a different value moves them.
rip_phase rip
for t in "${titles_to_rip[@]}"; do
  makemkvcon mkv -r --profile="$TMP_PROFILE" --minlength="$minlen" disc:0 "$t" "$rip_dir/"
done

# rename staged MKVs to "<Title (Year)>_tNN.mkv" using the name resolved above
# (no re-prompt). the disc is ejected by hand; transcode-video finishes on the desktop.
movie-name --rename-as "$display" "$rip_dir" \
  || echo "rip-video: rename failed — re-run: movie-name --rename $rip_dir" >&2

rip_phase_end
