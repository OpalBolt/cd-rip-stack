#!/usr/bin/env bash
# movie-name — resolve a real movie name for a DVD/Blu-ray disc or a lossless MKV,
# and (with --rename) rename a rip dir's junk-labeled MKVs to "<Title> (Year)_tNN.mkv".
# makemkvcon names DVD rips after a useless label code (B1_t00.mkv, or per-title
# codes like C2/D1 for extras); this returns "<Title>\t<Year>\t<Lang>" (Lang =
# ISO 639-1, or "-") so rip/transcode/move name files, folders, and logs, and Task 3
# picks audio by original_language.
#
# Modes:
#   movie-name <disc:N | file.mkv>     resolve + print "Title\tYear\tLang"
#   movie-name --rename <dir|file>     resolve, then rename every still-junk-labeled
#                                     MKV in that dir to "<Title> (Year)_tNN.mkv"
#                                     (main + extras); prints the new base name
#   movie-name --rename-as <name> <dir>
#                                     rename using an already-resolved name (no
#                                     resolve, no prompt) — rip-video resolves the
#                                     name + lang before rip, then calls this after.
#
# Name source, first real hit wins:
#   1. an already-named "Title (Year)_tNN.mkv" in the dir (cache) — used as-is.
#   2. disc/MKV metadata if it "looks real" (Blu-ray titles pass; DVD labels fail).
#   3. TMDB search — pick prompt (Enter=1, s=skip, q=quit).
# No $TMDB_API_KEY / quit -> hard error (a DVD must not file as B1 unnoticed).
# No network / skip -> fall back to the label and warn. Reads $TMDB_API_KEY from
# the process env; never opens .env.
# ponytail: one resolver; rip-video calls print (pre-rip, for the lang) then
# --rename-as (post-rip) so TMDB is prompted exactly once.
set -euo pipefail
shopt -s nullglob

mode=print
ra_name=; ra_dir=
case "${1:-}" in
  --rename) mode=rename; shift ;;
  --rename-as)
    mode=rename-as; shift
    ra_name="${1:?--rename-as needs <name>}"; ra_dir="${2:?--rename-as needs <dir>}"; shift 2 ;;
  -h|--help)
    cat <<EOF
usage: movie-name [--rename | --rename-as <name> <dir>] [disc:N | file.mkv | rip-dir]
  resolves "<Title>\t<Year>\t<Lang>" (Lang = ISO 639-1, or "-").
  --rename <dir|file>: resolve, then rename every still-junk-labeled MKV in the
                       dir to "<Title> (Year)_tNN.mkv" (main + extras); prints
                       the base name.
  --rename-as <name> <dir>: rename the dir's junk-labeled MKVs to "<name>_tNN.mkv"
                       using an already-resolved name (no resolve, no prompt).
  Blu-ray metadata is used directly; a DVD label code (B1) prompts a TMDB search
  (pick prompt: Enter=1, s=skip, q=quit). No \$TMDB_API_KEY -> error; other TMDB
  failures fall back to the label. Reads \$TMDB_API_KEY from the env.
EOF
    exit 0 ;;
esac
arg=${1:-disc:0}

TITLE=-; YEAR=-; LANG=-
warn() { echo "movie-name: $*" >&2; }
emit() { printf '%s\t%s\t%s\n' "$TITLE" "$YEAR" "${LANG:--}"; }
setlabel() { TITLE=${1:--}; YEAR=-; LANG=-; }   # fall back to the raw disc label

# looks_real: a makemkvcon name is a real title if it has a space, is longer than
# 3, isn't the DVD file-label pattern ^[A-Z][0-9]+ (B1, C2), and isn't a short
# all-caps generic label. Blu-ray titles pass; DVD labels fail.
# ponytail: heuristic — real titles carry lowercase + spaces; label codes don't.
looks_real() {
  local n=$1
  [ ${#n} -gt 3 ] || return 1
  [[ $n =~ ^[A-Z][0-9]+$ ]] && return 1          # B1, C2 — the DVD file-label pattern
  [[ $n == *" "* ]] || return 1                  # needs at least one space
  [[ $n =~ ^[^a-z]*$ && ${#n} -le 16 ]] && return 1   # short all-caps = generic label
  return 0
}

# cache_hit <path>: an already-named "<Title> (Year)_tNN.mkv" sets TITLE/YEAR.
cache_hit() {
  local b
  b=$(basename "$1" .mkv); b=${b%_t*}
  if [[ $b =~ ^(.+)\ \((19[0-9]{2}|20[0-9]{2})\)$ ]]; then
    TITLE=${BASH_REMATCH[1]}; YEAR=${BASH_REMATCH[2]}; LANG=-
    return 0
  fi
  return 1
}

# makemkvcon's output name = file base minus trailing "_tNN".
path_raw() { local b; b=$(basename "$1" .mkv); printf '%s' "${b%_t*}"; }

# disc_raw <disc>: scan makemkvcon info, prefer title 0's name (TINFO:0,2), fall
# back to the disc volume label (CINFO:2). MAKEMKV_INFO replays a captured dump
# instead of hitting the drive — the same override titles.sh uses.
disc_raw() {
  local disc=$1 info
  if [ -n "${MAKEMKV_INFO:-}" ]; then info=$("$MAKEMKV_INFO" "$disc")
  else info=$(makemkvcon -r info --minlength="${RIP_MINLENGTH:-120}" "$disc"); fi
  # ponytail: mirror titles.sh's quote-strip (handles commas inside the title).
  printf '%s' "$info" | awk '
    /^CINFO:2,/  { v=$0; sub(/^[^"]*"/,"",v); sub(/"$/,"",v); disc=v }
    /^TINFO:0,2,/{ v=$0; sub(/^[^"]*"/,"",v); sub(/"$/,"",v); title=v }
    END { print (title != "") ? title : disc }
  '
}

# tmdb_resolve <fallback>: prompt -> search -> pick. Sets TITLE/YEAR/LANG and
# returns 0 on a pick or soft fallback, 1 on no-key or quit (hard error).
# pick UX: Enter=1 (top match), s=skip (use label), q=quit (abort).
tmdb_resolve() {
  local fallback=${1:-}

  if [ -z "${TMDB_API_KEY:-}" ]; then
    warn "no \$TMDB_API_KEY in env — cannot resolve via TMDB. set it and re-run."
    return 1
  fi

  printf '\nTMDB search (blank=skip, q=quit): ' >&2
  local query; read -r query || query=q
  case "$query" in
    q|Q) warn "quit — re-run movie-name to rename later"; return 1 ;;
    '') warn "skipped — using disc label '${fallback:--}'"; setlabel "$fallback"; return 0 ;;
  esac

  local q_enc url json
  q_enc=$(jq -nr --arg q "$query" '$q|@uri')
  url="https://api.themoviedb.org/3/search/movie?api_key=${TMDB_API_KEY}&query=${q_enc}"
  if ! json=$(curl -fsS --max-time 15 "$url"); then
    warn "TMDB request failed (no network?) — using disc label '${fallback:--}'"
    setlabel "$fallback"; return 0
  fi

  # top 10 results: title \t year (release_date[0:4]) \t original_language
  local picks=()
  mapfile -t picks < <(printf '%s' "$json" | jq -r \
      '.results[0:10][] | [ .title, (.release_date[0:4] // "-"), (.original_language // "-") ] | @tsv')
  local n=${#picks[@]}
  [ "$n" -gt 0 ] || { warn "no TMDB matches for '$query' — using disc label '${fallback:--}'"; setlabel "$fallback"; return 0; }

  local i t y l
  for i in "${!picks[@]}"; do
    IFS=$'\t' read -r t y l <<< "${picks[$i]}"
    printf '%d. %s  (%s)  [%s]\n' "$((i+1))" "$t" "$y" "$l" >&2
  done

  local pick
  while true; do
    printf 'pick 1-%s (Enter=1, s=skip, q=quit): ' "$n" >&2
    read -r pick || pick=q
    case "$pick" in
      ''|1) IFS=$'\t' read -r t y l <<< "${picks[0]}"; TITLE=$t; YEAR=$y; LANG=${l:--}; return 0 ;;
      s|S) warn "skipped — using disc label '${fallback:--}'"; setlabel "$fallback"; return 0 ;;
      q|Q) warn "quit — re-run movie-name to rename later"; return 1 ;;
    esac
    if [[ $pick =~ ^[0-9]+$ ]] && [ "$pick" -ge 1 ] && [ "$pick" -le "$n" ]; then
      IFS=$'\t' read -r t y l <<< "${picks[$((pick-1))]}"; TITLE=$t; YEAR=$y; LANG=${l:--}; return 0
    fi
    warn "invalid choice '$pick'" >&2
  done
}

# resolve: set TITLE/YEAR/LANG for $arg (disc or file). returns 1 on hard error.
resolve() {
  local raw
  if [[ $arg == disc:* ]]; then
    raw=$(disc_raw "$arg")
  else
    cache_hit "$arg" && return 0
    raw=$(path_raw "$arg")
  fi
  if [ -n "$raw" ] && looks_real "$raw"; then
    TITLE=$raw; YEAR=-; LANG=-          # Blu-ray: real title, no year/lang in metadata
  else
    tmdb_resolve "$raw" || return 1     # DVD junk label -> TMDB; no-key/quit -> error
  fi
}

# rename_to <dir> <display>: rename every still-junk-labeled MKV in <dir> to
# "<display>_tNN.mkv" (preserving _tNN); leave genuinely-named files alone.
# prints the display name to stdout, the rename map to stderr.
# ponytail: shared by --rename (after resolve) and --rename-as (name already known).
rename_to() {
  local dir=$1 display=$2 f b cur n
  for f in "$dir"/*.mkv; do
    b=$(basename "$f" .mkv)
    case "$b" in *_t*) n=${b##*_t};; *) n=;; esac
    cur=${b%_t*}
    [ "$cur" = "$display" ] && continue          # already this name
    looks_real "$cur" && continue                # a genuinely-named file (another movie) — leave it
    mv -- "$f" "$dir/${display}${n:+_t$n}.mkv"
    echo "movie-name: $cur → ${display}${n:+_t$n}" >&2
  done
  printf '%s\n' "$display"
}

# do_rename: resolve the name for a rip dir (or a file in it), then rename every
# still-junk-labeled MKV there to "<Title> (Year)_tNN.mkv". an already-named file
# sets the name (cache); per-title junk labels (B1/C2/D1…) from one disc all map to
# it. prints the new base name to stdout, the rename map to stderr.
# ponytail: one rip = one movie, so all junk labels in the dir map to one name;
# genuinely-named files (another movie left staged) are left alone.
do_rename() {
  local dir sample f b
  if [ -d "$arg" ]; then dir=$arg; else dir=$(dirname "$arg"); fi
  # sample = an already-named MKV if any (its name wins), else the first MKV.
  sample=
  for f in "$dir"/*.mkv; do
    b=$(basename "$f" .mkv); b=${b%_t*}
    if [[ $b =~ ^(.+)\ \((19[0-9]{2}|20[0-9]{2})\)$ ]]; then sample=$f; break; fi
    [ -z "$sample" ] && sample=$f
  done
  if [ -z "$sample" ]; then warn "no MKVs in $dir"; printf '%s\n' -; return 0; fi

  arg=$sample
  resolve || return 1
  local display=$TITLE; [ "$YEAR" != - ] && display="$TITLE ($YEAR)"
  display=${display//\//-}     # '/' would split the path; transcode sanitizes the rest
  rename_to "$dir" "$display"
}

# do_rename_as: rename a rip dir's junk-labeled MKVs to "<name>_tNN.mkv" using an
# already-resolved display name (no resolve, no TMDB prompt). rip-video calls this
# after ripping, with the name it resolved before the rip.
do_rename_as() {  # $1 = display, $2 = dir
  local display=$1 dir=$2 f sample
  if [ ! -d "$dir" ]; then warn "not a dir: $dir"; return 1; fi
  sample=
  for f in "$dir"/*.mkv; do [ -z "$sample" ] && sample=$f; done
  if [ -z "$sample" ]; then warn "no MKVs in $dir"; printf '%s\n' -; return 0; fi
  rename_to "$dir" "$display"
}

case "$mode" in
  rename)    do_rename || exit 1 ;;
  rename-as) do_rename_as "$ra_name" "$ra_dir" || exit 1 ;;
  *)         resolve || exit 1; emit ;;
esac
