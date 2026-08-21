#!/usr/bin/env bash
# movie-name — resolve a real movie name for a DVD/Blu-ray disc or a lossless MKV,
# and (with --rename) rename a rip dir's junk-labeled MKVs to "<Title> (Year)_tNN.mkv".
# makemkvcon names DVD rips after a useless label code (B1_t00.mkv, or per-title
# codes like C2/D1 for extras); this returns "<Title>\t<Year>\t<Lang>" (Lang =
# ISO 639-1, or "-") so rip/transcode/move name files, folders, and logs, and Task 3
# picks audio by original_language.
#
# Modes:
#   movie-name --tmdbid <id|url>       fetch a TMDB movie or TV entry directly
#                                      (no search, no prompt) and print the TSV.
#                                      <id> is a movie id, "tv:N" a TV id, or a
#                                      pasted themoviedb.org /movie/N or /tv/N
#                                      URL. failures are hard errors.
#   movie-name --tmdb                  skip the disc/file; prompt for a title and
#                                      TMDB-resolve it (rip-video uses this — it
#                                      asks for the title instead of scanning the
#                                      disc). no match / skip / quit -> error.
#   movie-name <disc:N | file.mkv>     resolve + print "Title\tYear\tLang"
#   movie-name --rename <dir|file>     resolve, then rename every still-junk-labeled
#                                      MKV in that dir to "<Title> (Year)_tNN.mkv"
#                                      (main + extras); prints the new base name
#   movie-name --rename-as [--extras] [--part N] <name> <dir>
#                                     rename using an already-resolved name (no
#                                     resolve, no prompt) — rip-video calls this
#                                     after rip with the name it resolved before.
#                                     --extras uses "_eNN" so a set's extras disc
#                                     never collides with the movie's "_tNN" files.
#                                     --part N stamps ".pN" (a split-film half;
#                                     transcode merges the main feature's parts).
#
# Name source, first real hit wins:
#   1. an already-named "Title (Year)_tNN.mkv" in the dir (cache) — used as-is.
#   2. disc/MKV metadata if it "looks real" (Blu-ray titles pass; DVD labels fail).
#   3. TMDB search — pick prompt (Enter=1, s=skip, q=quit).
# No $TMDB_API_KEY / quit -> hard error (a DVD must not file as B1 unnoticed).
# No network / skip -> fall back to the label and warn (unless --tmdb, a hard
# error). Reads $TMDB_API_KEY from the process env; never opens .env.
# ponytail: one resolver; rip-video calls --tmdb (pre-rip) then --rename-as
# (post-rip) so TMDB is hit exactly once.
set -euo pipefail
shopt -s nullglob
mode=print
ra_name=; ra_dir=; ra_sfx=t; ra_part=
case "${1:-}" in
  --rename) mode=rename; shift ;;
  --rename-as)
    mode=rename-as; shift
    [ "${1:-}" = "--extras" ] && { ra_sfx=e; shift; }
    if [ "${1:-}" = "--part" ]; then
      [[ ${2:-} =~ ^[0-9]+$ ]] || { echo "movie-name: --part needs a number, got '${2:-}'" >&2; exit 1; }
      ra_part=".p$2"; shift 2
    fi
    ra_name="${1:?--rename-as needs <name>}"; ra_dir="${2:?--rename-as needs <dir>}"; shift 2 ;;
  --tmdbid)
    mode=tmdbid; shift
    [ -n "${1:-}" ] || { echo "movie-name: --tmdbid needs a TMDB id, tv:N, or a themoviedb.org URL" >&2; exit 1; }
    tmdb_id=$1; shift ;;
  --tmdb) mode=tmdb; shift ;;
  -h|--help)
    cat <<EOF
usage: movie-name [--rename | --rename-as [--extras] [--part N] <name> <dir> | --tmdbid <id> | --tmdb] [disc:N | file.mkv | rip-dir]
  resolves "<Title>\t<Year>\t<Lang>" (Lang = ISO 639-1, or "-").
  --tmdbid <id>:            fetch a TMDB movie or TV entry directly (no search
                            prompt). <id> = movie id | tv:N | a pasted
                            themoviedb.org/movie/N or /tv/N URL. any failure
                            -> error.
  --tmdb:                   skip the disc/file, go straight to a TMDB search prompt
                            (rip-video uses this — it asks for the title instead of
                            scanning the disc). no match / skip / quit -> error.
  --rename <dir|file>:      resolve, then rename every still-junk-labeled MKV in
                            the dir to "<Title> (Year)_tNN.mkv" (main + extras).
  --rename-as <name> <dir>: rename the dir's junk-labeled MKVs to "<name>_tNN.mkv"
                            using an already-resolved name (no resolve, no prompt).
                            add --extras to use "<name>_eNN.mkv" (a set's extras
                            disc — transcode files these as extras, never main).
                            add --part N to stamp "<name>_tNN.pN.mkv" (a split-film
                            half; transcode merges the main feature's parts).
  <disc:N | file>:          Blu-ray metadata is used directly; a DVD label code (B1)
                            prompts a TMDB search (pick: Enter=1, s=skip, q=quit).
  No \$TMDB_API_KEY -> error; other TMDB failures fall back to the label (unless
  --tmdb, where they are hard errors). Reads \$TMDB_API_KEY from the env.
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

# tmdb fields differ by type: movies use title/release_date, TV uses
# name/first_air_date. one jq covers both via // fallbacks.
# ponytail: TV join — search/movie alone never returns TV shows, which is why
# correctly-named TV discs used to find nothing.
TMDB_TSV='[ (.title // .name), ((.release_date // .first_air_date // "-")[0:4] // "-"), (.original_language // "-"), $t ] | @tsv'

# tmdb_by_id <id-arg>: fetch the exact movie or TV entry — <id-arg> is a bare id
# (movie), "tv:N"/"movie:N", or a pasted themoviedb.org /movie/N or /tv/N URL.
# sets TITLE/YEAR/LANG; returns 1 on no-key / offline / bad id.
tmdb_by_id() {
  local type num json
  case "$1" in
    *themoviedb.org*)
      [[ $1 =~ /(movie|tv)/([0-9]+) ]] || { warn "no movie or tv id found in URL '$1'"; return 1; }
      type=${BASH_REMATCH[1]}; num=${BASH_REMATCH[2]} ;;
    tv:*|movie:*) type=${1%%:*}; num=${1##*:} ;;
    *) type=movie; num=$1 ;;
  esac
  [[ $num =~ ^[0-9]+$ ]] || { warn "bad TMDB id '$1'"; return 1; }
  if [ -z "${TMDB_API_KEY:-}" ]; then
    warn "no \$TMDB_API_KEY in env — cannot resolve via TMDB. set it and re-run."; return 1
  fi
  if ! json=$(curl -fsS --max-time 15 "https://api.themoviedb.org/3/${type}/${num}?api_key=${TMDB_API_KEY}"); then
    warn "TMDB fetch of ${type}/${num} failed (offline, bad key, or no such id)"; return 1
  fi
  local t y l typ
  IFS=$'\t' read -r t y l typ < <(printf '%s' "$json" | jq -r --arg t "$type" "$TMDB_TSV")
  TITLE=$t; YEAR=$y; LANG=${l:--}
}

# tmdb_resolve <fallback> [required=0|1]: prompt -> search -> pick. Sets
# TITLE/YEAR/LANG. returns 0 on a pick (or, when required=0, a soft fallback to
# the label); returns 1 on no-key/quit, and on every soft-fallback when
# required=1 (--tmdb: a name MUST be resolved, so skip/no-match/offline abort).
# pick UX: Enter=1 (top match), s=skip (use label unless required), q=quit.
tmdb_resolve() {
  local fallback=${1:-} required=${2:-0}

  if [ -z "${TMDB_API_KEY:-}" ]; then
    warn "no \$TMDB_API_KEY in env — cannot resolve via TMDB. set it and re-run."
    return 1
  fi

  printf '\nTMDB search (blank=skip, q=quit): ' >&2
  local query; read -r query || query=q
  case "$query" in
    q|Q) warn "quit — re-run movie-name later"; return 1 ;;
    '') if [ "$required" = 1 ]; then warn "no title entered — aborting"; return 1; fi
         warn "skipped — using disc label '${fallback:--}'"; setlabel "$fallback"; return 0 ;;
  esac

  local q_enc qurl mjson tjson
  q_enc=$(jq -nr --arg q "$query" '$q|@uri')
  qurl="api_key=${TMDB_API_KEY}&query=${q_enc}"
  # movies and TV live in separate TMDB indexes — query both, then merge.
  mjson=$(curl -fsS --max-time 15 "https://api.themoviedb.org/3/search/movie?${qurl}") || mjson=
  tjson=$(curl -fsS --max-time 15 "https://api.themoviedb.org/3/search/tv?${qurl}") || tjson=
  if [ -z "$mjson$tjson" ]; then
    if [ "$required" = 1 ]; then warn "TMDB request failed (offline or bad key) — aborting"; return 1; fi
    warn "TMDB request failed (no network?) — using disc label '${fallback:--}'"
    setlabel "$fallback"; return 0
  fi

  # top 5 movie + top 5 TV results: title \t year \t original_language \t type
  local picks=()
  mapfile -t picks < <(
    if [ -n "$mjson" ]; then printf '%s' "$mjson" | jq -r --arg t movie ".results[] | $TMDB_TSV" | head -5; fi
    if [ -n "$tjson" ]; then printf '%s' "$tjson" | jq -r --arg t tv ".results[] | $TMDB_TSV" | head -5; fi
  )
  local n=${#picks[@]}
  if [ "$n" -eq 0 ]; then
    if [ "$required" = 1 ]; then warn "no TMDB matches for '$query' — aborting"; return 1; fi
    warn "no TMDB matches for '$query' — using disc label '${fallback:--}'"; setlabel "$fallback"; return 0
  fi

  local i t y l typ
  for i in "${!picks[@]}"; do
    IFS=$'\t' read -r t y l typ <<< "${picks[$i]}"
    printf '%d. %s  (%s)  [%s/%s]\n' "$((i+1))" "$t" "$y" "$l" "$typ" >&2
  done

  local pick
  while true; do
    printf 'pick 1-%s (Enter=1, s=skip, q=quit): ' "$n" >&2
    read -r pick || pick=q
    case "$pick" in
      ''|1) IFS=$'\t' read -r t y l typ <<< "${picks[0]}"; TITLE=$t; YEAR=$y; LANG=${l:--}; return 0 ;;
      s|S) if [ "$required" = 1 ]; then warn "skipped — aborting"; return 1; fi
            warn "skipped — using disc label '${fallback:--}'"; setlabel "$fallback"; return 0 ;;
      q|Q) warn "quit — re-run movie-name later"; return 1 ;;
    esac
    if [[ $pick =~ ^[0-9]+$ ]] && [ "$pick" -ge 1 ] && [ "$pick" -le "$n" ]; then
      IFS=$'\t' read -r t y l typ <<< "${picks[$((pick-1))]}"; TITLE=$t; YEAR=$y; LANG=${l:--}; return 0
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

# rename_to <dir> <display> [suffix=t]: rename every still-junk-labeled MKV in
# <dir> to "<display>_<suffix>NN.mkv" (suffix t for the movie's titles, e for an
# extras disc); leave genuinely-named files alone. rolls the number forward when
# the target already exists (a second extras disc for the same movie) so a staged
# file is never clobbered. prints the display name to stdout, the rename map to stderr.
# ponytail: shared by --rename (after resolve) and --rename-as (name already known).
rename_to() {
  local dir=$1 display=$2 sfx=${3:-t} part=${4:-} f b cur n m target
  for f in "$dir"/*.mkv; do
    b=$(basename "$f" .mkv)
    case "$b" in *_t*) n=${b##*_t};; *) n=;; esac     # source is always makemkvcon's _tNN
    cur=${b%_t*}
    [ "$cur" = "$display" ] && continue          # already this name
    looks_real "$cur" && continue                # a genuinely-named file (another movie) — leave it
    # roll past an existing same-named file (a 2nd extras disc for this movie) so we
    # never clobber a staged file; n is always set from makemkvcon's _tNN. part (.pN,
    # a split-film half) is part of the name, so p1/p2 of the same title coexist.
    # printf %02d: makemkvcon pads (_t01); an unpadded roll (_t1) would miss it on
    # the existence check and then stage a file transcode sorts before _t02.
    m=$n
    while [ -n "$m" ] && [ -e "$(printf '%s/%s_%s%02d%s.mkv' "$dir" "$display" "$sfx" "$m" "$part")" ]; do
      m=$((m+1))
    done
    if [ -n "$m" ]; then target=$(printf '%s/%s_%s%02d%s.mkv' "$dir" "$display" "$sfx" "$m" "$part"); else target="$dir/${display}${part}.mkv"; fi
    mv -- "$f" "$target"
    echo "movie-name: $cur → $(basename "$target" .mkv)" >&2
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

# do_rename_as: rename a rip dir's junk-labeled MKVs to "<name>_tNN.mkv" (or
# "_eNN.mkv" with --extras) using an already-resolved display name (no resolve, no
# TMDB prompt). rip-video calls this after ripping, with the name it resolved first.
do_rename_as() {  # $1 = display, $2 = dir
  local display=$1 dir=$2 f sample
  if [ ! -d "$dir" ]; then warn "not a dir: $dir"; return 1; fi
  sample=
  for f in "$dir"/*.mkv; do [ -z "$sample" ] && sample=$f; done
  if [ -z "$sample" ]; then warn "no MKVs in $dir"; printf '%s\n' -; return 0; fi
  rename_to "$dir" "$display" "$ra_sfx" "$ra_part"
}

case "$mode" in
  rename)    do_rename || exit 1 ;;
  rename-as) do_rename_as "$ra_name" "$ra_dir" || exit 1 ;;
  tmdbid)    tmdb_by_id "$tmdb_id" || exit 1; emit ;;
  tmdb)      tmdb_resolve "" 1 || exit 1; emit ;;
  *)         resolve || exit 1; emit ;;
esac
